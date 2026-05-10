from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.hermes2stackchan_bridge import ConfigError, parse_env_file


DEFAULT_ENV = Path(".env")
DEFAULT_SDKCONFIG = Path("firmware/sdkconfig")

STRING_KEYS = {
    "H2S_WIFI_SSID": "CONFIG_STACKCHAN_WIFI_SSID",
    "H2S_WIFI_PASSWORD": "CONFIG_STACKCHAN_WIFI_PASSWORD",
    "H2S_MQTT_URI": "CONFIG_STACKCHAN_MQTT_URI",
    "H2S_PAIR_ID": "CONFIG_STACKCHAN_PAIR_ID",
    "H2S_STACKCHAN_ID": "CONFIG_STACKCHAN_STACKCHAN_ID",
}

INT_KEYS = {
    "H2S_DISPLAY_BRIGHTNESS": "CONFIG_STACKCHAN_DISPLAY_BRIGHTNESS",
    "H2S_STATUS_INTERVAL_MS": "CONFIG_STACKCHAN_STATUS_INTERVAL_MS",
}


def quote_sdkconfig_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_updates(env: dict[str, str]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for env_key, sdk_key in STRING_KEYS.items():
        if env_key in env:
            updates[sdk_key] = quote_sdkconfig_string(env[env_key])

    for env_key, sdk_key in INT_KEYS.items():
        if env_key in env:
            try:
                updates[sdk_key] = str(int(env[env_key]))
            except ValueError as exc:
                raise ConfigError(f"{env_key} must be an integer") from exc
    return updates


def merge_sdkconfig(existing: str, updates: dict[str, str]) -> str:
    lines = existing.splitlines()
    seen: set[str] = set()
    output: list[str] = []

    pattern = re.compile(r"^(# )?(CONFIG_STACKCHAN_[A-Z0-9_]+)(=.*| is not set)$")
    for line in lines:
        match = pattern.match(line)
        if match and match.group(2) in updates:
            key = match.group(2)
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)

    if updates:
        if output and output[-1] != "":
            output.append("")
        output.append("# Hermes2StackChan local values generated from .env")
        for key in sorted(updates):
            if key not in seen:
                output.append(f"{key}={updates[key]}")

    return "\n".join(output) + "\n"


def apply_env(env_path: Path, sdkconfig_path: Path) -> list[str]:
    if not env_path.exists():
        raise ConfigError(f"{env_path} not found. Create it from .env.example first.")

    updates = build_updates(parse_env_file(env_path))
    if not updates:
        raise ConfigError(f"{env_path} contains no firmware keys")

    existing = sdkconfig_path.read_text(encoding="utf-8") if sdkconfig_path.exists() else ""
    sdkconfig_path.parent.mkdir(parents=True, exist_ok=True)
    sdkconfig_path.write_text(merge_sdkconfig(existing, updates), encoding="utf-8")
    return sorted(updates)


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy firmware-related .env values into ignored firmware/sdkconfig.")
    parser.add_argument("--env", default=str(DEFAULT_ENV), help="Path to local .env file.")
    parser.add_argument("--sdkconfig", default=str(DEFAULT_SDKCONFIG), help="Path to firmware sdkconfig.")
    args = parser.parse_args()

    try:
        keys = apply_env(Path(args.env), Path(args.sdkconfig))
    except ConfigError as exc:
        print(f"[firmware-env] error: {exc}")
        return 1

    print("[firmware-env] synced keys:")
    for key in keys:
        print(f"  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
