# Debug Cockpit

Use the debug cockpit when hardware and bridge behavior must be compared live.

It streams these sources into one timestamped terminal:

- Bridge `/healthz?status=1` summary
- Bridge MQTT watch for `hermes-stackchan/<pair-id>/#`
- Optional remote `journalctl --user -u hermes2stackchan.service -f`
- Remote bridge log file: `/home/wollux/.hermes/logs/hermes2stackchan.log`
- Power watcher log
- Life animator log
- Touch lamp log
- ESP-IDF serial monitor

Start it with:

```bash
./scripts/debug_cockpit.sh --serial-port /dev/cu.usbmodem21301
```

For the Pi bridge plus local serial:

```bash
H2S_BRIDGE_URL=http://192.168.99.58:8788 ./scripts/debug_cockpit.sh \
  --remote-host 192.168.99.58 \
  --remote-user wollux \
  --serial-port /dev/cu.usbmodem21301
```

Useful variants:

```bash
./scripts/debug_cockpit.sh --no-serial
./scripts/debug_cockpit.sh --no-mqtt
./scripts/debug_cockpit.sh --no-health
./scripts/debug_cockpit.sh --remote-host 192.168.99.58 --no-remote-log
./scripts/debug_cockpit.sh --log /path/to/extra.log
```

Stop it with `Ctrl-C`.

The serial monitor is the hardware reference. If MQTT and serial disagree,
trust serial first and then inspect the bridge path.

On the current Pi user-service install, stdout/stderr are appended to:

```bash
tail -f /home/wollux/.hermes/logs/hermes2stackchan.log
```

Use this when `journalctl --user -u hermes2stackchan.service` has no entries.
