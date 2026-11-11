"""共用的小工具。复用项目采样和分析代码，生成一份可核验的试验记录。"""

import hashlib
import json
import platform
import sys
from pathlib import Path

# 支持在任意工作目录直接执行本文件夹里的脚本。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from labagent.models import ExperimentSpec, MeasurementPoint
from labagent.tools.analysis import analyze_samples
from labagent.tools.devices import SimulatedDevices


def fingerprint(value):
    """先统一 JSON 格式，再计算 SHA-256。字典键顺序不会影响指纹。

    本例约定 Python JSON 编码。跨语言部署时需要共同采用标准化编码方案。
    """
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def collect_record():
    """一次最小模拟试验，结构对应现有 Agent 的设备工具调用。"""
    point = MeasurementPoint(
        id="p1", x_mm=100, y_mm=100, fan_x=0, fan_y=0, target_mps=5,
    )
    config = ExperimentSpec(
        name="数据存证验证", rows=1, columns=1, points=[point],
    )
    devices = SimulatedDevices(config)
    fan_spec = [{"type": "uniform", "value": 5.0}]
    devices.set_fan_field(fan_spec, "demo-set")
    devices.move_probe(point, "demo-move")
    measurement = devices.measure_hotwire(point, "demo-sample")
    analysis = analyze_samples(
        measurement["samples_mps"], sample_rate_hz=measurement["sample_rate_hz"],
    )

    # 记录直接参与采样、分析的代码版本，避免只记录结果而遗漏处理方法。
    source_files = [
        "labagent/models.py", "labagent/specs.py",
        "labagent/tools/devices.py", "labagent/tools/analysis.py",
    ]
    return {
        "schema_version": 1,
        "config": config.model_dump(),
        "commands": fan_spec,
        "measurement": measurement,
        "analysis": analysis,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "source_sha256": {
                name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
                for name in source_files
            },
            # 示例条件由程序声明，真实试验需要从可信仪器读取。
            "room_temperature_c": 25.0,
            "temperature_source": "demo-declared",
        },
    }


def save_record(record):
    """用内容指纹命名，保留不同试验的原始记录。"""
    directory = Path(__file__).resolve().parent / "output"
    directory.mkdir(exist_ok=True)
    path = directory / f"{fingerprint(record)}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def verify_record(record, expected_digest):
    """expected_digest 必须来自可信存证渠道，否则二者都可被替换。"""
    return fingerprint(record) == expected_digest
