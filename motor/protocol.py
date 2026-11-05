"""电机协议帧的编码接收校验。"""

import json

from .models import MoveCommand


def decode_frame(frame: bytes) -> dict:
    if len(frame) > 1024 or not frame.endswith(b"\n") or frame.count(b"\n") != 1:
        raise ValueError("协议帧必须是 1024 字节以内、以一个换行结束的单行 JSON")
    try:
        payload = json.loads(frame.decode("utf-8"))
        return MoveCommand.model_validate(payload).model_dump()
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("接收端拒绝了无效协议帧") from error
