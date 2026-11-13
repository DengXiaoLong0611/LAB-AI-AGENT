"""原语验证与模拟展开。真实档位和串口由王凯的 FanController 处理。"""

import math

from .models import FAN_ROW_REF_HEIGHT_M, ExperimentSpec, FanArgs


def normalize_field(spec: list[dict]) -> list[dict]:
    return FanArgs.model_validate({"spec": spec}).model_dump(
        by_alias=True, exclude_none=True
    )["spec"]


def _heights(size: int) -> list[float]:
    """沿轴等间距占位高度：第 0 行/列 → 0，最后一行/列 → 参考高度（行号自底部起，第 0 行=物理底部）。"""
    return [FAN_ROW_REF_HEIGHT_M * i / max(1, size - 1) for i in range(size)]


def expand_field(spec: list[dict], config: ExperimentSpec) -> list[list[float]]:
    layers = normalize_field(spec)
    rows, columns = config.rows, config.columns
    field = [[0.0] * columns for _ in range(rows)]
    for layer in layers:
        kind = layer["type"]
        if kind == "raw":
            continue  # raw 是档位层，不参与风速展开（设备层 _apply_raw 处理）
        if kind == "rect" and not (
            layer["x0"] <= layer["x1"] < columns
            and layer["y0"] <= layer["y1"] < rows
        ):
            raise ValueError("矩形范围或端点顺序无效")
        if kind == "row" and layer["y"] >= rows:
            raise ValueError("行索引越界")
        if kind == "column" and layer["x"] >= columns:
            raise ValueError("列索引越界")
        if kind == "gaussian" and (layer["cx"] >= columns or layer["cy"] >= rows):
            raise ValueError("高斯中心越界")
        profile = None
        if kind == "profile":
            size = columns if layer["axis"] == "x" else rows
            if layer["law"] == "custom":
                profile = layer.get("values")
                if profile is None or len(profile) != size:
                    raise ValueError("custom 剖面长度必须与指定轴一致")
            else:
                # 幂律高度：z 沿轴从 0 升到参考高度。
                # 行号自底部起：row0=物理底部(0)，末行=物理顶部(参考高度)。
                # _heights 已按 row0→0、末行→参考高度排列，y 轴直接使用（无需反转）。
                heights = _heights(size)
                profile = [
                    layer["v_ref"] * (z / layer["z_ref"]) ** layer["alpha"]
                    for z in heights
                ]
        for y in range(rows):
            for x in range(columns):
                value = None
                overlay = False
                if kind == "uniform":
                    value = layer["value"]
                elif kind == "gradient":
                    index, size = (x, columns) if layer["axis"] == "x" else (y, rows)
                    value = layer["from"] + (layer["to"] - layer["from"]) * index / max(
                        1, size - 1
                    )
                elif (
                    kind == "rect"
                    and layer["x0"] <= x <= layer["x1"]
                    and layer["y0"] <= y <= layer["y1"]
                ):
                    value = layer["value"]
                elif kind == "row" and y == layer["y"]:
                    value = layer["value"]
                elif kind == "column" and x == layer["x"]:
                    value = layer["value"]
                elif kind == "gaussian":
                    distance = (x - layer["cx"]) ** 2 + (y - layer["cy"]) ** 2
                    value = layer["baseline"] + layer["amplitude"] * math.exp(
                        -distance / (2 * layer["sigma"] ** 2)
                    )
                    overlay = True  # 叠加语义：鼓包加在下层之上，而非覆盖全场
                elif kind == "profile":
                    value = profile[x if layer["axis"] == "x" else y]
                if value is not None:
                    if not math.isfinite(value) or not 0 <= value <= config.max_speed_mps:
                        raise ValueError("原语展开风速非有限值或超出软件范围")
                    if overlay:
                        total = field[y][x] + value
                        if total > config.max_speed_mps:
                            raise ValueError("gaussian 叠加后风速超出软件范围")
                        field[y][x] = total
                    else:
                        field[y][x] = value
    return field


def initial_field(config: ExperimentSpec) -> list[dict]:
    if config.initial_spec:
        expand_field(config.initial_spec, config)
        return normalize_field(config.initial_spec)
    base = sum(p.target_mps for p in config.points) / len(config.points)
    return [{"type": "uniform", "value": base}] + [
        {
            "type": "rect",
            "x0": p.fan_x,
            "x1": p.fan_x,
            "y0": p.fan_y,
            "y1": p.fan_y,
            "value": p.target_mps,
        }
        for p in config.points
    ]
