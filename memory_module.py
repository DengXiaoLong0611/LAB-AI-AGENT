"""可持久化的轻量记忆层。

记忆默认保存在 SQLite 中，不依赖外部服务。思考模块只通过本文件的
MemoryStore 访问记忆，便于以后替换为向量数据库或 LangChain retriever。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4
from contextlib import closing


@dataclass(frozen=True)
class Memory:
    """一条可被思考模块检索的记忆。"""

    id: str
    content: str
    category: str
    importance: int
    created_at: str
    metadata: dict[str, str]


class MemoryStore:
    """使用 SQLite 保存、检索和删除记忆。"""

    def __init__(self, path: str | Path = "agent_memory.sqlite3") -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_created_at "
                "ON memories(created_at DESC)"
            )

    def add(
        self,
        content: str,
        *,
        category: str = "note",
        importance: int = 3,
        metadata: dict[str, str] | None = None,
    ) -> Memory:
        """新增记忆；空内容和超出范围的优先级会明确报错。"""
        content = content.strip()
        category = category.strip()
        if not content:
            raise ValueError("记忆内容不能为空")
        if not category:
            raise ValueError("记忆类别不能为空")
        if type(importance) is not int or not 1 <= importance <= 5:
            raise ValueError("importance 必须是 1 到 5 的整数")
        memory = Memory(
            id=f"mem-{uuid4().hex[:12]}",
            content=content,
            category=category,
            importance=importance,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=dict(metadata or {}),
        )
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO memories
                    (id, content, category, importance, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.id,
                    memory.content,
                    memory.category,
                    memory.importance,
                    memory.created_at,
                    json.dumps(memory.metadata, ensure_ascii=False),
                ),
            )
        return memory

    def search(self, query: str, *, limit: int = 8) -> list[Memory]:
        """按关键词检索记忆；没有关键词时按重要性和时间返回最近记忆。"""
        query = query.strip()
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        with closing(self._connect()) as connection:
            if query:
                terms = [term for term in query.split() if term]
                if len(terms) == 1 and re.search(r"[\u3400-\u9fff]", terms[0]):
                    compact = re.sub(r"\s+", "", terms[0])
                    terms = list(dict.fromkeys(
                        [compact, *(compact[index:index + 2]
                                   for index in range(len(compact) - 1))]
                    ))
                clauses = " OR ".join("content LIKE ?" for _ in terms)
                rows = connection.execute(
                    f"""
                    SELECT * FROM memories
                    WHERE {clauses}
                    ORDER BY importance DESC, created_at DESC
                    LIMIT ?
                    """,
                    [f"%{term}%" for term in terms] + [limit],
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM memories
                    ORDER BY importance DESC, created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [self._from_row(row) for row in rows]

    def recent(self, *, limit: int = 8) -> list[Memory]:
        """返回最近写入的记忆。"""
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, memory_id: str) -> bool:
        """删除指定记忆，并返回是否确实删除了一条记录。"""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cursor.rowcount == 1

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            importance=row["importance"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"]),
        )


def format_memories(memories: Iterable[Memory]) -> str:
    """将检索结果格式化为可放入提示词或日志的文本。"""
    return "\n".join(
        f"[{memory.category}][{memory.id}] {memory.content}" for memory in memories
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="科学代理的 SQLite 记忆管理")
    parser.add_argument("--db", default="agent_memory.sqlite3")
    subparsers = parser.add_subparsers(dest="action", required=True)
    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("content")
    add_parser.add_argument("--category", default="note")
    add_parser.add_argument("--importance", type=int, default=3)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--limit", type=int, default=8)
    arguments = parser.parse_args()
    store = MemoryStore(arguments.db)
    if arguments.action == "add":
        memory = store.add(
            arguments.content,
            category=arguments.category,
            importance=arguments.importance,
        )
        print(json.dumps(memory.__dict__, ensure_ascii=False, indent=2))
    else:
        print(json.dumps([memory.__dict__ for memory in store.recent(limit=arguments.limit)],
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
