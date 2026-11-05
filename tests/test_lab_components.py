import math
import unittest

from labagent.models import ExperimentSpec
from labagent.specs import expand_field
from labagent.tools.analysis import analyze_samples, evaluate_field
from labagent.tools.control import FeedbackStrategy


def example_spec(**updates):
    data = dict(
        name="左右不同风速",
        points=[
            dict(id="left", x_mm=0, y_mm=0, fan_x=0, fan_y=0, target_mps=6),
            dict(id="right", x_mm=100, y_mm=0, fan_x=1, fan_y=0, target_mps=3),
        ],
        rows=2,
        columns=2,
        max_iterations=8,
        tolerance_mps=0.15,
    )
    data.update(updates)
    return ExperimentSpec.model_validate(data)


class ComponentTests(unittest.TestCase):
    def test_layers_and_coordinate_validation(self):
        spec = example_spec()
        field = expand_field(
            [{"type": "uniform", "value": 2}, {"type": "column", "x": 0, "value": 6}],
            spec,
        )
        self.assertEqual(field, [[6, 2], [6, 2]])
        with self.assertRaises(ValueError):
            expand_field(
                [{"type": "rect", "x0": 0, "y0": 0, "x1": 1, "y1": 2, "value": 3}], spec
            )
        with self.assertRaises(ValueError):
            expand_field([{"type": "uniform", "value": math.nan}], spec)
        with self.assertRaises(ValueError):
            example_spec(
                points=[dict(id="a", x_mm=0, y_mm=0, fan_x=3, fan_y=0, target_mps=3)]
            )

    def test_statistics_and_zero_mean(self):
        result = analyze_samples([2, 4, 2, 4], sample_rate_hz=100)
        self.assertEqual(result["mean_mps"], 3)
        self.assertAlmostEqual(result["std_mps"], 1)
        self.assertAlmostEqual(result["turbulence_intensity"], 1 / 3)
        self.assertIsNone(
            analyze_samples([0, 0], sample_rate_hz=10)["turbulence_intensity"]
        )
        for samples in ([1], [1, math.nan], [-1, 1]):
            with self.assertRaises(ValueError):
                analyze_samples(samples, sample_rate_hz=10)

    def test_missing_points_cannot_pass(self):
        with self.assertRaises(ValueError):
            evaluate_field(example_spec(), [])

    def test_feedback_is_bounded(self):
        spec = example_spec()
        strategy = FeedbackStrategy()
        proposed = strategy.propose(
            spec,
            [{"type": "uniform", "value": 2}],
            [{"point_id": "left", "mean_mps": 1}, {"point_id": "right", "mean_mps": 4}],
        )
        field = expand_field(proposed, spec)
        self.assertGreater(field[0][0], 2)
        self.assertLess(field[0][1], 2)
        self.assertLessEqual(field[0][0] - 2, spec.max_change_mps)
