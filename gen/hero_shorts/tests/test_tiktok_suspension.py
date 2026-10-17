"""TikTok suspension: zero external calls, Instagram continues through mocks."""
import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gen.hero_shorts import publish_runtime as runtime
from gen.hero_shorts.tiktok import publish, runner


class TikTokSuspensionTests(unittest.TestCase):
    def test_segment_blocked_before_validation_or_preflight(self):
        with patch.object(publish.tk_account, "assert_token_ready") as preflight, \
                patch.object(runtime, "create_and_monitor_post") as create:
            with self.assertRaisesRegex(RuntimeError, "TIKTOK_PUBLISH_SUSPENDED"):
                publish.publish_segment({}, preflight=False, fallback_media_url="https://example.com/v.mp4")
            preflight.assert_not_called()
            create.assert_not_called()

    def test_platform_blocked_before_create(self):
        with patch.object(runtime, "create_post") as create:
            with self.assertRaisesRegex(RuntimeError, "TIKTOK_PUBLISH_SUSPENDED"):
                runtime.create_and_monitor_post("caption", "url", "account", platform=" TikTok ")
            create.assert_not_called()

    def test_direct_tiktok_options_blocked_before_external_call(self):
        for option, value in [("tiktok_draft", False), ("tiktok_privacy_level", "PUBLIC_TO_EVERYONE"),
                              ("tiktok_allow_comments", False), ("tiktok_allow_duet", True),
                              ("tiktok_allow_stitch", True)]:
            with self.subTest(option=option), patch.object(runtime, "_run_json") as external:
                with self.assertRaisesRegex(RuntimeError, "TIKTOK_PUBLISH_SUSPENDED"):
                    runtime.create_post("caption", "url", "account", **{option: value})
                external.assert_not_called()

    def test_configured_tiktok_account_blocked_without_platform(self):
        with patch.dict(os.environ, {"HERO_TIKTOK_ACCOUNT_ID": "tk-account"}), \
                patch.object(runtime, "_run_json") as external:
            with self.assertRaisesRegex(RuntimeError, "TIKTOK_PUBLISH_SUSPENDED"):
                runtime.create_post("caption", "url", "tk-account")
            external.assert_not_called()

    def test_instagram_path_still_creates_and_polls(self):
        with patch.dict(os.environ, {"HERO_TIKTOK_ACCOUNT_ID": "tk-account"}), \
                patch.object(runtime, "_run_json", return_value={"data": {"post": {"_id": "ig-post"}}}) as external, \
                patch.object(runtime, "poll_post", return_value={"outcome": "published"}) as poll, \
                patch.object(runtime, "maybe_alert"):
            result = runtime.create_and_monitor_post("caption", "url", "ig-account", platform="instagram")
            self.assertEqual(result["outcome"], "published")
            external.assert_called_once()
            self.assertEqual(poll.call_args.kwargs["platform"], "instagram")

    def test_ready_returns_empty_without_reading_or_restoring_ledger(self):
        output = io.StringIO()
        with patch.object(runner, "load_publish_state") as load, contextlib.redirect_stdout(output):
            code = runner.cmd_ready(SimpleNamespace(state="/nonexistent/publish_state.json"))
        self.assertEqual(code, runner.READY_EXIT_NONE)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "suspended")
        self.assertEqual(payload["items"], [])
        load.assert_not_called()

    def test_shell_skips_tiktok_and_combined_without_arguments(self):
        root = Path(__file__).resolve().parents[3]
        script = root / "gen/run_zernio_publish.sh"
        for platform, combined in [("tiktok", "0"), ("TikTok", "0"), ("instagram", "1")]:
            with self.subTest(platform=platform, combined=combined):
                env = {**os.environ, "HERO_ZERNIO_PLATFORM": platform, "HERO_COMBINED": combined}
                result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("TIKTOK_PUBLISH_SUSPENDED", result.stdout)


if __name__ == "__main__":
    unittest.main()
