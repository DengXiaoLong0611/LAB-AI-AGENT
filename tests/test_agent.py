"""自动化测试：验证转换流程和拒绝逻辑，不连接真实模型或电机。

运行方式：python -m unittest discover -s tests -v。
unittest 会发现 TestCase 子类里名字以 test_ 开头的方法。
"""

import json
import tempfile
import unittest
from pathlib import Path

from command_history import CommandHistory
from motor_agent import (
    LLMDecision,
    MoveCommand,
    decode_frame,
    normalize_console_text,
    reject_non_motor_intent,
    run_command,
)


class AgentTests(unittest.TestCase):
    """每个测试独立检查一个行为；断言不满足时，该测试就失败。"""

    def test_natural_language_reaches_simulated_receiver(self):
        """检查自然语言经过全部节点后，字段、回执和编码是否正确。"""
        result = run_command("让电机顺时针转90度")
        # assertTrue 检查为真；assertEqual 检查两个值相等。
        self.assertTrue(result["ok"])
        self.assertEqual(result["command"]["command"], "move_relative")
        self.assertEqual(result["command"]["angle_deg"], 90)
        self.assertTrue(result["command"]["id"].startswith("cmd-"))
        self.assertEqual(result["ack"]["status"], "simulated")
        self.assertEqual(result["trace"], ["parse", "validate", "simulate"])
        self.assertEqual(decode_frame(result["frame"]), result["command"])

    def test_chinese_numbers_and_reverse(self):
        """中文数字与逆时针方向也应该能被规则识别。"""
        result = run_command("电机反转一百二十度")
        self.assertTrue(result["ok"])
        self.assertEqual(result["command"]["angle_deg"], -120)

    def test_stop(self):
        """停止命令应该输出 stop 与零转速。"""
        result = run_command("停止电机")
        self.assertFalse(result["ok"])

    def test_ambiguous_or_unsafe_text_never_reaches_receiver(self):
        """模糊、超限或多动作指令不能生成协议帧和成功回执。"""
        for text in ["快一点", "电机正转", "电机正转9999度",
                     "电机正转1.5度", "电机正转90度然后反转", ""]:
            # with 管理上下文；subTest 让每句话作为子测试单独报告。
            with self.subTest(text=text):
                result = run_command(text)
                self.assertFalse(result["ok"])
                self.assertNotIn("ack", result)
                self.assertNotIn("frame", result)

    def test_ambiguous_speed_request_is_rejected_even_if_model_returns_zero(self):
        """模型不能把“快一点”擅自转换为零角度。"""
        result = run_command(
            "让电机转的快一点",
            parser=lambda text: {"command": "move_relative", "angle_deg": 0},
        )
        self.assertFalse(result["ok"])
        self.assertIn("速度类表达", result["error"])
        self.assertNotIn("ack", result)
        self.assertNotIn("frame", result)

    def test_non_motor_intent_is_rejected(self):
        """明显无关的话题不能被当成电机指令。"""
        with self.assertRaisesRegex(ValueError, "这不是有效的电机控制指令"):
            reject_non_motor_intent("今天天气怎么样")

    def test_llm_decision_supports_rejection(self):
        """LLM 决策模型允许拒绝请求，且拒绝时不能带控制指令。"""
        decision = LLMDecision(accepted=False, reason="缺少明确的方向和角度")
        self.assertIsNone(decision.command)
        with self.assertRaises(ValueError):
            LLMDecision(
                accepted=False,
                reason="拒绝",
                command={"command": "move_relative", "angle_deg": 90},
            )

    def test_candidate_numeric_strings_become_integers(self):
        """模拟解析器返回字符串数值，检查其是否转换为真正整数。"""
        candidate = {"command": "move_relative", "angle_deg": "90"}
        # lambda 替代真实解析器：不管输入是什么，都返回指定字典。
        result = run_command("测试输入", parser=lambda text: candidate)
        self.assertTrue(result["ok"])
        self.assertIs(type(result["command"]["angle_deg"]), int)

    def test_bad_model_outputs_are_rejected(self):
        """伪造不合法的模型输出，确认不能绕过校验。"""
        valid = {"command": "move_relative", "angle_deg": 90}
        for change in [{"angle_deg": True}, {"angle_deg": 1.2}, {"angle_deg": "1.2"},
                       {"command": "stop"}, {"pin": 9}, {"angle_deg": 361}]:
            with self.subTest(change=change):
                # ** 展开并合并字典；后面的 change 覆盖 valid 中同名字段。
                result = run_command("测试输入", parser=lambda text: {**valid, **change})
                self.assertFalse(result["ok"])
                self.assertNotIn("ack", result)

    def test_parser_failure_is_reported(self):
        """模拟解析服务异常，确认程序返回失败结果而不是继续发送。"""
        def failing_parser(text):
            raise RuntimeError("provider unavailable")
        self.assertFalse(run_command("测试", parser=failing_parser)["ok"])

    def test_wire_format_is_json_line(self):
        """协议帧需要以换行结束，内容必须能被解析为 JSON。"""
        result = run_command("让电机顺时针转90度")
        self.assertTrue(result["frame"].endswith(b"\n"))
        self.assertEqual(json.loads(result["frame"])["command"], "move_relative")

    def test_receiver_rejects_malformed_frames(self):
        """检查缺少换行、多行、非法编码、超长和缺字段的协议帧。"""
        for frame in [b"{}", b"{}\n", b"{}\n{}\n", b"\xff\n", b"x" * 1025 + b"\n"]:
            with self.subTest(frame=frame):
                # assertRaises 预期抛出异常；没有抛出反而意味着测试失败。
                with self.assertRaises(ValueError):
                    decode_frame(frame)

    def test_protocol_rejects_bad_id(self):
        """命令编号必须符合第一版协议格式。"""
        result = run_command("让电机顺时针转90度")
        for change in [{"id": "bad"}, {"id": ""}]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    MoveCommand.model_validate({**result["command"], **change})

    def test_command_history_persists_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            history = CommandHistory(Path(directory) / "history.sqlite3")
            success = run_command("让电机顺时针转90度")
            failure = run_command("快一点")
            history.record("让电机顺时针转90度", success)
            history.record("快一点", failure)

            records = history.recent()
            self.assertEqual(len(records), 2)
            self.assertFalse(records[0].ok)
            self.assertEqual(records[1].command["angle_deg"], 90)

    def test_console_text_replaces_invalid_surrogates(self):
        self.assertEqual(normalize_console_text("电机\udcac"), "电机?")


# 支持直接运行测试文件；通常建议从项目目录用 unittest discover。
if __name__ == "__main__":
    unittest.main()
