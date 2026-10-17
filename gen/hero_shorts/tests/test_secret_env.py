"""gen/hero_shorts/tests/test_secret_env.py — 시크릿 단일 해석 지점 + 하드코딩 회귀 가드

2026-10-18 마스터 지시(소스 하드코딩 전량 삭제 → 시크릿 주입)의 이행 증명.
가드 테스트는 소스 트리를 직접 스캔하므로, 향후 누구든 계정 ID/PII 를 소스에 다시
하드코딩하면 이 테스트가 실패한다.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gen.hero_shorts import pipeline, secret_env as se

ROOT = Path(__file__).resolve().parents[3]
SCAN_SUFFIXES = {".py", ".sh", ".yml", ".yaml", ".md"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
ID24 = re.compile(r"\b[0-9a-f]{24}\b")

WORKFLOW = ".github/workflows/hero_shorts_gen.yml"
SHELL = "gen/run_zernio_publish.sh"
RUNBOOK = "gen/hero_shorts/docs/YOUTUBE_PUBLISH.md"


def _is_test_path(rel: str) -> bool:
    return "/tests/" in ("/" + rel) or Path(rel).name.startswith("test_")


def _prod_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if _is_test_path(rel):
            continue
        yield rel, path


class HardcodingGuardTests(unittest.TestCase):
    """소스 트리 회귀 가드 — 계정 ID·PII 재유입 차단."""

    def test_no_account_id_literal_in_production_tree(self):
        hits = []
        for rel, path in _prod_files():
            for lineno, line in enumerate(path.read_text(encoding="utf-8",
                                                         errors="replace").splitlines(), 1):
                if ID24.search(line):
                    hits.append(f"{rel}:{lineno}")
        self.assertEqual(hits, [], f"운영 소스에 24자리 계정 ID 리터럴 재유입: {hits}")

    def test_workflow_binds_secret_refs_not_literals(self):
        text = (ROOT / WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("secrets.HERO_YOUTUBE_ACCOUNT_ID", text)
        self.assertIn("secrets.HERO_ZERNIO_ACCOUNT_ID", text)
        self.assertNotRegex(text, r"HERO_YOUTUBE_ACCOUNT_ID:\s*\"")
        self.assertIsNone(ID24.search(text), "워크플로에 계정 ID 리터럴 잔존")

    def test_workflow_preflight_never_prints_secret(self):
        text = (ROOT / WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("preflight_report", text)
        self.assertNotIn("_resolve_publish_opts(platform='youtube')['account_id']", text)

    def test_shell_has_no_account_id_fallback(self):
        text = (ROOT / SHELL).read_text(encoding="utf-8")
        # 허용되는 것은 빈 기본값(미설정 감지)뿐 — 값이 있는 폴백은 금지.
        self.assertNotRegex(text, r"HERO_YOUTUBE_ACCOUNT_ID:-[^}]")
        self.assertIn("exit 78", text)

    def test_runbook_has_no_account_id_literal(self):
        self.assertIsNone(ID24.search((ROOT / RUNBOOK).read_text(encoding="utf-8")))

    def test_email_envs_have_no_source_fallback(self):
        for rel in ("gen/hero_shorts/pipeline.py", "gen/hero_shorts/publish_runtime.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotRegex(text, r'HERO_(?:ALERT_EMAIL|FROM_ACCOUNT)",\s*"',
                                f"{rel} 에 메일 소스 기본값 잔존")


class SecretResolutionTests(unittest.TestCase):

    def test_missing_secret_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(se.SecretMissingError):
                se.resolve_required("HERO_YOUTUBE_ACCOUNT_ID", required=True)
            self.assertIsNone(se.resolve_account_id("youtube"))
            self.assertIsNone(se.resolve_email("HERO_ALERT_EMAIL"))

    def test_platform_isolation(self):
        # YouTube 는 Instagram 계정으로 폴백하지 않는다.
        with patch.dict(os.environ, {"HERO_ZERNIO_ACCOUNT_ID": "ig-account"}, clear=True):
            self.assertIsNone(se.resolve_account_id("youtube"))
            self.assertEqual(se.resolve_account_id("instagram"), "ig-account")
        with patch.dict(os.environ, {"HERO_YOUTUBE_ACCOUNT_ID": "yt-account"}, clear=True):
            self.assertEqual(se.resolve_account_id("youtube"), "yt-account")
            self.assertIsNone(se.resolve_account_id("instagram"))

    def test_cli_argument_beats_environment(self):
        with patch.dict(os.environ, {"HERO_YOUTUBE_ACCOUNT_ID": "from-env"}, clear=True):
            self.assertEqual(se.resolve_account_id("youtube", cli_value="from-cli"), "from-cli")

    def test_unsupported_platform_rejected(self):
        with self.assertRaises(se.SecretInvalidError):
            se.resolve_account_id("myspace")

    def test_whitespace_control_and_overlong_rejected(self):
        for bad in (" has-space", "has-space ", "line\nbreak", "nul\x00byte", "x" * 300, "   "):
            with self.subTest(bad=repr(bad)[:24]), self.assertRaises((se.SecretInvalidError,
                                                                      se.SecretMissingError)):
                se.validate_secret_value("HERO_YOUTUBE_ACCOUNT_ID", bad)

    def test_placeholder_values_rejected(self):
        for bad in ("changeme", "PLACEHOLDER", "your-account-id", "<set-me>", "xxx", "****"):
            with self.subTest(bad=bad), self.assertRaises(se.SecretInvalidError):
                se.validate_secret_value("HERO_YOUTUBE_ACCOUNT_ID", bad)

    def test_strict_mode_requires_24_hex(self):
        with self.assertRaises(se.SecretInvalidError):
            se.validate_secret_value("X", "not-hex", strict=True)
        self.assertEqual(se.validate_secret_value("X", "a" * 24, strict=True), "a" * 24)
        # 기본은 관대 모드 — 실제 Zernio ID 형식 변경에 견딘다.
        self.assertEqual(se.validate_secret_value("X", "yt-account", strict=False), "yt-account")

    def test_email_format_validated(self):
        self.assertEqual(se.resolve_email("HERO_ALERT_EMAIL", cli_value="a@b.co"), "a@b.co")
        with self.assertRaises(se.SecretInvalidError):
            se.resolve_email("HERO_ALERT_EMAIL", cli_value="not-an-email")

    def test_redact_and_scrub_hide_value(self):
        secret = "abcdef0123456789abcdef01"
        with patch.dict(os.environ, {"HERO_YOUTUBE_ACCOUNT_ID": secret}, clear=True):
            resolved = se.resolve_account_id("youtube")
        self.assertEqual(resolved, secret)
        self.assertNotIn(secret, se.redact(resolved))
        self.assertNotIn(secret, se.scrub(f"발행 실패: account={secret}"))
        self.assertIn("***", se.scrub(f"발행 실패: account={secret}"))

    def test_preflight_report_contains_no_value(self):
        secret = "abcdef0123456789abcdef01"
        with patch.dict(os.environ, {"HERO_YOUTUBE_ACCOUNT_ID": secret}, clear=True):
            report = se.preflight_report("youtube")
        self.assertTrue(report["configured"])
        self.assertTrue(report["valid"])
        self.assertFalse(report["value_logged"])
        self.assertNotIn(secret, repr(report))

    def test_preflight_report_flags_invalid_without_leaking(self):
        with patch.dict(os.environ, {"HERO_YOUTUBE_ACCOUNT_ID": "changeme"}, clear=True):
            report = se.preflight_report("youtube")
        self.assertTrue(report["configured"])
        self.assertFalse(report["valid"])
        self.assertNotIn("changeme", repr(report))

    def test_pipeline_resolution_has_no_source_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(pipeline._resolve_publish_opts()["account_id"])
            self.assertIsNone(pipeline.DEFAULT_ALERT_EMAIL)
            self.assertIsNone(pipeline.DEFAULT_FROM_ACCOUNT)
        with patch.dict(os.environ, {"HERO_YOUTUBE_ACCOUNT_ID": "env-account"}, clear=True):
            self.assertEqual(pipeline._resolve_publish_opts()["account_id"], "env-account")


class ShellFailClosedTests(unittest.TestCase):

    def test_shell_exits_78_without_youtube_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["bash", str(ROOT / SHELL), "101", "plan.json", "caption.txt",
                 "https://example.com/v.mp4"],
                env={"PATH": os.defpath}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
        self.assertIn("HERO_YOUTUBE_ACCOUNT_ID", result.stderr)


if __name__ == "__main__":
    unittest.main()
