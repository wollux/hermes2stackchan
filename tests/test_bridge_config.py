from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.hermes2stackchan_bridge import (
    ConfigError,
    build_display_payload,
    build_motion_profile_points,
    load_config,
    normalize_motion_points,
    parse_env_file,
)


class BridgeConfigTests(unittest.TestCase):
    def test_load_example_config(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})

        self.assertEqual(config.mqtt.host, "localhost")
        self.assertIn("desk", config.pairs)
        self.assertEqual(config.pairs["desk"].display_topic, "hermes-stackchan/desk/cmd/display")
        self.assertEqual(config.pairs["desk"].move_topic, "hermes-stackchan/desk/cmd/move")
        self.assertEqual(config.pairs["desk"].motion_topic, "hermes-stackchan/desk/cmd/motion")
        self.assertEqual(config.pairs["desk"].device_topic, "hermes-stackchan/desk/cmd/device")

    def test_rejects_wrong_namespace(self) -> None:
        raw = {
            "mqtt": {"host": "localhost"},
            "pairs": [
                {
                    "pair_id": "desk",
                    "hermes_id": "hermes-desk",
                    "stackchan_id": "stackchan-desk",
                    "mqtt_prefix": "wrong/desk",
                }
            ],
        }

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(raw, handle)
            path = Path(handle.name)

        try:
            with self.assertRaises(ConfigError):
                load_config(path, env_path=None, environ={})
        finally:
            path.unlink(missing_ok=True)

    def test_env_overrides_mqtt_and_pair(self) -> None:
        config = load_config(
            Path("config/pairs.example.json"),
            env_path=None,
            environ={
                "H2S_MQTT_HOST": "192.0.2.10",
                "H2S_MQTT_PORT": "1884",
                "H2S_PAIR_ID": "bench",
                "H2S_HERMES_ID": "hermes-bench",
                "H2S_STACKCHAN_ID": "stackchan-bench",
            },
        )

        self.assertEqual(config.mqtt.host, "192.0.2.10")
        self.assertEqual(config.mqtt.port, 1884)
        self.assertIn("bench", config.pairs)
        self.assertEqual(config.pairs["bench"].display_topic, "hermes-stackchan/bench/cmd/display")

    def test_parse_env_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write("# comment\nexport H2S_MQTT_HOST=\"mqtt.local\"\nH2S_PAIR_ID='desk'\n")
            path = Path(handle.name)

        try:
            parsed = parse_env_file(path)
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(parsed["H2S_MQTT_HOST"], "mqtt.local")
        self.assertEqual(parsed["H2S_PAIR_ID"], "desk")

    def test_display_payload(self) -> None:
        payload = build_display_payload("Hallo StackChan", 5000, "test-001")

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["mode"], "text")
        self.assertEqual(payload["text"], "Hallo StackChan")
        self.assertEqual(payload["request_id"], "test-001")

    def test_motion_points_are_clamped_and_normalized(self) -> None:
        points = normalize_motion_points(
            [[120, -130, 25, 200, 5000], {"yaw": -10.4, "pitch": 20.6}],
            default_speed_pct=50,
            default_duration_ms=None,
        )

        self.assertEqual(
            points,
            [
                {"yaw_pct": 100, "pitch_pct": -100, "speed_pct": 100, "duration_ms": 40, "hold_ms": 4000},
                {"yaw_pct": -10, "pitch_pct": 21, "speed_pct": 50},
            ],
        )

    def test_generated_circle_is_points_only(self) -> None:
        args = SimpleNamespace(
            profile="circle",
            speed_pct=35,
            duration_ms=None,
            steps=12,
            loops=1,
            yaw_radius_pct=30,
            pitch_radius_pct=20,
        )

        points = build_motion_profile_points(args)

        self.assertGreaterEqual(len(points), 14)
        self.assertEqual(points[0]["yaw_pct"], 30)
        self.assertEqual(points[0]["pitch_pct"], 0)
        self.assertGreaterEqual(points[0]["duration_ms"], 300)
        self.assertEqual(points[-1]["yaw_pct"], 0)
        self.assertEqual(points[-1]["pitch_pct"], 0)
        self.assertGreaterEqual(points[-1]["duration_ms"], 300)
        self.assertTrue(all("profile" not in point for point in points))


if __name__ == "__main__":
    unittest.main()
