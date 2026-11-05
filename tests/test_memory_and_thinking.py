import tempfile
import unittest
from pathlib import Path

from memory_module import MemoryStore
from thinking_module import think


class MemoryAndThinkingTests(unittest.TestCase):
    def test_memory_is_persistent_and_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            first_store = MemoryStore(database)
            memory = first_store.add(
                "光照会影响植物生长速度", category="observation", importance=4
            )

            second_store = MemoryStore(database)
            results = second_store.search("植物 光照")

            self.assertEqual(results[0].id, memory.id)
            self.assertEqual(results[0].metadata, {})

    def test_thinking_graph_returns_actions_and_safety_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            store.add("电机实验必须先确认急停和限位", category="safety", importance=5)
            result = think("如何设计电机负载实验？", store)

            self.assertTrue(result["ok"])
            self.assertEqual(result["trace"], ["retrieve", "reason", "review"])
            self.assertGreaterEqual(len(result["plan"]["next_actions"]), 1)
            self.assertGreaterEqual(len(result["plan"]["risks"]), 1)
            self.assertEqual(result["memories"][0]["category"], "safety")

    def test_thinking_rejects_empty_question(self):
        result = think("   ", MemoryStore(":memory:"))
        self.assertFalse(result["ok"])
        self.assertIn("问题", result["error"])


if __name__ == "__main__":
    unittest.main()
