"""Deterministic reception interval regression tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import queue
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.receiver import LaCrosseReceiver


PACKET_5 = b"OK 9 5 1 4 150 45\n"
PACKET_10 = b"OK 9 10 1 4 160 55\n"
EXTERNAL_5 = b"OK 9 5 130 4 170 106\n"


class FakeConnection:
    def __init__(self) -> None:
        self.incoming: queue.Queue[bytes] = queue.Queue()

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def readline(self) -> bytes:
        try:
            return self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b""


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for packet statistics")


class TestReceptionIntervals(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = FakeConnection()
        self.receiver = LaCrosseReceiver(self.connection, lambda _: None)
        self.receiver.start()
        wait_for(lambda: self.receiver.is_connected)

    def tearDown(self) -> None:
        self.receiver.stop()

    def inject_and_wait(self, packet: bytes, total: int) -> None:
        self.connection.incoming.put(packet)
        wait_for(
            lambda: self.receiver.reception_statistics.valid_packets == total
        )

    def test_first_packet_has_no_interval_and_records_first_seen(self) -> None:
        first = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        with patch("pylacrosse_ng.receiver.datetime") as clock:
            clock.now.return_value = first
            self.inject_and_wait(PACKET_5, 1)
        sensor = self.receiver.reception_statistics.sensors[0]
        self.assertEqual(sensor.first_seen, first)
        self.assertEqual(sensor.last_seen, first)
        self.assertIsNone(sensor.last_interval_seconds)
        self.assertIsNone(sensor.average_interval_seconds)

    def test_last_and_average_intervals_use_packet_times(self) -> None:
        start = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        with patch("pylacrosse_ng.receiver.datetime") as clock:
            for index, seconds in enumerate((0, 10, 40), 1):
                clock.now.return_value = start + timedelta(seconds=seconds)
                self.inject_and_wait(PACKET_5, index)
        sensor = self.receiver.reception_statistics.sensors[0]
        self.assertEqual(sensor.packets, 3)
        self.assertEqual(sensor.first_seen, start)
        self.assertEqual(sensor.last_seen, start + timedelta(seconds=40))
        self.assertEqual(sensor.last_interval_seconds, 30.0)
        self.assertEqual(sensor.average_interval_seconds, 20.0)

    def test_each_sensor_has_independent_interval_history(self) -> None:
        start = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        sequence = (
            (PACKET_5, 0),
            (PACKET_10, 5),
            (PACKET_5, 20),
            (PACKET_10, 35),
        )
        with patch("pylacrosse_ng.receiver.datetime") as clock:
            for count, (packet, seconds) in enumerate(sequence, 1):
                clock.now.return_value = start + timedelta(seconds=seconds)
                self.inject_and_wait(packet, count)
        sensor_5, sensor_10 = self.receiver.reception_statistics.sensors
        self.assertEqual((sensor_5.sensor_id, sensor_10.sensor_id), (5, 10))
        self.assertEqual(sensor_5.last_interval_seconds, 20.0)
        self.assertEqual(sensor_10.last_interval_seconds, 30.0)
        self.assertEqual(sensor_5.average_interval_seconds, 20.0)
        self.assertEqual(sensor_10.average_interval_seconds, 30.0)

    def test_external_probe_packet_counts_as_same_sensor_interval(self) -> None:
        start = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        with patch("pylacrosse_ng.receiver.datetime") as clock:
            clock.now.return_value = start
            self.inject_and_wait(PACKET_5, 1)
            clock.now.return_value = start + timedelta(seconds=12)
            self.inject_and_wait(EXTERNAL_5, 2)
        sensor = self.receiver.reception_statistics.sensors[0]
        self.assertEqual(sensor.packets, 2)
        self.assertEqual(sensor.internal_packets, 1)
        self.assertEqual(sensor.external_packets, 1)
        self.assertEqual(sensor.last_interval_seconds, 12.0)
        self.assertEqual(sensor.average_interval_seconds, 12.0)


if __name__ == "__main__":
    unittest.main()
