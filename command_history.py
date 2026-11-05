"""电机自然语言解析结果的本地历史记录。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class CommandRecord:
    id: str
    text: str
    ok: bool
    command: dict[str, Any] | None
    error: str | None
    created_at: str


class CommandHistory:
    """使用 SQLite 保存每次解析尝试，包括被拒绝的输入。"""

    def __init__(self, path: str | Path = "motor_history.sqlite3") -> None:
        self.path = str(path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS command_history (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
                    command TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def record(self, text: str, result: dict[str, Any]) -> CommandRecord:
        """保存一次结果，不保存模型提示词或 API key。"""
        record = CommandRecord(
            id=f"rec-{uuid4().hex[:12]}",
            text=text,
            ok=bool(result.get("ok")),
            command=result.get("command"),
            error=result.get("error"),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO command_history
                    (id, text, ok, command, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.text,
                    int(record.ok),
                    json.dumps(record.command, ensure_ascii=False)
                    if record.command is not None else None,
                    record.error,
                    record.created_at,
                ),
            )
        return record

    def recent(self, limit: int = 20) -> list[CommandRecord]:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, text, ok, command, error, created_at
                FROM command_history
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            CommandRecord(
                id=row["id"],
                text=row["text"],
                ok=bool(row["ok"]),
                command=json.loads(row["command"]) if row["command"] else None,
                error=row["error"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
