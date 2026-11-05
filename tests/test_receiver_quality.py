"""Receiver-level tests for opt-in temperature quality callbacks."""

from __future__ import annotations

from pathlib import Path
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.quality import TemperatureQualityRules
from pylacrosse_ng.receiver import LaCrosseReceiver


def packet(sensor_id: int, temperature_tenths: int, *, external: bool = False) -> bytes:
    encoded = temperature_tenths + 1000
    flags = 130 if external else 2
    return (
        f"OK 9 {sensor_id} {flags} {encoded // 256} "
        f"{encoded % 256} 106\n"
    ).encode("ascii")


class FakeConnection:
    def __init__(self) -> None:
        self.incoming: queue.Queue[bytes] = queue.Queue()
        self.opens = 0
        self.closes = 0

    def open(self) -> None:
        self.opens += 1

    def close(self) -> None:
        self.closes += 1

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
    raise AssertionError("Timed out waiting for quality callbacks")


class TestReceiverQualityCallbacks(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = FakeConnection()
        self.raw_readings = []
        self.quality_events = []
        self.receiver: LaCrosseReceiver | None = None

    def tearDown(self) -> None:
        if self.receiver is not None:
            self.receiver.stop()

    def start(self, *, rules=None, quality_callback=None) -> None:
        self.receiver = LaCrosseReceiver(
            self.connection,
            self.raw_readings.append,
            quality_rules=rules,
            quality_callback=quality_callback,
            retry_initial=0.02,
            retry_max=0.05,
        )
        self.receiver.start()
        wait_for(lambda: self.receiver.is_connected)

    def send(self, line: bytes, expected_readings: int) -> None:
        self.connection.incoming.put(line)
        wait_for(lambda: len(self.raw_readings) == expected_readings)

    def test_default_configuration_has_no_quality_filter(self) -> None:
        self.start()
        self.send(packet(5, 174), 1)
        self.assertEqual(self.raw_readings[0].temperature, 17.4)
        self.assertEqual(self.receiver.reception_statistics.valid_packets, 1)
        self.assertEqual(self.connection.opens, 1)

    def test_rejected_temperature_still_reaches_normal_callback(self) -> None:
        self.start(
            rules=TemperatureQualityRules(minimum=0, maximum=30),
            quality_callback=lambda reading, result: self.quality_events.append(
                (reading, result)
            ),
        )
        self.send(packet(5, 174), 1)
        self.send(packet(5, 350), 2)
        wait_for(lambda: len(self.quality_events) == 2)
        self.assertEqual([r.temperature for r in self.raw_readings], [17.4, 35.0])
        self.assertTrue(self.quality_events[0][1].accepted)
        self.assertFalse(self.quality_events[1][1].accepted)
        self.assertEqual(self.quality_events[1][1].reason, "above_maximum")
        self.assertEqual(self.receiver.reception_statistics.valid_packets, 2)

    def test_rejected_jump_does_not_replace_last_accepted_reference(self) -> None:
        self.start(
            rules=TemperatureQualityRules(max_change=2),
            quality_callback=lambda reading, result: self.quality_events.append(
                result
            ),
        )
        for i, tenths in enumerate((200, 300, 210), 1):
            self.send(packet(5, tenths), i)
        wait_for(lambda: len(self.quality_events) == 3)
        self.assertEqual(
            [(event.accepted, event.reason) for event in self.quality_events],
            [(True, None), (False, "excessive_change"), (True, None)],
        )
        self.assertEqual(len(self.raw_readings), 3)

    def test_tx25tp_channels_and_sensor_ids_keep_separate_references(self) -> None:
        self.start(
            rules=TemperatureQualityRules(max_change=1),
            quality_callback=lambda reading, result: self.quality_events.append(
                (reading.sensor_id, reading.external_probe, result.accepted)
            ),
        )
        self.send(packet(25, 200), 1)
        self.send(packet(25, 300, external=True), 2)
        self.send(packet(26, 350), 3)
        self.send(packet(25, 205), 4)
        self.send(packet(25, 305, external=True), 5)
        wait_for(lambda: len(self.quality_events) == 5)
        self.assertTrue(all(item[2] for item in self.quality_events))
        self.assertEqual(self.connection.opens, 1)

    def test_failing_quality_callback_does_not_stop_normal_packets(self) -> None:
        callback_called = threading.Event()

        def failing_quality_callback(_reading, _result) -> None:
            callback_called.set()
            raise RuntimeError("consumer quality error")

        self.start(
            rules=TemperatureQualityRules(max_change=1),
            quality_callback=failing_quality_callback,
        )
        with self.assertLogs("pylacrosse_ng.receiver", level="ERROR") as logs:
            self.send(packet(5, 200), 1)
            self.assertTrue(callback_called.wait(timeout=1))
            wait_for(lambda: any("quality callback failed" in m.lower()
                                 for m in logs.output))
        self.assertTrue(self.receiver.is_connected)
        self.send(packet(5, 205), 2)
        self.assertEqual(self.connection.opens, 1)


if __name__ == "__main__":
    unittest.main()
