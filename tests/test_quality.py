"""Regression tests for opt-in temperature plausibility checks."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.parser import parse_telegram
from pylacrosse_ng.quality import (
    TemperatureQualityResult,
    TemperatureQualityRules,
    assess_temperature,
)


class TestTemperatureQuality(unittest.TestCase):
    def setUp(self) -> None:
        self.internal = parse_telegram("OK 9 25 2 4 150 106")
        self.external = parse_telegram("OK 9 25 130 4 200 106")
        assert self.internal is not None
        assert self.external is not None

    def test_default_has_no_filter_and_preserves_reading(self) -> None:
        result = assess_temperature(self.internal)
        self.assertIsInstance(result, TemperatureQualityResult)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.reason)
        self.assertEqual(result.temperature, 17.4)
        self.assertEqual(self.internal.temperature, 17.4)

    def test_minimum_and_maximum_are_inclusive(self) -> None:
        exact = TemperatureQualityRules(minimum=17.4, maximum=17.4)
        self.assertTrue(assess_temperature(self.internal, rules=exact).accepted)
        too_cold = assess_temperature(
            self.internal, rules=TemperatureQualityRules(minimum=18)
        )
        self.assertEqual((too_cold.accepted, too_cold.reason), (False, "below_minimum"))
        too_hot = assess_temperature(
            self.internal, rules=TemperatureQualityRules(maximum=17)
        )
        self.assertEqual((too_hot.accepted, too_hot.reason), (False, "above_maximum"))

    def test_max_change_accepts_boundary_and_rejects_larger_jump(self) -> None:
        rules = TemperatureQualityRules(max_change=2)
        self.assertTrue(
            assess_temperature(
                self.internal, rules=rules, previous_temperature=15.4
            ).accepted
        )
        rejected = assess_temperature(
            self.internal, rules=rules, previous_temperature=10
        )
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, "excessive_change")
        self.assertEqual(rejected.temperature, 17.4)

    def test_first_measurement_has_no_change_reference(self) -> None:
        result = assess_temperature(
            self.internal,
            rules=TemperatureQualityRules(max_change=0),
        )
        self.assertTrue(result.accepted)

    def test_tx25tp_external_channel_uses_its_own_history(self) -> None:
        self.assertTrue(self.external.external_probe)
        self.assertFalse(self.internal.external_probe)
        self.assertEqual(self.external.temperature, 22.4)
        rules = TemperatureQualityRules(max_change=1)
        self.assertTrue(
            assess_temperature(
                self.external, rules=rules, previous_temperature=22.0
            ).accepted
        )
        # Passing the internal channel's previous value would be incorrect.
        self.assertEqual(
            assess_temperature(
                self.external, rules=rules,
                previous_temperature=self.internal.temperature,
            ).reason,
            "excessive_change",
        )

    def test_non_finite_measurement_is_rejected_even_without_rules(self) -> None:
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(invalid=invalid):
                result = assess_temperature(
                    replace(self.internal, temperature=invalid)
                )
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, "non_finite")

    def test_invalid_rules_and_non_finite_previous_temperature(self) -> None:
        for settings in (
            {"minimum": math.nan},
            {"maximum": math.inf},
            {"max_change": -1},
            {"max_change": math.inf},
            {"minimum": 20, "maximum": 10},
        ):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    TemperatureQualityRules(**settings)
        with self.assertRaises(ValueError):
            assess_temperature(
                self.internal,
                rules=TemperatureQualityRules(max_change=2),
                previous_temperature=math.nan,
            )

    def test_quality_results_and_rules_are_immutable(self) -> None:
        rules = TemperatureQualityRules(minimum=-50, maximum=50)
        result = assess_temperature(self.internal, rules=rules)
        with self.assertRaises(FrozenInstanceError):
            rules.minimum = -100
        with self.assertRaises(FrozenInstanceError):
            result.accepted = False


if __name__ == "__main__":
    unittest.main()
