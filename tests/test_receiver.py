"""Recovery and lifecycle regression tests for LaCrosseReceiver."""

from __future__ import annotations

from pathlib import Path
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.connection import SerialConnectionError
from pylacrosse_ng.receiver import LaCrosseReceiver


TELEGRAM = b"OK 9 5 1 4 150 45\n"


class FakeConnection:
    """Controllable fake transport with finite simulated read timeouts."""

    def __init__(self, *, fail_opens: int = 0) -> None:
        self._lock = threading.Lock()
        self._incoming: queue.Queue[bytes | Exception] = queue.Queue()
        self.fail_opens = fail_opens
        self.open_calls = 0
        self.close_calls = 0
        self.connected = False

    def open(self) -> None:
        with self._lock:
            self.open_calls += 1
            if self.open_calls <= self.fail_opens:
                raise SerialConnectionError("USB not available")
            self.connected = True

    def close(self) -> None:
        with self._lock:
            self.close_calls += 1
            self.connected = False

    def readline(self) -> bytes:
        try:
            item = self._incoming.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def inject(self, item: bytes | Exception) -> None:
        self._incoming.put(item)


def wait_for(predicate, *, timeout: float = 2.0) -> None:
    """Wait for worker progress with a bounded timeout, never indefinitely."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for receiver state")


class TestReceiverRecovery(unittest.TestCase):
    def make_receiver(self, connection, callback):
        return LaCrosseReceiver(
            connection,
            callback,
            retry_initial=0.02,
            retry_max=0.05,
        )

    def test_reconnect_after_serial_read_failure(self) -> None:
        connection = FakeConnection()
        readings = []
        receiver = self.make_receiver(connection, readings.append)
        try:
            receiver.start()
            wait_for(lambda: receiver.is_connected)
            connection.inject(TELEGRAM)
            wait_for(lambda: len(readings) == 1)

            connection.inject(SerialConnectionError("USB removed"))
            wait_for(lambda: connection.open_calls >= 2)
            wait_for(lambda: receiver.is_connected)

            connection.inject(TELEGRAM)
            wait_for(lambda: len(readings) == 2)
            self.assertEqual([r.sensor_id for r in readings], [5, 5])
            self.assertEqual(connection.open_calls, 2)
        finally:
            receiver.stop()
        self.assertFalse(receiver.is_running)
        self.assertFalse(receiver.is_connected)
        self.assertEqual(connection.close_calls, 2)

    def test_retries_failed_open_until_receiver_available(self) -> None:
        connection = FakeConnection(fail_opens=2)
        readings = []
        receiver = self.make_receiver(connection, readings.append)
        try:
            receiver.start()
            wait_for(lambda: receiver.is_connected)
            self.assertEqual(connection.open_calls, 3)
            connection.inject(TELEGRAM)
            wait_for(lambda: len(readings) == 1)
        finally:
            receiver.stop()
        self.assertEqual(connection.close_calls, 3)

    def test_stop_cancels_pending_retries(self) -> None:
        connection = FakeConnection(fail_opens=1000)
        receiver = LaCrosseReceiver(
            connection, lambda _: None, retry_initial=0.25, retry_max=0.5
        )
        receiver.start()
        wait_for(lambda: connection.open_calls >= 1)
        started = time.monotonic()
        receiver.stop()
        self.assertLess(time.monotonic() - started, 0.5)
        attempts = connection.open_calls
        time.sleep(0.05)
        self.assertEqual(connection.open_calls, attempts)
        self.assertFalse(receiver.is_running)
        self.assertFalse(receiver.is_connected)

    def test_repeated_start_does_not_spawn_second_worker(self) -> None:
        connection = FakeConnection()
        readings = []
        receiver = self.make_receiver(connection, readings.append)
        try:
            receiver.start()
            receiver.start()
            wait_for(lambda: receiver.is_connected)
            connection.inject(TELEGRAM)
            wait_for(lambda: len(readings) == 1)
            self.assertEqual(connection.open_calls, 1)
            self.assertEqual(len(readings), 1)
        finally:
            receiver.stop()
        receiver.stop()
        self.assertEqual(connection.close_calls, 1)

    def test_radio_silence_does_not_trigger_reconnect(self) -> None:
        connection = FakeConnection()
        receiver = self.make_receiver(connection, lambda _: None)
        try:
            receiver.start()
            wait_for(lambda: receiver.is_connected)
            time.sleep(0.1)
            self.assertTrue(receiver.is_connected)
            self.assertEqual(connection.open_calls, 1)
        finally:
            receiver.stop()

    def test_callback_failure_does_not_disconnect_receiver(self) -> None:
        connection = FakeConnection()
        called = threading.Event()

        def faulty_callback(_reading) -> None:
            called.set()
            raise RuntimeError("consumer failed")

        receiver = self.make_receiver(connection, faulty_callback)
        try:
            receiver.start()
            wait_for(lambda: receiver.is_connected)
            with self.assertLogs("pylacrosse_ng.receiver", level="ERROR"):
                connection.inject(TELEGRAM)
                self.assertTrue(called.wait(timeout=1.0))
                # Allow the worker to handle the exception and log it.
                time.sleep(0.03)
            self.assertTrue(receiver.is_connected)
            self.assertEqual(connection.open_calls, 1)
        finally:
            receiver.stop()

    def test_receiver_can_restart_after_stop(self) -> None:
        connection = FakeConnection()
        receiver = self.make_receiver(connection, lambda _: None)
        receiver.start()
        wait_for(lambda: receiver.is_connected)
        receiver.stop()
        self.assertFalse(receiver.is_running)

        receiver.start()
        try:
            wait_for(lambda: receiver.is_connected)
            self.assertEqual(connection.open_calls, 2)
        finally:
            receiver.stop()
        self.assertEqual(connection.close_calls, 2)

    def test_rejects_invalid_retry_configuration(self) -> None:
        connection = FakeConnection()
        with self.assertRaises(ValueError):
            self.make_receiver_invalid(connection, retry_initial=0)
        with self.assertRaises(ValueError):
            self.make_receiver_invalid(connection, retry_initial=2, retry_max=1)

    @staticmethod
    def make_receiver_invalid(connection, **kwargs):
        return LaCrosseReceiver(connection, lambda _: None, **kwargs)


if __name__ == "__main__":
    unittest.main()
