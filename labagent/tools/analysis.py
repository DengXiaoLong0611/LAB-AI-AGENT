"""基础风场分析。数值细节封装在一个分析工具内。"""

import math
from statistics import fmean, pstdev

from ..models import ExperimentSpec


def analyze_samples(samples: list[float], *, sample_rate_hz: float) -> dict:
    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("采样率必须为正有限数")
    if len(samples) < 2 or any(
        isinstance(x, bool)
        or not isinstance(x, (float, int))
        or not math.isfinite(x)
        or x < 0
        for x in samples
    ):
        raise ValueError("需要至少两个非负有限风速样本，单位 m/s")
    mean, std = fmean(samples), pstdev(samples)
    return {
        "mean_mps": mean,
        "std_mps": std,
        "turbulence_intensity": std / mean if mean > 1e-9 else None,
        "sample_count": len(samples),
        "sample_rate_hz": sample_rate_hz,
        "duration_seconds": len(samples) / sample_rate_hz,
    }


def evaluate_field(config: ExperimentSpec, measurements: list[dict]) -> dict:
    by_id = {m["point_id"]: m for m in measurements}
    if len(by_id) != len(measurements) or set(by_id) != {p.id for p in config.points}:
        raise ValueError("测点结果缺失、重复或与目标不匹配")
    points = []
    for point in config.points:
        measured = by_id[point.id]
        mean = measured["mean_mps"]
        ti = measured["turbulence_intensity"]
        if (
            not math.isfinite(mean)
            or mean < 0
            or (ti is not None and (not math.isfinite(ti) or ti < 0))
        ):
            raise ValueError("测量统计无效")
        error = mean - point.target_mps
        ti_error = (
            None if point.target_ti is None or ti is None else ti - point.target_ti
        )
        ti_pass = point.target_ti is None or (
            ti_error is not None and abs(ti_error) <= config.tolerance_ti
        )
        points.append(
            {
                **measured,
                "target_mps": point.target_mps,
                "target_ti": point.target_ti,
                "error_mps": error,
                "ti_error": ti_error,
                "passed": abs(error) <= config.tolerance_mps and ti_pass,
            }
        )
    return {
        "points": points,
        "rmse_mps": math.sqrt(fmean(p["error_mps"] ** 2 for p in points)),
        "max_error_mps": max(abs(p["error_mps"]) for p in points),
        "converged": all(p["passed"] for p in points),
    }


def comparison_svg(analysis: dict) -> str:
    """离线可下载 SVG，字符串内容仅来自经过转义的点编号和数值。"""
    from html import escape

    points = analysis["points"]
    height = 90 + len(points) * 45
    maximum = max(1, *(max(p["target_mps"], p["mean_mps"]) for p in points))
    shapes = [
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<text x="24" y="28" font-size="16">Target (blue) / measured (green), m/s</text>',
    ]
    for i, point in enumerate(points):
        y = 60 + i * 45
        shapes.append(
            f'<text x="16" y="{y + 12}" font-size="12">{escape(point["point_id"])}</text>'
        )
        for offset, key, color in [
            (0, "target_mps", "#597ef7"),
            (16, "mean_mps", "#20a59a"),
        ]:
            width = point[key] / maximum * 400
            shapes.append(
                f'<rect x="130" y="{y + offset}" width="{width:.2f}" height="12" fill="{color}"/>'
            )
            shapes.append(
                f'<text x="{140 + width:.2f}" y="{y + offset + 11}" font-size="11">{point[key]:.3f}</text>'
            )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 {height}">'
        + "".join(shapes)
        + "</svg>"
    )
