"""Thread-safe discovery and radio-ID classification for JeeLink sensors.

The registry is intentionally independent of Home Assistant and serial
transport. Applications retain ownership of persistent configuration: the
library never auto-configures discovered radio IDs or reassigns sensors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading

from .parser import LaCrosseReading


def _validate_radio_id(radio_id: int) -> int:
    if isinstance(radio_id, bool) or not isinstance(radio_id, int):
        raise TypeError("radio ID must be an integer")
    if not 0 <= radio_id <= 255:
        raise ValueError("radio ID must be between 0 and 255")
    return radio_id


@dataclass(frozen=True, slots=True)
class DiscoveredSensor:
    """Immutable discovery snapshot for one radio ID."""

    radio_id: int
    classification: str
    first_seen: datetime
    last_seen: datetime
    packets: int
    last_temperature: float
    last_humidity: int | None
    low_battery: bool
    internal_packets: int
    external_packets: int
    last_new_battery_seen: datetime | None


class SensorRegistry:
    """Track discoveries and classify configured/ignored/unknown radio IDs.

    Configuration updates are explicit and thread-safe. Radio IDs received
    from the air never modify configured or ignored memberships. The library
    does not persist these sets; callers should save their configuration.
    """

    def __init__(
        self,
        *,
        configured_ids: set[int] | frozenset[int] | None = None,
        ignored_ids: set[int] | frozenset[int] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._configured: set[int] = set()
        self._ignored: set[int] = set()
        self._discovered: dict[int, DiscoveredSensor] = {}
        self.set_memberships(
            configured_ids=set(configured_ids or ()),
            ignored_ids=set(ignored_ids or ()),
        )

    def set_memberships(
        self,
        *,
        configured_ids: set[int],
        ignored_ids: set[int],
    ) -> None:
        """Replace memberships atomically, rejecting conflicting IDs."""
        configured = {_validate_radio_id(i) for i in configured_ids}
        ignored = {_validate_radio_id(i) for i in ignored_ids}
        if configured & ignored:
            raise ValueError("a radio ID cannot be configured and ignored")
        with self._lock:
            self._configured = configured
            self._ignored = ignored

    @property
    def configured_ids(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._configured)

    @property
    def ignored_ids(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._ignored)

    def ignore(self, radio_id: int) -> None:
        """Ignore an unconfigured radio ID, without losing discovery data."""
        radio_id = _validate_radio_id(radio_id)
        with self._lock:
            if radio_id in self._configured:
                raise ValueError("cannot ignore a configured radio ID")
            self._ignored.add(radio_id)

    def restore(self, radio_id: int) -> None:
        """Stop ignoring a radio ID; existing discovery remains available."""
        radio_id = _validate_radio_id(radio_id)
        with self._lock:
            self._ignored.discard(radio_id)

    def classification(self, radio_id: int) -> str:
        """Return configured, ignored, or unknown."""
        radio_id = _validate_radio_id(radio_id)
        with self._lock:
            if radio_id in self._configured:
                return "configured"
            if radio_id in self._ignored:
                return "ignored"
            return "unknown"

    def observe(
        self,
        reading: LaCrosseReading,
        *,
        received_at: datetime | None = None,
    ) -> DiscoveredSensor:
        """Record one packet and return its updated discovery snapshot.

        The TX25TP-IT external probe's flag 130 must not count as a
        new-battery event. Ordinary packets retain the last genuine event.
        """
        radio_id = _validate_radio_id(reading.sensor_id)
        if received_at is None:
            received_at = datetime.now(timezone.utc)
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")

        with self._lock:
            previous = self._discovered.get(radio_id)
            if radio_id in self._configured:
                classification = "configured"
            elif radio_id in self._ignored:
                classification = "ignored"
            else:
                classification = "unknown"

            genuine_battery = reading.new_battery and not reading.external_probe
            snapshot = DiscoveredSensor(
                radio_id=radio_id,
                classification=classification,
                first_seen=previous.first_seen if previous else received_at,
                last_seen=received_at,
                packets=(previous.packets + 1) if previous else 1,
                last_temperature=reading.temperature,
                last_humidity=reading.humidity,
                low_battery=reading.low_battery,
                internal_packets=(
                    (previous.internal_packets if previous else 0)
                    + (0 if reading.external_probe else 1)
                ),
                external_packets=(
                    (previous.external_packets if previous else 0)
                    + (1 if reading.external_probe else 0)
                ),
                last_new_battery_seen=(
                    received_at if genuine_battery
                    else previous.last_new_battery_seen if previous else None
                ),
            )
            self._discovered[radio_id] = snapshot
            return snapshot

    def discoveries(
        self, *, include_ignored: bool = False
    ) -> tuple[DiscoveredSensor, ...]:
        """Return a stable radio-ID-sorted snapshot of discovered sensors."""
        with self._lock:
            results = []
            for radio_id in sorted(self._discovered):
                record = self._discovered[radio_id]
                status = (
                    "configured" if radio_id in self._configured
                    else "ignored" if radio_id in self._ignored
                    else "unknown"
                )
                if status == "ignored" and not include_ignored:
                    continue
                # Memberships can change without a new radio packet.
                results.append(
                    DiscoveredSensor(
                        radio_id=record.radio_id,
                        classification=status,
                        first_seen=record.first_seen,
                        last_seen=record.last_seen,
                        packets=record.packets,
                        last_temperature=record.last_temperature,
                        last_humidity=record.last_humidity,
                        low_battery=record.low_battery,
                        internal_packets=record.internal_packets,
                        external_packets=record.external_packets,
                        last_new_battery_seen=record.last_new_battery_seen,
                    )
                )
            return tuple(results)
