# MQTT Display Smoke Test

This is the first Hermes2StackChan V1.0 hardware slice: the bridge sends MQTT commands and StackChan executes them locally with ACK/Error and retained status.

## Topics

- Command: `hermes-stackchan/desk/cmd/display`
- System command: `hermes-stackchan/desk/cmd/system`
- Face command: `hermes-stackchan/desk/cmd/face`
- Move command: `hermes-stackchan/desk/cmd/move`
- Motion command: `hermes-stackchan/desk/cmd/motion`
- Sound command: `hermes-stackchan/desk/cmd/sound`
- LED command: `hermes-stackchan/desk/cmd/led`
- Device command: `hermes-stackchan/desk/cmd/device`
- Say command: `hermes-stackchan/desk/cmd/say`
- Status: `hermes-stackchan/desk/status`
- ACK: `hermes-stackchan/desk/ack`
- Error: `hermes-stackchan/desk/error`

## Bridge Setup

Create a local environment file from the public example:

```sh
cp .env.example .env
```

Edit `.env` and set your MQTT broker host and port. The local `.env` file is ignored by Git.

Check that the broker is reachable before testing:

```sh
set -a
source .env
set +a
nc -vz "$H2S_MQTT_HOST" "$H2S_MQTT_PORT"
```

If the broker should run on a Debian/Raspberry Pi host, a simple Mosquitto setup is enough for this smoke test:

```sh
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Install the bridge dependency:

```sh
python3 -m pip install -e .
```

Watch all messages for the `desk` pair:

```sh
python3 -m bridge.hermes2stackchan_bridge --env .env watch --pair desk
```

Send the first display command:

```sh
python3 -m bridge.hermes2stackchan_bridge --env .env send-display \
  --pair desk \
  --text "Hello from Hermes2StackChan" \
  --request-id test-001 \
  --wait-ack
```

Try hardware commands:

```sh
python3 -m bridge.hermes2stackchan_bridge --env .env send-face --pair desk --emotion happy --wait-ack
python3 -m bridge.hermes2stackchan_bridge --env .env send-move --pair desk --direction left --wait-ack
python3 -m bridge.hermes2stackchan_bridge --env .env send-move --pair desk --direction center --wait-ack
python3 -m bridge.hermes2stackchan_bridge --env .env send-motion --pair desk --profile circle --curve spline --speed-pct 35 --steps 40 --wait-ack
python3 -m bridge.hermes2stackchan_bridge --env .env send-motion --pair desk --curve linear --points '[[0,0,0,25],[0,28,0,25],[0,-18,0,25],[0,0,0,25]]' --wait-ack
python3 -m bridge.hermes2stackchan_bridge --env .env send-led --pair desk --mode party --wait-ack
python3 -m bridge.hermes2stackchan_bridge --env .env send-device --pair desk --volume-pct 80 --brightness-pct 70 --wait-ack
python3 -m bridge.hermes2stackchan_bridge --env .env send-sound --pair desk --frequency-hz 880 --duration-ms 140 --wait-ack
```

`send-motion --profile ...` is only a bridge-side test helper. The MQTT payload sent to StackChan always contains concrete `points`; the firmware does not keep named motion profiles. For Hermes integration, let Hermes compute the waypoint sequence and publish `points` directly.

## Firmware Setup

The firmware uses the same local `.env` file. Sync the firmware-related values into the ignored ESP-IDF `firmware/sdkconfig` file:

```sh
python3 scripts/apply_firmware_env.py --env .env
```

Then build and flash:

```sh
cd firmware
source /Users/wolfgangvieregg/development/esp-idf-v5.5.4/export.sh
idf.py set-target esp32s3
```

These `.env` values are used for firmware:

- `H2S_WIFI_SSID`
- `H2S_WIFI_PASSWORD`
- `H2S_MQTT_URI`, for example `mqtt://192.168.99.58:1883`
- `H2S_PAIR_ID`, default `desk`
- `H2S_STACKCHAN_ID`, default `stackchan-desk`

The firmware stops on a visible `SET WIFI CONFIG` or `SET MQTT URI` screen when those local values are empty or were not synced.

```sh
idf.py build
idf.py -p /dev/cu.usbmodem21301 flash monitor
```

## Expected Result

1. StackChan boots and shows a Hermes2StackChan start screen.
2. StackChan connects to WiFi and MQTT.
3. StackChan publishes retained status.
4. The bridge sends a `cmd/display` message.
5. StackChan shows the text and publishes an ACK with the same `request_id`.
6. Hardware commands publish ACK/Error and the retained `status` reflects the new state.
