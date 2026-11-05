"""Regression tests for the thread-safe JeeLink sensor registry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.discovery import DiscoveredSensor, SensorRegistry
from pylacrosse_ng.parser import parse_telegram


NOW = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


def reading(radio_id: int, flags: int = 1, humidity: int = 45):
    packet = parse_telegram(f"OK 9 {radio_id} {flags} 4 150 {humidity}")
    assert packet is not None
    return packet


class TestSensorRegistry(unittest.TestCase):
    def test_configured_ignored_and_unknown_classification(self) -> None:
        registry = SensorRegistry(configured_ids={5}, ignored_ids={8})
        self.assertEqual(registry.classification(5), "configured")
        self.assertEqual(registry.classification(8), "ignored")
        self.assertEqual(registry.classification(60), "unknown")
        self.assertEqual(registry.configured_ids, frozenset({5}))
        self.assertEqual(registry.ignored_ids, frozenset({8}))

    def test_ignored_sensor_is_hidden_but_preserved_and_restorable(self) -> None:
        registry = SensorRegistry(configured_ids={5}, ignored_ids={8})
        registry.observe(reading(5), received_at=NOW)
        registry.observe(reading(8), received_at=NOW)
        registry.observe(reading(60), received_at=NOW)
        self.assertEqual(
            [sensor.radio_id for sensor in registry.discoveries()], [5, 60]
        )
        hidden = registry.discoveries(include_ignored=True)
        self.assertEqual([sensor.radio_id for sensor in hidden], [5, 8, 60])
        self.assertEqual(hidden[1].classification, "ignored")

        registry.restore(8)
        restored = registry.discoveries()
        self.assertEqual([sensor.radio_id for sensor in restored], [5, 8, 60])
        self.assertEqual(restored[1].classification, "unknown")
        self.assertEqual(restored[1].packets, 1)

        registry.ignore(8)
        self.assertEqual([s.radio_id for s in registry.discoveries()], [5, 60])

    def test_configured_ids_cannot_be_ignored_or_overlap(self) -> None:
        registry = SensorRegistry(configured_ids={5}, ignored_ids={8})
        with self.assertRaisesRegex(ValueError, "configured"):
            registry.ignore(5)
        with self.assertRaisesRegex(ValueError, "configured and ignored"):
            registry.set_memberships(configured_ids={5, 8}, ignored_ids={8})
        # Failed atomic update must preserve existing memberships.
        self.assertEqual(registry.configured_ids, frozenset({5}))
        self.assertEqual(registry.ignored_ids, frozenset({8}))
        with self.assertRaises(ValueError):
            SensorRegistry(configured_ids={5}, ignored_ids={5})

    def test_membership_changes_reclassify_without_new_packets(self) -> None:
        registry = SensorRegistry()
        first = registry.observe(reading(5), received_at=NOW)
        self.assertEqual(first.classification, "unknown")
        registry.set_memberships(configured_ids={5}, ignored_ids=set())
        self.assertEqual(registry.discoveries()[0].classification, "configured")
        registry.set_memberships(configured_ids=set(), ignored_ids={5})
        self.assertEqual(registry.discoveries(), ())
        self.assertEqual(
            registry.discoveries(include_ignored=True)[0].classification,
            "ignored",
        )
        self.assertEqual(first.classification, "unknown")

    def test_discovery_history_and_immutable_snapshot(self) -> None:
        registry = SensorRegistry()
        first = registry.observe(reading(10), received_at=NOW)
        later = NOW + timedelta(seconds=30)
        second = registry.observe(reading(10, humidity=55), received_at=later)
        self.assertIsInstance(second, DiscoveredSensor)
        self.assertEqual(first.packets, 1)
        self.assertEqual(second.packets, 2)
        self.assertEqual(first.last_seen, NOW)
        self.assertEqual(second.first_seen, NOW)
        self.assertEqual(second.last_seen, later)
        self.assertEqual(second.last_humidity, 55)
        with self.assertRaises(FrozenInstanceError):
            second.packets = 99

    def test_tx25tp_external_packet_does_not_fake_battery_change(self) -> None:
        registry = SensorRegistry()
        external = registry.observe(reading(25, flags=130, humidity=106), received_at=NOW)
        self.assertEqual(external.external_packets, 1)
        self.assertEqual(external.internal_packets, 0)
        self.assertIsNone(external.last_new_battery_seen)
        self.assertIsNone(external.last_humidity)

        later = NOW + timedelta(seconds=10)
        genuine = registry.observe(reading(25, flags=129), received_at=later)
        self.assertEqual(genuine.last_new_battery_seen, later)
        normal = registry.observe(
            reading(25), received_at=later + timedelta(seconds=10)
        )
        self.assertEqual(normal.last_new_battery_seen, later)
        self.assertEqual(normal.internal_packets, 2)
        self.assertEqual(normal.external_packets, 1)

    def test_received_packets_do_not_change_ignored_membership(self) -> None:
        registry = SensorRegistry(ignored_ids={8})
        for offset in range(3):
            record = registry.observe(
                reading(8, flags=129), received_at=NOW + timedelta(seconds=offset)
            )
            self.assertEqual(record.classification, "ignored")
        self.assertEqual(registry.ignored_ids, frozenset({8}))
        self.assertEqual(registry.discoveries(), ())
        self.assertEqual(
            registry.discoveries(include_ignored=True)[0].packets, 3
        )

    def test_invalid_radio_ids_and_naive_timestamps_are_rejected(self) -> None:
        registry = SensorRegistry()
        for value in (-1, 256):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    registry.ignore(value)
        for value in (True, 2.5, "5"):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    registry.classification(value)
        with self.assertRaises(ValueError):
            registry.observe(
                reading(5), received_at=NOW.replace(tzinfo=None)
            )
        with self.assertRaises(ValueError):
            registry.set_memberships(configured_ids={256}, ignored_ids=set())

    def test_concurrent_observations_do_not_lose_packet_counts(self) -> None:
        registry = SensorRegistry()
        errors: list[Exception] = []
        workers = 5
        packets_per_worker = 50
        gate = threading.Barrier(workers)

        def record_packets() -> None:
            try:
                gate.wait(timeout=2.0)
                for _ in range(packets_per_worker):
                    registry.observe(reading(5), received_at=NOW)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=record_packets) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3.0)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(registry.discoveries()[0].packets, 250)


if __name__ == "__main__":
    unittest.main()
