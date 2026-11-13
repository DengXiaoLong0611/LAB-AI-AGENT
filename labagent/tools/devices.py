"""硬件边界。设备回调仅在动作实际完成后返回，ACK 接收不等同完成。"""

import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from dotenv import load_dotenv

from ..models import MAX_WIND_SPEED_MPS, RAW_MAX, ExperimentSpec, FlapSetting, MeasurementPoint
from ..specs import expand_field

# 读取项目根目录 .env（FAN_DRY_RUN / FAN_SERIAL_PORT），与 wind_tunnel_agent 的 config 行为一致
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


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


# ---- 王凯的风扇真链路适配器（忠实移植 wind_tunnel_agent/devices/fan_controller.py）----
FAN_DRY_RUN = os.getenv("FAN_DRY_RUN", "1") == "1"
FAN_SERIAL_PORT = os.getenv("FAN_SERIAL_PORT", "COM16")
FAN_BAUD = int(os.getenv("FAN_BAUD", "115200"))


def speed_to_level(speed: float) -> int:
    """物理风速 m/s → 档位 0~253（线性标定：253 档 = MAX_WIND_SPEED_MPS m/s）。"""
    return int(max(0, min(RAW_MAX, round(speed * RAW_MAX / MAX_WIND_SPEED_MPS))))


class WindTunnelDevices(SimulatedDevices):
    """真实风扇适配器（FAN_DRY_RUN=1 时不开串口，用于虚拟验证）。

    忠实移植 FanController 的完整下发链路，协议对齐 WT_1_NEW/6X8_V2_1frame.py：
    connect(等 Arduino 启动 1.5s) → activate(预热全 0 帧 + 等 1s) → 展开(m/s)
    → 档位换算(×12.65 取整) → raw 覆盖 → 打包 770 字节帧(0xFE + 768 + 0xFF) → 下发。
    move_probe / measure_hotwire 仍沿用模拟器（尚未接真机）。
    """

    simulated = FAN_DRY_RUN  # dry-run 视为模拟；真机(FAN_DRY_RUN=0)视为真实设备

    FRAME_HEAD = 0xFE
    FRAME_TAIL = 0xFF
    BOOT_SETTLE_S = float(os.getenv("FAN_BOOT_SETTLE_S", "4.0"))  # Mac 开串口复位 Arduino，需等完 bootloader+setup（原 Windows 脚本 1.5 不够）
    ACTIVATE_SLEEP_S = 1.0  # 预热后等待（对应上位机脚本 control_activate 的 sleep(1)）

    def __init__(self, config: ExperimentSpec):
        super().__init__(config)
        self.last_frame: bytes | None = None
        self.current_levels: list[list[int]] | None = None
        self._ser = None
        self._activated = False

    # ---------- 连接（对应 FanController.connect / close / list_ports） ----------
    def connect(self):
        if FAN_DRY_RUN:
            return self
        import serial  # 真机才需要 pyserial，dry-run 不加载

        self._ser = serial.Serial(FAN_SERIAL_PORT, FAN_BAUD, timeout=0.1)
        time.sleep(self.BOOT_SETTLE_S)  # 等 Arduino 启动
        return self

    def close(self):
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    @staticmethod
    def list_ports():
        from serial.tools import list_ports as lp

        return [p.device for p in lp.comports()]

    # ---------- 预热玄学（对应 activate / _ensure_activated） ----------
    def activate(self):
        """预热：连发 2 帧全 0。原脚本 control_activate + control_all(0) 两帧才稳，
        Mac 真机实测 1 帧不够——真指令须落在第 3 帧才真正驱动 I2C/风扇。"""
        zeros = [[0] * self.config.columns for _ in range(self.config.rows)]
        self._send_levels(zeros)      # 第 1 帧（复位后的无效帧）
        time.sleep(self.ACTIVATE_SLEEP_S)
        self._send_levels(zeros)      # 第 2 帧（对应原脚本 control_all(0)）
        time.sleep(self.ACTIVATE_SLEEP_S)
        self._activated = True

    def _ensure_activated(self):
        if not self._activated and not FAN_DRY_RUN:
            self.activate()

    # ---------- 高层入口（对应 apply_spec） ----------
    def set_fan_field(self, spec, operation_id):
        if not FAN_DRY_RUN and self._ser is None:
            self.connect()  # 真机首次调用自动连接（等 Arduino 启动）
        self._ensure_activated()

        field = expand_field(spec, self.config)  # 24×32 m/s（raw 跳过）
        levels = [[speed_to_level(v) for v in row] for row in field]
        levels = self._apply_raw(spec, levels)
        self._send_levels(levels)
        self.field = field  # 供模拟测量复用，与 SimulatedDevices 行为一致

        return {
            "completed": True,
            "simulated": FAN_DRY_RUN,
            "operation_id": operation_id,
            **self._summarize(spec, field, levels),
            "frame_bytes": len(self.last_frame),
        }

    # ---------- 档位 / raw / 打包 / 下发 ----------
    @staticmethod
    def _apply_raw(spec, levels):
        for layer in spec:
            if isinstance(layer, dict) and layer.get("type") == "raw":
                matrix = layer["matrix"]
                for y in range(len(levels)):
                    for x in range(len(levels[y])):
                        levels[y][x] = max(0, min(RAW_MAX, matrix[y][x]))
        return levels

    def _pack(self, levels):
        payload = bytearray()
        for row in levels:
            payload.extend(row)
        return bytes([self.FRAME_HEAD]) + bytes(payload) + bytes([self.FRAME_TAIL])

    def _send_levels(self, levels):
        frame = self._pack(levels)
        self.last_frame = frame
        self.current_levels = levels
        if self._ser is not None:
            payload_len = len(frame) - 2
            if payload_len != 768:
                raise ValueError(f"硬件帧须 24×32=768 字节，当前 {payload_len}")
            self._ser.write(frame)
            self._ser.flush()

    # ---------- 摘要（对应 _summarize / _describe） ----------
    @staticmethod
    def _summarize(spec, field, levels):
        layers = [WindTunnelDevices._describe(layer) for layer in spec]
        non_raw = [l for l in spec if not (isinstance(l, dict) and l.get("type") == "raw")]
        if non_raw:
            flat = [v for row in field for v in row]
            speed_min, speed_max = min(flat), max(flat)
        else:
            speed_min = speed_max = None
        return {
            "n_layers": len(spec),
            "layers": layers,
            "speed_min": speed_min,
            "speed_max": speed_max,
            "level_mean": int(round(sum(sum(r) for r in levels) / (len(levels) * len(levels[0])))),
            "level_max": int(max(max(r) for r in levels)),
        }

    @staticmethod
    def _describe(layer):
        t = layer.get("type")
        if t == "uniform":
            return f"uniform {layer['value']}"
        if t == "gradient":
            return f"gradient {layer['axis']} {layer.get('from')}→{layer['to']}"
        if t == "rect":
            return f"rect ({layer['x0']},{layer['y0']})-({layer['x1']},{layer['y1']}) {layer['value']}"
        if t == "gaussian":
            return f"gaussian ({layer['cx']},{layer['cy']}) σ={layer['sigma']} A={layer['amplitude']}"
        if t == "row":
            return f"row y={layer['y']} {layer['value']}"
        if t == "column":
            return f"column x={layer['x']} {layer['value']}"
        if t == "profile":
            if layer["law"] == "power":
                return f"profile 幂律 α={layer['alpha']}"
            return f"profile custom {len(layer['values'])} 值"
        if t == "raw":
            return "raw 档位矩阵"
        return str(t)

    def stop(self):
        return {
            "completed": True,
            "simulated": FAN_DRY_RUN,
            "message": "风扇已停止（dry-run）" if FAN_DRY_RUN else "风扇已停止",
        }
