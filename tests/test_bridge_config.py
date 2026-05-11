from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.hermes2stackchan_bridge import (
    ConfigError,
    action_to_topic_payload,
    battery_snapshot,
    build_display_payload,
    build_life_sequence,
    build_multipart_form_data,
    build_motion_profile_points,
    build_power_change_actions,
    build_power_followup_actions,
    build_touch_lamp_payload,
    DEFAULT_IDLE_PITCH_PCT,
    DEFAULT_IDLE_YAW_PCT,
    face_snapshot,
    LIFE_VARIANT_NAMES,
    load_config,
    missing_status_paths,
    normalize_motion_points,
    parse_hermes_action_response,
    parse_env_file,
    should_listen_for_followup,
    status_allows_life_animation,
)


class BridgeConfigTests(unittest.TestCase):
    def test_load_example_config(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})

        self.assertEqual(config.mqtt.host, "localhost")
        self.assertEqual(config.hermes.base_url, "http://127.0.0.1:8642")
        self.assertIn("desk", config.pairs)
        self.assertEqual(config.pairs["desk"].display_topic, "hermes-stackchan/desk/cmd/display")
        self.assertEqual(config.pairs["desk"].move_topic, "hermes-stackchan/desk/cmd/move")
        self.assertEqual(config.pairs["desk"].motion_topic, "hermes-stackchan/desk/cmd/motion")
        self.assertEqual(config.pairs["desk"].device_topic, "hermes-stackchan/desk/cmd/device")
        self.assertEqual(config.pairs["desk"].audio_topic, "hermes-stackchan/desk/cmd/audio")
        self.assertEqual(config.pairs["desk"].events_topic, "hermes-stackchan/desk/events")

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
        self.assertEqual(config.hermes.base_url, "http://127.0.0.1:8642")
        self.assertIn("bench", config.pairs)
        self.assertEqual(config.pairs["bench"].display_topic, "hermes-stackchan/bench/cmd/display")

    def test_env_overrides_hermes(self) -> None:
        config = load_config(
            Path("config/pairs.example.json"),
            env_path=None,
            environ={
                "H2S_HERMES_BASE_URL": "http://192.0.2.20:8642/v1",
                "H2S_HERMES_MODEL": "local-hermes",
                "H2S_HERMES_API_KEY": "secret",
                "H2S_HERMES_TIMEOUT_S": "12.5",
            },
        )

        self.assertEqual(config.hermes.base_url, "http://192.0.2.20:8642/v1")
        self.assertEqual(config.hermes.model, "local-hermes")
        self.assertEqual(config.hermes.api_key, "secret")
        self.assertEqual(config.hermes.timeout_s, 12.5)

    def test_env_overrides_speech(self) -> None:
        config = load_config(
            Path("config/pairs.example.json"),
            env_path=None,
            environ={
                "H2S_GROQ_API_KEY": "groq-secret",
                "H2S_STT_MODEL": "whisper-large-v3",
                "H2S_STT_LANGUAGE": "de",
                "H2S_TRANSCRIPT_DISPLAY_MS": "7000",
            },
        )

        self.assertEqual(config.speech.groq_api_key, "groq-secret")
        self.assertEqual(config.speech.groq_model, "whisper-large-v3")
        self.assertEqual(config.speech.language, "de")
        self.assertEqual(config.speech.display_duration_ms, 7000)

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

    def test_multipart_form_data_contains_audio_file(self) -> None:
        body, boundary = build_multipart_form_data(
            {"model": "whisper-large-v3-turbo", "language": "de"},
            "file",
            "stackchan.wav",
            "audio/wav",
            b"RIFF....WAVE",
        )

        self.assertIn(boundary.encode("utf-8"), body)
        self.assertIn(b'name="model"', body)
        self.assertIn(b'filename="stackchan.wav"', body)
        self.assertIn(b"Content-Type: audio/wav", body)
        self.assertIn(b"RIFF....WAVE", body)

    def test_motion_points_are_clamped_and_normalized(self) -> None:
        points = normalize_motion_points(
            [[120, -130, 25, 200, 5000], {"yaw": -10.4, "pitch": 20.6}],
            default_speed_pct=50,
            default_duration_ms=None,
        )

        self.assertEqual(
            points,
            [
                {"yaw_pct": 100, "pitch_pct": 0, "speed_pct": 100, "duration_ms": 40, "hold_ms": 4000},
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
        self.assertEqual(points[0]["pitch_pct"], DEFAULT_IDLE_PITCH_PCT)
        self.assertGreaterEqual(points[0]["duration_ms"], 300)
        self.assertEqual(points[-1]["yaw_pct"], 0)
        self.assertEqual(points[-1]["pitch_pct"], DEFAULT_IDLE_PITCH_PCT)
        self.assertGreaterEqual(points[-1]["duration_ms"], 300)
        self.assertTrue(all("profile" not in point for point in points))

    def test_parse_hermes_action_response_accepts_fenced_json(self) -> None:
        parsed = parse_hermes_action_response(
            "```json\n{\"reply\":\"Hallo\",\"actions\":[{\"action\":\"face\",\"emotion\":\"happy\"}]}\n```"
        )

        self.assertEqual(parsed["reply"], "Hallo")
        self.assertEqual(parsed["actions"][0]["action"], "face")

    def test_plain_hermes_text_falls_back_to_say(self) -> None:
        parsed = parse_hermes_action_response("Hallo Wolfgang.")

        self.assertEqual(parsed["actions"][0]["action"], "say")
        self.assertEqual(parsed["actions"][0]["text"], "Hallo Wolfgang.")

    def test_followup_listen_detects_explicit_flag(self) -> None:
        self.assertTrue(should_listen_for_followup({"follow_up_listen": True, "actions": []}, "Alles klar."))

    def test_followup_listen_detects_question_reply(self) -> None:
        self.assertTrue(should_listen_for_followup({"actions": []}, "Moechtest du noch etwas wissen?"))

    def test_followup_listen_ignores_statement(self) -> None:
        self.assertFalse(should_listen_for_followup({"actions": []}, "Mache ich."))

    def test_hermes_action_to_topic_payload(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        pair = config.pairs["desk"]

        topic, payload = action_to_topic_payload(
            pair,
            {"action": "move", "yaw_target_pct": 25, "pitch_target_pct": -10},
            "req-001",
        )

        self.assertEqual(topic, "hermes-stackchan/desk/cmd/move")
        self.assertEqual(payload["request_id"], "req-001")
        self.assertEqual(payload["yaw_target_pct"], 25)
        self.assertEqual(payload["pitch_target_pct"], 0)

    def test_battery_snapshot_accepts_status_aliases(self) -> None:
        snapshot = battery_snapshot(
            {"battery_pct": 82, "charging": True, "battery_discharging": False, "battery_known": True}
        )

        self.assertEqual(snapshot["pct"], 82)
        self.assertTrue(snapshot["charging"])
        self.assertFalse(snapshot["discharging"])
        self.assertTrue(snapshot["external_power"])
        self.assertTrue(snapshot["known"])

    def test_power_change_actions_for_charging_transition(self) -> None:
        actions = build_power_change_actions(
            {"known": True, "pct": 75, "charging": False, "discharging": True, "external_power": False},
            {"known": True, "pct": 76, "charging": True, "discharging": False, "external_power": True},
        )

        self.assertEqual(actions[0]["action"], "display")
        self.assertEqual(actions[0]["text"], "AKKU 76% LAEDT")
        self.assertGreaterEqual(actions[0]["duration_ms"], 4000)
        self.assertNotIn("face", {action["action"] for action in actions})
        self.assertNotIn("led", {action["action"] for action in actions})
        self.assertNotIn("sound", {action["action"] for action in actions})

    def test_power_change_actions_for_full_external_power(self) -> None:
        actions = build_power_change_actions(
            {"known": True, "pct": 99, "charging": False, "discharging": True, "external_power": False},
            {"known": True, "pct": 100, "charging": False, "discharging": False, "external_power": True},
        )

        self.assertEqual(actions[0]["text"], "AKKU 100% AM STROM")

    def test_power_change_actions_for_unplug_low_battery(self) -> None:
        actions = build_power_change_actions(
            {"known": True, "pct": 21, "charging": True, "discharging": False, "external_power": True},
            {"known": True, "pct": 18, "charging": False, "discharging": True, "external_power": False},
        )

        self.assertEqual(actions[0]["action"], "display")
        self.assertEqual(actions[0]["text"], "AKKU 18% ENTLAEDT")
        self.assertNotIn("face", {action["action"] for action in actions})
        self.assertNotIn("led", {action["action"] for action in actions})
        self.assertNotIn("sound", {action["action"] for action in actions})

    def test_power_followup_for_plugged_in_is_happy_motion_only(self) -> None:
        actions = build_power_followup_actions(
            {"known": True, "pct": 75, "charging": False, "discharging": True, "external_power": False},
            {"known": True, "pct": 76, "charging": True, "discharging": False, "external_power": True},
        )

        action_names = {action["action"] for action in actions}
        self.assertEqual(actions[0]["emotion"], "happy")
        self.assertIn("motion", action_names)
        self.assertNotIn("led", action_names)
        self.assertNotIn("sound", action_names)

    def test_power_followup_for_unplugged_is_neutral_down_motion_only(self) -> None:
        actions = build_power_followup_actions(
            {"known": True, "pct": 21, "charging": True, "discharging": False, "external_power": True},
            {"known": True, "pct": 18, "charging": False, "discharging": True, "external_power": False},
        )

        action_names = {action["action"] for action in actions}
        motion = next(action for action in actions if action["action"] == "motion")
        self.assertEqual(actions[0]["emotion"], "neutral")
        self.assertLess(motion["points"][-1]["pitch_pct"], DEFAULT_IDLE_PITCH_PCT)
        self.assertNotIn("led", action_names)
        self.assertNotIn("sound", action_names)

    def test_face_snapshot_restores_current_face(self) -> None:
        restore = face_snapshot({"face": {"emotion": "happy", "intensity_pct": 73}})

        self.assertEqual(restore, {"action": "face", "emotion": "happy", "intensity_pct": 73})

    def test_face_snapshot_does_not_restore_old_battery_face(self) -> None:
        restore = face_snapshot({"face": {"emotion": "battery", "intensity_pct": 75}})

        self.assertEqual(restore, {"action": "face", "emotion": "neutral", "intensity_pct": 75})

    def test_status_health_required_paths(self) -> None:
        status = {
            "schema_version": "1.0",
            "pair_id": "desk",
            "stackchan_id": "stackchan-desk",
            "uptime_ms": 1234,
            "firmware": "1.0.0-mqtt-hardware",
            "firmware_version": "1.0.0-mqtt-hardware",
            "battery_pct": 100,
            "battery_known": True,
            "battery_charging": False,
            "battery_discharging": False,
            "usb_power": True,
            "external_power": True,
            "volume_pct": 80,
            "brightness_pct": 75,
            "display_sleeping": False,
            "wakeword_enabled": True,
            "recording": False,
            "speaking": False,
            "head": {"pan_pct": 0, "tilt_pct": 0, "ready": True},
            "face": {"emotion": "neutral", "intensity_pct": 60},
            "ui": {"mode": "face"},
            "led": {"mode": "off", "mode_id": 0, "r": 0, "g": 0, "b": 0, "ready": True},
            "speaker": {"ready": True, "volume_pct": 80},
            "temperature": {"soc_c": 40, "servo_yaw_c": -1, "servo_pitch_c": -1},
            "audio": {
                "input_ready": False,
                "wakeword_enabled": True,
                "wakeword": "Computer",
                "recording": False,
                "recording_source": "none",
                "recording_started_ms": 0,
                "recording_min_ms": 5000,
                "recording_silence_timeout_ms": 1000,
                "recording_max_ms": 15000,
            },
        }

        self.assertEqual(missing_status_paths(status), [])
        del status["firmware"]
        self.assertEqual(missing_status_paths(status), ["firmware"])

    def test_audio_action_to_topic_payload_start_recording(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        topic, payload = action_to_topic_payload(
            pair,
            {
                "action": "start_recording",
                "source": "push_to_talk",
                "min_ms": 5000,
                "silence_timeout_ms": 1000,
                "max_ms": 20000,
            },
            "audio-001",
        )

        self.assertEqual(topic, "hermes-stackchan/desk/cmd/audio")
        self.assertEqual(payload["action"], "start_recording")
        self.assertEqual(payload["source"], "push_to_talk")
        self.assertEqual(payload["request_id"], "audio-001")
        self.assertEqual(payload["min_ms"], 5000)

    def test_audio_action_to_topic_payload_set_wakeword(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        topic, payload = action_to_topic_payload(
            pair,
            {"action": "audio", "audio_action": "set_wakeword", "wakeword": "Computer", "enabled": True},
            "wake-001",
        )

        self.assertEqual(topic, "hermes-stackchan/desk/cmd/audio")
        self.assertEqual(payload["action"], "set_wakeword")
        self.assertEqual(payload["wakeword"], "Computer")
        self.assertTrue(payload["enabled"])

    def test_touch_lamp_payload_tracks_touch_and_recording_state(self) -> None:
        down = build_touch_lamp_payload({"event": "touch_down"}, "touch-1")
        started = build_touch_lamp_payload({"event": "recording_started"}, "touch-2")
        stopped = build_touch_lamp_payload({"event": "recording_stopped"}, "touch-3")
        ignored = build_touch_lamp_payload({"event": "touch_up"}, "touch-4")

        self.assertEqual(down, {"mode": "solid", "r": 0, "g": 255, "b": 0, "schema_version": "1.0", "request_id": "touch-1"})
        self.assertEqual(started, {"mode": "solid", "r": 0, "g": 255, "b": 0, "schema_version": "1.0", "request_id": "touch-2"})
        self.assertEqual(stopped, {"mode": "off", "r": 0, "g": 0, "b": 0, "schema_version": "1.0", "request_id": "touch-3"})
        self.assertIsNone(ignored)

    def test_life_animation_only_runs_on_idle_face(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        self.assertTrue(status_allows_life_animation(status))
        status["ui"]["mode"] = "display"
        self.assertFalse(status_allows_life_animation(status))

    def test_life_sequence_contains_no_led_or_sound(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        for seed in range(20):
            sequence = build_life_sequence(status, random.Random(seed))
            action_names = {action["action"] for _delay, action in sequence}
            self.assertTrue(action_names)
            self.assertNotIn("led", action_names)
            self.assertNotIn("sound", action_names)

    def test_life_variant_pool_has_100_named_variants(self) -> None:
        self.assertEqual(len(LIFE_VARIANT_NAMES), 100)
        self.assertEqual(len(set(LIFE_VARIANT_NAMES)), 100)
        for name in [
            "double_blink",
            "wink_left",
            "wink_right",
            "surprise_pop",
            "happy_squint",
            "look_behind",
            "desk_spin",
            "reset_grin",
        ]:
            self.assertIn(name, LIFE_VARIANT_NAMES)

    def test_life_sequence_reaches_at_least_90_variants_over_500_seeds(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        seen = {
            action["variant"]
            for seed in range(500)
            for _delay, action in build_life_sequence(status, random.Random(seed))
            if "variant" in action
        }

        self.assertGreaterEqual(len(seen), 90)

    def test_life_sequence_blinks_often(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        sequences = [build_life_sequence(status, random.Random(seed)) for seed in range(120)]
        blink_sequences = [
            sequence
            for sequence in sequences
            if any(action["action"] == "face" and action["emotion"] == "blink" for _delay, action in sequence)
        ]

        self.assertGreaterEqual(len(blink_sequences), 48)

    def test_life_sequence_uses_pupil_glances(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        emotions = [
            action["emotion"]
            for seed in range(50)
            for _delay, action in build_life_sequence(status, random.Random(seed), include_motion=False)
            if action["action"] == "face"
        ]

        self.assertTrue(any(emotion.startswith("glance_") for emotion in emotions))

    def test_life_sequence_uses_mouth_impulses_and_breathing(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        emotions = [
            action["emotion"]
            for seed in range(80)
            for _delay, action in build_life_sequence(status, random.Random(seed), include_motion=False)
            if action["action"] == "face"
        ]

        self.assertTrue(any(emotion.startswith("mouth_") for emotion in emotions))
        self.assertIn("breathe", emotions)
        self.assertIn("deep_breathe", emotions)

    def test_life_sequence_uses_micro_sleep_rarely(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        emotions = [
            action["emotion"]
            for seed in range(120)
            for _delay, action in build_life_sequence(status, random.Random(seed), include_motion=False)
            if action["action"] == "face"
        ]

        self.assertIn("micro_sleep", emotions)

    def test_life_motion_is_mostly_small_and_slow(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        motions = [
            (delay, action)
            for seed in range(50)
            for delay, action in build_life_sequence(status, random.Random(seed))
            if action["action"] == "motion"
        ]
        self.assertTrue(motions)
        subtle = [
            (delay, motion)
            for delay, motion in motions
            if all(
                abs(point["yaw_pct"] - DEFAULT_IDLE_YAW_PCT) <= 5
                and abs(point["pitch_pct"] - DEFAULT_IDLE_PITCH_PCT) <= 4
                for point in motion["points"]
            )
        ]
        self.assertGreaterEqual(len(subtle), len(motions) // 2)
        for delay, motion in subtle:
            self.assertGreaterEqual(delay, 700)
            self.assertLessEqual(motion["speed_pct"], 12)
            for point in motion["points"]:
                self.assertLessEqual(abs(point["yaw_pct"] - DEFAULT_IDLE_YAW_PCT), 5)
                self.assertLessEqual(abs(point["pitch_pct"] - DEFAULT_IDLE_PITCH_PCT), 4)
                self.assertGreaterEqual(point["duration_ms"], 1400)

    def test_life_sequence_has_rare_big_desk_sweep_motion(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        motions = [
            (delay, action)
            for seed in range(300)
            for delay, action in build_life_sequence(status, random.Random(seed))
            if action["action"] == "motion"
        ]
        big_horizontal = [
            (delay, motion)
            for delay, motion in motions
            if any(abs(point["yaw_pct"]) >= 60 for point in motion["points"])
        ]
        desk_spins = [
            motion
            for _delay, motion in motions
            if motion.get("variant") == "desk_spin"
        ]
        vertical_motion = [
            (delay, motion)
            for delay, motion in motions
            if any(abs(point["pitch_pct"] - DEFAULT_IDLE_PITCH_PCT) >= 4 for point in motion["points"])
        ]
        big_faces = [
            (delay, action)
            for seed in range(300)
            for delay, action in build_life_sequence(status, random.Random(seed))
            if action["action"] == "face" and action["emotion"] in {"glance_left", "glance_right", "glance_up", "glance_down"}
        ]

        self.assertTrue(big_horizontal)
        self.assertTrue(desk_spins)
        self.assertTrue(vertical_motion)
        self.assertGreaterEqual(len(big_faces), (len(big_horizontal) + len(vertical_motion)))
        self.assertLess(len(big_horizontal), len(motions))
        for delay, motion in big_horizontal + vertical_motion:
            self.assertGreaterEqual(delay, 0)
            self.assertLessEqual(motion["speed_pct"], 45)
            for point in motion["points"]:
                self.assertLessEqual(abs(point["yaw_pct"]), 75)
                self.assertLessEqual(point["pitch_pct"], DEFAULT_IDLE_PITCH_PCT + 22)
                self.assertGreaterEqual(point["pitch_pct"], DEFAULT_IDLE_PITCH_PCT - 30)

    def test_life_motion_returns_to_high_idle_pose(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        motions = [
            action
            for seed in range(300)
            for _delay, action in build_life_sequence(status, random.Random(seed))
            if action["action"] == "motion"
        ]

        self.assertTrue(motions)
        for motion in motions:
            last = motion["points"][-1]
            self.assertEqual(last["yaw_pct"], DEFAULT_IDLE_YAW_PCT)
            self.assertEqual(last["pitch_pct"], DEFAULT_IDLE_PITCH_PCT)

    def test_life_sequence_pairs_vertical_faces_with_vertical_motion(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        vertical_sequences = []
        for seed in range(400):
            sequence = build_life_sequence(status, random.Random(seed))
            has_vertical_face = any(
                action["action"] == "face" and action["emotion"] in {"glance_up", "glance_down"}
                for _delay, action in sequence
            )
            has_vertical_motion = any(
                action["action"] == "motion"
                and any(abs(point["pitch_pct"] - DEFAULT_IDLE_PITCH_PCT) >= 18 for point in action["points"])
                for _delay, action in sequence
            )
            if has_vertical_face and has_vertical_motion:
                vertical_sequences.append(sequence)

        self.assertTrue(vertical_sequences)

    def test_life_sequence_can_disable_motion(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        for seed in range(50):
            sequence = build_life_sequence(status, random.Random(seed), include_motion=False)
            self.assertNotIn("motion", {action["action"] for _delay, action in sequence})

    def test_life_question_face_is_never_final_state(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        for seed in range(500):
            sequence = build_life_sequence(status, random.Random(seed))
            face_actions = [action for _delay, action in sequence if action["action"] == "face"]
            self.assertTrue(face_actions)
            self.assertNotEqual(face_actions[-1]["emotion"], "question")


if __name__ == "__main__":
    unittest.main()
