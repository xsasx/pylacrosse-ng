"""End-to-end receive-path tests: bytes -> parser -> receiver -> registry."""

from __future__ import annotations

from pathlib import Path
import queue
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.connection import SerialConnectionError
from pylacrosse_ng.discovery import SensorRegistry
from pylacrosse_ng.receiver import LaCrosseReceiver


NORMAL_5 = b"OK 9 5 1 4 150 45\n"
NORMAL_8 = b"OK 9 8 1 4 165 55\n"
NORMAL_10 = b"OK 9 10 1 4 170 65\n"
EXTERNAL_5 = b"OK 9 5 130 4 180 106\n"
BATTERY_5 = b"OK 9 5 129 4 155 45\n"


class FakeTransport:
    def __init__(self) -> None:
        self.events: queue.Queue[bytes | Exception] = queue.Queue()
        self.opens = 0
        self.closes = 0

    def open(self) -> None:
        self.opens += 1

    def readline(self) -> bytes:
        try:
            event = self.events.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(event, Exception):
            raise event
        return event

    def close(self) -> None:
        self.closes += 1

    def send(self, event: bytes | Exception) -> None:
        self.events.put(event)


def wait_for(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Receiver did not reach expected state")


class TestReceiverDiscoveryIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeTransport()
        self.registry = SensorRegistry(configured_ids={5}, ignored_ids={8})
        self.readings = []
        self.receiver = LaCrosseReceiver(
            self.transport,
            self.readings.append,
            sensor_registry=self.registry,
            retry_initial=0.02,
            retry_max=0.05,
        )
        self.receiver.start()
        wait_for(lambda: self.receiver.is_connected)

    def tearDown(self) -> None:
        self.receiver.stop()

    def send_and_wait(self, packet: bytes, count: int) -> None:
        self.transport.send(packet)
        wait_for(lambda: self.receiver.reception_statistics.valid_packets == count)
        wait_for(lambda: len(self.readings) == count)

    def test_configured_ignored_and_unknown_are_classified(self) -> None:
        for count, packet in enumerate((NORMAL_5, NORMAL_8, NORMAL_10), 1):
            self.send_and_wait(packet, count)
        self.assertIs(self.receiver.sensor_registry, self.registry)
        visible = self.receiver.sensor_registry.discoveries()
        self.assertEqual([s.radio_id for s in visible], [5, 10])
        self.assertEqual(
            [s.classification for s in visible], ["configured", "unknown"]
        )
        all_sensors = self.registry.discoveries(include_ignored=True)
        self.assertEqual([s.radio_id for s in all_sensors], [5, 8, 10])
        self.assertEqual(all_sensors[1].classification, "ignored")
        self.assertEqual(self.registry.ignored_ids, frozenset({8}))
        self.assertEqual(self.registry.configured_ids, frozenset({5}))
        self.assertEqual([r.sensor_id for r in self.readings], [5, 8, 10])

    def test_ignored_packets_cannot_mutate_memberships(self) -> None:
        for count in range(1, 4):
            self.send_and_wait(NORMAL_8, count)
        self.assertEqual(self.registry.discoveries(), ())
        self.assertEqual(self.registry.ignored_ids, frozenset({8}))
        self.assertEqual(
            self.registry.discoveries(include_ignored=True)[0].packets, 3
        )
        self.registry.restore(8)
        restored = self.registry.discoveries()
        self.assertEqual(restored[0].classification, "unknown")
        self.assertEqual(restored[0].packets, 3)

    def test_tx25tp_internal_external_and_battery_event(self) -> None:
        for count, packet in enumerate(
            (NORMAL_5, EXTERNAL_5, BATTERY_5), 1
        ):
            self.send_and_wait(packet, count)
        record = self.registry.discoveries()[0]
        self.assertEqual(record.radio_id, 5)
        self.assertEqual(record.packets, 3)
        self.assertEqual(record.internal_packets, 2)
        self.assertEqual(record.external_packets, 1)
        self.assertIsNotNone(record.last_new_battery_seen)
        self.assertEqual(
            [reading.external_probe for reading in self.readings],
            [False, True, False],
        )
        self.assertEqual(
            [reading.new_battery for reading in self.readings],
            [False, False, True],
        )

    def test_invalid_packet_does_not_create_discovery(self) -> None:
        self.transport.send(b"garbled packet\n")
        wait_for(lambda: self.receiver.reception_statistics.invalid_packets == 1)
        self.assertEqual(self.registry.discoveries(include_ignored=True), ())
        self.assertEqual(self.readings, [])
        self.send_and_wait(NORMAL_5, 1)
        self.assertEqual(self.receiver.reception_statistics.invalid_packets, 1)
        self.assertEqual(self.registry.discoveries()[0].packets, 1)

    def test_registry_and_packet_counts_survive_reconnect(self) -> None:
        self.send_and_wait(NORMAL_5, 1)
        self.transport.send(SerialConnectionError("USB unplugged"))
        wait_for(lambda: self.transport.opens >= 2)
        wait_for(lambda: self.receiver.is_connected)
        self.send_and_wait(NORMAL_5, 2)
        self.assertEqual(self.transport.opens, 2)
        self.assertEqual(self.registry.discoveries()[0].packets, 2)
        self.assertEqual(self.receiver.reception_statistics.valid_packets, 2)
        self.assertEqual(self.receiver.health.successful_connections, 2)


if __name__ == "__main__":
    unittest.main()
