"""Regression tests for the JeeLink serial transport lifecycle."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pylacrosse_ng.connection import SerialConnection, SerialConnectionError


class FakeSerialPort:
    """Controllable serial port without physical USB hardware."""

    def __init__(
        self,
        *,
        open_error: Exception | None = None,
        read_error: Exception | None = None,
        flush_error: Exception | None = None,
        close_error: Exception | None = None,
        response: bytes = b"OK 9 5 1 4 150 45\n",
    ) -> None:
        self.is_open = False
        self.open_error = open_error
        self.read_error = read_error
        self.flush_error = flush_error
        self.close_error = close_error
        self.response = response
        self.open_calls = 0
        self.close_calls = 0
        self.flush_calls = 0
        self.read_calls = 0

    def open(self) -> None:
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error
        self.is_open = True

    def reset_input_buffer(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error

    def readline(self) -> bytes:
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.response

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False
        if self.close_error is not None:
            raise self.close_error


class SerialFactory:
    """Return a new fake port on each connection attempt."""

    def __init__(self, *ports: FakeSerialPort) -> None:
        self.ports = list(ports)
        self.calls: list[tuple[str, int, float, bool]] = []

    def __call__(
        self,
        port: str,
        *,
        baudrate: int,
        timeout: float,
        do_not_open: bool,
    ) -> FakeSerialPort:
        self.calls.append((port, baudrate, timeout, do_not_open))
        return self.ports.pop(0)


class TestSerialConnection(unittest.TestCase):
    def test_open_read_close_and_idempotent_lifecycle(self) -> None:
        port = FakeSerialPort()
        factory = SerialFactory(port)
        connection = SerialConnection(
            "/dev/ttyUSB0", 57600, timeout=1.5, serial_factory=factory
        )
        self.assertFalse(connection.is_open)

        connection.open()
        connection.open()
        self.assertTrue(connection.is_open)
        self.assertEqual(port.open_calls, 1)
        self.assertEqual(port.flush_calls, 1)
        self.assertEqual(factory.calls, [("/dev/ttyUSB0", 57600, 1.5, True)])
        self.assertEqual(connection.readline(), b"OK 9 5 1 4 150 45\n")

        connection.close()
        connection.close()
        self.assertFalse(connection.is_open)
        self.assertEqual(port.close_calls, 1)

    def test_read_without_open_fails_cleanly(self) -> None:
        connection = SerialConnection(
            "/dev/ttyUSB0", serial_factory=SerialFactory()
        )
        with self.assertRaisesRegex(SerialConnectionError, "not open"):
            connection.readline()

    def test_open_failure_cleans_partial_port_and_can_retry(self) -> None:
        failing = FakeSerialPort(open_error=serial.SerialException("USB absent"))
        recovered = FakeSerialPort()
        factory = SerialFactory(failing, recovered)
        connection = SerialConnection("/dev/ttyUSB0", serial_factory=factory)

        with self.assertRaises(SerialConnectionError):
            connection.open()
        self.assertFalse(connection.is_open)
        self.assertEqual(failing.close_calls, 1)

        connection.open()
        self.assertTrue(connection.is_open)
        self.assertEqual(recovered.open_calls, 1)
        connection.close()

    def test_open_buffer_reset_error_closes_partial_port(self) -> None:
        port = FakeSerialPort(flush_error=OSError("USB removed"))
        connection = SerialConnection(
            "/dev/ttyUSB0", serial_factory=SerialFactory(port)
        )
        with self.assertRaises(SerialConnectionError):
            connection.open()
        self.assertFalse(connection.is_open)
        self.assertEqual(port.close_calls, 1)

    def test_serial_read_error_is_exposed_for_recovery(self) -> None:
        port = FakeSerialPort(
            read_error=serial.SerialException("device disconnected")
        )
        connection = SerialConnection(
            "/dev/ttyUSB0", serial_factory=SerialFactory(port)
        )
        connection.open()
        with self.assertRaisesRegex(SerialConnectionError, "Reading"):
            connection.readline()
        connection.close()

    def test_os_read_error_is_exposed_for_recovery(self) -> None:
        port = FakeSerialPort(read_error=OSError("I/O error"))
        connection = SerialConnection(
            "/dev/ttyUSB0", serial_factory=SerialFactory(port)
        )
        connection.open()
        with self.assertRaises(SerialConnectionError):
            connection.readline()
        connection.close()

    def test_failed_close_detaches_connection_and_does_not_retry_close(self) -> None:
        port = FakeSerialPort(close_error=serial.SerialException("port lost"))
        connection = SerialConnection(
            "/dev/ttyUSB0", serial_factory=SerialFactory(port)
        )
        connection.open()
        with self.assertRaisesRegex(SerialConnectionError, "Closing"):
            connection.close()
        self.assertFalse(connection.is_open)
        connection.close()
        self.assertEqual(port.close_calls, 1)

    def test_invalid_connection_settings(self) -> None:
        for kwargs in (
            {"port": ""},
            {"port": "/dev/ttyUSB0", "baudrate": 0},
            {"port": "/dev/ttyUSB0", "timeout": 0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    SerialConnection(**kwargs)


if __name__ == "__main__":
    unittest.main()
