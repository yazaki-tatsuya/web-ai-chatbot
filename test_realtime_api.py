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


class FeedbackDisplayTestCase(unittest.TestCase):
    SECTIONS = {
        "better_questions": "次に確認すべき質問",
        "key_moments": "重要局面レビュー",
        "model_answer": "模範会話例",
        "alt_phrasings": "言い換え例",
        "next_drill": "次の1本ノック",
    }

    def render_feedback(self, feedback):
        with webui.app.test_request_context():
            return webui.render_template(
                "feedback.html",
                session={"id": "test-session", "scenario_title": "テスト"},
                transcript=None,
                feedback=feedback,
            )

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
