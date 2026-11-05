"""命令行入口。"""

import argparse
import json
import sys

from command_history import CommandHistory
from .graph import run_command
from .parsers import make_llm_chat, make_llm_parser, rule_parser

LLM_FAILURE_LIMIT = 30


def normalize_console_text(text: str) -> str:
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _display(result: dict) -> dict:
    display = {key: value for key, value in result.items() if key not in ("candidate", "frame")}
    if "frame" in result:
        display["serial_frame"] = result["frame"].decode("utf-8")
    return display


def main() -> int:
    argument_parser = argparse.ArgumentParser(description="LangGraph 电机控制 JSON 测试（仅模拟）")
    argument_parser.add_argument("text", nargs="?", help="自然语言控制指令")
    argument_parser.add_argument("--mode", choices=["rule", "llm"], default="llm")
    argument_parser.add_argument("--demo", action="store_true")
    argument_parser.add_argument("--interactive", action="store_true",
                                 help="循环输入自然语言指令；输入 quit 或 exit 结束")
    argument_parser.add_argument("--chat", action="store_true",
                                 help="与 DeepSeek 连续聊天；不会解析或执行电机指令")
    argument_parser.add_argument("--history", action="store_true", help="查看最近解析记录")
    argument_parser.add_argument("--history-db", default="motor_history.sqlite3")
    arguments = argument_parser.parse_args()
    if arguments.chat:
        try:
            chat = make_llm_chat()
        except ValueError as error:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
            return 1
        print("进入 DeepSeek 聊天模式。输入 quit 或 exit 结束。")
        while True:
            try:
                text = normalize_console_text(input("你> ")).strip()
            except EOFError:
                break
            if text.lower() in {"quit", "exit"}:
                break
            if not text:
                continue
            try:
                print(f"DeepSeek> {chat(text)}")
            except Exception as error:
                print(f"DeepSeek 请求失败：{error}")
        return 0
    history = CommandHistory(arguments.history_db)
    if arguments.history:
        for record in history.recent():
            print(json.dumps(record.__dict__, ensure_ascii=False, indent=2))
        return 0
    if not arguments.demo and arguments.text is None and not arguments.interactive:
        argument_parser.error("请提供一句指令，或使用 --demo/--interactive")
    try:
        parser = make_llm_parser() if arguments.mode == "llm" else rule_parser
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    if arguments.interactive:
        print("进入交互模式。每行输入一句指令；输入 quit 或 exit 结束。")
        exit_code = 0
        consecutive_failures = 0
        active_mode = arguments.mode
        while True:
            try:
                text = normalize_console_text(input("电机> ")).strip()
            except EOFError:
                break
            if text.lower() in {"quit", "exit"}:
                break
            if not text:
                continue
            result = run_command(text, parser)
            history.record(text, result)
            print(json.dumps(_display(result), ensure_ascii=False, indent=2))
            exit_code = max(exit_code, 0 if result["ok"] else 1)
            if active_mode == "llm":
                if result["ok"]:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= LLM_FAILURE_LIMIT:
                        parser = rule_parser
                        active_mode = "rule"
                        print("连续拒绝 30 次，已切换到 rule 模式。")
        return exit_code
    texts = ["让电机顺时针转90度", "电机反转一百二十度", "让马达逆时针转45度"] \
        if arguments.demo else [arguments.text]
    exit_code = 0
    for text in texts:
        result = run_command(text, parser)
        history.record(text, result)
        print(json.dumps(_display(result), ensure_ascii=False, indent=2))
        exit_code = max(exit_code, 0 if result["ok"] else 1)
    return exit_code
