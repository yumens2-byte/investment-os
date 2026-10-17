import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gen.hero_shorts import pipeline, publish_runtime as runtime, assemble_qc


class YouTubePublishTests(unittest.TestCase):
    def test_default_account_is_youtube_even_with_instagram_env(self):
        with patch.dict(os.environ, {"HERO_ZERNIO_ACCOUNT_ID": "instagram"}, clear=True):
            self.assertEqual(pipeline._resolve_publish_opts()["account_id"], "6ad38f1d80b2fa2e1133f591")
            self.assertEqual(pipeline._resolve_publish_opts(platform="instagram")["account_id"], "instagram")

    def test_youtube_account_override_and_cli_priority(self):
        with patch.dict(os.environ, {"HERO_YOUTUBE_ACCOUNT_ID": "youtube-override"}):
            self.assertEqual(pipeline._resolve_publish_opts()["account_id"], "youtube-override")
            self.assertEqual(pipeline._resolve_publish_opts(account_id="cli")["account_id"], "cli")

    def test_title_first_line_without_duplicate(self):
        self.assertEqual(runtime.youtube_text("제목", "설명"), "제목\n\n설명")
        self.assertEqual(runtime.youtube_text("제목", "제목\n\n설명"), "제목\n\n설명")

    def test_invalid_title_and_description_fail(self):
        for title, desc in [("", "x"), ("x" * 101, "x"), ("a\nb", "x"), ("t", "x" * 5000)]:
            with self.subTest(title=title[:10]), self.assertRaises(ValueError):
                runtime.youtube_text(title, desc)

    def test_youtube_status_uses_target_and_real_host(self):
        cases = [([], "pending"),
                 ([{"platform": "instagram", "status": "published", "url": "https://instagram.com/a"}], "pending"),
                 ([{"platform": "youtube", "status": "published", "url": "https://youtube.com.evil.test/a"}], "pending"),
                 ([{"platform": "youtube", "status": "published"}], "pending"),
                 ([{"platform": "youtube", "status": "pending", "url": "https://youtu.be/a"}], "pending"),
                 ([{"platform": "youtube", "status": "failed"}], "failed"),
                 ([{"platform": "youtube", "status": "published", "url": "https://www.youtube.com/watch?v=a"}], "published")]
        for platforms, expected in cases:
            with self.subTest(platforms=platforms):
                self.assertEqual(runtime._normalize_status({"status": "published", "platforms": platforms}, "youtube")[0], expected)

    def test_approved_publish_and_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.json"
            caption = root / "caption.txt"
            statefile = root / "state.json"
            plan.write_text(json.dumps({"episode": 101, "title": "제목", "type": "battle"}))
            caption.write_text("설명", encoding="utf-8")
            qc = {"G1": True, "G2": True, "G3": True, "G4": True}
            initial = {"ep101": {"approval": {"status": "APPROVED"}, "combined": {"duration_sec": 70}}}
            statefile.write_text(json.dumps(initial))
            result = {"outcome": "published", "post_id": "yt-post", "post_url": "https://youtu.be/a",
                      "snapshot": {"platforms": [{"platform": "instagram", "publishedAt": "wrong"},
                                                 {"platform": "youtube", "publishedAt": "correct"}]}}
            with patch.dict(os.environ, {"HERO_STATE_FILE": str(statefile)}, clear=True), \
                    patch.object(assemble_qc, "validate_publish_assets"), \
                    patch.object(assemble_qc, "check_media_url_reachable"), \
                    patch.object(runtime, "create_and_monitor_post", return_value=result) as create:
                kwargs = dict(plan_file=str(plan), final_video="video.mp4", caption_file=str(caption),
                              media_url="https://example.com/video.mp4", qc_result=qc)
                pipeline.cmd_zernio_publish(**kwargs)
                self.assertEqual(create.call_args.kwargs["platform"], "youtube")
                self.assertEqual(create.call_args.kwargs["account_id"], "6ad38f1d80b2fa2e1133f591")
                self.assertEqual(create.call_args.kwargs["text"], "제목\n\n설명")
                stored = json.loads(statefile.read_text())["ep101"]
                self.assertEqual(stored["channel"], "youtube")
                self.assertEqual(stored["published_at"], "correct")
                self.assertEqual(stored["combined"]["duration_sec"], 70)
                with self.assertRaisesRegex(RuntimeError, "중복 발행"):
                    pipeline.cmd_zernio_publish(**kwargs)
                self.assertEqual(create.call_count, 1)
                statefile.write_text(json.dumps({"ep101": {"approval": {"status": "PENDING"}}}))
                with self.assertRaisesRegex(RuntimeError, "승인 게이트"):
                    pipeline.cmd_zernio_publish(**kwargs)
                with self.assertRaisesRegex(RuntimeError, "QC 미통과"):
                    pipeline.cmd_zernio_publish(**{**kwargs, "qc_result": {}})
                with self.assertRaisesRegex(RuntimeError, "TIKTOK_PUBLISH_SUSPENDED"):
                    pipeline.cmd_zernio_publish(**kwargs, platform="tiktok")
                self.assertEqual(create.call_count, 1)

    def test_shell_default_and_combined_route_to_youtube(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "python3"
            fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o755)
            env = {"PATH": tmp + os.pathsep + os.defpath}
            for combined, args in [("0", ["101", "plan.json", "caption.txt", "https://example.com/v.mp4"]),
                                   ("1", ["ep101", "video.mp4", "제목", "https://example.com/v.mp4"])]:
                with self.subTest(combined=combined):
                    result = subprocess.run(["bash", str(root / "gen/run_zernio_publish.sh"), *args],
                                            env={**env, "HERO_COMBINED": combined}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    tokens = result.stdout.splitlines()
                    self.assertEqual(tokens[tokens.index("--platform") + 1], "youtube")
                    self.assertEqual(tokens[tokens.index("--account-id") + 1], "6ad38f1d80b2fa2e1133f591")
                    self.assertIn("zernio_publish", tokens)
                    self.assertIn("--require-audible-audio", tokens)


if __name__ == "__main__":
    unittest.main()
