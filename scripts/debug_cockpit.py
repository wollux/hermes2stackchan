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
import os
import queue
import signal
import subprocess
import sys
import threading
import time
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stream bridge logs, MQTT, and serial monitor together.")
    parser.add_argument("--pair", default=os.environ.get("H2S_PAIR_ID", "desk"), help="Pair id to watch.")
    parser.add_argument("--serial-port", default=find_serial_port(), help="ESP serial port, e.g. /dev/cu.usbmodem21301.")
    parser.add_argument("--idf-export", default=str(DEFAULT_IDF_EXPORT), help="Path to ESP-IDF export.sh.")
    parser.add_argument("--no-serial", action="store_true", help="Do not start ESP-IDF serial monitor.")
    parser.add_argument("--no-mqtt", action="store_true", help="Do not start bridge MQTT watch.")
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
    print(f"pair={args.pair} serial={args.serial_port or 'none'}", flush=True)
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
