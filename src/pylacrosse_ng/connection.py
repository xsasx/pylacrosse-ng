"""Serial transport lifecycle for JeeLink receivers.

This module intentionally does not start threads or automatically retry.
Higher-level receiver code will own recovery and thread coordination.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Protocol

import serial


class SerialConnectionError(ConnectionError):
    """The JeeLink serial transport could not be used."""


class SerialPort(Protocol):
    """Minimal serial interface needed by the transport."""

    is_open: bool

    def open(self) -> None: ...

    def close(self) -> None: ...

    def readline(self) -> bytes: ...

    def reset_input_buffer(self) -> None: ...


class SerialConnection:
    """Open, read and close exactly one serial connection.

    Opening and closing are serialized. A failed open closes the partially
    initialized port, allowing a future recovery attempt to try again.
    Reading is deliberately not locked against close: higher-level receiver
    lifecycle management must stop its worker before closing the port.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 57600,
        *,
        timeout: float = 2.0,
        serial_factory: Callable[..., SerialPort] | None = None,
    ) -> None:
        if not port:
            raise ValueError("port must not be empty")
        if baudrate <= 0:
            raise ValueError("baudrate must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial_factory = serial_factory or serial.serial_for_url
        self._serial: SerialPort | None = None
        self._lifecycle_lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        """Return whether the serial port reports itself open."""
        with self._lifecycle_lock:
            return self._serial is not None and self._serial.is_open

    def open(self) -> None:
        """Open the receiver or raise SerialConnectionError."""
        with self._lifecycle_lock:
            if self.is_open:
                return

            # Discard an old or failed port before opening a new one.
            self._close_unlocked()
            candidate: SerialPort | None = None
            try:
                candidate = self._serial_factory(
                    self.port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    do_not_open=True,
                )
                candidate.open()
                candidate.reset_input_buffer()
            except (OSError, serial.SerialException) as exc:
                if candidate is not None:
                    try:
                        candidate.close()
                    except Exception:
                        pass
                raise SerialConnectionError(
                    f"Cannot open JeeLink serial port {self.port}"
                ) from exc

            self._serial = candidate

    def readline(self) -> bytes:
        """Read one line; serial failures are surfaced for recovery handling."""
        connection = self._serial
        if connection is None or not connection.is_open:
            raise SerialConnectionError("JeeLink serial port is not open")
        try:
            return connection.readline()
        except (OSError, serial.SerialException) as exc:
            raise SerialConnectionError(
                f"Reading JeeLink serial port {self.port} failed"
            ) from exc

    def close(self) -> None:
        """Release the connection; repeated close calls are safe."""
        with self._lifecycle_lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        connection = self._serial
        self._serial = None
        if connection is not None:
            try:
                connection.close()
            except (OSError, serial.SerialException) as exc:
                raise SerialConnectionError(
                    f"Closing JeeLink serial port {self.port} failed"
                ) from exc
