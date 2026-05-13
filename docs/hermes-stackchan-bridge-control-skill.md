---
name: stackchan-bridge-control
description: "Use when controlling, testing, or extending Wollux' StackChan through the Hermes2StackChan MQTT bridge: proactive speech, Telegram-to-StackChan notifications, TTS, wake/audio flows, MQTT hardware commands, state checks, reminders, and safe debugging."
version: 1.1.0
author: Hermes2StackChan
license: MIT
metadata:
  hermes:
    tags: [stackchan, hermes2stackchan, mqtt, tts, audio, telegram, raspberry-pi, smart-home]
    related_skills: [home-assistant-core-pi, alexa-cli, systematic-debugging]
---

# StackChan Bridge Control

## Critical Rule For Telegram And Proactive Speech

If Wollux asks from Telegram or any non-StackChan channel to send, say, read,
announce, brief, notify, or remind something on StackChan, you MUST call:

`POST http://127.0.0.1:8788/stackchan/notify`

with JSON containing `reply`. This is the only proactive path that creates a TTS
WAV and pushes display plus audio to the physical StackChan.

Never use `send-say`, `cmd/say`, MQTT display-only commands, `/stackchan/events`,
or a plain Telegram text answer as the primary action for proactive StackChan
speech. After the notify call succeeds, answer Telegram only briefly, for example
`Erledigt.`

## Current System

The active implementation is the newer `hermes2stackchan` MQTT bridge, not the old
legacy HTTP-only bridge.

Known Pi paths:

- Project: `/home/wollux/hermes2stackchan`
- Service: `hermes2stackchan.service`
- Service command: `/home/wollux/hermes2stackchan/.venv/bin/python -u -m bridge.hermes2stackchan_bridge --env .env run --pair desk --host 0.0.0.0 --port 8788 --touch-verbose`
- Bridge log: `/home/wollux/.hermes/logs/hermes2stackchan.log`
- Bridge health: `http://127.0.0.1:8788/health`
- Extended health: `http://127.0.0.1:8788/healthz`
- StackChan IP normally seen in logs: `192.168.99.131`
- MQTT namespace: `hermes-stackchan/desk/#`
- Hermes Gateway service: `hermes-gateway.service`

If `curl -fsS http://127.0.0.1:8788/health` returns
`{"ok":true,"service":"hermes2stackchan-bridge","pair_id":"desk"}`, use this
skill's MQTT bridge instructions.

## When To Use

Use this skill for:

- "Sag X auf StackChan"
- "Schick mir ueber StackChan ..."
- "Lies mir ueber StackChan ..."
- "Zeig dieses Bild auf StackChan ..."
- "Mach ein Foto mit StackChan ..." when camera firmware is available
- "Benachrichtige mich auf StackChan ..."
- StackChan reminders, direct notifications, status checks, TTS tests, wake/audio
  debugging, MQTT hardware control, face/motion/display/audio/LED/device checks.

Do not use this skill for Alexa/Echo playback. Use the Alexa/Home Assistant skills
for those.

## First Checks

Run these before claiming that StackChan did or did not do something:

```bash
curl -fsS http://127.0.0.1:8788/health
curl -fsS http://127.0.0.1:8788/healthz
tail -n 80 /home/wollux/.hermes/logs/hermes2stackchan.log
systemctl --user status hermes2stackchan.service --no-pager
```

Do not print `.env`, API keys, tokens, passwords, or service environments.

## Proactive Speech Endpoint

For Telegram, reminders, and any direct Hermes-to-StackChan message:

```bash
curl -fsS -X POST http://127.0.0.1:8788/stackchan/notify \
  -H 'Content-Type: application/json' \
  -d '{"reply":"Hallo Wolfgang, ich lese das jetzt auf StackChan vor.","actions":[{"action":"face","emotion":"happy","intensity_pct":70}]}'
```

Expected success:

- HTTP JSON contains `"ok": true`
- JSON contains a `tts_url`
- Bridge log contains `POST /stackchan/notify`
- Bridge log contains `cmd/display`
- Bridge log contains `cmd/audio`
- Bridge log contains `GET /stackchan/tts/...wav` from StackChan

This is the tested working path for Telegram-to-StackChan voice.

## Image Display Endpoint

For images coming from Hermes, Telegram, or another channel, do not publish binary
data over MQTT. Call the bridge and let it convert the image for StackChan:

```bash
curl -fsS -X POST http://127.0.0.1:8788/stackchan/display-image \
  -H 'Content-Type: application/json' \
  -d '{"image_url":"https://example.com/picture.jpg","caption":"Bild","duration_ms":9000}'
```

Accepted JSON fields:

- `image_url` or `url`
- `data_url`
- `image_base64` plus optional `content_type`
- `caption`
- `duration_ms`

Expected success:

- HTTP JSON contains `"ok": true`
- JSON contains `image.url` under `/stackchan/images/...jpg`
- Bridge log contains `POST /stackchan/display-image`
- Bridge publishes `cmd/display` with `mode:"image"`
- StackChan fetches `/stackchan/images/...jpg`, decodes JPEG locally, and shows the image.

## Internet Image Search Endpoint

When the user asks for an image from the internet and gives no direct URL, call
the bridge search endpoint or emit an `image_search` action. The bridge searches
Openverse, prefers images close to StackChan's 4:3 display ratio, converts the
chosen image to a 320x240 JPEG, and sends it to StackChan. Long or decorated
queries are simplified automatically, and Wikimedia Commons is used as a
fallback source when Openverse has no useful result.

Hermes action:

```json
{
  "action": "image_search",
  "query": "polar lights over iceland",
  "caption": "Polarlicht",
  "duration_ms": 9000
}
```

HTTP:

```bash
curl -fsS -X POST http://127.0.0.1:8788/stackchan/search-image \
  -H 'Content-Type: application/json' \
  -d '{"query":"polar lights over iceland","caption":"Polarlicht","duration_ms":9000}'
```

StackChan display payload after conversion:

```json
{
  "mode": "image",
  "format": "jpeg",
  "width": 320,
  "height": 240
}
```

## Camera Photo Endpoint

The bridge accepts direct camera uploads:

```bash
curl -fsS -X POST 'http://127.0.0.1:8788/stackchan/photo?prompt=Was%20siehst%20du%3F' \
  -H 'Content-Type: image/jpeg' \
  --data-binary @photo.jpg
```

The bridge sends the image to Hermes vision/chat, creates TTS for the reply, and
publishes display/audio actions back to StackChan.

For the onboard StackChan camera, use the MQTT system action instead of uploading
manually:

```json
{
  "action": "system",
  "system_action": "take_photo",
  "prompt": "Beschreibe kurz auf Deutsch, was du siehst.",
  "request_id": "photo-001"
}
```

Firmware captures QVGA RGB565, posts it to `/stackchan/photo`, the bridge
converts it for Hermes vision and also sends a short preview to the display.
Only ask for this when the retained status says `camera_available:true`.

## Why Text-Only Happens

If Wollux says "it only came as Telegram text", check:

1. Was there a fresh `POST /stackchan/notify` in
   `/home/wollux/.hermes/logs/hermes2stackchan.log`?
2. If not, Hermes used the wrong path. Use the notify endpoint manually or update
   this skill.
3. Did an old Telegram session continue with old examples? Delete that session and
   restart the Hermes gateway.
4. Did the notify endpoint return `cmd/audio`, but StackChan did not fetch the WAV?
   Then debug firmware/audio playback, not Hermes.

Important observed pitfall: old sessions can keep wrong behavior in context even
after this skill was changed. For a stuck Telegram channel, inspect sessions:

```bash
/home/wollux/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main sessions list
```

Delete only the specific stale Telegram session after confirming it is the active
bad one:

```bash
/home/wollux/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main sessions delete SESSION_ID --yes
/home/wollux/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway restart
```

## StackChan Audio Request Flow

When StackChan itself records audio:

1. Wakeword or touch starts recording.
2. StackChan posts WAV data to `POST /stackchan/audio`.
3. Bridge transcribes using the configured STT provider.
4. Bridge handles simple one-step local commands directly when possible.
5. Bridge asks Hermes for conversation, combined tasks, and complex requests.
6. Hermes returns JSON with top-level `reply` and optional `actions`.
7. Bridge validates actions and publishes MQTT commands.
8. Bridge creates TTS for `reply`.
9. StackChan receives or fetches the TTS WAV and plays it.

Local shortcuts include volume, brightness, display sleep/wake, battery,
temperature, info mode with date/time/weekday, and basic sensor questions. They
log `hermes=0ms` and still return display/TTS to StackChan. Combined tasks and
anything requiring reasoning still goes to Hermes.

For this flow Hermes should return JSON:

```json
{
  "reply": "Mache ich.",
  "actions": [
    {"action": "face", "emotion": "friendly", "intensity_pct": 70}
  ]
}
```

Do not put normal spoken answers into a `say` action. Use the top-level `reply`.

## Face Contract

StackChan's current face system is template-based and intentionally simple:
black background, white eyes, white mouth, white brows, black pupil cutouts.
Hermes should only send the supported semantic face names below.

Base emotions:

- `neutral`, `calm`, `friendly`, `happy`, `super_happy`, `thankful`, `love`
- `curious`, `playful`, `focused`, `concerned`, `annoyed`, `help`
- `sad`, `angry`, `surprised`, `tired`, `confused`, `scared`
- `listening`, `thinking`, `speaking`, `charging`

Short transients:

- `soft_blink`, `blink`, `breathe`, `deep_breathe`
- `glance_left`, `glance_right`, `glance_up`, `glance_down`
- `look_left`, `look_right`, `look_up`, `look_down`
- `brow_raise`, `brow_soft`, `brow_skeptic`, `brow_skeptic_left`,
  `brow_skeptic_right`, `brow_wiggle`
- `mouth_smile`, `mouth_tiny`, `mouth_wiggle`

Use `friendly` for "glücklich gucken", "freundlich schauen", "lächeln", and
normal warm confirmations. Use `super_happy` only when the user asks for extra
happy or when a celebration really fits.

Never send old removed face names: `happy_squint`, `derp`, `cross_eyes`,
`surprise_pop`, `micro_sleep`, `silent_giggle`, `smirk_slide`, `mischievous`,
`smug`, `evil_grin`, `glitch`, or `dead`. If an old session still uses them,
restart/delete that stale Hermes session.

## Available MQTT Topics

Pair namespace:

`hermes-stackchan/desk`

Important command topics:

- `hermes-stackchan/desk/cmd/display`
- `hermes-stackchan/desk/cmd/face`
- `hermes-stackchan/desk/cmd/move`
- `hermes-stackchan/desk/cmd/motion`
- `hermes-stackchan/desk/cmd/led`
- `hermes-stackchan/desk/cmd/device`
- `hermes-stackchan/desk/cmd/sound`
- `hermes-stackchan/desk/cmd/audio`
- `hermes-stackchan/desk/cmd/system`

Important feedback topics:

- `hermes-stackchan/desk/status`
- `hermes-stackchan/desk/state/device_settings`
- `hermes-stackchan/desk/ack`
- `hermes-stackchan/desk/error`
- `hermes-stackchan/desk/events`

MQTT is for state and commands. Do not send binary audio over MQTT; audio goes over
HTTP.

## Hardware Actions Hermes May Request

Use these through Hermes JSON actions or through bridge helper commands:

- `display`: accepted for compatibility; `mode:"info"` shows a sticky local
  info screen with time, weekday, date, and a small face
- `display_image`: accepted for compatibility, but current firmware does not replace the face with images
- `face`: set a supported template emotion such as `neutral`, `friendly`,
  `super_happy`, `happy`, `thankful`, `curious`, `playful`, `focused`,
  `concerned`, `annoyed`, `help`, `sad`, `angry`, `surprised`, `tired`,
  `confused`, `scared`, `listening`, `thinking`, `speaking`, or a calm
  transient such as `soft_blink`, `breathe`, `glance_left`, `glance_right`,
  `glance_up`, `glance_down`, `mouth_smile`, `mouth_tiny`, `mouth_wiggle`,
  `brow_raise`, `brow_soft`, `brow_skeptic`, `brow_skeptic_left`,
  `brow_skeptic_right`, `brow_wiggle`
- `move`: safe directional or target movement
- `motion`: computed motion path with waypoints
- `led`: LED modes/colors
- `device`: brightness, volume, display sleep/wake, persistent settings where
  supported
- `audio`: play a TTS URL
- `system`: ping/status, display sleep/wake, reboot, and explicit shutdown
- `system take_photo`: request a StackChan photo when camera firmware is available
- `reminder` or `notify`: schedule bridge reminders if the user asks for them

The bridge and firmware are authoritative. They validate actions, clamp servo
limits, and may ignore unsafe commands.

## Companion Context, Privacy, And History

The bridge now sends Hermes a V1.0 companion context package with:

- pair profile: pair ID, wakeword, voice, personality/capability files
- persistent mood and mood intensity
- privacy mode: `normal`, `focus`, `private`, `demo`, `debug`
- proactivity level: `quiet`, `balanced`, `playful`
- status summary and local capabilities
- recent interactions when privacy allows it

Use this context instead of guessing. In `private` mode, do not ask for camera,
web/Hermes-dependent enrichment, or proactive speech. In `focus` mode, avoid
unrequested chatter. Debug mode may keep audio if the bridge is configured to do
so; normal mode should not depend on archived audio.

Useful diagnostics:

```bash
cd /home/wollux/hermes2stackchan
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env healthz --pair desk
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env watchdog-status --pair desk
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env replay-list --pair desk
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env replay-last --pair desk
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env read-companion --pair desk --with-status
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env set-companion --pair desk --privacy-mode focus
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env list-history --pair desk
```

Watchdog and replay notes:

- `/healthz?status=1` includes StackChan presence age, stale/offline state, last
  skip reason, watchdog config, and a short replay-buffer summary.
- `watchdog-status --pair desk` is the quick human-readable check before sending
  actions. If StackChan is stale, do not push notify/reminder/display/motion output.
- `replay-list`, `replay-last`, and `replay-show --request-id ...` inspect the
  last bridge speech/notify/photo rounds. Normal privacy keeps text/metadata only;
  debug privacy may keep audio paths; private privacy keeps only minimal errors.
- A real IMU shake during TTS is local: bridge sends `cmd/audio` with
  `audio_action: stop_playback`, turns LEDs off, and does not ask Hermes.

The bridge shows an animated thinking heartbeat while Hermes is processing:
glances, brows, breathing, blinks, and a small mouth impulse. It intentionally
does not move servos, use LEDs, or add spoken filler unless Wollux explicitly
asks for that. Normal non-LED speech requests clean LEDs back to off at the end,
so do not rely on a weather/status answer leaving a decorative lamp active.

## Face And Motion Behavior

StackChan has an idle-life animator in the bridge. It may blink, breathe, glance,
move its mouth, and occasionally move the head. This should only run when idle and
must not interrupt recording, speaking, battery overlays, sleep, or errors.
The bridge also has an idle-sleep watcher: after ten quiet minutes without human
interaction or non-life actions, it sends `cmd/device {"display_sleep":true}` and
the life animator must stay quiet because the retained status reports
`display_sleeping:true`.
Head touch, display touch, `cmd/move`, `cmd/motion`, BMI270 IMU movement, and
LTR553 proximity wake StackChan through the CRT wake animation before continuing
with recording or movement. The bridge treats the firmware `interaction` event as
human activity for the ten-minute idle-sleep timer.

The bridge also runs a sensor watcher. It filters BMI270/LTR553 noise and reacts
only to stable physical interaction: shake becomes a short surprise face, lying
on the side becomes a help/panic face, face-down becomes a tantrum face, and proximity lowers the head a little
until the object moves away. These reactions are bridge-owned helper behavior;
Hermes should not duplicate them with extra LED or sound actions.

Normal pitch/rest position is around `45`, not `0`. Yaw can move more freely than
pitch; pitch must remain conservative.

If Wollux reports resets after motion:

1. Check the serial monitor if available.
2. Check bridge logs for recent `cmd/motion`.
3. Prefer slower/smoother motion and avoid sending face changes while a long motion
   is still running.
4. Do not assume the reset is from Hermes unless logs show it.

## Wake, Touch, And Recording

Current behavior:

- Wakeword is expected to start the same recording path as touch.
- Touch events are published to `hermes-stackchan/desk/events`.
- Fast-touch bridge logic can turn LEDs green quickly while recording.
- Audio WAV is posted to `/stackchan/audio`.
- STT and TTS are bridge responsibilities.

If recording stops too early, inspect events and voice-level fields in MQTT status
and bridge logs before changing timing.

## Reminders And Notifications

When Wollux says "erinnere mich ..." and means StackChan should notify him later:

- Use the bridge reminder mechanism if available.
- If the reminder fires, it must eventually call the proactive notify path so that
  StackChan wakes, displays, and speaks.
- If the user only says "erinnere mich" without enough content/time, ask a short
  follow-up question instead of inventing details.

For immediate proactive messages, do not create a reminder; call `/stackchan/notify`.

## News Briefing Over StackChan

When Wollux asks for "die Nachrichten" or "KI-Nachrichten" via StackChan:

1. Do current web research first.
2. Synthesize a compact German briefing.
3. Respect a character limit if provided.
4. Send it through `POST /stackchan/notify`.
5. Verify the bridge log shows `cmd/audio` and StackChan fetched the WAV.
6. Reply in Telegram only briefly after success.

Example:

```bash
TEXT='Nachrichten kurz: ...'
curl -fsS -X POST http://127.0.0.1:8788/stackchan/notify \
  -H 'Content-Type: application/json' \
  -d "{\"reply\":\"$TEXT\",\"actions\":[{\"action\":\"face\",\"emotion\":\"speaking\",\"intensity_pct\":60}]}"
```

## Direct Diagnostics CLI

Use the CLI only for manual diagnostics, not for proactive Telegram speech:

```bash
cd /home/wollux/hermes2stackchan
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env read-status --pair desk
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env send-display --pair desk --text 'Text' --wait-ack
/home/wollux/hermes2stackchan/.venv/bin/python -m bridge.hermes2stackchan_bridge --env .env send-face --pair desk --emotion happy --wait-ack
```

`send-say` is not the Telegram/proactive voice path. Avoid it unless explicitly
debugging legacy display compatibility.

## Debugging Checklist

For every claimed StackChan action:

- Health checked: `curl -fsS http://127.0.0.1:8788/health`
- Correct path used:
  - StackChan-originated voice: `/stackchan/audio`
  - Hermes/Telegram-originated voice: `/stackchan/notify`
- Bridge log inspected.
- If audio was expected, `cmd/audio` was logged.
- If physical playback was claimed, StackChan fetched `/stackchan/tts/...wav`.
- No secrets printed.
- No bridge/gateway restart unless needed for deployment or stale sessions.

## Gateway Notes

Hermes Gateway may cache session context. If the agent keeps using old behavior:

```bash
/home/wollux/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main sessions list
/home/wollux/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main sessions delete SESSION_ID --yes
/home/wollux/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway restart
```

The gateway can report `api_server` port `8642` already in use. Telegram may still
run correctly. Do not confuse that warning with StackChan notify audio failure.

## Safety

- Never print keys from `.env`.
- Do not reboot the Pi or StackChan unless explicitly asked.
- Do not shut StackChan down unless Wollux explicitly says to power off,
  runterfahren, abschalten, or ausschalten. Sleep/display-off uses
  `display_sleep`, not `shutdown`.
- Do not restart services just to inspect state.
- Do not claim live playback unless `/stackchan/notify` succeeded and StackChan
  fetched the TTS WAV.
- Prefer short spoken messages; long text can stress the display/audio path.
- Keep StackChan hardware safe: bridge/firmware limits win over Hermes wishes.
