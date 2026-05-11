# Debug Cockpit

Use the debug cockpit when hardware and bridge behavior must be compared live.

It streams these sources into one timestamped terminal:

- Bridge MQTT watch for `hermes-stackchan/<pair-id>/#`
- Power watcher log
- Life animator log
- Touch lamp log
- ESP-IDF serial monitor

Start it with:

```bash
./scripts/debug_cockpit.sh --serial-port /dev/cu.usbmodem21301
```

Useful variants:

```bash
./scripts/debug_cockpit.sh --no-serial
./scripts/debug_cockpit.sh --no-mqtt
./scripts/debug_cockpit.sh --log /path/to/extra.log
```

Stop it with `Ctrl-C`.

The serial monitor is the hardware reference. If MQTT and serial disagree,
trust serial first and then inspect the bridge path.
