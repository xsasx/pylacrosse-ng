"""Public API for pylacrosse-ng.

The API is experimental until the first stable library release.

Basic usage::

    from pylacrosse_ng import LaCrosseReceiver, SerialConnection

    receiver = LaCrosseReceiver(
        SerialConnection("/dev/ttyUSB0"),
        callback=lambda reading: print(reading),
    )
    receiver.start()
    # Call receiver.stop() during application shutdown.
"""

from __future__ import annotations

from .connection import SerialConnection, SerialConnectionError
from .discovery import BatteryReassignmentCandidate, DiscoveredSensor, SensorRegistry
from .parser import LaCrosseReading, parse_telegram
from .quality import (
    TemperatureQualityResult,
    TemperatureQualityRules,
    assess_temperature,
)
from .receiver import (
    LaCrosseReceiver,
    ReceiverHealth,
    ReceptionStatistics,
    SensorActivity,
    SensorReception,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "BatteryReassignmentCandidate",
    "DiscoveredSensor",
    "LaCrosseReading",
    "LaCrosseReceiver",
    "ReceiverHealth",
    "ReceptionStatistics",
    "SensorActivity",
    "SensorReception",
    "SensorRegistry",
    "SerialConnection",
    "SerialConnectionError",
    "TemperatureQualityResult",
    "TemperatureQualityRules",
    "__version__",
    "assess_temperature",
    "parse_telegram",
]
