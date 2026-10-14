"""러너3 `ready` 판정 회귀 테스트 (v2.9.0 — 2026-10-15 설계 C-3).

검증 대상:
  1. 승인 완료 + 틱톡 미발행 회차만 대상으로 뽑는다(fail-closed).
  2. combined.media_url 이 approval.media_url 보다 우선한다.
  3. 없는 필드는 추측하지 않고 None + missing 으로 남긴다(설계 F-3).
  4. 종료코드: 0(대상 있음) / 10(대상 없음) / 66(원장 부재).
"""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from .. import runner
except ImportError:                                    # 단독 실행 대비
    from gen.hero_shorts.tiktok import runner


class _Args:
    def __init__(self, state=None):
        self.state = state


class CollectReadyTest(unittest.TestCase):
    def test_picks_only_approved_and_unpublished(self):
        state = {
            "ep86": {"approval": {"status": "HOLD"}, "combined": {"title": "보류"}},
            "ep87": {"approval": {"status": "APPROVED"}, "tiktok": {"status": "published"}},
            "ep88": {
                "approval": {"status": "APPROVED", "media_url": "https://x/88-approval"},
                "combined": {"title": "물러서지 않는 두 전선", "duration_sec": 60.859,
                             "media_url": "https://x/88-combined",
                             "aidrive_path": "/hero-shorts/media/ep88_final_voiced.mp4"},
            },
            "meta": {"note": "회차 키 아님 — 무시되어야 함"},
        }
        items, stats = runner.collect_ready(state)
        self.assertEqual([i["ep"] for i in items], [88])
        self.assertEqual(items[0]["title"], "물러서지 않는 두 전선")
        self.assertEqual(items[0]["duration_sec"], 60.859)
        self.assertEqual(items[0]["media_url"], "https://x/88-combined")   # combined 우선
        self.assertEqual(items[0]["aidrive_path"], "/hero-shorts/media/ep88_final_voiced.mp4")
        self.assertEqual(items[0]["missing"], [])
        self.assertEqual(stats["published"], 1)
        self.assertEqual(stats["not_approved"], 1)

    def test_approval_media_url_fallback_and_missing_flags(self):
        state = {"ep90": {"approval": {"status": "APPROVED", "media_url": "https://x/90"},
                          "combined": {"title": "구십"}}}
        items, _ = runner.collect_ready(state)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["media_url"], "https://x/90")
        self.assertEqual(items[0]["missing"], ["duration_sec"])

    def test_no_approval_block_is_not_ready(self):
        state = {"ep91": {"status": "QC_PASSED_READY", "title": "승인 요청 전"}}
        items, stats = runner.collect_ready(state)
        self.assertEqual(items, [])
        self.assertEqual(stats["no_approval"], 1)

    def test_duplicate_approved_sorted_ascending(self):
        state = {f"ep{n}": {"approval": {"status": "APPROVED"},
                            "combined": {"title": f"t{n}", "duration_sec": 60.0,
                                         "media_url": f"https://x/{n}"}}
                 for n in (95, 93, 94)}
        items, _ = runner.collect_ready(state)
        self.assertEqual([i["ep"] for i in items], [93, 94, 95])


class CmdReadyExitCodeTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("HERO_STATE_FILE")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HERO_STATE_FILE", None)
        else:
            os.environ["HERO_STATE_FILE"] = self._old

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = runner.cmd_ready(_Args())
        return rc, buf.getvalue()

    def test_exit_0_when_target_exists(self):
        with TemporaryDirectory() as tmp:
            sp = Path(tmp) / "publish_state.json"
            sp.write_text(json.dumps({
                "ep88": {"approval": {"status": "APPROVED"},
                         "combined": {"title": "t", "duration_sec": 60.0, "media_url": "u"}}
            }, ensure_ascii=False), encoding="utf-8")
            os.environ["HERO_STATE_FILE"] = str(sp)
            rc, out = self._run()
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["ready_count"], 1)
        self.assertEqual(payload["items"][0]["ep"], 88)

    def test_exit_10_when_nothing_to_publish(self):
        with TemporaryDirectory() as tmp:
            sp = Path(tmp) / "publish_state.json"
            sp.write_text(json.dumps({"ep88": {"approval": {"status": "HOLD"}}}), encoding="utf-8")
            os.environ["HERO_STATE_FILE"] = str(sp)
            rc, out = self._run()
        self.assertEqual(rc, runner.READY_EXIT_NONE)
        self.assertFalse(json.loads(out)["ok"])

    def test_exit_66_when_state_missing(self):
        with TemporaryDirectory() as tmp:
            os.environ["HERO_STATE_FILE"] = str(Path(tmp) / "nope.json")
            rc, out = self._run()
        self.assertEqual(rc, runner.READY_EXIT_NO_STATE)
        self.assertEqual(json.loads(out)["reason"], "state_missing")

    def test_incomplete_item_reported_not_dropped(self):
        with TemporaryDirectory() as tmp:
            sp = Path(tmp) / "publish_state.json"
            sp.write_text(json.dumps({
                "ep88": {"approval": {"status": "APPROVED"}, "combined": {"title": "t"}}
            }, ensure_ascii=False), encoding="utf-8")
            os.environ["HERO_STATE_FILE"] = str(sp)
            rc, out = self._run()
        payload = json.loads(out)
        self.assertEqual(rc, 0)                       # 대상은 있음 — 스킬이 F-3 로 판단
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["ready_count"], 0)
        self.assertEqual(payload["incomplete"][0]["missing"], ["duration_sec", "media_url"])


if __name__ == "__main__":
    unittest.main()
