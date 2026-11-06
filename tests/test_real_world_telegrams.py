"""Regression fixtures captured from a live Home Assistant JeeLink receiver.

Source: LaCrosse Jeelink v0.3.0 raw DEBUG telegrams, 2026-11-06.
These cases validate parsing only; they do not prove USB lifecycle
or end-to-end Home Assistant migration compatibility.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng import parse_telegram


class TestCapturedRealWorldTelegrams(unittest.TestCase):
    def test_pantry_sensor_temperature_humidity(self) -> None:
        reading = parse_telegram("OK 9 10 1 4 176 38")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.sensor_id, 10)
        self.assertAlmostEqual(reading.temperature, 20.0)
        self.assertEqual(reading.humidity, 38)
        self.assertFalse(reading.external_probe)
        self.assertFalse(reading.low_battery)
        self.assertFalse(reading.new_battery)

    def test_laundry_sensor_temperature_variation(self) -> None:
        for raw, expected in (
            ("OK 9 60 1 4 199 36", 22.3),
            ("OK 9 60 1 4 200 36", 22.4),
        ):
            with self.subTest(raw=raw):
                reading = parse_telegram(raw)
                self.assertIsNotNone(reading)
                self.assertEqual(reading.sensor_id, 60)
                self.assertAlmostEqual(reading.temperature, expected)
                self.assertEqual(reading.humidity, 36)
                self.assertEqual(reading.raw_flags, 1)

    def test_freezer_negative_temperature_invalid_humidity(self) -> None:
        raw = "OK 9 5 1 3 21 106"
        reading = parse_telegram(raw)
        self.assertIsNotNone(reading)
        self.assertEqual(reading.sensor_id, 5)
        self.assertAlmostEqual(reading.temperature, -21.1)
        self.assertIsNone(reading.humidity)
        self.assertEqual(reading.raw_humidity, 106)
        self.assertEqual(reading.raw_telegram, raw)
        self.assertFalse(reading.low_battery)

    def test_real_world_telegram_bytes_supported(self) -> None:
        reading = parse_telegram(b"OK 9 5 1 3 21 106\r\n")
        self.assertIsNotNone(reading)
        self.assertAlmostEqual(reading.temperature, -21.1)
        self.assertIsNone(reading.humidity)


if __name__ == "__main__":
    unittest.main()
