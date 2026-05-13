#!/usr/bin/env python3
"""Combined Hermes2StackChan debug cockpit.

Streams the important bridge logs, the pair MQTT stream, and the ESP-IDF
serial monitor into one timestamped terminal. The script intentionally uses
subprocesses for MQTT and serial so it follows the exact tools we already use
manually during hardware debugging.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_DIR = ROOT / "firmware"
DEFAULT_IDF_EXPORT = Path.home() / "development" / "esp-idf-v5.5.4" / "export.sh"
DEFAULT_LOGS = [
    ROOT / "power-watcher.log",
    ROOT / "life-animator.log",
    Path("/tmp/hermes2stackchan-touch-lamp.log"),
    ROOT / "touch-lamp.log",
]


def now() -> str:
    return time.strftime("%H:%M:%S")


def emit(out: "queue.Queue[tuple[str, str]]", source: str, line: str) -> None:
    out.put((source, line.rstrip("\n\r")))


def follow_file(path: Path, source: str, out: "queue.Queue[tuple[str, str]]", stop: threading.Event) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        emit(out, source, f"tailing {path}")
        while not stop.is_set():
            line = handle.readline()
            if line:
                emit(out, source, line)
                continue
            time.sleep(0.05)


def stream_process(
    command: list[str],
    source: str,
    out: "queue.Queue[tuple[str, str]]",
    stop: threading.Event,
    cwd: Path,
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )

    def reader() -> None:
        emit(out, source, "started: " + " ".join(command))
        assert process.stdout is not None
        for line in process.stdout:
            emit(out, source, line)
            if stop.is_set():
                break
        code = process.poll()
        emit(out, source, f"stopped exit={code}")

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return process


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def find_serial_port() -> str | None:
    env_port = os.environ.get("H2S_SERIAL_PORT")
    if env_port:
        return env_port
    candidates: list[str] = []
    for pattern in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        candidates.extend(glob.glob(pattern))
    return sorted(candidates)[0] if candidates else None


def existing_logs(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def summarize_health(payload: dict[str, object]) -> str:
    stackchan = payload.get("stackchan") if isinstance(payload.get("stackchan"), dict) else {}
    watchdog = payload.get("watchdog") if isinstance(payload.get("watchdog"), dict) else {}
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    replay = payload.get("replay_buffer") if isinstance(payload.get("replay_buffer"), dict) else {}
    hermes = health.get("hermes") if isinstance(health.get("hermes"), dict) else {}
    stt = health.get("stt") if isinstance(health.get("stt"), dict) else {}
    tts = health.get("tts") if isinstance(health.get("tts"), dict) else {}

    online = bool(stackchan.get("online"))
    age = stackchan.get("last_seen_age_s")
    age_text = "never" if age is None else f"{float(age):.1f}s"
    stale = bool(watchdog.get("stale"))
    last_skip = watchdog.get("last_skip_reason") or "-"
    last_replay = replay.get("last_debug_request_id") or "-"
    replay_error = replay.get("last_debug_error") or "-"
    return (
        f"stackchan={'online' if online else 'offline'} age={age_text} stale={stale} "
        f"skip={last_skip} hermes={bool(hermes.get('ok'))} "
        f"stt={bool(stt.get('configured'))} tts={bool(tts.get('configured'))} "
        f"replay={last_replay} replay_error={replay_error}"
    )


def poll_health(
    bridge_url: str,
    interval_s: float,
    source: str,
    out: "queue.Queue[tuple[str, str]]",
    stop: threading.Event,
) -> None:
    url = bridge_url.rstrip("/") + "/healthz?status=1"
    emit(out, source, f"polling {url} every {interval_s:.1f}s")
    while not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=3.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                emit(out, source, summarize_health(payload))
            else:
                emit(out, source, "invalid health payload")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            emit(out, source, f"health failed: {exc}")
        stop.wait(max(1.0, interval_s))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stream bridge logs, MQTT, and serial monitor together.")
    parser.add_argument("--pair", default=os.environ.get("H2S_PAIR_ID", "desk"), help="Pair id to watch.")
    parser.add_argument("--bridge-url", default=os.environ.get("H2S_BRIDGE_URL", "http://127.0.0.1:8788"), help="Bridge HTTP base URL for health polling.")
    parser.add_argument("--serial-port", default=find_serial_port(), help="ESP serial port, e.g. /dev/cu.usbmodem21301.")
    parser.add_argument("--idf-export", default=str(DEFAULT_IDF_EXPORT), help="Path to ESP-IDF export.sh.")
    parser.add_argument("--no-health", action="store_true", help="Do not poll /healthz.")
    parser.add_argument("--health-interval-s", type=float, default=5.0, help="Seconds between /healthz summaries.")
    parser.add_argument("--no-serial", action="store_true", help="Do not start ESP-IDF serial monitor.")
    parser.add_argument("--no-mqtt", action="store_true", help="Do not start bridge MQTT watch.")
    parser.add_argument("--remote-host", default=os.environ.get("H2S_REMOTE_HOST", ""), help="Optional bridge host for remote journalctl tail.")
    parser.add_argument("--remote-user", default=os.environ.get("H2S_REMOTE_USER", "wollux"), help="SSH user for --remote-host.")
    parser.add_argument("--remote-service", default=os.environ.get("H2S_REMOTE_SERVICE", "hermes2stackchan.service"), help="Systemd user service to tail remotely.")
    parser.add_argument("--no-remote-log", action="store_true", help="Do not tail remote journal even if --remote-host is set.")
    parser.add_argument(
        "--log",
        action="append",
        default=[],
        help="Extra log file to tail. Can be passed multiple times.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out: "queue.Queue[tuple[str, str]]" = queue.Queue()
    stop = threading.Event()
    processes: list[subprocess.Popen[str]] = []

    log_paths = existing_logs([*DEFAULT_LOGS, *(Path(item) for item in args.log)])
    for path in log_paths:
        source = f"log:{path.name}"
        threading.Thread(target=follow_file, args=(path, source, out, stop), daemon=True).start()

    if not args.no_health:
        threading.Thread(
            target=poll_health,
            args=(args.bridge_url, max(1.0, args.health_interval_s), "healthz", out, stop),
            daemon=True,
        ).start()

    python = os.environ.get("PYTHON", "/opt/homebrew/bin/python3.11" if Path("/opt/homebrew/bin/python3.11").exists() else sys.executable)
    if not args.no_mqtt:
        processes.append(
            stream_process(
                [python, "-u", "-m", "bridge.hermes2stackchan_bridge", "--env", ".env", "watch", "--pair", args.pair],
                "mqtt",
                out,
                stop,
                ROOT,
            )
        )

    if args.remote_host and not args.no_remote_log:
        remote = f"{args.remote_user}@{args.remote_host}" if args.remote_user else args.remote_host
        remote_cmd = f"journalctl --user -u {args.remote_service} -f -n 80 --no-pager"
        processes.append(
            stream_process(
                ["ssh", remote, remote_cmd],
                "bridge-remote",
                out,
                stop,
                ROOT,
            )
        )

    if not args.no_serial:
        if not args.serial_port:
            emit(out, "serial", "no serial port found; pass --serial-port /dev/cu.usbmodemXXXX")
        elif not Path(args.idf_export).exists():
            emit(out, "serial", f"ESP-IDF export script not found: {args.idf_export}")
        else:
            command = [
                "bash",
                "-lc",
                f"source {args.idf_export!r} >/dev/null && idf.py -p {args.serial_port!r} monitor",
            ]
            processes.append(stream_process(command, "serial", out, stop, FIRMWARE_DIR))

    print("Hermes2StackChan debug cockpit running. Stop with Ctrl-C.", flush=True)
    print(
        f"pair={args.pair} bridge={args.bridge_url} serial={args.serial_port or 'none'} "
        f"remote={args.remote_host or 'none'}",
        flush=True,
    )
    try:
        while True:
            try:
                source, line = out.get(timeout=0.5)
            except queue.Empty:
                continue
            print(f"{now()} [{source:<28}] {line}", flush=True)
    except KeyboardInterrupt:
        print("\nStopping debug cockpit...", flush=True)
    finally:
        stop.set()
        for process in processes:
            stop_process(process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
