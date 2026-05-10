from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any


SCHEMA_VERSION = "1.0"
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
class BridgeConfig:
    mqtt: MqttConfig
    pairs: dict[str, PairConfig]


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

    return BridgeConfig(mqtt=mqtt, pairs=pairs)


def load_env(env_path: Path | None, environ: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    if env_path and env_path.exists():
        env.update(parse_env_file(env_path))

    source = os.environ if environ is None else environ
    for key, value in source.items():
        if key.startswith("H2S_"):
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

    watch_parser = subcommands.add_parser("watch", help="Print all MQTT messages for a pair.")
    watch_parser.add_argument("--pair", default="desk", help="Pair id to watch.")
    watch_parser.set_defaults(func=watch)
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
