# MQTT Display Smoke Test

This is the first Hermes2StackChan V1.0 hardware slice: the bridge sends MQTT commands and StackChan executes them locally with ACK/Error and retained status.

## Topics

- Command: `hermes-stackchan/desk/cmd/display`
- System command: `hermes-stackchan/desk/cmd/system`
- Face command: `hermes-stackchan/desk/cmd/face`
- Move command: `hermes-stackchan/desk/cmd/move`
- Motion command: `hermes-stackchan/desk/cmd/motion`
- Sound command: `hermes-stackchan/desk/cmd/sound`
- Audio command: `hermes-stackchan/desk/cmd/audio`
- LED command: `hermes-stackchan/desk/cmd/led`
- Device command: `hermes-stackchan/desk/cmd/device`
- Legacy say command: `hermes-stackchan/desk/cmd/say` exists in older firmware, but the bridge maps `say` to `display` and does not send beeps for normal Hermes replies.
- Status: `hermes-stackchan/desk/status`
- ACK: `hermes-stackchan/desk/ack`
- Error: `hermes-stackchan/desk/error`
- Events: `hermes-stackchan/desk/events`

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
scripts/h2s_bridge.sh watch --pair desk
```

Send the first display command:

```sh
scripts/h2s_bridge.sh send-display \
  --pair desk \
  --text "Hello from Hermes2StackChan" \
  --request-id test-001 \
  --wait-ack
```

Try hardware commands:

```sh
scripts/h2s_bridge.sh read-status --pair desk
scripts/h2s_bridge.sh status-health --pair desk
scripts/h2s_bridge.sh send-face --pair desk --emotion happy --wait-ack
scripts/h2s_bridge.sh send-move --pair desk --direction left --wait-ack
scripts/h2s_bridge.sh send-move --pair desk --direction center --wait-ack
scripts/h2s_bridge.sh send-motion --pair desk --profile circle --curve spline --speed-pct 35 --steps 40 --wait-ack
scripts/h2s_bridge.sh send-motion --pair desk --curve linear --points '[[0,0,0,25],[0,28,0,25],[0,-18,0,25],[0,0,0,25]]' --wait-ack
scripts/h2s_bridge.sh send-led --pair desk --mode party --wait-ack
scripts/h2s_bridge.sh send-device --pair desk --volume-pct 80 --brightness-pct 70 --wait-ack
scripts/h2s_bridge.sh send-sound --pair desk --frequency-hz 880 --duration-ms 140 --wait-ack
```

`send-motion --profile ...` is only a bridge-side test helper. The MQTT payload sent to StackChan always contains concrete `points`; the firmware does not keep named motion profiles. For Hermes integration, let Hermes compute the waypoint sequence and publish `points` directly.

React to USB/battery power changes:

```sh
scripts/h2s_bridge.sh watch-power --pair desk
```

`watch-power` listens to `hermes-stackchan/desk/status`. When the AXP2101 reports a transition from battery to external power or back, the bridge displays the battery percentage and charge state for about five seconds and starts the matching head motion immediately. After the overlay, it switches to the matching face: external power becomes happy; unplugging becomes neutral. It does not change LEDs or sound, so Hermes can keep using those channels.

For normal local use, start it as a background process:

```sh
scripts/start_power_watcher.sh desk
scripts/status_power_watcher.sh
scripts/stop_power_watcher.sh
```

Run the idle life animator:

```sh
scripts/start_life_animator.sh desk
scripts/status_life_animator.sh desk
scripts/stop_life_animator.sh desk
```

`animate-life` only sends small face and mostly subtle head impulses while the retained status reports `ui.mode: face`, the display is awake, and StackChan is not recording or speaking. The firmware renders blink, normal breathing, occasional deep breathing, tiny `Z` micro-sleeps, small mouth impulses, and pupil-glance impulses as short smooth frame animations and returns to the current default face. Sometimes StackChan first glances with the pupils, then gently turns the head in that direction, and finally centers the pupils again. Upward glances are slightly favored so he does not feel stuck looking down. Rarely, StackChan performs a bigger desk-scan sweep left/right, a cautious up/down scan, or a diagonal room glance and returns to center; the Bridge sends matching pupil glances for each sweep segment so the eyes track the head direction. Firmware also glances in the detected movement direction for direct `move` and `motion` commands. Servo life motions are bounded and can be disabled with `--no-motion`. It never sends LED or sound commands.

## Audio Control Slice

Issue #6 starts with the control contract for wakeword and push-to-talk. Firmware now initializes the ES7210 microphone and runs a local voice-activity detector. This slice still does not upload PCM audio to the bridge, but it gives firmware, bridge, MQTTX, and later Hermes one stable way to set and observe speech mode.

Enable the configured wakeword:

```sh
scripts/h2s_bridge.sh send-audio \
  --pair desk \
  --action set_wakeword \
  --wakeword Computer \
  --enabled \
  --wait-ack
```

Simulate a wakeword trigger and let the firmware auto-stop when speech has been heard and then falls below the silence threshold:

```sh
scripts/h2s_bridge.sh send-audio \
  --pair desk \
  --action simulate_wakeword \
  --wakeword Computer \
  --silence-timeout-ms 500 \
  --wait-ack
```

Start and stop recording manually:

```sh
scripts/h2s_bridge.sh send-audio --pair desk --action start_recording --source push_to_talk --wait-ack
scripts/h2s_bridge.sh send-audio --pair desk --action stop_recording --source push_to_talk --reason manual_stop --wait-ack
```

The retained status includes top-level `wakeword_enabled` and `recording`, plus an `audio` object with `input_ready`, `wakeword`, `recording_source`, timing fields, `voice_active`, `voice_level_pct`, `voice_avg_level`, and `voice_peak_level`. The firmware also publishes realtime state changes to `hermes-stackchan/desk/events`, for example `wakeword_detected`, `recording_started`, and `recording_stopped`.

While recording, the face remains visible and the face renderer adds a small waveform overlay below the mouth from the same framebuffer before the frame is flushed. The overlay is the first `FaceExtraMode`; later extras can use the same hook without replacing the face screen.

For a quick head-touch hardware test, run the bridge touch lamp watcher:

```sh
scripts/h2s_bridge.sh watch-touch-lamp --pair desk
```

StackChan publishes `touch_down` and `touch_up` events from the SI12T head-touch sensor. A touch starts recording immediately in firmware. Releasing touch does not stop recording; recording stops when the microphone has heard speech and then sees about 500 ms of silence, or after a no-voice timeout. A touch turns the LEDs solid green immediately through the bridge watcher. They stay green while recording is active and turn off about 500 ms after `recording_stopped` or a retained status update reports `recording:false` after an active recording. This is intentionally bridge-driven so the MQTT event path is visible. The watcher logs the bridge-side publish timing in verbose mode.

To watch both bridge and firmware logs during a touch test:

```sh
tail -f touch-lamp.log
```

For serial firmware logs:

```sh
cd firmware
source /Users/wolfgangvieregg/development/esp-idf-v5.5.4/export.sh
idf.py -p /dev/cu.usbmodem21301 monitor | rg --line-buffered 'touch|SI12T|MQTT|led'
```

For voice/VAD debugging, include microphone lines too:

```sh
idf.py -p /dev/cu.usbmodem21301 monitor | rg --line-buffered 'touch|voice|microphone|recording|led'
```

## Hermes HTTP Adapter

The bridge can now ask a configured Hermes HTTP server and dispatch the returned JSON actions to StackChan over MQTT.

Add these local values to `.env`:

```sh
H2S_HERMES_BASE_URL=http://192.168.99.58:8642
H2S_HERMES_MODEL=default
H2S_HERMES_API_KEY=
H2S_HERMES_TIMEOUT_S=30
```

Check the Hermes server:

```sh
scripts/h2s_bridge.sh hermes-health
```

Ask Hermes and publish the resulting StackChan actions:

```sh
scripts/h2s_bridge.sh ask-hermes \
  --pair desk \
  --text "Sag kurz Hallo und lächle." \
  --show-response
```

Hermes is instructed to return JSON only and put the spoken answer in the top-level `reply` field. For external Hermes-triggered messages the bridge shows `reply` with `display` and then sends TTS, so Hermes should not use `say` for normal answers.

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
6. Hardware commands publish ACK/Error and the retained `status` reflects the new state, including `battery_pct`, `battery_charging`, `battery_discharging`, `temperature.soc_c`, `temperature.servo_yaw_c`, `temperature.servo_pitch_c`, `interaction`, and `sensors`.

The firmware publishes an `interaction` event when the BMI270 IMU detects real device movement or the LTR553 proximity sensor sees something close in front of StackChan. These events reset the bridge idle-sleep timer and wake a sleeping display. Internal head-servo motion is ignored for IMU interaction detection so idle animations do not keep waking the device by themselves.
