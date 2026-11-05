"""控制策略只暴露 propose，后续 BO/RL 等在同一接口下实现。"""

from typing import Protocol

from ..models import ExperimentSpec
from ..specs import expand_field


class ControlStrategy(Protocol):
    def propose(
        self,
        config: ExperimentSpec,
        current: list[dict],
        measurements: list[dict],
        *,
        gain: float = 0.8,
    ) -> list[dict]: ...


class FeedbackStrategy:
    """在映射单元做限幅比例修正，不假定其为精确气动逆模型。"""

    def propose(self, config, current, measurements, *, gain=0.8):
        field = expand_field(current, config)
        by_id = {m["point_id"]: m for m in measurements}
        for point in config.points:
            error = point.target_mps - by_id[point.id]["mean_mps"]
            change = max(
                -config.max_change_mps, min(config.max_change_mps, gain * error)
            )
            old = field[point.fan_y][point.fan_x]
            field[point.fan_y][point.fan_x] = max(
                0, min(config.max_speed_mps, old + change)
            )
        # 压缩为逐行基底加局部贴片，层数上限与 256 个测点兼容。
        result = list(current)
        for point in config.points:
            result.append(
                {
                    "type": "rect",
                    "x0": point.fan_x,
                    "x1": point.fan_x,
                    "y0": point.fan_y,
                    "y1": point.fan_y,
                    "value": field[point.fan_y][point.fan_x],
                }
            )
        # 相同几何图层只保留最后一层，避免每轮膨胀。
        kept, seen = [], set()
        for layer in reversed(result):
            key = tuple(
                (k, layer[k]) for k in ("type", "x0", "x1", "y0", "y1") if k in layer
            )
            if layer["type"] != "rect" or key not in seen:
                kept.append(layer)
            if layer["type"] == "rect":
                seen.add(key)
        return list(reversed(kept))
