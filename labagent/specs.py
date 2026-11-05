"""原语验证与模拟展开。真实档位和串口由王凯的 FanController 处理。"""

import math

from .models import ExperimentSpec, FanArgs


def normalize_field(spec: list[dict]) -> list[dict]:
    return FanArgs.model_validate({"spec": spec}).model_dump(
        by_alias=True, exclude_none=True
    )["spec"]


def expand_field(spec: list[dict], config: ExperimentSpec) -> list[list[float]]:
    layers = normalize_field(spec)
    rows, columns = config.rows, config.columns
    field = [[0.0] * columns for _ in range(rows)]
    for layer in layers:
        kind = layer["type"]
        if kind == "rect":
            if not (
                layer["x0"] <= layer["x1"] < columns
                and layer["y0"] <= layer["y1"] < rows
            ):
                raise ValueError("矩形范围或端点顺序无效")
        if kind == "row" and layer["y"] >= rows:
            raise ValueError("行索引越界")
        if kind == "column" and layer["x"] >= columns:
            raise ValueError("列索引越界")
        if kind == "gaussian" and (layer["cx"] > columns - 1 or layer["cy"] > rows - 1):
            raise ValueError("高斯中心越界")
        profile = None
        if kind == "profile":
            size = columns if layer["axis"] == "x" else rows
            if layer["law"] == "custom":
                profile = layer.get("values")
                if profile is None or len(profile) != size:
                    raise ValueError("custom 剖面长度必须与指定轴一致")
            else:
                heights = layer.get("heights_m")
                if (
                    heights is None
                    or len(heights) != size
                    or any(z <= 0 for z in heights)
                ):
                    raise ValueError("幂律剖面需要每行或列的正物理高度 heights_m")
                if any(layer.get(key) is None for key in ("alpha", "z_ref", "v_ref")):
                    raise ValueError("幂律剖面缺少 alpha、z_ref 或 v_ref")
                profile = [
                    layer["v_ref"] * (z / layer["z_ref"]) ** layer["alpha"]
                    for z in heights
                ]
        for y in range(rows):
            for x in range(columns):
                value = None
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
                elif kind == "profile":
                    value = profile[x if layer["axis"] == "x" else y]
                if value is not None:
                    if (
                        not math.isfinite(value)
                        or not 0 <= value <= config.max_speed_mps
                    ):
                        raise ValueError("原语展开风速非有限值或超出软件范围")
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
