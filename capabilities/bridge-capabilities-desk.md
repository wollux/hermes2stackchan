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

Supported emotions: `neutral`, `happy`, `sad`, `angry`, `surprised`, `question`, `wink`, `sleep`, `speaking`, `error`.

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

### motion

Publishes to `hermes-stackchan/desk/cmd/motion`.

This sends a computed motion path to StackChan. Hermes or the bridge must calculate the waypoints. The firmware does not store named choreographies; it only validates, clamps to safe hardware limits, and interpolates between received waypoints.

Rules:

- `points` is required.
- Maximum `points`: 48.
- `yaw_pct`: -100..100, where 0 is the center/start position.
- `pitch_pct`: -100..100, where 0 is the center/start position.
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
    {"yaw_pct": 0, "pitch_pct": 0},
    {"yaw_pct": 20, "pitch_pct": 15, "speed_pct": 35},
    {"yaw_pct": 0, "pitch_pct": 30, "speed_pct": 35},
    {"yaw_pct": -20, "pitch_pct": 15, "speed_pct": 35},
    {"yaw_pct": 0, "pitch_pct": 0, "speed_pct": 35}
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

### system

Publishes to `hermes-stackchan/desk/cmd/system`.

Supported actions: `ping`, `status`, `display_sleep`, `display_wake`, `reboot`.

## Status

StackChan publishes retained status to `hermes-stackchan/desk/status`, including volume, brightness, display sleep state, head position, LED mode, speaker readiness, face emotion, and firmware version.

## Forbidden In This Slice

- Audio capture
- Wake word handling
- Camera commands
- Multi-device routing
- Cloud TTS playback
