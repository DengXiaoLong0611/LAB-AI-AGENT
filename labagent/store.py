"""SQLite 事实记录与带参数校验的操作日志，不依赖聊天上下文。"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import ExperimentSpec

TERMINAL = {"succeeded", "exhausted", "cancelled", "failed"}


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class UnknownOperation(RuntimeError):
    pass


class LabStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "lab.sqlite3"
        (self.directory / "artifacts").mkdir(exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, status TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, error TEXT, elapsed_seconds REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_task ON events(task_id,seq);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool TEXT NOT NULL,
                    params TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    calibration_version TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                    content TEXT NOT NULL, task_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(task_id,kind));
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            if "pending_stop" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN pending_stop TEXT")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def create_task(self, spec: ExperimentSpec):
        task_id = "exp-" + uuid4().hex[:16]
        timestamp = now()
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO tasks(id,spec,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (task_id, encode(spec.model_dump()), "queued", timestamp, timestamp),
            )
        self.event(
            task_id,
            "task_created",
            {"name": spec.name, "description": spec.description},
        )
        return self.get_task(task_id)

    def get_task(self, task_id):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        result = dict(row)
        result["spec"], result["state"] = (
            json.loads(row["spec"]),
            json.loads(row["state"]),
        )
        return result

    def list_tasks(self):
        with closing(self.connect()) as db:
            ids = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 100"
                )
            ]
        return [self.get_task(task_id) for task_id in ids]

    def update_task(self, task_id, **changes):
        if not changes or not set(changes) <= {
            "status",
            "state",
            "error",
            "elapsed_seconds",
            "pending_stop",
        }:
            raise ValueError("任务更新字段无效")
        changes["updated_at"] = now()
        if "state" in changes:
            changes["state"] = encode(changes["state"])
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE tasks SET "
                + ",".join(key + "=?" for key in changes)
                + " WHERE id=?",
                [*changes.values(), task_id],
            )

    def mark_running(self, task_id):
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE tasks SET status='running',error=NULL,updated_at=? WHERE id=? AND status='queued'",
                (now(), task_id),
            )

    def pause_at_boundary(self, task_id):
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                "UPDATE tasks SET status='paused',updated_at=? WHERE id=? AND status='pause_requested'",
                (now(), task_id),
            )
            return cursor.rowcount == 1

    def prepare_stop(self, task_id, status, *, state=None, error=None):
        """把停止意图与最终分析一起持久化，取消优先于其他终态。"""
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            pending = (
                "cancelled"
                if row["status"] == "cancel_requested"
                or row["pending_stop"] == "cancelled"
                else status
            )
            saved_state = json.loads(row["state"]) if state is None else dict(state)
            saved_state["status"] = pending
            if error:
                saved_state["stop_reason"] = error
            db.execute(
                "UPDATE tasks SET pending_stop=?,state=?,error=?,updated_at=? WHERE id=?",
                (pending, encode(saved_state), error, now(), task_id),
            )

    def stop_unknown(self, task_id, error):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE tasks SET pending_stop=CASE WHEN status='cancel_requested'
                THEN 'cancelled' ELSE pending_stop END, status='needs_attention',error=?,updated_at=? WHERE id=?""",
                (error, now(), task_id),
            )

    def finish_stop(self, task_id):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            status = (
                "cancelled"
                if row["status"] == "cancel_requested"
                else row["pending_stop"]
            )
            state = json.loads(row["state"])
            state["status"] = status
            if status == "cancelled":
                state["stop_reason"] = "用户停止，设备已确认停止"
            error = state.get("stop_reason") if status == "failed" else None
            db.execute(
                "UPDATE tasks SET status=?,state=?,pending_stop=NULL,error=?,updated_at=? WHERE id=?",
                (status, encode(state), error, now(), task_id),
            )
        return status

    def request_control(self, task_id, action):
        allowed = {
            "pause": {"queued", "running"},
            "cancel": {
                "queued",
                "running",
                "pause_requested",
                "paused",
                "needs_attention",
            },
            "resume": {"paused", "needs_attention"},
        }
        target = {
            "pause": "pause_requested",
            "cancel": "cancel_requested",
            "resume": "queued",
        }
        if action not in allowed:
            raise ValueError("未知任务控制动作")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] not in allowed[action]:
                raise ValueError("当前状态不支持此操作")
            db.execute(
                "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                (target[action], now(), task_id),
            )
        self.event(task_id, "control_requested", {"action": action})

    def event(self, task_id, kind, payload):
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO events(task_id,created_at,kind,payload) VALUES(?,?,?,?)",
                (task_id, now(), kind, encode(payload)),
            )

    def events(self, task_id, after=0, limit=1000):
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, after, limit),
            ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def operations(self, task_id):
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM operations WHERE task_id=? ORDER BY rowid", (task_id,)
            ).fetchall()
        return [
            {
                **dict(r),
                "params": json.loads(r["params"]),
                "result": json.loads(r["result"]) if r["result"] else None,
            }
            for r in rows
        ]

    def operate(self, task_id, operation_id, tool, params, call, lookup=None):
        parameters = encode(params)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            if old:
                if (
                    old["params"] != parameters
                    or old["tool"] != tool
                    or old["task_id"] != task_id
                ):
                    raise ValueError("同一 operation_id 不能改变参数、工具或任务")
                if old["status"] == "completed":
                    return json.loads(old["result"])
            else:
                db.execute(
                    "INSERT INTO operations(id,task_id,tool,params,status) VALUES(?,?,?,?,?)",
                    (operation_id, task_id, tool, parameters, "pending"),
                )
        if old:
            recovered = lookup(operation_id) if lookup else None
            if recovered is None:
                raise UnknownOperation(f"{operation_id} 执行结果未知，需设备查询确认")
            result = recovered
        else:
            self.event(
                task_id,
                "tool_started",
                {"operation_id": operation_id, "tool": tool, "arguments": params},
            )
            try:
                result = call()
                encode(result)
            except Exception as error:
                with closing(self.connect()) as db, db:
                    db.execute(
                        "UPDATE operations SET status=?,error=? WHERE id=?",
                        ("unknown", str(error), operation_id),
                    )
                self.event(
                    task_id,
                    "tool_error",
                    {"operation_id": operation_id, "tool": tool, "error": str(error)},
                )
                raise UnknownOperation(
                    f"{tool} 结果未确认，{operation_id}，{error}"
                ) from error
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE operations SET status=?,result=?,error=NULL WHERE id=?",
                ("completed", encode(result), operation_id),
            )
        self.event(
            task_id,
            "tool_completed",
            {"operation_id": operation_id, "tool": tool, "result": result},
        )
        return result

    def save_artifact(self, task_id, filename, value):
        directory = self.directory / "artifacts" / task_id
        directory.mkdir(exist_ok=True)
        path = directory / filename
        if path.name != filename:
            raise ValueError("产物名称必须为单个文件名")
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            value if isinstance(value, str) else encode(value), encoding="utf-8"
        )
        temporary.replace(path)
        return filename

    def remember(self, task_id, kind, content, status="candidate"):
        task = self.get_task(task_id)
        spec = task["spec"]
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO memories VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "mem-" + uuid4().hex[:16],
                    spec["project_id"],
                    spec["device_id"],
                    spec["calibration_version"],
                    kind,
                    status,
                    encode(content),
                    task_id,
                    now(),
                ),
            )

    def memories(self, config, query="", limit=20):
        with closing(self.connect()) as db:
            rows = db.execute(
                """SELECT * FROM memories WHERE project_id=? AND device_id=?
                AND calibration_version=? AND status IN ('recorded','candidate','verified')
                AND content LIKE ? ORDER BY created_at DESC LIMIT ?""",
                (
                    config.project_id,
                    config.device_id,
                    config.calibration_version,
                    "%" + query + "%",
                    limit,
                ),
            ).fetchall()
        return [{**dict(row), "content": json.loads(row["content"])} for row in rows]

    def set_memory_status(self, memory_id, status):
        if status not in {"candidate", "verified", "disputed", "expired"}:
            raise ValueError("无效经验状态")
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                "UPDATE memories SET status=? WHERE id=? AND kind='experience'",
                (status, memory_id),
            )
            if not cursor.rowcount:
                raise KeyError(memory_id)
