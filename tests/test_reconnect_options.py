"""Tests for configurable reconnect behavior and retry validation."""

from __future__ import annotations

import math
from pathlib import Path
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.connection import SerialConnectionError
from pylacrosse_ng.receiver import LaCrosseReceiver


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for reconnect state")


class FakeConnection:
    def __init__(self, *, fail_opens: int = 0) -> None:
        self.fail_opens = fail_opens
        self.open_calls = 0
        self.close_calls = 0
        self.open_times: list[float] = []
        self.incoming: queue.Queue[bytes | Exception] = queue.Queue()

    def open(self) -> None:
        self.open_calls += 1
        self.open_times.append(time.monotonic())
        if self.open_calls <= self.fail_opens:
            raise SerialConnectionError("USB unavailable")

    def readline(self) -> bytes:
        try:
            item = self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.close_calls += 1


class TestReconnectOptions(unittest.TestCase):
    def test_reconnect_disabled_stops_after_initial_open_failure(self) -> None:
        connection = FakeConnection(fail_opens=100)
        receiver = LaCrosseReceiver(
            connection, lambda _: None, auto_reconnect=False,
            retry_initial=0.02, retry_max=0.05,
        )
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "stopped")
            self.assertEqual(connection.open_calls, 1)
            self.assertFalse(receiver.is_running)
            self.assertFalse(receiver.is_connected)
            self.assertIn("USB unavailable", receiver.health.last_error)
            time.sleep(0.07)
            self.assertEqual(connection.open_calls, 1)
        finally:
            receiver.stop()

    def test_reconnect_disabled_stops_after_read_error(self) -> None:
        connection = FakeConnection()
        receiver = LaCrosseReceiver(
            connection, lambda _: None, auto_reconnect=False,
            retry_initial=0.02, retry_max=0.05,
        )
        try:
            receiver.start()
            wait_for(lambda: receiver.is_connected)
            connection.incoming.put(SerialConnectionError("USB disconnected"))
            wait_for(lambda: receiver.health.status == "stopped")
            self.assertFalse(receiver.is_running)
            self.assertEqual(connection.open_calls, 1)
            self.assertIn("USB disconnected", receiver.health.last_error)
        finally:
            receiver.stop()
        self.assertEqual(connection.close_calls, 1)

    def test_retry_multiplier_applies_backoff_and_maximum(self) -> None:
        connection = FakeConnection(fail_opens=100)
        receiver = LaCrosseReceiver(
            connection, lambda _: None,
            retry_initial=0.04, retry_max=0.08, retry_multiplier=3.0,
        )
        try:
            receiver.start()
            wait_for(lambda: connection.open_calls >= 4)
        finally:
            receiver.stop()
        delays = [
            second - first
            for first, second in zip(
                connection.open_times[:3], connection.open_times[1:4]
            )
        ]
        # With multiplier=3 and max=0.08, delays are 0.04, 0.08, 0.08.
        # Allow small scheduling differences without requiring exact timing.
        self.assertGreaterEqual(delays[0], 0.03)
        self.assertGreaterEqual(delays[1], 0.07)
        self.assertGreaterEqual(delays[2], 0.07)
        self.assertLess(delays[0], delays[1])

    def test_multiplier_one_keeps_constant_retry_interval(self) -> None:
        connection = FakeConnection(fail_opens=100)
        receiver = LaCrosseReceiver(
            connection, lambda _: None,
            retry_initial=0.04, retry_max=0.2, retry_multiplier=1.0,
        )
        try:
            receiver.start()
            wait_for(lambda: connection.open_calls >= 3)
        finally:
            receiver.stop()
        intervals = [
            connection.open_times[i + 1] - connection.open_times[i]
            for i in (0, 1)
        ]
        self.assertTrue(all(interval >= 0.03 for interval in intervals))
        self.assertTrue(all(interval < 0.15 for interval in intervals))

    def test_invalid_retry_parameters_are_rejected(self) -> None:
        connection = FakeConnection()
        cases = (
            ({"retry_initial": 0}, ValueError),
            ({"retry_initial": math.nan}, ValueError),
            ({"retry_initial": math.inf}, ValueError),
            ({"retry_max": math.inf}, ValueError),
            ({"retry_max": 0.5, "retry_initial": 1}, ValueError),
            ({"retry_multiplier": 0.9}, ValueError),
            ({"retry_multiplier": math.nan}, ValueError),
            ({"retry_multiplier": math.inf}, ValueError),
            ({"auto_reconnect": "yes"}, TypeError),
            ({"auto_reconnect": 1}, TypeError),
        )
        for settings, expected_error in cases:
            with self.subTest(settings=settings):
                with self.assertRaises(expected_error):
                    LaCrosseReceiver(connection, lambda _: None, **settings)


if __name__ == "__main__":
    unittest.main()
