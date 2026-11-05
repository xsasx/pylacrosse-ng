"""Background JeeLink receiver with automatic serial reconnection.

The receiver owns one worker thread and one serial transport. This module
is independent of Home Assistant; diagnostics and configurable recovery
settings will be added as the library develops.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading

from .connection import SerialConnection, SerialConnectionError
from .parser import LaCrosseReading, parse_telegram

_LOGGER = logging.getLogger(__name__)


class LaCrosseReceiver:
    """Read radio packets and recover from serial connection failures.

    The provided transport must use a finite read timeout so stop() can
    join the worker cleanly. Calls to start() and stop() are serialized.
    """

    def __init__(
        self,
        connection: SerialConnection,
        callback: Callable[[LaCrosseReading], None],
        *,
        retry_initial: float = 1.0,
        retry_max: float = 60.0,
    ) -> None:
        if retry_initial <= 0:
            raise ValueError("retry_initial must be positive")
        if retry_max < retry_initial:
            raise ValueError("retry_max must be >= retry_initial")
        self._connection = connection
        self._callback = callback
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """Whether the receive worker is currently alive."""
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def is_connected(self) -> bool:
        """Whether the receive worker has an open serial connection."""
        return self._connected_event.is_set()

    def start(self) -> None:
        """Start receiving in the background; repeated starts are safe."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._connected_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="pylacrosse-ng-receiver",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the worker and release serial resources before returning."""
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            if thread is threading.current_thread():
                raise RuntimeError("stop() cannot be called from receiver callback")
            self._stop_event.set()
            # The serial transport uses a finite read timeout. Only the
            # worker closes the serial connection, after it stops reading.
            thread.join()
            self._thread = None
            self._connected_event.clear()

    def _run(self) -> None:
        delay = self._retry_initial
        try:
            while not self._stop_event.is_set():
                try:
                    self._connection.open()
                    self._connected_event.set()
                    delay = self._retry_initial
                    _LOGGER.info("JeeLink serial receiver connected")
                    self._read_until_failure()
                except SerialConnectionError as exc:
                    self._connected_event.clear()
                    if not self._stop_event.is_set():
                        _LOGGER.warning("JeeLink serial receiver unavailable: %s", exc)
                finally:
                    self._connected_event.clear()
                    try:
                        self._connection.close()
                    except SerialConnectionError:
                        _LOGGER.warning("Could not cleanly close JeeLink serial port")

                if self._stop_event.is_set():
                    break
                # Event.wait() makes the recovery delay cancelable by stop().
                if self._stop_event.wait(delay):
                    break
                delay = min(delay * 2, self._retry_max)
        finally:
            self._connected_event.clear()

    def _read_until_failure(self) -> None:
        while not self._stop_event.is_set():
            line = self._connection.readline()
            if not line:
                # Read timeouts are normal; absence of radio packets does
                # not prove USB disconnection.
                continue

            reading = parse_telegram(line)
            if reading is None:
                continue
            try:
                self._callback(reading)
            except Exception:
                # User callbacks must never kill the serial worker or force
                # a spurious USB reconnection.
                _LOGGER.exception("JeeLink packet callback failed")
