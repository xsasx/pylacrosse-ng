"""Health diagnostics regression tests for the JeeLink receiver."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.connection import SerialConnectionError
from pylacrosse_ng.receiver import LaCrosseReceiver, ReceiverHealth


TELEGRAM = b"OK 9 5 1 4 150 45\n"


class FakeConnection:
    def __init__(self, *, failed_opens: int = 0) -> None:
        self.failed_opens = failed_opens
        self.opens = 0
        self.closes = 0
        self.connected = False
        self.incoming: queue.Queue[bytes | Exception] = queue.Queue()

    def open(self) -> None:
        self.opens += 1
        if self.opens <= self.failed_opens:
            raise SerialConnectionError("USB missing")
        self.connected = True

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
        self.connected = False


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Receiver did not reach expected health state")


class TestReceiverHealth(unittest.TestCase):
    def make_receiver(self, conn: FakeConnection, callback=None) -> LaCrosseReceiver:
        return LaCrosseReceiver(
            conn, callback or (lambda reading: None),
            retry_initial=0.05, retry_max=0.1,
        )

    def test_initial_health_snapshot_is_stopped_and_immutable(self) -> None:
        receiver = self.make_receiver(FakeConnection())
        health = receiver.health
        self.assertIsInstance(health, ReceiverHealth)
        self.assertEqual(health.status, "stopped")
        self.assertIsNone(health.last_packet_at)
        self.assertIsNone(health.last_connected_at)
        self.assertIsNone(health.last_error)
        self.assertEqual(health.connection_attempts, 0)
        self.assertEqual(health.successful_connections, 0)
        with self.assertRaises(FrozenInstanceError):
            health.status = "connected"

    def test_connection_and_stop_update_health(self) -> None:
        conn = FakeConnection()
        receiver = self.make_receiver(conn)
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "connected")
            health = receiver.health
            self.assertEqual(health.connection_attempts, 1)
            self.assertEqual(health.successful_connections, 1)
            self.assertIsNone(health.last_error)
            self.assertIsNotNone(health.last_connected_at)
            self.assertEqual(health.last_connected_at.tzinfo, timezone.utc)
        finally:
            receiver.stop()
        self.assertEqual(receiver.health.status, "stopped")
        self.assertEqual(receiver.health.successful_connections, 1)

    def test_valid_packets_update_timestamp_invalid_packets_do_not(self) -> None:
        conn = FakeConnection()
        receiver = self.make_receiver(conn)
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "connected")
            conn.incoming.put(b"garbage\n")
            time.sleep(0.05)
            self.assertIsNone(receiver.health.last_packet_at)
            conn.incoming.put(TELEGRAM)
            wait_for(lambda: receiver.health.last_packet_at is not None)
            last_packet = receiver.health.last_packet_at
            self.assertIsInstance(last_packet, datetime)
            self.assertEqual(last_packet.tzinfo, timezone.utc)
            time.sleep(0.07)
            self.assertEqual(receiver.health.last_packet_at, last_packet)
            self.assertEqual(receiver.health.status, "connected")
        finally:
            receiver.stop()

    def test_failed_open_then_success_resets_last_error(self) -> None:
        conn = FakeConnection(failed_opens=1)
        receiver = self.make_receiver(conn)
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "reconnecting")
            failed = receiver.health
            self.assertEqual(failed.connection_attempts, 1)
            self.assertEqual(failed.successful_connections, 0)
            self.assertIn("USB missing", failed.last_error)
            wait_for(lambda: receiver.health.status == "connected")
            recovered = receiver.health
            self.assertEqual(recovered.connection_attempts, 2)
            self.assertEqual(recovered.successful_connections, 1)
            self.assertIsNone(recovered.last_error)
        finally:
            receiver.stop()

    def test_read_failure_reconnect_updates_counters_and_timestamp(self) -> None:
        conn = FakeConnection()
        receiver = self.make_receiver(conn)
        try:
            receiver.start()
            wait_for(lambda: receiver.health.status == "connected")
            first_connected = receiver.health.last_connected_at
            conn.incoming.put(SerialConnectionError("serial cable removed"))
            wait_for(lambda: receiver.health.status == "reconnecting")
            failure = receiver.health
            self.assertIn("serial cable removed", failure.last_error)
            wait_for(lambda: receiver.health.successful_connections == 2)
            recovered = receiver.health
            self.assertEqual(recovered.connection_attempts, 2)
            self.assertEqual(recovered.successful_connections, 2)
            self.assertIsNone(recovered.last_error)
            self.assertGreaterEqual(recovered.last_connected_at, first_connected)
        finally:
            receiver.stop()
        self.assertEqual(receiver.health.status, "stopped")


if __name__ == "__main__":
    unittest.main()
