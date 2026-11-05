"""科学问题和项目的思考工作流。

默认模式是可测试、可离线的结构化规划器：检索记忆、拆解问题、提出假设、
设计验证动作并检查风险。未来可将 ``reason_node`` 替换为 LangChain 模型节点，
而不改变输入输出协议。
"""

from __future__ import annotations

import argparse
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from memory_module import Memory, MemoryStore, format_memories


class ScientificPlan(BaseModel):
    """面向下一步行动的科学计划，而不是未经验证的结论。"""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    understanding: str = Field(min_length=1)
    hypotheses: list[str] = Field(min_length=1, max_length=5)
    next_actions: list[str] = Field(min_length=1, max_length=8)
    risks: list[str] = Field(min_length=1, max_length=8)
    evidence_needed: list[str] = Field(min_length=1, max_length=8)


class ThinkingState(TypedDict, total=False):
    question: str
    memories: list[dict]
    context: str
    plan: dict
    trace: list[str]
    ok: bool
    error: str


def build_thinking_graph(store: MemoryStore):
    """创建思考图；store 由调用方注入，便于测试和更换存储实现。"""

    def retrieve_node(state: ThinkingState) -> dict:
        question = state["question"].strip()
        if not question or len(question) > 2000:
            return {"ok": False, "error": "问题须为 1–2000 个字符", "trace": ["retrieve"]}
        memories = store.search(question, limit=8)
        return {
            "memories": [memory.__dict__ for memory in memories],
            "context": format_memories(memories),
            "trace": ["retrieve"],
        }

    def reason_node(state: ThinkingState) -> dict:
        question = state["question"].strip()
        memory_text = state.get("context", "")
        context_note = (
            f"已有相关记忆：{memory_text}"
            if memory_text
            else "暂无相关记忆，需要先建立基线事实。"
        )
        plan = ScientificPlan(
            question=question,
            understanding=(
                f"将问题拆为可观测变量、可能机制和可证伪的验证步骤。{context_note}"
            ),
            hypotheses=[
                "问题中的关键变量之间存在可测量的关系，但方向和强度尚未确认。",
                "当前结果可能受到未记录的环境变量或测量误差影响。",
            ],
            next_actions=[
                "明确目标、约束条件、成功指标和不可接受的风险。",
                "列出自变量、因变量、控制变量，并记录当前基线。",
                "为主要假设设计最小可行实验，一次只改变一个关键变量。",
                "执行前检查设备、数据来源和停止条件；执行后保存原始数据。",
                "根据结果更新假设，不要把相关性直接当作因果关系。",
            ],
            risks=[
                "没有原始数据或对照组时，结论只能作为待验证假设。",
                "涉及电机或其他设备时，必须先人工确认限位、急停和断连保护。",
            ],
            evidence_needed=[
                "问题的定义、初始条件和现有观测数据。",
                "重复测量、对照条件以及记录时间和环境的实验日志。",
            ],
        )
        return {"plan": plan.model_dump(), "trace": state["trace"] + ["reason"]}

    def review_node(state: ThinkingState) -> dict:
        plan = ScientificPlan.model_validate(state["plan"])
        if not plan.next_actions or not plan.risks:
            return {
                "ok": False,
                "error": "思考计划必须包含行动和风险检查",
                "trace": state["trace"] + ["review"],
            }
        return {"ok": True, "plan": plan.model_dump(), "trace": state["trace"] + ["review"]}

    graph = StateGraph(ThinkingState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("reason", reason_node)
    graph.add_node("review", review_node)
    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges(
        "retrieve",
        lambda state: "reject" if "error" in state else "next",
        {"reject": END, "next": "reason"},
    )
    graph.add_edge("reason", "review")
    graph.add_edge("review", END)
    return graph.compile()


def think(question: str, store: MemoryStore | None = None) -> ThinkingState:
    """针对一个科学问题运行一次思考流程。"""
    return build_thinking_graph(store or MemoryStore()).invoke({"question": question})


def main() -> int:
    parser = argparse.ArgumentParser(description="科学问题思考与行动规划")
    parser.add_argument("question")
    parser.add_argument("--db", default="agent_memory.sqlite3")
    arguments = parser.parse_args()
    result = think(arguments.question, MemoryStore(arguments.db))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
