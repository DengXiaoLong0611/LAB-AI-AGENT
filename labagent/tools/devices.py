"""硬件边界。设备回调仅在动作实际完成后返回，ACK 接收不等同完成。"""

import math
from datetime import datetime, timezone
from typing import Callable, Protocol

from ..models import ExperimentSpec, FlapSetting, MeasurementPoint
from ..specs import expand_field


class DeviceAdapter(Protocol):
    """真实适配器应自行实现硬件范围校验、超时、去重与完成状态查询。"""

    simulated: bool
    supports_flap: bool

    def set_fan_field(self, spec: list[dict], operation_id: str) -> dict: ...
    def set_flap(self, setting: dict, operation_id: str) -> dict: ...
    def move_probe(self, point: MeasurementPoint, operation_id: str) -> dict: ...
    def measure_hotwire(self, point: MeasurementPoint, operation_id: str) -> dict: ...
    def lookup_operation(self, operation_id: str) -> dict | None: ...
    def stop(self) -> dict: ...


class SimulatedDevices:
    simulated = True
    supports_flap = True

    def __init__(self, config: ExperimentSpec):
        self.config = config
        self.field = [[0.0] * config.columns for _ in range(config.rows)]
        self.flap = FlapSetting().model_dump()
        self.position = None

    def restore(self, state: dict):
        if state.get("current_spec"):
            self.field = expand_field(state["current_spec"], self.config)
        if state.get("flap_setting"):
            self.flap = state["flap_setting"]

    def set_fan_field(self, spec, operation_id):
        self.field = expand_field(spec, self.config)
        return {"completed": True, "simulated": True, "operation_id": operation_id}

    def set_flap(self, setting, operation_id):
        self.flap = FlapSetting.model_validate(setting).model_dump()
        return {"completed": True, "simulated": True, "operation_id": operation_id}

    def move_probe(self, point, operation_id):
        self.position = (point.x_mm, point.y_mm)
        return {
            "completed": True,
            "simulated": True,
            "x_mm": point.x_mm,
            "y_mm": point.y_mm,
        }

    def measure_hotwire(self, point, operation_id):
        average = sum(map(sum, self.field)) / (self.config.rows * self.config.columns)
        mean = 0.82 * self.field[point.fan_y][point.fan_x] + 0.06 * average
        ti = 0.04 + self.flap["amplitude_deg"] * 0.004
        samples = [
            max(
                0,
                mean
                * (
                    1
                    + math.sqrt(2)
                    * ti
                    * math.sin(2 * math.pi * 7 * i / self.config.sample_rate_hz)
                ),
            )
            for i in range(self.config.sample_count)
        ]
        return {
            "samples_mps": samples,
            "sample_rate_hz": self.config.sample_rate_hz,
            "point_id": point.id,
            "x_mm": point.x_mm,
            "y_mm": point.y_mm,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "quality": "good",
            "simulated": True,
            "calibration_version": self.config.calibration_version,
        }

    def lookup_operation(self, operation_id):
        return None

    def stop(self):
        return {"completed": True, "simulated": True, "message": "模拟设备已停止"}


class CallbackDevices:
    """注入王凯和华航已有 Python 程序，不在 Agent 内实现串口或采集驱动。

    set_fan_field(spec, operation_id=...) -> {completed: True, ...}
    move_probe(x_mm=..., y_mm=..., operation_id=...) -> {completed: True, ...}
    hotwire(point_id=..., x_mm=..., y_mm=..., sample_count=..., sample_rate_hz=...,
            operation_id=...) -> {samples_mps, sample_rate_hz, point_id, x_mm, y_mm,
                                 timestamp, quality: 'good', calibration_version}
    查询回调须返回持久化的、与原始调用相同的结果，未知返回 None。
    """

    simulated = False

    def __init__(
        self,
        config: ExperimentSpec,
        *,
        set_fan_field: Callable,
        move_probe: Callable,
        hotwire: Callable,
        stop: Callable,
        lookup_operation: Callable,
        set_flap: Callable | None = None,
    ):
        self.config = config
        self._fan, self._move, self._hotwire = set_fan_field, move_probe, hotwire
        self._stop, self._lookup, self._flap = stop, lookup_operation, set_flap
        self.supports_flap = set_flap is not None

    def set_fan_field(self, spec, operation_id):
        return self._fan(spec, operation_id=operation_id)

    def set_flap(self, setting, operation_id):
        if self._flap is None:
            raise ValueError("当前设备未配置翼板接口")
        return self._flap(**setting, operation_id=operation_id)

    def move_probe(self, point, operation_id):
        return self._move(x_mm=point.x_mm, y_mm=point.y_mm, operation_id=operation_id)

    def measure_hotwire(self, point, operation_id):
        return self._hotwire(
            point_id=point.id,
            x_mm=point.x_mm,
            y_mm=point.y_mm,
            sample_count=self.config.sample_count,
            sample_rate_hz=self.config.sample_rate_hz,
            operation_id=operation_id,
        )

    def lookup_operation(self, operation_id):
        return self._lookup(operation_id=operation_id)

    def stop(self):
        return self._stop()
