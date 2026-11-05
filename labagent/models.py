"""实验契约。风扇索引与探针毫米坐标分别声明。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    max_speed_mps: float = Field(default=20, gt=0, le=100)
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
    value: float = Field(ge=0)


class Gradient(Contract):
    type: Literal["gradient"]
    axis: Literal["x", "y"]
    start: float = Field(alias="from", ge=0)
    end: float = Field(alias="to", ge=0)


class Rect(Contract):
    type: Literal["rect"]
    x0: int = Field(ge=0, strict=True)
    y0: int = Field(ge=0, strict=True)
    x1: int = Field(ge=0, strict=True)
    y1: int = Field(ge=0, strict=True)
    value: float = Field(ge=0)


class Row(Contract):
    type: Literal["row"]
    y: int = Field(ge=0, strict=True)
    value: float = Field(ge=0)


class Column(Contract):
    type: Literal["column"]
    x: int = Field(ge=0, strict=True)
    value: float = Field(ge=0)


class Gaussian(Contract):
    type: Literal["gaussian"]
    cx: float = Field(ge=0)
    cy: float = Field(ge=0)
    sigma: float = Field(gt=0)
    amplitude: float = Field(ge=0)
    baseline: float = Field(default=0, ge=0)


class Profile(Contract):
    type: Literal["profile"]
    axis: Literal["x", "y"]
    law: Literal["power", "custom"]
    values: list[float] | None = None
    heights_m: list[float] | None = None
    alpha: float | None = Field(default=None, ge=0, le=2)
    z_ref: float | None = Field(default=None, gt=0)
    v_ref: float | None = Field(default=None, ge=0)


Primitive = Annotated[
    Uniform | Gradient | Rect | Row | Column | Gaussian | Profile,
    Field(discriminator="type"),
]


class FanArgs(Contract):
    spec: list[Primitive] = Field(min_length=1, max_length=300)


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
