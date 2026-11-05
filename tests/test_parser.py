"""Regression tests for the standalone JeeLink telegram parser."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

# Keep tests runnable before the package is installed in CI.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.parser import LaCrosseReading, parse_telegram


class TestParser(unittest.TestCase):
    def test_regular_temperature_and_humidity(self) -> None:
        reading = parse_telegram("OK 9 248 1 4 150 56")
        self.assertIsInstance(reading, LaCrosseReading)
        self.assertEqual(reading.sensor_id, 248)
        self.assertAlmostEqual(reading.temperature, 17.4)
        self.assertEqual(reading.humidity, 56)
        self.assertEqual(reading.sensor_type, 1)
        self.assertFalse(reading.low_battery)
        self.assertFalse(reading.new_battery)
        self.assertFalse(reading.external_probe)

    def test_accepts_bytes_and_trailing_newline(self) -> None:
        reading = parse_telegram(b"OK 9 5 1 4 150 45\r\n")
        self.assertIsNotNone(reading)
        self.assertEqual(reading.raw_telegram, "OK 9 5 1 4 150 45")

    def test_tx25tp_external_probe_is_not_a_battery_change(self) -> None:
        reading = parse_telegram("OK 9 60 130 4 160 106")
        self.assertIsNotNone(reading)
        self.assertTrue(reading.external_probe)
        self.assertFalse(reading.new_battery)
        self.assertEqual(reading.raw_flags, 130)
        self.assertIsNone(reading.humidity)
        self.assertAlmostEqual(reading.temperature, 18.4)

    def test_internal_and_external_channels_keep_distinct_values(self) -> None:
        internal = parse_telegram("OK 9 60 2 4 150 106")
        external = parse_telegram("OK 9 60 130 4 180 106")
        self.assertIsNotNone(internal)
        self.assertIsNotNone(external)
        self.assertEqual(internal.sensor_id, external.sensor_id)
        self.assertFalse(internal.external_probe)
        self.assertTrue(external.external_probe)
        self.assertNotEqual(internal.temperature, external.temperature)

    def test_invalid_humidity_is_not_treated_as_a_measurement(self) -> None:
        for humidity in (106, 125):
            with self.subTest(humidity=humidity):
                reading = parse_telegram(f"OK 9 5 1 4 150 {humidity}")
                self.assertIsNotNone(reading)
                self.assertIsNone(reading.humidity)
                self.assertEqual(reading.raw_humidity, humidity)

    def test_low_battery_bit(self) -> None:
        reading = parse_telegram("OK 9 5 1 4 150 173")
        self.assertIsNotNone(reading)
        self.assertTrue(reading.low_battery)
        self.assertEqual(reading.humidity, 45)

    def test_genuine_new_battery_bit(self) -> None:
        reading = parse_telegram("OK 9 5 129 4 150 45")
        self.assertIsNotNone(reading)
        self.assertTrue(reading.new_battery)
        self.assertFalse(reading.external_probe)

    def test_incomplete_unknown_and_out_of_range_telegrams(self) -> None:
        invalid = (
            "",
            "noise",
            "OK 9 5 1 4 150",
            "OK 8 5 1 4 150 45",
            "OK 9 5 1 4 150 45 extra",
            "OK 9 5 1 4 150 256",
            "OK 9 5 -1 4 150 45",
            b"\xff\xfe",
        )
        for telegram in invalid:
            with self.subTest(telegram=telegram):
                self.assertIsNone(parse_telegram(telegram))

    def test_rejects_unsupported_input_types(self) -> None:
        with self.assertRaises(TypeError):
            parse_telegram(123)


if __name__ == "__main__":
    unittest.main()
