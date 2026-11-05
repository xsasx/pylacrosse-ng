# JeeLink hardware validation (pre-release)

This checklist validates pylacrosse-ng on a **real JeeLink receiver** without
silently changing Home Assistant configuration or sensor identities.

> **Production safety:** Never let two processes open the same serial device.
> If your JeeLink is currently assigned to Home Assistant, **do not run this
> test against that port**. Use a spare JeeLink attached to an isolated
> development machine/container with exclusive USB access. Do not unplug the
> production adapter or stop Home Assistant just for these checks.

## 1. Prepare an isolated test environment

Requirements: Python 3.11+, access to an unused JeeLink serial device, and
at least one transmitting LaCrosse/Technoline sensor.

~~~bash
git clone https://github.com/xsasx/pylacrosse-ng.git
cd pylacrosse-ng
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m unittest discover -s tests -p 'test_*.py' -v
~~~

Identify **only the spare receiver** using a persistent device name:

~~~bash
ls -l /dev/serial/by-id/
~~~

For an LXC/container, the serial device must already be explicitly passed
through to the isolated test environment. Do not change production LXC/VM
USB mappings to run this test.

## 2. Receive real measurements (read-only smoke test)

Set the port to the **spare** JeeLink device. This program reads packets
and prints diagnostics but does not edit any Home Assistant entities or
persist radio-ID assignments.

~~~bash
export JEELINK_TEST_PORT='/dev/serial/by-id/YOUR-SPARE-JEELINK'
python - <<'PY'
import os
import time

from pylacrosse_ng import LaCrosseReceiver, SerialConnection

port = os.environ["JEELINK_TEST_PORT"]


def on_reading(reading):
    channel = "external" if reading.external_probe else "internal"
    print(
        f"ID={reading.sensor_id} channel={channel} "
        f"temp={reading.temperature:.1f}°C "
        f"humidity={reading.humidity} "
        f"low_battery={reading.low_battery} "
        f"new_battery={reading.new_battery}",
        flush=True,
    )


receiver = LaCrosseReceiver(
    SerialConnection(port, baudrate=57600, timeout=1.0),
    callback=on_reading,
)
receiver.start()
try:
    for _ in range(12):  # Two minutes, no infinite background task
        time.sleep(10)
        health = receiver.health
        stats = receiver.reception_statistics
        print(
            f"receiver={health.status} attempts={health.connection_attempts} "
            f"connected={health.successful_connections} "
            f"valid={stats.valid_packets} invalid={stats.invalid_packets}",
            flush=True,
        )
        for sensor in receiver.sensor_registry.discoveries():
            print(
                f"  discovery ID={sensor.radio_id} "
                f"class={sensor.classification} packets={sensor.packets} "
                f"internal={sensor.internal_packets} "
                f"external={sensor.external_packets}",
                flush=True,
            )
except KeyboardInterrupt:
    pass
finally:
    receiver.stop()
    print(f"after stop: {receiver.health.status}", flush=True)
PY
~~~

**Expected:** Health reaches \`connected\`; valid packet count increases
when supported sensors transmit; each sensor appears in discovery; shutdown
reaches \`stopped\`. Empty reads without radio packets must not increment
connection attempts. Check timestamps and temperature/humidity values against
a known reference sensor.

**Important:** A successful serial connection **does not guarantee** that a
radio telegram will arrive in two minutes. Position, hardware protocol,
sensor transmission interval, battery state, or antenna may prevent reception.

## 3. Optional disruption/reconnect test — spare receiver ONLY

Only after the read-only test passes, and **only on a spare JeeLink**:

1. While the smoke test is running, physically disconnect **the spare USB
   receiver**, not the Home Assistant receiver.
2. Record changes in \`receiver.health.status\`,
   \`connection_attempts\`, \`last_error\`, and
   \`successful_connections\`.
3. Reconnect **the same spare receiver**; check whether its serial device
   path remains stable. A \`/dev/serial/by-id/\` link may disappear/reappear.
4. Confirm that subsequent radio packets resume and previous discovery
   records and counters remain available.
5. Repeat at most a few times. Stop if the OS/driver behaves unexpectedly.

A hardware unplug/replug may expose OS-dependent read/close behavior that
unit tests with mock serial ports cannot establish. **Do not mark USB
recovery as hardware-validated without collecting actual evidence.**

## 4. Additional manual checks

- [ ] Supported TX29DTH-IT or other ordinary sensor: temperature/humidity
  and battery flags match actual values.
- [ ] TX25TP-IT (if available): internal and external packets use the same
  radio ID but distinct \`external_probe\` flags; external humidity is absent.
- [ ] Invalid/partial packets do not terminate the receiver.
- [ ] Normal shutdown finishes without blocked threads.
- [ ] A genuine battery-change event is observed (if performed deliberately
  with a **non-production test sensor**).
- [ ] Battery reassignment proposes candidates only; no automatic
  modification of configured identities.
- [ ] Reconnect test on spare hardware completed, with status/error evidence.
- [ ] Home Assistant migration tested separately in a staging environment.

## 5. Report results

When reporting a test, include:
- OS and Python version; JeeLink model/firmware; test setup (host/LXC/VM)
- Device access method (\`/dev/serial/by-id/\`, without private host details)
- Sensor models and redacted sample \`OK 9 ...\` packets
- First and last health snapshots, packet counters, shutdown result
- Whether physical reconnect was performed and observed behavior
- Any exceptions, tracebacks, or differences in data decoding

**Release gate:** Passing CI plus a mock-serial suite is not a substitute
for real hardware validation. Keep the library experimental and the
production Home Assistant integration unchanged until staging migration
and the real receiver tests succeed.
