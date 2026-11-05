"""电机命令的 LangGraph 工作流。"""

import json
from typing import Callable
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .models import GraphState, MoveCommand, MoveCommandCandidate
from .parsers import reject_ambiguous_motion, rule_parser
from .protocol import decode_frame


def build_graph(parser: Callable[[str], dict] = rule_parser):
    def parse_node(state: GraphState) -> dict:
        try:
            text = state["text"].strip()
            if not text or len(text) > 500:
                raise ValueError("输入须为 1–500 个字符")
            reject_ambiguous_motion(text)
            return {"candidate": parser(text), "trace": ["parse"]}
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "解析服务失败，请检查网络或模型配置"
            return {"ok": False, "error": message, "trace": ["parse"]}

    def validate_node(state: GraphState) -> dict:
        trace = state["trace"] + ["validate"]
        try:
            candidate = MoveCommandCandidate.model_validate(state["candidate"])
            command = MoveCommand(**candidate.model_dump(), id=f"cmd-{uuid4().hex[:8]}")
            return {"command": command.model_dump(), "trace": trace}
        except ValueError as error:
            return {"ok": False, "error": str(error), "trace": trace}

    def simulate_node(state: GraphState) -> dict:
        frame = (json.dumps(state["command"], separators=(",", ":")) + "\n").encode("utf-8")
        received = decode_frame(frame)
        ack = {"id": received["id"], "status": "simulated",
               "message": "模拟接收成功；未打开串口，未驱动电机"}
        return {"ok": True, "frame": frame, "ack": ack, "trace": state["trace"] + ["simulate"]}

    graph = StateGraph(GraphState)
    graph.add_node("parse", parse_node)
    graph.add_node("validate", validate_node)
    graph.add_node("simulate", simulate_node)
    graph.add_edge(START, "parse")
    graph.add_conditional_edges("parse", lambda state: "reject" if "error" in state else "next",
                                {"reject": END, "next": "validate"})
    graph.add_conditional_edges("validate", lambda state: "reject" if "error" in state else "next",
                                {"reject": END, "next": "simulate"})
    graph.add_edge("simulate", END)
    return graph.compile()


def run_command(text: str, parser: Callable[[str], dict] = rule_parser) -> GraphState:
    return build_graph(parser).invoke({"text": text})
