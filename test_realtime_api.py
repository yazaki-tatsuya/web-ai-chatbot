import json
import os
import unittest
from unittest.mock import Mock, patch

import test_OpenAI_WebUI as webui


class RealtimeApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = webui.app.test_client()
        self.auth_patch = patch.object(webui, "_auth_enabled", return_value=False)
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()

    def test_sdp_proxy_uses_unified_calls_endpoint(self):
        upstream = Mock()
        upstream.ok = True
        upstream.text = "v=0\r\n"
        upstream.status_code = 201
        upstream.headers = {"Content-Type": "application/sdp"}

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch("requests.post", return_value=upstream) as post:
            response = self.client.post(
                "/realtime/sdp-proxy",
                data="v=0\r\n",
                content_type="application/sdp",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.mimetype, "application/sdp")
        self.assertEqual(response.get_data(as_text=True), "v=0\r\n")
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/realtime/calls")

        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-key"})
        self.assertEqual(kwargs["files"]["sdp"], (None, "v=0\r\n", "application/sdp"))
        session_config = json.loads(kwargs["files"]["session"][1])
        self.assertEqual(session_config, {
            "type": "realtime",
            "model": webui.REALTIME_MODEL,
            "output_modalities": ["audio"],
            "audio": {"output": {"voice": webui.REALTIME_VOICE}},
        })

    def test_flask_secret_key_comes_from_environment(self):
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "configured-secret"}):
            self.assertEqual(webui._load_flask_secret_key(), "configured-secret")

    def test_flask_secret_key_warns_when_not_configured(self):
        env = {k: v for k, v in os.environ.items() if k != "FLASK_SECRET_KEY"}
        with patch.dict(os.environ, env, clear=True), self.assertWarns(RuntimeWarning):
            secret_key = webui._load_flask_secret_key()

        self.assertEqual(secret_key, "local-dev-secret-change-me")

    def test_sdp_proxy_rejects_missing_key(self):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPEN_AI_KEY")}
        with patch.dict(os.environ, env, clear=True):
            response = self.client.post(
                "/realtime/sdp-proxy",
                data="v=0\r\n",
                content_type="application/sdp",
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn("not configured", response.get_json()["error"])

    def test_sdp_proxy_rejects_empty_offer(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            response = self.client.post(
                "/realtime/sdp-proxy",
                data="",
                content_type="application/sdp",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "SDP offer is empty")


class LoginRedirectTestCase(unittest.TestCase):
    SAFE_PATHS = (
        "/history",
        "/feedback/test-session?tab=summary#details",
        "/modes?mode=basic",
    )
    UNSAFE_PATHS = (
        "https://example.com/phishing",
        "http://example.com",
        "//example.com/phishing",
        "\\\\example.com\\phishing",
        "/\\example.com/phishing",
        "javascript:alert(1)",
        "history",
        "/history\r\nLocation: https://example.com",
    )

    def setUp(self):
        self.client = webui.app.test_client()

    def test_safe_next_paths_are_preserved_after_successful_login(self):
        for next_url in self.SAFE_PATHS:
            with self.subTest(next_url=next_url), \
                    patch.object(webui, "_auth_enabled", return_value=True), \
                    patch.dict(os.environ, {"APP_PIN": "1234"}):
                response = self.client.post("/login", data={"pin": "1234", "next": next_url})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], next_url)

    def test_unsafe_next_values_fall_back_to_home_after_successful_login(self):
        for next_url in self.UNSAFE_PATHS:
            with self.subTest(next_url=next_url), \
                    patch.object(webui, "_auth_enabled", return_value=True), \
                    patch.dict(os.environ, {"APP_PIN": "1234"}):
                response = self.client.post("/login", data={"pin": "1234", "next": next_url})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], "/home")

    def test_auth_disabled_still_restricts_next_to_local_paths(self):
        with patch.object(webui, "_auth_enabled", return_value=False):
            safe = self.client.get("/login?next=/history")
            unsafe = self.client.get("/login?next=https://example.com")
        self.assertEqual(safe.headers["Location"], "/history")
        self.assertEqual(unsafe.headers["Location"], "/home")

    def test_login_form_contains_only_sanitized_and_escaped_next_value(self):
        with patch.object(webui, "_auth_enabled", return_value=True):
            safe = self.client.get("/login?next=/feedback/test%3Ftab%3D%27summary%27")
            unsafe = self.client.get("/login?next=https://example.com/%27%3E%3Cscript%3E")
        safe_html = safe.get_data(as_text=True)
        unsafe_html = unsafe.get_data(as_text=True)
        self.assertIn("value='/feedback/test?tab=&#x27;summary&#x27;'", safe_html)
        self.assertNotIn("<script>", safe_html)
        self.assertIn("value='/home'", unsafe_html)
        self.assertNotIn("example.com", unsafe_html)

    def test_wrong_pin_does_not_redirect_or_authorize(self):
        with patch.object(webui, "_auth_enabled", return_value=True), \
                patch.dict(os.environ, {"APP_PIN": "1234"}):
            response = self.client.post(
                "/login", data={"pin": "wrong", "next": "https://example.com"}
            )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Location", response.headers)
        with self.client.session_transaction() as session_data:
            self.assertIsNot(session_data.get("authorized"), True)


class SessionStartTestCase(unittest.TestCase):
    def setUp(self):
        self.client = webui.app.test_client()
        self.auth_patch = patch.object(webui, "_auth_enabled", return_value=False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.scenario = {
            "id": "free_talk",
            "mode": "basic",
            "title": "フリートーク",
            "default_instructions": "既定の指示",
        }

    def test_known_scenario_with_blank_override_creates_session(self):
        meta = Mock(session_id="new-session")
        with patch.object(webui.store, "find_scenario", return_value=self.scenario), \
                patch.object(webui.store, "create_session", return_value=meta) as create:
            response = self.client.post(
                "/session/start",
                data={"scenario_id": " free_talk ", "instructions": "   "},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/?session_id=new-session")
        create.assert_called_once_with("free_talk", None)

    def test_known_scenario_preserves_trimmed_instruction_override(self):
        meta = Mock(session_id="new-session")
        with patch.object(webui.store, "find_scenario", return_value=self.scenario), \
                patch.object(webui.store, "create_session", return_value=meta) as create:
            response = self.client.post(
                "/session/start",
                data={"scenario_id": "free_talk", "instructions": "  短く答える  "},
            )
        self.assertEqual(response.status_code, 302)
        create.assert_called_once_with("free_talk", "短く答える")

    def test_missing_or_unknown_scenario_returns_400_without_creating_session(self):
        cases = [
            ({}, "scenario_id is required"),
            ({"scenario_id": "   "}, "scenario_id is required"),
            ({"scenario_id": "missing"}, "scenario not found"),
        ]
        for form, expected_error in cases:
            with self.subTest(form=form), \
                    patch.object(webui.store, "find_scenario", return_value=None), \
                    patch.object(webui.store, "create_session") as create:
                response = self.client.post("/session/start", data=form)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), {"ok": False, "error": expected_error})
                create.assert_not_called()

    def test_oversized_instructions_are_rejected_without_creating_session(self):
        with patch.object(webui.store, "find_scenario", return_value=self.scenario), \
                patch.object(webui.store, "create_session") as create:
            response = self.client.post(
                "/session/start",
                data={
                    "scenario_id": "free_talk",
                    "instructions": "x" * (webui.MAX_INSTRUCTIONS_LENGTH + 1),
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn(str(webui.MAX_INSTRUCTIONS_LENGTH), response.get_json()["error"])
        create.assert_not_called()

    def test_pin_enabled_requires_authorization_before_creating_session(self):
        with patch.object(webui, "_auth_enabled", return_value=True), \
                patch.object(webui.store, "find_scenario") as find_scenario, \
                patch.object(webui.store, "create_session") as create:
            response = self.client.post(
                "/session/start", data={"scenario_id": "free_talk"}
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"], "unauthorized")
        self.assertIn("/login?next=/session/start", response.get_json()["login_url"])
        find_scenario.assert_not_called()
        create.assert_not_called()


class TranscriptApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = webui.app.test_client()
        self.auth_patch = patch.object(webui, "_auth_enabled", return_value=False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.meta = Mock(session_id="test-session")
        self.url = "/api/session/test-session/transcript"

    def post(self, payload):
        return self.client.post(self.url, json=payload)

    def test_valid_transcript_is_saved_without_removing_future_fields(self):
        payload = {
            "ended_at": 1730000000000,
            "transcript": [
                {"role": "user", "text": "こんにちは", "ts": 1729999999000,
                 "future_item_field": "kept"},
                {"role": "assistant", "text": "こんにちは。", "ts": 1730000000000},
            ],
            "future_payload_field": {"version": 2},
        }
        with patch.object(webui.store, "get_session", return_value=self.meta), \
                patch.object(webui.store, "save_transcript", return_value=True) as save:
            response = self.post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        save.assert_called_once_with("test-session", payload)

    def test_empty_transcript_is_allowed_for_sessions_ended_before_speaking(self):
        payload = {"ended_at": 1730000000000, "transcript": []}
        with patch.object(webui.store, "get_session", return_value=self.meta), \
                patch.object(webui.store, "save_transcript", return_value=True) as save:
            response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        save.assert_called_once_with("test-session", payload)

    def test_unknown_session_returns_404_without_saving(self):
        with patch.object(webui.store, "get_session", return_value=None), \
                patch.object(webui.store, "save_transcript") as save:
            response = self.post({"transcript": []})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"ok": False, "error": "session not found"})
        save.assert_not_called()

    def test_non_json_body_returns_400_without_saving(self):
        with patch.object(webui.store, "get_session", return_value=self.meta), \
                patch.object(webui.store, "save_transcript") as save:
            response = self.client.post(self.url, data="not json", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON object", response.get_json()["error"])
        save.assert_not_called()

    def test_invalid_payloads_return_400_without_saving(self):
        invalid_cases = [
            (None, "JSON object"),
            ([], "JSON object"),
            ({}, "transcript must be an array"),
            ({"transcript": {}}, "transcript must be an array"),
            ({"transcript": ["text"]}, "transcript[0] must be an object"),
            ({"transcript": [{"role": "system", "text": "x"}]}, "role"),
            ({"transcript": [{"role": "user", "text": 1}]}, "text"),
            ({"transcript": [{"role": "user", "text": "  "}]}, "text"),
            ({"transcript": [{"role": "user", "text": "x", "ts": -1}]}, "ts"),
            ({"transcript": [{"role": "user", "text": "x", "ts": True}]}, "ts"),
            ({"transcript": [], "ended_at": -1}, "ended_at"),
            ({"transcript": [], "ended_at": True}, "ended_at"),
        ]
        for payload, expected_error in invalid_cases:
            with self.subTest(payload=payload), \
                    patch.object(webui.store, "get_session", return_value=self.meta), \
                    patch.object(webui.store, "save_transcript") as save:
                response = self.post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["ok"])
                self.assertIn(expected_error, response.get_json()["error"])
                save.assert_not_called()

    def test_store_failure_returns_500(self):
        payload = {"transcript": [{"role": "user", "text": "こんにちは"}]}
        with patch.object(webui.store, "get_session", return_value=self.meta), \
                patch.object(webui.store, "save_transcript", return_value=False):
            response = self.post(payload)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"ok": False})


class FeedbackGenerationTestCase(unittest.TestCase):
    def setUp(self):
        self.client = webui.app.test_client()
        self.store = webui.SQLiteSessionStore(":memory:")
        self.addCleanup(self.store._conn.close)
        store_patch = patch.object(webui, "store", self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        auth_patch = patch.object(webui, "_auth_enabled", return_value=False)
        auth_patch.start()
        self.addCleanup(auth_patch.stop)
        self.meta = self.store.create_session("free_talk")
        self.transcript = [{"role": "user", "text": "こんにちは"}]
        self.store.save_transcript(self.meta.session_id, {"transcript": self.transcript})
        self.url = f"/api/session/{self.meta.session_id}/feedback/generate"

    def test_generation_failure_does_not_create_feedback(self):
        with patch.object(webui, "_generate_feedback_with_openai",
                          return_value={"error": "生成に失敗しました"}), \
                patch.object(self.store, "save_feedback", wraps=self.store.save_feedback) as save:
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json(), {"ok": False, "error": "生成に失敗しました"})
        save.assert_not_called()
        self.assertIsNone(self.store.get_feedback(self.meta.session_id))

    def test_failed_regeneration_preserves_existing_feedback_and_transcript(self):
        existing = {"summary": "保存済みの結果", "score": 80}
        self.store.save_feedback(self.meta.session_id, existing)
        with patch.object(webui, "_generate_feedback_with_openai",
                          return_value={"error": "再生成に失敗しました"}), \
                patch.object(self.store, "save_feedback", wraps=self.store.save_feedback) as save:
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["ok"])
        save.assert_not_called()
        self.assertEqual(self.store.get_feedback(self.meta.session_id), existing)
        self.assertEqual(self.store.get_transcript(self.meta.session_id),
                         {"transcript": self.transcript})

    def test_successful_retry_saves_normalized_feedback(self):
        generated = {"summary": "新しい結果", "score": 90, "good_points": ["簡潔"]}
        with patch.object(webui, "_generate_feedback_with_openai",
                          side_effect=[{"error": "一時的な生成失敗"}, generated]):
            failed = self.client.post(self.url)
            response = self.client.post(self.url)
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["feedback"], webui._normalize_feedback_payload(generated))
        self.assertEqual(self.store.get_feedback(self.meta.session_id), payload["feedback"])

    def test_successful_regeneration_replaces_existing_feedback(self):
        self.store.save_feedback(self.meta.session_id, {"summary": "以前の結果", "score": 80})
        with patch.object(webui, "_generate_feedback_with_openai",
                          return_value={"summary": "更新された結果", "score": 90}):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(self.store.get_feedback(self.meta.session_id),
                         response.get_json()["feedback"])
        self.assertEqual(self.store.get_feedback(self.meta.session_id)["summary"], "更新された結果")


class HistoryNavigationTestCase(unittest.TestCase):
    def test_home_link_returns_home_without_creating_session(self):
        client = webui.app.test_client()
        with patch.object(webui.store, "list_sessions", return_value=[]), \
                patch.object(webui.store, "create_session") as create_session:
            response = client.get("/history")
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/home" class="text-decoration-none">← Home',
                          response.get_data(as_text=True))
            home = client.get("/home")
            self.assertEqual(home.status_code, 200)
            self.assertIn("コミュニケーション練習", home.get_data(as_text=True))
            create_session.assert_not_called()


class ModeNavigationTestCase(unittest.TestCase):
    def test_home_link_returns_home_without_creating_session(self):
        client = webui.app.test_client()
        with patch.object(webui.store, "list_modes", return_value=["basic"]), \
                patch.object(webui.store, "list_scenarios", return_value=[]), \
                patch.object(webui.store, "create_session") as create_session:
            response = client.get("/modes")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('href="/home" class="text-decoration-none">← Home', html)
            self.assertNotIn('href="/" class="text-decoration-none">← Home', html)
            self.assertIn('href="/shelves?mode=basic"', html)
            home = client.get("/home")
            self.assertEqual(home.status_code, 200)
            self.assertIn("コミュニケーション練習", home.get_data(as_text=True))
            create_session.assert_not_called()


class ListEmptyStateTestCase(unittest.TestCase):
    def setUp(self):
        self.client = webui.app.test_client()

    def test_empty_history_shows_practice_link(self):
        with patch.object(webui.store, "list_sessions", return_value=[]):
            response = self.client.get("/history")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("練習履歴はまだありません。", html)
        self.assertIn('href="/modes">練習を始める</a>', html)

    def test_history_with_session_hides_empty_state(self):
        sessions = [{"id": "test-session", "title": "練習タイトル",
                     "created_at": 0, "mode": "basic"}]
        with patch.object(webui.store, "list_sessions", return_value=sessions):
            response = self.client.get("/history")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("練習履歴はまだありません。", html)
        self.assertIn("練習タイトル", html)
        self.assertIn('href="/practice/test-session"', html)
        self.assertIn('href="/feedback/test-session"', html)

    def test_empty_feedbacks_shows_history_link(self):
        with patch.object(webui.store, "list_feedback_sessions", return_value=[]):
            response = self.client.get("/feedbacks")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        body_before_script = html.split("<script>", 1)[0]
        self.assertIn("生成済みのフィードバックはありません。", html)
        self.assertIn('href="/history">履歴からフィードバックを生成する</a>', html)
        self.assertNotIn('data-sid=', body_before_script)

    def test_feedbacks_with_item_hides_empty_state(self):
        items = [("test-session", "練習タイトル", 0, "basic")]
        with patch.object(webui.store, "list_feedback_sessions", return_value=items):
            response = self.client.get("/feedbacks")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("生成済みのフィードバックはありません。", html)
        self.assertIn("練習タイトル", html)
        self.assertIn('href="/feedback/test-session"', html)
        self.assertIn('data-sid="test-session"', html)


class FeedbackPageTestCase(unittest.TestCase):
    def setUp(self):
        self.client = webui.app.test_client()

    def test_unknown_session_returns_helpful_404_without_loading_related_data(self):
        with patch.object(webui.store, "get_session", return_value=None), \
                patch.object(webui.store, "get_transcript") as get_transcript, \
                patch.object(webui.store, "get_feedback") as get_feedback:
            response = self.client.get("/feedback/missing-session")

        self.assertEqual(response.status_code, 404)
        html = response.get_data(as_text=True)
        self.assertIn("セッションが見つかりません", html)
        self.assertIn("missing-session", html)
        self.assertIn('href="/history">履歴を確認する</a>', html)
        self.assertIn('href="/modes">新しい練習を始める</a>', html)
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', html)
        self.assertNotIn("フィードバック結果", html)
        get_transcript.assert_not_called()
        get_feedback.assert_not_called()

    def test_existing_session_still_displays_feedback_page(self):
        meta = Mock(session_id="test-session", title="テストシナリオ")
        transcript = {"transcript": [{"role": "user", "text": "こんにちは"}]}
        feedback = {"summary": "保存済みの結果", "score": 80}
        with patch.object(webui.store, "get_session", return_value=meta), \
                patch.object(webui.store, "get_transcript", return_value=transcript), \
                patch.object(webui.store, "get_feedback", return_value=feedback):
            response = self.client.get("/feedback/test-session")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("フィードバック結果", html)
        self.assertIn("保存済みの結果", html)
        self.assertIn("テストシナリオ", html)
        self.assertNotIn("セッションが見つかりません", html)


class FeedbackDisplayTestCase(unittest.TestCase):
    SECTIONS = {
        "better_questions": "次に確認すべき質問",
        "key_moments": "重要局面レビュー",
        "model_answer": "模範会話例",
        "alt_phrasings": "言い換え例",
        "next_drill": "次の1本ノック",
    }

    def render_feedback(self, feedback, transcript=None):
        with webui.app.test_request_context():
            return webui.render_template(
                "feedback.html",
                session={"id": "test-session", "scenario_title": "テスト"},
                transcript=transcript,
                feedback=feedback,
            )

    def test_current_feedback_heading_is_displayed(self):
        html = self.render_feedback({"summary": "要約", "score": None})
        self.assertIn("フィードバック結果", html)
        self.assertNotIn("まとめ（仮）", html)

    def test_empty_feedback_guides_user_to_generate_when_log_exists(self):
        html = self.render_feedback(
            None,
            transcript={"transcript": [{"role": "user", "text": "こんにちは"}]},
        )
        self.assertIn("「フィードバック生成」ボタンから生成できます", html)
        self.assertNotIn("次ステップでOpenAI評価に置き換えます", html)

    def test_empty_feedback_guides_user_to_save_when_log_is_missing(self):
        html = self.render_feedback(None)
        self.assertIn("先に練習を終了して会話ログを保存してください", html)
        self.assertNotIn("次ステップでOpenAI評価に置き換えます", html)

    def test_sections_display_independently_without_improvements(self):
        for improvements in ([], None):
            for field, heading in self.SECTIONS.items():
                with self.subTest(improvements=improvements, field=field):
                    feedback = {"score": None}
                    if improvements is not None:
                        feedback["improvements"] = improvements
                    text = "この項目のテスト内容"
                    feedback[field] = text if field in (
                        "model_answer", "next_drill"
                    ) else [text]
                    html = self.render_feedback(feedback)
                    self.assertIn(heading, html)
                    self.assertIn(text, html)
                    self.assertNotIn('class="fw-semibold">改善点', html)
                    for other_field, other_heading in self.SECTIONS.items():
                        if other_field != field:
                            self.assertNotIn(other_heading, html)

    def test_improvements_and_other_sections_display_together(self):
        feedback = {
            "score": None,
            "improvements": ["結論から話す"],
            "better_questions": ["期限はいつですか"],
            "model_answer": "結論から説明します",
        }
        html = self.render_feedback(feedback)
        for text in (
            "改善点", "結論から話す", "次に確認すべき質問",
            "期限はいつですか", "模範会話例", "結論から説明します",
        ):
            self.assertIn(text, html)

    def test_empty_sections_are_not_displayed(self):
        feedback = {"score": None, "summary": "要約のみ", "improvements": []}
        feedback.update({
            field: "" if field in ("model_answer", "next_drill") else []
            for field in self.SECTIONS
        })
        html = self.render_feedback(feedback)
        self.assertIn("要約のみ", html)
        for heading in self.SECTIONS.values():
            self.assertNotIn(heading, html)
        self.assertNotIn('class="fw-semibold">改善点', html)


if __name__ == "__main__":
    unittest.main()
