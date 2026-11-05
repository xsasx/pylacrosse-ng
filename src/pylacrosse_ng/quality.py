"""Opt-in temperature plausibility checks for LaCrosse readings.

The parser always preserves the original measurement. Applications can use
this independent helper to flag questionable readings without silently
modifying or discarding the raw data.

No default sensor temperature range is assumed: limits vary by hardware and
use case (freezer, outdoor, probe, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .parser import LaCrosseReading


@dataclass(frozen=True, slots=True)
class TemperatureQualityRules:
    """Optional limits for assessing a temperature measurement.

    max_change is the maximum permitted absolute change in Celsius from
    the previously accepted reading *of the same sensor and channel*.
    """

    minimum: float | None = None
    maximum: float | None = None
    max_change: float | None = None

    def __post_init__(self) -> None:
        for name in ("minimum", "maximum", "max_change"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum must not exceed maximum")
        if self.max_change is not None and self.max_change < 0:
            raise ValueError("max_change must not be negative")


@dataclass(frozen=True, slots=True)
class TemperatureQualityResult:
    """Immutable assessment; original reading is never changed."""

    accepted: bool
    reason: str | None
    temperature: float


def assess_temperature(
    reading: LaCrosseReading,
    *,
    rules: TemperatureQualityRules | None = None,
    previous_temperature: float | None = None,
) -> TemperatureQualityResult:
    """Check temperature against explicitly supplied limits.

    With rules=None, finite readings are accepted without filtering.
    For dual-temperature sensors the caller must supply history for the
    matching (sensor_id, external_probe) channel only.
    """
    temperature = reading.temperature
    if not math.isfinite(temperature):
        return TemperatureQualityResult(False, "non_finite", temperature)

    if rules is None:
        return TemperatureQualityResult(True, None, temperature)

    if rules.minimum is not None and temperature < rules.minimum:
        return TemperatureQualityResult(False, "below_minimum", temperature)
    if rules.maximum is not None and temperature > rules.maximum:
        return TemperatureQualityResult(False, "above_maximum", temperature)

    if rules.max_change is not None and previous_temperature is not None:
        if not math.isfinite(previous_temperature):
            raise ValueError("previous_temperature must be finite")
        if abs(temperature - previous_temperature) > rules.max_change:
            return TemperatureQualityResult(False, "excessive_change", temperature)

    return TemperatureQualityResult(True, None, temperature)
