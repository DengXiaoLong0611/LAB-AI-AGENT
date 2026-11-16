"""实验契约。风扇索引与探针毫米坐标分别声明。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---- 风扇阵列硬件事实（253 档 ↔ 20 m/s，帧固定 24×32=768）----
MAX_WIND_SPEED_MPS = 20.0   # 风扇可达上限 m/s（253 档对应 20 m/s）
RAW_MAX = 253               # 档位上限（254/255 被帧头帧尾 0xFE/0xFF 占用）
FAN_ROWS = 24               # 行数（y 方向，竖直高度）
FAN_COLS = 32               # 列数（x 方向，水平跨度）
FAN_ROW_REF_HEIGHT_M = 1.0  # 顶部行(row 23)参考高度；真机实测行号自底部起 row0=物理底部，值占位待标定

# 物理风速：0 ~ 上限（m/s）。所有「风速类」字段复用这份约束。
Speed = Annotated[float, Field(ge=0.0, le=MAX_WIND_SPEED_MPS)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MeasurementPoint(Contract):
    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    x_mm: float = Field(ge=0, le=10000)
    y_mm: float = Field(ge=0, le=10000)
    fan_x: int = Field(ge=0, strict=True)
    fan_y: int = Field(ge=0, strict=True)
    target_mps: float = Field(ge=0)
    target_ti: float | None = Field(default=None, ge=0, le=2)


class FlapSetting(Contract):
    angle_deg: float = Field(default=0, ge=-45, le=45)
    amplitude_deg: float = Field(default=0, ge=0, le=30)
    frequency_hz: float = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def check_travel(self):
        if abs(self.angle_deg) + self.amplitude_deg > 45:
            raise ValueError("翼板角度加振幅超出 ±45 度软件范围")
        return self


class ExperimentSpec(Contract):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    project_id: str = Field(default="wind-tunnel", min_length=1, max_length=100)
    calibration_version: str = Field(
        default="simulation-v1", min_length=1, max_length=100
    )
    device_id: str = Field(default="simulator", min_length=1, max_length=100)
    mode: Literal["baseline", "deepseek"] = "baseline"
    rows: int = Field(default=24, ge=1, le=64, strict=True)
    columns: int = Field(default=32, ge=1, le=64, strict=True)
    points: list[MeasurementPoint] = Field(min_length=1, max_length=256)
    initial_spec: list[dict] | None = None
    flap: FlapSetting | None = None
    tolerance_mps: float = Field(default=0.2, gt=0, le=10)
    tolerance_ti: float = Field(default=0.03, gt=0, le=1)
    max_iterations: int = Field(default=8, ge=1, le=100, strict=True)
    max_seconds: float = Field(default=600, gt=0, le=86400)
    max_speed_mps: float = Field(default=MAX_WIND_SPEED_MPS, gt=0, le=MAX_WIND_SPEED_MPS)
    max_change_mps: float = Field(default=2, gt=0, le=20)
    settle_seconds: float = Field(default=0.1, ge=0, le=120)
    sample_rate_hz: float = Field(default=100, gt=0, le=100000)
    sample_count: int = Field(default=200, ge=2, le=100000, strict=True)

    @model_validator(mode="after")
    def validate_points(self):
        if len({p.id for p in self.points}) != len(self.points):
            raise ValueError("测点编号必须唯一")
        if len({(p.fan_x, p.fan_y) for p in self.points}) != len(self.points):
            raise ValueError("第一版每个测点须映射到不同风扇单元")
        for point in self.points:
            if point.fan_x >= self.columns or point.fan_y >= self.rows:
                raise ValueError("测点映射的风扇索引越界")
            if point.target_mps > self.max_speed_mps:
                raise ValueError("目标风速超过软件上限")
        return self


class Uniform(Contract):
    type: Literal["uniform"]
    value: Speed


class Gradient(Contract):
    type: Literal["gradient"]
    axis: Literal["x", "y"]
    start: Speed = Field(alias="from")
    end: Speed = Field(alias="to")


class Rect(Contract):
    type: Literal["rect"]
    x0: int = Field(ge=0, strict=True)
    y0: int = Field(ge=0, strict=True)
    x1: int = Field(ge=0, strict=True)
    y1: int = Field(ge=0, strict=True)
    value: Speed

    @model_validator(mode="after")
    def check_endpoints(self):
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("rect 端点顺序无效：须 x0≤x1 且 y0≤y1")
        return self


class Row(Contract):
    type: Literal["row"]
    y: int = Field(ge=0, strict=True)
    value: Speed


class Column(Contract):
    type: Literal["column"]
    x: int = Field(ge=0, strict=True)
    value: Speed


class Gaussian(Contract):
    """叠加语义（局部鼓包）：在全场每个格点加 baseline + amplitude·exp(−d²/(2σ²))。"""

    type: Literal["gaussian"]
    cx: int = Field(ge=0, strict=True)
    cy: int = Field(ge=0, strict=True)
    sigma: float = Field(gt=0)
    amplitude: Speed
    baseline: Speed = 0.0


class Profile(Contract):
    """一维剖面。

    law="power"：u(z)=v_ref·(z/z_ref)^alpha，高度 z 来自设备行高映射；
    law="custom"：直接给每行/列一个风速，长度须等于该轴长度（在 specs.expand_field 校验）。
    """

    type: Literal["profile"]
    axis: Literal["x", "y"]
    law: Literal["power", "custom"]
    alpha: float | None = Field(default=None, gt=0)
    z_ref: float | None = Field(default=None, gt=0)
    v_ref: Speed | None = None
    values: list[Speed] | None = None

    @model_validator(mode="after")
    def check_law(self):
        if self.law == "power":
            if self.alpha is None or self.z_ref is None or self.v_ref is None:
                raise ValueError("law='power' 须提供 alpha、z_ref、v_ref")
            if self.values is not None:
                raise ValueError("law='power' 不应提供 values")
        else:  # custom
            if self.values is None:
                raise ValueError("law='custom' 须提供 values")
            if self.alpha is not None or self.z_ref is not None or self.v_ref is not None:
                raise ValueError("law='custom' 不应提供 alpha/z_ref/v_ref")
        return self


class Raw(Contract):
    """逃生门：24×32 档位矩阵（0~253），跳过风速换算，只在设备层叠加。"""

    type: Literal["raw"]
    matrix: list[list[int]]

    @model_validator(mode="after")
    def check_matrix(self):
        if len(self.matrix) != FAN_ROWS:
            raise ValueError(f"raw matrix 须 {FAN_ROWS} 行")
        for i, row in enumerate(self.matrix):
            if len(row) != FAN_COLS:
                raise ValueError(f"raw matrix 第 {i} 行须 {FAN_COLS} 列")
            if any(not (0 <= x <= RAW_MAX) for x in row):
                raise ValueError(f"raw matrix 第 {i} 行存在越界档位（须 0~{RAW_MAX}）")
        return self


Primitive = Annotated[
    Uniform | Gradient | Rect | Row | Column | Gaussian | Profile | Raw,
    Field(discriminator="type"),
]


class FanArgs(Contract):
    spec: list[Primitive] = Field(min_length=1, max_length=4096)


class EmptyArgs(Contract):
    pass


class AdjustmentArgs(Contract):
    gain: float = Field(default=0.8, gt=0, le=1)


class FinishArgs(Contract):
    reason: str = Field(min_length=1, max_length=1000)


class ToolDecision(Contract):
    tool: Literal[
        "set_fan_field",
        "set_flap",
        "scan_field",
        "analyze_field",
        "propose_adjustment",
        "finish",
    ]
    arguments: dict = Field(default_factory=dict)
    summary: str = Field(min_length=1, max_length=2000)
