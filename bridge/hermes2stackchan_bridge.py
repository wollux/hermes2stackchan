from __future__ import annotations

import argparse
import json
import math
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Timer
from typing import Any


SCHEMA_VERSION = "1.0"
POWER_DISPLAY_DURATION_MS = 5000
DEFAULT_CONFIG = Path("config/pairs.json")
EXAMPLE_CONFIG = Path("config/pairs.example.json")
DEFAULT_ENV = Path(".env")


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
    def ack_topic(self) -> str:
        return f"{self.mqtt_prefix}/ack"

    @property
    def error_topic(self) -> str:
        return f"{self.mqtt_prefix}/error"


@dataclass(frozen=True)
class HermesConfig:
    base_url: str = "http://127.0.0.1:8642"
    api_key: str | None = None
    model: str = "default"
    timeout_s: float = 30.0


@dataclass(frozen=True)
class BridgeConfig:
    mqtt: MqttConfig
    pairs: dict[str, PairConfig]
    hermes: HermesConfig = field(default_factory=HermesConfig)


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

    return BridgeConfig(mqtt=mqtt, pairs=pairs, hermes=hermes)


def load_env(env_path: Path | None, environ: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    if env_path and env_path.exists():
        env.update(parse_env_file(env_path))

    source = os.environ if environ is None else environ
    for key, value in source.items():
        if key.startswith("H2S_") or key == "API_SERVER_KEY":
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
    text = text.strip()
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


def read_latest_status(config: BridgeConfig, pair: PairConfig, timeout_s: float = 2.0) -> dict[str, Any] | None:
    client = create_mqtt_client(config.mqtt)
    status_seen = Event()
    status: dict[str, Any] = {}

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        status.clear()
        status.update(data)
        status_seen.set()

    client.on_message = on_message
    try:
        connect_and_start(client, config.mqtt)
        client.subscribe(pair.status_topic, qos=0)
        if status_seen.wait(timeout_s):
            return status
        return None
    finally:
        client.loop_stop()
        client.disconnect()


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
    "head.pan_pct",
    "head.tilt_pct",
    "head.ready",
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


def build_hermes_messages(
    pair: PairConfig,
    capabilities: str,
    personality: str,
    status: dict[str, Any] | None,
    user_text: str,
) -> list[dict[str, str]]:
    status_text = json.dumps(status or {}, ensure_ascii=False, sort_keys=True)
    system_parts = [
        f"You are {pair.hermes_id}. You control exactly one StackChan: {pair.stackchan_id}.",
        f"Your MQTT namespace is {pair.mqtt_prefix}. Never address another StackChan.",
        "Return JSON only. Do not wrap it in Markdown.",
        "Schema: {\"reply\":\"short German text\",\"actions\":[{\"action\":\"say|display|face|move|motion|led|device|sound|system\",...}]}",
        "Use action say for the spoken/displayed answer. Keep answers concise unless the user asks for detail.",
        "For status questions, use the current status JSON and answer directly; do not invent sensor values.",
        f"Current StackChan status JSON: {status_text}",
    ]
    if capabilities:
        system_parts.append(f"Bridge capabilities:\n{capabilities}")
    if personality:
        system_parts.append(f"Personality notes:\n{personality}")
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": user_text},
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


def action_to_topic_payload(pair: PairConfig, action: dict[str, Any], request_id: str | None = None) -> tuple[str, dict[str, Any]]:
    if not isinstance(action, dict):
        raise ConfigError("Hermes action must be an object")
    raw_name = action.get("action") or action.get("type") or action.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ConfigError("Hermes action needs an action name")
    name = raw_name.strip().lower().replace("-", "_")
    action_request_id = optional_string(action.get("request_id")) or request_id

    if name == "display":
        text = optional_string(action.get("text"))
        if not text:
            raise ConfigError("display action needs text")
        payload = build_display_payload(text, parse_int_value(action.get("duration_ms"), 5000, "display.duration_ms"), action_request_id)
        return pair.display_topic, payload

    if name == "say":
        text = optional_string(action.get("text"))
        if not text:
            raise ConfigError("say action needs text")
        payload = with_request_id(
            {
                "text": text,
                "emotion": optional_string(action.get("emotion")) or "speaking",
                "beep": parse_bool_value(action.get("beep"), True),
            },
            action_request_id,
        )
        return pair.say_topic, payload

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
        if action.get("volume_pct") is not None:
            payload["volume_pct"] = action["volume_pct"]
        return pair.sound_topic, with_request_id(payload, action_request_id)

    if name in {"system", "ping", "status", "reboot", "display_sleep", "display_wake"}:
        system_action = optional_string(action.get("system_action") or action.get("command"))
        if name != "system":
            system_action = name
        if not system_action:
            raise ConfigError("system action needs system_action or command")
        if system_action not in {"ping", "status", "reboot", "display_sleep", "display_wake"}:
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
                    {"yaw_pct": 0, "pitch_pct": 0, "duration_ms": 180, "speed_pct": 35},
                    {"yaw_pct": 0, "pitch_pct": 26, "duration_ms": 650, "speed_pct": 35},
                    {"yaw_pct": 0, "pitch_pct": 14, "duration_ms": 420, "speed_pct": 30},
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
                    {"yaw_pct": 0, "pitch_pct": 0, "duration_ms": 120, "speed_pct": 38},
                    {"yaw_pct": -14, "pitch_pct": -8, "duration_ms": 280, "speed_pct": 45},
                    {"yaw_pct": 14, "pitch_pct": -12, "duration_ms": 280, "speed_pct": 45},
                    {"yaw_pct": -8, "pitch_pct": -16, "duration_ms": 260, "speed_pct": 42},
                    {"yaw_pct": 0, "pitch_pct": -24, "duration_ms": 600, "speed_pct": 34},
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
    ui_mode = nested_status_value(status, "ui.mode")
    if isinstance(ui_mode, str) and ui_mode != "face":
        return False
    emotion = nested_status_value(status, "face.emotion")
    if emotion in {"sleep", "error", "battery", "charging", "battery_low", "speaking"}:
        return False
    return True


def current_face_action(status: dict[str, Any] | None, default_intensity: int = 60) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {"action": "face", "emotion": "neutral", "intensity_pct": default_intensity}
    emotion = optional_string(nested_status_value(status, "face.emotion")) or "neutral"
    if emotion in {
        "blink",
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
    }:
        emotion = "neutral"
    intensity = nested_status_value(status, "face.intensity_pct")
    if not isinstance(intensity, int):
        intensity = default_intensity
    return {"action": "face", "emotion": emotion, "intensity_pct": clamp_int(intensity, 35, 90)}


def build_subtle_life_motion(rng: random.Random) -> tuple[str, dict[str, Any]]:
    glance = rng.choice(["glance_left", "glance_right", "glance_up", "glance_up", "glance_down"])
    yaw = -5 if glance == "glance_left" else 5 if glance == "glance_right" else rng.choice([-2, 2])
    pitch = 4 if glance == "glance_up" else -4 if glance == "glance_down" else rng.choice([-2, 2])
    return glance, {
        "action": "motion",
        "curve": "spline",
        "speed_pct": 12,
        "points": [
            {"yaw_pct": yaw, "pitch_pct": pitch, "duration_ms": 1400, "speed_pct": 12, "hold_ms": 350},
            {"yaw_pct": 0, "pitch_pct": 0, "duration_ms": 1800, "speed_pct": 10},
        ],
    }


def build_big_life_sequence(rng: random.Random, base_intensity: int, mood: str) -> list[tuple[int, dict[str, Any]]]:
    left_first = rng.choice([True, False])
    first = -75 if left_first else 75
    second = 75 if left_first else -75
    first_glance = "glance_left" if left_first else "glance_right"
    second_glance = "glance_right" if left_first else "glance_left"
    center_glance = first_glance
    return [
        (0, {"action": "face", "emotion": first_glance, "intensity_pct": base_intensity}),
        (
            160,
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 34,
                "points": [{"yaw_pct": first, "pitch_pct": 4, "duration_ms": 900, "speed_pct": 34, "hold_ms": 300}],
            },
        ),
        (650, {"action": "face", "emotion": first_glance, "intensity_pct": base_intensity}),
        (600, {"action": "face", "emotion": second_glance, "intensity_pct": base_intensity}),
        (
            0,
            {
                "action": "motion",
                "curve": "spline",
                "speed_pct": 32,
                "points": [{"yaw_pct": second, "pitch_pct": 6, "duration_ms": 1600, "speed_pct": 32, "hold_ms": 260}],
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
                "points": [{"yaw_pct": 0, "pitch_pct": 0, "duration_ms": 1200, "speed_pct": 22}],
            },
        ),
        (1500, {"action": "face", "emotion": mood, "intensity_pct": base_intensity}),
    ]


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
    choice = rng.random()

    if choice < 0.34:
        return [(0, {"action": "face", "emotion": "blink", "intensity_pct": base_intensity})]

    if choice < 0.50:
        return [
            (0, {"action": "face", "emotion": "blink", "intensity_pct": base_intensity}),
            (360, {"action": "face", "emotion": "blink", "intensity_pct": base_intensity}),
        ]

    if choice < 0.66:
        glance = rng.choice(["glance_left", "glance_right", "glance_up", "glance_up", "glance_down"])
        return [(0, {"action": "face", "emotion": glance, "intensity_pct": base_intensity})]

    if choice < 0.80:
        mouth = rng.choice(["mouth_smile", "mouth_tiny", "mouth_wiggle"])
        return [(0, {"action": "face", "emotion": mouth, "intensity_pct": base_intensity})]

    if choice < 0.86:
        return [(0, {"action": "face", "emotion": "deep_breathe", "intensity_pct": base_intensity})]

    if choice < 0.89:
        return [(0, {"action": "face", "emotion": "micro_sleep", "intensity_pct": base_intensity})]

    if include_motion and choice >= 0.97:
        return build_big_life_sequence(rng, base_intensity, mood)

    if include_motion and choice >= 0.89:
        glance, motion = build_subtle_life_motion(rng)
        return [
            (0, {"action": "face", "emotion": glance, "intensity_pct": base_intensity}),
            (760, motion),
            (2200, {"action": "face", "emotion": mood, "intensity_pct": base_intensity}),
        ]

    return [(0, {"action": "face", "emotion": "breathe", "intensity_pct": base_intensity})]


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
    if args.show_response or args.dry_run:
        print(json.dumps({"hermes": response, "actions": actions}, ensure_ascii=False, indent=2))
    action_messages = [
        action_to_topic_payload(pair, action, f"hermes-{uuid.uuid4().hex[:12]}")
        for action in actions
    ]
    if args.dry_run:
        print(json.dumps(
            [{"topic": topic, "payload": payload} for topic, payload in action_messages],
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    return dispatch_mqtt_actions(config, pair, action_messages, not args.no_wait_ack, args.timeout)


def publish_action_messages(client: Any, action_messages: list[tuple[str, dict[str, Any]]]) -> None:
    for topic, payload in action_messages:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        result = client.publish(topic, body, qos=1, retain=False)
        result.wait_for_publish(timeout=5)
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] sent {topic}: {body}", flush=True)


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
        publish_action_messages(client, action_messages)
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

    try:
        connect_and_start(client, config.mqtt)
        print(f"[{time.strftime('%H:%M:%S')}] [bridge] life animation active for {pair.pair_id}", flush=True)
        while True:
            status = read_latest_status(config, pair, args.status_timeout)
            sequence = build_life_sequence(status, rng, include_motion=not args.no_motion)
            if sequence:
                for delay_ms, action in sequence:
                    if delay_ms > 0:
                        time.sleep(delay_ms / 1000.0)
                    publish_action_messages(
                        client,
                        [action_to_topic_payload(pair, action, f"life-{uuid.uuid4().hex[:10]}")],
                    )
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
        payload["yaw_target_pct"] = args.yaw_target_pct
    if args.pitch_target_pct is not None:
        payload["pitch_target_pct"] = args.pitch_target_pct
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
            "yaw_pct": clamp_int(round(yaw), -100, 100),
            "pitch_pct": clamp_int(round(pitch), -100, 100),
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
            "yaw_pct": clamp_int(yaw, -100, 100),
            "pitch_pct": clamp_int(pitch, -100, 100),
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
        points: list[dict[str, int]] = [
            point(args.yaw_radius_pct, 0, duration_ms=approach_duration),
        ]
        for i in range(1, total_steps + 1):
            angle = 2.0 * math.pi * loops * i / total_steps
            points.append(
                point(
                    round(math.cos(angle) * args.yaw_radius_pct),
                    round(math.sin(angle) * args.pitch_radius_pct),
                    duration_ms=arc_segment_duration,
                )
            )
        points.append(point(0, 0, duration_ms=approach_duration))
        return points

    if profile in {"nod", "yes"}:
        segment_duration = max(40, total_duration_ms // 5) if total_duration_ms is not None else None
        return [
            point(0, 0, duration_ms=segment_duration),
            point(0, 30, duration_ms=segment_duration),
            point(0, -22, duration_ms=segment_duration),
            point(0, 28, duration_ms=segment_duration),
            point(0, 0, duration_ms=segment_duration),
        ]

    if profile in {"shake", "no"}:
        segment_duration = max(40, total_duration_ms // 6) if total_duration_ms is not None else None
        return [
            point(0, 0, duration_ms=segment_duration),
            point(-32, 0, duration_ms=segment_duration),
            point(32, 0, duration_ms=segment_duration),
            point(-26, 0, duration_ms=segment_duration),
            point(26, 0, duration_ms=segment_duration),
            point(0, 0, duration_ms=segment_duration),
        ]

    if profile in {"look_around", "look-around"}:
        segment_duration = max(40, total_duration_ms // 6) if total_duration_ms is not None else None
        return [
            point(0, 0, duration_ms=segment_duration),
            point(-38, 8, 120, duration_ms=segment_duration),
            point(-18, -22, 80, duration_ms=segment_duration),
            point(36, 12, 120, duration_ms=segment_duration),
            point(16, -18, 80, duration_ms=segment_duration),
            point(0, 0, duration_ms=segment_duration),
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


def send_sound(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload: dict[str, Any] = {
        "frequency_hz": args.frequency_hz,
        "duration_ms": args.duration_ms,
    }
    if args.volume_pct is not None:
        payload["volume_pct"] = args.volume_pct
    return send_payload(args, pair.sound_topic, with_request_id(payload, args.request_id))


def send_say(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.env))
    pair = get_pair(config, args.pair)
    payload = with_request_id(
        {
            "text": args.text,
            "emotion": args.emotion,
            "beep": not args.no_beep,
        },
        args.request_id,
    )
    return send_payload(args, pair.say_topic, payload)


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
    face.add_argument("--emotion", default="neutral", help="neutral, happy, sad, angry, surprised, question, wink, sleep, speaking, error.")
    face.add_argument("--intensity-pct", type=int, default=60, help="Expression intensity 0..100.")
    face.set_defaults(func=send_face)

    move = subcommands.add_parser("send-move", help="Move the head inside firmware soft limits.")
    add_common_send_options(move)
    move.add_argument("--direction", choices=["left", "right", "up", "down", "center", "straight"], default=None)
    move.add_argument("--yaw-delta", type=int, default=None, help="Raw yaw delta step, clamped by firmware.")
    move.add_argument("--pitch-delta", type=int, default=None, help="Raw pitch delta step, clamped by firmware.")
    move.add_argument("--yaw-target-pct", type=int, default=None, help="Target yaw percent -100..100.")
    move.add_argument("--pitch-target-pct", type=int, default=None, help="Target pitch percent -100..100.")
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

    sound = subcommands.add_parser("send-sound", help="Play a simple speaker tone.")
    add_common_send_options(sound)
    sound.add_argument("--frequency-hz", type=int, default=880, help="Tone frequency.")
    sound.add_argument("--duration-ms", type=int, default=140, help="Tone duration.")
    sound.add_argument("--volume-pct", type=int, default=None, help="Optional volume update before tone.")
    sound.set_defaults(func=send_sound)

    say = subcommands.add_parser("send-say", help="Display a short text with optional beep.")
    add_common_send_options(say)
    say.add_argument("--text", required=True, help="Text to display.")
    say.add_argument("--emotion", default="speaking", help="Face state stored with the say event.")
    say.add_argument("--no-beep", action="store_true", help="Do not play the small acknowledgement beep.")
    say.set_defaults(func=send_say)

    system = subcommands.add_parser("send-system", help="Send a system action.")
    add_common_send_options(system)
    system.add_argument("--action", required=True, choices=["ping", "status", "reboot", "display_sleep", "display_wake"])
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

    power = subcommands.add_parser("watch-power", help="React to StackChan battery charge/discharge status changes.")
    power.add_argument("--pair", default="desk", help="Pair id to watch.")
    power.add_argument("--debounce-s", type=float, default=1.0, help="Minimum seconds between power reactions.")
    power.add_argument("--announce-initial", action="store_true", help="Also show the current power state immediately.")
    power.add_argument("--once", action="store_true", help="Exit after the first emitted reaction.")
    power.add_argument("--no-restore-face", action="store_true", help="Do not run the delayed face/motion reaction after the short battery overlay.")
    power.set_defaults(func=watch_power)

    life = subcommands.add_parser("animate-life", help="Send small idle face and motion impulses so StackChan feels alive.")
    life.add_argument("--pair", default="desk", help="Pair id to animate.")
    life.add_argument("--min-interval-s", type=float, default=4.0, help="Minimum seconds between idle impulses.")
    life.add_argument("--max-interval-s", type=float, default=11.0, help="Maximum seconds between idle impulses.")
    life.add_argument("--status-timeout", type=float, default=1.5, help="Retained status wait timeout in seconds.")
    life.add_argument("--seed", type=int, default=None, help="Optional random seed for repeatable tests.")
    life.add_argument("--once", action="store_true", help="Emit one life sequence and exit.")
    life.add_argument("--no-motion", action="store_true", help="Only animate the face, without servo head motion.")
    life.set_defaults(func=animate_life)
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
