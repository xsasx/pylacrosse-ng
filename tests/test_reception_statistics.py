"""Packet reception statistics regression tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timezone
from pathlib import Path
import queue
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.connection import SerialConnectionError
from pylacrosse_ng.receiver import LaCrosseReceiver, ReceptionStatistics, SensorReception


STANDARD = b"OK 9 5 1 4 150 45\n"
EXTERNAL = b"OK 9 5 130 4 170 106\n"
OTHER = b"OK 9 10 1 4 155 55\n"


class FakeConnection:
    def __init__(self) -> None:
        self.incoming: queue.Queue[bytes | Exception] = queue.Queue()
        self.opens = 0
        self.closes = 0

    def open(self) -> None:
        self.opens += 1

    def readline(self) -> bytes:
        try:
            item = self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closes += 1


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Receiver statistics did not reach expected state")


class TestReceptionStatistics(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = FakeConnection()
        self.received = []
        self.receiver = LaCrosseReceiver(
            self.connection, self.received.append,
            retry_initial=0.02, retry_max=0.05,
        )

    def tearDown(self) -> None:
        self.receiver.stop()

    def start_receiver(self) -> None:
        self.receiver.start()
        wait_for(lambda: self.receiver.is_connected)

    def test_empty_statistics_and_immutable_snapshots(self) -> None:
        stats = self.receiver.reception_statistics
        self.assertIsInstance(stats, ReceptionStatistics)
        self.assertEqual(stats.valid_packets, 0)
        self.assertEqual(stats.invalid_packets, 0)
        self.assertEqual(stats.sensors, ())
        with self.assertRaises(FrozenInstanceError):
            stats.valid_packets = 10

        self.start_receiver()
        self.connection.incoming.put(STANDARD)
        wait_for(lambda: self.receiver.reception_statistics.valid_packets == 1)
        sensor = self.receiver.reception_statistics.sensors[0]
        self.assertIsInstance(sensor, SensorReception)
        self.assertEqual(sensor.sensor_id, 5)
        self.assertEqual(sensor.last_seen.tzinfo, timezone.utc)
        with self.assertRaises(FrozenInstanceError):
            sensor.packets = 10
        # Snapshots created earlier must not change.
        self.assertEqual(stats.valid_packets, 0)
        self.assertEqual(stats.sensors, ())

    def test_counts_valid_invalid_and_multiple_sensor_ids(self) -> None:
        self.start_receiver()
        for line in (STANDARD, b"noise\n", OTHER, STANDARD, b"OK 9 5 1\n"):
            self.connection.incoming.put(line)
        wait_for(lambda: (
            self.receiver.reception_statistics.valid_packets == 3
            and self.receiver.reception_statistics.invalid_packets == 2
        ))
        stats = self.receiver.reception_statistics
        self.assertEqual([s.sensor_id for s in stats.sensors], [5, 10])
        self.assertEqual([s.packets for s in stats.sensors], [2, 1])
        self.assertEqual(len(self.received), 3)
        self.assertEqual(stats.sensors[0].internal_packets, 2)
        self.assertEqual(stats.sensors[0].external_packets, 0)

    def test_tx25tp_channels_are_counted_separately(self) -> None:
        self.start_receiver()
        for line in (STANDARD, EXTERNAL, EXTERNAL):
            self.connection.incoming.put(line)
        wait_for(lambda: self.receiver.reception_statistics.valid_packets == 3)
        sensor = self.receiver.reception_statistics.sensors[0]
        self.assertEqual(sensor.sensor_id, 5)
        self.assertEqual(sensor.packets, 3)
        self.assertEqual(sensor.internal_packets, 1)
        self.assertEqual(sensor.external_packets, 2)
        self.assertEqual(self.receiver.reception_statistics.invalid_packets, 0)
        self.assertEqual([r.external_probe for r in self.received], [False, True, True])

    def test_radio_silence_does_not_count_as_invalid_packets(self) -> None:
        self.start_receiver()
        time.sleep(0.08)
        stats = self.receiver.reception_statistics
        self.assertEqual(stats.valid_packets, 0)
        self.assertEqual(stats.invalid_packets, 0)
        self.assertEqual(stats.sensors, ())
        self.assertIsNone(self.receiver.health.last_packet_at)

    def test_statistics_survive_reconnection_and_shutdown(self) -> None:
        self.start_receiver()
        self.connection.incoming.put(STANDARD)
        wait_for(lambda: self.receiver.reception_statistics.valid_packets == 1)
        first_seen = self.receiver.reception_statistics.sensors[0].last_seen
        self.connection.incoming.put(SerialConnectionError("USB unplugged"))
        wait_for(lambda: self.connection.opens >= 2)
        wait_for(lambda: self.receiver.is_connected)
        self.connection.incoming.put(OTHER)
        wait_for(lambda: self.receiver.reception_statistics.valid_packets == 2)
        self.assertEqual(
            [s.sensor_id for s in self.receiver.reception_statistics.sensors],
            [5, 10],
        )
        self.assertEqual(
            self.receiver.reception_statistics.sensors[0].last_seen,
            first_seen,
        )
        self.receiver.stop()
        self.assertEqual(self.receiver.reception_statistics.valid_packets, 2)
        self.assertEqual(self.receiver.reception_statistics.invalid_packets, 0)


if __name__ == "__main__":
    unittest.main()
