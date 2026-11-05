"""电机命令代理的兼容入口。

具体实现按职责拆分到 models、parsers、protocol、graph 和 cli。
"""

from motor.cli import main, normalize_console_text
from motor.graph import build_graph, run_command
from motor.models import GraphState, LLMDecision, MoveCommand, MoveCommandCandidate
from motor.parsers import (
    chinese_integer,
    make_llm_chat,
    make_llm_parser,
    reject_ambiguous_motion,
    reject_non_motor_intent,
    rule_parser,
)
from motor.protocol import decode_frame

__all__ = [
    "GraphState", "LLMDecision", "MoveCommand", "MoveCommandCandidate",
    "build_graph", "chinese_integer", "decode_frame", "main",
    "make_llm_chat", "make_llm_parser", "normalize_console_text",
    "reject_ambiguous_motion", "reject_non_motor_intent", "rule_parser",
    "run_command",
]


if __name__ == "__main__":
    raise SystemExit(main())
