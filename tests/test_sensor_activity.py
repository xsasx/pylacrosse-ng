"""Deterministic tests for adaptive ACTIVE/STALE sensor activity."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.receiver import (
    LaCrosseReceiver,
    SensorActivity,
    SensorReception,
)


NOW = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


class TestSensorActivity(unittest.TestCase):
    def setUp(self) -> None:
        # The activity API reads stored reception snapshots; no USB worker is
        # needed to verify its deterministic time and threshold calculations.
        self.receiver = LaCrosseReceiver(object(), lambda _: None)

    def add_sensor(
        self,
        sensor_id: int,
        *,
        age_seconds: float,
        average_interval: float | None = None,
        packets: int = 1,
        external_packets: int = 0,
    ) -> None:
        last_seen = NOW - timedelta(seconds=age_seconds)
        sensor = SensorReception(
            sensor_id=sensor_id,
            packets=packets,
            last_seen=last_seen,
            internal_packets=packets - external_packets,
            external_packets=external_packets,
            first_seen=last_seen,
            average_interval_seconds=average_interval,
        )
        with self.receiver._health_lock:
            self.receiver._sensor_reception[sensor_id] = sensor

    def test_empty_discovery_has_no_activity(self) -> None:
        self.assertEqual(self.receiver.sensor_activity(now=NOW), ())

    def test_one_packet_uses_default_ten_minute_timeout(self) -> None:
        self.add_sensor(5, age_seconds=599)
        activity = self.receiver.sensor_activity(now=NOW)[0]
        self.assertIsInstance(activity, SensorActivity)
        self.assertEqual(activity.status, "ACTIVE")
        self.assertEqual(activity.stale_after_seconds, 600)
        self.assertEqual(activity.age_seconds, 599)
        self.assertEqual(activity.packets, 1)
        with self.assertRaises(FrozenInstanceError):
            activity.status = "STALE"

    def test_exact_timeout_boundary_is_stale(self) -> None:
        self.add_sensor(5, age_seconds=600)
        self.assertEqual(
            self.receiver.sensor_activity(now=NOW)[0].status, "STALE"
        )

    def test_slow_sensor_uses_adaptive_interval_threshold(self) -> None:
        # Five-minute average interval => three intervals = 15 minutes.
        self.add_sensor(
            10, age_seconds=899, average_interval=300, packets=4
        )
        active = self.receiver.sensor_activity(now=NOW)[0]
        self.assertEqual(active.stale_after_seconds, 900)
        self.assertEqual(active.status, "ACTIVE")
        self.assertEqual(
            self.receiver.sensor_activity(
                now=NOW + timedelta(seconds=1)
            )[0].status,
            "STALE",
        )

    def test_multiple_sensors_are_independent_and_sorted(self) -> None:
        self.add_sensor(60, age_seconds=1200, average_interval=600, packets=6)
        self.add_sensor(5, age_seconds=601, average_interval=30, packets=7)
        activities = self.receiver.sensor_activity(now=NOW)
        self.assertEqual([a.sensor_id for a in activities], [5, 60])
        self.assertEqual([a.status for a in activities], ["STALE", "ACTIVE"])
        self.assertEqual(
            [a.stale_after_seconds for a in activities], [600, 1800]
        )
        self.assertEqual(self.receiver.health.status, "stopped")

    def test_custom_thresholds_and_dual_channel_sensor(self) -> None:
        self.add_sensor(
            25, age_seconds=240, average_interval=100,
            packets=5, external_packets=2,
        )
        result = self.receiver.sensor_activity(
            now=NOW, minimum_timeout=120, interval_multiplier=2.5
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stale_after_seconds, 250)
        self.assertEqual(result[0].status, "ACTIVE")
        self.assertEqual(result[0].packets, 5)
        self.assertEqual(
            self.receiver.sensor_activity(
                now=NOW + timedelta(seconds=10),
                minimum_timeout=120, interval_multiplier=2.5,
            )[0].status,
            "STALE",
        )

    def test_future_timestamp_age_is_clamped_to_zero(self) -> None:
        self.add_sensor(5, age_seconds=-30)
        result = self.receiver.sensor_activity(now=NOW)[0]
        self.assertEqual(result.age_seconds, 0)
        self.assertEqual(result.status, "ACTIVE")

    def test_invalid_options_and_naive_timestamps_are_rejected(self) -> None:
        options = (
            {"minimum_timeout": 0},
            {"minimum_timeout": -1},
            {"minimum_timeout": math.nan},
            {"minimum_timeout": math.inf},
            {"interval_multiplier": 0.5},
            {"interval_multiplier": math.nan},
            {"interval_multiplier": math.inf},
        )
        for values in options:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.receiver.sensor_activity(now=NOW, **values)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.receiver.sensor_activity(now=NOW.replace(tzinfo=None))


if __name__ == "__main__":
    unittest.main()
