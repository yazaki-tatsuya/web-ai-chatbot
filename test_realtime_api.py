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


if __name__ == "__main__":
    unittest.main()