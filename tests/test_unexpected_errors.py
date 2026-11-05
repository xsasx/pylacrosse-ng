"""Regression tests for unexpected receiver and cleanup exceptions."""

from __future__ import annotations

from pathlib import Path
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.receiver import LaCrosseReceiver


TELEGRAM = b"OK 9 5 1 4 150 45\n"


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for receiver state")


class FaultInjectingConnection:
    """Fake transport with controllable unexpected errors."""

    def __init__(
        self,
        *,
        open_failure: bool = False,
        close_failure: bool = False,
    ) -> None:
        self.open_failure = open_failure
        self.close_failure = close_failure
        self.opens = 0
        self.closes = 0
        self.incoming: queue.Queue[bytes | Exception] = queue.Queue()
        self._lock = threading.Lock()

    def open(self) -> None:
        with self._lock:
            self.opens += 1
            if self.open_failure and self.opens == 1:
                raise ValueError("unexpected open error")

    def readline(self) -> bytes:
        try:
            item = self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        with self._lock:
            self.closes += 1
            if self.close_failure and self.closes == 1:
                raise RuntimeError("unexpected close error")


class TestUnexpectedReceiverExceptions(unittest.TestCase):
    def make_receiver(self, connection, callback):
        return LaCrosseReceiver(
            connection, callback, retry_initial=0.05, retry_max=0.1
        )

    def test_unexpected_read_exception_reconnects_and_resumes_packets(self):
        connection = FaultInjectingConnection()
        readings = []
        receiver = self.make_receiver(connection, readings.append)
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "connected")
            connection.incoming.put(RuntimeError("decoder unexpectedly failed"))
            wait_for(lambda: receiver.health.status == "reconnecting")
            self.assertIn("RuntimeError", receiver.health.last_error)
            self.assertIn("decoder unexpectedly failed", receiver.health.last_error)
            wait_for(lambda: receiver.health.successful_connections == 2)
            connection.incoming.put(TELEGRAM)
            wait_for(lambda: len(readings) == 1)
            self.assertEqual(readings[0].sensor_id, 5)
            self.assertEqual(receiver.health.connection_attempts, 2)
            self.assertIsNone(receiver.health.last_error)
        finally:
            receiver.stop()
        self.assertEqual(connection.opens, 2)
        self.assertEqual(connection.closes, 2)
        self.assertEqual(receiver.health.status, "stopped")

    def test_unexpected_open_exception_retries(self):
        connection = FaultInjectingConnection(open_failure=True)
        receiver = self.make_receiver(connection, lambda _: None)
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "reconnecting")
            self.assertIn("unexpected open error", receiver.health.last_error)
            wait_for(lambda: receiver.health.status == "connected")
            self.assertEqual(connection.opens, 2)
            self.assertEqual(receiver.health.successful_connections, 1)
            self.assertIsNone(receiver.health.last_error)
        finally:
            receiver.stop()
        self.assertEqual(receiver.health.status, "stopped")

    def test_failed_close_does_not_prevent_recovery(self):
        connection = FaultInjectingConnection(close_failure=True)
        readings = []
        receiver = self.make_receiver(connection, readings.append)
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "connected")
            connection.incoming.put(RuntimeError("read failure"))
            wait_for(lambda: connection.closes >= 1)
            wait_for(lambda: receiver.health.successful_connections == 2)
            connection.incoming.put(TELEGRAM)
            wait_for(lambda: len(readings) == 1)
            self.assertEqual(readings[0].sensor_id, 5)
            self.assertTrue(receiver.is_running)
        finally:
            receiver.stop()
        self.assertEqual(connection.opens, 2)
        self.assertEqual(connection.closes, 2)


if __name__ == "__main__":
    unittest.main()
