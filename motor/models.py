"""电机命令与 LangGraph 状态模型。"""

import re
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ANGLE_DEG = 360


class MoveCommandCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Literal["move_relative"]
    angle_deg: Annotated[int, Field(ge=-MAX_ANGLE_DEG, le=MAX_ANGLE_DEG)]

    @field_validator("angle_deg", mode="before")
    @classmethod
    def integer_only(cls, value):
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
            return int(value)
        raise ValueError("角度必须是整数或整数数字字符串，不能是小数或布尔值")


class LLMDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    reason: str = Field(min_length=1)
    command: MoveCommandCandidate | None = None

    @model_validator(mode="after")
    def require_command_when_accepted(self):
        if self.accepted and self.command is None:
            raise ValueError("接受的指令必须包含 command")
        if not self.accepted and self.command is not None:
            raise ValueError("拒绝的请求不能包含 command")
        return self


class MoveCommand(MoveCommandCandidate):
    id: str = Field(pattern=r"^cmd-[0-9a-f]+$")


class GraphState(TypedDict, total=False):
    text: str
    candidate: dict
    command: dict
    frame: bytes
    ack: dict
    ok: bool
    error: str
    trace: list[str]
