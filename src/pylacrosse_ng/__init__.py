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
from .parser import LaCrosseReading, parse_telegram
from .receiver import LaCrosseReceiver, ReceiverHealth

__version__ = "0.1.0.dev0"

__all__ = [
    "LaCrosseReading",
    "LaCrosseReceiver",
    "ReceiverHealth",
    "SerialConnection",
    "SerialConnectionError",
    "__version__",
    "parse_telegram",
]
