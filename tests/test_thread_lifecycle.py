"""Concurrency and shutdown regression tests for the JeeLink receiver."""

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


class TimedFakeConnection:
    """Finite-read fake transport for thread lifecycle tests."""

    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self._lock = threading.Lock()
        self._incoming: queue.Queue[bytes] = queue.Queue()

    def open(self) -> None:
        with self._lock:
            self.open_calls += 1

    def readline(self) -> bytes:
        try:
            return self._incoming.get(timeout=0.03)
        except queue.Empty:
            return b""

    def close(self) -> None:
        with self._lock:
            self.close_calls += 1

    def inject(self, line: bytes) -> None:
        self._incoming.put(line)


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("receiver did not reach expected state")


class TestReceiverThreadLifecycle(unittest.TestCase):
    def test_concurrent_starts_create_one_receiver_worker(self) -> None:
        connection = TimedFakeConnection()
        readings = []
        receiver = LaCrosseReceiver(connection, readings.append)
        gate = threading.Barrier(6)
        failures: list[BaseException] = []

        def start_together() -> None:
            try:
                gate.wait(timeout=2.0)
                receiver.start()
            except BaseException as exc:
                failures.append(exc)

        threads = [
            threading.Thread(target=start_together, daemon=True)
            for _ in range(6)
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2.0)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            wait_until(lambda: receiver.is_connected)
            self.assertEqual(connection.open_calls, 1)
            connection.inject(TELEGRAM)
            wait_until(lambda: len(readings) == 1)
            self.assertEqual(len(readings), 1)
        finally:
            receiver.stop()
        self.assertEqual(connection.close_calls, 1)

    def test_stop_during_finite_read_waits_for_clean_shutdown(self) -> None:
        connection = TimedFakeConnection()
        receiver = LaCrosseReceiver(connection, lambda _: None)
        receiver.start()
        wait_until(lambda: receiver.is_connected)
        began = time.monotonic()
        receiver.stop()
        self.assertLess(time.monotonic() - began, 1.0)
        self.assertFalse(receiver.is_running)
        self.assertFalse(receiver.is_connected)
        self.assertEqual(receiver.health.status, "stopped")
        self.assertEqual(connection.close_calls, 1)

    def test_concurrent_stops_close_connection_once(self) -> None:
        connection = TimedFakeConnection()
        receiver = LaCrosseReceiver(connection, lambda _: None)
        receiver.start()
        wait_until(lambda: receiver.is_connected)
        gate = threading.Barrier(4)
        failures: list[BaseException] = []

        def stop_together() -> None:
            try:
                gate.wait(timeout=2.0)
                receiver.stop()
            except BaseException as exc:
                failures.append(exc)

        threads = [
            threading.Thread(target=stop_together, daemon=True)
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertFalse(receiver.is_running)
        self.assertEqual(connection.close_calls, 1)

    def test_stopping_from_callback_is_rejected_without_killing_worker(self) -> None:
        connection = TimedFakeConnection()
        callback_finished = threading.Event()
        errors: list[str] = []

        def on_reading(_reading) -> None:
            try:
                receiver.stop()
            except RuntimeError as exc:
                errors.append(str(exc))
            finally:
                callback_finished.set()

        receiver = LaCrosseReceiver(connection, on_reading)
        try:
            receiver.start()
            wait_until(lambda: receiver.is_connected)
            connection.inject(TELEGRAM)
            self.assertTrue(callback_finished.wait(timeout=2.0))
            self.assertEqual(
                errors, ["stop() cannot be called from receiver callback"]
            )
            self.assertTrue(receiver.is_running)
            self.assertTrue(receiver.is_connected)
        finally:
            receiver.stop()
        self.assertEqual(connection.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
