# pylacrosse-ng

**Modern Python library for LaCrosse / Technoline wireless sensors via JeeLink.**

> 🚧 **Status: Pre-alpha development (0.1.0.dev0)**
>
> Parsing, serial transport, automatic reconnection, and receiver health snapshots are implemented and covered by automated tests. **There is no published release or hardware-validated migration yet.** The public API may change; do not replace a production integration with this package yet.

## ✨ Features implemented

- 🔌 Serial transport using **pyserial**, with configurable port, baud rate and read timeout
- 🔄 Automatic reconnection after serial read/open errors, with bounded exponential backoff
- 🛡️ Single background receiver worker and controlled shutdown
- 📡 Thread-safe receiver health snapshots
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

## 🗺️ Development roadmap

1. ✅ Review original pylacrosse behavior and establish parser compatibility tests
2. ✅ Set up packaging, LGPL license, and automated regression testing
3. ✅ Implement serial transport and initial automatic recovery
4. ✅ Add receiver health snapshots
5. ⏳ Harden thread/callback lifecycle and verify on real JeeLink hardware
6. ⏳ Extend sensor and multi-channel compatibility tests
7. ⏳ Validate the Home Assistant migration and prepare the first release

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
