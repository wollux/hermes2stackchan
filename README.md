# hermes2stackchan

Hermes2StackChan connects one Hermes agent to one StackChan companion device.

The design is intentionally split:

- StackChan firmware is the safe hardware body: display, face, LEDs, touch, audio capture, speaker, servos, status.
- The Python bridge is the protocol brain: MQTT routing, status reading, Hermes prompting, STT, TTS, service runtime, validation.
- Hermes is the intelligence: conversation, decisions, and safe hardware intentions.

## What Works Now

This repository is already beyond the first MQTT smoke test. The current feature branch contains the working speech and companion slice:

- ESP32-S3 firmware for StackChan.
- Wi-Fi connection from firmware.
- MQTT namespace per fixed pair: `hermes-stackchan/<pair-id>/...`.
- Default pair: `desk`.
- Retained status on `hermes-stackchan/desk/status`.
- Structured ACK, error, and event topics.
- Display text commands.
- Face rendering with eyes, pupils, mouth, blink, breathing, gaze directions, sleep hints, battery states, and simple emotions.
- Idle life animation from the bridge while StackChan is idle.
- Head movement with soft limits and smooth waypoint paths.
- Expressive motion commands for nodding, shaking, scans, circles, and Hermes-selected motion profiles.
- LED/neon commands and recording-level LED feedback.
- Speaker volume and local tone test commands.
- Display brightness, display sleep, display wake, reboot, ping, and status commands.
- Battery and power status in retained MQTT state.
- Power watcher reactions for plug/unplug without taking over LEDs.
- Temperature fields for SoC and servos where available.
- ES7210 microphone recording.
- Built-in WakeNet wakeword `Computer`.
- Push-to-talk from touch.
- Local voice activity detection: stop after speech plus short silence.
- HTTP WAV upload from StackChan to the bridge.
- Groq Whisper STT through the bridge.
- Hermes chat call with current StackChan status, capabilities, and personality.
- Validated Hermes hardware actions dispatched over MQTT.
- Edge/Katja German TTS generation.
- TTS WAV returned to StackChan and played through the speaker.
- Follow-up listening mode: when Hermes asks a real question, StackChan speaks first and then starts recording again.
- Debug cockpit script for bridge log plus serial monitor.
- Raspberry Pi service installer for the unified bridge.

Still intentionally not part of v1.0:

- Radio playback. It was tested in the prototype and interfered with the wakeword/audio loop.
- Multi-StackChan group control.
- Camera capture and image-to-Hermes flow.
- Displaying images from Hermes.
- Custom trained wakewords.
- Production-grade CI for firmware builds.

## Architecture

Each Hermes/StackChan pair is isolated.

```text
Hermes Agent
    |
    | HTTP chat/completions
    v
Python Bridge
    |
    | MQTT commands/status/events
    v
StackChan firmware
```

Rules:

- One Hermes controls exactly one StackChan.
- One StackChan belongs to exactly one Hermes.
- Multiple pairs are allowed, but each pair has its own namespace.
- The bridge validates Hermes actions before publishing MQTT commands.
- The firmware validates again and enforces local hardware limits.
- Binary audio is sent over HTTP, not MQTT.

Default topics for pair `desk`:

```text
hermes-stackchan/desk/cmd/display
hermes-stackchan/desk/cmd/face
hermes-stackchan/desk/cmd/move
hermes-stackchan/desk/cmd/motion
hermes-stackchan/desk/cmd/led
hermes-stackchan/desk/cmd/device
hermes-stackchan/desk/cmd/sound
hermes-stackchan/desk/cmd/audio
hermes-stackchan/desk/cmd/system
hermes-stackchan/desk/status
hermes-stackchan/desk/state/device_settings
hermes-stackchan/desk/ack
hermes-stackchan/desk/error
hermes-stackchan/desk/events
```

## Hardware And Services You Need

Minimum working setup:

- StackChan hardware with ESP32-S3, display, microphone, speaker/codec, LEDs, servos, and touch sensor.
- A computer for firmware flashing. macOS is currently used during development.
- ESP-IDF 5.5.x.
- A Raspberry Pi or Linux host for Hermes and the Python bridge.
- MQTT broker, usually Mosquitto.
- Hermes agent reachable by HTTP, usually on the same Raspberry Pi as the bridge.
- Groq API key for fast German STT.
- Internet access for Groq STT and Edge TTS.
- Python 3.11 recommended for the bridge host.

Known working pair from development:

```text
Hermes/bridge host: Raspberry Pi
StackChan IP:       assigned by Wi-Fi
MQTT prefix:        hermes-stackchan/desk
Wakeword:           Computer
TTS voice:          de-DE-KatjaNeural
STT model:          whisper-large-v3-turbo
```

## Repository Layout

```text
bridge/          Python bridge and CLI
firmware/        ESP-IDF firmware for StackChan
config/          Public example pair configuration
capabilities/    Capability file shown to Hermes
personalities/   Personality notes included in Hermes prompt
scripts/         Local helper scripts
systemd/         Linux service example
docs/            Additional setup and workflow notes
tests/           Python bridge tests
assets/          Public assets placeholder
```

Private or generated material is excluded from Git:

- `.env`
- `Ressourcen/`
- `MEGAPLAN_EN.MD`
- `.venv/`
- firmware build outputs
- logs and caches
- captured audio/photos

## 1: Install MQTT Broker On The Raspberry Pi

On the Pi:

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients python3 python3-venv python3-pip rsync
sudo systemctl enable --now mosquitto
```

Quick broker check:

```bash
mosquitto_sub -t 'hermes-stackchan/desk/#' -v
```

Leave that running in one terminal if you want to see all MQTT traffic.

## 2: Install Hermes

Install and start Hermes according to the Hermes documentation for your device.

The bridge expects an OpenAI-compatible endpoint:

```text
http://127.0.0.1:8642/v1/chat/completions
```

Health check on the Pi:

```bash
curl http://127.0.0.1:8642/health
```

If Hermes requires an API key, keep it in `.env` as `H2S_HERMES_API_KEY`. Do not commit it.

## 3: Clone And Configure The Bridge

On the Pi:

```bash
git clone https://github.com/wollux/hermes2stackchan.git
cd hermes2stackchan
cp .env.example .env
```

Edit `.env`:

```env
H2S_PAIR_ID=desk
H2S_HERMES_ID=hermes-desk
H2S_STACKCHAN_ID=stackchan-desk
H2S_CAPABILITIES_FILE=capabilities/bridge-capabilities-desk.md
H2S_PERSONALITY_FILE=personalities/hermes-desk.md

H2S_MQTT_HOST=127.0.0.1
H2S_MQTT_PORT=1883
H2S_MQTT_USERNAME=
H2S_MQTT_PASSWORD=
H2S_MQTT_TLS=false

H2S_BRIDGE_HTTP_HOST=0.0.0.0
H2S_BRIDGE_HTTP_PORT=8788

H2S_STT_PROVIDER=groq
H2S_GROQ_API_KEY=put-your-groq-key-here
H2S_STT_MODEL=whisper-large-v3-turbo
H2S_STT_LANGUAGE=de
H2S_STT_TIMEOUT_S=30
H2S_WAV_ARCHIVE_DIR=/home/wollux/.hermes/stackchan_wavs

H2S_TTS_ENGINE=edge
H2S_EDGE_TTS_VOICE=de-DE-KatjaNeural
H2S_EDGE_TTS_RATE=+8%
H2S_TTS_DIR=/home/wollux/.hermes/stackchan_tts

H2S_HERMES_BASE_URL=http://127.0.0.1:8642
H2S_HERMES_MODEL=default
H2S_HERMES_API_KEY=put-your-hermes-key-here-if-needed
H2S_HERMES_TIMEOUT_S=30

H2S_REMINDER_STORE=~/.hermes/hermes2stackchan/reminders.json
H2S_REMINDER_POLL_S=1
H2S_REMINDER_DISPLAY_MS=9000
```

Install Python package:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

Run tests:

```bash
.venv/bin/python -m pytest -q
```

## 4: Install Bridge As A Service

For the reproducible Linux service install:

```bash
sudo ./scripts/install_bridge_service.sh
sudo nano /opt/hermes2stackchan/.env
sudo systemctl start hermes2stackchan.service
sudo systemctl status hermes2stackchan.service
```

Watch logs:

```bash
journalctl -u hermes2stackchan.service -f
```

The service runs one multithreaded bridge process:

- HTTP audio endpoint.
- Fast touch/recording LED worker.
- Power watcher.
- Persistent reminder worker.
- Idle life animator.

Disable individual workers for debugging:

```bash
h2s-bridge --env /opt/hermes2stackchan/.env run --pair desk --no-life
h2s-bridge --env /opt/hermes2stackchan/.env run --pair desk --no-power
h2s-bridge --env /opt/hermes2stackchan/.env run --pair desk --touch-verbose
```

## 5: Configure And Flash Firmware

On the flashing computer:

```bash
git clone https://github.com/wollux/hermes2stackchan.git
cd hermes2stackchan
cp .env.example .env
```

Set the firmware values in `.env`:

```env
H2S_WIFI_SSID=your-wifi
H2S_WIFI_PASSWORD=your-wifi-password
H2S_MQTT_URI=mqtt://192.168.99.58:1883
H2S_PAIR_ID=desk
H2S_STACKCHAN_ID=stackchan-desk
H2S_BRIDGE_AUDIO_URL=http://192.168.99.58:8788/stackchan/audio
H2S_WAKEWORD_LABEL=Computer
H2S_WAKEWORD_MODEL_HINT=computer
H2S_DISPLAY_BRIGHTNESS=80
H2S_STATUS_INTERVAL_MS=5000
```

`H2S_WAKEWORD_LABEL` is the label shown in MQTT status and events.
`H2S_WAKEWORD_MODEL_HINT` selects the WakeNet model from the ESP-SR model partition. The current checked build contains `wn9_computer_tts`, so the reliable built-in wakeword is still `Computer`. To use `Hermes` as a real wakeword, add or generate a WakeNet model whose name/words match Hermes, then set:

```env
H2S_WAKEWORD_LABEL=Hermes
H2S_WAKEWORD_MODEL_HINT=hermes
```

If no matching model exists, firmware falls back to the first available WakeNet model and logs a warning.

Sync `.env` into the ignored ESP-IDF `sdkconfig` values:

```bash
python3 scripts/apply_firmware_env.py --env .env
```

Build and flash:

```bash
cd firmware
source /path/to/esp-idf/export.sh
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/cu.usbmodem21301 flash monitor
```

Use your actual serial port. Common macOS names look like:

```text
/dev/cu.usbmodem21301
/dev/cu.usbserial-*
```

## 6: Smoke Test MQTT

From the bridge checkout:

```bash
scripts/h2s_bridge.sh watch --pair desk
```

Read status:

```bash
scripts/h2s_bridge.sh read-status --pair desk
scripts/h2s_bridge.sh status-health --pair desk
```

Show display text:

```bash
scripts/h2s_bridge.sh send-display \
  --pair desk \
  --text "Hello from Hermes2StackChan" \
  --wait-ack
```

Set a face:

```bash
scripts/h2s_bridge.sh send-face --pair desk --emotion happy --wait-ack
scripts/h2s_bridge.sh send-face --pair desk --emotion blink --wait-ack
scripts/h2s_bridge.sh send-face --pair desk --emotion deep_breathe --wait-ack
```

Move the head:

```bash
scripts/h2s_bridge.sh send-move --pair desk --direction left --wait-ack
scripts/h2s_bridge.sh send-move --pair desk --direction center --wait-ack
```

Send a smooth path:

```bash
scripts/h2s_bridge.sh send-motion \
  --pair desk \
  --curve spline \
  --points '[{"yaw_pct":-20,"pitch_pct":50,"duration_ms":900,"speed_pct":25},{"yaw_pct":20,"pitch_pct":50,"duration_ms":1200,"speed_pct":25},{"yaw_pct":0,"pitch_pct":45,"duration_ms":900,"speed_pct":18}]' \
  --wait-ack
```

LED, device, and sound tests:

```bash
scripts/h2s_bridge.sh send-led --pair desk --mode party --wait-ack
scripts/h2s_bridge.sh send-device --pair desk --volume-pct 80 --brightness-pct 70 --wait-ack
scripts/h2s_bridge.sh send-device --pair desk --display-sleep --wait-ack
scripts/h2s_bridge.sh send-device --pair desk --display-wake --wait-ack
scripts/h2s_bridge.sh send-sound --pair desk --frequency-hz 880 --duration-ms 140 --wait-ack
```

Retained device settings:

```bash
scripts/h2s_bridge.sh restore-device-settings --pair desk --wait-ack
```

The bridge keeps the latest useful device settings on
`hermes-stackchan/desk/state/device_settings` as a retained MQTT message.
Currently this restores speaker volume and display brightness. The unified
bridge service also watches StackChan status and reapplies these settings after
a reboot or reconnect.

## 7: Test Hermes Integration

Check Hermes:

```bash
scripts/h2s_bridge.sh hermes-health
```

Ask Hermes and publish returned actions:

```bash
scripts/h2s_bridge.sh ask-hermes \
  --pair desk \
  --text "Sag kurz Hallo und lächle." \
  --show-response
```

Expected Hermes JSON shape:

```json
{
  "reply": "Hallo, ich bin bereit.",
  "follow_up_listen": false,
  "actions": [
    {"action": "say", "text": "Hallo, ich bin bereit.", "emotion": "speaking"},
    {"action": "face", "emotion": "happy", "intensity_pct": 70}
  ]
}
```

When Hermes asks a real follow-up question, it should set:

```json
{
  "reply": "Welche Farbe soll ich nehmen?",
  "follow_up_listen": true,
  "actions": [
    {"action": "face", "emotion": "question", "intensity_pct": 70}
  ]
}
```

StackChan will speak the question first and then automatically start a follow-up recording.

Hermes can also schedule reminders:

```json
{
  "reply": "Mache ich. Ich melde mich in zwei Minuten.",
  "follow_up_listen": false,
  "actions": [
    {"action": "reminder", "text": "Bei Wolfgang melden", "delay_s": 120}
  ]
}
```

The bridge stores pending reminders persistently in `H2S_REMINDER_STORE`. When a
reminder is due, the unified bridge wakes StackChan, plays a short tone, shows
the reminder, and sends a `say` command. If the user only says "erinnere mich"
without time or content, Hermes should ask what/when and set
`follow_up_listen: true`.

Reminder CLI:

```bash
scripts/h2s_bridge.sh add-reminder --pair desk --text "Test" --delay-s 120
scripts/h2s_bridge.sh list-reminders --pair desk
scripts/h2s_bridge.sh watch-reminders --pair desk
```

## 8: Test Speech End To End

Start the unified bridge service or run locally:

```bash
scripts/h2s_bridge.sh run --pair desk --touch-verbose
```

Then use StackChan:

1. Say `Computer`.
2. Speak a short German command.
3. Wait for the green recording LED to turn off.
4. StackChan uploads the WAV to the bridge.
5. Bridge transcribes with Groq.
6. Bridge asks Hermes.
7. Bridge generates Edge/Katja TTS.
8. StackChan downloads and plays the WAV.

Useful log lines:

```text
[bridge-http] received ... request_id=...
[bridge-http] transcript after ... via groq: ...
[bridge-http] ... follow_up=True/False reply='...'
voice upload done: status=200 ... "tts_url":"http://..."
tts playback start: http://...
tts playback done: status=200 err=ESP_OK
follow-up recording start after Hermes question
```

## Debugging

Bridge logs on the Pi:

```bash
journalctl -u hermes2stackchan.service -f
```

MQTT traffic:

```bash
mosquitto_sub -h 127.0.0.1 -t 'hermes-stackchan/desk/#' -v
```

Serial monitor:

```bash
cd firmware
source /path/to/esp-idf/export.sh
idf.py -p /dev/cu.usbmodem21301 monitor
```

Combined debug cockpit:

```bash
scripts/debug_cockpit.sh /dev/cu.usbmodem21301
```

Common symptoms:

- No MQTT status: check Wi-Fi, `H2S_MQTT_URI`, broker address, and serial log.
- No speech answer: check `H2S_BRIDGE_AUDIO_URL`, bridge service, `H2S_GROQ_API_KEY`, and Hermes health.
- TTS URL appears but no sound: check speaker init, volume, and serial `tts playback` logs.
- Wakeword false triggers after speech: firmware pauses WakeNet during TTS; reflash if old firmware is still running.
- Reset on touch: current firmware increases `touch_event` task stack; reflash current branch.

## Current GitHub Work Status

Open issues were checked during this README update.

Implemented on branch `feature/issue-6-audio-capture`:

- #6 Wakeword and push-to-talk audio capture.
- #7 STT to Hermes to TTS playback.

Mostly implemented, but still worth keeping open for hardening:

- #8 Safe Hermes hardware command exposure.
- #13 Motion safety, calibration, and expressive profiles.
- #14 Persist device state and expose status to Hermes.

Still open:

- #5 MQTT schema hardening and documentation.
- #9 Camera capture and image-to-Hermes flow.
- #10 Display images received from Hermes.
- #11 Touch quick-menu interaction model.
- #12 Sleep, wake, and power polish.
- #15 CI checks.
- #18 Wakeword research and custom wakeword training.

The PR for the current branch should close #6 and #7 when merged.

## Prototype Lessons

Before this clean v1 repository, a local prototype explored many ideas quickly:

- Hermes on a Raspberry Pi.
- A Python bridge service on the Pi.
- StackChan sending recorded WAV audio.
- Groq Whisper for fast German STT.
- Piper TTS and Edge/Katja TTS.
- Word-by-word display replies.
- Wakeword experiments.
- Push-to-talk from touch.
- LEDs for recording/listening feedback.
- Battery plug/unplug reactions.
- Sleep/wake display animations.
- Servo movement commands from speech.
- Hermes returning hardware commands like volume, brightness, movement, sleep, and reboot.
- Early image/camera and radio experiments.

What carried forward:

- Audio quality from StackChan is good enough.
- Cloud STT is much faster than first local attempts.
- TTS must be fast and must not retrigger WakeNet.
- Radio playback should stay out of v1.0.
- The face should be the default UI, not debug text.
- Hermes should always receive the current local StackChan state.
- Hardware control needs strict schema validation and firmware-side limits.

## Development Workflow

Use GitHub as the source of truth:

1. Pick or create an Issue.
2. Work on a feature branch.
3. Keep tests and docs updated.
4. Push the branch.
5. Open or update a PR that links the Issue.

Useful local checks:

```bash
python3 -m pytest -q
cd firmware && source /path/to/esp-idf/export.sh && idf.py build
git status --short --branch
```

Do not commit:

- API keys.
- Wi-Fi credentials.
- `.env`.
- captured WAVs or photos.
- firmware build output.
- internal planning material.

## License

No license has been selected yet.
