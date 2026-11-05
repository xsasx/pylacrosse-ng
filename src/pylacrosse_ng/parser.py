"""Pure parser for LaCrosse / Technoline JeeLink radio telegrams.

The supported framing is OK 9 <id> <flags> <temp_hi> <temp_lo> <humidity>.
Parsing has no serial-port or Home Assistant dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_TELEGRAM = re.compile(
    r"OK\s+9\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)"
)


@dataclass(frozen=True, slots=True)
class LaCrosseReading:
    """Decoded measurement with original protocol metadata."""

    sensor_id: int
    temperature: float
    humidity: int | None
    low_battery: bool
    new_battery: bool
    sensor_type: int
    raw_flags: int
    raw_humidity: int
    external_probe: bool
    raw_telegram: str


def parse_telegram(line: str | bytes) -> LaCrosseReading | None:
    """Decode one JeeLink telegram; return None for nonmatching input.

    Invalid humidity readings are represented as None rather than deleting
    a previously known humidity capability. The TX25TP-IT external probe
    uses flag 130 and has no humidity measurement.
    """
    if isinstance(line, bytes):
        try:
            line = line.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(line, str):
        raise TypeError("line must be str or bytes")

    raw = line.strip()
    match = _TELEGRAM.fullmatch(raw)
    if match is None:
        return None

    sensor_id, flags, temp_hi, temp_lo, humidity_byte = map(
        int, match.groups()
    )
    if not all(
        0 <= value <= 255
        for value in (sensor_id, flags, temp_hi, temp_lo, humidity_byte)
    ):
        return None

    external_probe = flags == 130
    humidity_value = humidity_byte & 0x7F
    humidity = (
        humidity_value
        if not external_probe and 0 <= humidity_value <= 100
        else None
    )

    return LaCrosseReading(
        sensor_id=sensor_id,
        temperature=(temp_hi * 256 + temp_lo - 1000) / 10,
        humidity=humidity,
        low_battery=bool(humidity_byte & 0x80),
        new_battery=bool(flags & 0x80) and not external_probe,
        sensor_type=flags & 0x7F,
        raw_flags=flags,
        raw_humidity=humidity_byte,
        external_probe=external_probe,
        raw_telegram=raw,
    )
