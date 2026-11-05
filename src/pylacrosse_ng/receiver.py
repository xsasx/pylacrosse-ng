"""Background JeeLink receiver with automatic serial reconnection.

The receiver owns one worker thread and one serial transport. This module
is independent of Home Assistant; diagnostics and configurable recovery
settings will be added as the library develops.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import threading

from .connection import SerialConnection, SerialConnectionError
from .discovery import SensorRegistry
from .parser import LaCrosseReading, parse_telegram
from .quality import TemperatureQualityResult, TemperatureQualityRules, assess_temperature

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReceiverHealth:
    """Thread-safe receiver diagnostics snapshot."""

    status: str
    last_packet_at: datetime | None
    last_connected_at: datetime | None
    connection_attempts: int
    successful_connections: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class SensorReception:
    """Immutable packet statistics for a discovered radio ID."""

    sensor_id: int
    packets: int
    last_seen: datetime
    internal_packets: int
    external_packets: int
    first_seen: datetime | None = None
    last_interval_seconds: float | None = None
    average_interval_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ReceptionStatistics:
    """Immutable receiver-wide packet statistics."""

    valid_packets: int
    invalid_packets: int
    sensors: tuple[SensorReception, ...]


@dataclass(frozen=True, slots=True)
class SensorActivity:
    """Current radio-packet freshness of a discovered sensor.

    ACTIVE/STALE describes only radio reception, not USB link health.
    """

    sensor_id: int
    status: str
    age_seconds: float
    stale_after_seconds: float
    last_seen: datetime
    packets: int


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
        retry_multiplier: float = 2.0,
        auto_reconnect: bool = True,
        quality_rules: TemperatureQualityRules | None = None,
        quality_callback: Callable[[LaCrosseReading, TemperatureQualityResult], None] | None = None,
        sensor_registry: SensorRegistry | None = None,
    ) -> None:
        if not math.isfinite(retry_initial) or retry_initial <= 0:
            raise ValueError("retry_initial must be a finite positive number")
        if not math.isfinite(retry_max) or retry_max < retry_initial:
            raise ValueError("retry_max must be finite and >= retry_initial")
        if not math.isfinite(retry_multiplier) or retry_multiplier < 1:
            raise ValueError("retry_multiplier must be finite and >= 1")
        if not isinstance(auto_reconnect, bool):
            raise TypeError("auto_reconnect must be a boolean")
        self._connection = connection
        self._callback = callback
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._retry_multiplier = retry_multiplier
        self._auto_reconnect = auto_reconnect
        self._quality_rules = quality_rules
        self._quality_callback = quality_callback
        self._sensor_registry = sensor_registry if sensor_registry is not None else SensorRegistry()
        # Reference values are isolated by radio ID and probe channel.
        self._accepted_temperatures: dict[tuple[int, bool], float] = {}
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._health_lock = threading.Lock()
        self._status = "stopped"
        self._last_packet_at: datetime | None = None
        self._last_connected_at: datetime | None = None
        self._connection_attempts = 0
        self._successful_connections = 0
        self._last_error: str | None = None
        self._valid_packets = 0
        self._invalid_packets = 0
        self._sensor_reception: dict[int, SensorReception] = {}

    @property
    def sensor_registry(self) -> SensorRegistry:
        """Registry populated automatically for each valid radio telegram.

        Applications can set configured/ignored radio IDs explicitly; the
        receiver never changes memberships on its own.
        """
        return self._sensor_registry

    @property
    def is_running(self) -> bool:
        """Whether the receive worker is currently alive."""
        with self._lifecycle_lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def is_connected(self) -> bool:
        """Whether the receive worker has an open serial connection."""
        return self._connected_event.is_set()

    @property
    def health(self) -> ReceiverHealth:
        """Return consistent receiver diagnostics without exposing mutable state."""
        with self._health_lock:
            return ReceiverHealth(
                status=self._status,
                last_packet_at=self._last_packet_at,
                last_connected_at=self._last_connected_at,
                connection_attempts=self._connection_attempts,
                successful_connections=self._successful_connections,
                last_error=self._last_error,
            )

    @property
    def reception_statistics(self) -> ReceptionStatistics:
        """Return per-sensor packet counts and invalid telegram statistics.

        Sensor IDs are discovered from valid packets. Classification into
        configured, ignored, or unknown sensors belongs to the caller.
        """
        with self._health_lock:
            return ReceptionStatistics(
                valid_packets=self._valid_packets,
                invalid_packets=self._invalid_packets,
                sensors=tuple(
                    self._sensor_reception[sensor_id]
                    for sensor_id in sorted(self._sensor_reception)
                ),
            )

    def sensor_activity(
        self,
        *,
        minimum_timeout: float = 600.0,
        interval_multiplier: float = 3.0,
        now: datetime | None = None,
    ) -> tuple[SensorActivity, ...]:
        """Calculate ACTIVE/STALE freshness per radio ID on demand.

        The threshold is the greater of the minimum timeout and the
        observed average packet interval multiplied by the configured
        tolerance. A sensor with just one packet uses the minimum timeout.

        This method does not alter receiver status or reconnect behavior.
        It measures per-radio-ID reception (both TX25TP-IT channels together).
        """
        if not math.isfinite(minimum_timeout) or minimum_timeout <= 0:
            raise ValueError("minimum_timeout must be finite and positive")
        if not math.isfinite(interval_multiplier) or interval_multiplier < 1:
            raise ValueError("interval_multiplier must be finite and >= 1")
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        sensors = self.reception_statistics.sensors
        result = []
        for sensor in sensors:
            threshold = minimum_timeout
            if sensor.average_interval_seconds is not None:
                threshold = max(
                    threshold,
                    sensor.average_interval_seconds * interval_multiplier,
                )
            age = max(0.0, (now - sensor.last_seen).total_seconds())
            result.append(
                SensorActivity(
                    sensor_id=sensor.sensor_id,
                    status="ACTIVE" if age < threshold else "STALE",
                    age_seconds=age,
                    stale_after_seconds=threshold,
                    last_seen=sensor.last_seen,
                    packets=sensor.packets,
                )
            )
        return tuple(result)

    def _set_status(self, status: str) -> None:
        with self._health_lock:
            self._status = status

    def start(self) -> None:
        """Start receiving in the background; repeated starts are safe."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._connected_event.clear()
            self._set_status("connecting")
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
            self._set_status("stopped")

    def _run(self) -> None:
        delay = self._retry_initial
        try:
            while not self._stop_event.is_set():
                try:
                    with self._health_lock:
                        self._connection_attempts += 1
                    self._connection.open()
                    self._connected_event.set()
                    with self._health_lock:
                        self._status = "connected"
                        self._successful_connections += 1
                        self._last_connected_at = datetime.now(timezone.utc)
                        self._last_error = None
                    delay = self._retry_initial
                    _LOGGER.info("JeeLink serial receiver connected")
                    self._read_until_failure()
                except SerialConnectionError as exc:
                    self._connected_event.clear()
                    if not self._stop_event.is_set():
                        with self._health_lock:
                            self._status = "reconnecting"
                            self._last_error = str(exc)
                        _LOGGER.warning("JeeLink serial receiver unavailable: %s", exc)
                except Exception as exc:
                    # Unexpected transport/parser failures must not terminate
                    # the worker silently. Release the port and retry.
                    self._connected_event.clear()
                    if not self._stop_event.is_set():
                        with self._health_lock:
                            self._status = "reconnecting"
                            self._last_error = (
                                f"{type(exc).__name__}: {exc}"
                            )
                        _LOGGER.exception(
                            "Unexpected JeeLink receiver error; retrying"
                        )
                finally:
                    self._connected_event.clear()
                    try:
                        self._connection.close()
                    except Exception:
                        # A failed cleanup must not prevent later retries.
                        _LOGGER.exception(
                            "Could not cleanly close JeeLink serial port"
                        )

                if self._stop_event.is_set() or not self._auto_reconnect:
                    break
                # Event.wait() makes the recovery delay cancelable by stop().
                if self._stop_event.wait(delay):
                    break
                delay = min(delay * self._retry_multiplier, self._retry_max)
        finally:
            self._connected_event.clear()
            self._set_status("stopped")

    def _read_until_failure(self) -> None:
        while not self._stop_event.is_set():
            line = self._connection.readline()
            if not line:
                # Read timeouts are normal; absence of radio packets does
                # not prove USB disconnection.
                continue

            reading = parse_telegram(line)
            if reading is None:
                with self._health_lock:
                    self._invalid_packets += 1
                continue
            received_at = datetime.now(timezone.utc)
            # Keep discovery synchronized with the same reception timestamp
            # used by receiver statistics. Ignored IDs remain observable in
            # the registry without being automatically configured.
            self._sensor_registry.observe(reading, received_at=received_at)
            with self._health_lock:
                self._last_packet_at = received_at
                self._valid_packets += 1
                previous = self._sensor_reception.get(reading.sensor_id)
                interval = (
                    max(0.0, (received_at - previous.last_seen).total_seconds())
                    if previous is not None
                    else None
                )
                average_interval = (
                    None if previous is None
                    else (
                        (
                            (previous.average_interval_seconds or 0.0)
                            * (previous.packets - 1)
                            + interval
                        ) / previous.packets
                    )
                )
                self._sensor_reception[reading.sensor_id] = SensorReception(
                    sensor_id=reading.sensor_id,
                    packets=1 if previous is None else previous.packets + 1,
                    last_seen=received_at,
                    internal_packets=(
                        (0 if previous is None else previous.internal_packets)
                        + (0 if reading.external_probe else 1)
                    ),
                    external_packets=(
                        (0 if previous is None else previous.external_packets)
                        + (1 if reading.external_probe else 0)
                    ),
                    first_seen=(
                        received_at if previous is None
                        else previous.first_seen or previous.last_seen
                    ),
                    last_interval_seconds=interval,
                    average_interval_seconds=average_interval,
                )
            # Quality checks are opt-in and never suppress original readings.
            # Rejected readings never replace the last accepted reference.
            if self._quality_rules is not None or self._quality_callback is not None:
                channel = (reading.sensor_id, reading.external_probe)
                result = assess_temperature(
                    reading,
                    rules=self._quality_rules,
                    previous_temperature=self._accepted_temperatures.get(channel),
                )
                if result.accepted:
                    self._accepted_temperatures[channel] = reading.temperature
                if self._quality_callback is not None:
                    try:
                        self._quality_callback(reading, result)
                    except Exception:
                        _LOGGER.exception("JeeLink quality callback failed")

            try:
                self._callback(reading)
            except Exception:
                # User callbacks must never kill the serial worker or force
                # a spurious USB reconnection.
                _LOGGER.exception("JeeLink packet callback failed")
