# Bridge Capabilities - hermes-desk / stackchan-desk

## V1.0 MQTT Hardware Slice

This public slice supports direct MQTT hardware control. Hermes may request actions, but the firmware and bridge validate the pair namespace and the firmware keeps local soft limits.

## Pair

- Hermes ID: `hermes-desk`
- StackChan ID: `stackchan-desk`
- Pair ID: `desk`
- MQTT prefix: `hermes-stackchan/desk`

## Actions

### display

Publishes to `hermes-stackchan/desk/cmd/display`.

Payload:

```json
{
  "schema_version": "1.0",
  "mode": "text",
  "text": "Hello from Hermes2StackChan",
  "duration_ms": 5000,
  "request_id": "test-001"
}
```

### face

Publishes to `hermes-stackchan/desk/cmd/face`.

Supported emotions: `neutral`, `happy`, `sad`, `angry`, `surprised`, `question`, `wink`, `blink`, `look_left`, `look_right`, `look_up`, `look_down`, `breathe`, `sleep`, `speaking`, `error`, `battery`, `charging`, `battery_low`.

```json
{
  "schema_version": "1.0",
  "emotion": "happy",
  "intensity_pct": 70,
  "request_id": "face-001"
}
```

### move

Publishes to `hermes-stackchan/desk/cmd/move`.

Safe firmware limits are enforced locally.

```json
{
  "schema_version": "1.0",
  "direction": "center",
  "request_id": "move-001"
}
```

Allowed directions: `left`, `right`, `up`, `down`, `center`, `straight`.

Also supported: `yaw_delta`, `pitch_delta`, `yaw_target_pct`, `pitch_target_pct`.
Use `yaw_target_pct` as -100..100. Use `pitch_target_pct` as 0..100, matching the original StackChan pitch range.

### motion

Publishes to `hermes-stackchan/desk/cmd/motion`.

This sends a computed motion path to StackChan. Hermes or the bridge must calculate the waypoints. The firmware does not store named choreographies; it only validates, clamps to safe hardware limits, and interpolates between received waypoints.

Rules:

- `points` is required.
- Maximum `points`: 48.
- `yaw_pct`: -100..100, where 0 is the center/start position.
- `pitch_pct`: 0..100, matching the original StackChan pitch range. 0 is down, 45 is the normal idle height, 100 is up.
- `speed_pct`: 1..100. Used when a point has no `duration_ms`.
- `duration_ms`: 0 or omitted means derive timing from `speed_pct`; otherwise 40..4000 per segment.
- `hold_ms`: optional pause after a point, 0..4000.
- `curve`: `linear` or `spline`. Use `spline` for round/organic paths and `linear` for hard corners.
- Firmware also enforces servo soft limits and a safe maximum raw servo step rate.

Path command with per-point speed:

```json
{
  "schema_version": "1.0",
  "curve": "spline",
  "speed_pct": 35,
  "points": [
    {"yaw_pct": 0, "pitch_pct": 45},
    {"yaw_pct": 20, "pitch_pct": 60, "speed_pct": 35},
    {"yaw_pct": 0, "pitch_pct": 75, "speed_pct": 35},
    {"yaw_pct": -20, "pitch_pct": 60, "speed_pct": 35},
    {"yaw_pct": 0, "pitch_pct": 45, "speed_pct": 35}
  ],
  "request_id": "motion-001"
}
```

Path command with explicit segment durations:

```json
{
  "schema_version": "1.0",
  "curve": "linear",
  "points": [
    [0, 0, 120, 40],
    [0, 30, 260, 30],
    [0, -22, 260, 30],
    [0, 0, 260, 30, 120]
  ],
  "request_id": "path-001"
}
```

### led

Publishes to `hermes-stackchan/desk/cmd/led`.

Modes: `off`, `solid`, `rainbow`, `scanner`, `blink`, `breathe`, `sparkle`, `party`.

```json
{
  "schema_version": "1.0",
  "mode": "solid",
  "r": 0,
  "g": 80,
  "b": 255,
  "request_id": "led-001"
}
```

### device

Publishes to `hermes-stackchan/desk/cmd/device`.

Supported fields: `volume_pct`, `brightness_pct`, `display_sleep`, `display_wake`.

### sound

Publishes to `hermes-stackchan/desk/cmd/sound`.

Currently supports simple local tones through the speaker.

### audio

Publishes to `hermes-stackchan/desk/cmd/audio`.

This is the V1.0 control contract for wakeword and push-to-talk state. StackChan can detect the built-in WakeNet wakeword `Computer` locally and can also start recording from touch/push-to-talk. Recorded WAV audio is uploaded to the bridge over HTTP, not MQTT.

Supported actions: `set_wakeword`, `simulate_wakeword`, `start_recording`, `stop_recording`.

Enable wakeword listening:

```json
{
  "schema_version": "1.0",
  "action": "set_wakeword",
  "wakeword": "Computer",
  "enabled": true,
  "request_id": "audio-001"
}
```

Push-to-talk start and stop:

```json
{
  "schema_version": "1.0",
  "action": "start_recording",
  "source": "push_to_talk",
  "min_ms": 5000,
  "max_ms": 20000,
  "request_id": "ptt-001"
}
```

```json
{
  "schema_version": "1.0",
  "action": "stop_recording",
  "source": "push_to_talk",
  "reason": "touch_release",
  "request_id": "ptt-002"
}
```

Wakeword and touch recording use local voice activity detection and stop after silence or the configured maximum duration.

### speech conversation

StackChan sends recorded WAV audio to the bridge endpoint:

```text
POST /stackchan/audio
Content-Type: audio/wav
X-H2S-Pair-Id: desk
X-H2S-Request-Id: optional-request-id
```

Bridge processing:

1. Transcribe the WAV with the configured STT provider.
2. Read retained StackChan status from MQTT.
3. Send the transcript, status, this capabilities file, and personality notes to Hermes.
4. Validate Hermes JSON actions.
5. Publish valid actions to `hermes-stackchan/desk/cmd/*`.
6. Generate TTS for the final reply and return `tts_url` to StackChan.

Hermes must return JSON only:

```json
{
  "reply": "Mache ich.",
  "actions": [
    {"action": "say", "text": "Mache ich.", "emotion": "speaking"},
    {"action": "face", "emotion": "happy", "intensity_pct": 65},
    {"action": "move", "pitch_target_pct": 55}
  ]
}
```

### system

Publishes to `hermes-stackchan/desk/cmd/system`.

Supported actions: `ping`, `status`, `display_sleep`, `display_wake`, `reboot`.

## Status

StackChan publishes retained status to `hermes-stackchan/desk/status`, including battery, charge direction, volume, brightness, display sleep state, head position, LED mode, speaker readiness, UI mode, face emotion, audio-control state, temperatures, `firmware`, and `firmware_version`.

Battery fields:

```json
{
  "battery_pct": 82,
  "charging": true,
  "battery_charging": true,
  "battery_discharging": false,
  "battery_charging_done": false,
  "battery_known": true,
  "usb_power": true,
  "external_power": true,
  "battery_current_direction": 1
}
```

The bridge command `watch-power` watches `external_power`/`usb_power` in these retained status updates and reacts to plug/unplug transitions with a battery percentage/charge overlay plus immediate head motion. After about five seconds, plugging in triggers a happy face; unplugging triggers a neutral face. It must not change LEDs or sound for power changes. `battery_charging` only means active charging; a full battery can have `external_power: true` and `battery_charging: false`.

Temperature fields:

```json
{
  "temperature": {
    "soc_c": 42,
    "servo_yaw_c": 31,
    "servo_pitch_c": 32
  }
}
```

Temperature value `-1` means unavailable. Servo temperatures are only known while the servo bus is powered and answering.

Audio-control fields:

```json
{
  "wakeword_enabled": true,
  "recording": false,
  "audio": {
    "input_ready": false,
    "wakeword_enabled": true,
    "wakeword": "Computer",
    "recording": false,
    "recording_source": "none",
    "recording_started_ms": 0,
    "recording_min_ms": 5000,
    "recording_silence_timeout_ms": 1000,
    "recording_max_ms": 15000
  }
}
```

StackChan also publishes realtime audio events to `hermes-stackchan/desk/events`, for example `wakeword_detected`, `recording_started`, and `recording_stopped`.

Hermes HTTP responses consumed by the bridge must be JSON:

```json
{
  "reply": "Kurz und freundlich antworten.",
  "actions": [
    {"action": "say", "text": "Kurz und freundlich antworten.", "emotion": "speaking"},
    {"action": "face", "emotion": "happy", "intensity_pct": 70}
  ]
}
```

## Forbidden In This Slice

- Camera commands
- Multi-device routing
- Radio playback
- Binary audio over MQTT
- Direct control of another pair namespace
