# hermes2stackchan

Hermes2StackChan is an MQTT-based interface between one Hermes agent and one StackChan companion device.

The current direction is simple on purpose: StackChan should be a reliable, hardware-safe, MQTT-controlled body. The bridge owns the protocol, validation, state handling, and Hermes integration. Hermes owns the intelligence.

## Current Status

This repository is a new V1 codebase. It is not a direct continuation of the earlier local prototype, but it keeps the useful lessons from that prototype.

What already works in this repo:

- ESP32-S3 firmware for StackChan.
- Wi-Fi and MQTT connection from firmware.
- Pair namespace: `hermes-stackchan/<pair-id>/...`.
- Default pair: `desk`.
- Bridge CLI for MQTT commands.
- Retained device status.
- ACK/Error replies for commands.
- Display text commands.
- Face rendering with eyes, pupils, mouth, blink, breathing, small sleep `Z`s, and idle life animations.
- Head movement through safe MQTT commands.
- Smooth motion paths with waypoint payloads.
- Speaker tone test commands.
- LED/neon mode commands.
- Volume, brightness, display sleep/wake device commands.
- Battery/power status in retained MQTT state.
- Power watcher that reacts to plug/unplug without taking over LEDs or sound.
- Hermes HTTP adapter that can ask Hermes and dispatch returned JSON actions to StackChan.
- GitHub-first workflow with Issues, labels, milestone, feature branches, and PRs.

Active/nearby work:

- PR #4 improves idle life animation: more blinking, larger desk-scan yaw sweeps, and pupil tracking during the sweep.
- Issue #3 tracks the full v1.0 backlog.

## Architecture

The intended architecture is one-to-one:

```text
Hermes Agent
    |
    v
Python Bridge
    |
    v
MQTT namespace for one pair
    |
    v
StackChan firmware
```

Rules:

- One Hermes controls exactly one StackChan.
- One StackChan belongs to exactly one Hermes.
- Multiple pairs are allowed, but each pair has its own namespace.
- The bridge validates commands before StackChan receives them.
- Firmware still enforces hardware safety.

Example namespaces:

```text
hermes-stackchan/desk/cmd/display
hermes-stackchan/desk/cmd/face
hermes-stackchan/desk/cmd/move
hermes-stackchan/desk/cmd/motion
hermes-stackchan/desk/cmd/device
hermes-stackchan/desk/status
hermes-stackchan/desk/ack
hermes-stackchan/desk/error
```

## Repository Layout

```text
bridge/          Python MQTT and Hermes bridge
firmware/        ESP-IDF firmware for StackChan
config/          Public example pair configuration
capabilities/    Capabilities shown to Hermes
personalities/   Pair personality notes
scripts/         Local helper scripts
docs/            Setup and workflow documentation
tests/           Bridge unit tests
```

Private material is intentionally excluded from Git:

- `.env`
- `Ressourcen/`
- `MEGAPLAN_EN.MD`
- firmware build outputs
- logs and caches

## Setup

The command-level smoke-test guide lives in [docs/mqtt-display-smoke-test.md](docs/mqtt-display-smoke-test.md).

Create local configuration:

```sh
cp .env.example .env
```

Edit `.env` with your local values:

```sh
H2S_MQTT_HOST=192.168.1.10
H2S_MQTT_PORT=1883
H2S_MQTT_USERNAME=
H2S_MQTT_PASSWORD=
H2S_MQTT_TLS=false

H2S_HERMES_BASE_URL=http://192.168.1.10:8642
H2S_HERMES_MODEL=default
H2S_HERMES_API_KEY=
H2S_HERMES_TIMEOUT_S=30

H2S_WIFI_SSID=
H2S_WIFI_PASSWORD=
H2S_MQTT_URI=mqtt://192.168.1.10:1883
H2S_PAIR_ID=desk
H2S_STACKCHAN_ID=stackchan-desk
```

Install the bridge:

```sh
python3 -m pip install -e .
```

Run tests:

```sh
python3 -m pytest -q
```

## Firmware

Sync `.env` values into the ignored ESP-IDF config:

```sh
python3 scripts/apply_firmware_env.py --env .env
```

Build and flash:

```sh
cd firmware
source /path/to/esp-idf/export.sh
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/cu.usbmodem21301 flash monitor
```

The firmware will show a visible setup/error screen if Wi-Fi or MQTT values were not configured.

## Bridge Commands

Watch all MQTT traffic for the `desk` pair:

```sh
scripts/h2s_bridge.sh watch --pair desk
```

Read and validate status:

```sh
scripts/h2s_bridge.sh read-status --pair desk
scripts/h2s_bridge.sh status-health --pair desk
```

Display text:

```sh
scripts/h2s_bridge.sh send-display \
  --pair desk \
  --text "Hello from Hermes2StackChan" \
  --wait-ack
```

Set a face:

```sh
scripts/h2s_bridge.sh send-face --pair desk --emotion happy --wait-ack
scripts/h2s_bridge.sh send-face --pair desk --emotion blink --wait-ack
scripts/h2s_bridge.sh send-face --pair desk --emotion deep_breathe --wait-ack
```

Move the head:

```sh
scripts/h2s_bridge.sh send-move --pair desk --direction left --wait-ack
scripts/h2s_bridge.sh send-move --pair desk --direction center --wait-ack
```

Send a smooth motion path:

```sh
scripts/h2s_bridge.sh send-motion \
  --pair desk \
  --curve spline \
  --points '[{"yaw_pct":-20,"pitch_pct":50,"duration_ms":900,"speed_pct":25},{"yaw_pct":20,"pitch_pct":50,"duration_ms":1200,"speed_pct":25},{"yaw_pct":0,"pitch_pct":45,"duration_ms":900,"speed_pct":18}]' \
  --wait-ack
```

Test LEDs, device settings, and sound:

```sh
scripts/h2s_bridge.sh send-led --pair desk --mode party --wait-ack
scripts/h2s_bridge.sh send-device --pair desk --volume-pct 80 --brightness-pct 70 --wait-ack
scripts/h2s_bridge.sh send-sound --pair desk --frequency-hz 880 --duration-ms 140 --wait-ack
```

Ask Hermes and dispatch returned actions:

```sh
scripts/h2s_bridge.sh hermes-health
scripts/h2s_bridge.sh ask-hermes \
  --pair desk \
  --text "Sag kurz Hallo und lächle." \
  --show-response
```

## Background Helpers

Start the power watcher:

```sh
scripts/start_power_watcher.sh desk
scripts/status_power_watcher.sh desk
scripts/stop_power_watcher.sh desk
```

Start the idle life animator:

```sh
scripts/start_life_animator.sh desk
scripts/status_life_animator.sh desk
scripts/stop_life_animator.sh desk
```

The life animator only runs while StackChan is idle on the face screen. It avoids LED and sound commands. It sends face impulses and occasional bounded head movements. Idle action now includes horizontal sweeps, cautious up/down scans, diagonal room glances, matching pupil direction, blinking, breathing, mouth impulses, and tiny micro-sleep moments.

## What Worked In The Prototype

Before this clean V1 repository, a local prototype explored a lot of behavior quickly. The important working or partially working pieces were:

- Hermes reachable on a Raspberry Pi over HTTP.
- A Python bridge running on the Hermes Pi as a service.
- StackChan sending recorded WAV audio to the bridge.
- Speech recognition with Groq Whisper, much faster than local-only transcription.
- Local Piper TTS was tested.
- Edge/Katja TTS was tested and sounded better for German.
- Text replies were displayed word-by-word on the small display.
- Wakeword experiments, including existing wakewords like `Computer`.
- Push-to-talk experiments from touch.
- LEDs used for recording/listening feedback.
- Battery plug/unplug detection and reactions.
- Sleep/wake display animations.
- Servo movement commands from speech.
- Hermes returning hardware commands like volume, brightness, movement, sleep, reboot.
- Image/camera and radio experiments were explored.

What the prototype taught us:

- Audio quality from StackChan was good enough.
- Cloud STT was much faster and more useful than the first local attempts.
- TTS must be asynchronous or fast enough to avoid long pauses.
- Radio playback interfered with the wakeword/audio loop and should not be part of v1.0.
- Servo power, task stack size, and status publishing can cause resets if treated casually.
- The face/UI should be the default, not debug text.
- Hermes should know the complete local device state before answering.
- Hardware control needs a strict command contract and safety layer.

Those lessons are now being rebuilt cleanly through MQTT and GitHub issues.

## v1.0 Plan

The v1.0 backlog lives in GitHub:

- #3 Overall v1.0 backlog
- #5 MQTT topic contract and JSON schemas
- #6 Wakeword and push-to-talk audio capture
- #7 STT to Hermes to TTS playback
- #8 Safe Hermes hardware command control
- #9 Camera capture and image-to-Hermes flow
- #10 Display images received from Hermes on StackChan
- #11 Touch and quick-menu interaction model
- #12 Sleep, wake, and power reactions
- #13 Motion safety, calibration, expressive motion profiles
- #14 Persist device state and expose status to Hermes
- #15 CI checks for Bridge and firmware build
- #16 Installation, services, and operations docs

Current #6 slice status:

- ES7210 microphone initializes in firmware.
- Touch starts recording immediately.
- Recording stops from local voice activity detection: after speech plus about 500 ms silence, or after a no-voice timeout.
- The face remains the default display while recording, with a small waveform overlay rendered below it from the face framebuffer.
- Retained MQTT status exposes `audio.voice_active`, `audio.voice_level_pct`, `audio.voice_avg_level`, and `audio.voice_peak_level`.

Suggested order:

1. Harden MQTT schemas.
2. Persist and expose device state.
3. Expose safe hardware commands to Hermes.
4. Improve motion safety and expressive profiles.
5. Stabilize sleep, wake, and power behavior.
6. Add wakeword and push-to-talk recording.
7. Add full speech pipeline with STT, Hermes, and TTS playback.
8. Add touch quick-menu.
9. Add camera capture and image-to-Hermes flow.
10. Add image display from Hermes.
11. Add CI.
12. Finish setup and operations docs.

## GitHub Workflow

This repo should be worked through GitHub:

1. Create or pick an Issue.
2. Create a branch like `feature/issue-5-mqtt-schema`.
3. Implement the slice.
4. Run tests.
5. Push the branch.
6. Open a PR that links the Issue.
7. Merge only after review.

Current example:

- Issue #2 tracks idle animation tuning.
- PR #4 implements the current animation improvements.

## Development Notes

Useful checks:

```sh
python3 -m pytest -q
scripts/h2s_bridge.sh status-health --pair desk
git status --short --branch
```

Do not commit:

- real API keys
- Wi-Fi credentials
- `.env`
- captured audio/photos
- firmware build outputs
- internal planning material in `Ressourcen/`

## License

No license has been selected yet.
