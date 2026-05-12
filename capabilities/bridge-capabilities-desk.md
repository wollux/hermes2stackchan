# Bridge Capabilities - hermes-desk / stackchan-desk

## V1.0 MQTT Hardware Slice

This public slice supports direct MQTT hardware control. Hermes may request actions, but the firmware and bridge validate the pair namespace and the firmware keeps local soft limits.

## Pair

- Hermes ID: `hermes-desk`
- StackChan ID: `stackchan-desk`
- Pair ID: `desk`
- MQTT prefix: `hermes-stackchan/desk`

## Actions

### local spoken shortcuts

Before Hermes is called, the bridge handles simple one-step spoken commands
locally after STT. These do not consume a Hermes turn and log `hermes=0ms`.

Local shortcuts include absolute and simple relative hardware/status requests:

- `Helligkeit 80 Prozent`, `mach heller`, `mach dunkler`
- `Lautstaerke 55 Prozent`, `mach lauter`, `mach leiser`
- `Display aus`, `Bildschirm an`, `geh schlafen`, `wach auf`
- `Akku`, `Temperatur`, `Sensoren`, `Naehe`, `Seite`, `Bewegung`

Combined tasks, questions needing reasoning, and anything with multiple intents
still goes to Hermes.

### response contract

For normal answers, direct messages, reminders, notifications, and command
confirmations, put the spoken text in the top-level `reply` field.

Do not use `say` for normal replies. The bridge handles `reply` by showing text
and generating TTS when TTS is enabled. Use `display` only for extra visible text
that should appear in addition to the spoken `reply`.

For messages that originate outside StackChan, for example Telegram asking Hermes
to notify the device, call the bridge notify endpoint instead of publishing MQTT
directly:

```http
POST http://127.0.0.1:8788/stackchan/notify
Content-Type: application/json
```

```json
{
  "reply": "Ich lese diese Nachricht auf StackChan vor.",
  "actions": [
    {"action": "face", "emotion": "happy", "intensity_pct": 70}
  ]
}
```

The notify endpoint creates TTS, publishes `cmd/display`, publishes `cmd/audio`,
and returns a `tts_url`. Do not use legacy `say` for proactive speech; `say` is
treated as display-only compatibility.

### images and camera

Hermes can show images on StackChan through the bridge endpoint. Send normal image
URLs, data URLs, or base64 JSON to the bridge; the bridge converts the image to
the firmware display format and publishes a safe `display_image` command.

```http
POST http://127.0.0.1:8788/stackchan/display-image
Content-Type: application/json
```

```json
{
  "image_url": "https://example.com/image.jpg",
  "caption": "Kamera",
  "duration_ms": 9000
}
```

If the user asks for an image from the internet and does not provide a URL, use
the bridge image search action. The bridge searches Openverse, prefers results
close to StackChan's 4:3 display ratio, converts the chosen result to a 320x240
JPEG preview, and sends it to StackChan. If a long search phrase returns no
result, the bridge automatically tries shorter variants and can fall back to
Wikimedia Commons.

Hermes action:

```json
{
  "action": "image_search",
  "query": "polar lights over iceland",
  "caption": "Polarlicht",
  "duration_ms": 9000
}
```

Direct HTTP endpoint:

```http
POST http://127.0.0.1:8788/stackchan/search-image
Content-Type: application/json
```

```json
{
  "query": "polar lights over iceland",
  "caption": "Polarlicht",
  "duration_ms": 9000
}
```

StackChan camera uploads use:

```http
POST http://127.0.0.1:8788/stackchan/photo
Content-Type: image/jpeg
```

The bridge sends the uploaded photo to Hermes vision/chat, then returns Hermes'
answer through the same display/TTS/audio path as speech. Firmware can also
capture directly from the onboard camera by sending a system command:

```json
{
  "action": "system",
  "system_action": "take_photo",
  "prompt": "Beschreibe kurz auf Deutsch, was du siehst.",
  "request_id": "photo-001"
}
```

Only request a photo when status reports `camera_available:true`.

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

Image display payload:

```json
{
  "schema_version": "1.0",
  "mode": "image",
  "url": "http://127.0.0.1:8788/stackchan/images/example.jpg",
  "width": 320,
  "height": 240,
  "format": "jpeg",
  "duration_ms": 9000,
  "request_id": "image-001"
}
```

### face

Publishes to `hermes-stackchan/desk/cmd/face`.

Supported emotions include: `neutral`, `happy`, `sad`, `angry`, `surprised`, `tired`, `annoyed`, `confused`, `scared`, `love`, `dead`, `glitch`, `super_happy`, `friendly`, `mischievous`, `smug`, `proud`, `shy`, `skeptical`, `offended`, `panic`, `dramatic`, `evil_grin`, `sleepy`, `bored`, `thinking`, `listening`, `speaking`, `charging`, `battery`, `battery_low`, `error`, `face_down`, `help`, `thankful`, plus transients such as `blink`, `wink`, `wink_left`, `wink_right`, `glance_left`, `glance_right`, `glance_up`, `glance_down`, `breathe`, `deep_breathe`, `micro_sleep`, `surprise_pop`, and `happy_squint`.

Recommended usage: use `friendly`, `happy`, `thinking`, `listening`, or `speaking` for normal conversation; use `mischievous`, `smug`, or `evil_grin` for humor; reserve `help`, `panic`, `face_down`, and `error` for real alarm/error situations.

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

Hermes must return JSON only. Put the answer in `reply`; do not use `say` for
the spoken answer:

```json
{
  "reply": "Mache ich.",
  "follow_up_listen": false,
  "actions": [
    {"action": "face", "emotion": "happy", "intensity_pct": 65},
    {"action": "move", "pitch_target_pct": 55}
  ]
}
```

Use `follow_up_listen: true` only when the reply is a real question and Hermes expects the user to answer immediately. StackChan will play the TTS answer first and then start a short follow-up recording.

### reminder / notify

Schedules a persistent reminder on the bridge host. Use this when the user says
things like "erinnere mich in zwei Minuten" or "benachrichtige mich morgen".
The bridge stores the reminder outside git and later wakes StackChan via MQTT.
When it fires, the bridge generates TTS, sets a fitting speaking face, shows the
reminder on the display, and sends StackChan a TTS URL to play.

Parameters:

- `text`: required reminder text.
- `delay_s`: seconds from now, or
- `due_at`: ISO timestamp with timezone.

Example:

```json
{
  "action": "reminder",
  "text": "Wasser trinken",
  "delay_s": 120
}
```

When the user only says "erinnere mich" without time or content, do not create
a reminder. Ask what and when, set `follow_up_listen: true`, and wait for the
answer.

### system

Publishes to `hermes-stackchan/desk/cmd/system`.

Supported actions: `ping`, `status`, `display_sleep`, `display_wake`, `reboot`, `shutdown`, `power_off`.

Use `display_sleep` when the user wants StackChan to sleep or turn only the
screen off. Use `display_wake` for wake/display-on commands. Use `shutdown`
only for explicit power-off/runterfahren/abschalten commands; it asks the
AXP2101 PMIC to turn StackChan off after any voice reply has finished.

## Status

StackChan publishes retained status to `hermes-stackchan/desk/status`, including battery, charge direction, volume, brightness, display sleep state, head position, LED mode, speaker readiness, UI mode, face emotion, audio-control state, interaction state, BMI270 IMU motion, LTR553 proximity/ambient light, temperatures, `firmware`, and `firmware_version`.

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

The bridge command `watch-sensors` watches BMI270 and LTR553 fields in retained
status plus `interaction` events. It is noise-filtered and only reacts to stable
signals: shake triggers a short surprise face, lying on the side triggers a
help/panic face, face-down triggers a tantrum face, and proximity lowers the head slightly until the object moves
away. Confirmed sensor interaction wakes a sleeping display. It must not change
LEDs or sound.

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
    "wakenet_model": "wn9_computer_tts",
    "wakenet_words": "Computer",
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
