"""ReAct 决策入口。离线基线与 DeepSeek 共享工具和实验约束。"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .models import (
    AdjustmentArgs,
    EmptyArgs,
    FanArgs,
    FinishArgs,
    FlapSetting,
    ToolDecision,
)
from .specs import initial_field

TOOL_MODELS = {
    "set_fan_field": FanArgs,
    "set_flap": FlapSetting,
    "scan_field": EmptyArgs,
    "analyze_field": EmptyArgs,
    "propose_adjustment": AdjustmentArgs,
    "finish": FinishArgs,
}
TOOL_GROUPS = {
    "devices": ["set_fan_field", "set_flap", "scan_field"],
    "analysis": ["analyze_field"],
    "control": ["propose_adjustment"],
}
TOOL_DESCRIPTIONS = {
    "set_fan_field": "按顺序覆盖的物理风速原语，单位 m/s。只设置风扇场，不代替实测。",
    "set_flap": "设置翼板静态角度、振幅和频率，完成后返回。",
    "scan_field": "等待流场稳定，按计划移动 XY 探针并采集全部测点。",
    "analyze_field": "读取本轮热线原始数据，计算均值、脉动、湍流度、误差，输出图表。",
    "propose_adjustment": "调用封装的反馈修正策略，生成下一轮风场原语建议。",
    "finish": "结束实验并说明原因，达标仍由数值代码判定。",
}


def allowed_tools(state):
    if state["phase"] == "initialize":
        if state["spec"]["flap"] and not state.get("flap_setting"):
            return ["set_flap"]
        return ["set_fan_field"]
    return {
        "scan": ["scan_field"],
        "analyze": ["analyze_field"],
        "adjust": ["propose_adjustment", "set_fan_field", "set_flap", "finish"],
        "apply": ["set_fan_field", "finish"],
    }[state["phase"]]


def baseline_policy(state):
    from .models import ExperimentSpec

    config = ExperimentSpec.model_validate(state["spec"])
    tool = allowed_tools(state)[0]
    arguments = {}
    summary = ""
    if tool == "set_flap":
        arguments = config.flap.model_dump()
        summary = "应用实验配置中的翼板参数。"
    elif tool == "set_fan_field":
        arguments = {"spec": state.get("proposed_spec") or initial_field(config)}
        summary = "将风场原语交给风扇工具，随后通过热线实测验证。"
    elif tool == "scan_field":
        summary = "保持当前控制参数，依次扫描所有计划测点。"
    elif tool == "analyze_field":
        summary = "使用实测样本计算风场统计与目标误差。"
    elif tool == "propose_adjustment":
        arguments = {"gain": 0.8}
        summary = "本轮尚未达标，使用限幅比例修正生成下一轮候选参数。"
    return ToolDecision(tool=tool, arguments=arguments, summary=summary)


class ModelPolicy:
    """真正的模型工具调用，可注入支持 bind_tools 的聊天模型进行测试。"""

    def __init__(self, model):
        self.model = model

    def __call__(self, state):
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        allowed = allowed_tools(state)
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": TOOL_MODELS[name].model_json_schema(by_alias=True),
                },
            }
            for name in allowed
        ]
        context = {
            key: state.get(key)
            for key in (
                "spec",
                "iteration",
                "phase",
                "current_spec",
                "proposed_spec",
                "analysis",
                "memories",
                "flap_setting",
            )
        }
        messages = [
            SystemMessage(
                content=(
                    "你是流体实验 Agent。根据目标、实测反馈与相关历史证据选择下一工具。"
                    "只调用当前允许的一个工具，每次输出简短可审阅的决策摘要。"
                    "保持风速 m/s、探针 mm 与阵列索引独立。历史经验是参考数据。"
                    "不要宣称未经实测的参数已达标。不得改变目标、标定版本和软件限值。"
                    "翼板主要用于湍流调节，缺乏标定时可说明限制并结束。"
                )
            )
        ]
        for item in state.get("history", [])[-6:]:
            messages.extend(
                [
                    AIMessage(
                        content=item["summary"],
                        tool_calls=[
                            {
                                "name": item["tool"],
                                "args": item["arguments"],
                                "id": item["id"],
                            }
                        ],
                    ),
                    ToolMessage(
                        content=json.dumps(item["result"], ensure_ascii=False),
                        tool_call_id=item["id"],
                    ),
                ]
            )
        messages.append(HumanMessage(content=json.dumps(context, ensure_ascii=False)))
        response = self.model.bind_tools(definitions, tool_choice="required").invoke(
            messages
        )
        calls = response.tool_calls
        if len(calls) != 1 or calls[0]["name"] not in allowed:
            raise ValueError("模型必须选择当前允许的一个工具")
        call = calls[0]
        TOOL_MODELS[call["name"]].model_validate(call["args"])
        summary = response.content if isinstance(response.content, str) else ""
        return ToolDecision(
            tool=call["name"],
            arguments=call["args"],
            summary=summary.strip()[:2000]
            or f"模型选择 {call['name']}，参数见工具调用记录。",
        )


def make_deepseek_policy():
    from langchain_openai import ChatOpenAI

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DeepSeek 模式需要 DEEPSEEK_API_KEY")
    model = ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=key,
        base_url="https://api.deepseek.com",
        temperature=0,
        timeout=40,
        max_retries=0,
    )
    return ModelPolicy(model)
