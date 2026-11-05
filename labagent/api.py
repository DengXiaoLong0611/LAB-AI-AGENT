"""本地网页与后台任务接口，单实验串行执行。"""

import asyncio
import csv
import io
import json
import os
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .agent import TOOL_DESCRIPTIONS, TOOL_GROUPS, TOOL_MODELS
from .models import Contract, ExperimentSpec
from .runner import ExperimentRunner
from .specs import expand_field, initial_field
from .store import TERMINAL, LabStore
from .tools.analysis import analyze_samples
from .tools.devices import SimulatedDevices


class DebugRequest(Contract):
    spec: ExperimentSpec
    tool: str
    arguments: dict = Field(default_factory=dict)


class MemoryReview(Contract):
    status: str


class DirectoryLock:
    """操作系统锁随进程退出释放，限制一个运行目录只有一个服务。"""

    def __init__(self, path):
        self.file = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            self.file.close()
            raise RuntimeError(
                "此运行目录已有平台进程，请使用同一个网页或不同数据目录"
            ) from error

    def close(self):
        self.file.close()


def create_app(data_dir=None, device_factory=SimulatedDevices, policy_factory=None):
    directory = Path(data_dir or os.getenv("LABAGENT_DATA_DIR", "lab_data")).resolve()
    store = LabStore(directory)
    mutex = threading.Lock()
    futures = {}

    @asynccontextmanager
    async def lifespan(app):
        directory_lock = DirectoryLock(directory / "service.lock")
        runner = ExperimentRunner(store, device_factory, policy_factory)
        executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wind-experiment"
        )
        app.state.runner = runner
        app.state.executor = executor
        for task in store.list_tasks():
            if task["status"] not in TERMINAL:
                store.update_task(
                    task["id"], status="paused", error="服务重启，请核对设备状态后继续"
                )
                store.event(
                    task["id"],
                    "recovery_required",
                    {"message": "实验已保留，等待继续请求"},
                )
        try:
            yield
        finally:
            for task in store.list_tasks():
                if task["status"] in {"queued", "running"}:
                    try:
                        store.request_control(task["id"], "pause")
                    except ValueError:
                        pass
            executor.shutdown(wait=True)
            runner.close()
            directory_lock.close()

    app = FastAPI(title="流体实验 Agent 工作台", lifespan=lifespan)
    app.state.store = store

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return Response("跨来源修改请求被拒绝", status_code=403)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return Response("记录不存在", status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_value(request, error):
        return Response(str(error), status_code=422)

    def submit(task_id):
        futures[task_id] = app.state.executor.submit(app.state.runner.run, task_id)

    def ensure_idle():
        if any(t["status"] not in TERMINAL for t in store.list_tasks()):
            raise HTTPException(409, "已有未结束实验，请先继续或停止该实验")

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "device_backend": device_factory.__name__,
            "local_only": True,
        }

    @app.get("/api/example")
    def example():
        return ExperimentSpec(
            name="左高右低风场",
            description="依次扫描四个测点，匹配目标平均风速。",
            points=[
                {
                    "id": f"p{i + 1}",
                    "x_mm": i * 100,
                    "y_mm": 0,
                    "fan_x": i * 8,
                    "fan_y": 12,
                    "target_mps": 8 - i * 1.5,
                }
                for i in range(4)
            ],
        ).model_dump()

    @app.get("/api/tools")
    def tools():
        return {
            "groups": TOOL_GROUPS,
            "descriptions": TOOL_DESCRIPTIONS,
            "schemas": {
                name: model.model_json_schema() for name, model in TOOL_MODELS.items()
            },
        }

    def task_view(task):
        runner = app.state.runner
        if runner.active_task_id == task["id"] and runner.started_at is not None:
            task["elapsed_seconds"] += time.monotonic() - runner.started_at
        return task

    @app.get("/api/tasks")
    def tasks():
        return [task_view(task) for task in store.list_tasks()]

    @app.post("/api/validate")
    def validate_spec(spec: ExperimentSpec):
        expand_field(initial_field(spec), spec)
        return spec.model_dump()

    @app.post("/api/tasks", status_code=201)
    def create_task(spec: ExperimentSpec):
        initial_field_spec = initial_field(spec)
        expand_field(initial_field_spec, spec)
        with mutex:
            ensure_idle()
            task = store.create_task(spec)
            submit(task["id"])
        return task

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        return task_view(store.get_task(task_id))

    @app.post("/api/tasks/{task_id}/control/{action}")
    def control(task_id: str, action: str):
        with mutex:
            pending = futures.get(task_id)
            if action == "resume" and pending is not None and not pending.done():
                raise HTTPException(409, "执行器尚在退出当前步骤，请稍后继续")
            store.request_control(task_id, action)
            if action in {"resume", "cancel"} and (pending is None or pending.done()):
                submit(task_id)
        return store.get_task(task_id)

    @app.get("/api/tasks/{task_id}/events")
    def events(task_id: str, after: int = Query(0, ge=0)):
        store.get_task(task_id)
        return store.events(task_id, after)

    @app.get("/api/tasks/{task_id}/stream")
    async def stream(task_id: str, request: Request, after: int = Query(0, ge=0)):
        store.get_task(task_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "Last-Event-ID 必须是整数")

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = store.events(task_id, cursor)
                for row in rows:
                    cursor = row["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/tasks/{task_id}/operations")
    def operations(task_id: str):
        store.get_task(task_id)
        return store.operations(task_id)

    @app.get("/api/tasks/{task_id}/artifacts/{filename}")
    def artifact(task_id: str, filename: str):
        store.get_task(task_id)
        base = directory / "artifacts" / task_id
        path = (base / filename).resolve()
        if (
            path.parent != base
            or not path.is_file()
            or path.suffix not in {".json", ".svg", ".csv"}
        ):
            raise HTTPException(404, "产物不存在")
        return FileResponse(path)

    @app.get("/api/tasks/{task_id}/logs")
    def logs(task_id: str):
        store.get_task(task_id)
        return StreamingResponse(
            (
                json.dumps(event, ensure_ascii=False) + "\n"
                for event in all_events(task_id)
            ),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{task_id}-logs.jsonl"'
            },
        )

    def all_events(task_id):
        cursor = 0
        while True:
            rows = store.events(task_id, cursor)
            if not rows:
                return
            yield from rows
            cursor = rows[-1]["seq"]

    @app.get("/api/tasks/{task_id}/download")
    def download(task_id: str):
        task = store.get_task(task_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "task.json", json.dumps(task, ensure_ascii=False, indent=2)
            )
            archive.writestr(
                "events.jsonl",
                "\n".join(
                    json.dumps(e, ensure_ascii=False) for e in all_events(task_id)
                ),
            )
            archive.writestr(
                "operations.json",
                json.dumps(store.operations(task_id), ensure_ascii=False, indent=2),
            )
            base = directory / "artifacts" / task_id
            if base.exists():
                for path in sorted(base.iterdir()):
                    if path.suffix in {".json", ".svg", ".csv"} and path.is_file():
                        archive.write(path, "artifacts/" + path.name)
            analysis = task["state"].get("analysis", {})
            if analysis.get("points"):
                output = io.StringIO()
                keys = [
                    "point_id",
                    "x_mm",
                    "y_mm",
                    "target_mps",
                    "mean_mps",
                    "std_mps",
                    "turbulence_intensity",
                    "error_mps",
                    "passed",
                    "raw_ref",
                ]
                writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(analysis["points"])
                archive.writestr("latest-field.csv", output.getvalue())
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{task_id}.zip"'},
        )

    @app.get("/api/memories")
    def memories(
        project_id: str = "wind-tunnel",
        device_id: str = "simulator",
        calibration_version: str = "simulation-v1",
        query: str = "",
    ):
        config = ExperimentSpec(
            name="检索",
            project_id=project_id,
            device_id=device_id,
            calibration_version=calibration_version,
            points=[
                {
                    "id": "query",
                    "x_mm": 0,
                    "y_mm": 0,
                    "fan_x": 0,
                    "fan_y": 0,
                    "target_mps": 0,
                }
            ],
        )
        return store.memories(config, query=query)

    @app.patch("/api/memories/{memory_id}")
    def review_memory(memory_id: str, review: MemoryReview):
        store.set_memory_status(memory_id, review.status)
        return {"ok": True}

    @app.post("/api/debug")
    def debug(payload: DebugRequest):
        # 调试使用独立模拟器，真实设备调试应通过带操作记录的实验任务进行。
        with mutex:
            ensure_idle()
            spec = payload.spec
            device = SimulatedDevices(spec)
            arguments = payload.arguments
            if payload.tool == "set_fan_field":
                validated = (
                    TOOL_MODELS["set_fan_field"]
                    .model_validate(arguments)
                    .model_dump(by_alias=True)
                )
                return {
                    "simulated": True,
                    "field_mps": expand_field(validated["spec"], spec),
                    "message": "原语校验与展开完成",
                }
            if payload.tool == "set_flap":
                setting = TOOL_MODELS["set_flap"].model_validate(arguments).model_dump()
                return device.set_flap(setting, "debug")
            if payload.tool == "scan_field":
                TOOL_MODELS["scan_field"].model_validate(arguments)
                device.set_fan_field(initial_field(spec), "debug-fan")
                output = []
                for point in spec.points:
                    device.move_probe(point, "debug-move")
                    raw = device.measure_hotwire(point, "debug-read")
                    output.append(
                        {
                            "point_id": point.id,
                            **analyze_samples(
                                raw["samples_mps"], sample_rate_hz=raw["sample_rate_hz"]
                            ),
                        }
                    )
                return {"simulated": True, "measurements": output}
            raise HTTPException(
                422,
                "独立调试支持 set_fan_field、set_flap、scan_field；分析与策略在实验中查看",
            )

    web = Path(__file__).parent / "web"
    app.mount("/web", StaticFiles(directory=web), name="web")

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    return app
