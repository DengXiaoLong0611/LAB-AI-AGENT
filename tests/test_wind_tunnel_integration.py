"""王凯 set_fan_field 工具接入后的集成验证。

覆盖：档位换算、gaussian 叠加语义、profile 高度映射、raw 跳过风速展开，
以及 WindTunnelDevices 生成 770 字节帧、跑通完整实验并落到操作日志。
"""

import tempfile
import unittest
from pathlib import Path

from labagent.models import FAN_COLS, FAN_ROWS, ExperimentSpec
from labagent.runner import ExperimentRunner
from labagent.specs import expand_field
from labagent.store import LabStore
from labagent.tools.devices import WindTunnelDevices, speed_to_level


def spec_2x2(**updates):
    data = dict(
        name="2x2 单元测试",
        points=[
            dict(id="a", x_mm=0, y_mm=0, fan_x=0, fan_y=0, target_mps=6),
            dict(id="b", x_mm=100, y_mm=0, fan_x=1, fan_y=0, target_mps=3),
        ],
        rows=2,
        columns=2,
        max_iterations=6,
        tolerance_mps=0.15,
    )
    data.update(updates)
    return ExperimentSpec.model_validate(data)


def spec_24x32(**updates):
    data = dict(
        name="24x32 风扇阵列",
        points=[
            dict(id="p0", x_mm=0, y_mm=0, fan_x=0, fan_y=0, target_mps=6),
            dict(id="p1", x_mm=100, y_mm=0, fan_x=1, fan_y=0, target_mps=3),
        ],
        rows=24,
        columns=32,
        max_iterations=8,
        tolerance_mps=0.15,
    )
    data.update(updates)
    return ExperimentSpec.model_validate(data)


class SpeedToLevelTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(speed_to_level(0), 0)
        self.assertEqual(speed_to_level(20), 253)
        self.assertEqual(speed_to_level(3), 38)


class ExpandSemanticsTests(unittest.TestCase):
    def test_gaussian_is_additive(self):
        field = expand_field(
            [
                {"type": "uniform", "value": 2},
                {"type": "gaussian", "cx": 0, "cy": 0, "sigma": 1, "amplitude": 3, "baseline": 0},
            ],
            spec_2x2(),
        )
        self.assertAlmostEqual(field[0][0], 5.0)  # 2 + 3*exp(0)，叠加而非覆盖

    def test_profile_power_uses_height_map(self):
        field = expand_field(
            [{"type": "profile", "axis": "y", "law": "power", "alpha": 1, "z_ref": 1, "v_ref": 2}],
            spec_2x2(),
        )
        self.assertAlmostEqual(field[0][0], 0.0)  # row0=底部 → z=0
        self.assertAlmostEqual(field[1][0], 2.0)  # row1=顶部 → z=1 → v_ref

    def test_raw_is_skipped_in_mps_expansion(self):
        raw = [[0] * FAN_COLS for _ in range(FAN_ROWS)]
        field = expand_field(
            [{"type": "uniform", "value": 3}, {"type": "raw", "matrix": raw}], spec_24x32()
        )
        self.assertAlmostEqual(field[0][0], 3.0)


class WindTunnelDevicesTests(unittest.TestCase):
    def test_set_fan_field_produces_770_byte_frame(self):
        device = WindTunnelDevices(spec_24x32())
        result = device.set_fan_field([{"type": "uniform", "value": 3}], "op-1")
        self.assertTrue(result["completed"])
        self.assertEqual(result["frame_bytes"], 770)
        self.assertEqual(result["level_max"], 38)
        self.assertEqual(len(device.last_frame), 770)
        self.assertEqual(device.last_frame[0], 0xFE)
        self.assertEqual(device.last_frame[-1], 0xFF)


class IntegrationTests(unittest.TestCase):
    def test_runner_uses_wind_tunnel_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory))
            task = store.create_task(spec_24x32())
            runner = ExperimentRunner(store, WindTunnelDevices)
            runner.run(task["id"])
            runner.close()
            final = store.get_task(task["id"])
            self.assertEqual(final["status"], "succeeded", final)
            fans = [
                op for op in store.operations(task["id"]) if op["tool"] == "set_fan_field"
            ]
            self.assertTrue(fans)
            self.assertEqual(fans[0]["result"]["frame_bytes"], 770)
            self.assertEqual(fans[0]["result"]["simulated"], True)  # dry-run


if __name__ == "__main__":
    unittest.main()
