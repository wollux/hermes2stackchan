from __future__ import annotations

import datetime as dt
import json
import importlib.util
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
    bridge_base_url_from_audio_url,
    build_display_payload,
    build_info_payload,
    build_idle_sleep_payload,
    build_life_sequence,
    build_multipart_form_data,
    build_motion_profile_points,
    build_restore_device_payload,
    build_device_settings_snapshot,
    build_power_change_actions,
    build_power_followup_actions,
    build_reminder,
    build_sensor_reaction_actions,
    sensor_upright_cleanup_actions,
    build_touch_emotion_actions,
    build_touch_lamp_payload,
    command_requests_display_sleep,
    direct_system_command_from_transcript,
    direct_local_command_from_transcript,
    due_reminders,
    DEFAULT_IDLE_PITCH_PCT,
    DEFAULT_IDLE_YAW_PCT,
    LifeMotionLimiter,
    external_reply_actions,
    face_snapshot,
    add_reminder,
    load_config,
    local_command_may_need_status,
    missing_status_paths,
    motion_action_duration_ms,
    life_motion_size,
    mqtt_settle_delay_after_publish_s,
    notify_actions_from_payload,
    notify_text_from_payload,
    normalize_motion_points,
    pending_reminders,
    reminder_actions,
    schedule_reminders_from_actions,
    split_post_tts_system_actions,
    parse_hermes_action_response,
    parse_env_file,
    publish_action_messages,
    should_listen_for_followup,
    STACKCHAN_PRESENCE,
    build_hermes_messages,
    build_hermes_vision_messages,
    build_bridge_healthz,
    build_hermes_context_package,
    image_data_url,
    image_result_aspect_score,
    image_search_queries,
    command_counts_as_idle_activity,
    current_face_action,
    request_id_counts_as_idle_activity,
    rgb565_to_jpeg,
    SensorReactionState,
    TouchEmotionState,
    sensor_status_is_face_down,
    sensor_status_is_sideways,
    status_allows_life_animation,
    action_queue_record,
    build_waiting_animation_actions,
    waiting_animation_delay_s,
    privacy_policy_for_mode,
    record_interaction_event,
    local_history_reply_from_transcript,
    speech_cleanup_actions,
    transcript_mentions_led_control,
)


class BridgeConfigTests(unittest.TestCase):
    def test_load_example_config(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})

        self.assertEqual(config.mqtt.host, "localhost")
        self.assertEqual(config.hermes.base_url, "http://127.0.0.1:8642")
        self.assertEqual(config.companion.history_keep, 200)
        self.assertIn("desk", config.pairs)
        self.assertEqual(config.pairs["desk"].display_topic, "hermes-stackchan/desk/cmd/display")
        self.assertEqual(config.pairs["desk"].move_topic, "hermes-stackchan/desk/cmd/move")
        self.assertEqual(config.pairs["desk"].motion_topic, "hermes-stackchan/desk/cmd/motion")
        self.assertEqual(config.pairs["desk"].device_topic, "hermes-stackchan/desk/cmd/device")
        self.assertEqual(config.pairs["desk"].audio_topic, "hermes-stackchan/desk/cmd/audio")
        self.assertEqual(config.pairs["desk"].events_topic, "hermes-stackchan/desk/events")
        self.assertEqual(config.pairs["desk"].mood_default, "playful")
        self.assertEqual(config.pairs["desk"].privacy_mode, "normal")
        self.assertEqual(config.pairs["desk"].proactivity, "playful")
        self.assertEqual(config.pairs["desk"].wakeword, "Computer")

    def test_pair_profile_env_overrides(self) -> None:
        config = load_config(
            Path("config/pairs.example.json"),
            env_path=None,
            environ={
                "H2S_PRIVACY_MODE": "private",
                "H2S_PAIR_MOOD": "focused",
                "H2S_PAIR_PROACTIVITY": "quiet",
                "H2S_PAIR_WAKEWORD": "Hermes",
                "H2S_PAIR_VOICE": "de-DE-ConradNeural",
            },
        )
        pair = config.pairs["desk"]

        self.assertEqual(pair.privacy_mode, "private")
        self.assertEqual(pair.mood_default, "focused")
        self.assertEqual(pair.proactivity, "quiet")
        self.assertEqual(pair.wakeword, "Hermes")
        self.assertEqual(pair.voice, "de-DE-ConradNeural")

    def test_privacy_modes_define_retention_and_permissions(self) -> None:
        normal = privacy_policy_for_mode("normal")
        private = privacy_policy_for_mode("private")
        debug = privacy_policy_for_mode("debug")

        self.assertEqual(normal["audio_retention"], "off")
        self.assertTrue(normal["hermes_allowed"])
        self.assertFalse(private["text_history"])
        self.assertFalse(private["camera_allowed"])
        self.assertFalse(private["hermes_allowed"])
        self.assertEqual(debug["audio_retention"], "debug")

    def test_hermes_context_package_contains_profile_privacy_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(
                Path("config/pairs.example.json"),
                env_path=None,
                environ={"H2S_COMPANION_STATE_STORE": str(Path(tmp) / "state.json")},
            )
            pair = config.pairs["desk"]
            status = {"battery_pct": 82, "external_power": True, "ui": {"mode": "face"}, "face": {"emotion": "friendly"}}
            context = build_hermes_context_package(config, pair, status)

        self.assertEqual(context["pair"]["pair_id"], "desk")
        self.assertEqual(context["pair"]["wakeword"], "Computer")
        self.assertEqual(context["mood"]["state"], "playful")
        self.assertEqual(context["privacy"]["mode"], "normal")
        self.assertIn("time_date_weekday_calendar_week", context["local_capabilities"])
        self.assertEqual(context["status_summary"]["battery_pct"], 82)

    def test_hermes_prompt_includes_companion_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(
                Path("config/pairs.example.json"),
                env_path=None,
                environ={"H2S_COMPANION_STATE_STORE": str(Path(tmp) / "state.json")},
            )
            pair = config.pairs["desk"]
            context = build_hermes_context_package(config, pair, {"battery_pct": 88})

            messages = build_hermes_messages(pair, "", "", {"battery_pct": 88}, "Hallo", context)
        system_text = messages[0]["content"]

        self.assertIn("Companion context JSON", system_text)
        self.assertIn("privacy_mode", system_text)
        self.assertIn("local_capabilities", system_text)
        self.assertIn("configured wakeword", system_text)

    def test_action_queue_priority_classifies_sources(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        pair = config.pairs["desk"]

        safety = action_queue_record(pair, pair.system_topic, {"action": "shutdown", "request_id": "cmd-1"}, "queued")
        idle = action_queue_record(pair, pair.face_topic, {"emotion": "soft_blink", "request_id": "life-1"}, "queued")
        notify = action_queue_record(pair, pair.audio_topic, {"action": "play_tts_url", "request_id": "notify-1"}, "queued")

        self.assertEqual(safety["priority"], "user")
        self.assertEqual(idle["priority"], "idle")
        self.assertEqual(notify["priority"], "notification")

    def test_healthz_payload_has_v1_sections_without_status_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(
                Path("config/pairs.example.json"),
                env_path=None,
                environ={"H2S_COMPANION_STATE_STORE": str(Path(tmp) / "state.json")},
            )
            pair = config.pairs["desk"]
            payload = build_bridge_healthz(config, pair, include_status=False)

        self.assertEqual(payload["service"], "hermes2stackchan-bridge")
        self.assertIn("companion", payload)
        self.assertIn("queue", payload)
        self.assertEqual(payload["stackchan"]["status_available"], False)

    def test_local_history_reply_uses_user_readable_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(
                Path("config/pairs.example.json"),
                env_path=None,
                environ={
                    "H2S_COMPANION_STATE_STORE": str(Path(tmp) / "state.json"),
                    "H2S_INTERACTION_HISTORY_STORE": str(Path(tmp) / "history.jsonl"),
                },
            )
            pair = config.pairs["desk"]
            record_interaction_event(
                config,
                pair,
                {"kind": "speech", "transcript": "Wie spaet ist es?", "reply": "Es ist 14 Uhr 30."},
            )
            reply = local_history_reply_from_transcript("Was hast du gesagt?", config, pair)

        self.assertIsNotNone(reply)
        self.assertIn("Wie spaet ist es", reply[0])
        self.assertIn("Es ist 14 Uhr 30", reply[0])

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

    def test_bridge_public_url_can_derive_from_audio_url(self) -> None:
        self.assertEqual(
            bridge_base_url_from_audio_url("http://192.168.99.58:8788/stackchan/audio"),
            "http://192.168.99.58:8788",
        )

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

    def test_info_payload_uses_german_date_fields(self) -> None:
        now = dt.datetime(2026, 5, 13, 14, 37)
        payload = build_info_payload(now, "info-001")

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["mode"], "info")
        self.assertEqual(payload["time"], "14:37")
        self.assertEqual(payload["date"], "13.05.2026")
        self.assertEqual(payload["weekday"], "Mittwoch")
        self.assertEqual(payload["duration_ms"], 0)
        self.assertEqual(payload["request_id"], "info-001")

    def test_info_action_targets_display_topic(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        topic, payload = action_to_topic_payload(pair, {"action": "info"}, "info-002")

        self.assertEqual(topic, pair.display_topic)
        self.assertEqual(payload["mode"], "info")
        self.assertIn("time", payload)
        self.assertIn("date", payload)
        self.assertIn("weekday", payload)
        self.assertEqual(payload["request_id"], "info-002")

    def test_display_image_action_targets_display_topic(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        topic, payload = action_to_topic_payload(
            config.pairs["desk"],
            {
                "action": "display_image",
                "url": "http://127.0.0.1:8788/stackchan/images/test.jpg",
                "caption": "Kamera",
            },
            "img-1",
        )

        self.assertEqual(topic, "hermes-stackchan/desk/cmd/display")
        self.assertEqual(payload["mode"], "image")
        self.assertEqual(payload["format"], "jpeg")
        self.assertEqual(payload["width"], 320)
        self.assertEqual(payload["height"], 240)
        self.assertEqual(payload["request_id"], "img-1")

    def test_image_result_aspect_score_prefers_stackchan_ratio(self) -> None:
        good = {"width": 1024, "height": 768, "url": "https://example.com/good.jpg"}
        tall = {"width": 400, "height": 1200, "url": "https://example.com/tall.jpg"}
        tiny = {"width": 120, "height": 90, "url": "https://example.com/tiny.jpg"}

        self.assertLess(image_result_aspect_score(good), image_result_aspect_score(tall))
        self.assertLess(image_result_aspect_score(good), image_result_aspect_score(tiny))

    def test_image_search_queries_simplifies_long_photo_requests(self) -> None:
        queries = image_search_queries("Spandau Berlin Altstadt Havel Zitadelle Foto")

        self.assertEqual(queries[0], "Spandau Berlin Altstadt Havel Zitadelle Foto")
        self.assertIn("Spandau Berlin Altstadt Havel Zitadelle", queries)
        self.assertIn("Spandau Berlin Altstadt", queries)
        self.assertIn("Spandau", queries)

    def test_idle_sleep_ignores_life_and_its_own_commands(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        pair = config.pairs["desk"]

        self.assertFalse(request_id_counts_as_idle_activity("life-123"))
        self.assertFalse(request_id_counts_as_idle_activity("idle-sleep-123"))
        self.assertFalse(command_counts_as_idle_activity(pair, pair.face_topic, {"request_id": "life-123"}))
        self.assertFalse(command_counts_as_idle_activity(pair, pair.device_topic, {"display_sleep": True, "request_id": "manual"}))
        self.assertTrue(command_requests_display_sleep(pair, pair.device_topic, {"display_sleep": True, "request_id": "manual"}))
        self.assertTrue(command_requests_display_sleep(pair, pair.system_topic, {"action": "display_sleep"}))
        self.assertFalse(command_requests_display_sleep(pair, pair.system_topic, {"action": "display_wake"}))
        self.assertTrue(command_counts_as_idle_activity(pair, pair.display_topic, {"text": "Hallo", "request_id": "notify-123"}))
        self.assertTrue(command_counts_as_idle_activity(pair, pair.system_topic, {"action": "display_wake", "request_id": "reminder-123"}))

    def test_idle_sleep_payload_turns_display_off_only(self) -> None:
        payload = build_idle_sleep_payload("idle-sleep-test")

        self.assertEqual(payload["request_id"], "idle-sleep-test")
        self.assertTrue(payload["display_sleep"])
        self.assertNotIn("display_wake", payload)
        self.assertNotIn("motion", payload)

    def test_hermes_vision_messages_include_data_url(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        messages = build_hermes_vision_messages(
            config.pairs["desk"],
            "",
            "",
            {},
            "Was siehst du?",
            b"fake-image",
            "image/jpeg",
        )

        self.assertEqual(messages[1]["content"][0]["text"], "Was siehst du?")
        self.assertTrue(messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(image_data_url(b"x", "image/png"), "data:image/png;base64,eA==")

    @unittest.skipIf(importlib.util.find_spec("PIL") is None, "Pillow not installed")
    def test_rgb565_camera_conversion_uses_big_endian(self) -> None:
        import io
        from PIL import Image

        row = bytes([0xF8, 0x00]) * 8 + bytes([0x07, 0xE0]) * 8 + bytes([0x00, 0x1F]) * 8
        pixels = row * 8
        jpeg = rgb565_to_jpeg(pixels, 24, 8)
        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        red = image.getpixel((4, 4))
        green = image.getpixel((12, 4))
        blue = image.getpixel((20, 4))

        self.assertGreater(red[0], red[1] + red[2])
        self.assertGreater(green[1], green[0] + green[2])
        self.assertGreater(blue[2], blue[0] + blue[1])

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

    def test_motion_action_duration_sums_segments_and_holds(self) -> None:
        duration = motion_action_duration_ms(
            {
                "action": "motion",
                "points": [
                    {"duration_ms": 1200, "hold_ms": 300},
                    {"duration_ms": 800},
                ],
            }
        )

        self.assertEqual(duration, 2300)

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

    def test_hermes_prompt_forbids_say_for_normal_replies(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        pair = config.pairs["desk"]

        messages = build_hermes_messages(pair, "", "", {}, "Sag hallo.")
        system_text = messages[0]["content"]

        self.assertIn("Do not use action say", system_text)
        self.assertNotIn("Use action say", system_text)
        self.assertIn("top-level reply", system_text)

    def test_external_tts_reply_uses_display_instead_of_say(self) -> None:
        actions = external_reply_actions(
            [{"action": "say", "text": "Hallo Wolfgang.", "emotion": "speaking"}],
            "Hallo Wolfgang.",
            tts_enabled=True,
        )

        self.assertEqual(actions[0]["action"], "display")
        self.assertNotIn("say", {action["action"] for action in actions})

    def test_external_non_tts_reply_keeps_say(self) -> None:
        actions = [{"action": "say", "text": "Hallo Wolfgang.", "emotion": "speaking"}]

        self.assertEqual(external_reply_actions(actions, "Hallo Wolfgang.", tts_enabled=False), actions)

    def test_say_action_is_mapped_to_display_without_beep(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        topic, payload = action_to_topic_payload(
            pair,
            {"action": "say", "text": "Hallo Wolfgang.", "emotion": "speaking", "beep": True},
            "legacy-say-001",
        )

        self.assertEqual(topic, pair.display_topic)
        self.assertEqual(payload["mode"], "text")
        self.assertEqual(payload["text"], "Hallo Wolfgang.")
        self.assertNotIn("beep", payload)

    def test_display_payload_is_hard_clamped_for_stackchan(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]
        long_text = "Wort " * 200

        _topic, payload = action_to_topic_payload(pair, {"action": "display", "text": long_text}, "display-001")

        self.assertLessEqual(len(payload["text"]), 320)

    def test_notify_payload_extracts_text_and_maps_say_to_display(self) -> None:
        text = notify_text_from_payload({"reply": "Hallo vom externen Hermes."})
        actions = notify_actions_from_payload(
            {"actions": [{"action": "say", "text": "Bitte nicht say."}, {"action": "face", "emotion": "happy"}]},
            text,
            tts_enabled=True,
        )

        self.assertEqual(text, "Hallo vom externen Hermes.")
        self.assertEqual(actions[0]["action"], "display")
        self.assertIn("face", {action["action"] for action in actions})
        self.assertNotIn("say", {action["action"] for action in actions})

    def test_speech_cleanup_turns_led_off_unless_user_controls_leds(self) -> None:
        self.assertFalse(transcript_mentions_led_control("Wie ist das Wetter?"))
        self.assertTrue(transcript_mentions_led_control("Mach die Lampe blau."))

        cleanup = speech_cleanup_actions("Wie ist das Wetter?", [{"action": "face", "emotion": "thinking"}])
        self.assertEqual(cleanup, [{"action": "led", "mode": "off", "r": 0, "g": 0, "b": 0}])
        self.assertEqual(speech_cleanup_actions("Mach die LED blau.", [{"action": "led", "mode": "solid"}]), [])
        self.assertEqual(speech_cleanup_actions("Hilfe.", [{"action": "led", "mode": "blink"}]), [])

    def test_waiting_animation_is_visible_and_safe(self) -> None:
        first = build_waiting_animation_actions(0)
        self.assertEqual(first[0], {"action": "led", "mode": "off", "r": 0, "g": 0, "b": 0})
        self.assertEqual(first[1]["emotion"], "thinking")

        steps = [action for step in range(1, 13) for action in build_waiting_animation_actions(step)]
        emotions = {action.get("emotion") for action in steps if action.get("action") == "face"}
        self.assertGreaterEqual(len(emotions), 6)
        self.assertNotIn("motion", {action.get("action") for action in build_waiting_animation_actions(10)})
        self.assertIn("motion", {action.get("action") for action in build_waiting_animation_actions(10, include_motion=True)})
        self.assertTrue(all(action.get("action") not in {"audio", "sound", "display"} for action in steps))
        self.assertLess(waiting_animation_delay_s(1), 1.0)

    def test_mqtt_settle_delay_spaces_text_before_followup_actions(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        self.assertGreater(
            mqtt_settle_delay_after_publish_s(pair.say_topic, {"text": "Hallo von Hermes."}, pair),
            0.8,
        )
        self.assertGreater(
            mqtt_settle_delay_after_publish_s(pair.display_topic, {"text": "Direkte Nachricht"}, pair),
            0.6,
        )
        self.assertEqual(
            mqtt_settle_delay_after_publish_s(pair.audio_topic, {"action": "play_tts_url"}, pair),
            0.0,
        )

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

    def test_system_sleep_wake_shutdown_actions_to_topic_payload(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        pair = config.pairs["desk"]

        for action_name in ("display_sleep", "display_wake", "shutdown", "power_off"):
            topic, payload = action_to_topic_payload(
                pair,
                {"action": "system", "system_action": action_name},
                f"system-{action_name}",
            )
            self.assertEqual(topic, "hermes-stackchan/desk/cmd/system")
            self.assertEqual(payload["request_id"], f"system-{action_name}")
            self.assertEqual(payload["action"], action_name)

    def test_direct_spoken_system_commands(self) -> None:
        self.assertEqual(
            direct_system_command_from_transcript("Geh schlafen.")[:2],
            ("Ich schlafe jetzt.", []),
        )
        self.assertEqual(direct_system_command_from_transcript("Geh schlafen.")[2], "display_sleep")
        self.assertEqual(direct_system_command_from_transcript("StackChan runterfahren.")[2], "shutdown")
        self.assertEqual(direct_system_command_from_transcript("Wach auf.")[1][0]["system_action"], "display_wake")
        self.assertIsNone(direct_system_command_from_transcript("Kannst du schlafen?"))
        self.assertIsNone(direct_system_command_from_transcript("Bitte nicht schlafen."))
        self.assertIsNone(direct_system_command_from_transcript("Radio abschalten."))

    def test_direct_local_device_commands_skip_hermes(self) -> None:
        brightness = direct_local_command_from_transcript("Helligkeit 80 Prozent.")
        volume = direct_local_command_from_transcript("Computer Lautstaerke auf 55.")
        display = direct_local_command_from_transcript("Display aus.")

        self.assertEqual(brightness, ("Helligkeit 80 Prozent.", [{"action": "device", "brightness_pct": 80}], ""))
        self.assertEqual(volume, ("Lautstaerke 55 Prozent.", [{"action": "device", "volume_pct": 55}], ""))
        self.assertEqual(display[2], "display_sleep")

    def test_direct_local_relative_device_commands_use_status(self) -> None:
        status = {"brightness_pct": 45, "speaker": {"volume_pct": 80}}

        brighter = direct_local_command_from_transcript("Mach heller.", status)
        quieter = direct_local_command_from_transcript("Mach leiser.", status)

        self.assertEqual(brighter, ("Helligkeit 55 Prozent.", [{"action": "device", "brightness_pct": 55}], ""))
        self.assertEqual(quieter, ("Lautstaerke 70 Prozent.", [{"action": "device", "volume_pct": 70}], ""))
        self.assertIsNone(direct_local_command_from_transcript("Mach leiser."))
        self.assertTrue(local_command_may_need_status("Mach leiser."))

    def test_direct_local_status_and_sensor_questions_skip_hermes(self) -> None:
        status = {
            "battery_pct": 82,
            "external_power": False,
            "battery_charging": False,
            "brightness_pct": 66,
            "speaker": {"volume_pct": 44},
            "temperature": {"soc_c": 41, "servo_yaw_c": 30, "servo_pitch_c": 31},
            "sensors": {
                "imu": {
                    "ready": True,
                    "accel_mg": {"x": 820, "y": 0, "z": 650},
                    "motion_score_pct": 72,
                    "motion_active": True,
                },
                "ltr553": {
                    "ready": True,
                    "proximity_delta": 180,
                    "near": True,
                },
            },
        }

        self.assertIn("Akku 82 Prozent", direct_local_command_from_transcript("Wie ist dein Akku?", status)[0])
        self.assertIn("SoC 41 Grad", direct_local_command_from_transcript("Temperatur?", status)[0])
        self.assertEqual(direct_local_command_from_transcript("Wie ist die Helligkeit?", status)[0], "Helligkeit 66 Prozent.")
        self.assertEqual(direct_local_command_from_transcript("Wie ist die Lautstaerke?", status)[0], "Lautstaerke 44 Prozent.")
        self.assertIn("Naehe erkannt", direct_local_command_from_transcript("Ist mein Finger am Sensor?", status)[0])
        self.assertEqual(direct_local_command_from_transcript("Liegst du auf der Seite?", status)[0], "Ich liege auf der Seite.")
        self.assertIn("Bewegung erkannt", direct_local_command_from_transcript("Wirst du geschuettelt?", status)[0])

    def test_direct_local_time_calendar_answers_skip_hermes(self) -> None:
        now = dt.datetime(2026, 5, 12, 14, 30)

        self.assertEqual(direct_local_command_from_transcript("Wie spaet ist es?", now=now)[0], "Es ist 14 Uhr 30.")
        self.assertEqual(direct_local_command_from_transcript("Welcher Wochentag ist heute?", now=now)[0], "Heute ist Dienstag.")
        self.assertEqual(
            direct_local_command_from_transcript("Welches Datum haben wir?", now=now)[0],
            "Heute ist Dienstag, der 12. Mai 2026.",
        )
        self.assertEqual(direct_local_command_from_transcript("Ist heute Dienstag?", now=now)[0], "Ja, heute ist Dienstag.")
        self.assertIn("Kalenderwoche 20", direct_local_command_from_transcript("Welche Kalenderwoche?", now=now)[0])
        self.assertIn("9 Stunden und 30 Minuten", direct_local_command_from_transcript("Wie lange bis Mitternacht?", now=now)[0])
        info = direct_local_command_from_transcript("Zeige Datum und Uhrzeit.", now=now)
        self.assertEqual(info[0], "Info Modus.")
        self.assertEqual(info[1][0]["action"], "info")

    def test_direct_local_reminder_timer_math_and_random_skip_hermes(self) -> None:
        now = dt.datetime(2026, 5, 12, 14, 30)

        timer = direct_local_command_from_transcript("Stell einen Timer auf 5 Minuten.", now=now)
        self.assertEqual(timer[0], "Timer auf 5 Minuten gestellt.")
        self.assertEqual(timer[1][1]["action"], "reminder")
        self.assertEqual(timer[1][1]["delay_s"], 300)

        reminder = direct_local_command_from_transcript("Erinnere mich in 10 Minuten an Tee.", now=now)
        self.assertEqual(reminder[0], "Erinnerung gestellt.")
        self.assertEqual(reminder[1][1]["text"], "tee")
        self.assertEqual(reminder[1][1]["delay_s"], 600)

        self.assertIsNone(direct_local_command_from_transcript("Erinnere mich bitte.", now=now))
        self.assertEqual(direct_local_command_from_transcript("Was ist 3 plus 4?")[0], "Das sind 7.")
        self.assertEqual(direct_local_command_from_transcript("Was sind 20 Prozent von 50?")[0], "Das sind 10.")
        self.assertTrue(direct_local_command_from_transcript("Wuerfel.")[0].startswith("Ich wuerfle "))

    def test_direct_local_more_device_commands_skip_hermes(self) -> None:
        self.assertEqual(direct_local_command_from_transcript("Kopf nach links.")[1][0]["direction"], "left")
        self.assertEqual(direct_local_command_from_transcript("Schau nach oben.")[1][0]["pitch_target_pct"], 65)
        self.assertEqual(direct_local_command_from_transcript("Mach ein Foto.")[1][0]["system_action"], "take_photo")
        self.assertEqual(direct_local_command_from_transcript("Piep.")[1][0]["action"], "sound")

    def test_direct_local_combined_tasks_go_to_hermes(self) -> None:
        self.assertIsNone(direct_local_command_from_transcript("Helligkeit 80 und sag mir das Wetter."))
        self.assertIsNone(direct_local_command_from_transcript("Mach den Bildschirm aus und erinnere mich morgen."))
        self.assertIsNone(direct_local_command_from_transcript("Bitte nicht die Helligkeit aendern."))

    def test_split_post_tts_system_actions(self) -> None:
        actions, post_tts = split_post_tts_system_actions(
            [
                {"action": "face", "emotion": "sad"},
                {"action": "system", "system_action": "shutdown"},
                {"action": "led", "mode": "off"},
            ]
        )

        self.assertEqual(post_tts, "shutdown")
        self.assertEqual([action["action"] for action in actions], ["face", "led"])

    def test_publish_action_messages_skips_when_stackchan_offline(self) -> None:
        config = load_config(Path("config/pairs.example.json"), env_path=None, environ={})
        pair = config.pairs["desk"]
        published: list[tuple[str, str]] = []

        class FakeClient:
            def publish(self, topic: str, body: str, qos: int, retain: bool) -> SimpleNamespace:
                published.append((topic, body))
                return SimpleNamespace(wait_for_publish=lambda timeout=None: None)

        STACKCHAN_PRESENCE.clear()
        publish_action_messages(FakeClient(), [(pair.face_topic, {"emotion": "happy"})], pair)
        self.assertEqual(published, [])

        STACKCHAN_PRESENCE.mark_seen(pair)
        publish_action_messages(FakeClient(), [(pair.face_topic, {"emotion": "happy"})], pair)
        self.assertEqual(len(published), 1)
        STACKCHAN_PRESENCE.clear()

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

    def test_current_life_base_does_not_keep_funny_transient_faces(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "cross_eyes", "intensity_pct": 71},
        }

        self.assertEqual(current_face_action(status), {"action": "face", "emotion": "neutral", "intensity_pct": 71})

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
            "head": {"pan_pct": 0, "tilt_pct": 0, "ready": True, "motion_active": False},
            "face": {"emotion": "neutral", "intensity_pct": 60},
            "ui": {"mode": "face"},
            "led": {"mode": "off", "mode_id": 0, "r": 0, "g": 0, "b": 0, "ready": True},
            "speaker": {"ready": True, "volume_pct": 80},
            "temperature": {"soc_c": 40, "servo_yaw_c": -1, "servo_pitch_c": -1},
            "interaction": {"active": False, "last_source": "none", "last_ms": 0},
            "sensors": {
                "imu": {
                    "ready": True,
                    "accel_mg": {"x": 0, "y": 0, "z": 1000},
                    "gyro_dps": {"x": 0, "y": 0, "z": 0},
                    "motion_score_pct": 0,
                    "motion_active": False,
                },
                "ltr553": {
                    "ready": True,
                    "proximity_raw": 0,
                    "ambient_raw": 100,
                    "proximity_baseline": 0,
                    "proximity_delta": 0,
                    "near": False,
                    "light_changed": False,
                },
            },
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

    def test_device_settings_snapshot_extracts_safe_settings(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        snapshot = build_device_settings_snapshot(
            pair,
            {"volume_pct": 120, "brightness_pct": -5, "display_sleeping": True},
            "test",
        )

        self.assertEqual(snapshot["pair_id"], "desk")
        self.assertEqual(snapshot["stackchan_id"], "stackchan-desk")
        self.assertEqual(snapshot["volume_pct"], 100)
        self.assertEqual(snapshot["brightness_pct"], 0)
        self.assertNotIn("display_sleeping", snapshot)

    def test_restore_device_payload_uses_retained_status_shape(self) -> None:
        payload = build_restore_device_payload(
            {
                "brightness_pct": 42,
                "speaker": {"volume_pct": 77},
                "display_sleeping": True,
            },
            display_wake=True,
        )

        self.assertEqual(payload, {"brightness_pct": 42, "volume_pct": 77, "display_wake": True})

    def sensor_status(
        self,
        *,
        display_sleeping: bool = False,
        recording: bool = False,
        speaking: bool = False,
        tilt_pct: int = DEFAULT_IDLE_PITCH_PCT,
        proximity_delta: int = 0,
        proximity_raw: int = 0,
        proximity_near: bool = False,
        motion_score_pct: int = 0,
        motion_active: bool = False,
        head_motion_active: bool = False,
        accel_x: int = 0,
        accel_y: int = 820,
        accel_z: int = 540,
    ) -> dict[str, object]:
        return {
            "display_sleeping": display_sleeping,
            "recording": recording,
            "speaking": speaking,
            "audio": {"recording": recording},
            "head": {"tilt_pct": tilt_pct, "motion_active": head_motion_active},
            "sensors": {
                "imu": {
                    "ready": True,
                    "accel_mg": {"x": accel_x, "y": accel_y, "z": accel_z},
                    "gyro_dps": {"x": 0, "y": 0, "z": 0},
                    "motion_score_pct": motion_score_pct,
                    "motion_active": motion_active,
                },
                "ltr553": {
                    "ready": True,
                    "proximity_raw": proximity_raw,
                    "ambient_raw": 100,
                    "proximity_baseline": 8,
                    "proximity_delta": proximity_delta,
                    "near": proximity_near,
                    "light_changed": False,
                },
            },
        }

    def test_sensor_reaction_filters_proximity_noise_and_restores_head(self) -> None:
        state = SensorReactionState()
        near = self.sensor_status(proximity_delta=160, proximity_raw=500, proximity_near=True)

        first_actions, first_reasons = build_sensor_reaction_actions(near, state, now_s=10.0)
        second_actions, second_reasons = build_sensor_reaction_actions(near, state, now_s=10.1)

        self.assertEqual(first_actions, [])
        self.assertEqual(first_reasons, [])
        self.assertIn("proximity_near", second_reasons)
        self.assertEqual([action["action"] for action in second_actions], ["face", "move"])
        self.assertEqual(second_actions[0]["emotion"], "glance_down")
        self.assertLess(second_actions[1]["pitch_target_pct"], DEFAULT_IDLE_PITCH_PCT)

        clear = self.sensor_status(proximity_delta=8, proximity_raw=10, proximity_near=False)
        self.assertEqual(build_sensor_reaction_actions(clear, state, now_s=11.2)[0], [])
        self.assertEqual(build_sensor_reaction_actions(clear, state, now_s=11.3)[0], [])
        restore_actions, restore_reasons = build_sensor_reaction_actions(clear, state, now_s=11.4)

        self.assertEqual(restore_reasons, ["proximity_clear"])
        self.assertEqual(restore_actions, [{"action": "move", "pitch_target_pct": DEFAULT_IDLE_PITCH_PCT}])

    def test_sensor_reaction_wakes_sleeping_display_on_confirmed_proximity(self) -> None:
        state = SensorReactionState()
        near = self.sensor_status(display_sleeping=True, proximity_delta=160, proximity_raw=500, proximity_near=True)

        self.assertEqual(build_sensor_reaction_actions(near, state, now_s=20.0)[0], [])
        actions, reasons = build_sensor_reaction_actions(near, state, now_s=20.1)

        self.assertIn("wake:proximity", reasons)
        self.assertEqual(actions[0], {"action": "system", "system_action": "display_wake"})
        self.assertEqual(actions[1]["action"], "face")
        self.assertEqual(actions[2]["action"], "move")

    def test_sensor_reaction_shake_uses_motion_score_and_cooldown(self) -> None:
        state = SensorReactionState()
        shake = self.sensor_status(motion_score_pct=75, motion_active=True)

        actions, reasons = build_sensor_reaction_actions(shake, state, now_s=30.0, source_hint="imu")
        immediate_actions, _ = build_sensor_reaction_actions(shake, state, now_s=30.5, source_hint="imu")
        later_actions, later_reasons = build_sensor_reaction_actions(shake, state, now_s=35.0, source_hint="imu")

        self.assertEqual(reasons, ["shake"])
        self.assertEqual(actions, [{"action": "face", "emotion": "surprise_pop", "intensity_pct": 90}])
        self.assertEqual(immediate_actions, [])
        self.assertEqual(later_reasons, ["shake"])
        self.assertEqual(later_actions[0]["emotion"], "surprise_pop")

    def test_sensor_reaction_sideways_requires_orientation_event(self) -> None:
        state = SensorReactionState()
        side = self.sensor_status(accel_x=820, accel_y=100, accel_z=120)

        self.assertEqual(build_sensor_reaction_actions(side, state, now_s=40.0)[0], [])
        side_actions, side_reasons = build_sensor_reaction_actions(side, state, now_s=40.1, source_hint="orientation")

        self.assertEqual(side_reasons, ["sideways"])
        self.assertEqual([action["action"] for action in side_actions], ["led", "face", "display", "local_tts"])
        self.assertEqual(side_actions[0], {"action": "led", "mode": "blink", "r": 255, "g": 0, "b": 0})
        self.assertEqual(side_actions[1]["emotion"], "help")
        self.assertEqual(side_actions[2]["text"], "HILFE!")
        self.assertIn("umgekippt", side_actions[3]["text"])

        pitched_head = self.sensor_status(accel_x=90, accel_y=430, accel_z=870)
        self.assertFalse(sensor_status_is_sideways(pitched_head))
        self.assertFalse(sensor_status_is_face_down(pitched_head))

        upright = self.sensor_status(accel_x=0)
        self.assertEqual(build_sensor_reaction_actions(upright, state, now_s=41.0)[0], [])
        upright_actions, upright_reasons = build_sensor_reaction_actions(upright, state, now_s=41.2, source_hint="orientation")

        self.assertEqual(upright_reasons, ["upright"])
        self.assertEqual([action["action"] for action in upright_actions], ["led", "face", "motion", "local_tts"])
        self.assertEqual(upright_actions[0], {"action": "led", "mode": "off", "r": 0, "g": 0, "b": 0})
        self.assertEqual(upright_actions[1], {"action": "face", "emotion": "thankful", "intensity_pct": 82})
        self.assertEqual(upright_actions[2]["speed_pct"], 72)
        self.assertIn("Danke", upright_actions[3]["text"])

    def test_sensor_reaction_upright_always_turns_leds_off(self) -> None:
        state = SensorReactionState()
        upright = self.sensor_status(accel_x=0)

        actions, reasons = build_sensor_reaction_actions(upright, state, now_s=45.0, source_hint="orientation")

        self.assertEqual(reasons, ["upright"])
        self.assertEqual(actions, [{"action": "led", "mode": "off", "r": 0, "g": 0, "b": 0}])
        self.assertEqual(
            sensor_upright_cleanup_actions(),
            [
                {"action": "led", "mode": "off", "r": 0, "g": 0, "b": 0},
                {"action": "face", "emotion": "neutral", "intensity_pct": 68},
            ],
        )

    def test_sensor_reaction_face_down_repeats_until_upright(self) -> None:
        state = SensorReactionState()
        face_down = self.sensor_status(accel_x=40, accel_y=60, accel_z=980)

        first_actions, first_reasons = build_sensor_reaction_actions(face_down, state, now_s=55.0, source_hint="orientation")
        quiet_actions, _ = build_sensor_reaction_actions(face_down, state, now_s=56.0, source_hint="orientation")
        repeat_actions, repeat_reasons = build_sensor_reaction_actions(face_down, state, now_s=57.1, source_hint="orientation")

        self.assertTrue(sensor_status_is_face_down(face_down))
        self.assertEqual(first_reasons, ["face_down"])
        self.assertEqual([action["action"] for action in first_actions], ["led", "face", "display", "motion", "local_tts"])
        self.assertEqual(first_actions[0]["mode"], "party")
        self.assertEqual(first_actions[1]["emotion"], "face_down")
        self.assertEqual(first_actions[2]["text"], "NICHT AUFS GESICHT!")
        self.assertIn("Nicht aufs Gesicht", first_actions[4]["text"])
        self.assertEqual(quiet_actions, [])
        self.assertEqual(repeat_reasons, ["face_down"])
        self.assertEqual([action["action"] for action in repeat_actions], ["led", "face", "display", "motion"])

        upright = self.sensor_status(accel_x=0)
        upright_actions, upright_reasons = build_sensor_reaction_actions(upright, state, now_s=58.3, source_hint="orientation")

        self.assertEqual(upright_reasons, ["upright"])
        self.assertEqual([action["action"] for action in upright_actions], ["led", "face", "motion", "local_tts"])
        self.assertEqual(upright_actions[0]["mode"], "off")

    def test_sensor_reaction_ignores_busy_recording_status(self) -> None:
        state = SensorReactionState()
        busy = self.sensor_status(
            recording=True,
            proximity_delta=200,
            proximity_raw=600,
            proximity_near=True,
            motion_score_pct=90,
            motion_active=True,
            accel_x=900,
        )

        actions, reasons = build_sensor_reaction_actions(busy, state, now_s=50.0)

        self.assertEqual(actions, [])
        self.assertEqual(reasons, [])

    def test_sensor_reaction_ignores_head_motion_status(self) -> None:
        state = SensorReactionState()
        moving = self.sensor_status(
            head_motion_active=True,
            proximity_delta=200,
            proximity_raw=600,
            proximity_near=True,
            motion_score_pct=90,
            motion_active=True,
            accel_x=900,
        )

        for source_hint in ("", "imu", "orientation", "proximity"):
            actions, reasons = build_sensor_reaction_actions(moving, state, now_s=50.0, source_hint=source_hint)
            self.assertEqual(actions, [])
            self.assertEqual(reasons, [])

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

    def test_audio_action_to_topic_payload_play_tts_url(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        topic, payload = action_to_topic_payload(
            pair,
            {"action": "audio", "audio_action": "play_tts_url", "url": "http://example.test/tts.wav"},
            "tts-001",
        )

        self.assertEqual(topic, "hermes-stackchan/desk/cmd/audio")
        self.assertEqual(payload["action"], "play_tts_url")
        self.assertEqual(payload["url"], "http://example.test/tts.wav")

    def test_sound_action_to_topic_payload_supports_safe_patterns(self) -> None:
        pair = load_config(Path("config/pairs.example.json"), env_path=None, environ={}).pairs["desk"]

        topic, payload = action_to_topic_payload(
            pair,
            {"action": "sound", "pattern": "question", "volume_pct": 65},
            "sound-001",
        )

        self.assertEqual(topic, "hermes-stackchan/desk/cmd/sound")
        self.assertEqual(payload["pattern"], "question")
        self.assertEqual(payload["frequency_hz"], 880)
        self.assertEqual(payload["duration_ms"], 140)
        self.assertEqual(payload["volume_pct"], 65)
        self.assertEqual(payload["request_id"], "sound-001")

    def test_reminder_builds_from_delay_and_fires_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(
                Path("config/pairs.example.json"),
                env_path=None,
                environ={"H2S_REMINDER_STORE": str(Path(tmpdir) / "reminders.json")},
            )
            pair = config.pairs["desk"]
            reminder = build_reminder(
                {"action": "reminder", "text": "Test trinken", "delay_s": 2},
                pair,
                "reminder-001",
                now_ts=1000,
            )
            self.assertEqual(reminder["due_ts"], 1002)

            add_reminder(config, reminder)
            self.assertEqual(len(pending_reminders(config, "desk")), 1)
            self.assertEqual(due_reminders(config, pair, now_ts=1001), [])
            fired = due_reminders(config, pair, now_ts=1003)
            self.assertEqual(fired[0]["text"], "Test trinken")
            self.assertEqual(pending_reminders(config, "desk"), [])

    def test_reminder_actions_wake_display_without_audio_path(self) -> None:
        actions = reminder_actions({"id": "rem-1", "text": "Wasser trinken"}, 7000)

        self.assertEqual(actions[0], {"action": "system", "system_action": "display_wake"})
        self.assertTrue(any(action["action"] == "display" and "Wasser trinken" in action["text"] for action in actions))
        self.assertNotIn("sound", {action["action"] for action in actions})
        self.assertNotIn("say", {action["action"] for action in actions})

    def test_schedule_reminders_filters_action_from_mqtt_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(
                Path("config/pairs.example.json"),
                env_path=None,
                environ={"H2S_REMINDER_STORE": str(Path(tmpdir) / "reminders.json")},
            )
            pair = config.pairs["desk"]
            dispatch, scheduled, errors = schedule_reminders_from_actions(
                config,
                pair,
                [
                    {"action": "say", "text": "Mache ich."},
                    {"action": "reminder", "text": "Kaffee", "delay_s": 120},
                ],
                "speech-reminder-test",
            )

            self.assertEqual(errors, [])
            self.assertEqual([action["action"] for action in dispatch], ["say"])
            self.assertEqual(scheduled[0]["text"], "Kaffee")

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
        side_touch = build_touch_lamp_payload({"event": "touch_down", "source": "head_touch_left"}, "touch-5")

        self.assertEqual(down, {"mode": "solid", "r": 0, "g": 255, "b": 0, "schema_version": "1.0", "request_id": "touch-1"})
        self.assertEqual(started, {"mode": "solid", "r": 0, "g": 255, "b": 0, "schema_version": "1.0", "request_id": "touch-2"})
        self.assertEqual(stopped, {"mode": "off", "r": 0, "g": 0, "b": 0, "schema_version": "1.0", "request_id": "touch-3"})
        self.assertIsNone(ignored)
        self.assertIsNone(side_touch)

    def test_side_touch_emotions_do_not_trigger_audio_or_led(self) -> None:
        state = TouchEmotionState()

        left_actions, left_reasons = build_touch_emotion_actions(
            {"event": "touch_down", "source": "head_touch_left"},
            state,
            now_s=10.0,
        )
        center_actions, center_reasons = build_touch_emotion_actions(
            {"event": "touch_down", "source": "head_touch"},
            state,
            now_s=10.2,
        )
        giggle_actions, giggle_reasons = build_touch_emotion_actions(
            {"event": "touch_down", "source": "head_touch_right"},
            state,
            now_s=10.6,
        )

        self.assertEqual(left_reasons, ["side_touch_left"])
        self.assertEqual(center_actions, [])
        self.assertEqual(center_reasons, [])
        self.assertEqual(giggle_reasons, ["side_touch_giggle"])
        forbidden = {"audio", "local_tts", "tts", "speak", "sound", "led"}
        for action in left_actions + giggle_actions:
            self.assertNotIn(action["action"], forbidden)
        self.assertTrue(any(action["action"] == "face" for action in left_actions))
        self.assertTrue(any(action.get("emotion") == "silent_giggle" for action in giggle_actions))

    def test_head_pet_swipe_uses_side_emotion_without_led_or_audio(self) -> None:
        state = TouchEmotionState()

        actions, reasons = build_touch_emotion_actions(
            {"event": "touch_swipe_forward", "source": "head_touch_right"},
            state,
            now_s=20.0,
        )

        self.assertEqual(reasons, ["head_pet_swipe_right"])
        forbidden = {"audio", "local_tts", "tts", "speak", "sound", "led"}
        for action in actions:
            self.assertNotIn(action["action"], forbidden)
        self.assertTrue(any(action["action"] == "face" for action in actions))
        self.assertTrue(any(action["action"] == "motion" for action in actions))

    def test_life_animation_only_runs_on_idle_face(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "head": {"motion_active": False},
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        self.assertTrue(status_allows_life_animation(status))
        status["head"]["motion_active"] = True
        self.assertFalse(status_allows_life_animation(status))
        status["head"]["motion_active"] = False
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

    def test_life_sequence_uses_only_template_idle_variants(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        seen = {
            action["variant"]
            for seed in range(200)
            for _delay, action in build_life_sequence(status, random.Random(seed))
            if "variant" in action
        }

        self.assertGreaterEqual(len(seen), 5)
        self.assertTrue(all(variant.startswith("template_") for variant in seen))

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
            if any(action["action"] == "face" and action["emotion"] == "soft_blink" for _delay, action in sequence)
        ]

        self.assertGreaterEqual(len(blink_sequences), 48)

    def test_life_sequence_has_no_long_idle_face_gap(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        for seed in range(500):
            sequence = build_life_sequence(status, random.Random(seed), include_motion=False)
            for delay_ms, action in sequence:
                if action["action"] == "face":
                    self.assertLessEqual(delay_ms, 1200)

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

    def test_life_sequence_uses_brow_impulses(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        emotions = {
            action["emotion"]
            for seed in range(120)
            for _delay, action in build_life_sequence(status, random.Random(seed), include_motion=False)
            if action["action"] == "face"
        }

        self.assertTrue(any(emotion.startswith("brow_") for emotion in emotions))

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

    def test_life_sequence_does_not_use_old_gag_faces(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }
        forbidden = {
            "cross_eyes", "eye_swap", "derp", "boing_eyes", "suspicious_squint",
            "confused_dots", "mouth_pop", "smirk_slide", "silent_giggle", "sleepy_snapback",
            "happy_squint", "surprise_pop", "micro_sleep", "glitch", "dead",
        }

        emotions = {
            action["emotion"]
            for seed in range(300)
            for _delay, action in build_life_sequence(status, random.Random(seed), include_motion=False)
            if action["action"] == "face"
        }

        self.assertFalse(emotions.intersection(forbidden))

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

    def test_life_sequence_has_rare_big_template_scan_motion(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        motions = [
            (delay, action)
            for seed in range(1000)
            for delay, action in build_life_sequence(status, random.Random(seed))
            if action["action"] == "motion"
        ]
        big_horizontal = [
            (delay, motion)
            for delay, motion in motions
            if any(abs(point["yaw_pct"]) >= 60 for point in motion["points"])
        ]
        template_scans = [
            motion
            for _delay, motion in motions
            if motion.get("variant") == "template_big_scan"
        ]
        vertical_motion = [
            (delay, motion)
            for delay, motion in motions
            if any(abs(point["pitch_pct"] - DEFAULT_IDLE_PITCH_PCT) >= 4 for point in motion["points"])
        ]
        big_faces = [
            (delay, action)
            for seed in range(1000)
            for delay, action in build_life_sequence(status, random.Random(seed))
            if action["action"] == "face" and action["emotion"] in {"glance_left", "glance_right", "glance_up", "glance_down"}
        ]

        self.assertTrue(big_horizontal)
        self.assertTrue(template_scans)
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

    def test_life_motion_limiter_rates_small_and_big_motion(self) -> None:
        small = {
            "action": "motion",
            "variant": "gen_head_left_0",
            "points": [
                {"yaw_pct": -5, "pitch_pct": DEFAULT_IDLE_PITCH_PCT + 2, "duration_ms": 1200},
                {"yaw_pct": DEFAULT_IDLE_YAW_PCT, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 1200},
            ],
        }
        big = {
            "action": "motion",
            "variant": "desk_spin",
            "points": [
                {"yaw_pct": 65, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 500},
                {"yaw_pct": DEFAULT_IDLE_YAW_PCT, "pitch_pct": DEFAULT_IDLE_PITCH_PCT, "duration_ms": 500},
            ],
        }
        limiter = LifeMotionLimiter(small_gap_s=20.0, big_gap_s=120.0)

        self.assertEqual(life_motion_size(small), "small")
        self.assertEqual(life_motion_size(big), "big")
        self.assertTrue(limiter.allow(small, now_s=100.0))
        self.assertFalse(limiter.allow(small, now_s=119.0))
        self.assertTrue(limiter.allow(small, now_s=120.0))
        self.assertTrue(limiter.allow(big, now_s=130.0))
        self.assertFalse(limiter.allow(big, now_s=249.0))
        self.assertTrue(limiter.allow(big, now_s=250.0))

    def test_life_sequence_pairs_motion_with_directional_faces(self) -> None:
        status = {
            "display_sleeping": False,
            "recording": False,
            "speaking": False,
            "ui": {"mode": "face"},
            "face": {"emotion": "neutral", "intensity_pct": 60},
        }

        paired_sequences = []
        for seed in range(400):
            sequence = build_life_sequence(status, random.Random(seed))
            has_directional_face = any(
                action["action"] == "face" and action["emotion"] in {"glance_left", "glance_right", "glance_up", "glance_down"}
                for _delay, action in sequence
            )
            has_motion = any(
                action["action"] == "motion"
                for _delay, action in sequence
            )
            if has_directional_face and has_motion:
                paired_sequences.append(sequence)

        self.assertTrue(paired_sequences)

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

    def test_life_question_or_error_face_is_never_final_state(self) -> None:
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
            self.assertNotIn(face_actions[-1]["emotion"], {"question", "error"})


if __name__ == "__main__":
    unittest.main()
