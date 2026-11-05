"""导出项目中的 LangGraph 流程图。"""

from pathlib import Path

from memory_module import MemoryStore
from motor_agent import build_graph, rule_parser
from thinking_module import build_thinking_graph


def main() -> None:
    """生成 Mermaid 源文件，并尝试生成 PNG 图片。"""
    motor_graph = build_graph(rule_parser)
    thinking_graph = build_thinking_graph(MemoryStore("agent_memory.sqlite3"))
    output_dir = Path("pic") / "langGraphPic"
    output_dir.mkdir(parents=True, exist_ok=True)

    graphs = {
        "motor_graph": motor_graph,
        "thinking_graph": thinking_graph,
    }
    for name, graph in graphs.items():
        drawable = graph.get_graph()
        (output_dir / f"{name}.mmd").write_text(
            drawable.draw_mermaid(),
            encoding="utf-8",
        )
        try:
            (output_dir / f"{name}.png").write_bytes(drawable.draw_mermaid_png())
        except Exception as error:
            print(f"{name}.png 生成失败：{error}")
            print(f"已生成 {name}.mmd，可在 Mermaid Live 或 VS Code 中预览")


if __name__ == "__main__":
    main()
