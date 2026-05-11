# Hermes Integration

This is the reproducible V1.0 integration path for one Hermes and one StackChan.

## Contract

- One Hermes controls exactly one StackChan.
- The active pair is configured in `.env` and `config/pairs.example.json`.
- The active MQTT namespace is `hermes-stackchan/<pair-id>`.
- StackChan records audio locally and uploads WAV data to the bridge over HTTP.
- The bridge transcribes speech, asks Hermes, validates Hermes actions, publishes MQTT commands, generates TTS, and returns the TTS URL to StackChan.

## Runtime Flow

1. StackChan hears `Computer` or touch starts recording.
2. StackChan stops after silence or the configured maximum duration.
3. StackChan posts WAV audio to `POST /stackchan/audio`.
4. Bridge transcribes audio through the configured STT provider.
5. Bridge reads retained MQTT status from `hermes-stackchan/desk/status`.
6. Bridge sends transcript, status, personality, and `capabilities/bridge-capabilities-desk.md` to Hermes.
7. Hermes returns JSON only:

```json
{
  "reply": "Mache ich.",
  "actions": [
    {"action": "face", "emotion": "happy", "intensity_pct": 70}
  ]
}
```

8. Bridge publishes valid hardware/display actions to MQTT.
9. Bridge creates TTS for `reply` and returns or sends a `tts_url`.
10. StackChan plays the returned WAV.

## Required `.env`

```env
H2S_PAIR_ID=desk
H2S_HERMES_ID=hermes-desk
H2S_STACKCHAN_ID=stackchan-desk
H2S_CAPABILITIES_FILE=capabilities/bridge-capabilities-desk.md
H2S_PERSONALITY_FILE=personalities/hermes-desk.md

H2S_MQTT_HOST=127.0.0.1
H2S_MQTT_PORT=1883

H2S_BRIDGE_HTTP_HOST=0.0.0.0
H2S_BRIDGE_HTTP_PORT=8788

H2S_STT_PROVIDER=groq
H2S_GROQ_API_KEY=...
H2S_STT_MODEL=whisper-large-v3-turbo
H2S_STT_LANGUAGE=de

H2S_HERMES_BASE_URL=http://127.0.0.1:8642
H2S_HERMES_API_KEY=...
H2S_HERMES_MODEL=default

H2S_TTS_ENGINE=edge
H2S_EDGE_TTS_VOICE=de-DE-KatjaNeural
H2S_EDGE_TTS_RATE=+8%
```

## Local Test

Check Hermes:

```bash
scripts/h2s_bridge.sh hermes-health
```

Ask Hermes and publish returned actions:

```bash
scripts/h2s_bridge.sh ask-hermes --text "Sag kurz hallo und schaue freundlich."
```

Run the unified bridge:

```bash
scripts/h2s_bridge.sh run --pair desk
```

Watch logs:

```bash
journalctl -u hermes2stackchan.service -f
```

## Install As A Service

On the target Raspberry Pi:

```bash
sudo ./scripts/install_bridge_service.sh
sudo nano /opt/hermes2stackchan/.env
sudo systemctl start hermes2stackchan.service
sudo systemctl status hermes2stackchan.service
```

The installer copies the project to `/opt/hermes2stackchan`, creates a virtual environment, installs the Python package, installs the systemd unit, and enables it for boot.

## Hermes Capabilities

Hermes learns the interface from:

```text
capabilities/bridge-capabilities-desk.md
```

Keep that file current whenever a firmware or bridge command changes. The bridge still validates every Hermes action before publishing it to MQTT, so Hermes may ask, but the bridge and firmware decide what is safe to execute.
