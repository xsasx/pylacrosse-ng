# pylacrosse-ng

**Modern Python library for LaCrosse / Technoline wireless sensors via JeeLink.**

> 🚧 **Status: Pre-alpha development (0.1.0.dev0)**
>
> Parsing, serial transport, automatic reconnection, receiver health, sensor discovery, and guarded battery reassignment are implemented and covered by automated tests. **There is no published release or hardware-validated migration yet.** The public API may change; do not replace a production integration with this package yet.

## ✨ Features implemented

- 🔌 Serial transport using **pyserial**, with configurable port, baud rate and read timeout
- 🔄 Automatic reconnection after serial read/open errors, with bounded exponential backoff
- 🛡️ Single background receiver worker and controlled shutdown
- 📡 Thread-safe receiver health snapshots, per-sensor packet statistics and adaptive ACTIVE/STALE assessment
- 🔎 Automatic sensor discovery with configured / ignored / unknown radio-ID classification
- 🔋 Guarded battery-change reassignment with explicit confirmation and ignored-ID protection
- 🌡️ Optional temperature plausibility checks with separate TX25TP-IT channel histories
- 🌡️ LaCrosse / Technoline telegram parsing with temperature, humidity and battery flags
- 🌡️ TX25TP-IT external-probe packet identification (flag 130) and raw channel metadata
- 💧 Invalid humidity readings (106% / 125%) represented as missing measurements
- 🧪 Parser, connection lifecycle, recovery and health regression tests
- 📦 Wheel build and clean-environment import test in GitHub Actions

**Not yet validated:** Home Assistant migration, physical USB disconnect/reconnect behavior, and stable API compatibility.

## 🚀 Python example

The package currently provides an experimental API:

~~~python
from pylacrosse_ng import LaCrosseReceiver, SerialConnection

def on_reading(reading):
    channel = "external" if reading.external_probe else "standard/internal"
    print(f"Sensor {reading.sensor_id} ({channel}): {reading.temperature:.1f} °C")
    if reading.humidity is not None:
        print(f"Humidity: {reading.humidity}%")

connection = SerialConnection(
    "/dev/serial/by-id/YOUR-JEELINK-DEVICE",
    baudrate=57600,
    timeout=2.0,
)

receiver = LaCrosseReceiver(
    connection,
    callback=on_reading,
    retry_initial=1.0,
    retry_max=60.0,
)

receiver.start()
try:
    # Keep your application running until its shutdown signal.
    ...
finally:
    receiver.stop()
~~~

This is a library usage example, not a standalone executable application. Call `stop()` from your application's shutdown path.

### Recovery settings

| Parameter | Default | Purpose |
|---|---|---|
| `port` | Required | JeeLink serial device path |
| `baudrate` | `57600` | Serial baud rate |
| `timeout` | `2.0` seconds | Finite read timeout for clean shutdown |
| `retry_initial` | `1.0` second | Initial reconnect delay |
| `retry_max` | `60.0` seconds | Maximum reconnect delay |
| `retry_multiplier` | `2.0` | Backoff multiplier (minimum 1) |
| `auto_reconnect` | `True` | Enable retry after connection failures |

An open failure or serial read exception starts reconnect attempts with exponential backoff. Backoff resets after successful connection. Empty reads due to timeouts are treated as normal radio silence and **do not** trigger reconnects.

### Receiver diagnostics

Read the immutable snapshot with `receiver.health`:

~~~python
health = receiver.health
print(health.status)                  # stopped / connecting / connected / reconnecting
print(health.last_packet_at)          # UTC datetime or None
print(health.last_connected_at)       # UTC datetime or None
print(health.connection_attempts)     # Number of open attempts
print(health.successful_connections)  # Successful opens
print(health.last_error)              # Latest connection error or None
~~~

Receiver health is independent of individual sensor availability. No recent packets does **not** necessarily mean the USB receiver has disconnected.

### Sensor discovery and classification

Every valid radio telegram automatically updates `receiver.sensor_registry`. The registry tracks discovered radio IDs, received packet counts, latest readings, battery flags and first/last reception times.

~~~python
from pylacrosse_ng import SensorRegistry, LaCrosseReceiver, SerialConnection

registry = SensorRegistry(configured_ids={5, 10}, ignored_ids={8})
receiver = LaCrosseReceiver(
    SerialConnection("/dev/serial/by-id/YOUR-JEELINK-DEVICE"),
    callback=lambda reading: print(reading),
    sensor_registry=registry,
)

# Once receiving, inspect discoveries:
visible = registry.discoveries()                 # Ignored radio IDs hidden
all_discovered = registry.discoveries(include_ignored=True)
registry.ignore(60)                             # Cannot ignore configured IDs
registry.restore(60)
~~~

The library **does not automatically assign detected radio IDs**. Applications own persistent configuration, names and device identities. An ignored radio ID remains observable internally but is hidden from normal discovery views.

### Safe battery-change reassignment

The library records genuine new-battery events while excluding TX25TP-IT external-probe packets from battery-change detection. Replacement candidates are suggested only within an explicit time window and are never assigned without confirmation.

~~~python
from datetime import datetime, timedelta, timezone

since = datetime.now(timezone.utc)
# Start the guided battery change, replace the battery, and receive packets.
candidates = registry.battery_reassignment_candidates(
    5, since=since, max_age=timedelta(minutes=10)
)

# Only after the user explicitly chooses and confirms a candidate:
# updated_ids = registry.confirm_battery_reassignment(
#     5, chosen_radio_id, since=since
# )
# Persist updated_ids in the calling application.
~~~

Confirmation rechecks that the target ID is neither ignored nor already configured, and that a fresh genuine battery event exists. This registry update does not change Home Assistant entities or devices.

### Temperature quality assessment

Use `TemperatureQualityRules` to enable optional plausibility checks (minimum, maximum and maximum change). Set `quality_rules` and an optional `quality_callback` on `LaCrosseReceiver`. Raw sensor callbacks still receive every valid packet; questionable temperatures are reported, not silently discarded. Accepted-value histories are tracked independently by sensor ID and internal/external probe channel.

## 🗺️ Development roadmap

1. ✅ Review original pylacrosse behavior and establish parser compatibility tests
2. ✅ Set up packaging, LGPL license, and automated regression testing
3. ✅ Implement serial transport and initial automatic recovery
4. ✅ Add receiver health snapshots
5. 🚧 Harden thread/callback lifecycle and verify on real JeeLink hardware
   - ✅ Automated tests for concurrent start/stop, callback failures, and unexpected receiver errors
   - ⏳ Validate actual USB unplug/replug, blocked reads, and recovery on physical JeeLink hardware
6. 🚧 Extend sensor and multi-channel compatibility tests
   - ✅ TX25TP-IT internal/external channel parsing and automated regression tests
   - ✅ Per-sensor packet statistics, reception intervals, and adaptive ACTIVE/STALE assessment
   - ✅ Optional temperature plausibility checks and receiver quality callback tests
   - ⏳ Expand real-hardware coverage and validate additional sensor models
7. ✅ Add reusable sensor discovery and management APIs to the library
   - ✅ Configured/ignored/unknown sensor classification and controlled ignore/restore
   - ✅ Automatic registry population from receiver packets
   - ✅ Battery-change candidates, fresh-event checks, and revalidation on explicit confirmation
   - ⏳ Verify guided reassignment behavior with physical sensors and the Home Assistant configuration flow
8. ⏳ Validate the Home Assistant migration and prepare the first release

*Roadmap checkmarks indicate completed implementation or automated tests, not production hardware validation.*

## 🏠 Home Assistant integration

This project is intended as a future foundation for **[LaCrosse JeeLink for Home Assistant](https://github.com/xsasx/home-assistant-lacrosse-jeelink)**.

The Home Assistant integration currently uses the original `pylacrosse` dependency. Migration will happen only after additional compatibility and hardware validation.

## 🙏 Credits & Licensing

This project builds on the concepts and behavior of the original [python-lacrosse / pylacrosse](https://github.com/hthiery/python-lacrosse) project by Heiko Thiery.

The original project uses the **LGPL-2.1-or-later** license. Its license text is included in [COPYING](COPYING). Preserve applicable upstream copyright and license notices when incorporating original source code.

## ☕ Support the Project

If you find this project or my Home Assistant integrations useful and would like to support continued open-source development, you can buy me a coffee! ☕

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/xsas1337)

Thank you for supporting open-source development! ❤️

## 🤝 Contributing

Hardware testing, bug reports, protocol documentation and pull requests are welcome.

Please note that APIs and implementation details may change during early development.

## 📦 Releases

There is **no published pylacrosse-ng release yet**. For production Home Assistant use, continue using the existing [LaCrosse JeeLink integration](https://github.com/xsasx/home-assistant-lacrosse-jeelink).
