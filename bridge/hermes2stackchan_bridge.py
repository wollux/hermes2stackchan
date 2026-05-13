from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import http.server
import io
import json
import math
import mimetypes
import os
import random
import re
import signal
import ssl
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, RLock, Thread, Timer
from typing import Any, Callable
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "1.0"
POWER_DISPLAY_DURATION_MS = 5000
MAX_STACKCHAN_TEXT_CHARS = 700
MAX_STACKCHAN_DISPLAY_CHARS = 320
MAX_STACKCHAN_TTS_CHARS = 2500
STACKCHAN_DISPLAY_ASPECT = 320 / 240
OPENVERSE_IMAGE_SEARCH_URL = "https://api.openverse.org/v1/images/"
WIKIMEDIA_COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
HTTP_USER_AGENT = "hermes2stackchan-bridge/1.0 (https://github.com/wollux/hermes2stackchan)"
DEFAULT_IDLE_YAW_PCT = 0
DEFAULT_IDLE_PITCH_PCT = 45
YAW_TARGET_MIN_PCT = -100
YAW_TARGET_MAX_PCT = 100
PITCH_TARGET_MIN_PCT = 0
PITCH_TARGET_MAX_PCT = 100
DEFAULT_CONFIG = Path("config/pairs.json")
EXAMPLE_CONFIG = Path("config/pairs.example.json")
DEFAULT_ENV = Path(".env")
DEFAULT_REMINDER_STORE = "~/.hermes/hermes2stackchan/reminders.json"
DEFAULT_IDLE_SLEEP_TIMEOUT_S = 300.0
DEFAULT_LIFE_MIN_INTERVAL_S = 0.35
DEFAULT_LIFE_MAX_INTERVAL_S = 1.35
MAX_LIFE_FACE_GAP_MS = 1200
DEFAULT_LIFE_SMALL_MOTION_GAP_S = 20.0
DEFAULT_LIFE_BIG_MOTION_GAP_S = 120.0
LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")
STACKCHAN_PRESENCE_TIMEOUT_S = 15.0
SENSOR_PROXIMITY_ON_DELTA = 55
SENSOR_PROXIMITY_OFF_DELTA = 28
SENSOR_PROXIMITY_ON_RAW = 120
SENSOR_PROXIMITY_OFF_RAW = 70
SENSOR_PROXIMITY_STABLE_SAMPLES = 2
SENSOR_PROXIMITY_CLEAR_SAMPLES = 3
SENSOR_PROXIMITY_HEAD_DROP_PCT = 18
SENSOR_SIDE_AXIS_MG = 760
SENSOR_SIDE_UPRIGHT_MAX_MG = 560
SENSOR_UPRIGHT_AXIS_MG = 620
SENSOR_FACE_DOWN_AXIS_MG = 900
SENSOR_FACE_DOWN_OTHER_MAX_MG = 650
SENSOR_SIDE_STABLE_SAMPLES = 2
SENSOR_FACE_DOWN_STABLE_SAMPLES = 2
SENSOR_FACE_DOWN_REPEAT_S = 2.0
SENSOR_SHAKE_SCORE_THRESHOLD = 20
SENSOR_SHAKE_COOLDOWN_S = 4.0
SENSOR_REACTION_COOLDOWN_S = 1.0
SENSOR_WAKE_COOLDOWN_S = 2.0
SENSOR_SIDE_HELP_TEXT = "Hilfe! Ich bin umgekippt!"
SENSOR_SIDE_THANKS_TEXT = "Danke, ich stehe wieder. Rettung erfolgreich!"
SENSOR_FACE_DOWN_TEXT = "Hey! Nicht aufs Gesicht. Das mag ich gar nicht!"
REMINDER_STORE_LOCK = RLock()
LIFE_PAUSE_LOCK = RLock()
LIFE_PAUSED_UNTIL: dict[str, float] = {}
SIDE_TOUCH_SOURCES = {"head_touch_left", "head_touch_right"}
SIDE_TOUCH_GIGGLE_WINDOW_S = 1.0


class ConfigError(ValueError):
    """Raised when the local pair registry is invalid."""


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int = 1883
    client_id: str = "hermes2stackchan-bridge"
    username: str | None = None
    password: str | None = None
    tls: bool = False


@dataclass(frozen=True)
class PairConfig:
    pair_id: str
    hermes_id: str
    stackchan_id: str
    mqtt_prefix: str
    capabilities_file: str | None = None
    personality_file: str | None = None

    @property
    def display_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/display"

    @property
    def system_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/system"

    @property
    def face_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/face"

    @property
    def move_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/move"

    @property
    def motion_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/motion"

    @property
    def sound_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/sound"

    @property
    def audio_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/audio"

    @property
    def led_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/led"

    @property
    def device_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/device"

    @property
    def say_topic(self) -> str:
        return f"{self.mqtt_prefix}/cmd/say"

    @property
    def status_topic(self) -> str:
        return f"{self.mqtt_prefix}/status"

    @property
    def settings_topic(self) -> str:
        return f"{self.mqtt_prefix}/state/device_settings"

    @property
    def ack_topic(self) -> str:
        return f"{self.mqtt_prefix}/ack"

    @property
    def error_topic(self) -> str:
        return f"{self.mqtt_prefix}/error"

    @property
    def events_topic(self) -> str:
        return f"{self.mqtt_prefix}/events"


@dataclass(frozen=True)
class HermesConfig:
    base_url: str = "http://127.0.0.1:8642"
    api_key: str | None = None
    model: str = "default"
    timeout_s: float = 30.0


@dataclass(frozen=True)
class SpeechConfig:
    provider: str = "groq"
    groq_api_key: str | None = None
    groq_url: str = "https://api.groq.com/openai/v1/audio/transcriptions"
    groq_model: str = "whisper-large-v3-turbo"
    language: str = "de"
    prompt: str = "Deutsch. StackChan, Hermes, Wollux. Kurze Befehle und Fragen."
    timeout_s: float = 30.0
    max_audio_bytes: int = 2 * 1024 * 1024
    archive_dir: str | None = None
    tts_dir: str | None = None
    image_dir: str | None = None
    max_image_bytes: int = 8 * 1024 * 1024
    tts_engine: str = "edge"
    edge_tts_python: str = ""
    edge_tts_voice: str = "de-DE-KatjaNeural"
    edge_tts_rate: str = "+8%"
    bridge_public_url: str = ""
    display_duration_ms: int = 9000


@dataclass(frozen=True)
class ReminderConfig:
    store_path: str = DEFAULT_REMINDER_STORE
    poll_interval_s: float = 1.0
    display_duration_ms: int = 9000


@dataclass(frozen=True)
class BridgeConfig:
    mqtt: MqttConfig
    pairs: dict[str, PairConfig]
    hermes: HermesConfig = field(default_factory=HermesConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    reminders: ReminderConfig = field(default_factory=ReminderConfig)


@dataclass
class SensorReactionState:
    proximity_active: bool = False
    proximity_seen_count: int = 0
    proximity_clear_count: int = 0
    proximity_restore_pitch_pct: int = DEFAULT_IDLE_PITCH_PCT
    side_active: bool = False
    side_seen_count: int = 0
    upright_seen_count: int = 0
    face_down_active: bool = False
    face_down_seen_count: int = 0
    last_shake_at: float = -9999.0
    last_side_at: float = -9999.0
    last_face_down_at: float = -9999.0
    last_proximity_at: float = -9999.0
    last_wake_at: float = -9999.0


LifeSequence = list[tuple[int, dict[str, Any]]]


@dataclass(frozen=True)
class LifeVariant:
    name: str
    weight: float
    rare: bool
    min_gap_s: float
    builder: Callable[[random.Random, int, str], LifeSequence]


def load_config(
    path: Path = DEFAULT_CONFIG,
    env_path: Path | None = DEFAULT_ENV,
    environ: dict[str, str] | None = None,
) -> BridgeConfig:
    config_path = path
    if not config_path.exists() and path == DEFAULT_CONFIG:
        config_path = EXAMPLE_CONFIG

    if not config_path.exists():
        raise ConfigError(f"config file not found: {path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    env = load_env(env_path, environ)
    mqtt_raw = raw.get("mqtt") or {}
    mqtt = MqttConfig(
        host=env.get("H2S_MQTT_HOST") or require_string(mqtt_raw, "host", "mqtt.host"),
        port=parse_int(env.get("H2S_MQTT_PORT"), int(mqtt_raw.get("port", 1883)), "H2S_MQTT_PORT"),
        client_id=env.get("H2S_MQTT_CLIENT_ID") or str(mqtt_raw.get("client_id") or "hermes2stackchan-bridge"),
        username=optional_string(env.get("H2S_MQTT_USERNAME", mqtt_raw.get("username"))),
        password=optional_string(env.get("H2S_MQTT_PASSWORD", mqtt_raw.get("password"))),
        tls=parse_bool(env.get("H2S_MQTT_TLS"), bool(mqtt_raw.get("tls", False)), "H2S_MQTT_TLS"),
    )
    hermes_raw = raw.get("hermes") or {}
    if not isinstance(hermes_raw, dict):
        raise ConfigError("hermes must be an object when present")
    hermes = HermesConfig(
        base_url=(env.get("H2S_HERMES_BASE_URL") or str(hermes_raw.get("base_url") or "http://127.0.0.1:8642")).rstrip("/"),
        api_key=optional_string(env.get("H2S_HERMES_API_KEY") or env.get("API_SERVER_KEY") or hermes_raw.get("api_key")),
        model=env.get("H2S_HERMES_MODEL") or str(hermes_raw.get("model") or "default"),
        timeout_s=parse_float(env.get("H2S_HERMES_TIMEOUT_S"), float(hermes_raw.get("timeout_s", 30.0)), "H2S_HERMES_TIMEOUT_S"),
    )
    speech_raw = raw.get("speech") or {}
    if not isinstance(speech_raw, dict):
        raise ConfigError("speech must be an object when present")
    speech = SpeechConfig(
        provider=(env.get("H2S_STT_PROVIDER") or str(speech_raw.get("provider") or "groq")).strip().lower(),
        groq_api_key=optional_string(
            env.get("H2S_GROQ_API_KEY")
            or env.get("GROQ_API_KEY")
            or env.get("GROQ_KEY")
            or speech_raw.get("groq_api_key")
        ),
        groq_url=env.get("H2S_GROQ_STT_URL") or str(speech_raw.get("groq_url") or "https://api.groq.com/openai/v1/audio/transcriptions"),
        groq_model=env.get("H2S_STT_MODEL") or str(speech_raw.get("groq_model") or "whisper-large-v3-turbo"),
        language=env.get("H2S_STT_LANGUAGE") or str(speech_raw.get("language") or "de"),
        prompt=env.get("H2S_STT_PROMPT") or str(speech_raw.get("prompt") or "Deutsch. StackChan, Hermes, Wollux. Kurze Befehle und Fragen."),
        timeout_s=parse_float(env.get("H2S_STT_TIMEOUT_S"), float(speech_raw.get("timeout_s", 30.0)), "H2S_STT_TIMEOUT_S"),
        max_audio_bytes=parse_int(
            env.get("H2S_MAX_AUDIO_BYTES"),
            int(speech_raw.get("max_audio_bytes", 2 * 1024 * 1024)),
            "H2S_MAX_AUDIO_BYTES",
        ),
        archive_dir=optional_string(env.get("H2S_WAV_ARCHIVE_DIR") or speech_raw.get("archive_dir")),
        tts_dir=optional_string(env.get("H2S_TTS_DIR") or speech_raw.get("tts_dir")),
        image_dir=optional_string(env.get("H2S_IMAGE_DIR") or speech_raw.get("image_dir")),
        max_image_bytes=parse_int(
            env.get("H2S_MAX_IMAGE_BYTES"),
            int(speech_raw.get("max_image_bytes", 8 * 1024 * 1024)),
            "H2S_MAX_IMAGE_BYTES",
        ),
        tts_engine=env.get("H2S_TTS_ENGINE") or str(speech_raw.get("tts_engine") or "edge"),
        edge_tts_python=env.get("H2S_EDGE_TTS_PYTHON") or str(speech_raw.get("edge_tts_python") or ""),
        edge_tts_voice=env.get("H2S_EDGE_TTS_VOICE") or str(speech_raw.get("edge_tts_voice") or "de-DE-KatjaNeural"),
        edge_tts_rate=env.get("H2S_EDGE_TTS_RATE") or str(speech_raw.get("edge_tts_rate") or "+8%"),
        bridge_public_url=(
            env.get("H2S_BRIDGE_PUBLIC_URL")
            or bridge_base_url_from_audio_url(env.get("H2S_BRIDGE_AUDIO_URL"))
            or str(speech_raw.get("bridge_public_url") or "")
        ).rstrip("/"),
        display_duration_ms=parse_int(
            env.get("H2S_TRANSCRIPT_DISPLAY_MS"),
            int(speech_raw.get("display_duration_ms", 9000)),
            "H2S_TRANSCRIPT_DISPLAY_MS",
        ),
    )
    reminder_raw = raw.get("reminders") or {}
    if not isinstance(reminder_raw, dict):
        raise ConfigError("reminders must be an object when present")
    reminders = ReminderConfig(
        store_path=env.get("H2S_REMINDER_STORE") or str(reminder_raw.get("store_path") or DEFAULT_REMINDER_STORE),
        poll_interval_s=parse_float(
            env.get("H2S_REMINDER_POLL_S"),
            float(reminder_raw.get("poll_interval_s", 1.0)),
            "H2S_REMINDER_POLL_S",
        ),
        display_duration_ms=parse_int(
            env.get("H2S_REMINDER_DISPLAY_MS"),
            int(reminder_raw.get("display_duration_ms", 9000)),
            "H2S_REMINDER_DISPLAY_MS",
        ),
    )

    pairs_raw = raw.get("pairs")
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise ConfigError("pairs must be a non-empty list")

    pairs: dict[str, PairConfig] = {}
    for index, item in enumerate(pairs_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"pairs[{index}] must be an object")

        pair_id = require_string(item, "pair_id", f"pairs[{index}].pair_id")
        mqtt_prefix = require_string(item, "mqtt_prefix", f"pairs[{index}].mqtt_prefix").rstrip("/")
        validate_pair_namespace(pair_id, mqtt_prefix, f"pairs[{index}].mqtt_prefix")
        if pair_id in pairs:
            raise ConfigError(f"duplicate pair_id: {pair_id}")

        pairs[pair_id] = PairConfig(
            pair_id=pair_id,
            hermes_id=require_string(item, "hermes_id", f"pairs[{index}].hermes_id"),
            stackchan_id=require_string(item, "stackchan_id", f"pairs[{index}].stackchan_id"),
            mqtt_prefix=mqtt_prefix,
            capabilities_file=optional_string(item.get("capabilities_file")),
            personality_file=optional_string(item.get("personality_file")),
        )

    if has_pair_env(env):
        base_pair = pairs.get(env.get("H2S_PAIR_ID", "")) or next(iter(pairs.values()))
        pair_id = env.get("H2S_PAIR_ID") or base_pair.pair_id
        mqtt_prefix = env.get("H2S_MQTT_PREFIX") or f"hermes-stackchan/{pair_id}"
        validate_pair_namespace(pair_id, mqtt_prefix, "H2S_MQTT_PREFIX")
        pairs = {
            pair_id: PairConfig(
                pair_id=pair_id,
                hermes_id=env.get("H2S_HERMES_ID") or base_pair.hermes_id,
                stackchan_id=env.get("H2S_STACKCHAN_ID") or base_pair.stackchan_id,
                mqtt_prefix=mqtt_prefix,
                capabilities_file=env.get("H2S_CAPABILITIES_FILE") or base_pair.capabilities_file,
                personality_file=env.get("H2S_PERSONALITY_FILE") or base_pair.personality_file,
            )
        }

    return BridgeConfig(mqtt=mqtt, pairs=pairs, hermes=hermes, speech=speech, reminders=reminders)


def load_env(env_path: Path | None, environ: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    if env_path and env_path.exists():
        env.update(parse_env_file(env_path))

    source = os.environ if environ is None else environ
    for key, value in source.items():
        if key.startswith("H2S_") or key in {"API_SERVER_KEY", "GROQ_API_KEY", "GROQ_KEY"}:
            env[key] = value
    return {key: value for key, value in env.items() if value != ""}


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            raise ConfigError(f"{path}:{line_number}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigError(f"{path}:{line_number}: empty env key")
        values[key] = unquote_env_value(value.strip())
    return values


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def parse_int(value: str | None, default: int, label: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{label} must be an integer") from exc


def parse_float(value: str | None, default: float, label: str) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a number") from exc


def parse_bool(value: str | None, default: bool, label: str) -> bool:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{label} must be true or false")


def bridge_base_url_from_audio_url(value: str | None) -> str:
    if not value:
        return ""
    marker = "/stackchan/audio"
    if marker in value:
        return value.split(marker, 1)[0].rstrip("/")
    return value.rstrip("/")


def has_pair_env(env: dict[str, str]) -> bool:
    return any(
        key in env
        for key in (
            "H2S_PAIR_ID",
            "H2S_HERMES_ID",
            "H2S_STACKCHAN_ID",
            "H2S_MQTT_PREFIX",
            "H2S_CAPABILITIES_FILE",
            "H2S_PERSONALITY_FILE",
        )
    )


def validate_pair_namespace(pair_id: str, mqtt_prefix: str, label: str) -> None:
    expected_prefix = f"hermes-stackchan/{pair_id}"
    if mqtt_prefix.rstrip("/") != expected_prefix:
        raise ConfigError(f"{label} must be {expected_prefix!r}, got {mqtt_prefix!r}")


def require_string(raw: dict[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"optional string value must be string or null, got {type(value).__name__}")
    value = value.strip()
    return value or None


def clamp_int(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, int(value)))


def build_display_payload(text: str, duration_ms: int, request_id: str | None = None) -> dict[str, Any]:
    text = safe_stackchan_text(text)
    if not text:
        raise ConfigError("display text must not be empty")
    if duration_ms < 0:
        raise ConfigError("duration_ms must be >= 0")

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "text",
        "text": text,
        "duration_ms": duration_ms,
        "request_id": request_id or uuid.uuid4().hex,
    }


def with_request_id(payload: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    result = {"schema_version": SCHEMA_VERSION, **payload}
    result["request_id"] = request_id or uuid.uuid4().hex
    return result


def create_mqtt_client(mqtt: MqttConfig):
    try:
        import paho.mqtt.client as mqtt_client
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency paho-mqtt. Install with: python -m pip install -e ."
        ) from exc

    try:
        client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=f"{mqtt.client_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}",
        )
    except AttributeError:
        client = mqtt_client.Client(client_id=f"{mqtt.client_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}")

    if mqtt.username:
        client.username_pw_set(mqtt.username, mqtt.password)
    if mqtt.tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    return client


def connect_and_start(client: Any, mqtt: MqttConfig, timeout_s: float = 8.0) -> None:
    connected = Event()

    def on_connect(_client: Any, _userdata: Any, _flags: Any, reason_code: Any, *_extra: Any) -> None:
        if is_success_reason_code(reason_code):
            connected.set()
        else:
            print(f"[bridge] MQTT connect failed: {reason_code}", file=sys.stderr)

    client.on_connect = on_connect
    client.connect(mqtt.host, mqtt.port, keepalive=30)
    client.loop_start()
    if not connected.wait(timeout_s):
        client.loop_stop()
        raise TimeoutError(f"MQTT connection timed out: {mqtt.host}:{mqtt.port}")


def is_success_reason_code(reason_code: Any) -> bool:
    value = getattr(reason_code, "value", None)
    if value is not None:
        return value == 0
    try:
        return int(reason_code) == 0
    except (TypeError, ValueError):
        return str(reason_code).lower() == "success"


def send_payload(
    args: argparse.Namespace,
    topic: str,
    payload: dict[str, Any],
) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    client = create_mqtt_client(config.mqtt)
    ack_seen = Event()
    response: dict[str, Any] = {}

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if data.get("request_id") != payload["request_id"]:
            return
        response.update(data)
        response["_topic"] = message.topic
        ack_seen.set()

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        if args.wait_ack:
            client.subscribe([(pair.ack_topic, 1), (pair.error_topic, 1)])
        result = client.publish(topic, body, qos=1, retain=False)
        result.wait_for_publish(timeout=5)
        print(f"[bridge] sent {topic}: {body}")
        if topic == pair.device_topic:
            publish_device_settings_snapshot(client, pair, payload, "send-payload")

        if args.wait_ack:
            if ack_seen.wait(args.timeout):
                print(f"[bridge] response {response.get('_topic')}: {json.dumps(response, ensure_ascii=False)}")
                return 0 if response.get("_topic") == pair.ack_topic else 2
            print(f"[bridge] no ACK within {args.timeout:.1f}s for {payload['request_id']}", file=sys.stderr)
            return 3
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


def build_touch_lamp_payload(event_payload: dict[str, Any], request_id: str | None = None) -> dict[str, Any] | None:
    event = optional_string(event_payload.get("event"))
    source = optional_string(event_payload.get("source"))
    if source in SIDE_TOUCH_SOURCES:
        return None
    recording = event_payload.get("recording")
    if event in {"touch_down", "recording_started"} or recording is True:
        return with_request_id({"mode": "solid", "r": 0, "g": 255, "b": 0}, request_id)
    if event == "recording_stopped" or recording is False:
        return with_request_id({"mode": "off", "r": 0, "g": 0, "b": 0}, request_id)
    return None


@dataclass
class TouchEmotionState:
    last_side: str = ""
    last_side_at: float = -9999.0


def build_touch_emotion_actions(
    event_payload: dict[str, Any],
    state: TouchEmotionState,
    now_s: float | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    event = optional_string(event_payload.get("event"))
    source = optional_string(event_payload.get("source"))
    if event not in {"touch_down", "touch_swipe_forward", "touch_swipe_backward"} or source not in SIDE_TOUCH_SOURCES:
        return [], []

    now_s = time.monotonic() if now_s is None else now_s
    side = "left" if source == "head_touch_left" else "right"
    previous_side = state.last_side
    previous_at = state.last_side_at
    state.last_side = side
    state.last_side_at = now_s

    if event in {"touch_swipe_forward", "touch_swipe_backward"}:
        return [
            {"action": "face", "emotion": "happy_squint", "intensity_pct": 84},
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 46,
                "points": [
                    {
                        "yaw_pct": -11 if side == "left" else 11,
                        "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 2,
                        "duration_ms": 180,
                        "speed_pct": 42,
                    },
                    {
                        "yaw_pct": 6 if side == "left" else -6,
                        "pitch_pct": DEFAULT_IDLE_PITCH_PCT,
                        "duration_ms": 160,
                        "speed_pct": 38,
                    },
                    {
                        "yaw_pct": DEFAULT_IDLE_YAW_PCT,
                        "pitch_pct": DEFAULT_IDLE_PITCH_PCT,
                        "duration_ms": 260,
                        "speed_pct": 28,
                    },
                ],
            },
        ], [f"head_pet_swipe_{side}"]

    if previous_side and previous_side != side and now_s - previous_at <= SIDE_TOUCH_GIGGLE_WINDOW_S:
        return [
            {"action": "face", "emotion": "silent_giggle", "intensity_pct": 86},
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 52,
                "points": [
                    {"yaw_pct": -9, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 1, "duration_ms": 130, "speed_pct": 52},
                    {"yaw_pct": 9, "pitch_pct": DEFAULT_IDLE_PITCH_PCT - 1, "duration_ms": 130, "speed_pct": 52},
                    {"yaw_pct": -5, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 1, "duration_ms": 120, "speed_pct": 45},
                    {"yaw_pct": DEFAULT_IDLE_YAW_PCT, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 220, "speed_pct": 35},
                ],
            },
        ], ["side_touch_giggle"]

    if side == "left":
        return [
            {"action": "face", "emotion": "happy_squint", "intensity_pct": 76},
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 24,
                "points": [
                    {"yaw_pct": -8, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 3, "duration_ms": 260, "speed_pct": 24},
                    {"yaw_pct": DEFAULT_IDLE_YAW_PCT, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 420, "speed_pct": 18},
                ],
            },
        ], ["side_touch_left"]

    return [
        {"action": "face", "emotion": "smirk_slide", "intensity_pct": 78},
        {
            "action": "motion",
            "curve": "spline",
            "speed_pct": 32,
            "points": [
                {"yaw_pct": 9, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 1, "duration_ms": 180, "speed_pct": 32},
                {"yaw_pct": 3, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 2, "duration_ms": 170, "speed_pct": 26},
                {"yaw_pct": DEFAULT_IDLE_YAW_PCT, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 320, "speed_pct": 22},
            ],
        },
    ], ["side_touch_right"]


def read_latest_status(config: BridgeConfig, pair: PairConfig, timeout_s: float = 2.0) -> dict[str, Any] | None:
    return read_retained_json(config, pair.status_topic, timeout_s)


def read_retained_json(config: BridgeConfig, topic: str, timeout_s: float = 2.0) -> dict[str, Any] | None:
    client = create_mqtt_client(config.mqtt)
    payload_seen = Event()
    payload: dict[str, Any] = {}

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        payload.clear()
        payload.update(data)
        payload_seen.set()

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        client.subscribe(topic, qos=0)
        if payload_seen.wait(timeout_s):
            return payload
        return None
    finally:
        client.loop_stop()
        client.disconnect()


def extract_device_settings(source: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    settings: dict[str, Any] = {}
    for key in ("volume_pct", "brightness_pct"):
        value = source.get(key)
        if value is not None:
            settings[key] = clamp_int(parse_int_value(value, 0, f"settings.{key}"), 0, 100)
    speaker_volume = nested_status_value(source, "speaker.volume_pct")
    if "volume_pct" not in settings and speaker_volume is not None:
        settings["volume_pct"] = clamp_int(parse_int_value(speaker_volume, 0, "settings.speaker.volume_pct"), 0, 100)
    return settings


def build_device_settings_snapshot(
    pair: PairConfig,
    settings: dict[str, Any],
    source: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(previous or {})
    merged.update(extract_device_settings(settings))
    if not merged:
        return {}
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair.pair_id,
        "stackchan_id": pair.stackchan_id,
        "source": source,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **merged,
    }


def publish_device_settings_snapshot(
    client: Any,
    pair: PairConfig,
    settings: dict[str, Any],
    source: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = build_device_settings_snapshot(pair, settings, source, previous)
    if not snapshot:
        return {}
    body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    result = client.publish(pair.settings_topic, body, qos=1, retain=True)
    result.wait_for_publish(timeout=5)
    print(f"[{time.strftime('%H:%M:%S')}] [bridge] retained {pair.settings_topic}: {body}", flush=True)
    return snapshot


def read_status(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    status = read_latest_status(config, pair, args.timeout)
    if status is None:
        print(f"[bridge] no retained status within {args.timeout:.1f}s for {pair.status_topic}", file=sys.stderr)
        return 3
    print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


REQUIRED_STATUS_PATHS = (
    "schema_version",
    "pair_id",
    "stackchan_id",
    "uptime_ms",
    "firmware",
    "firmware_version",
    "battery_pct",
    "battery_known",
    "battery_charging",
    "battery_discharging",
    "usb_power",
    "external_power",
    "volume_pct",
    "brightness_pct",
    "display_sleeping",
    "wakeword_enabled",
    "recording",
    "speaking",
    "head.pan_pct",
    "head.tilt_pct",
    "head.ready",
    "head.motion_active",
    "face.emotion",
    "face.intensity_pct",
    "ui.mode",
    "led.mode",
    "led.mode_id",
    "led.r",
    "led.g",
    "led.b",
    "led.ready",
    "speaker.ready",
    "speaker.volume_pct",
    "temperature.soc_c",
    "temperature.servo_yaw_c",
    "temperature.servo_pitch_c",
    "audio.input_ready",
    "audio.wakeword_enabled",
    "audio.wakeword",
    "audio.recording",
    "audio.recording_source",
    "audio.recording_started_ms",
    "audio.recording_min_ms",
    "audio.recording_silence_timeout_ms",
    "audio.recording_max_ms",
    "interaction.active",
    "interaction.last_source",
    "interaction.last_ms",
    "sensors.imu.ready",
    "sensors.imu.accel_mg.x",
    "sensors.imu.accel_mg.y",
    "sensors.imu.accel_mg.z",
    "sensors.imu.gyro_dps.x",
    "sensors.imu.gyro_dps.y",
    "sensors.imu.gyro_dps.z",
    "sensors.imu.motion_score_pct",
    "sensors.imu.motion_active",
    "sensors.ltr553.ready",
    "sensors.ltr553.proximity_raw",
    "sensors.ltr553.ambient_raw",
    "sensors.ltr553.proximity_baseline",
    "sensors.ltr553.proximity_delta",
    "sensors.ltr553.near",
    "sensors.ltr553.light_changed",
)


def nested_status_value(status: dict[str, Any], path: str) -> Any:
    value: Any = status
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def missing_status_paths(status: dict[str, Any]) -> list[str]:
    return [path for path in REQUIRED_STATUS_PATHS if nested_status_value(status, path) is None]


def status_health(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    status = read_latest_status(config, pair, args.timeout)
    if status is None:
        print(f"[bridge] status health failed: no retained status within {args.timeout:.1f}s for {pair.status_topic}", file=sys.stderr)
        return 3

    missing = missing_status_paths(status)
    if missing:
        print("[bridge] status health failed: missing required fields", file=sys.stderr)
        for path in missing:
            print(f"- {path}", file=sys.stderr)
        if args.show_status:
            print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    print(
        "[bridge] status health ok: "
        f"battery={status.get('battery_pct')}% "
        f"external_power={status.get('external_power')} "
        f"head=({nested_status_value(status, 'head.pan_pct')},{nested_status_value(status, 'head.tilt_pct')}) "
        f"face={nested_status_value(status, 'face.emotion')} "
        f"firmware={status.get('firmware')}",
        flush=True,
    )
    if args.show_status:
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def resolve_asset_path(path_value: str | None, config_path: Path) -> Path | None:
    if not path_value:
        return None
    raw_path = Path(path_value)
    if raw_path.is_absolute():
        return raw_path
    candidates = [
        Path.cwd() / raw_path,
        config_path.parent / raw_path,
        config_path.parent.parent / raw_path if config_path.parent.name == "config" else config_path.parent / raw_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def read_optional_text(path_value: str | None, config_path: Path) -> str:
    path = resolve_asset_path(path_value, config_path)
    if path is None:
        return ""
    if not path.exists():
        raise ConfigError(f"referenced file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def hermes_chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def hermes_health_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/health"


def hermes_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def http_get_text(url: str, api_key: str | None, timeout_s: float) -> str:
    request = urllib.request.Request(url, headers=hermes_headers(api_key), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ConfigError(f"Hermes HTTP {exc.code} at {url}: {body}") from exc


def http_post_json(url: str, api_key: str | None, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=hermes_headers(api_key), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ConfigError(f"Hermes HTTP {exc.code} at {url}: {error_body}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Hermes returned non-JSON response: {raw[:500]}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Hermes response must be a JSON object")
    return data


def strip_json_code_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_hermes_action_response(content: str) -> dict[str, Any]:
    text = strip_json_code_fence(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"reply": content.strip(), "actions": [{"action": "say", "text": content.strip(), "emotion": "speaking"}]}
    if isinstance(parsed, list):
        return {"reply": "", "actions": parsed}
    if not isinstance(parsed, dict):
        raise ConfigError("Hermes action response must be an object or array")
    actions = parsed.get("actions", [])
    if isinstance(actions, dict):
        actions = [actions]
    if actions is None:
        actions = []
    if not isinstance(actions, list):
        raise ConfigError("Hermes response field actions must be an array")
    parsed["actions"] = actions
    return parsed


QUESTION_WORDS_DE = (
    "was ",
    "wie ",
    "wo ",
    "wann ",
    "warum ",
    "weshalb ",
    "wieso ",
    "welche ",
    "welcher ",
    "welches ",
    "möchtest ",
    "moechtest ",
    "willst ",
    "soll ",
    "sollen ",
    "kann ",
    "können ",
    "koennen ",
    "darf ",
    "brauchst ",
)


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ja", "on"}
    return False


def should_listen_for_followup(response: dict[str, Any], reply: str) -> bool:
    if boolish(response.get("follow_up_listen")) or boolish(response.get("followup_listen")):
        return True
    if boolish(response.get("expects_reply")) or boolish(response.get("expects_user_reply")):
        return True

    actions = response.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            if boolish(action.get("follow_up_listen")) or boolish(action.get("expects_reply")):
                return True
            emotion = str(action.get("emotion") or "").strip().lower().replace("-", "_")
            if emotion in {"question", "curious"}:
                return True

    normalized = " ".join(reply.strip().lower().split())
    if "?" not in normalized:
        return False
    return normalized.startswith(QUESTION_WORDS_DE) or any(
        f" {word}" in normalized for word in QUESTION_WORDS_DE
    )


def extract_hermes_message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ConfigError("Hermes response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ConfigError("Hermes response choice must be an object")
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(first.get("text"), str):
        return first["text"]
    raise ConfigError("Hermes response has no message content")


def build_hermes_system_content(
    pair: PairConfig,
    capabilities: str,
    personality: str,
    status: dict[str, Any] | None,
) -> str:
    status_text = json.dumps(status or {}, ensure_ascii=False, sort_keys=True)
    system_parts = [
        f"You are {pair.hermes_id}. You control exactly one StackChan: {pair.stackchan_id}.",
        f"Your MQTT namespace is {pair.mqtt_prefix}. Never address another StackChan.",
        "Return JSON only. Do not wrap it in Markdown.",
        "Schema: {\"reply\":\"short German text\",\"follow_up_listen\":false,\"actions\":[{\"action\":\"display|face|move|motion|led|device|sound|system|reminder\",...}]}",
        "Answer in German unless the user explicitly asks for another language.",
        "Put the spoken answer only in the top-level reply field. Do not use action say for normal answers, direct messages, reminders, notifications, or command confirmations.",
        "Use display only when you want to show extra visible text beyond reply. The bridge will synthesize reply as audio for StackChan when TTS is enabled.",
        "You may add hardware actions when useful, but never invent unsupported parameters. The bridge and firmware enforce limits.",
        "Keep answers concise for spoken interaction unless the user asks for detail.",
        "If your reply asks the user a real follow-up question and you expect an immediate answer, set follow_up_listen to true.",
        "If your reply is only a statement, command confirmation, or rhetorical question, set follow_up_listen to false.",
        "For reminders or notifications, use action reminder with text and delay_s or due_at. Example: {\"action\":\"reminder\",\"text\":\"Wasser trinken\",\"delay_s\":120}.",
        "If the user only says 'erinnere mich' without enough time or content, ask what/when and set follow_up_listen to true; do not invent reminder details.",
        "For explicit StackChan sleep commands use {\"action\":\"system\",\"system_action\":\"display_sleep\"}. For wake/display-on commands use display_wake. For explicit power-off/shutdown/runterfahren/abschalten commands use {\"action\":\"system\",\"system_action\":\"shutdown\"}; never use shutdown for ordinary sleep.",
        "For status questions, use the current status JSON and answer directly; do not invent sensor values.",
        f"Current StackChan status JSON: {status_text}",
    ]
    if capabilities:
        system_parts.append(f"Bridge capabilities:\n{capabilities}")
    if personality:
        system_parts.append(f"Personality notes:\n{personality}")
    return "\n\n".join(system_parts)


def build_hermes_messages(
    pair: PairConfig,
    capabilities: str,
    personality: str,
    status: dict[str, Any] | None,
    user_text: str,
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": build_hermes_system_content(pair, capabilities, personality, status)},
        {"role": "user", "content": user_text},
    ]


def image_data_url(image_bytes: bytes, content_type: str) -> str:
    content_type = content_type if content_type.startswith("image/") else "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def build_hermes_vision_messages(
    pair: PairConfig,
    capabilities: str,
    personality: str,
    status: dict[str, Any] | None,
    user_text: str,
    image_bytes: bytes,
    content_type: str,
) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": build_hermes_system_content(pair, capabilities, personality, status)},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_data_url(image_bytes, content_type)}},
            ],
        },
    ]


def ask_hermes_http(
    config: BridgeConfig,
    pair: PairConfig,
    capabilities: str,
    personality: str,
    status: dict[str, Any] | None,
    user_text: str,
) -> dict[str, Any]:
    payload = {
        "model": config.hermes.model,
        "messages": build_hermes_messages(pair, capabilities, personality, status, user_text),
        "temperature": 0.3,
    }
    response = http_post_json(
        hermes_chat_url(config.hermes.base_url),
        config.hermes.api_key,
        payload,
        config.hermes.timeout_s,
    )
    return parse_hermes_action_response(extract_hermes_message_content(response))


def ask_hermes_vision_http(
    config: BridgeConfig,
    pair: PairConfig,
    capabilities: str,
    personality: str,
    status: dict[str, Any] | None,
    user_text: str,
    image_bytes: bytes,
    content_type: str,
) -> dict[str, Any]:
    payload = {
        "model": config.hermes.model,
        "messages": build_hermes_vision_messages(
            pair,
            capabilities,
            personality,
            status,
            user_text,
            image_bytes,
            content_type,
        ),
        "temperature": 0.2,
    }
    response = http_post_json(
        hermes_chat_url(config.hermes.base_url),
        config.hermes.api_key,
        payload,
        config.hermes.timeout_s,
    )
    return parse_hermes_action_response(extract_hermes_message_content(response))


def speech_text_from_hermes_response(response: dict[str, Any], fallback: str) -> str:
    reply = optional_string(response.get("reply"))
    if reply:
        return reply
    actions = response.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict) and str(action.get("action", "")).lower().replace("-", "_") == "say":
                text = optional_string(action.get("text"))
                if text:
                    return text
        for action in actions:
            if isinstance(action, dict) and str(action.get("action", "")).lower().replace("-", "_") == "display":
                text = optional_string(action.get("text"))
                if text:
                    return text
    return fallback


def actions_to_topic_payloads(
    pair: PairConfig,
    actions: list[dict[str, Any]],
    request_id_prefix: str,
    skip_actions: set[str] | None = None,
    config: BridgeConfig | None = None,
    handler: http.server.BaseHTTPRequestHandler | None = None,
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    messages: list[tuple[str, dict[str, Any]]] = []
    errors: list[str] = []
    skip_actions = skip_actions or set()
    for index, action in enumerate(actions):
        name = ""
        if isinstance(action, dict):
            name = str(action.get("action") or action.get("type") or action.get("name") or "").strip().lower().replace("-", "_")
            if name in skip_actions:
                continue
        try:
            if name in {"search_image", "image_search", "web_image", "internet_image", "net_image"}:
                if config is None:
                    raise ConfigError("image_search action needs bridge config")
                query = optional_string(action.get("query") or action.get("q") or action.get("text") or action.get("prompt"))
                image_bytes, _content_type, meta = search_openverse_image(
                    query or "",
                    config.speech,
                    parse_int_value(action.get("limit"), 20, "image_search.limit"),
                )
                image_info = prepare_stackchan_image(config, handler, image_bytes, f"{request_id_prefix}-{index:02d}")
                caption = optional_string(action.get("caption")) or query or meta.get("title", "")
                display_action = {
                    "action": "display_image",
                    "url": image_info["url"],
                    "width": image_info["width"],
                    "height": image_info["height"],
                    "format": image_info["format"],
                    "duration_ms": parse_int_value(action.get("duration_ms"), 9000, "image_search.duration_ms"),
                    "caption": caption,
                }
                messages.append(action_to_topic_payload(pair, display_action, f"{request_id_prefix}-{index:02d}"))
                continue
            if name in {"local_tts", "tts", "speak"}:
                if config is None:
                    raise ConfigError("local_tts action needs bridge config")
                text = optional_string(action.get("text") or action.get("message"))
                if not text:
                    raise ConfigError("local_tts action needs text")
                tts_path = make_tts_wav(safe_tts_text(text), config.speech, f"{request_id_prefix}-{index:02d}")
                tts_url = tts_public_url(config, tts_path)
                audio_action = {"action": "audio", "audio_action": "play_tts_url", "url": tts_url}
                messages.append(action_to_topic_payload(pair, audio_action, f"{request_id_prefix}-{index:02d}"))
                continue
            messages.append(action_to_topic_payload(pair, action, f"{request_id_prefix}-{index:02d}"))
        except ConfigError as exc:
            errors.append(str(exc))
    return messages, errors


def action_to_topic_payload(pair: PairConfig, action: dict[str, Any], request_id: str | None = None) -> tuple[str, dict[str, Any]]:
    if not isinstance(action, dict):
        raise ConfigError("Hermes action must be an object")
    raw_name = action.get("action") or action.get("type") or action.get("name")
    name = action_name(action)
    if not name:
        raise ConfigError("Hermes action needs an action name")
    action_request_id = optional_string(action.get("request_id")) or request_id

    if name == "display":
        text = optional_string(action.get("text"))
        if not text:
            raise ConfigError("display action needs text")
        text = safe_stackchan_text(text, MAX_STACKCHAN_DISPLAY_CHARS)
        payload = build_display_payload(text, parse_int_value(action.get("duration_ms"), 5000, "display.duration_ms"), action_request_id)
        return pair.display_topic, payload

    if name in {"display_image", "image"}:
        url = optional_string(action.get("url") or action.get("image_url"))
        if not url:
            raise ConfigError("display_image action needs url")
        payload: dict[str, Any] = {
            "mode": "image",
            "url": url,
            "width": clamp_int(parse_int_value(action.get("width"), 320, "display_image.width"), 1, 320),
            "height": clamp_int(parse_int_value(action.get("height"), 240, "display_image.height"), 1, 240),
            "format": optional_string(action.get("format")) or "jpeg",
            "duration_ms": parse_int_value(action.get("duration_ms"), 9000, "display_image.duration_ms"),
        }
        caption = optional_string(action.get("caption"))
        if caption:
            payload["caption"] = safe_stackchan_text(caption, 80)
        return pair.display_topic, with_request_id(payload, action_request_id)

    if name == "say":
        text = optional_string(action.get("text"))
        if not text:
            raise ConfigError("say action needs text")
        text = safe_stackchan_text(text, MAX_STACKCHAN_DISPLAY_CHARS)
        payload = build_display_payload(
            text,
            parse_int_value(action.get("duration_ms"), 7000, "say.duration_ms"),
            action_request_id,
        )
        return pair.display_topic, payload

    if name == "face":
        payload = with_request_id(
            {
                "emotion": optional_string(action.get("emotion")) or "neutral",
                "intensity_pct": parse_int_value(action.get("intensity_pct"), 60, "face.intensity_pct"),
            },
            action_request_id,
        )
        return pair.face_topic, payload

    if name in {"move", "look"}:
        payload: dict[str, Any] = {}
        if name == "look" and optional_string(action.get("direction")):
            payload["direction"] = optional_string(action.get("direction"))
        for key in ("direction", "yaw_delta", "pitch_delta", "yaw_target_pct", "pitch_target_pct"):
            if key in action and action[key] is not None:
                payload[key] = action[key]
        if "pan_pct" in action and "yaw_target_pct" not in payload:
            payload["yaw_target_pct"] = action["pan_pct"]
        if "tilt_pct" in action and "pitch_target_pct" not in payload:
            payload["pitch_target_pct"] = action["tilt_pct"]
        if "yaw_target_pct" in payload:
            payload["yaw_target_pct"] = clamp_int(
                parse_int_value(payload["yaw_target_pct"], DEFAULT_IDLE_YAW_PCT, "move.yaw_target_pct"),
                YAW_TARGET_MIN_PCT,
                YAW_TARGET_MAX_PCT,
            )
        if "pitch_target_pct" in payload:
            payload["pitch_target_pct"] = clamp_int(
                parse_int_value(payload["pitch_target_pct"], DEFAULT_IDLE_PITCH_PCT, "move.pitch_target_pct"),
                PITCH_TARGET_MIN_PCT,
                PITCH_TARGET_MAX_PCT,
            )
        if not payload:
            raise ConfigError("move action needs direction, delta, or target percent")
        return pair.move_topic, with_request_id(payload, action_request_id)

    if name == "motion":
        points = action.get("points")
        if points is None:
            raise ConfigError("motion action needs points")
        speed_pct = parse_int_value(action.get("speed_pct"), 45, "motion.speed_pct")
        segment_ms = parse_optional_int_value(action.get("segment_ms"), "motion.segment_ms")
        payload = {
            "curve": optional_string(action.get("curve")) or "spline",
            "speed_pct": clamp_int(speed_pct, 1, 100),
            "points": normalize_motion_points(points, clamp_int(speed_pct, 1, 100), segment_ms),
        }
        if segment_ms is not None:
            payload["segment_ms"] = clamp_int(segment_ms, 0, 4000)
        return pair.motion_topic, with_request_id(payload, action_request_id)

    if name == "led":
        payload: dict[str, Any] = {"mode": optional_string(action.get("mode")) or "solid"}
        for key in ("r", "g", "b"):
            if key in action and action[key] is not None:
                payload[key] = action[key]
        return pair.led_topic, with_request_id(payload, action_request_id)

    if name == "device":
        payload = {}
        for key in ("volume_pct", "brightness_pct", "display_sleep", "display_wake"):
            if key in action and action[key] is not None:
                payload[key] = action[key]
        if not payload:
            raise ConfigError("device action needs at least one device field")
        return pair.device_topic, with_request_id(payload, action_request_id)

    if name == "sound":
        payload = {
            "frequency_hz": parse_int_value(action.get("frequency_hz"), 880, "sound.frequency_hz"),
            "duration_ms": parse_int_value(action.get("duration_ms"), 140, "sound.duration_ms"),
        }
        pattern = optional_string(action.get("pattern") or action.get("kind") or action.get("sound"))
        if pattern:
            payload["pattern"] = pattern
        if action.get("volume_pct") is not None:
            payload["volume_pct"] = action["volume_pct"]
        return pair.sound_topic, with_request_id(payload, action_request_id)

    if name in {"audio", "start_recording", "stop_recording", "set_wakeword", "simulate_wakeword", "play_tts_url"}:
        audio_action = optional_string(action.get("audio_action") or action.get("command"))
        if name != "audio":
            audio_action = name
        if not audio_action:
            raise ConfigError("audio action needs audio_action or command")
        audio_action = audio_action.strip().lower().replace("-", "_")
        if audio_action not in {"start_recording", "stop_recording", "set_wakeword", "simulate_wakeword", "play_tts_url"}:
            raise ConfigError(f"unsupported audio action: {audio_action}")
        payload: dict[str, Any] = {"action": audio_action}
        for key in ("source", "wakeword", "reason"):
            if optional_string(action.get(key)):
                payload[key] = optional_string(action.get(key))
        if optional_string(action.get("url")):
            payload["url"] = optional_string(action.get("url"))
        for key in ("min_ms", "silence_timeout_ms", "max_ms"):
            if action.get(key) is not None:
                payload[key] = parse_int_value(action.get(key), 0, f"audio.{key}")
        if action.get("enabled") is not None:
            payload["enabled"] = parse_bool_value(action.get("enabled"), True)
        return pair.audio_topic, with_request_id(payload, action_request_id)

    if name in {"system", "ping", "status", "reboot", "display_sleep", "display_wake", "shutdown", "power_off", "take_photo", "photo", "camera"}:
        system_action = optional_string(action.get("system_action") or action.get("command"))
        if name != "system":
            system_action = "take_photo" if name in {"photo", "camera"} else name
        if not system_action:
            raise ConfigError("system action needs system_action or command")
        system_action = system_action.strip().lower().replace("-", "_")
        if system_action not in {"ping", "status", "reboot", "display_sleep", "display_wake", "shutdown", "power_off", "take_photo"}:
            raise ConfigError(f"unsupported system action: {system_action}")
        return pair.system_topic, with_request_id({"action": system_action}, action_request_id)

    raise ConfigError(f"unsupported Hermes action: {raw_name}")


def parse_int_value(value: Any, default: int, label: str) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer")
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        return parse_int(value, default, label)
    raise ConfigError(f"{label} must be an integer")


def parse_optional_int_value(value: Any, label: str) -> int | None:
    if value is None or value == "":
        return None
    return parse_int_value(value, 0, label)


def parse_bool_value(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return parse_bool(value, default, "boolean value")
    raise ConfigError("boolean value must be true or false")


REMINDER_ACTIONS = {"reminder", "notify", "notification", "remind"}
POST_TTS_SYSTEM_ACTIONS = {"display_sleep", "shutdown", "power_off"}
STATUS_NOT_PROVIDED = object()


class StackChanPresence:
    def __init__(self, timeout_s: float = STACKCHAN_PRESENCE_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._lock = RLock()
        self._last_seen: dict[str, float] = {}
        self._last_log: dict[str, str] = {}

    def mark_status(self, pair: PairConfig, status: dict[str, Any], *, retained: bool = False) -> None:
        if retained:
            return
        if not isinstance(status, dict):
            return
        pair_id = optional_string(status.get("pair_id"))
        if pair_id and pair_id != pair.pair_id:
            return
        if status.get("uptime_ms") is None:
            return
        with self._lock:
            self._last_seen[pair.pair_id] = time.monotonic()

    def mark_seen(self, pair: PairConfig) -> None:
        with self._lock:
            self._last_seen[pair.pair_id] = time.monotonic()

    def is_online(self, pair: PairConfig, now_s: float | None = None) -> bool:
        now = time.monotonic() if now_s is None else now_s
        with self._lock:
            seen = self._last_seen.get(pair.pair_id)
        return seen is not None and now - seen <= self.timeout_s

    def age_s(self, pair: PairConfig, now_s: float | None = None) -> float | None:
        now = time.monotonic() if now_s is None else now_s
        with self._lock:
            seen = self._last_seen.get(pair.pair_id)
        if seen is None:
            return None
        return max(0.0, now - seen)

    def note_skip(self, pair: PairConfig, reason: str) -> None:
        age = self.age_s(pair)
        age_text = "never" if age is None else f"{age:.1f}s"
        message = f"StackChan offline/stale for {age_text}; skipped {reason}"
        with self._lock:
            if self._last_log.get(pair.pair_id) == message:
                return
            self._last_log[pair.pair_id] = message
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] {message}", flush=True)

    def clear(self) -> None:
        with self._lock:
            self._last_seen.clear()
            self._last_log.clear()


STACKCHAN_PRESENCE = StackChanPresence()


def note_stackchan_status(pair: PairConfig, status: dict[str, Any], *, retained: bool = False) -> None:
    STACKCHAN_PRESENCE.mark_status(pair, status, retained=retained)


def note_stackchan_seen(pair: PairConfig) -> None:
    STACKCHAN_PRESENCE.mark_seen(pair)


def stackchan_is_online(pair: PairConfig) -> bool:
    return STACKCHAN_PRESENCE.is_online(pair)


def message_is_retained(message: Any) -> bool:
    return bool(getattr(message, "retain", False))


def action_name(action: dict[str, Any]) -> str:
    raw_name = action.get("action") or action.get("type") or action.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return ""
    return raw_name.strip().lower().replace("-", "_")


def normalize_spoken_command_text(text: str) -> str:
    translation = str.maketrans(
        {
            "ä": "ae",
            "ö": "oe",
            "ü": "ue",
            "ß": "ss",
            "Ä": "ae",
            "Ö": "oe",
            "Ü": "ue",
        }
    )
    normalized = text.translate(translation).lower()
    for char in ".,!?;:()[]{}\"'`´":
        normalized = normalized.replace(char, " ")
    return " ".join(normalized.split())


def strip_spoken_command_prefixes(normalized: str) -> str:
    command = normalized
    changed = True
    while changed:
        changed = False
        for prefix in ("bitte ", "computer ", "stackchan ", "stack chan ", "hermes "):
            if command.startswith(prefix):
                command = command[len(prefix):].strip()
                changed = True
    return command


def spoken_command_is_negated(normalized: str) -> bool:
    return any(negative in f" {normalized} " for negative in (" nicht ", " kein ", " keine "))


def spoken_command_is_combined(command: str) -> bool:
    return any(
        marker in f" {command} "
        for marker in (
            " und ",
            " dann ",
            " danach ",
            " nachdem ",
            " ausserdem ",
            " wenn ",
            " sobald ",
        )
    )


SPOKEN_NUMBER_WORDS = {
    "null": 0,
    "eins": 1,
    "ein": 1,
    "eine": 1,
    "einen": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fuenf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "elf": 11,
    "zwoelf": 12,
    "dreizehn": 13,
    "vierzehn": 14,
    "fuenfzehn": 15,
    "sechzehn": 16,
    "siebzehn": 17,
    "achtzehn": 18,
    "neunzehn": 19,
    "zwanzig": 20,
    "dreissig": 30,
    "vierzig": 40,
    "fuenfzig": 50,
    "sechzig": 60,
    "siebzig": 70,
    "achtzig": 80,
    "neunzig": 90,
    "hundert": 100,
}

GERMAN_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
GERMAN_WEEKDAY_NORMALIZED = tuple(normalize_spoken_command_text(day) for day in GERMAN_WEEKDAYS)
GERMAN_MONTHS = (
    "Januar",
    "Februar",
    "Maerz",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def local_datetime(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(LOCAL_TIMEZONE)
    if now.tzinfo is None:
        return now.replace(tzinfo=LOCAL_TIMEZONE)
    return now.astimezone(LOCAL_TIMEZONE)


def spoken_int_token(token: str) -> int | None:
    token = normalize_spoken_command_text(token).strip()
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    return SPOKEN_NUMBER_WORDS.get(token)


def parse_spoken_number_value(text: str) -> int | None:
    text = normalize_spoken_command_text(text)
    match = re.search(r"-?\d+", text)
    if match:
        return int(match.group(0))
    for word, value in SPOKEN_NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            return value
    return None


def format_minutes_duration(minutes: int) -> str:
    minutes = max(0, minutes)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} Stunden und {rest} Minuten"
    if hours:
        return f"{hours} Stunden"
    return f"{rest} Minuten"


def local_time_reply_from_command(command: str, now: dt.datetime | None = None) -> tuple[str, list[dict[str, Any]], str] | None:
    current = local_datetime(now)
    padded = f" {command} "
    weekday = GERMAN_WEEKDAYS[current.weekday()]
    month = GERMAN_MONTHS[current.month - 1]

    for index, normalized_day in enumerate(GERMAN_WEEKDAY_NORMALIZED):
        if f"heute {normalized_day}" in command or f"heute ein {normalized_day}" in command or f"heute {normalized_day}?" in command:
            if current.weekday() == index:
                return f"Ja, heute ist {GERMAN_WEEKDAYS[index]}.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""
            return f"Nein, heute ist {weekday}.", [{"action": "face", "emotion": "thinking", "intensity_pct": 60}], ""

    if "wochenende" in command:
        if current.weekday() >= 5:
            return "Ja, es ist Wochenende.", [{"action": "face", "emotion": "happy", "intensity_pct": 66}], ""
        days_until_saturday = 5 - current.weekday()
        target = (current + dt.timedelta(days=days_until_saturday)).replace(hour=0, minute=0, second=0, microsecond=0)
        minutes = int((target - current).total_seconds() // 60)
        return f"Bis zum Wochenende sind es noch {format_minutes_duration(minutes)}.", [{"action": "face", "emotion": "thinking", "intensity_pct": 60}], ""

    if "mitternacht" in command:
        target = (current + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        minutes = int((target - current).total_seconds() // 60)
        return f"Bis Mitternacht sind es noch {format_minutes_duration(minutes)}.", [{"action": "face", "emotion": "thinking", "intensity_pct": 60}], ""

    if any(token in padded for token in (" uhrzeit ", " wie spaet ", " wieviel uhr ", " welche uhrzeit ")):
        if current.minute:
            text = f"Es ist {current.hour} Uhr {current.minute:02d}."
        else:
            text = f"Es ist {current.hour} Uhr."
        return text, [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""

    if "kalenderwoche" in command or "kw" in padded:
        return f"Kalenderwoche {current.isocalendar().week}.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""

    if "wochentag" in command or "welcher tag" in command:
        return f"Heute ist {weekday}.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""

    if "datum" in command or "welches datum" in command:
        return f"Heute ist {weekday}, der {current.day}. {month} {current.year}.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""

    if "monat" in command and command.startswith(("welcher", "was", "sag", "zeige")):
        return f"Wir haben {month}.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""

    if "jahr" in command and command.startswith(("welches", "was", "sag", "zeige")):
        return f"Wir haben {current.year}.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""

    if "tageszeit" in command or "guten morgen" in command or "guten abend" in command:
        if 5 <= current.hour < 11:
            greeting = "Guten Morgen."
        elif 11 <= current.hour < 17:
            greeting = "Guten Tag."
        elif 17 <= current.hour < 22:
            greeting = "Guten Abend."
        else:
            greeting = "Gute Nacht."
        return greeting, [{"action": "face", "emotion": "friendly", "intensity_pct": 66}], ""

    return None


def parse_local_duration_s(command: str) -> int | None:
    match = re.search(
        r"\b(?:in|auf|fuer)?\s*(\d+|ein|eine|einen|eins|zwei|drei|vier|fuenf|sechs|sieben|acht|neun|zehn|elf|zwoelf|zwanzig|dreissig|vierzig|fuenfzig|sechzig)\s*"
        r"(sekunden?|minuten?|stunden?)\b",
        command,
    )
    if not match:
        return None
    amount = spoken_int_token(match.group(1))
    if amount is None:
        return None
    unit = match.group(2)
    if unit.startswith("sekunde"):
        return max(1, amount)
    if unit.startswith("minute"):
        return max(1, amount * 60)
    if unit.startswith("stunde"):
        return max(1, amount * 3600)
    return None


def parse_local_due_at(command: str, now: dt.datetime | None = None) -> str | None:
    match = re.search(r"\bum\s*(\d{1,2})(?::| uhr)?\s*(\d{1,2})?\b", command)
    if not match:
        return None
    current = local_datetime(now)
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += dt.timedelta(days=1)
    return target.isoformat()


def local_reminder_reply_from_command(command: str, now: dt.datetime | None = None) -> tuple[str, list[dict[str, Any]], str] | None:
    padded = f" {command} "
    is_timer = " timer " in padded or command.startswith("timer") or "kurzzeitwecker" in command
    is_reminder = "erinnere" in command or "erinnerung" in command or "weck mich" in command or "wecker" in command
    if not is_timer and not is_reminder:
        return None

    delay_s = parse_local_duration_s(command)
    due_at = parse_local_due_at(command, now)
    if delay_s is None and due_at is None:
        return None

    if is_timer:
        text = "Timer abgelaufen."
        seconds = delay_s or 0
        if seconds >= 3600:
            hours = seconds // 3600
            reply = f"Timer auf {hours} {'Stunde' if hours == 1 else 'Stunden'} gestellt."
        elif seconds >= 60:
            minutes = seconds // 60
            reply = f"Timer auf {minutes} {'Minute' if minutes == 1 else 'Minuten'} gestellt."
        else:
            reply = f"Timer auf {seconds} {'Sekunde' if seconds == 1 else 'Sekunden'} gestellt."
    else:
        reminder_text = ""
        match = re.search(r"\ban\s+(.+)$", command)
        if match:
            reminder_text = match.group(1).strip(" .")
        elif "weck mich" in command or "wecker" in command:
            reminder_text = "Aufwachen"
        if not reminder_text:
            return None
        text = reminder_text[:120]
        reply = "Erinnerung gestellt."

    action: dict[str, Any] = {"action": "reminder", "text": text}
    if delay_s is not None:
        action["delay_s"] = delay_s
    elif due_at:
        action["due_at"] = due_at
    return reply, [{"action": "face", "emotion": "happy", "intensity_pct": 68}, action], ""


def local_math_reply_from_command(command: str) -> tuple[str, list[dict[str, Any]], str] | None:
    number = r"(-?\d+|null|eins|ein|eine|einen|zwei|drei|vier|fuenf|sechs|sieben|acht|neun|zehn|elf|zwoelf|zwanzig|dreissig|vierzig|fuenfzig|sechzig|hundert)"
    percent = re.search(rf"\b{number}\s*prozent\s+von\s+{number}\b", command)
    if percent:
        left = spoken_int_token(percent.group(1))
        right = spoken_int_token(percent.group(2))
        if left is not None and right is not None:
            result = right * left / 100
            text = f"Das sind {result:g}."
            return text, [{"action": "face", "emotion": "thinking", "intensity_pct": 62}], ""

    match = re.search(rf"\b{number}\s+(plus|minus|mal|geteilt(?: durch)?)\s+{number}\b", command)
    if not match:
        return None
    left = spoken_int_token(match.group(1))
    right = spoken_int_token(match.group(3))
    op = match.group(2)
    if left is None or right is None:
        return None
    if op == "plus":
        result: float = left + right
    elif op == "minus":
        result = left - right
    elif op == "mal":
        result = left * right
    else:
        if right == 0:
            return "Durch null lieber nicht.", [{"action": "face", "emotion": "skeptical", "intensity_pct": 65}], ""
        result = left / right
    return f"Das sind {result:g}.", [{"action": "face", "emotion": "thinking", "intensity_pct": 62}], ""


def local_random_reply_from_command(command: str) -> tuple[str, list[dict[str, Any]], str] | None:
    padded = f" {command} "
    if " wuerfel " in padded or command.startswith("wuerfel") or "wuerfeln" in command:
        return f"Ich wuerfle {random.randint(1, 6)}.", [{"action": "face", "emotion": "mischievous", "intensity_pct": 62}], ""
    if "kopf oder zahl" in command:
        return f"Ich nehme {random.choice(('Kopf', 'Zahl'))}.", [{"action": "face", "emotion": "mischievous", "intensity_pct": 62}], ""
    if command.startswith(("waehle ", "such dir ", "entscheide ")) and " oder " in command:
        tail = re.sub(r"^(waehle|such dir|entscheide)\s+", "", command).strip()
        options = [part.strip(" .") for part in tail.split(" oder ") if part.strip(" .")]
        if len(options) >= 2:
            return f"Ich nehme {random.choice(options)}.", [{"action": "face", "emotion": "mischievous", "intensity_pct": 62}], ""
    return None


def local_identity_reply_from_command(command: str) -> tuple[str, list[dict[str, Any]], str] | None:
    if "wer bist du" in command:
        return "Ich bin StackChan, lokal schnell und mit Hermes im Ruecken.", [{"action": "face", "emotion": "friendly", "intensity_pct": 68}], ""
    if "bist du wach" in command or command == "hallo" or command == "computer":
        return "Ja, ich bin wach.", [{"action": "face", "emotion": "friendly", "intensity_pct": 68}], ""
    if "was kannst du lokal" in command or "was kannst du alleine" in command:
        return (
            "Lokal kann ich Zeit, Datum, Timer, Akku, Temperatur, Lautstaerke, Helligkeit, LEDs, Display und Kopfbewegungen.",
            [{"action": "face", "emotion": "friendly", "intensity_pct": 68}],
            "",
        )
    return None


def parse_spoken_percent(command: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,3})(?:\s*(?:prozent|percent|%))?", command)
    if match:
        return clamp_int(int(match.group(1)), 0, 100)
    for word, value in SPOKEN_NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", command):
            return value
    return None


def local_command_may_need_status(text: str) -> bool:
    normalized = normalize_spoken_command_text(text)
    command = strip_spoken_command_prefixes(normalized)
    if not command or spoken_command_is_negated(normalized) or spoken_command_is_combined(command):
        return False
    return any(
        token in f" {command} "
        for token in (
            " akku ",
            " akkustand ",
            " batterie ",
            " temperatur ",
            " warm ",
            " sensor ",
            " sensoren ",
            " imu ",
            " ltr ",
            " naehe ",
            " naehesensor ",
            " proximity ",
            " finger ",
            " lichtsensor ",
            " helligkeit ",
            " lautstaerke ",
            " lauter ",
            " leiser ",
            " heller ",
            " dunkler ",
            " geschuettelt ",
            " schuetteln ",
            " bewegung ",
            " seite ",
            " wlan ",
            " wifi ",
            " kamera ",
            " display ",
            " bildschirm ",
            " schlaeft ",
            " schlafen ",
        )
    )


def status_percent(status: dict[str, Any] | None, path: str, fallback: int = 0) -> int:
    return clamp_int(status_int_at(status, path, fallback), 0, 100)


def direct_status_reply_from_transcript(
    command: str,
    status: Any = STATUS_NOT_PROVIDED,
) -> tuple[str, list[dict[str, Any]], str] | None:
    if status is STATUS_NOT_PROVIDED:
        return None
    if not isinstance(status, dict):
        if any(
            token in f" {command} "
            for token in (
                "akku",
                "akkustand",
                "batterie",
                "temperatur",
                "sensor",
                "sensoren",
                "helligkeit",
                "lautstaerke",
                "wlan",
                "wifi",
                "kamera",
                "display",
                "bildschirm",
            )
        ):
            return "Status ist gerade nicht verfuegbar.", [{"action": "face", "emotion": "error", "intensity_pct": 55}], ""
        return None

    if any(token in f" {command} " for token in ("akku", "akkustand", "batterie")):
        pct = status_percent(status, "battery_pct", 0)
        external = status_bool(status.get("external_power")) is True
        charging = status_bool(status.get("battery_charging")) is True
        if charging:
            suffix = "und laedt."
        elif external:
            suffix = "und haengt am Strom."
        else:
            suffix = "und laeuft auf Akku."
        return f"Akku {pct} Prozent, {suffix}", [{"action": "face", "emotion": "battery", "intensity_pct": 65}], ""

    if any(token in f" {command} " for token in ("temperatur", "warm")):
        soc = status_int_at(status, "temperature.soc_c", -1)
        yaw = status_int_at(status, "temperature.servo_yaw_c", -1)
        pitch = status_int_at(status, "temperature.servo_pitch_c", -1)
        parts = []
        if soc >= 0:
            parts.append(f"SoC {soc} Grad")
        if yaw >= 0:
            parts.append(f"Yaw Servo {yaw} Grad")
        if pitch >= 0:
            parts.append(f"Pitch Servo {pitch} Grad")
        text = ", ".join(parts) if parts else "Temperaturen sind gerade nicht bekannt."
        return text, [{"action": "face", "emotion": "neutral", "intensity_pct": 60}], ""

    if "helligkeit" in command and command.startswith(("wie ", "was ", "sag ", "zeige ")):
        pct = status_percent(status, "brightness_pct", 0)
        return f"Helligkeit {pct} Prozent.", [], ""

    if "lautstaerke" in command and command.startswith(("wie ", "was ", "sag ", "zeige ")):
        pct = status_percent(status, "speaker.volume_pct", status_int_at(status, "volume_pct", 0))
        return f"Lautstaerke {pct} Prozent.", [], ""

    if any(token in f" {command} " for token in ("wlan", "wifi")):
        rssi = status_int_at(status, "wifi.rssi", status_int_at(status, "wifi_rssi", 0))
        connected = status_bool(nested_status_value(status, "wifi.connected"))
        if connected is False:
            return "WLAN ist gerade nicht verbunden.", [{"action": "face", "emotion": "error", "intensity_pct": 55}], ""
        if rssi:
            return f"WLAN ist verbunden, RSSI {rssi} dBm.", [{"action": "face", "emotion": "neutral", "intensity_pct": 60}], ""
        return "WLAN Status ist nicht genau bekannt.", [{"action": "face", "emotion": "thinking", "intensity_pct": 60}], ""

    if "kamera" in command or "foto" in command:
        camera_ready = status_bool(status.get("camera_available"))
        if camera_ready is True:
            return "Kamera ist verfuegbar.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""
        if camera_ready is False:
            return "Kamera ist nicht verfuegbar.", [{"action": "face", "emotion": "error", "intensity_pct": 55}], ""

    if "display" in command or "bildschirm" in command:
        sleeping = status_bool(status.get("display_sleeping"))
        if sleeping is True:
            return "Display schlaeft.", [{"action": "face", "emotion": "sleepy", "intensity_pct": 60}], ""
        if sleeping is False:
            return "Display ist wach.", [{"action": "face", "emotion": "friendly", "intensity_pct": 62}], ""

    if any(token in f" {command} " for token in ("naehe", "naehesensor", "proximity", "finger")):
        near = status_bool(nested_status_value(status, "sensors.ltr553.near")) is True
        delta = status_int_at(status, "sensors.ltr553.proximity_delta", 0)
        if near or delta >= SENSOR_PROXIMITY_ON_DELTA:
            return f"Naehe erkannt, Delta {delta}.", [{"action": "face", "emotion": "glance_down", "intensity_pct": 62}], ""
        return f"Keine Naehe erkannt, Delta {delta}.", [{"action": "face", "emotion": "neutral", "intensity_pct": 60}], ""

    if any(token in f" {command} " for token in ("seite", "liegst", "liegt")):
        if sensor_status_is_sideways(status):
            return "Ich liege auf der Seite.", [{"action": "face", "emotion": "surprised", "intensity_pct": 78}], ""
        return "Ich stehe normal.", [{"action": "face", "emotion": "neutral", "intensity_pct": 60}], ""

    if any(token in f" {command} " for token in ("geschuettelt", "schuetteln", "bewegung", "imu")):
        score = status_int_at(status, "sensors.imu.motion_score_pct", 0)
        active = status_bool(nested_status_value(status, "sensors.imu.motion_active")) is True
        if active:
            return f"Bewegung erkannt, Score {score} Prozent.", [{"action": "face", "emotion": "surprise_pop", "intensity_pct": 75}], ""
        return f"Keine starke Bewegung, Score {score} Prozent.", [{"action": "face", "emotion": "neutral", "intensity_pct": 60}], ""

    if "sensor" in command or "sensoren" in command:
        imu_ready = status_bool(nested_status_value(status, "sensors.imu.ready")) is True
        ltr_ready = status_bool(nested_status_value(status, "sensors.ltr553.ready")) is True
        motion = status_int_at(status, "sensors.imu.motion_score_pct", 0)
        proximity = status_int_at(status, "sensors.ltr553.proximity_delta", 0)
        text = (
            f"IMU {'bereit' if imu_ready else 'nicht bereit'}, "
            f"LTR553 {'bereit' if ltr_ready else 'nicht bereit'}, "
            f"Bewegung {motion} Prozent, Naehe Delta {proximity}."
        )
        return text, [{"action": "face", "emotion": "neutral", "intensity_pct": 60}], ""

    return None


def direct_local_command_from_transcript(
    text: str,
    status: Any = STATUS_NOT_PROVIDED,
    now: dt.datetime | None = None,
) -> tuple[str, list[dict[str, Any]], str] | None:
    normalized = normalize_spoken_command_text(text)
    if not normalized:
        return None
    command = strip_spoken_command_prefixes(normalized)
    if spoken_command_is_negated(normalized):
        return None
    if spoken_command_is_combined(command):
        return None

    for local_reply in (
        local_time_reply_from_command(command, now),
        local_reminder_reply_from_command(command, now),
        local_math_reply_from_command(command),
        local_random_reply_from_command(command),
        local_identity_reply_from_command(command),
    ):
        if local_reply:
            return local_reply

    def is_command_phrase(phrase: str) -> bool:
        return command == phrase or command.startswith(f"{phrase} ")

    shutdown_phrases = (
        "runterfahren",
        "fahre runter",
        "fahr runter",
        "herunterfahren",
        "abschalten",
        "ausschalten",
        "schalte dich ab",
        "mach dich aus",
        "power off",
        "shutdown",
    )
    if any(is_command_phrase(phrase) for phrase in shutdown_phrases):
        return "Ich fahre jetzt runter.", [], "shutdown"

    reboot_phrases = (
        "neustart",
        "neu starten",
        "starte neu",
        "reboot",
    )
    if any(is_command_phrase(phrase) for phrase in reboot_phrases):
        return "Ich starte neu.", [{"action": "system", "system_action": "reboot"}], ""

    sleep_phrases = (
        "geh schlafen",
        "gehe schlafen",
        "schlafen",
        "schlaf ein",
        "schlafmodus",
        "bildschirm aus",
        "display aus",
        "mach den bildschirm aus",
    )
    if any(is_command_phrase(phrase) for phrase in sleep_phrases):
        return "Ich schlafe jetzt.", [], "display_sleep"

    wake_phrases = (
        "wach auf",
        "aufwachen",
        "weck auf",
        "bildschirm an",
        "display an",
        "mach den bildschirm an",
    )
    if any(is_command_phrase(phrase) for phrase in wake_phrases):
        return "Bin wach.", [{"action": "system", "system_action": "display_wake"}], ""

    percent = parse_spoken_percent(command)
    brightness_command = "helligkeit" in command or any(
        word in f" {command} "
        for word in (" heller ", " dunkler ")
    )
    if brightness_command:
        if percent is not None:
            return f"Helligkeit {percent} Prozent.", [{"action": "device", "brightness_pct": percent}], ""
        if any(word in f" {command} " for word in (" heller ", " hoch ", " hoeher ", " rauf ")):
            if status is STATUS_NOT_PROVIDED:
                return None
            if not isinstance(status, dict):
                return "Status ist gerade nicht verfuegbar.", [{"action": "face", "emotion": "error", "intensity_pct": 55}], ""
            current = status_percent(status, "brightness_pct", 70)
            target = clamp_int(current + 10, 0, 100)
            return f"Helligkeit {target} Prozent.", [{"action": "device", "brightness_pct": target}], ""
        if any(word in f" {command} " for word in (" dunkler ", " runter ", " niedriger ")):
            if status is STATUS_NOT_PROVIDED:
                return None
            if not isinstance(status, dict):
                return "Status ist gerade nicht verfuegbar.", [{"action": "face", "emotion": "error", "intensity_pct": 55}], ""
            current = status_percent(status, "brightness_pct", 70)
            target = clamp_int(current - 10, 0, 100)
            return f"Helligkeit {target} Prozent.", [{"action": "device", "brightness_pct": target}], ""

    if "lautstaerke" in command or command in {"lauter", "leiser"} or command.startswith(("mach lauter", "mach leiser")):
        if percent is not None:
            return f"Lautstaerke {percent} Prozent.", [{"action": "device", "volume_pct": percent}], ""
        if status is STATUS_NOT_PROVIDED:
            return None
        if not isinstance(status, dict):
            return "Status ist gerade nicht verfuegbar.", [{"action": "face", "emotion": "error", "intensity_pct": 55}], ""
        current = status_percent(status, "speaker.volume_pct", status_int_at(status, "volume_pct", 70))
        if "leiser" in command or "runter" in command or "niedriger" in command:
            target = clamp_int(current - 10, 0, 100)
        elif "lauter" in command or "hoch" in command or "hoeher" in command:
            target = clamp_int(current + 10, 0, 100)
        else:
            target = current
        return f"Lautstaerke {target} Prozent.", [{"action": "device", "volume_pct": target}], ""

    if any(token in f" {command} " for token in ("led aus", "leds aus", "lampe aus", "lampen aus")):
        return "LEDs aus.", [{"action": "led", "mode": "off"}], ""
    if any(token in f" {command} " for token in ("led an", "leds an", "lampe an", "lampen an")):
        return "LEDs an.", [{"action": "led", "mode": "solid", "r": 40, "g": 120, "b": 255}], ""

    if any(token in f" {command} " for token in ("ton test", "tontest", "piep", "beep", "sound test")):
        return "Ton.", [{"action": "sound", "pattern": "good", "frequency_hz": 880, "duration_ms": 120}], ""

    if any(token in f" {command} " for token in ("mach foto", "mach ein foto", "mache foto", "mache ein foto", "foto machen", "kamera ausloesen")):
        return "Foto.", [{"action": "system", "system_action": "take_photo"}], ""

    if any(token in f" {command} " for token in ("kopf", "schau", "guck", "blicke")):
        if any(token in f" {command} " for token in (" links ", " nach links ")):
            return "Links.", [{"action": "move", "direction": "left"}, {"action": "face", "emotion": "glance_left", "intensity_pct": 65}], ""
        if any(token in f" {command} " for token in (" rechts ", " nach rechts ")):
            return "Rechts.", [{"action": "move", "direction": "right"}, {"action": "face", "emotion": "glance_right", "intensity_pct": 65}], ""
        if any(token in f" {command} " for token in (" oben ", " hoch ", " nach oben ")):
            return "Hoch.", [{"action": "move", "pitch_target_pct": 65}, {"action": "face", "emotion": "glance_up", "intensity_pct": 65}], ""
        if any(token in f" {command} " for token in (" unten ", " runter ", " nach unten ")):
            return "Runter.", [{"action": "move", "pitch_target_pct": 25}, {"action": "face", "emotion": "glance_down", "intensity_pct": 65}], ""
        if any(token in f" {command} " for token in (" mitte ", " gerade ", " grade ", " zentrum ")):
            return "Mitte.", [{"action": "move", "yaw_target_pct": 0, "pitch_target_pct": DEFAULT_IDLE_PITCH_PCT}, {"action": "face", "emotion": "neutral", "intensity_pct": 60}], ""

    status_reply = direct_status_reply_from_transcript(command, status)
    if status_reply:
        return status_reply

    return None


def direct_system_command_from_transcript(text: str) -> tuple[str, list[dict[str, Any]], str] | None:
    result = direct_local_command_from_transcript(text)
    if result is None:
        return None
    display_text, actions, post_tts_system_action = result
    if post_tts_system_action:
        return result
    if all(action_name(action) == "system" for action in actions):
        return result
    return None


def split_post_tts_system_actions(actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    kept: list[dict[str, Any]] = []
    post_tts_system_action = ""
    for action in actions:
        if not isinstance(action, dict):
            continue
        name = action_name(action)
        system_action = optional_string(action.get("system_action") or action.get("command"))
        if name != "system":
            system_action = "shutdown" if name == "power_off" else name
        system_action = (system_action or "").strip().lower().replace("-", "_")
        if system_action in POST_TTS_SYSTEM_ACTIONS:
            post_tts_system_action = "shutdown" if system_action == "power_off" else system_action
            continue
        kept.append(action)
    return kept, post_tts_system_action


def reminder_store_path(config: BridgeConfig) -> Path:
    return Path(config.reminders.store_path).expanduser()


def read_reminder_store(config: BridgeConfig) -> dict[str, Any]:
    path = reminder_store_path(config)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "reminders": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"reminder store is invalid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"reminder store must be a JSON object: {path}")
    reminders = data.get("reminders")
    if not isinstance(reminders, list):
        data["reminders"] = []
    return data


def write_reminder_store(config: BridgeConfig, store: dict[str, Any]) -> None:
    path = reminder_store_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def parse_due_at(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = optional_string(value)
    if not text:
        raise ConfigError("reminder due_at must be an ISO timestamp or epoch seconds")
    normalized = text.replace("Z", "+00:00")
    try:
        due = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ConfigError("reminder due_at must be an ISO timestamp") from exc
    if due.tzinfo is None:
        due = due.replace(tzinfo=dt.timezone.utc)
    return due.timestamp()


def reminder_due_ts(action: dict[str, Any], now_ts: float | None = None) -> float:
    now = time.time() if now_ts is None else now_ts
    for key, scale in (("delay_s", 1.0), ("delay_seconds", 1.0), ("in_s", 1.0), ("delay_ms", 0.001)):
        if action.get(key) is not None:
            delay = float(parse_int_value(action.get(key), 0, f"reminder.{key}")) * scale
            if delay < 0:
                raise ConfigError("reminder delay must be >= 0")
            return now + delay
    if action.get("due_at") is not None:
        return parse_due_at(action.get("due_at"))
    if action.get("at") is not None:
        return parse_due_at(action.get("at"))
    raise ConfigError("reminder action needs delay_s, delay_ms, or due_at")


def build_reminder(action: dict[str, Any], pair: PairConfig, request_id: str, now_ts: float | None = None) -> dict[str, Any]:
    text = optional_string(action.get("text") or action.get("message") or action.get("title"))
    if not text:
        raise ConfigError("reminder action needs text")
    due_ts = reminder_due_ts(action, now_ts)
    created_ts = time.time() if now_ts is None else now_ts
    reminder_id = optional_string(action.get("reminder_id") or action.get("id")) or f"rem-{uuid.uuid4().hex[:12]}"
    return {
        "id": reminder_id,
        "pair_id": pair.pair_id,
        "stackchan_id": pair.stackchan_id,
        "request_id": request_id,
        "text": text[:500],
        "due_ts": round(due_ts, 3),
        "created_ts": round(created_ts, 3),
        "source": optional_string(action.get("source")) or "hermes",
        "status": "pending",
    }


def add_reminder(config: BridgeConfig, reminder: dict[str, Any]) -> dict[str, Any]:
    with REMINDER_STORE_LOCK:
        store = read_reminder_store(config)
        reminders = [item for item in store.get("reminders", []) if isinstance(item, dict)]
        reminders.append(reminder)
        store = {"schema_version": SCHEMA_VERSION, "updated_at": time.time(), "reminders": reminders}
        write_reminder_store(config, store)
    return reminder


def schedule_reminders_from_actions(
    config: BridgeConfig,
    pair: PairConfig,
    actions: list[dict[str, Any]],
    request_id_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    dispatch_actions: list[dict[str, Any]] = []
    scheduled: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            dispatch_actions.append(action)
            continue
        if action_name(action) not in REMINDER_ACTIONS:
            dispatch_actions.append(action)
            continue
        try:
            scheduled.append(add_reminder(config, build_reminder(action, pair, f"{request_id_prefix}-{index:02d}")))
        except ConfigError as exc:
            errors.append(str(exc))
    return dispatch_actions, scheduled, errors


def due_reminders(config: BridgeConfig, pair: PairConfig, now_ts: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now_ts is None else now_ts
    fired: list[dict[str, Any]] = []
    with REMINDER_STORE_LOCK:
        store = read_reminder_store(config)
        reminders = [item for item in store.get("reminders", []) if isinstance(item, dict)]
        for reminder in reminders:
            if reminder.get("pair_id") != pair.pair_id or reminder.get("status", "pending") != "pending":
                continue
            try:
                due_ts = float(reminder.get("due_ts", 0))
            except (TypeError, ValueError):
                continue
            if due_ts <= now:
                reminder["status"] = "fired"
                reminder["fired_ts"] = round(now, 3)
                fired.append(dict(reminder))
        if fired:
            store["updated_at"] = now
            store["reminders"] = reminders
            write_reminder_store(config, store)
    return fired


def pending_reminders(config: BridgeConfig, pair_id: str | None = None) -> list[dict[str, Any]]:
    with REMINDER_STORE_LOCK:
        reminders = [item for item in read_reminder_store(config).get("reminders", []) if isinstance(item, dict)]
    result = [item for item in reminders if item.get("status", "pending") == "pending"]
    if pair_id:
        result = [item for item in result if item.get("pair_id") == pair_id]
    return sorted(result, key=lambda item: float(item.get("due_ts", 0)))


def reminder_actions(reminder: dict[str, Any], display_duration_ms: int) -> list[dict[str, Any]]:
    text = optional_string(reminder.get("text")) or "Erinnerung."
    return [
        {"action": "system", "system_action": "display_wake"},
        {"action": "face", "emotion": "speaking", "intensity_pct": 72},
        {"action": "display", "text": f"ERINNERUNG: {text}", "duration_ms": display_duration_ms},
    ]


def tts_public_url(config: BridgeConfig, tts_path: str) -> str:
    if not tts_path:
        return ""
    base_url = config.speech.bridge_public_url
    if not base_url:
        raise ConfigError("H2S_BRIDGE_PUBLIC_URL or H2S_BRIDGE_AUDIO_URL is required for scheduled TTS playback")
    return f"{base_url}{tts_path}"


def reminder_actions_with_tts(config: BridgeConfig, reminder: dict[str, Any]) -> list[dict[str, Any]]:
    text = optional_string(reminder.get("text")) or "Erinnerung."
    spoken_text = f"Erinnerung: {text}"
    request_id = optional_string(reminder.get("id")) or uuid.uuid4().hex
    tts_path = make_tts_wav(spoken_text, config.speech, f"reminder-{request_id}")
    actions = reminder_actions(reminder, config.reminders.display_duration_ms)
    actions.append({"action": "audio", "audio_action": "play_tts_url", "url": tts_public_url(config, tts_path)})
    return actions


def status_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "ja", "on"}:
            return True
        if normalized in {"0", "false", "no", "nein", "off"}:
            return False
    return None


def status_int_at(status: dict[str, Any] | None, path: str, default: int = 0) -> int:
    if not isinstance(status, dict):
        return default
    value = nested_status_value(status, path)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(round(float(value.strip())))
        except ValueError:
            return default
    return default


def battery_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    pct = status.get("battery_pct")
    if not isinstance(pct, int):
        pct = None
    charging = status_bool(status.get("battery_charging", status.get("charging")))
    discharging = status_bool(status.get("battery_discharging"))
    external_power = status_bool(status.get("external_power", status.get("usb_power")))
    known = status_bool(status.get("battery_known"))
    if known is None:
        known = pct is not None and pct >= 0
    if external_power is None:
        external_power = bool(charging)
    return {
        "known": bool(known),
        "pct": pct if pct is not None and pct >= 0 else None,
        "charging": bool(charging),
        "discharging": bool(discharging),
        "external_power": bool(external_power),
    }


def format_battery_text(current: dict[str, Any]) -> str:
    pct = current.get("pct")
    pct_text = f"{pct}%" if isinstance(pct, int) else "?"
    if current.get("charging"):
        state = "LAEDT"
    elif current.get("external_power"):
        state = "AM STROM"
    else:
        state = "ENTLAEDT"
    return f"AKKU {pct_text} {state}"


def face_snapshot(status: dict[str, Any]) -> dict[str, Any] | None:
    face = status.get("face")
    if not isinstance(face, dict):
        return None
    emotion = optional_string(face.get("emotion")) or "neutral"
    if emotion in {"battery", "charging", "battery_low"}:
        emotion = "neutral"
    intensity = face.get("intensity_pct")
    if not isinstance(intensity, int):
        intensity = 60
    return {
        "action": "face",
        "emotion": emotion,
        "intensity_pct": clamp_int(intensity, 0, 100),
    }


def build_power_change_actions(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[dict[str, Any]]:
    if previous is None or not current["known"]:
        return []
    if previous.get("external_power") == current.get("external_power"):
        return []

    pct = current["pct"]
    if current["external_power"]:
        return [
            {"action": "display", "text": format_battery_text(current), "duration_ms": POWER_DISPLAY_DURATION_MS},
        ]

    if current["discharging"] or previous.get("external_power"):
        return [
            {"action": "display", "text": format_battery_text(current), "duration_ms": POWER_DISPLAY_DURATION_MS},
        ]

    return []


def build_power_followup_actions(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[dict[str, Any]]:
    if previous is None or not current["known"]:
        return []
    if previous.get("external_power") == current.get("external_power"):
        return []

    if current["external_power"]:
        return [
            {"action": "face", "emotion": "happy", "intensity_pct": 84},
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 36,
                "points": [
                    {"yaw_pct": 0, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 180, "speed_pct": 35},
                    {"yaw_pct": 0, "pitch_pct": clamp_int(DEFAULT_IDLE_PITCH_PCT + 26, 0, 100), "duration_ms": 650, "speed_pct": 35},
                    {"yaw_pct": 0, "pitch_pct": clamp_int(DEFAULT_IDLE_PITCH_PCT + 14, 0, 100), "duration_ms": 420, "speed_pct": 30},
                ],
            },
        ]

    if current["discharging"] or previous.get("external_power"):
        return [
            {"action": "face", "emotion": "neutral", "intensity_pct": 58},
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 42,
                "points": [
                    {"yaw_pct": 0, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 120, "speed_pct": 38},
                    {"yaw_pct": -14, "pitch_pct": clamp_int(DEFAULT_IDLE_PITCH_PCT - 8, 0, 100), "duration_ms": 280, "speed_pct": 45},
                    {"yaw_pct": 14, "pitch_pct": clamp_int(DEFAULT_IDLE_PITCH_PCT - 12, 0, 100), "duration_ms": 280, "speed_pct": 45},
                    {"yaw_pct": -8, "pitch_pct": clamp_int(DEFAULT_IDLE_PITCH_PCT - 16, 0, 100), "duration_ms": 260, "speed_pct": 42},
                    {"yaw_pct": 0, "pitch_pct": clamp_int(DEFAULT_IDLE_PITCH_PCT - 24, 0, 100), "duration_ms": 600, "speed_pct": 34},
                ],
            },
        ]

    return []


def status_allows_life_animation(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    if status_bool(status.get("display_sleeping")):
        return False
    if status_bool(status.get("recording")) or status_bool(status.get("speaking")):
        return False
    if status_bool(nested_status_value(status, "head.motion_active")):
        return False
    ui_mode = nested_status_value(status, "ui.mode")
    if isinstance(ui_mode, str) and ui_mode != "face":
        return False
    emotion = nested_status_value(status, "face.emotion")
    if emotion in {"sleep", "error", "battery", "charging", "battery_low", "speaking"}:
        return False
    return True


HUMAN_ACTIVITY_EVENTS = {
    "interaction",
    "touch_down",
    "touch_up",
    "wakeword_detected",
    "recording_started",
    "recording_stopped",
    "recording_error",
    "audio_upload_started",
    "audio_upload_done",
    "audio_upload_failed",
    "photo_upload_started",
    "photo_upload_done",
    "photo_upload_failed",
}
IDLE_SLEEP_IGNORED_REQUEST_PREFIXES = (
    "life-",
    "idle-sleep-",
    "settings-",
)


def request_id_counts_as_idle_activity(request_id: str | None) -> bool:
    if not request_id:
        return True
    return not request_id.startswith(IDLE_SLEEP_IGNORED_REQUEST_PREFIXES)


def command_requests_display_sleep(pair: PairConfig, topic: str, payload: dict[str, Any]) -> bool:
    return (
        (topic == pair.device_topic and payload.get("display_sleep") is True and not payload.get("display_wake"))
        or (topic == pair.system_topic and payload.get("action") == "display_sleep")
    )


def command_counts_as_idle_activity(pair: PairConfig, topic: str, payload: dict[str, Any]) -> bool:
    if not topic.startswith(f"{pair.mqtt_prefix}/cmd/"):
        return False
    if not request_id_counts_as_idle_activity(optional_string(payload.get("request_id"))):
        return False
    if command_requests_display_sleep(pair, topic, payload):
        return False
    return True


def status_is_recording(status: dict[str, Any]) -> bool:
    return (
        status_bool(status.get("recording")) is True
        or status_bool(nested_status_value(status, "audio.recording")) is True
    )


def status_is_busy(status: dict[str, Any]) -> bool:
    return (
        status_is_recording(status)
        or status_bool(status.get("speaking")) is True
        or status_bool(nested_status_value(status, "head.motion_active")) is True
    )


def sensor_status_is_sideways(status: dict[str, Any]) -> bool:
    ax = status_int_at(status, "sensors.imu.accel_mg.x")
    ay = status_int_at(status, "sensors.imu.accel_mg.y")
    return abs(ax) >= SENSOR_SIDE_AXIS_MG and abs(ay) <= SENSOR_SIDE_UPRIGHT_MAX_MG


def sensor_status_is_face_down(status: dict[str, Any]) -> bool:
    ax = status_int_at(status, "sensors.imu.accel_mg.x")
    ay = status_int_at(status, "sensors.imu.accel_mg.y")
    az = status_int_at(status, "sensors.imu.accel_mg.z")
    return (
        abs(az) >= SENSOR_FACE_DOWN_AXIS_MG
        and abs(ax) <= SENSOR_FACE_DOWN_OTHER_MAX_MG
        and abs(ay) <= SENSOR_FACE_DOWN_OTHER_MAX_MG
    )


def sensor_face_down_tantrum_actions() -> list[dict[str, Any]]:
    return [
        {"action": "led", "mode": "party", "r": 255, "g": 40, "b": 180},
        {"action": "face", "emotion": "face_down", "intensity_pct": 92},
        {"action": "display", "mode": "text", "text": "NICHT AUFS GESICHT!", "duration_ms": 2200},
        {
            "action": "motion",
            "curve": "spline",
            "speed_pct": 80,
            "points": [
                {"yaw_pct": -34, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 7, "duration_ms": 110},
                {"yaw_pct": 34, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 3, "duration_ms": 115},
                {"yaw_pct": -26, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 9, "duration_ms": 105},
                {"yaw_pct": 22, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 1, "duration_ms": 115},
                {"yaw_pct": 0, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 5, "duration_ms": 130},
            ],
        },
    ]


def sensor_shake_motion_action() -> dict[str, Any]:
    return {
        "action": "motion",
        "curve": "spline",
        "speed_pct": 28,
        "points": [
            {"yaw_pct": -8, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 1, "duration_ms": 220},
            {"yaw_pct": 8, "pitch_pct": DEFAULT_IDLE_PITCH_PCT - 1, "duration_ms": 240},
            {"yaw_pct": -5, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 220},
            {"yaw_pct": 0, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 280},
        ],
    }


def merge_sensor_event_status(
    latest_status: dict[str, Any] | None,
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(latest_status or {})
    for key in ("recording", "speaking", "display_sleeping"):
        if key in event_payload:
            merged[key] = event_payload[key]
    if "audio" in event_payload and isinstance(event_payload["audio"], dict):
        merged["audio"] = event_payload["audio"]
    if "head" in event_payload and isinstance(event_payload["head"], dict):
        merged["head"] = event_payload["head"]
    if "sensors" in event_payload and isinstance(event_payload["sensors"], dict):
        merged["sensors"] = event_payload["sensors"]
    return merged


def build_sensor_reaction_actions(
    status: dict[str, Any] | None,
    state: SensorReactionState,
    now_s: float | None = None,
    source_hint: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(status, dict):
        return [], []

    now_s = time.monotonic() if now_s is None else now_s
    actions: list[dict[str, Any]] = []
    reasons: list[str] = []
    display_sleeping = status_bool(status.get("display_sleeping")) is True
    recording = status_is_recording(status)
    head_motion_active = status_bool(nested_status_value(status, "head.motion_active")) is True
    source_hint = source_hint.strip().lower()

    def maybe_wake(reason: str) -> None:
        if display_sleeping and now_s - state.last_wake_at >= SENSOR_WAKE_COOLDOWN_S:
            actions.append({"action": "system", "system_action": "display_wake"})
            reasons.append(f"wake:{reason}")
            state.last_wake_at = now_s

    if recording:
        return actions, reasons
    if head_motion_active:
        return actions, reasons

    proximity_ready = status_bool(nested_status_value(status, "sensors.ltr553.ready")) is True
    proximity_delta = status_int_at(status, "sensors.ltr553.proximity_delta", 0)
    proximity_raw = status_int_at(status, "sensors.ltr553.proximity_raw", 0)
    proximity_near = status_bool(nested_status_value(status, "sensors.ltr553.near")) is True
    force_proximity = source_hint == "proximity"
    near_signal = proximity_ready and (
        force_proximity
        or proximity_near
        or proximity_delta >= SENSOR_PROXIMITY_ON_DELTA
        or proximity_raw >= SENSOR_PROXIMITY_ON_RAW
    )
    clear_signal = (
        not proximity_near
        and proximity_delta <= SENSOR_PROXIMITY_OFF_DELTA
        and proximity_raw <= SENSOR_PROXIMITY_OFF_RAW
    )
    if near_signal:
        if force_proximity:
            state.proximity_seen_count = max(state.proximity_seen_count, SENSOR_PROXIMITY_STABLE_SAMPLES - 1)
        state.proximity_seen_count += 1
        state.proximity_clear_count = 0
    elif clear_signal:
        state.proximity_clear_count += 1
        state.proximity_seen_count = 0

    if (
        proximity_ready
        and not state.proximity_active
        and state.proximity_seen_count >= SENSOR_PROXIMITY_STABLE_SAMPLES
        and now_s - state.last_proximity_at >= SENSOR_REACTION_COOLDOWN_S
    ):
        current_pitch = status_int_at(status, "head.tilt_pct", DEFAULT_IDLE_PITCH_PCT)
        if current_pitch <= PITCH_TARGET_MIN_PCT + 4:
            current_pitch = DEFAULT_IDLE_PITCH_PCT
        state.proximity_restore_pitch_pct = clamp_int(current_pitch, PITCH_TARGET_MIN_PCT, PITCH_TARGET_MAX_PCT)
        target_pitch = clamp_int(
            state.proximity_restore_pitch_pct - SENSOR_PROXIMITY_HEAD_DROP_PCT,
            PITCH_TARGET_MIN_PCT,
            PITCH_TARGET_MAX_PCT,
        )
        maybe_wake("proximity")
        actions.extend([
            {"action": "face", "emotion": "glance_down", "intensity_pct": 78},
            {"action": "move", "pitch_target_pct": target_pitch},
        ])
        reasons.append("proximity_near")
        state.proximity_active = True
        state.last_proximity_at = now_s

    if (
        state.proximity_active
        and state.proximity_clear_count >= SENSOR_PROXIMITY_CLEAR_SAMPLES
        and now_s - state.last_proximity_at >= SENSOR_REACTION_COOLDOWN_S
    ):
        actions.append({"action": "move", "pitch_target_pct": state.proximity_restore_pitch_pct})
        reasons.append("proximity_clear")
        state.proximity_active = False
        state.last_proximity_at = now_s

    imu_ready = status_bool(nested_status_value(status, "sensors.imu.ready")) is True
    motion_score = status_int_at(status, "sensors.imu.motion_score_pct", 0)
    imu_motion = status_bool(nested_status_value(status, "sensors.imu.motion_active")) is True
    face_down = imu_ready and sensor_status_is_face_down(status)
    sideways = imu_ready and not face_down and sensor_status_is_sideways(status)
    imu_event = source_hint in {"imu", "orientation"}
    if source_hint in {"imu", "orientation"} and face_down:
        state.face_down_seen_count = max(state.face_down_seen_count, SENSOR_FACE_DOWN_STABLE_SAMPLES - 1)
    if source_hint in {"imu", "orientation"} and sideways:
        state.side_seen_count = max(state.side_seen_count, SENSOR_SIDE_STABLE_SAMPLES - 1)
    if source_hint == "orientation" and not sideways and not face_down:
        state.upright_seen_count = max(state.upright_seen_count, SENSOR_SIDE_STABLE_SAMPLES - 1)
    if (
        imu_event
        and imu_ready
        and (imu_motion or source_hint == "imu")
        and motion_score >= SENSOR_SHAKE_SCORE_THRESHOLD
        and not sideways
        and now_s - state.last_shake_at >= SENSOR_SHAKE_COOLDOWN_S
    ):
        maybe_wake("shake")
        actions.append({"action": "face", "emotion": "surprise_pop", "intensity_pct": 90})
        reasons.append("shake")
        state.last_shake_at = now_s

    if imu_event:
        if face_down:
            state.face_down_seen_count += 1
            state.side_seen_count = 0
            state.upright_seen_count = 0
        elif sideways:
            state.side_seen_count += 1
            state.upright_seen_count = 0
            state.face_down_seen_count = 0
        else:
            state.upright_seen_count += 1
            state.side_seen_count = 0
            state.face_down_seen_count = 0

        if (
            face_down
            and state.face_down_seen_count >= SENSOR_FACE_DOWN_STABLE_SAMPLES
            and (
                not state.face_down_active
                or now_s - state.last_face_down_at >= SENSOR_FACE_DOWN_REPEAT_S
            )
        ):
            maybe_wake("face_down")
            actions.extend(sensor_face_down_tantrum_actions())
            if not state.face_down_active:
                actions.append({"action": "local_tts", "text": SENSOR_FACE_DOWN_TEXT})
            reasons.append("face_down")
            state.face_down_active = True
            state.last_face_down_at = now_s

        if (
            sideways
            and not state.side_active
            and state.side_seen_count >= SENSOR_SIDE_STABLE_SAMPLES
            and now_s - state.last_side_at >= SENSOR_REACTION_COOLDOWN_S
        ):
            maybe_wake("sideways")
            actions.extend([
                {"action": "led", "mode": "blink", "r": 255, "g": 0, "b": 0},
                {"action": "face", "emotion": "help", "intensity_pct": 94},
                {"action": "display", "mode": "text", "text": "HILFE!", "duration_ms": 4500},
                {"action": "local_tts", "text": SENSOR_SIDE_HELP_TEXT},
            ])
            reasons.append("sideways")
            state.side_active = True
            state.last_side_at = now_s

        if (
            source_hint == "orientation"
            and not sideways
            and state.upright_seen_count >= SENSOR_SIDE_STABLE_SAMPLES
            and now_s - state.last_side_at >= SENSOR_REACTION_COOLDOWN_S
        ):
            actions.append({"action": "led", "mode": "off", "r": 0, "g": 0, "b": 0})
            if state.side_active or state.face_down_active:
                actions.extend([
                    {"action": "face", "emotion": "thankful", "intensity_pct": 82},
                    {
                        "action": "motion",
                        "curve": "spline",
                        "speed_pct": 72,
                        "points": [
                            {"yaw_pct": -24, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 5, "duration_ms": 130},
                            {"yaw_pct": 24, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 1, "duration_ms": 150},
                            {"yaw_pct": -14, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 7, "duration_ms": 130},
                            {"yaw_pct": 14, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 3, "duration_ms": 130},
                            {"yaw_pct": 0, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 170},
                        ],
                    },
                    {"action": "local_tts", "text": SENSOR_SIDE_THANKS_TEXT},
                ])
            reasons.append("upright")
            state.side_active = False
            state.face_down_active = False
            state.last_side_at = now_s

    return actions, reasons


def build_idle_sleep_payload(request_id: str | None = None) -> dict[str, Any]:
    return with_request_id({"display_sleep": True}, request_id)


def pause_life_animation(pair_id: str, seconds: float, reason: str) -> None:
    until = time.monotonic() + max(0.0, seconds)
    with LIFE_PAUSE_LOCK:
        LIFE_PAUSED_UNTIL[pair_id] = max(LIFE_PAUSED_UNTIL.get(pair_id, 0.0), until)
    print(
        f"[{time.strftime('%H:%M:%S')}] [bridge] life animation paused for {pair_id} "
        f"{seconds:.1f}s: {reason}",
        flush=True,
    )


def life_animation_paused(pair_id: str) -> bool:
    with LIFE_PAUSE_LOCK:
        until = LIFE_PAUSED_UNTIL.get(pair_id, 0.0)
        if until <= time.monotonic():
            LIFE_PAUSED_UNTIL.pop(pair_id, None)
            return False
        return True


TRANSIENT_FACE_EMOTIONS = {
    "blink",
    "soft_blink",
    "glance_left",
    "glance_right",
    "glance_up",
    "glance_down",
    "mouth_smile",
    "mouth_tiny",
    "mouth_wiggle",
    "look_left",
    "look_right",
    "look_up",
    "look_down",
    "breathe",
    "deep_breathe",
    "micro_sleep",
    "question",
    "wink_left",
    "wink_right",
    "surprise_pop",
    "grumble",
    "yawn",
    "happy_squint",
    "cross_eyes",
    "eye_swap",
    "derp",
    "boing_eyes",
    "suspicious_squint",
    "confused_dots",
    "mouth_pop",
    "smirk_slide",
    "silent_giggle",
    "sleepy_snapback",
}


def current_face_action(status: dict[str, Any] | None, default_intensity: int = 60) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {"action": "face", "emotion": "neutral", "intensity_pct": default_intensity}
    emotion = optional_string(nested_status_value(status, "face.emotion")) or "neutral"
    if emotion in TRANSIENT_FACE_EMOTIONS:
        emotion = "neutral"
    intensity = nested_status_value(status, "face.intensity_pct")
    if not isinstance(intensity, int):
        intensity = default_intensity
    return {"action": "face", "emotion": emotion, "intensity_pct": clamp_int(intensity, 35, 90)}


def life_face(emotion: str, intensity_pct: int, variant: str) -> dict[str, Any]:
    return {
        "action": "face",
        "emotion": emotion,
        "intensity_pct": clamp_int(intensity_pct, 35, 95),
        "variant": variant,
    }


def life_motion(points: list[dict[str, int]], speed_pct: int = 18, curve: str = "spline", variant: str = "") -> dict[str, Any]:
    return {
        "action": "motion",
        "curve": curve,
        "speed_pct": clamp_int(speed_pct, 1, 100),
        "points": points,
        "variant": variant,
    }


def motion_action_duration_ms(action: dict[str, Any]) -> int:
    if action_name(action) != "motion":
        return 0
    points = action.get("points")
    if not isinstance(points, list):
        return 0
    total = 0
    for point in points:
        if not isinstance(point, dict):
            continue
        total += clamp_int(parse_int_value(point.get("duration_ms"), 0, "motion.duration_ms"), 0, 5000)
        total += clamp_int(parse_int_value(point.get("hold_ms"), 0, "motion.hold_ms"), 0, 5000)
    return total


def life_action_settle_delay_s(action: dict[str, Any]) -> float:
    duration_ms = motion_action_duration_ms(action)
    if duration_ms <= 0:
        return 0.0
    return (duration_ms + 350) / 1000.0


def life_motion_size(action: dict[str, Any]) -> str:
    if action_name(action) != "motion":
        return "none"
    points = action.get("points")
    if not isinstance(points, list):
        return "small"
    variant = optional_string(action.get("variant"))
    max_yaw = 0
    max_pitch_delta = 0
    for point in points:
        if not isinstance(point, dict):
            continue
        yaw = parse_int_value(point.get("yaw_pct"), DEFAULT_IDLE_YAW_PCT, "motion.yaw_pct")
        pitch = parse_int_value(point.get("pitch_pct"), DEFAULT_IDLE_PITCH_PCT, "motion.pitch_pct")
        max_yaw = max(max_yaw, abs(yaw - DEFAULT_IDLE_YAW_PCT))
        max_pitch_delta = max(max_pitch_delta, abs(pitch - DEFAULT_IDLE_PITCH_PCT))
    if variant in {"desk_spin", "look_behind"} or max_yaw >= 35 or max_pitch_delta >= 14:
        return "big"
    return "small"


class LifeMotionLimiter:
    def __init__(self, small_gap_s: float, big_gap_s: float) -> None:
        self.small_gap_s = max(0.0, float(small_gap_s))
        self.big_gap_s = max(0.0, float(big_gap_s))
        self.last_small_s = 0.0
        self.last_big_s = 0.0

    def allow(self, action: dict[str, Any], now_s: float | None = None) -> bool:
        size = life_motion_size(action)
        if size == "none":
            return True
        now = time.monotonic() if now_s is None else now_s
        if size == "big":
            if self.last_big_s and now - self.last_big_s < self.big_gap_s:
                return False
            self.last_big_s = now
            return True
        if self.last_small_s and now - self.last_small_s < self.small_gap_s:
            return False
        self.last_small_s = now
        return True


def gaze_for_direction(direction: str) -> str:
    return {
        "left": "glance_left",
        "right": "glance_right",
        "up": "glance_up",
        "down": "glance_down",
        "up_left": "glance_up",
        "up_right": "glance_up",
        "down_left": "glance_down",
        "down_right": "glance_down",
    }.get(direction, "glance_left")


def motion_point(yaw_pct: int, pitch_pct: int, duration_ms: int, speed_pct: int, hold_ms: int = 0) -> dict[str, int]:
    point = {
        "yaw_pct": clamp_int(yaw_pct, -90, 90),
        "pitch_pct": clamp_int(pitch_pct, PITCH_TARGET_MIN_PCT, PITCH_TARGET_MAX_PCT),
        "duration_ms": clamp_int(duration_ms, 120, 2800),
        "speed_pct": clamp_int(speed_pct, 6, 45),
    }
    if hold_ms:
        point["hold_ms"] = clamp_int(hold_ms, 0, 1600)
    return point


def idle_motion_point(yaw_offset_pct: int, pitch_offset_pct: int, duration_ms: int, speed_pct: int, hold_ms: int = 0) -> dict[str, int]:
    return motion_point(
        DEFAULT_IDLE_YAW_PCT + yaw_offset_pct,
        DEFAULT_IDLE_PITCH_PCT + pitch_offset_pct,
        duration_ms,
        speed_pct,
        hold_ms,
    )


def build_subtle_life_motion(rng: random.Random) -> tuple[str, dict[str, Any]]:
    glance = rng.choice(["glance_left", "glance_right", "glance_up", "glance_up", "glance_down"])
    yaw = -5 if glance == "glance_left" else 5 if glance == "glance_right" else rng.choice([-2, 2])
    pitch = 4 if glance == "glance_up" else -4 if glance == "glance_down" else rng.choice([-2, 2])
    return glance, {
        "action": "motion",
        "curve": "spline",
        "speed_pct": 12,
        "points": [
            idle_motion_point(yaw, pitch, 1400, 12, 350),
            idle_motion_point(0, 0, 1800, 10),
        ],
    }


def build_big_life_sequence(rng: random.Random, base_intensity: int, mood: str) -> list[tuple[int, dict[str, Any]]]:
    pattern = rng.choices(["horizontal", "vertical", "diagonal"], weights=[5, 3, 2], k=1)[0]
    if pattern == "vertical":
        up_first = rng.choice([True, False])
        first_yaw = rng.choice([-10, 10])
        second_yaw = -first_yaw
        first_pitch = DEFAULT_IDLE_PITCH_PCT if up_first else DEFAULT_IDLE_PITCH_PCT - 20
        second_pitch = DEFAULT_IDLE_PITCH_PCT - 18 if up_first else DEFAULT_IDLE_PITCH_PCT
        first_glance = "glance_up" if up_first else "glance_down"
        second_glance = "glance_down" if up_first else "glance_up"
        center_glance = rng.choice(["glance_left", "glance_right"])
        first_speed = 24
        second_speed = 22
    elif pattern == "diagonal":
        left_first = rng.choice([True, False])
        first_yaw = -58 if left_first else 58
        second_yaw = 46 if left_first else -46
        first_pitch = DEFAULT_IDLE_PITCH_PCT
        second_pitch = DEFAULT_IDLE_PITCH_PCT + rng.choice([-16, -20])
        first_glance = "glance_up"
        second_glance = "glance_down"
        center_glance = "glance_right" if left_first else "glance_left"
        first_speed = 30
        second_speed = 28
    else:
        left_first = rng.choice([True, False])
        first_yaw = -75 if left_first else 75
        second_yaw = 75 if left_first else -75
        first_pitch = DEFAULT_IDLE_PITCH_PCT + 4
        second_pitch = DEFAULT_IDLE_PITCH_PCT + 6
        first_glance = "glance_left" if left_first else "glance_right"
        second_glance = "glance_right" if left_first else "glance_left"
        center_glance = first_glance
        first_speed = 34
        second_speed = 32

    return [
        (0, {"action": "face", "emotion": first_glance, "intensity_pct": base_intensity}),
        (
            160,
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": first_speed,
                "points": [
                    {
                        "yaw_pct": first_yaw,
                        "pitch_pct": first_pitch,
                        "duration_ms": 900,
                        "speed_pct": first_speed,
                        "hold_ms": 300,
                    }
                ],
            },
        ),
        (650, {"action": "face", "emotion": first_glance, "intensity_pct": base_intensity}),
        (600, {"action": "face", "emotion": second_glance, "intensity_pct": base_intensity}),
        (
            0,
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": second_speed,
                "points": [
                    {
                        "yaw_pct": second_yaw,
                        "pitch_pct": second_pitch,
                        "duration_ms": 1600,
                        "speed_pct": second_speed,
                        "hold_ms": 260,
                    }
                ],
            },
        ),
        (850, {"action": "face", "emotion": second_glance, "intensity_pct": base_intensity}),
        (900, {"action": "face", "emotion": center_glance, "intensity_pct": base_intensity}),
        (
            0,
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 22,
                "points": [motion_point(DEFAULT_IDLE_YAW_PCT, DEFAULT_IDLE_PITCH_PCT, 1200, 22)],
            },
        ),
        (1500, {"action": "face", "emotion": mood, "intensity_pct": base_intensity}),
    ]


def build_named_life_sequence(name: str, rng: random.Random, base_intensity: int, mood: str) -> LifeSequence:
    low = clamp_int(base_intensity - 5, 35, 90)
    high = clamp_int(base_intensity + 12, 35, 95)

    if name == "double_blink":
        return [(0, life_face("soft_blink", low, name)), (360, life_face("soft_blink", low, name))]
    if name == "lazy_blink":
        return [(0, life_face("breathe", low, name)), (360, life_face("soft_blink", low, name))]
    if name == "suspicious_left":
        return [(0, life_face("glance_left", base_intensity, name)), (900, life_face("mouth_tiny", base_intensity, name))]
    if name == "suspicious_right":
        return [(0, life_face("glance_right", base_intensity, name)), (900, life_face("mouth_tiny", base_intensity, name))]
    if name == "tiny_smile":
        return [(0, life_face("mouth_tiny", high, name))]
    if name == "look_up_think":
        return [
            (0, life_face("glance_up", base_intensity, name)),
            (180, life_motion([idle_motion_point(0, 18, 1100, 14, 260), idle_motion_point(0, 0, 1200, 12)], 14, variant=name)),
            (520, life_face("question", base_intensity, name)),
            (820, life_face(mood, base_intensity, name)),
        ]
    if name == "look_down_table":
        return [
            (0, life_face("glance_down", base_intensity, name)),
            (180, life_motion([idle_motion_point(0, -18, 1100, 14, 260), idle_motion_point(0, 0, 1200, 12)], 14, variant=name)),
            (700, life_face("mouth_tiny", low, name)),
        ]
    if name == "wink_left":
        return [(0, life_face("wink_left", high, name))]
    if name == "wink_right":
        return [(0, life_face("wink_right", high, name))]
    if name == "deep_breathe":
        return [(0, life_face("deep_breathe", base_intensity, name))]
    if name == "mouth_wiggle":
        return [(0, life_face("mouth_wiggle", base_intensity, name))]
    if name == "micro_sleep":
        return [(0, life_face("micro_sleep", low, name))]
    if name == "surprise_pop":
        return [(0, life_face("surprise_pop", high, name)), (720, life_face("soft_blink", base_intensity, name))]
    if name == "cheeky_grin":
        side = rng.choice(["glance_left", "glance_right"])
        return [(0, life_face("mischievous", high, name)), (520, life_face("wink_right" if side == "glance_left" else "wink_left", high, name))]
    if name == "question_glance":
        return [
            (0, life_face("glance_up", base_intensity, name)),
            (440, life_face("question", base_intensity, name)),
            (820, life_face(mood, base_intensity, name)),
        ]
    if name == "nervous_flick":
        return [(0, life_face("glance_left", base_intensity, name)), (180, life_face("glance_right", base_intensity, name)), (180, life_face("soft_blink", low, name))]
    if name == "happy_squint":
        return [(0, life_face("super_happy", high, name))]
    if name == "grumble_mouth":
        return [(0, life_face("grumble", low, name))]
    if name == "scanner_eyes":
        return [(0, life_face("glance_left", base_intensity, name)), (520, life_face("glance_right", base_intensity, name)), (520, life_face("glance_left", base_intensity, name))]
    if name == "yawn_hint":
        return [(0, life_face("yawn", low, name))]
    if name == "look_behind":
        return [
            (0, life_face("glance_up", base_intensity, name)),
            (180, life_motion([
                idle_motion_point(rng.choice([-75, 75]), 0, 1000, 32, 300),
                idle_motion_point(0, 0, 1000, 24),
            ], 32, variant=name)),
            (1450, life_face(mood, base_intensity, name)),
        ]
    if name == "desk_spin":
        side = rng.choice([-1, 1])
        return [
            (0, life_face("surprise_pop", high, name)),
            (
                140,
                life_motion(
                    [
                        motion_point(65 * side, DEFAULT_IDLE_PITCH_PCT, 480, 30),
                        motion_point(56 * side, DEFAULT_IDLE_PITCH_PCT + 6, 130, 32),
                        motion_point(30 * side, DEFAULT_IDLE_PITCH_PCT + 11, 130, 32),
                        motion_point(-4 * side, DEFAULT_IDLE_PITCH_PCT + 12, 130, 32),
                        motion_point(-37 * side, DEFAULT_IDLE_PITCH_PCT + 10, 130, 32),
                        motion_point(-60 * side, DEFAULT_IDLE_PITCH_PCT + 5, 130, 32),
                        motion_point(-64 * side, DEFAULT_IDLE_PITCH_PCT - 2, 130, 32),
                        motion_point(-50 * side, DEFAULT_IDLE_PITCH_PCT - 8, 130, 32),
                        motion_point(-22 * side, DEFAULT_IDLE_PITCH_PCT - 11, 130, 32),
                        motion_point(13 * side, DEFAULT_IDLE_PITCH_PCT - 12, 130, 32),
                        motion_point(44 * side, DEFAULT_IDLE_PITCH_PCT - 8, 130, 32),
                        motion_point(63 * side, DEFAULT_IDLE_PITCH_PCT - 3, 130, 32),
                        motion_point(65 * side, DEFAULT_IDLE_PITCH_PCT, 130, 32),
                        motion_point(30 * side, DEFAULT_IDLE_PITCH_PCT + 11, 130, 32),
                        motion_point(-37 * side, DEFAULT_IDLE_PITCH_PCT + 10, 130, 32),
                        motion_point(-64 * side, DEFAULT_IDLE_PITCH_PCT - 2, 130, 32),
                        motion_point(-22 * side, DEFAULT_IDLE_PITCH_PCT - 11, 130, 32),
                        motion_point(44 * side, DEFAULT_IDLE_PITCH_PCT - 8, 130, 32),
                        motion_point(0, DEFAULT_IDLE_PITCH_PCT, 520, 28),
                    ],
                    32,
                    variant=name,
                ),
            ),
            (1200, life_face("happy_squint", high, name)),
            (1600, life_face(mood, base_intensity, name)),
        ]
    if name == "drama_blink":
        return [(0, life_face("soft_blink", low, name)), (520, life_face("surprise_pop", high, name))]
    if name == "shy_lookaway":
        side = rng.choice(["left", "right"])
        yaw = -18 if side == "left" else 18
        return [
            (0, life_face(gaze_for_direction(side), low, name)),
            (300, life_motion([idle_motion_point(yaw, -4, 1200, 14, 420), idle_motion_point(0, 0, 1500, 10)], 14, variant=name)),
            (2200, life_face("mouth_tiny", low, name)),
        ]
    if name == "proud_lift":
        return [
            (0, life_face("happy_squint", high, name)),
            (120, life_motion([idle_motion_point(0, 0, 1000, 18, 260), idle_motion_point(0, 0, 1300, 12)], 18, variant=name)),
        ]
    if name == "bored_sigh":
        return [(0, life_face("glance_down", low, name)), (520, life_face("deep_breathe", low, name))]
    if name == "sneaky_side_eye":
        side = rng.choice(["glance_left", "glance_right"])
        return [(0, life_face(side, low, name)), (900, life_face("smug", high, name)), (500, life_face(side, low, name))]
    if name == "tiny_laugh":
        return [(0, life_face("friendly", high, name)), (360, life_face("mouth_smile", high, name)), (360, life_face("soft_blink", base_intensity, name))]
    if name == "confused_scan":
        return [
            (0, life_face("glance_up", base_intensity, name)),
            (420, life_face("glance_left", base_intensity, name)),
            (420, life_face("glance_right", base_intensity, name)),
            (420, life_face("question", base_intensity, name)),
            (820, life_face(mood, base_intensity, name)),
        ]
    if name == "sleepy_recover":
        return [(0, life_face("micro_sleep", low, name)), (900, life_face("surprise_pop", high, name)), (620, life_face("soft_blink", base_intensity, name))]
    if name == "reset_grin":
        return [(0, life_face(rng.choice(["glance_left", "glance_right"]), base_intensity, name)), (520, life_face("mouth_smile", high, name)), (620, life_face(mood, base_intensity, name))]
    if name == "cross_eyes":
        return [(0, life_face("cross_eyes", high, name)), (760, life_face("soft_blink", base_intensity, name))]
    if name == "eye_swap":
        return [(0, life_face("eye_swap", high, name)), (720, life_face(mood, base_intensity, name))]
    if name == "derp":
        return [
            (0, life_face("derp", high, name)),
            (180, life_motion([idle_motion_point(rng.choice([-4, 4]), rng.choice([-2, 2]), 900, 10), idle_motion_point(0, 0, 1200, 10)], 10, variant=name)),
            (860, life_face("soft_blink", base_intensity, name)),
        ]
    if name == "boing_eyes":
        return [(0, life_face("boing_eyes", high, name)), (680, life_face("mouth_tiny", base_intensity, name))]
    if name == "suspicious_squint":
        side = rng.choice([-1, 1])
        return [
            (0, life_face("suspicious_squint", low, name)),
            (240, life_motion([idle_motion_point(7 * side, -1, 1100, 11, 260), idle_motion_point(0, 0, 1300, 10)], 11, variant=name)),
            (1200, life_face(mood, base_intensity, name)),
        ]
    if name == "confused_dots":
        return [(0, life_face("confused_dots", base_intensity, name)), (620, life_face("mouth_pop", base_intensity, name))]
    if name == "mouth_pop":
        return [(0, life_face("mouth_pop", high, name))]
    if name == "smirk_slide":
        return [(0, life_face("glance_right", base_intensity, name)), (280, life_face("mischievous", high, name)), (760, life_face(mood, base_intensity, name))]
    if name == "silent_giggle":
        return [(0, life_face("silent_giggle", high, name)), (680, life_face("happy_squint", high, name))]
    if name == "sleepy_snapback":
        return [(0, life_face("sleepy_snapback", low, name)), (780, life_face("soft_blink", base_intensity, name))]

    return [(0, life_face("soft_blink", base_intensity, name))]


def build_generated_life_sequence(name: str, rng: random.Random, base_intensity: int, mood: str) -> LifeSequence:
    parts = name.split("_")
    family = parts[1]

    if family == "gaze":
        direction = "_".join(parts[2:-1])
        hold_ms = int(parts[-1])
        glance = gaze_for_direction(direction)
        return [
            (0, life_face(glance, base_intensity, name)),
            (hold_ms, life_face(rng.choice(["mouth_tiny", "soft_blink", mood]), base_intensity, name)),
        ]

    if family == "mouth":
        mouth = parts[2]
        intensity_delta = int(parts[3])
        emotion = {
            "smile": "mouth_smile",
            "tiny": "mouth_tiny",
            "wiggle": "mouth_wiggle",
            "grumble": "grumble",
            "laugh": "happy_squint",
        }[mouth]
        return [(0, life_face(emotion, base_intensity + intensity_delta, name))]

    if family == "blink":
        style = parts[2]
        if style == "single":
            return [(0, life_face("soft_blink", base_intensity, name))]
        if style == "double":
            return [(0, life_face("soft_blink", base_intensity, name)), (int(parts[3]), life_face("soft_blink", base_intensity, name))]
        if style == "slow":
            return [(0, life_face("breathe", base_intensity - 4, name)), (500, life_face("soft_blink", base_intensity - 4, name))]
        if style == "asym":
            return [(0, life_face(rng.choice(["wink_left", "wink_right"]), base_intensity + 8, name))]
        return [(0, life_face("soft_blink", base_intensity - 4, name)), (540, life_face("surprise_pop", base_intensity + 10, name))]

    if family == "breath":
        style = parts[2]
        if style in {"small", "held"}:
            return [(0, life_face("breathe", base_intensity, name))]
        if style == "sleepy":
            return [(0, life_face("micro_sleep", base_intensity - 6, name))]
        return [(0, life_face("deep_breathe", base_intensity - (5 if style == "sigh" else 0), name))]

    if family == "head":
        direction = parts[2]
        glance = gaze_for_direction(direction)
        yaw_map = {"left": -5, "right": 5, "up": rng.choice([-2, 2]), "down": rng.choice([-2, 2]), "scan": rng.choice([-42, 42])}
        pitch_map = {"left": 2, "right": 2, "up": 4, "down": -4, "scan": rng.choice([8, -8])}
        yaw = yaw_map[direction]
        pitch = pitch_map[direction]
        speed = rng.choice([10, 12]) if direction != "scan" else rng.choice([20, 24])
        return [
            (0, life_face(glance, base_intensity, name)),
            (760, life_motion([
                idle_motion_point(yaw, pitch, rng.choice([1400, 1600, 1800]), speed, rng.choice([180, 320, 480])),
                idle_motion_point(0, 0, rng.choice([1500, 1700, 1900]), max(10, speed - 2)),
            ], speed, variant=name)),
            (1800, life_face(mood, base_intensity, name)),
        ]

    return [(0, life_face("soft_blink", base_intensity, name))]


CURATED_LIFE_VARIANT_NAMES = [
    "double_blink", "lazy_blink", "suspicious_left", "suspicious_right", "tiny_smile",
    "look_up_think", "look_down_table", "wink_left", "wink_right", "deep_breathe",
    "mouth_wiggle", "micro_sleep", "surprise_pop", "cheeky_grin", "question_glance",
    "nervous_flick", "happy_squint", "grumble_mouth", "scanner_eyes", "yawn_hint",
    "look_behind", "desk_spin", "drama_blink", "shy_lookaway", "proud_lift", "bored_sigh",
    "sneaky_side_eye", "tiny_laugh", "confused_scan", "sleepy_recover", "reset_grin",
    "cross_eyes", "eye_swap", "derp", "boing_eyes", "suspicious_squint", "confused_dots",
    "mouth_pop", "smirk_slide", "silent_giggle", "sleepy_snapback",
]


def build_life_variants() -> list[LifeVariant]:
    variants: list[LifeVariant] = []
    funny_names = {
        "cross_eyes", "eye_swap", "derp", "boing_eyes", "suspicious_squint", "confused_dots",
        "mouth_pop", "smirk_slide", "silent_giggle", "sleepy_snapback",
    }
    rare_names = {"micro_sleep", "surprise_pop", "look_behind", "desk_spin", "drama_blink", "yawn_hint", "sleepy_recover"} | funny_names
    for name in CURATED_LIFE_VARIANT_NAMES:
        weight = 1.1 if name in funny_names else 0.8 if name in rare_names else 2.4
        if name == "desk_spin":
            weight = 2.4
        variants.append(LifeVariant(
            name=name,
            weight=weight,
            rare=name in rare_names,
            min_gap_s=28.0 if name in funny_names else 45.0 if name in rare_names else 8.0,
            builder=lambda rng, intensity, mood, variant_name=name: build_named_life_sequence(variant_name, rng, intensity, mood),
        ))

    gaze_directions = ["left", "right", "up", "down", "up_left", "up_right", "down_left", "down_right"]
    for index in range(20):
        direction = gaze_directions[index % len(gaze_directions)]
        hold = [360, 520, 700, 900, 1150][index % 5]
        name = f"gen_gaze_{direction}_{hold}"
        variants.append(LifeVariant(name, 2.6, False, 5.0, lambda rng, intensity, mood, variant_name=name: build_generated_life_sequence(variant_name, rng, intensity, mood)))

    mouth_specs = [
        ("smile", 4), ("smile", 10), ("tiny", -4), ("tiny", 2), ("wiggle", 0),
        ("wiggle", 6), ("grumble", -8), ("grumble", -2), ("laugh", 8), ("laugh", 14),
        ("smile", -2), ("tiny", 8), ("wiggle", -4), ("grumble", 4), ("laugh", 2),
    ]
    for index, (mouth, delta) in enumerate(mouth_specs):
        name = f"gen_mouth_{mouth}_{delta}_{index}"
        variants.append(LifeVariant(name, 1.9, False, 7.0, lambda rng, intensity, mood, variant_name=name: build_generated_life_sequence(variant_name, rng, intensity, mood)))

    blink_specs = [
        "single", "single", "single", "single", "double_260", "double_360", "double_480",
        "slow", "slow", "asym", "asym", "drama", "single", "double_300", "slow",
    ]
    for index, style in enumerate(blink_specs):
        name = f"gen_blink_{style}_{index}"
        variants.append(LifeVariant(name, 8.0, style == "drama", 4.0 if style != "drama" else 35.0, lambda rng, intensity, mood, variant_name=name: build_generated_life_sequence(variant_name, rng, intensity, mood)))

    breath_specs = ["small", "small", "deep", "deep", "held", "sigh", "sleepy", "small", "deep", "sigh"]
    for index, style in enumerate(breath_specs):
        name = f"gen_breath_{style}_{index}"
        variants.append(LifeVariant(name, 3.2, style == "sleepy", 7.0 if style != "sleepy" else 45.0, lambda rng, intensity, mood, variant_name=name: build_generated_life_sequence(variant_name, rng, intensity, mood)))

    head_specs = ["left", "right", "up", "down", "scan", "left", "right", "up", "down"]
    for index, direction in enumerate(head_specs):
        name = f"gen_head_{direction}_{index}"
        variants.append(LifeVariant(name, 1.6 if direction != "scan" else 0.8, direction == "scan", 12.0 if direction != "scan" else 40.0, lambda rng, intensity, mood, variant_name=name: build_generated_life_sequence(variant_name, rng, intensity, mood)))

    if len(variants) < 100:
        raise RuntimeError(f"expected at least 100 life variants, got {len(variants)}")
    return variants


LIFE_VARIANTS = build_life_variants()
LIFE_VARIANT_NAMES = tuple(variant.name for variant in LIFE_VARIANTS)
LIFE_VARIANTS_BY_NAME = {variant.name: variant for variant in LIFE_VARIANTS}


def life_variants_matching(predicate: Callable[[str], bool]) -> list[LifeVariant]:
    return [variant for variant in LIFE_VARIANTS if predicate(variant.name)]


LIFE_VARIANT_CATEGORIES: dict[str, list[LifeVariant]] = {
    "blink_breathe": life_variants_matching(
        lambda name: name.startswith("gen_blink_")
        or name.startswith("gen_breath_")
        or name in {"double_blink", "lazy_blink", "deep_breathe"}
    ),
    "gaze": life_variants_matching(
        lambda name: name.startswith("gen_gaze_")
        or name in {
            "suspicious_left", "suspicious_right", "look_up_think", "look_down_table",
            "question_glance", "nervous_flick", "scanner_eyes", "sneaky_side_eye",
            "confused_scan", "reset_grin",
        }
    ),
    "mouth": life_variants_matching(
        lambda name: name.startswith("gen_mouth_")
        or name in {
            "tiny_smile", "mouth_wiggle", "cheeky_grin", "happy_squint",
            "grumble_mouth", "tiny_laugh",
        }
    ),
    "small_head": life_variants_matching(
        lambda name: (name.startswith("gen_head_") and "_scan_" not in name)
    ),
    "big_head": life_variants_matching(
        lambda name: name in {"look_behind", "desk_spin", "shy_lookaway", "proud_lift"}
        or (name.startswith("gen_head_") and "_scan_" in name)
    ),
    "rare_gag": life_variants_matching(
        lambda name: name in {"wink_left", "wink_right", "micro_sleep", "surprise_pop", "yawn_hint", "drama_blink", "sleepy_recover"}
    ),
    "funny_gag": life_variants_matching(
        lambda name: name in {
            "cross_eyes", "eye_swap", "derp", "boing_eyes", "suspicious_squint",
            "confused_dots", "mouth_pop", "smirk_slide", "silent_giggle", "sleepy_snapback",
        }
    ),
}


def choose_life_variant(rng: random.Random) -> LifeVariant:
    roll = rng.random()
    if roll < 0.62:
        category = LIFE_VARIANT_CATEGORIES["blink_breathe"]
    elif roll < 0.78:
        category = LIFE_VARIANT_CATEGORIES["gaze"]
    elif roll < 0.86:
        category = LIFE_VARIANT_CATEGORIES["mouth"]
    elif roll < 0.925:
        category = LIFE_VARIANT_CATEGORIES["small_head"]
    elif roll < 0.985:
        category = LIFE_VARIANT_CATEGORIES["funny_gag"]
    elif roll < 0.997:
        category = LIFE_VARIANT_CATEGORIES["big_head"]
    else:
        category = LIFE_VARIANT_CATEGORIES["rare_gag"]
    return rng.choices(category, weights=[variant.weight for variant in category], k=1)[0]


def strip_motion_from_life_sequence(sequence: LifeSequence) -> LifeSequence:
    stripped = [(delay, action) for delay, action in sequence if action.get("action") != "motion"]
    return stripped or [(0, {"action": "face", "emotion": "breathe", "intensity_pct": 60, "variant": "motion_stripped_breathe"})]


def life_eye_heartbeat(rng: random.Random, base_intensity: int, variant: str) -> dict[str, Any]:
    return life_face(
        rng.choice(["glance_left", "glance_right", "glance_up", "glance_down", "soft_blink"]),
        clamp_int(base_intensity, 45, 88),
        f"{variant}_heartbeat",
    )


def densify_life_sequence(sequence: LifeSequence, rng: random.Random, base_intensity: int, variant: str) -> LifeSequence:
    dense: LifeSequence = []
    for delay_ms, action in sequence:
        remaining = int(delay_ms)
        while remaining > MAX_LIFE_FACE_GAP_MS:
            dense.append((MAX_LIFE_FACE_GAP_MS, life_eye_heartbeat(rng, base_intensity, variant)))
            remaining -= MAX_LIFE_FACE_GAP_MS
        dense.append((remaining, action))
    return dense


def build_life_sequence(
    status: dict[str, Any] | None,
    rng: random.Random,
    include_motion: bool = True,
) -> list[tuple[int, dict[str, Any]]]:
    if not status_allows_life_animation(status):
        return []

    restore = current_face_action(status)
    mood = restore["emotion"]
    base_intensity = int(restore["intensity_pct"])
    variant = choose_life_variant(rng)
    sequence = variant.builder(rng, base_intensity, mood)
    sequence = densify_life_sequence(sequence, rng, base_intensity, variant.name)
    if not include_motion:
        sequence = strip_motion_from_life_sequence(sequence)
    for _delay, action in sequence:
        action.setdefault("variant", variant.name)
    return sequence


def ensure_reply_action(response: dict[str, Any]) -> list[dict[str, Any]]:
    actions = list(response.get("actions") or [])
    reply = optional_string(response.get("reply"))
    has_visible_reply = any(
        isinstance(action, dict) and str(action.get("action", "")).lower() in {"say", "display"}
        for action in actions
    )
    if reply and not has_visible_reply:
        actions.insert(0, {"action": "say", "text": reply, "emotion": "speaking", "beep": True})
    return actions


def external_reply_actions(actions: list[dict[str, Any]], display_text: str, tts_enabled: bool) -> list[dict[str, Any]]:
    if not tts_enabled:
        return actions
    filtered = [action for action in actions if action_name(action) != "say"]
    has_display = any(action_name(action) == "display" for action in filtered)
    if display_text and not has_display:
        filtered.insert(0, {"action": "display", "text": display_text, "duration_ms": 9000})
    return filtered


def notify_text_from_payload(payload: dict[str, Any]) -> str:
    for key in ("text", "reply", "message"):
        text = optional_string(payload.get(key))
        if text:
            return text
    return ""


def notify_actions_from_payload(payload: dict[str, Any], display_text: str, tts_enabled: bool) -> list[dict[str, Any]]:
    actions = payload.get("actions")
    if actions is None:
        actions = []
    elif isinstance(actions, dict):
        actions = [actions]
    elif not isinstance(actions, list):
        actions = []
    actions = [action for action in actions if isinstance(action, dict)]
    return external_reply_actions(actions, display_text, tts_enabled)


def mqtt_settle_delay_after_publish_s(topic: str, payload: dict[str, Any], pair: PairConfig | None = None) -> float:
    if pair is None:
        return 0.0
    if topic == pair.system_topic and payload.get("action") in {"display_wake", "display_sleep"}:
        return 0.25
    if topic == pair.face_topic:
        return 0.12
    if topic == pair.display_topic:
        text = optional_string(payload.get("text")) or ""
        return min(2.2, 0.7 + len(text) / 420.0)
    if topic == pair.say_topic:
        text = optional_string(payload.get("text")) or ""
        return min(2.6, 0.9 + len(text) / 360.0)
    if topic == pair.motion_topic:
        return life_action_settle_delay_s({"action": "motion", "points": payload.get("points")})
    return 0.0


def dispatch_mqtt_actions(
    config: BridgeConfig,
    pair: PairConfig,
    action_messages: list[tuple[str, dict[str, Any]]],
    wait_ack: bool,
    timeout_s: float,
) -> int:
    client = create_mqtt_client(config.mqtt)
    pending = {payload["request_id"] for _topic, payload in action_messages if payload.get("request_id")}
    responses: dict[str, dict[str, Any]] = {}
    ack_seen = Event()

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        request_id = data.get("request_id")
        if request_id not in pending:
            return
        data["_topic"] = message.topic
        responses[request_id] = data
        if pending.issubset(responses):
            ack_seen.set()

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        if wait_ack:
            client.subscribe([(pair.ack_topic, 1), (pair.error_topic, 1)])
        for topic, payload in action_messages:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            result = client.publish(topic, body, qos=1, retain=False)
            result.wait_for_publish(timeout=5)
            print(f"[bridge] sent {topic}: {body}")
            if topic == pair.device_topic:
                publish_device_settings_snapshot(client, pair, payload, "dispatch")
            settle_delay = mqtt_settle_delay_after_publish_s(topic, payload, pair)
            if settle_delay > 0:
                time.sleep(settle_delay)
        if not wait_ack or not pending:
            return 0
        deadline = time.monotonic() + timeout_s
        while pending.difference(responses) and time.monotonic() < deadline:
            ack_seen.wait(min(0.2, max(0.0, deadline - time.monotonic())))
        missing = pending.difference(responses)
        for response in responses.values():
            print(f"[bridge] response {response.get('_topic')}: {json.dumps(response, ensure_ascii=False)}")
        if missing:
            print(f"[bridge] missing ACK for: {', '.join(sorted(missing))}", file=sys.stderr)
            return 3
        return 0 if all(response.get("_topic") == pair.ack_topic for response in responses.values()) else 2
    finally:
        client.loop_stop()
        client.disconnect()


def hermes_health(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    body = http_get_text(hermes_health_url(config.hermes.base_url), config.hermes.api_key, args.timeout)
    print(body)
    return 0


def ask_hermes(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path, Path(args.env))
    pair = get_pair(config, args.pair)
    user_text = args.text.strip()
    if not user_text:
        raise ConfigError("ask-hermes needs non-empty --text")
    status = None if args.no_status else read_latest_status(config, pair, args.status_timeout)
    capabilities = read_optional_text(pair.capabilities_file, config_path)
    personality = read_optional_text(pair.personality_file, config_path)
    response = ask_hermes_http(config, pair, capabilities, personality, status, user_text)
    actions = ensure_reply_action(response)
    scheduled_reminders: list[dict[str, Any]] = []
    reminder_errors: list[str] = []
    if not args.dry_run:
        actions, scheduled_reminders, reminder_errors = schedule_reminders_from_actions(
            config,
            pair,
            actions,
            f"hermes-reminder-{uuid.uuid4().hex[:8]}",
        )
    spoken_text = speech_text_from_hermes_response(response, user_text)
    tts_enabled = bool(spoken_text and config.speech.bridge_public_url)
    actions = external_reply_actions(actions, spoken_text, tts_enabled)
    if args.show_response or args.dry_run:
        print(json.dumps(
            {"hermes": response, "actions": actions, "scheduled_reminders": scheduled_reminders, "reminder_errors": reminder_errors},
            ensure_ascii=False,
            indent=2,
        ))
    action_messages = [
        action_to_topic_payload(pair, action, f"hermes-{uuid.uuid4().hex[:12]}")
        for action in actions
    ]
    if tts_enabled:
        try:
            tts_request_id = f"hermes-tts-{uuid.uuid4().hex[:12]}"
            tts_path = make_tts_wav(safe_tts_text(spoken_text), config.speech, tts_request_id)
            action_messages.append(
                action_to_topic_payload(
                    pair,
                    {"action": "audio", "audio_action": "play_tts_url", "url": tts_public_url(config, tts_path)},
                    tts_request_id,
                )
            )
        except Exception as exc:
            print(f"[bridge] TTS skipped for Hermes reply: {exc}", file=sys.stderr, flush=True)
    if args.dry_run:
        print(json.dumps(
            [{"topic": topic, "payload": payload} for topic, payload in action_messages],
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    for reminder in scheduled_reminders:
        print(
            f"[bridge] scheduled reminder {reminder['id']} for {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(reminder['due_ts'])))}: {reminder['text']}",
            flush=True,
        )
    for error in reminder_errors:
        print(f"[bridge] reminder ignored: {error}", file=sys.stderr, flush=True)
    return dispatch_mqtt_actions(config, pair, action_messages, not args.no_wait_ack, args.timeout)


def publish_action_messages(
    client: Any,
    action_messages: list[tuple[str, dict[str, Any]]],
    pair: PairConfig | None = None,
    wait: bool = True,
) -> None:
    if pair is not None and action_messages and not stackchan_is_online(pair):
        STACKCHAN_PRESENCE.note_skip(pair, f"{len(action_messages)} action(s)")
        return
    for topic, payload in action_messages:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        result = client.publish(topic, body, qos=1, retain=False)
        if wait:
            result.wait_for_publish(timeout=5)
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] sent {topic}: {body}", flush=True)
        if pair is not None and topic == pair.device_topic:
            publish_device_settings_snapshot(client, pair, payload, "action")
        settle_delay = mqtt_settle_delay_after_publish_s(topic, payload, pair)
        if settle_delay > 0:
            time.sleep(settle_delay)


def watch_power(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)
    previous: dict[str, Any] | None = None
    last_event_at = 0.0
    restore_timer: Timer | None = None
    done = Event()

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        nonlocal previous, last_event_at, restore_timer
        try:
            status = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(status, dict):
            return
        if message_is_retained(message):
            return
        note_stackchan_status(pair, status)
        current = battery_snapshot(status)
        if previous is None:
            print(f"[{time.strftime('%H:%M:%S')}] [bridge] power state initial: {json.dumps(current, ensure_ascii=False)}", flush=True)
            if not args.announce_initial:
                previous = current
                return
            previous = {
                **current,
                "charging": not current["charging"],
                "discharging": not current["discharging"],
                "external_power": not current["external_power"],
            }

        now = time.monotonic()
        if now - last_event_at < args.debounce_s:
            previous = current
            return

        followup_actions = build_power_followup_actions(previous, current)
        immediate_followup_actions = [action for action in followup_actions if action.get("action") == "motion"]
        delayed_followup_actions = [action for action in followup_actions if action.get("action") != "motion"]
        actions = build_power_change_actions(previous, current)
        previous = current
        if not actions:
            return

        last_event_at = now
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] power change: {json.dumps(current, ensure_ascii=False)}", flush=True)
        action_messages = [
            action_to_topic_payload(pair, action, f"power-{uuid.uuid4().hex[:12]}")
            for action in actions + immediate_followup_actions
        ]
        publish_action_messages(client, action_messages, pair)
        if delayed_followup_actions and not args.no_restore_face:
            delay_ms = max(
                (int(action.get("duration_ms", 0)) for action in actions if action.get("action") == "display"),
                default=0,
            )
            if restore_timer:
                restore_timer.cancel()

            def publish_followup_actions() -> None:
                publish_action_messages(
                    client,
                    [
                        action_to_topic_payload(pair, action, f"power-followup-{uuid.uuid4().hex[:8]}")
                        for action in delayed_followup_actions
                    ],
                    pair,
                )
                if args.once:
                    done.set()

            restore_timer = Timer(max(0.0, delay_ms / 1000.0), publish_followup_actions)
            restore_timer.daemon = True
            restore_timer.start()
        elif args.once:
            done.set()

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        client.subscribe(pair.status_topic, qos=0)
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] watching power on {pair.status_topic}", flush=True)
        while not done.wait(0.25):
            pass
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if restore_timer:
            restore_timer.cancel()
        client.loop_stop()
        client.disconnect()


def animate_life(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    rng = random.Random(args.seed)
    client = create_mqtt_client(config.mqtt)
    emitted = 0
    motion_limiter = LifeMotionLimiter(args.small_motion_gap_s, args.big_motion_gap_s)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        if message.topic != pair.status_topic:
            return
        try:
            status = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if isinstance(status, dict):
            if message_is_retained(message):
                return
            note_stackchan_status(pair, status)

    try:
        client.on_message = on_message
        connect_and_start(client, config.mqtt)
        client.subscribe(pair.status_topic, qos=0)
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] life animation active for {pair.pair_id}", flush=True)
        while True:
            if not stackchan_is_online(pair):
                STACKCHAN_PRESENCE.note_skip(pair, "life animation")
                time.sleep(min(1.0, max(0.1, args.min_interval_s)))
                continue
            if life_animation_paused(pair.pair_id):
                if args.once:
                    print("[bridge] life animation skipped: paused by speech or reminder", file=sys.stderr)
                    return 2
                time.sleep(min(1.0, max(0.1, args.min_interval_s)))
                continue
            status = read_latest_status(config, pair, args.status_timeout)
            sequence = build_life_sequence(status, rng, include_motion=not args.no_motion)
            if sequence:
                for delay_ms, action in sequence:
                    if delay_ms > 0:
                        time.sleep(delay_ms / 1000.0)
                    if life_animation_paused(pair.pair_id):
                        break
                    if action_name(action) == "motion":
                        status = read_latest_status(config, pair, args.status_timeout)
                        if not status_allows_life_animation(status):
                            print("[bridge] life motion skipped: StackChan is no longer idle on face", flush=True)
                            break
                        if not motion_limiter.allow(action):
                            print(
                                f"[bridge] life motion skipped: {life_motion_size(action)} motion rate limit",
                                flush=True,
                            )
                            continue
                    publish_action_messages(
                        client,
                        [action_to_topic_payload(pair, action, f"life-{uuid.uuid4().hex[:10]}")],
                        pair,
                    )
                    settle_delay_s = life_action_settle_delay_s(action)
                    if settle_delay_s > 0:
                        time.sleep(settle_delay_s)
                emitted += 1
                if args.once:
                    return 0
            elif args.once:
                print("[bridge] life animation skipped: StackChan is not idle on face", file=sys.stderr)
                return 2

            time.sleep(rng.uniform(args.min_interval_s, args.max_interval_s))
    except KeyboardInterrupt:
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


def send_display(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload = build_display_payload(args.text, args.duration_ms, args.request_id)
    return send_payload(args, pair.display_topic, payload)


def send_face(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload = with_request_id(
        {"emotion": args.emotion, "intensity_pct": args.intensity_pct},
        args.request_id,
    )
    return send_payload(args, pair.face_topic, payload)


def send_move(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload: dict[str, Any] = {}
    if args.direction:
        payload["direction"] = args.direction
    if args.yaw_delta is not None:
        payload["yaw_delta"] = args.yaw_delta
    if args.pitch_delta is not None:
        payload["pitch_delta"] = args.pitch_delta
    if args.yaw_target_pct is not None:
        payload["yaw_target_pct"] = clamp_int(args.yaw_target_pct, YAW_TARGET_MIN_PCT, YAW_TARGET_MAX_PCT)
    if args.pitch_target_pct is not None:
        payload["pitch_target_pct"] = clamp_int(args.pitch_target_pct, PITCH_TARGET_MIN_PCT, PITCH_TARGET_MAX_PCT)
    if not payload:
        raise ConfigError("send-move needs a direction, delta, or target percent")
    return send_payload(args, pair.move_topic, with_request_id(payload, args.request_id))


def send_motion(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload: dict[str, Any] = {"speed_pct": clamp_int(args.speed_pct, 1, 100)}
    if args.points:
        curve = args.curve if args.curve != "auto" else "linear"
        try:
            points = json.loads(args.points)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"--points must be JSON: {exc}") from exc
        points = normalize_motion_points(points, payload["speed_pct"], args.segment_ms)
        payload["points"] = points
        payload["curve"] = curve
        if args.segment_ms is not None:
            payload["segment_ms"] = clamp_int(args.segment_ms, 0, 4000)
    else:
        curve = args.curve if args.curve != "auto" else ("spline" if args.profile == "circle" else "linear")
        payload["points"] = build_motion_profile_points(args)
        payload["curve"] = curve
    return send_payload(args, pair.motion_topic, with_request_id(payload, args.request_id))


def normalize_motion_points(points: Any, default_speed_pct: int, default_duration_ms: int | None = None) -> list[dict[str, int]]:
    if not isinstance(points, list):
        raise ConfigError("--points must be a JSON array")
    if not points:
        raise ConfigError("--points must contain at least one waypoint")
    if len(points) > 48:
        raise ConfigError("--points may contain at most 48 waypoints")

    normalized: list[dict[str, int]] = []
    for index, raw in enumerate(points):
        duration_ms = default_duration_ms
        speed_pct = default_speed_pct
        hold_ms = 0
        if isinstance(raw, list):
            if len(raw) < 2:
                raise ConfigError(f"point {index} needs yaw_pct and pitch_pct")
            yaw = raw[0]
            pitch = raw[1]
            if len(raw) > 2:
                duration_ms = raw[2]
            if len(raw) > 3:
                speed_pct = raw[3]
            if len(raw) > 4:
                hold_ms = raw[4]
        elif isinstance(raw, dict):
            yaw = raw.get("yaw_pct", raw.get("yaw"))
            pitch = raw.get("pitch_pct", raw.get("pitch"))
            duration_ms = raw.get("duration_ms", duration_ms)
            speed_pct = raw.get("speed_pct", speed_pct)
            hold_ms = raw.get("hold_ms", hold_ms)
        else:
            raise ConfigError(f"point {index} must be an array or object")

        if not isinstance(yaw, (int, float)) or not isinstance(pitch, (int, float)):
            raise ConfigError(f"point {index} needs numeric yaw/pitch values")
        if not isinstance(speed_pct, (int, float)):
            raise ConfigError(f"point {index} speed_pct must be numeric")

        item = {
            "yaw_pct": clamp_int(round(yaw), YAW_TARGET_MIN_PCT, YAW_TARGET_MAX_PCT),
            "pitch_pct": clamp_int(round(pitch), PITCH_TARGET_MIN_PCT, PITCH_TARGET_MAX_PCT),
            "speed_pct": clamp_int(round(speed_pct), 1, 100),
        }
        if duration_ms is not None:
            if not isinstance(duration_ms, (int, float)):
                raise ConfigError(f"point {index} duration_ms must be numeric")
            item["duration_ms"] = clamp_int(round(duration_ms), 40, 4000) if duration_ms > 0 else 0
        if hold_ms:
            if not isinstance(hold_ms, (int, float)):
                raise ConfigError(f"point {index} hold_ms must be numeric")
            item["hold_ms"] = clamp_int(round(hold_ms), 0, 4000)
        normalized.append(item)

    return normalized


def build_motion_profile_points(args: argparse.Namespace) -> list[dict[str, int]]:
    speed_pct = clamp_int(args.speed_pct, 1, 100)
    total_duration_ms = args.duration_ms

    def point(yaw: int, pitch: int, hold_ms: int = 0, duration_ms: int | None = None) -> dict[str, int]:
        item = {
            "yaw_pct": clamp_int(yaw, YAW_TARGET_MIN_PCT, YAW_TARGET_MAX_PCT),
            "pitch_pct": clamp_int(pitch, PITCH_TARGET_MIN_PCT, PITCH_TARGET_MAX_PCT),
            "speed_pct": speed_pct,
        }
        if duration_ms is not None:
            item["duration_ms"] = clamp_int(duration_ms, 40, 4000)
        if hold_ms:
            item["hold_ms"] = clamp_int(hold_ms, 0, 4000)
        return item

    profile = args.profile
    if profile == "circle":
        steps = max(12, int(args.steps))
        loops = max(1, int(args.loops))
        if total_duration_ms is None:
            avg_radius = (abs(args.yaw_radius_pct) + abs(args.pitch_radius_pct)) / 2.0
            ms_per_pct = 5.0 + (100 - speed_pct) * 0.30
            total_duration_ms = round((2.0 * math.pi * max(1.0, avg_radius) * loops) * ms_per_pct)
        total_steps = clamp_int(steps * loops, 12, 46)
        arc_segment_duration = clamp_int(round(total_duration_ms / total_steps), 50, 4000)
        approach_duration = clamp_int(round(arc_segment_duration * 6), 300, 1200)
        center_pitch = DEFAULT_IDLE_PITCH_PCT
        points: list[dict[str, int]] = [
            point(args.yaw_radius_pct, center_pitch, duration_ms=approach_duration),
        ]
        for i in range(1, total_steps + 1):
            angle = 2.0 * math.pi * loops * i / total_steps
            points.append(
                point(
                    round(math.cos(angle) * args.yaw_radius_pct),
                    center_pitch + round(math.sin(angle) * args.pitch_radius_pct),
                    duration_ms=arc_segment_duration,
                )
            )
        points.append(point(0, center_pitch, duration_ms=approach_duration))
        return points

    if profile in {"nod", "yes"}:
        segment_duration = max(40, total_duration_ms // 5) if total_duration_ms is not None else None
        return [
            point(0, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
            point(0, DEFAULT_IDLE_PITCH_PCT + 30, duration_ms=segment_duration),
            point(0, DEFAULT_IDLE_PITCH_PCT - 22, duration_ms=segment_duration),
            point(0, DEFAULT_IDLE_PITCH_PCT + 28, duration_ms=segment_duration),
            point(0, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
        ]

    if profile in {"shake", "no"}:
        segment_duration = max(40, total_duration_ms // 6) if total_duration_ms is not None else None
        return [
            point(0, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
            point(-32, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
            point(32, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
            point(-26, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
            point(26, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
            point(0, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
        ]

    if profile in {"look_around", "look-around"}:
        segment_duration = max(40, total_duration_ms // 6) if total_duration_ms is not None else None
        return [
            point(0, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
            point(-38, DEFAULT_IDLE_PITCH_PCT + 8, 120, duration_ms=segment_duration),
            point(-18, DEFAULT_IDLE_PITCH_PCT - 22, 80, duration_ms=segment_duration),
            point(36, DEFAULT_IDLE_PITCH_PCT + 12, 120, duration_ms=segment_duration),
            point(16, DEFAULT_IDLE_PITCH_PCT - 18, 80, duration_ms=segment_duration),
            point(0, DEFAULT_IDLE_PITCH_PCT, duration_ms=segment_duration),
        ]

    raise ConfigError(f"unsupported motion profile: {profile}")


def send_led(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload: dict[str, Any] = {"mode": args.mode}
    if args.r is not None:
        payload["r"] = args.r
    if args.g is not None:
        payload["g"] = args.g
    if args.b is not None:
        payload["b"] = args.b
    return send_payload(args, pair.led_topic, with_request_id(payload, args.request_id))


def send_device(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload: dict[str, Any] = {}
    if args.volume_pct is not None:
        payload["volume_pct"] = args.volume_pct
    if args.brightness_pct is not None:
        payload["brightness_pct"] = args.brightness_pct
    if args.display_sleep:
        payload["display_sleep"] = True
    if args.display_wake:
        payload["display_wake"] = True
    if not payload:
        raise ConfigError("send-device needs volume, brightness, display sleep, or display wake")
    return send_payload(args, pair.device_topic, with_request_id(payload, args.request_id))


def build_restore_device_payload(settings: dict[str, Any] | None, display_wake: bool = False) -> dict[str, Any]:
    payload = extract_device_settings(settings)
    if display_wake:
        payload["display_wake"] = True
    return payload


def safe_stackchan_text(text: str, max_chars: int = MAX_STACKCHAN_TEXT_CHARS) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= max_chars:
        return value
    clipped = value[: max(0, max_chars - 4)].rstrip()
    return f"{clipped} ..."


def safe_tts_text(text: str, max_chars: int = MAX_STACKCHAN_TTS_CHARS) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= max_chars:
        return value
    clipped = value[: max(0, max_chars - 60)].rstrip()
    return f"{clipped}. Ich habe den Rest gekuerzt, damit StackChan stabil bleibt."


def restore_device_settings(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    settings = read_retained_json(config, pair.settings_topic, args.timeout)
    source = "retained-settings"
    if settings is None:
        settings = read_latest_status(config, pair, args.timeout)
        source = "retained-status"

    payload = build_restore_device_payload(settings, args.display_wake)
    if not payload:
        print(f"[bridge] no retained device settings found on {pair.settings_topic} or {pair.status_topic}", file=sys.stderr)
        return 3

    request_id = args.request_id or f"restore-{uuid.uuid4().hex[:12]}"
    command = with_request_id(payload, request_id)
    print(f"[bridge] restoring device settings from {source}: {json.dumps(payload, ensure_ascii=False)}", flush=True)
    return send_payload(
        argparse.Namespace(
            config=args.config,
            env=args.env,
            pair=args.pair,
            request_id=request_id,
            wait_ack=args.wait_ack,
            timeout=args.ack_timeout,
        ),
        pair.device_topic,
        command,
    )


def watch_device_settings(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)
    seen_status = False
    last_uptime_ms: int | None = None
    last_snapshot: dict[str, Any] | None = read_retained_json(config, pair.settings_topic, args.timeout)

    def publish_restore(settings: dict[str, Any], reason: str) -> None:
        payload = build_restore_device_payload(settings, args.display_wake)
        if not payload:
            return
        if not stackchan_is_online(pair):
            STACKCHAN_PRESENCE.note_skip(pair, f"device settings restore after {reason}")
            return
        command = with_request_id(payload, f"settings-{uuid.uuid4().hex[:10]}")
        body = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
        result = client.publish(pair.device_topic, body, qos=1, retain=False)
        result.wait_for_publish(timeout=5)
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] restored settings after {reason}: {body}", flush=True)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        nonlocal seen_status, last_uptime_ms, last_snapshot
        try:
            status = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(status, dict):
            return
        if message_is_retained(message):
            return
        note_stackchan_status(pair, status)

        uptime_ms = parse_int_value(status.get("uptime_ms"), 0, "status.uptime_ms")
        reboot_or_reconnect = (
            not seen_status
            or (last_uptime_ms is not None and uptime_ms + int(args.reboot_drop_ms) < last_uptime_ms)
        )
        if reboot_or_reconnect and last_snapshot:
            publish_restore(last_snapshot, "boot/reconnect")
        seen_status = True
        last_uptime_ms = uptime_ms

        current = extract_device_settings(status)
        if current and current != extract_device_settings(last_snapshot):
            last_snapshot = publish_device_settings_snapshot(client, pair, current, "status", last_snapshot)

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        client.subscribe(pair.status_topic, qos=0)
        print(
            f"[{time.strftime('%H:%M:%S')}] [bridge] watching device settings "
            f"status={pair.status_topic} retained={pair.settings_topic}",
            flush=True,
        )
        if args.once:
            time.sleep(args.timeout)
            return 0
        while True:
            time.sleep(1)
    finally:
        client.loop_stop()
        client.disconnect()


def send_sound(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload: dict[str, Any] = {
        "frequency_hz": args.frequency_hz,
        "duration_ms": args.duration_ms,
    }
    if args.pattern:
        payload["pattern"] = args.pattern
    if args.volume_pct is not None:
        payload["volume_pct"] = args.volume_pct
    return send_payload(args, pair.sound_topic, with_request_id(payload, args.request_id))


def send_audio(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    if args.enabled and args.disabled:
        raise ConfigError("send-audio accepts either --enabled or --disabled, not both")
    payload: dict[str, Any] = {"action": args.action}
    if args.source:
        payload["source"] = args.source
    if args.wakeword:
        payload["wakeword"] = args.wakeword
    if args.reason:
        payload["reason"] = args.reason
    if args.min_ms is not None:
        payload["min_ms"] = args.min_ms
    if args.silence_timeout_ms is not None:
        payload["silence_timeout_ms"] = args.silence_timeout_ms
    if args.max_ms is not None:
        payload["max_ms"] = args.max_ms
    if args.enabled:
        payload["enabled"] = True
    if args.disabled:
        payload["enabled"] = False
    return send_payload(args, pair.audio_topic, with_request_id(payload, args.request_id))


def send_say(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload = build_display_payload(safe_stackchan_text(args.text, MAX_STACKCHAN_DISPLAY_CHARS), 7000, args.request_id)
    return send_payload(args, pair.display_topic, payload)


def send_system(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload = with_request_id({"action": args.action}, args.request_id)
    return send_payload(args, pair.system_topic, payload)


def send_raw(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    suffix = args.topic_suffix.strip("/")
    if not suffix.startswith("cmd/"):
        raise ConfigError("raw topic suffix must start with cmd/")
    try:
        payload = json.loads(args.json)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"raw JSON payload is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("raw JSON payload must be an object")
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("request_id", args.request_id or uuid.uuid4().hex)
    return send_payload(args, f"{pair.mqtt_prefix}/{suffix}", payload)


def watch(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        print(f"{message.topic} {payload}")

    client.on_message = on_message
    connect_and_start(client, config.mqtt)
    client.subscribe(f"{pair.mqtt_prefix}/#", qos=0)
    print(f"[bridge] watching {pair.mqtt_prefix}/# on {config.mqtt.host}:{config.mqtt.port}")
    try:
        while True:
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


def watch_touch_lamp(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)
    green_body = b'{"mode":"solid","r":0,"g":255,"b":0,"schema_version":"1.0"}'
    off_body = b'{"mode":"off","r":0,"g":0,"b":0,"schema_version":"1.0"}'
    off_delay_s = max(0.0, args.off_delay_ms / 1000)
    last_event = ""
    led_on = False
    recording_active = False
    recording_seen = False
    pending_off: Timer | None = None

    def publish_led(body: bytes, event_received_ms: float, event: str) -> None:
        if not stackchan_is_online(pair):
            STACKCHAN_PRESENCE.note_skip(pair, f"fast-touch led {event}")
            return
        client.publish(pair.led_topic, body, qos=0, retain=False)
        elapsed_ms = (time.monotonic() * 1000) - event_received_ms
        if args.verbose:
            print(f"[bridge] fast-touch {event} -> led in {elapsed_ms:.2f}ms", flush=True)

    def cancel_pending_off() -> None:
        nonlocal pending_off
        if pending_off is not None:
            pending_off.cancel()
            pending_off = None

    def set_green(event_received_ms: float, event: str) -> None:
        nonlocal led_on
        cancel_pending_off()
        if led_on:
            return
        led_on = True
        publish_led(green_body, event_received_ms, event)

    def set_off(event_received_ms: float, event: str) -> None:
        nonlocal led_on, pending_off
        pending_off = None
        if not led_on:
            return
        led_on = False
        publish_led(off_body, event_received_ms, event)

    def schedule_off(event_received_ms: float, event: str) -> None:
        nonlocal pending_off
        cancel_pending_off()
        if off_delay_s <= 0:
            set_off(event_received_ms, event)
            return
        pending_off = Timer(off_delay_s, set_off, args=(event_received_ms, event))
        pending_off.daemon = True
        pending_off.start()

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        nonlocal last_event, recording_active, recording_seen
        event_received_ms = time.monotonic() * 1000
        raw_payload = message.payload
        if message.topic == pair.events_topic:
            note_stackchan_seen(pair)
        if b'"event":"touch_down"' in raw_payload:
            event = "touch_down"
        elif b'"event":"touch_up"' in raw_payload:
            event = "touch_up"
        elif b'"event":"recording_started"' in raw_payload:
            event = "recording_started"
        elif b'"event":"recording_stopped"' in raw_payload:
            event = "recording_stopped"
        elif message.topic == pair.status_topic:
            try:
                data = json.loads(raw_payload.decode("utf-8"))
            except json.JSONDecodeError:
                return
            if message_is_retained(message):
                return
            recording = data.get("recording")
            if recording is True:
                note_stackchan_status(pair, data)
                event = "status_recording_true"
            elif recording is False:
                note_stackchan_status(pair, data)
                event = "status_recording_false"
            else:
                return
        else:
            try:
                data = json.loads(raw_payload.decode("utf-8"))
            except json.JSONDecodeError:
                if args.verbose:
                    print(f"[bridge] touch event invalid json topic={message.topic}", flush=True)
                return
            event = optional_string(data.get("event"))
            if event not in {"touch_down", "touch_up", "recording_started", "recording_stopped"}:
                return
        if event in {"touch_down", "touch_up"} and (
            b'"source":"head_touch_left"' in raw_payload or b'"source":"head_touch_right"' in raw_payload
        ):
            if args.verbose:
                print(f"[bridge] fast-touch {event} ignored for side head touch", flush=True)
            return
        if event == last_event:
            return
        last_event = event

        if event in {"touch_down", "recording_started", "status_recording_true"}:
            if event == "touch_down":
                recording_seen = False
            if event in {"recording_started", "status_recording_true"}:
                recording_active = True
                recording_seen = True
            set_green(event_received_ms, event)
        elif event == "recording_stopped":
            recording_active = False
            recording_seen = True
            schedule_off(event_received_ms, event)
        elif event == "status_recording_false":
            if recording_active:
                recording_active = False
                schedule_off(event_received_ms, event)
        elif event == "touch_up" and args.verbose:
            print("[bridge] fast-touch touch_up ignored; waiting for recording_stopped", flush=True)

    client.on_message = on_message
    connect_and_start(client, config.mqtt)
    client.subscribe([(pair.events_topic, 0), (pair.status_topic, 0)])
    print(
        f"[bridge] fast touch lamp on {pair.events_topic}; "
        f"touch=green, off after recording stops + {args.off_delay_ms}ms",
        flush=True,
    )
    try:
        while True:
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


def watch_touch_emotions(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)
    state = TouchEmotionState()
    done = Event()

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            if args.verbose:
                print("[bridge] touch-emotion ignored invalid json", flush=True)
            return
        if not isinstance(payload, dict):
            return
        if message.topic == pair.status_topic and message_is_retained(message):
            return
        note_stackchan_status(pair, payload)
        actions, reasons = build_touch_emotion_actions(payload, state)
        if not actions:
            return
        pause_life_animation(pair.pair_id, args.life_pause_s, f"side touch {','.join(reasons)}")
        messages, errors = actions_to_topic_payloads(pair, actions, f"touch-{uuid.uuid4().hex[:10]}")
        if errors:
            print(f"[bridge] touch-emotion ignored invalid actions: {errors}", file=sys.stderr, flush=True)
        publish_action_messages(client, messages, pair, wait=False)
        if args.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] [bridge] touch emotion {reasons}: {len(messages)} action(s)", flush=True)
        if args.once:
            done.set()

    client.on_message = on_message
    connect_and_start(client, config.mqtt)
    client.subscribe([(pair.events_topic, 0), (pair.status_topic, 0)])
    print(f"[{time.strftime('%H:%M:%S')}] [bridge] touch emotions active on {pair.events_topic}", flush=True)
    try:
        while not done.wait(0.25):
            pass
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


def watch_sensors(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)
    state = SensorReactionState()
    done = Event()
    latest_status: dict[str, Any] | None = None

    def publish_reactions(actions: list[dict[str, Any]], reasons: list[str]) -> None:
        nonlocal latest_status
        if not actions:
            return
        fresh_status = read_latest_status(config, pair, timeout_s=0.25)
        urgent_orientation = any(reason in {"sideways", "upright"} for reason in reasons)
        if isinstance(fresh_status, dict):
            if status_is_busy(fresh_status) and not urgent_orientation:
                if args.verbose:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] [bridge] sensor reaction skipped while busy: {reasons}",
                        flush=True,
                    )
                return
        if fresh_status:
            latest_status = fresh_status
        pause_life_animation(pair.pair_id, args.life_pause_s, f"sensor reaction {','.join(reasons)}")
        immediate_actions = [
            action for action in actions
            if action_name(action) not in {"local_tts", "tts", "speak"}
        ]
        delayed_tts_actions = [
            action for action in actions
            if action_name(action) in {"local_tts", "tts", "speak"}
        ]
        messages, errors = actions_to_topic_payloads(
            pair,
            immediate_actions,
            f"sensor-{uuid.uuid4().hex[:10]}",
            config=config,
        )
        for error in errors:
            print(f"[{time.strftime('%H:%M:%S')}] [bridge] sensor reaction ignored action: {error}", file=sys.stderr, flush=True)
        if args.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] [bridge] sensor reaction {reasons}: {actions}", flush=True)
        else:
            print(f"[{time.strftime('%H:%M:%S')}] [bridge] sensor reaction {reasons}: {len(actions)} action(s)", flush=True)
        publish_action_messages(client, messages, pair, wait=False)
        if delayed_tts_actions:
            tts_messages, tts_errors = actions_to_topic_payloads(
                pair,
                delayed_tts_actions,
                f"sensor-{uuid.uuid4().hex[:10]}",
                config=config,
            )
            for error in tts_errors:
                print(f"[{time.strftime('%H:%M:%S')}] [bridge] sensor reaction ignored tts: {error}", file=sys.stderr, flush=True)
            publish_action_messages(client, tts_messages, pair, wait=False)
        if args.once:
            done.set()

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        nonlocal latest_status
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return

        if message.topic == pair.events_topic:
            note_stackchan_status(pair, payload)
            if payload.get("event") != "interaction":
                return
            source_value = payload.get("source")
            source = source_value.strip() if isinstance(source_value, str) and source_value.strip() else "sensor"
            if args.verbose:
                print(f"[{time.strftime('%H:%M:%S')}] [bridge] sensor event: {json.dumps(payload, ensure_ascii=False)}", flush=True)

            def react_to_event() -> None:
                nonlocal latest_status
                if source == "orientation":
                    event_status = merge_sensor_event_status(latest_status, payload)
                else:
                    fresh_status = read_latest_status(config, pair, timeout_s=0.6)
                    if isinstance(fresh_status, dict):
                        latest_status = fresh_status
                    event_status = merge_sensor_event_status(latest_status, payload)
                actions, reasons = build_sensor_reaction_actions(event_status, state, source_hint=source)
                publish_reactions(actions, reasons)

            if source == "orientation":
                react_to_event()
            else:
                timer = Timer(0.12, react_to_event)
                timer.daemon = True
                timer.start()
            return

        if message.topic != pair.status_topic:
            return
        if message_is_retained(message):
            return

        latest_status = payload
        note_stackchan_status(pair, payload)
        actions, reasons = build_sensor_reaction_actions(payload, state)
        publish_reactions(actions, reasons)

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        client.subscribe([(pair.status_topic, 0), (pair.events_topic, 0)])
        print(
            f"[{time.strftime('%H:%M:%S')}] [bridge] sensor watcher active on {pair.status_topic}; "
            "IMU shake/sideways + LTR553 proximity wake/reactions",
            flush=True,
        )
        while not done.wait(0.25):
            pass
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


def watch_idle_sleep(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)
    timeout_s = max(5.0, float(args.timeout_s))
    poll_s = max(0.2, float(args.poll_s))
    retry_s = max(5.0, float(args.retry_s))
    state_lock = RLock()
    last_activity_at = time.monotonic()
    last_sleep_sent_at = 0.0
    sleep_sent = False
    display_sleeping = False
    busy = False
    done = Event()

    def mark_activity(reason: str, log: bool = True) -> None:
        nonlocal last_activity_at, sleep_sent
        last_activity_at = time.monotonic()
        sleep_sent = False
        if log:
            print(f"[{time.strftime('%H:%M:%S')}] [bridge] idle timer reset: {reason}", flush=True)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        nonlocal display_sleeping, busy, sleep_sent
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return

        with state_lock:
            if message.topic == pair.status_topic:
                if message_is_retained(message):
                    return
                note_stackchan_status(pair, payload)
                sleeping_value = status_bool(payload.get("display_sleeping"))
                if sleeping_value is True:
                    display_sleeping = True
                    sleep_sent = True
                elif sleeping_value is False and display_sleeping:
                    display_sleeping = False
                    mark_activity("display woke")

                is_busy = status_is_busy(payload)
                if is_busy:
                    mark_activity("recording/speaking", log=not busy)
                elif busy:
                    mark_activity("recording/speaking stopped")
                busy = is_busy
                return

            if message.topic == pair.events_topic:
                note_stackchan_status(pair, payload)
                event = optional_string(payload.get("event"))
                if event in HUMAN_ACTIVITY_EVENTS:
                    display_sleeping = False
                    mark_activity(event)
                return

            if command_requests_display_sleep(pair, message.topic, payload):
                display_sleeping = True
                sleep_sent = True
                pause_life_animation(pair.pair_id, 12.0, "display sleep command")
                return

            if command_counts_as_idle_activity(pair, message.topic, payload):
                if payload.get("display_wake") is True or payload.get("action") == "display_wake":
                    display_sleeping = False
                mark_activity(f"command {message.topic.rsplit('/', 1)[-1]}")

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        client.subscribe([(pair.status_topic, 0), (pair.events_topic, 0), (f"{pair.mqtt_prefix}/cmd/#", 0)])
        print(
            f"[{time.strftime('%H:%M:%S')}] [bridge] idle sleep active for {pair.pair_id}: "
            f"{timeout_s:.0f}s without human/action -> display sleep, no motion",
            flush=True,
        )
        while not done.wait(poll_s):
            with state_lock:
                now = time.monotonic()
                should_sleep = (
                    not display_sleeping
                    and not busy
                    and (now - last_activity_at) >= timeout_s
                    and (not sleep_sent or (now - last_sleep_sent_at) >= retry_s)
                )
                if not should_sleep:
                    continue
                request_id = f"idle-sleep-{uuid.uuid4().hex[:10]}"
                payload = build_idle_sleep_payload(request_id)
                last_sleep_sent_at = now
                sleep_sent = True

            pause_life_animation(pair.pair_id, 20.0, "idle sleep")
            if not stackchan_is_online(pair):
                STACKCHAN_PRESENCE.note_skip(pair, "idle sleep command")
                continue
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            result = client.publish(pair.device_topic, body, qos=1, retain=False)
            result.wait_for_publish(timeout=5)
            print(
                f"[{time.strftime('%H:%M:%S')}] [bridge] idle sleep after {timeout_s:.0f}s: {body}",
                flush=True,
            )
            if args.once:
                done.set()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


def build_multipart_form_data(fields: dict[str, str], file_field: str, filename: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"h2s-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(data)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def transcribe_wav_groq_bytes(audio: bytes, speech: SpeechConfig) -> str:
    if not speech.groq_api_key:
        raise ConfigError("Groq STT key missing. Set H2S_GROQ_API_KEY or GROQ_API_KEY in .env.")
    fields = {
        "model": speech.groq_model,
        "response_format": "json",
        "temperature": "0",
    }
    if speech.language:
        fields["language"] = speech.language
    if speech.prompt:
        fields["prompt"] = speech.prompt
    body, boundary = build_multipart_form_data(fields, "file", "stackchan.wav", "audio/wav", audio)
    request = urllib.request.Request(
        speech.groq_url,
        data=body,
        headers={
            "Authorization": f"Bearer {speech.groq_api_key}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "hermes2stackchan/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=speech.timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ConfigError(f"Groq STT HTTP {exc.code}: {error_body}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Groq STT returned non-JSON response: {raw[:500]}") from exc
    text = payload.get("text")
    if not isinstance(text, str):
        raise ConfigError("Groq STT response did not contain text")
    return text.strip()


def transcribe_audio_bytes(audio: bytes, speech: SpeechConfig) -> tuple[str, str]:
    provider = speech.provider.lower()
    if provider != "groq":
        raise ConfigError(f"unsupported STT provider for bridge HTTP audio: {speech.provider}")
    return transcribe_wav_groq_bytes(audio, speech), "groq"


def archive_audio_if_requested(audio: bytes, speech: SpeechConfig, request_id: str) -> Path | None:
    if not speech.archive_dir:
        return None
    archive_dir = Path(speech.archive_dir).expanduser()
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_request_id = "".join(char for char in request_id if char.isalnum() or char in {"-", "_"})[:48] or uuid.uuid4().hex[:12]
    path = archive_dir / f"stackchan-{time.strftime('%Y%m%d-%H%M%S')}-{safe_request_id}.wav"
    path.write_bytes(audio)
    return path


def tts_dir_for(speech: SpeechConfig) -> Path:
    path = Path(speech.tts_dir or Path.home() / ".hermes" / "stackchan_tts").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_tts_wav(text: str, speech: SpeechConfig, request_id: str) -> str:
    clean = text.strip()
    if not clean:
        return ""
    out_id = "".join(char for char in request_id if char.isalnum() or char in {"-", "_"})[:48] or uuid.uuid4().hex
    out_path = tts_dir_for(speech) / f"{out_id}.wav"
    tmp_mp3 = out_path.with_suffix(".mp3")

    engine = speech.tts_engine.strip().lower()
    if engine in {"edge", "katja", "edge-tts", "edge_tts"}:
        python_bin = speech.edge_tts_python or sys.executable
        subprocess.run(
            [
                python_bin,
                "-m",
                "edge_tts",
                "--voice",
                speech.edge_tts_voice,
                "--rate",
                speech.edge_tts_rate,
                "--text",
                clean,
                "--write-media",
                str(tmp_mp3),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(tmp_mp3),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(out_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        tmp_mp3.unlink(missing_ok=True)
    else:
        subprocess.run(
            ["espeak-ng", "-v", "de", "-s", "180", "-w", str(out_path), clean],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_path), "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out_path.with_suffix(".tmp.wav"))],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        out_path.with_suffix(".tmp.wav").replace(out_path)
    return f"/stackchan/tts/{out_path.name}"


def image_dir_for(speech: SpeechConfig) -> Path:
    path = Path(speech.image_dir or Path.home() / ".hermes" / "stackchan_images").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_asset_id(request_id: str) -> str:
    return "".join(char for char in request_id if char.isalnum() or char in {"-", "_"})[:48] or uuid.uuid4().hex[:16]


def bridge_public_url_for_request(config: BridgeConfig, handler: http.server.BaseHTTPRequestHandler | None = None) -> str:
    if config.speech.bridge_public_url:
        return config.speech.bridge_public_url.rstrip("/")
    if handler is not None:
        host = handler.headers.get("Host") or f"{handler.server.server_address[0]}:{handler.server.server_address[1]}"
        return f"http://{host}"
    raise ConfigError("H2S_BRIDGE_PUBLIC_URL or request Host is required")


def content_type_from_filename(path: str, fallback: str = "image/jpeg") -> str:
    guessed = mimetypes.guess_type(path)[0]
    return guessed if guessed and guessed.startswith("image/") else fallback


def parse_data_url(data_url: str) -> tuple[bytes, str]:
    header, separator, payload = data_url.partition(",")
    if not separator or not header.startswith("data:"):
        raise ConfigError("invalid image data_url")
    content_type = header[5:].split(";", 1)[0] or "image/jpeg"
    if ";base64" not in header:
        raise ConfigError("image data_url must be base64 encoded")
    return base64.b64decode(payload, validate=True), content_type


def download_image_bytes(url: str, max_bytes: int, timeout_s: float = 12.0) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        content_type = response.headers.get_content_type() or content_type_from_filename(url)
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ConfigError(f"image too large; max {max_bytes} bytes")
    if not content_type.startswith("image/"):
        content_type = content_type_from_filename(url)
    return data, content_type


def image_result_aspect_score(result: dict[str, Any], target_aspect: float = STACKCHAN_DISPLAY_ASPECT) -> float:
    width = parse_int(result.get("width"), 0, "image.width")
    height = parse_int(result.get("height"), 0, "image.height")
    if width <= 0 or height <= 0:
        return 500.0
    aspect = width / height
    aspect_penalty = abs(math.log(max(aspect, 0.01) / target_aspect)) * 100.0
    size_penalty = 0.0
    if width < 320 or height < 240:
        size_penalty += 60.0
    if width < 160 or height < 120:
        size_penalty += 120.0
    url = optional_string(result.get("url")) or ""
    if not re_like_image_url(url):
        size_penalty += 12.0
    return aspect_penalty + size_penalty


def re_like_image_url(url: str) -> bool:
    clean = urllib.parse.urlsplit(url).path.lower()
    return clean.endswith((".jpg", ".jpeg", ".png", ".webp"))


IMAGE_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "aus",
    "bild",
    "der",
    "die",
    "ein",
    "eine",
    "einen",
    "en",
    "foto",
    "from",
    "image",
    "im",
    "in",
    "mir",
    "of",
    "photo",
    "photograph",
    "picture",
    "show",
    "the",
    "von",
    "zeig",
}


def image_search_queries(query: str) -> list[str]:
    words = [word.strip(" ,.;:!?()[]{}\"'").strip() for word in query.split()]
    words = [word for word in words if word]
    clean_words = [word for word in words if word.lower() not in IMAGE_QUERY_STOPWORDS]
    variants: list[str] = []

    def add(value: str) -> None:
        value = " ".join(value.split()).strip()
        if value and value.lower() not in {item.lower() for item in variants}:
            variants.append(value)

    add(query)
    if clean_words and clean_words != words:
        add(" ".join(clean_words))
    if len(clean_words) > 3:
        add(" ".join(clean_words[:3]))
        add(" ".join(clean_words[-3:]))
    if len(clean_words) > 1:
        add(" ".join(clean_words[:2]))
    if clean_words:
        add(clean_words[0])
    return variants


def wikimedia_commons_candidates(query: str, speech: SpeechConfig, limit: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": clamp_int(limit, 1, 20),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "format": "json",
        }
    )
    request = urllib.request.Request(
        f"{WIKIMEDIA_COMMONS_API_URL}?{params}",
        headers={"User-Agent": HTTP_USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=speech.timeout_s) as response:
        payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    pages = ((payload.get("query") or {}).get("pages") or {}) if isinstance(payload, dict) else {}
    if not isinstance(pages, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        image_info = page.get("imageinfo") or []
        if not image_info or not isinstance(image_info[0], dict):
            continue
        info = image_info[0]
        mime = optional_string(info.get("mime")) or ""
        if mime and not mime.startswith("image/"):
            continue
        title = optional_string(page.get("title")) or ""
        if title.lower().endswith((".svg", ".gif", ".pdf", ".tif", ".tiff")):
            continue
        candidates.append(
            {
                "url": optional_string(info.get("url")) or "",
                "title": title.removeprefix("File:"),
                "width": parse_int(info.get("width"), 0, "commons.width"),
                "height": parse_int(info.get("height"), 0, "commons.height"),
                "mime": mime,
                "foreign_landing_url": optional_string(info.get("descriptionurl")) or "",
                "creator": "",
                "license": "",
                "license_url": "",
                "provider": "wikimedia-commons",
            }
        )
    return [item for item in candidates if optional_string(item.get("url"))]


def openverse_candidates(query: str, speech: SpeechConfig, limit: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "q": query,
        "page_size": clamp_int(limit, 1, 20),
        "mature": "false",
    })
    request = urllib.request.Request(
        f"{OPENVERSE_IMAGE_SEARCH_URL}?{params}",
        headers={"User-Agent": HTTP_USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=speech.timeout_s) as response:
        payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict) or not optional_string(item.get("url")):
            continue
        normalized = dict(item)
        normalized["provider"] = "openverse"
        candidates.append(normalized)
    return candidates


def search_openverse_image(query: str, speech: SpeechConfig, limit: int = 20) -> tuple[bytes, str, dict[str, Any]]:
    query = query.strip()
    if not query:
        raise ConfigError("image search needs query")
    errors: list[str] = []
    searched: list[str] = []
    for variant in image_search_queries(query):
        candidates: list[dict[str, Any]] = []
        for provider, loader in (("openverse", openverse_candidates), ("wikimedia-commons", wikimedia_commons_candidates)):
            try:
                found = loader(variant, speech, limit)
                for item in found:
                    item["provider"] = optional_string(item.get("provider")) or provider
                    candidates.append(item)
            except Exception as exc:
                errors.append(f"{provider} {variant!r}: {exc}")
        searched.append(variant)
        if not candidates:
            continue
        candidates.sort(key=image_result_aspect_score)
        for item in candidates:
            for url_key in ("url", "thumbnail"):
                image_url = optional_string(item.get(url_key))
                if not image_url:
                    continue
                try:
                    data, content_type = download_image_bytes(image_url, speech.max_image_bytes, speech.timeout_s)
                    if not content_type.startswith("image/"):
                        raise ConfigError(f"not an image: {content_type}")
                    meta = {
                        "provider": optional_string(item.get("provider")) or "openverse",
                        "query": query,
                        "matched_query": variant,
                        "searched_queries": searched,
                        "title": optional_string(item.get("title")) or "",
                        "source_url": image_url,
                        "foreign_landing_url": optional_string(item.get("foreign_landing_url")) or "",
                        "creator": optional_string(item.get("creator")) or "",
                        "license": optional_string(item.get("license")) or "",
                        "license_url": optional_string(item.get("license_url")) or "",
                        "width": parse_int(item.get("width"), 0, "image.width"),
                        "height": parse_int(item.get("height"), 0, "image.height"),
                        "aspect_score": round(image_result_aspect_score(item), 3),
                    }
                    return data, content_type, meta
                except Exception as exc:
                    errors.append(f"{image_url}: {exc}")
                    continue
    reason = "; ".join(errors[:4])
    if reason:
        raise ConfigError(f"no downloadable image search result for {query!r}; tried {searched}; {reason}")
    raise ConfigError(f"no image search results for {query!r}; tried {searched}")


def image_bytes_from_payload(payload: dict[str, Any], speech: SpeechConfig) -> tuple[bytes, str, str]:
    data_url = optional_string(payload.get("data_url") or payload.get("image_data_url"))
    if data_url:
        data, content_type = parse_data_url(data_url)
        source = "data_url"
    elif optional_string(payload.get("image_base64") or payload.get("base64")):
        encoded = optional_string(payload.get("image_base64") or payload.get("base64")) or ""
        data = base64.b64decode(encoded, validate=True)
        content_type = optional_string(payload.get("content_type") or payload.get("mime_type")) or "image/jpeg"
        source = "base64"
    else:
        url = optional_string(payload.get("image_url") or payload.get("url"))
        if not url:
            raise ConfigError("image payload needs image_url, url, data_url, or image_base64")
        data, content_type = download_image_bytes(url, speech.max_image_bytes, speech.timeout_s)
        source = url
    if len(data) > speech.max_image_bytes:
        raise ConfigError(f"image too large; max {speech.max_image_bytes} bytes")
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    return data, content_type, source


def convert_image_to_display_jpeg(image_bytes: bytes) -> bytes:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ConfigError("Pillow is required for image display. Install with: pip install -e .") from exc

    with Image.open(io.BytesIO(image_bytes)) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((320, 240), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (320, 240), (0, 0, 0))
        x = (320 - image.width) // 2
        y = (240 - image.height) // 2
        if image.mode in {"RGBA", "LA"} or ("transparency" in image.info):
            canvas.paste(image.convert("RGBA"), (x, y), image.convert("RGBA"))
        else:
            canvas.paste(image.convert("RGB"), (x, y))
        out = io.BytesIO()
        canvas.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue()


def rgb565_to_jpeg(image_bytes: bytes, width: int, height: int) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ConfigError("Pillow is required for camera RGB565 conversion. Install with: pip install -e .") from exc

    width = clamp_int(width, 1, 640)
    height = clamp_int(height, 1, 480)
    expected = width * height * 2
    if len(image_bytes) != expected:
        raise ConfigError(f"rgb565 image has {len(image_bytes)} bytes, expected {expected}")
    rgb = bytearray(width * height * 3)
    j = 0
    for i in range(0, len(image_bytes), 2):
        # GC0308 camera frames arrive as big-endian RGB565.
        value = (image_bytes[i] << 8) | image_bytes[i + 1]
        r = ((value >> 11) & 0x1F) << 3
        g = ((value >> 5) & 0x3F) << 2
        b = (value & 0x1F) << 3
        rgb[j] = r | (r >> 5)
        rgb[j + 1] = g | (g >> 6)
        rgb[j + 2] = b | (b >> 5)
        j += 3
    image = Image.frombytes("RGB", (width, height), bytes(rgb))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=88)
    return out.getvalue()


def prepare_stackchan_image(
    config: BridgeConfig,
    handler: http.server.BaseHTTPRequestHandler | None,
    image_bytes: bytes,
    request_id: str,
) -> dict[str, Any]:
    image_id = f"{safe_asset_id(request_id)}-{hashlib.sha256(image_bytes).hexdigest()[:10]}"
    image_dir = image_dir_for(config.speech)
    raw_path = image_dir / f"{image_id}.source"
    display_path = image_dir / f"{image_id}.jpg"
    raw_path.write_bytes(image_bytes)
    display_bytes = convert_image_to_display_jpeg(image_bytes)
    display_path.write_bytes(display_bytes)
    return {
        "id": image_id,
        "path": str(display_path),
        "url_path": f"/stackchan/images/{display_path.name}",
        "url": f"{bridge_public_url_for_request(config, handler)}/stackchan/images/{display_path.name}",
        "width": 320,
        "height": 240,
        "format": "jpeg",
        "sha256": hashlib.sha256(display_bytes).hexdigest(),
        "bytes": len(display_bytes),
    }


class SpeechHttpServer(http.server.ThreadingHTTPServer):
    config: BridgeConfig
    config_path: Path
    pair: PairConfig
    mqtt_client: Any


class SpeechRequestHandler(http.server.BaseHTTPRequestHandler):
    server: SpeechHttpServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[bridge-http] {self.address_string()} {fmt % args}", flush=True)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self, request_id: str, max_bytes: int = 65536) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self.send_json(400, {"ok": False, "error": "invalid content length", "request_id": request_id})
            return None
        if length <= 0:
            self.send_json(400, {"ok": False, "error": "missing json body", "request_id": request_id})
            return None
        if length > max_bytes:
            self.send_json(413, {"ok": False, "error": "json body too large", "request_id": request_id})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json(400, {"ok": False, "error": f"invalid json: {exc}", "request_id": request_id})
            return None
        if not isinstance(payload, dict):
            self.send_json(400, {"ok": False, "error": "json body must be an object", "request_id": request_id})
            return None
        return payload

    def public_tts_url(self, tts_path: str) -> str:
        if not tts_path:
            return ""
        try:
            return tts_public_url(self.server.config, tts_path)
        except ConfigError:
            host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
            return f"http://{host}{tts_path}"

    def publish_image_to_stackchan(
        self,
        image_info: dict[str, Any],
        request_id: str,
        caption: str = "",
        duration_ms: int = 9000,
    ) -> None:
        action = {
            "action": "display_image",
            "url": image_info["url"],
            "width": image_info["width"],
            "height": image_info["height"],
            "format": image_info["format"],
            "duration_ms": duration_ms,
        }
        if caption:
            action["caption"] = caption
        publish_action_messages(
            self.server.mqtt_client,
            [action_to_topic_payload(self.server.pair, action, request_id)],
            self.server.pair,
        )

    def handle_display_image_post(self, request_id: str) -> None:
        payload = self.read_json_body(request_id, self.server.config.speech.max_image_bytes + 65536)
        if payload is None:
            return
        started = time.monotonic()
        request_id = optional_string(payload.get("request_id")) or request_id
        pair_id = optional_string(payload.get("pair_id")) or (self.headers.get("X-H2S-Pair-Id") or self.server.pair.pair_id).strip()
        if pair_id != self.server.pair.pair_id:
            self.send_json(403, {"ok": False, "error": f"wrong pair_id {pair_id!r}", "request_id": request_id})
            return
        if not stackchan_is_online(self.server.pair):
            STACKCHAN_PRESENCE.note_skip(self.server.pair, "display-image request")
            self.send_json(503, {"ok": False, "error": "stackchan offline", "request_id": request_id})
            return

        try:
            image_bytes, content_type, source = image_bytes_from_payload(payload, self.server.config.speech)
            image_info = prepare_stackchan_image(self.server.config, self, image_bytes, request_id)
            caption = safe_stackchan_text(optional_string(payload.get("caption")) or "", 80)
            duration_ms = parse_int_value(payload.get("duration_ms"), 9000, "display_image.duration_ms")
            pause_life_animation(self.server.pair.pair_id, max(12.0, duration_ms / 1000.0 + 4.0), f"image display {request_id}")
            self.publish_image_to_stackchan(image_info, f"image-{request_id}", caption, duration_ms)
            total_ms = round((time.monotonic() - started) * 1000)
            print(
                f"[bridge-http] display-image request_id={request_id} source={source} "
                f"type={content_type} bytes={len(image_bytes)} total={total_ms}ms",
                flush=True,
            )
            self.send_json(200, {"ok": True, "request_id": request_id, "image": image_info, "total_ms": total_ms})
        except Exception as exc:
            print(f"[bridge-http] display-image error request_id={request_id}: {exc}", flush=True)
            self.send_json(500, {"ok": False, "request_id": request_id, "error": str(exc)})

    def handle_search_image_post(self, request_id: str) -> None:
        payload = self.read_json_body(request_id, 65536)
        if payload is None:
            return
        started = time.monotonic()
        request_id = optional_string(payload.get("request_id")) or request_id
        pair_id = optional_string(payload.get("pair_id")) or (self.headers.get("X-H2S-Pair-Id") or self.server.pair.pair_id).strip()
        if pair_id != self.server.pair.pair_id:
            self.send_json(403, {"ok": False, "error": f"wrong pair_id {pair_id!r}", "request_id": request_id})
            return
        if not stackchan_is_online(self.server.pair):
            STACKCHAN_PRESENCE.note_skip(self.server.pair, "search-image request")
            self.send_json(503, {"ok": False, "error": "stackchan offline", "request_id": request_id})
            return
        query = optional_string(payload.get("query") or payload.get("q") or payload.get("text") or payload.get("prompt"))
        if not query:
            self.send_json(400, {"ok": False, "error": "search-image needs query", "request_id": request_id})
            return

        try:
            image_bytes, content_type, meta = search_openverse_image(
                query,
                self.server.config.speech,
                parse_int_value(payload.get("limit"), 20, "search-image.limit"),
            )
            image_info = prepare_stackchan_image(self.server.config, self, image_bytes, f"search-{request_id}")
            caption = safe_stackchan_text(optional_string(payload.get("caption")) or query, 80)
            duration_ms = parse_int_value(payload.get("duration_ms"), 9000, "search-image.duration_ms")
            pause_life_animation(self.server.pair.pair_id, max(12.0, duration_ms / 1000.0 + 4.0), f"image search {request_id}")
            self.publish_image_to_stackchan(image_info, f"search-image-{request_id}", caption, duration_ms)
            total_ms = round((time.monotonic() - started) * 1000)
            print(
                f"[bridge-http] search-image request_id={request_id} query={query!r} "
                f"type={content_type} source={meta.get('source_url', '')} total={total_ms}ms",
                flush=True,
            )
            self.send_json(
                200,
                {"ok": True, "request_id": request_id, "query": query, "image": image_info, "source": meta, "total_ms": total_ms},
            )
        except Exception as exc:
            print(f"[bridge-http] search-image error request_id={request_id}: {exc}", flush=True)
            self.send_json(500, {"ok": False, "request_id": request_id, "error": str(exc)})

    def handle_photo_post(self, request_id: str) -> None:
        started = time.monotonic()
        pair_id = (self.headers.get("X-H2S-Pair-Id") or self.server.pair.pair_id).strip()
        if pair_id != self.server.pair.pair_id:
            self.send_json(403, {"ok": False, "error": f"wrong pair_id {pair_id!r}", "request_id": request_id})
            return
        note_stackchan_seen(self.server.pair)
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self.send_json(400, {"ok": False, "error": "invalid content length", "request_id": request_id})
            return
        if length <= 0:
            self.send_json(400, {"ok": False, "error": "missing image body", "request_id": request_id})
            return
        if length > self.server.config.speech.max_image_bytes:
            self.send_json(413, {"ok": False, "error": "image too large", "request_id": request_id})
            return
        image_bytes = self.rfile.read(length)
        content_type = self.headers.get_content_type() or "image/jpeg"
        image_format = (self.headers.get("X-H2S-Image-Format") or self.headers.get("X-StackChan-Image-Format") or "").strip().lower()
        if image_format == "rgb565":
            width = parse_int(
                self.headers.get("X-H2S-Image-Width") or self.headers.get("X-StackChan-Image-Width"),
                320,
                "X-H2S-Image-Width",
            )
            height = parse_int(
                self.headers.get("X-H2S-Image-Height") or self.headers.get("X-StackChan-Image-Height"),
                240,
                "X-H2S-Image-Height",
            )
            image_bytes = rgb565_to_jpeg(image_bytes, width, height)
            content_type = "image/jpeg"
        elif image_format in {"jpeg", "jpg"}:
            content_type = "image/jpeg"
        elif image_format == "png":
            content_type = "image/png"
        if not content_type.startswith("image/"):
            self.send_json(415, {"ok": False, "error": "expected image content type or X-H2S-Image-Format", "request_id": request_id})
            return

        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        prompt = (
            self.headers.get("X-H2S-Photo-Prompt")
            or (query.get("prompt", [""])[0] if query else "")
            or "Beschreibe kurz auf Deutsch, was auf diesem StackChan-Kamerabild zu sehen ist. "
               "Wenn es eine sinnvolle Aktion gibt, schlage sie knapp vor."
        )
        try:
            pause_life_animation(self.server.pair.pair_id, 60.0, f"photo {request_id}")
            image_info = prepare_stackchan_image(self.server.config, self, image_bytes, f"photo-{request_id}")
            self.publish_image_to_stackchan(image_info, f"photo-preview-{request_id}", "KAMERA", 2500)
            status_started = time.monotonic()
            status = read_latest_status(self.server.config, self.server.pair, timeout_s=1.0)
            status_ms = round((time.monotonic() - status_started) * 1000)
            capabilities = read_optional_text(self.server.pair.capabilities_file, self.server.config_path)
            personality = read_optional_text(self.server.pair.personality_file, self.server.config_path)
            hermes_started = time.monotonic()
            hermes_response = ask_hermes_vision_http(
                self.server.config,
                self.server.pair,
                capabilities,
                personality,
                status,
                prompt,
                image_bytes,
                content_type,
            )
            hermes_ms = round((time.monotonic() - hermes_started) * 1000)
            display_text = speech_text_from_hermes_response(hermes_response, "Ich habe das Bild bekommen.")
            actions = ensure_reply_action(hermes_response)
            actions, scheduled_reminders, reminder_errors = schedule_reminders_from_actions(
                self.server.config,
                self.server.pair,
                actions,
                f"photo-reminder-{request_id}",
            )
            action_messages, action_errors = actions_to_topic_payloads(
                self.server.pair,
                actions,
                f"photo-{request_id}",
                skip_actions={"say"},
                config=self.server.config,
                handler=self,
            )
            action_errors.extend(reminder_errors)
            if display_text and not any(
                optional_string(action.get("action")).lower() in {"display", "display_image", "image"}
                for action in actions
            ):
                action_messages.insert(
                    0,
                    (
                        self.server.pair.display_topic,
                        build_display_payload(display_text, 9000, f"photo-display-{request_id}"),
                    ),
                )
            tts_started = time.monotonic()
            tts_path = make_tts_wav(display_text, self.server.config.speech, f"photo-{request_id}") if display_text else ""
            tts_ms = round((time.monotonic() - tts_started) * 1000) if tts_path else 0
            tts_url = self.public_tts_url(tts_path)
            if tts_url:
                action_messages.append(action_to_topic_payload(
                    self.server.pair,
                    {"action": "audio", "audio_action": "play_tts_url", "url": tts_url},
                    f"photo-tts-{request_id}",
                ))
            publish_started = time.monotonic()
            publish_action_messages(self.server.mqtt_client, action_messages, self.server.pair)
            mqtt_ms = round((time.monotonic() - publish_started) * 1000)
            for reminder in scheduled_reminders:
                print(f"[bridge-http] scheduled reminder from photo {reminder['id']}: {reminder['text']}", flush=True)
            total_ms = round((time.monotonic() - started) * 1000)
            print(
                f"[bridge-http] photo request_id={request_id} bytes={len(image_bytes)} "
                f"status={status_ms}ms hermes={hermes_ms}ms tts={tts_ms}ms mqtt={mqtt_ms}ms total={total_ms}ms",
                flush=True,
            )
            if action_errors:
                print(f"[bridge-http] photo ignored invalid actions request_id={request_id}: {action_errors}", flush=True)
            self.send_json(
                200,
                {
                    "ok": True,
                    "request_id": request_id,
                    "reply": display_text[:240],
                    "image": image_info,
                    "tts_url": tts_url,
                    "hermes_ms": hermes_ms,
                    "tts_ms": tts_ms,
                    "total_ms": total_ms,
                    "action_errors": action_errors,
                },
            )
        except Exception as exc:
            print(f"[bridge-http] photo error request_id={request_id}: {exc}", flush=True)
            self.send_json(500, {"ok": False, "request_id": request_id, "error": str(exc)})

    def handle_notify_post(self, request_id: str) -> None:
        payload = self.read_json_body(request_id)
        if payload is None:
            return
        request_id = optional_string(payload.get("request_id")) or request_id
        pair_id = optional_string(payload.get("pair_id")) or (self.headers.get("X-H2S-Pair-Id") or self.server.pair.pair_id).strip()
        if pair_id != self.server.pair.pair_id:
            self.send_json(403, {"ok": False, "error": f"wrong pair_id {pair_id!r}", "request_id": request_id})
            return
        if not stackchan_is_online(self.server.pair):
            STACKCHAN_PRESENCE.note_skip(self.server.pair, "notify request")
            self.send_json(503, {"ok": False, "error": "stackchan offline", "request_id": request_id})
            return

        started = time.monotonic()
        display_text = safe_stackchan_text(notify_text_from_payload(payload), MAX_STACKCHAN_DISPLAY_CHARS)
        spoken_text = safe_tts_text(notify_text_from_payload(payload))
        if not spoken_text:
            self.send_json(400, {"ok": False, "error": "notify needs text, reply, or message", "request_id": request_id})
            return

        pause_life_animation(self.server.pair.pair_id, 45.0, f"external notify {request_id}")
        time.sleep(0.45)
        try:
            tts_started = time.monotonic()
            tts_path = make_tts_wav(spoken_text, self.server.config.speech, f"notify-{request_id}")
            tts_ms = round((time.monotonic() - tts_started) * 1000)
            tts_url = self.public_tts_url(tts_path)
            actions = notify_actions_from_payload(payload, display_text, bool(tts_url))
            action_messages, action_errors = actions_to_topic_payloads(
                self.server.pair,
                actions,
                f"notify-{request_id}",
                config=self.server.config,
                handler=self,
            )
            action_messages.append(
                action_to_topic_payload(
                    self.server.pair,
                    {"action": "audio", "audio_action": "play_tts_url", "url": tts_url},
                    f"notify-tts-{request_id}",
                )
            )
            publish_started = time.monotonic()
            publish_action_messages(self.server.mqtt_client, action_messages, self.server.pair)
            mqtt_ms = round((time.monotonic() - publish_started) * 1000)
            total_ms = round((time.monotonic() - started) * 1000)
            print(
                f"[bridge-http] notify request_id={request_id} chars={len(spoken_text)} "
                f"actions={len(action_messages)} mqtt={mqtt_ms}ms tts={tts_ms}ms total={total_ms}ms",
                flush=True,
            )
            if action_errors:
                print(f"[bridge-http] notify ignored invalid actions request_id={request_id}: {action_errors}", flush=True)
            self.send_json(
                200,
                {
                    "ok": True,
                    "request_id": request_id,
                    "reply": display_text,
                    "tts_path": tts_path,
                    "tts_url": tts_url,
                    "tts_ms": tts_ms,
                    "mqtt_ms": mqtt_ms,
                    "total_ms": total_ms,
                    "actions_published": len(action_messages),
                    "action_errors": action_errors,
                },
            )
        except Exception as exc:
            print(f"[bridge-http] notify error request_id={request_id}: {exc}", flush=True)
            self.send_json(500, {"ok": False, "request_id": request_id, "error": str(exc)})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self.send_json(200, {"ok": True, "service": "hermes2stackchan-bridge", "pair_id": self.server.pair.pair_id})
            return
        if path.startswith("/stackchan/tts/") and path.endswith(".wav"):
            filename = Path(path).name
            wav_path = tts_dir_for(self.server.config.speech) / filename
            if not wav_path.exists():
                self.send_json(404, {"ok": False, "error": "tts not found"})
                return
            body = wav_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/stackchan/images/") and (path.endswith(".jpg") or path.endswith(".jpeg")):
            filename = Path(path).name
            image_path = image_dir_for(self.server.config.speech) / filename
            if not image_path.exists():
                self.send_json(404, {"ok": False, "error": "image not found"})
                return
            body = image_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-H2S-Image-Width", "320")
            self.send_header("X-H2S-Image-Height", "240")
            self.send_header("X-H2S-Image-Format", "jpeg")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/stackchan/notify", "/hermes/notify"}:
            request_id = (
                self.headers.get("X-H2S-Request-Id")
                or self.headers.get("X-StackChan-Request-Id")
                or uuid.uuid4().hex
            ).strip()
            self.handle_notify_post(request_id)
            return
        if path in {"/stackchan/display-image", "/hermes/display-image"}:
            request_id = (
                self.headers.get("X-H2S-Request-Id")
                or self.headers.get("X-StackChan-Request-Id")
                or uuid.uuid4().hex
            ).strip()
            self.handle_display_image_post(request_id)
            return
        if path in {"/stackchan/search-image", "/hermes/search-image"}:
            request_id = (
                self.headers.get("X-H2S-Request-Id")
                or self.headers.get("X-StackChan-Request-Id")
                or uuid.uuid4().hex
            ).strip()
            self.handle_search_image_post(request_id)
            return
        if path in {"/stackchan/photo", "/hermes/photo"}:
            request_id = (
                self.headers.get("X-H2S-Request-Id")
                or self.headers.get("X-StackChan-Request-Id")
                or uuid.uuid4().hex
            ).strip()
            self.handle_photo_post(request_id)
            return
        if path != "/stackchan/audio":
            self.send_json(404, {"ok": False, "error": "not found"})
            return

        started = time.monotonic()
        request_id = (
            self.headers.get("X-H2S-Request-Id")
            or self.headers.get("X-StackChan-Request-Id")
            or uuid.uuid4().hex
        ).strip()
        pair_id = (self.headers.get("X-H2S-Pair-Id") or self.server.pair.pair_id).strip()
        if pair_id != self.server.pair.pair_id:
            self.send_json(403, {"ok": False, "error": f"wrong pair_id {pair_id!r}", "request_id": request_id})
            return
        note_stackchan_seen(self.server.pair)

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self.send_json(400, {"ok": False, "error": "invalid content length", "request_id": request_id})
            return
        if length <= 44:
            self.send_json(400, {"ok": False, "error": "missing wav body", "request_id": request_id})
            return
        if length > self.server.config.speech.max_audio_bytes:
            self.send_json(413, {"ok": False, "error": "audio too large", "request_id": request_id})
            return

        audio = self.rfile.read(length)
        read_ms = round((time.monotonic() - started) * 1000)
        archive_path = archive_audio_if_requested(audio, self.server.config.speech, request_id)
        if archive_path:
            print(f"[bridge-http] archived wav: {archive_path}", flush=True)
        print(f"[bridge-http] received {len(audio)} bytes in {read_ms}ms request_id={request_id}", flush=True)
        pause_life_animation(self.server.pair.pair_id, 75.0, f"speech request {request_id}")

        try:
            stt_started = time.monotonic()
            transcript, backend = transcribe_audio_bytes(audio, self.server.config.speech)
            stt_ms = round((time.monotonic() - stt_started) * 1000)
            post_tts_system_action = ""
            if not transcript:
                display_text = "NICHTS VERSTANDEN"
                hermes_response: dict[str, Any] = {"reply": display_text, "actions": [{"action": "say", "text": display_text, "emotion": "question"}]}
                hermes_ms = 0
                status_ms = 0
                action_errors: list[str] = []
                mqtt_ms = 0
                action_count = 0
            else:
                status: dict[str, Any] | None = None
                status_ms = 0
                direct_command = direct_local_command_from_transcript(transcript)
                if direct_command is None and local_command_may_need_status(transcript):
                    status_started = time.monotonic()
                    status = read_latest_status(self.server.config, self.server.pair, timeout_s=0.8)
                    status_ms = round((time.monotonic() - status_started) * 1000)
                    direct_command = direct_local_command_from_transcript(transcript, status)
                if direct_command:
                    display_text, actions, post_tts_system_action = direct_command
                    hermes_response = {"reply": display_text, "actions": actions}
                    hermes_ms = 0
                    actions, scheduled_reminders, reminder_errors = schedule_reminders_from_actions(
                        self.server.config,
                        self.server.pair,
                        actions,
                        f"speech-reminder-{request_id}",
                    )
                    print(
                        f"[bridge-http] local intent=direct request_id={request_id}: hermes=0ms "
                        f"post_tts={post_tts_system_action or '-'} actions={len(actions)}",
                        flush=True,
                    )
                else:
                    if status is None:
                        status_started = time.monotonic()
                        status = read_latest_status(self.server.config, self.server.pair, timeout_s=1.0)
                        status_ms = round((time.monotonic() - status_started) * 1000)
                    capabilities = read_optional_text(
                        self.server.pair.capabilities_file,
                        self.server.config_path,
                    )
                    personality = read_optional_text(
                        self.server.pair.personality_file,
                        self.server.config_path,
                    )
                    hermes_started = time.monotonic()
                    hermes_response = ask_hermes_http(
                        self.server.config,
                        self.server.pair,
                        capabilities,
                        personality,
                        status,
                        transcript,
                    )
                    hermes_ms = round((time.monotonic() - hermes_started) * 1000)
                    actions = ensure_reply_action(hermes_response)
                    actions, post_tts_system_action = split_post_tts_system_actions(actions)
                    actions, scheduled_reminders, reminder_errors = schedule_reminders_from_actions(
                        self.server.config,
                        self.server.pair,
                        actions,
                        f"speech-reminder-{request_id}",
                    )
                action_messages, action_errors = actions_to_topic_payloads(
                    self.server.pair,
                    actions,
                    f"speech-{request_id}",
                    skip_actions={"say"},
                    config=self.server.config,
                    handler=self,
                )
                action_errors.extend(reminder_errors)
                publish_started = time.monotonic()
                publish_action_messages(self.server.mqtt_client, action_messages, self.server.pair)
                mqtt_ms = round((time.monotonic() - publish_started) * 1000)
                action_count = len(action_messages)
                for reminder in scheduled_reminders:
                    print(
                        f"[bridge-http] scheduled reminder {reminder['id']} "
                        f"for {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(reminder['due_ts'])))}: {reminder['text']}",
                        flush=True,
                    )
                display_text = speech_text_from_hermes_response(hermes_response, transcript)
            follow_up_listen = should_listen_for_followup(hermes_response, display_text)

            tts_started = time.monotonic()
            tts_path = make_tts_wav(display_text, self.server.config.speech, request_id) if display_text else ""
            tts_ms = round((time.monotonic() - tts_started) * 1000) if tts_path else 0
            host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
            tts_url = f"http://{host}{tts_path}" if tts_path else ""
            total_ms = round((time.monotonic() - started) * 1000)
            print(
                f"[bridge-http] transcript after {stt_ms}ms via {backend}: {transcript!r}; "
                f"hermes={hermes_ms}ms status={status_ms}ms actions={action_count} mqtt={mqtt_ms}ms tts={tts_ms}ms "
                f"follow_up={follow_up_listen} reply={display_text!r}",
                flush=True,
            )
            if action_errors:
                print(
                    f"[bridge-http] ignored invalid Hermes actions request_id={request_id}: {action_errors}",
                    flush=True,
                )
            response_payload = {
                "ok": bool(display_text),
                "request_id": request_id,
                "tts_path": tts_path,
                "tts_url": tts_url,
                "reply": display_text[:240],
                "follow_up_listen": follow_up_listen,
                "follow_up_source": "hermes_question" if follow_up_listen else "",
                "stt_ms": stt_ms,
                "hermes_ms": hermes_ms,
                "tts_ms": tts_ms,
                "total_ms": total_ms,
                "actions_published": action_count,
            }
            if post_tts_system_action:
                response_payload["post_tts_system_action"] = post_tts_system_action
            self.send_json(200, response_payload)
        except Exception as exc:
            error_text = f"SPRACHBRIDGE FEHLER: {exc}"
            print(f"[bridge-http] error request_id={request_id}: {exc}", flush=True)
            try:
                publish_action_messages(
                    self.server.mqtt_client,
                    [(self.server.pair.display_topic, build_display_payload("BRIDGE FEHLER", 5000, request_id))],
                    self.server.pair,
                )
            except Exception:
                pass
            self.send_json(500, {"ok": False, "request_id": request_id, "error": error_text})


def serve_audio(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = load_config(config_path, Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        if message.topic != pair.status_topic:
            return
        try:
            status = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if isinstance(status, dict):
            if message_is_retained(message):
                return
            note_stackchan_status(pair, status)

    client.on_message = on_message
    connect_and_start(client, config.mqtt)
    client.subscribe(pair.status_topic, qos=0)
    server = SpeechHttpServer((args.host, args.port), SpeechRequestHandler)
    server.config = config
    server.config_path = config_path
    server.pair = pair
    server.mqtt_client = client
    print(f"[bridge-http] listening on http://{args.host}:{args.port}", flush=True)
    print(f"[bridge-http] endpoint: POST /stackchan/audio (audio/wav)", flush=True)
    print(f"[bridge-http] endpoint: POST /stackchan/notify (application/json)", flush=True)
    print(f"[bridge-http] endpoint: POST /stackchan/display-image (application/json)", flush=True)
    print(f"[bridge-http] endpoint: POST /stackchan/search-image (application/json)", flush=True)
    print(f"[bridge-http] endpoint: POST /stackchan/photo (image/*)", flush=True)
    print(f"[bridge-http] Hermes API: {hermes_chat_url(config.hermes.base_url)}", flush=True)
    print(f"[bridge-http] dispatch Hermes actions to {pair.mqtt_prefix}/cmd/*", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        client.loop_stop()
        client.disconnect()


def add_reminder_cli(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    action: dict[str, Any] = {"action": "reminder", "text": args.text}
    if args.delay_s is not None:
        action["delay_s"] = args.delay_s
    if args.due_at:
        action["due_at"] = args.due_at
    reminder = add_reminder(config, build_reminder(action, pair, args.request_id or f"cli-{uuid.uuid4().hex[:10]}"))
    print(json.dumps(reminder, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def list_reminders_cli(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    reminders = pending_reminders(config, args.pair)
    if args.json:
        print(json.dumps(reminders, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not reminders:
        print("[bridge] no pending reminders")
        return 0
    for reminder in reminders:
        due_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(reminder.get("due_ts", 0))))
        print(f"{reminder.get('id')} {due_text} {reminder.get('pair_id')}: {reminder.get('text')}")
    return 0


def fire_reminder(client: Any, pair: PairConfig, config: BridgeConfig, reminder: dict[str, Any]) -> None:
    if not stackchan_is_online(pair):
        STACKCHAN_PRESENCE.note_skip(pair, f"reminder {reminder.get('id')}")
        return
    duration_s = config.reminders.display_duration_ms / 1000.0
    pause_life_animation(pair.pair_id, max(12.0, duration_s + 8.0), f"reminder {reminder.get('id')}")
    messages = [
        action_to_topic_payload(pair, action, f"reminder-{reminder.get('id', uuid.uuid4().hex[:8])}-{index:02d}")
        for index, action in enumerate(reminder_actions_with_tts(config, reminder))
    ]
    print(
        f"[{time.strftime('%H:%M:%S')}] [bridge] firing reminder {reminder.get('id')}: {reminder.get('text')}",
        flush=True,
    )
    publish_action_messages(client, messages, pair)


def watch_reminders(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    client = create_mqtt_client(config.mqtt)
    poll_s = max(0.2, float(args.poll_s if args.poll_s is not None else config.reminders.poll_interval_s))

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        if message.topic != pair.status_topic:
            return
        try:
            status = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if isinstance(status, dict):
            if message_is_retained(message):
                return
            note_stackchan_status(pair, status)

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        client.subscribe(pair.status_topic, qos=0)
        print(
            f"[{time.strftime('%H:%M:%S')}] [bridge] watching reminders "
            f"store={reminder_store_path(config)} pair={pair.pair_id} poll={poll_s:.1f}s",
            flush=True,
        )
        while True:
            for reminder in due_reminders(config, pair):
                fire_reminder(client, pair, config, reminder)
                if args.once:
                    return 0
            if args.once:
                return 0
            time.sleep(poll_s)
    finally:
        client.loop_stop()
        client.disconnect()


def bridge_worker(
    name: str,
    target: Any,
    worker_args: argparse.Namespace,
    stop_event: Event,
    restart_delay_s: float,
) -> None:
    while not stop_event.is_set():
        try:
            print(f"[{time.strftime('%H:%M:%S')}] [bridge-run] starting {name}", flush=True)
            exit_code = target(worker_args)
            print(f"[{time.strftime('%H:%M:%S')}] [bridge-run] {name} exited with code {exit_code}", flush=True)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)) and stop_event.is_set():
                return
            print(f"[{time.strftime('%H:%M:%S')}] [bridge-run] {name} crashed: {exc}", file=sys.stderr, flush=True)
            traceback.print_exc()
        if stop_event.wait(restart_delay_s):
            return


def run_bridge(args: argparse.Namespace) -> int:
    stop_event = Event()
    restart_delay_s = max(0.5, float(args.restart_delay_s))
    workers: list[tuple[str, Any, argparse.Namespace]] = []

    if not args.no_audio:
        workers.append((
            "audio-http",
            serve_audio,
            argparse.Namespace(config=args.config, env=args.env, pair=args.pair, host=args.host, port=args.port),
        ))
    if not args.no_touch_lamp:
        workers.append((
            "touch-lamp",
            watch_touch_lamp,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                verbose=args.touch_verbose,
                off_delay_ms=args.touch_off_delay_ms,
            ),
        ))
    if not args.no_touch_emotions:
        workers.append((
            "touch-emotions",
            watch_touch_emotions,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                verbose=args.touch_emotion_verbose,
                life_pause_s=args.touch_emotion_life_pause_s,
                once=False,
            ),
        ))
    if not args.no_power:
        workers.append((
            "power-watcher",
            watch_power,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                debounce_s=args.power_debounce_s,
                announce_initial=args.power_announce_initial,
                once=False,
                no_restore_face=args.power_no_restore_face,
            ),
        ))
    if not args.no_sensors:
        workers.append((
            "sensor-watcher",
            watch_sensors,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                verbose=args.sensor_verbose,
                life_pause_s=args.sensor_life_pause_s,
                once=False,
            ),
        ))
    if not args.no_reminders:
        workers.append((
            "reminders",
            watch_reminders,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                poll_s=args.reminder_poll_s,
                once=False,
            ),
        ))
    if not args.no_settings:
        workers.append((
            "device-settings",
            watch_device_settings,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                timeout=args.settings_timeout,
                display_wake=args.settings_display_wake,
                reboot_drop_ms=args.settings_reboot_drop_ms,
                once=False,
            ),
        ))
    if not args.no_idle_sleep:
        workers.append((
            "idle-sleep",
            watch_idle_sleep,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                timeout_s=args.idle_sleep_timeout_s,
                poll_s=args.idle_sleep_poll_s,
                retry_s=args.idle_sleep_retry_s,
                once=False,
            ),
        ))
    if not args.no_life:
        workers.append((
            "life-animator",
            animate_life,
            argparse.Namespace(
                config=args.config,
                env=args.env,
                pair=args.pair,
                min_interval_s=args.life_min_interval_s,
                max_interval_s=args.life_max_interval_s,
                status_timeout=args.life_status_timeout,
                seed=args.life_seed,
                once=False,
                no_motion=args.life_no_motion,
                small_motion_gap_s=args.life_small_motion_gap_s,
                big_motion_gap_s=args.life_big_motion_gap_s,
            ),
        ))

    if not workers:
        raise ConfigError("run needs at least one worker enabled")

    def stop(_signum: int | None = None, _frame: Any | None = None) -> None:
        stop_event.set()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    threads = [
        Thread(
            target=bridge_worker,
            name=f"h2s-{name}",
            args=(name, target, worker_args, stop_event, restart_delay_s),
            daemon=True,
        )
        for name, target, worker_args in workers
    ]
    print(
        f"[{time.strftime('%H:%M:%S')}] [bridge-run] starting unified bridge "
        f"pair={args.pair} workers={','.join(name for name, _, _ in workers)}",
        flush=True,
    )
    for thread in threads:
        thread.start()

    try:
        while not stop_event.wait(1.0):
            pass
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
        print(f"[{time.strftime('%H:%M:%S')}] [bridge-run] stopping unified bridge", flush=True)
    return 0


def get_pair(config: BridgeConfig, pair_id: str) -> PairConfig:
    try:
        return config.pairs[pair_id]
    except KeyError as exc:
        known = ", ".join(sorted(config.pairs))
        raise ConfigError(f"unknown pair {pair_id!r}; known pairs: {known}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes2StackChan MQTT bridge smoke-test CLI.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to pairs.json.")
    parser.add_argument("--env", default=str(DEFAULT_ENV), help="Path to local .env file.")

    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_common_send_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--pair", default="desk", help="Pair id to address.")
        command_parser.add_argument("--request-id", default=None, help="Request id. Defaults to a random id.")
        command_parser.add_argument("--wait-ack", action="store_true", help="Wait for ACK or error.")
        command_parser.add_argument("--timeout", type=float, default=5.0, help="ACK wait timeout in seconds.")

    display = subcommands.add_parser("send-display", help="Send a text display command.")
    add_common_send_options(display)
    display.add_argument("--text", default="Hello from Hermes2StackChan", help="Text to show.")
    display.add_argument("--duration-ms", type=int, default=5000, help="Display duration hint.")
    display.set_defaults(func=send_display)

    face = subcommands.add_parser("send-face", help="Set the StackChan face.")
    add_common_send_options(face)
    face.add_argument(
        "--emotion",
        default="neutral",
        help="neutral, friendly, happy, super_happy, thinking, mischievous, panic, help, speaking, error, etc.",
    )
    face.add_argument("--intensity-pct", type=int, default=60, help="Expression intensity 0..100.")
    face.set_defaults(func=send_face)

    move = subcommands.add_parser("send-move", help="Move the head inside firmware soft limits.")
    add_common_send_options(move)
    move.add_argument("--direction", choices=["left", "right", "up", "down", "center", "straight"], default=None)
    move.add_argument("--yaw-delta", type=int, default=None, help="Raw yaw delta step, clamped by firmware.")
    move.add_argument("--pitch-delta", type=int, default=None, help="Raw pitch delta step, clamped by firmware.")
    move.add_argument("--yaw-target-pct", type=int, default=None, help="Target yaw percent -100..100.")
    move.add_argument("--pitch-target-pct", type=int, default=None, help="Original-style target pitch percent 0..100.")
    move.set_defaults(func=send_move)

    motion = subcommands.add_parser("send-motion", help="Send a computed smooth motion path to StackChan.")
    add_common_send_options(motion)
    motion.add_argument("--profile", default="circle", choices=["circle", "nod", "yes", "shake", "no", "look_around", "look-around"])
    motion.add_argument("--duration-ms", type=int, default=None, help="Optional total duration for generated profiles.")
    motion.add_argument("--speed-pct", type=int, default=45, help="Motion speed 1..100 when point durations are omitted.")
    motion.add_argument("--loops", type=int, default=1, help="Circle loops.")
    motion.add_argument("--yaw-radius-pct", type=int, default=32, help="Circle yaw radius percent.")
    motion.add_argument("--pitch-radius-pct", type=int, default=26, help="Circle pitch radius percent.")
    motion.add_argument("--steps", type=int, default=32, help="Circle interpolation waypoints.")
    motion.add_argument("--curve", default="auto", choices=["auto", "linear", "spline"], help="Path interpolation curve.")
    motion.add_argument("--points", default=None, help="JSON array of [yaw_pct,pitch_pct,duration_ms,speed_pct,hold_ms] or objects.")
    motion.add_argument("--segment-ms", type=int, default=None, help="Default path segment duration when --points omit duration.")
    motion.set_defaults(func=send_motion)

    led = subcommands.add_parser("send-led", help="Set LED/neon mode.")
    add_common_send_options(led)
    led.add_argument("--mode", default="solid", help="off, solid, rainbow, scanner, blink, breathe, sparkle, party.")
    led.add_argument("--r", type=int, default=None, help="Red 0..255.")
    led.add_argument("--g", type=int, default=None, help="Green 0..255.")
    led.add_argument("--b", type=int, default=None, help="Blue 0..255.")
    led.set_defaults(func=send_led)

    device = subcommands.add_parser("send-device", help="Set volume, brightness, and display power.")
    add_common_send_options(device)
    device.add_argument("--volume-pct", type=int, default=None, help="Speaker volume 0..100.")
    device.add_argument("--brightness-pct", type=int, default=None, help="Display brightness 0..100.")
    device.add_argument("--display-sleep", action="store_true", help="Turn display off and backlight to zero.")
    device.add_argument("--display-wake", action="store_true", help="Wake display and restore configured brightness.")
    device.set_defaults(func=send_device)

    restore_device = subcommands.add_parser("restore-device-settings", help="Restore retained volume and brightness to StackChan.")
    add_common_send_options(restore_device)
    restore_device.add_argument("--ack-timeout", type=float, default=5.0, help="ACK wait timeout in seconds.")
    restore_device.add_argument("--display-wake", action="store_true", help="Also wake the display while restoring settings.")
    restore_device.set_defaults(func=restore_device_settings)

    sound = subcommands.add_parser("send-sound", help="Play a simple speaker tone.")
    add_common_send_options(sound)
    sound.add_argument("--frequency-hz", type=int, default=880, help="Tone frequency.")
    sound.add_argument("--duration-ms", type=int, default=140, help="Tone duration.")
    sound.add_argument(
        "--pattern",
        choices=["tone", "good", "success", "ok", "error", "fail", "question", "ask", "followup", "camera", "photo", "shutter", "alarm", "notify", "message"],
        default=None,
        help="Named safe tone pattern.",
    )
    sound.add_argument("--volume-pct", type=int, default=None, help="Optional volume update before tone.")
    sound.set_defaults(func=send_sound)

    audio = subcommands.add_parser("send-audio", help="Control wakeword and push-to-talk recording state.")
    add_common_send_options(audio)
    audio.add_argument(
        "--action",
        required=True,
        choices=["start_recording", "stop_recording", "set_wakeword", "simulate_wakeword"],
        help="Audio-control action to send.",
    )
    audio.add_argument("--source", default=None, help="Recording source, for example wakeword, push_to_talk, or manual.")
    audio.add_argument("--wakeword", default=None, help="Wakeword label, for example Computer.")
    audio.add_argument("--reason", default=None, help="Stop reason, for example touch_release or silence_timeout.")
    audio.add_argument("--min-ms", type=int, default=None, help="Minimum recording time hint.")
    audio.add_argument("--silence-timeout-ms", type=int, default=None, help="Wakeword silence timeout hint.")
    audio.add_argument("--max-ms", type=int, default=None, help="Maximum recording time hint.")
    audio.add_argument("--enabled", action="store_true", help="Enable wakeword listening for set_wakeword.")
    audio.add_argument("--disabled", action="store_true", help="Disable wakeword listening for set_wakeword.")
    audio.set_defaults(func=send_audio)

    say = subcommands.add_parser("send-say", help="Display a short text with optional beep.")
    add_common_send_options(say)
    say.add_argument("--text", required=True, help="Text to display.")
    say.add_argument("--emotion", default="speaking", help="Face state stored with the say event.")
    say.add_argument("--no-beep", action="store_true", help="Do not play the small acknowledgement beep.")
    say.set_defaults(func=send_say)

    system = subcommands.add_parser("send-system", help="Send a system action.")
    add_common_send_options(system)
    system.add_argument(
        "--action",
        required=True,
        choices=["ping", "status", "reboot", "display_sleep", "display_wake", "shutdown", "power_off"],
    )
    system.set_defaults(func=send_system)

    raw = subcommands.add_parser("send-raw", help="Send a raw JSON payload to a cmd/* topic inside the pair namespace.")
    add_common_send_options(raw)
    raw.add_argument("--topic-suffix", required=True, help="Topic suffix, for example cmd/device.")
    raw.add_argument("--json", required=True, help="JSON object payload.")
    raw.set_defaults(func=send_raw)

    status_parser = subcommands.add_parser("read-status", help="Read the retained StackChan status once.")
    status_parser.add_argument("--pair", default="desk", help="Pair id to read.")
    status_parser.add_argument("--timeout", type=float, default=2.0, help="Status wait timeout in seconds.")
    status_parser.set_defaults(func=read_status)

    status_health_parser = subcommands.add_parser("status-health", help="Validate the retained StackChan status shape.")
    status_health_parser.add_argument("--pair", default="desk", help="Pair id to read.")
    status_health_parser.add_argument("--timeout", type=float, default=2.0, help="Status wait timeout in seconds.")
    status_health_parser.add_argument("--show-status", action="store_true", help="Print the retained status after validation.")
    status_health_parser.set_defaults(func=status_health)

    hermes_health_parser = subcommands.add_parser("hermes-health", help="Check the configured Hermes HTTP health endpoint.")
    hermes_health_parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds.")
    hermes_health_parser.set_defaults(func=hermes_health)

    ask = subcommands.add_parser("ask-hermes", help="Ask Hermes over HTTP and dispatch returned actions over MQTT.")
    ask.add_argument("--pair", default="desk", help="Pair id to address.")
    ask.add_argument("--text", required=True, help="User text to send to Hermes.")
    ask.add_argument("--timeout", type=float, default=8.0, help="MQTT ACK wait timeout in seconds.")
    ask.add_argument("--status-timeout", type=float, default=1.5, help="Retained status wait timeout in seconds.")
    ask.add_argument("--no-status", action="store_true", help="Do not include retained StackChan status in the Hermes prompt.")
    ask.add_argument("--no-wait-ack", action="store_true", help="Do not wait for StackChan ACK/Error messages.")
    ask.add_argument("--dry-run", action="store_true", help="Print Hermes actions and MQTT payloads without publishing.")
    ask.add_argument("--show-response", action="store_true", help="Print parsed Hermes response before dispatch.")
    ask.set_defaults(func=ask_hermes)

    watch_parser = subcommands.add_parser("watch", help="Print all MQTT messages for a pair.")
    watch_parser.add_argument("--pair", default="desk", help="Pair id to watch.")
    watch_parser.set_defaults(func=watch)

    touch_lamp = subcommands.add_parser("watch-touch-lamp", help="Turn LEDs green while StackChan head touch is held.")
    touch_lamp.add_argument("--pair", default="desk", help="Pair id to watch.")
    touch_lamp.add_argument("--verbose", action="store_true", help="Log per-event touch-to-publish timing.")
    touch_lamp.add_argument("--off-delay-ms", type=int, default=500, help="Delay before LEDs turn off after recording stops.")
    touch_lamp.set_defaults(func=watch_touch_lamp)

    touch_emotions = subcommands.add_parser("watch-touch-emotions", help="React to left/right head touch with face and small head emotions.")
    touch_emotions.add_argument("--pair", default="desk", help="Pair id to watch.")
    touch_emotions.add_argument("--verbose", action="store_true", help="Log generated side-touch emotion actions.")
    touch_emotions.add_argument("--life-pause-s", type=float, default=3.0, help="Pause idle life animation after side-touch reactions.")
    touch_emotions.add_argument("--once", action="store_true", help="Exit after the first side-touch reaction.")
    touch_emotions.set_defaults(func=watch_touch_emotions)

    sensors = subcommands.add_parser("watch-sensors", help="React to IMU movement and LTR553 proximity with filtered wake/face/head actions.")
    sensors.add_argument("--pair", default="desk", help="Pair id to watch.")
    sensors.add_argument("--verbose", action="store_true", help="Log sensor events and generated actions.")
    sensors.add_argument("--life-pause-s", type=float, default=5.0, help="Pause idle life animation after sensor reactions.")
    sensors.add_argument("--once", action="store_true", help="Exit after the first emitted sensor reaction.")
    sensors.set_defaults(func=watch_sensors)

    serve_audio_parser = subcommands.add_parser("serve-audio", help="Run HTTP audio endpoint and mirror STT text to StackChan display.")
    serve_audio_parser.add_argument("--pair", default="desk", help="Pair id to serve.")
    serve_audio_parser.add_argument("--host", default=os.environ.get("H2S_BRIDGE_HTTP_HOST", "0.0.0.0"), help="HTTP listen host.")
    serve_audio_parser.add_argument("--port", type=int, default=int(os.environ.get("H2S_BRIDGE_HTTP_PORT", "8788")), help="HTTP listen port.")
    serve_audio_parser.set_defaults(func=serve_audio)

    power = subcommands.add_parser("watch-power", help="React to StackChan battery charge/discharge status changes.")
    power.add_argument("--pair", default="desk", help="Pair id to watch.")
    power.add_argument("--debounce-s", type=float, default=1.0, help="Minimum seconds between power reactions.")
    power.add_argument("--announce-initial", action="store_true", help="Also show the current power state immediately.")
    power.add_argument("--once", action="store_true", help="Exit after the first emitted reaction.")
    power.add_argument("--no-restore-face", action="store_true", help="Do not run the delayed face/motion reaction after the short battery overlay.")
    power.set_defaults(func=watch_power)

    add_reminder_parser = subcommands.add_parser("add-reminder", help="Persist a reminder that StackChan will fire later.")
    add_reminder_parser.add_argument("--pair", default="desk", help="Pair id to notify.")
    add_reminder_parser.add_argument("--text", required=True, help="Reminder text.")
    add_reminder_parser.add_argument("--delay-s", type=int, default=None, help="Delay in seconds.")
    add_reminder_parser.add_argument("--due-at", default=None, help="ISO timestamp or epoch seconds.")
    add_reminder_parser.add_argument("--request-id", default=None, help="Optional request id.")
    add_reminder_parser.set_defaults(func=add_reminder_cli)

    list_reminders_parser = subcommands.add_parser("list-reminders", help="List pending reminders.")
    list_reminders_parser.add_argument("--pair", default=None, help="Optional pair id filter.")
    list_reminders_parser.add_argument("--json", action="store_true", help="Print JSON.")
    list_reminders_parser.set_defaults(func=list_reminders_cli)

    reminders = subcommands.add_parser("watch-reminders", help="Fire due persistent reminders.")
    reminders.add_argument("--pair", default="desk", help="Pair id to notify.")
    reminders.add_argument("--poll-s", type=float, default=None, help="Poll interval in seconds.")
    reminders.add_argument("--once", action="store_true", help="Check once and exit.")
    reminders.set_defaults(func=watch_reminders)

    settings = subcommands.add_parser("watch-device-settings", help="Keep retained device settings and restore them on boot/reconnect.")
    settings.add_argument("--pair", default="desk", help="Pair id to watch.")
    settings.add_argument("--timeout", type=float, default=1.5, help="Retained settings read timeout in seconds.")
    settings.add_argument("--display-wake", action="store_true", help="Also wake the display when restoring settings.")
    settings.add_argument("--reboot-drop-ms", type=int, default=10_000, help="Treat uptime drops larger than this as reboot.")
    settings.add_argument("--once", action="store_true", help="Process retained status briefly and exit.")
    settings.set_defaults(func=watch_device_settings)

    idle_sleep = subcommands.add_parser("watch-idle-sleep", help="Turn the display off after a quiet idle timeout.")
    idle_sleep.add_argument("--pair", default="desk", help="Pair id to watch.")
    idle_sleep.add_argument("--timeout-s", type=float, default=DEFAULT_IDLE_SLEEP_TIMEOUT_S, help="Seconds without human/action before display sleep.")
    idle_sleep.add_argument("--poll-s", type=float, default=1.0, help="Idle check interval in seconds.")
    idle_sleep.add_argument("--retry-s", type=float, default=30.0, help="Retry sleep command after this many seconds if status does not change.")
    idle_sleep.add_argument("--once", action="store_true", help="Exit after the first emitted sleep command.")
    idle_sleep.set_defaults(func=watch_idle_sleep)

    life = subcommands.add_parser("animate-life", help="Send small idle face and motion impulses so StackChan feels alive.")
    life.add_argument("--pair", default="desk", help="Pair id to animate.")
    life.add_argument("--min-interval-s", type=float, default=DEFAULT_LIFE_MIN_INTERVAL_S, help="Minimum seconds between idle impulses.")
    life.add_argument("--max-interval-s", type=float, default=DEFAULT_LIFE_MAX_INTERVAL_S, help="Maximum seconds between idle impulses.")
    life.add_argument("--status-timeout", type=float, default=1.5, help="Retained status wait timeout in seconds.")
    life.add_argument("--seed", type=int, default=None, help="Optional random seed for repeatable tests.")
    life.add_argument("--once", action="store_true", help="Emit one life sequence and exit.")
    life.add_argument("--no-motion", action="store_true", help="Only animate the face, without servo head motion.")
    life.add_argument("--small-motion-gap-s", type=float, default=DEFAULT_LIFE_SMALL_MOTION_GAP_S, help="Minimum seconds between small idle head motions.")
    life.add_argument("--big-motion-gap-s", type=float, default=DEFAULT_LIFE_BIG_MOTION_GAP_S, help="Minimum seconds between large idle head motions.")
    life.set_defaults(func=animate_life)

    run = subcommands.add_parser("run", help="Run the full bridge as one multithreaded process.")
    run.add_argument("--pair", default="desk", help="Pair id to serve.")
    run.add_argument("--host", default=os.environ.get("H2S_BRIDGE_HTTP_HOST", "0.0.0.0"), help="HTTP listen host.")
    run.add_argument("--port", type=int, default=int(os.environ.get("H2S_BRIDGE_HTTP_PORT", "8788")), help="HTTP listen port.")
    run.add_argument("--restart-delay-s", type=float, default=3.0, help="Delay before restarting a crashed worker thread.")
    run.add_argument("--no-audio", action="store_true", help="Disable the HTTP audio/STT/TTS worker.")
    run.add_argument("--no-touch-lamp", action="store_true", help="Disable the fast touch/recording LED worker.")
    run.add_argument("--no-touch-emotions", action="store_true", help="Disable left/right head-touch emotion reactions.")
    run.add_argument("--no-power", action="store_true", help="Disable the power-state reaction worker.")
    run.add_argument("--no-sensors", action="store_true", help="Disable IMU/LTR553 sensor reactions.")
    run.add_argument("--no-reminders", action="store_true", help="Disable persistent reminder worker.")
    run.add_argument("--no-settings", action="store_true", help="Disable retained device settings restore worker.")
    run.add_argument("--no-idle-sleep", action="store_true", help="Disable automatic display sleep after quiet idle timeout.")
    run.add_argument("--no-life", action="store_true", help="Disable the idle life-animation worker.")
    run.add_argument("--touch-verbose", action="store_true", help="Log per-event touch-to-publish timing.")
    run.add_argument("--touch-off-delay-ms", type=int, default=500, help="Delay before LEDs turn off after recording stops.")
    run.add_argument("--touch-emotion-verbose", action="store_true", help="Log generated side-touch emotion actions.")
    run.add_argument("--touch-emotion-life-pause-s", type=float, default=3.0, help="Pause idle life animation after side-touch reactions.")
    run.add_argument("--power-debounce-s", type=float, default=1.0, help="Minimum seconds between power reactions.")
    run.add_argument("--power-announce-initial", action="store_true", help="Also show the current power state immediately.")
    run.add_argument("--power-no-restore-face", action="store_true", help="Do not run delayed face/motion reaction after battery overlay.")
    run.add_argument("--sensor-verbose", action="store_true", help="Log IMU/LTR553 sensor reactions.")
    run.add_argument("--sensor-life-pause-s", type=float, default=5.0, help="Pause idle life animation after sensor reactions.")
    run.add_argument("--reminder-poll-s", type=float, default=None, help="Reminder worker poll interval in seconds.")
    run.add_argument("--settings-timeout", type=float, default=1.5, help="Retained settings read timeout in seconds.")
    run.add_argument("--settings-display-wake", action="store_true", help="Also wake display when restoring retained settings.")
    run.add_argument("--settings-reboot-drop-ms", type=int, default=10_000, help="Treat uptime drops larger than this as reboot.")
    run.add_argument("--idle-sleep-timeout-s", type=float, default=DEFAULT_IDLE_SLEEP_TIMEOUT_S, help="Seconds without human/action before display sleep.")
    run.add_argument("--idle-sleep-poll-s", type=float, default=1.0, help="Idle sleep check interval.")
    run.add_argument("--idle-sleep-retry-s", type=float, default=30.0, help="Retry sleep command if status does not switch to sleeping.")
    run.add_argument("--life-min-interval-s", type=float, default=DEFAULT_LIFE_MIN_INTERVAL_S, help="Minimum seconds between idle impulses.")
    run.add_argument("--life-max-interval-s", type=float, default=DEFAULT_LIFE_MAX_INTERVAL_S, help="Maximum seconds between idle impulses.")
    run.add_argument("--life-status-timeout", type=float, default=1.5, help="Retained status wait timeout in seconds.")
    run.add_argument("--life-seed", type=int, default=None, help="Optional random seed for repeatable tests.")
    run.add_argument("--life-no-motion", action="store_true", help="Only animate the face, without servo head motion.")
    run.add_argument("--life-small-motion-gap-s", type=float, default=DEFAULT_LIFE_SMALL_MOTION_GAP_S, help="Minimum seconds between small idle head motions.")
    run.add_argument("--life-big-motion-gap-s", type=float, default=DEFAULT_LIFE_BIG_MOTION_GAP_S, help="Minimum seconds between large idle head motions.")
    run.set_defaults(func=run_bridge)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, TimeoutError, OSError) as exc:
        print(f"[bridge] error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
