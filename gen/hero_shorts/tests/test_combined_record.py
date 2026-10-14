"""pipeline.record_combined() 회귀 테스트 (v2.9.0 — 2026-10-15 설계 C-1).

핵심 요건:
  1. duration_sec 을 원장에 남긴다 → 러너3 가 --duration 수동 입력을 하지 않는다.
  2. 기존 ep<N> 블록의 다른 키(status/approval/qc)를 파괴하지 않는다.
  3. 실측 불가(영상 파일 부재)면 추측값 대신 이전 값/None 을 유지한다.
"""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from .. import pipeline
except ImportError:                                    # 단독 실행 대비
    from gen.hero_shorts import pipeline


class RecordCombinedTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("HERO_STATE_FILE")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HERO_STATE_FILE", None)
        else:
            os.environ["HERO_STATE_FILE"] = self._old

    def _seed(self, tmp, payload):
        sp = Path(tmp) / "publish_state.json"
        sp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.environ["HERO_STATE_FILE"] = str(sp)
        return sp

    def test_records_and_preserves_other_keys(self):
        with TemporaryDirectory() as tmp:
            sp = self._seed(tmp, {
                "ep88": {"status": "QC_PASSED_READY", "title": "원장 제목",
                         "approval": {"status": "APPROVED", "media_url": "https://x/88"}}
            })
            block = pipeline.record_combined(88, "합본 제목", str(Path(tmp) / "absent.mp4"))
            state = json.loads(sp.read_text(encoding="utf-8"))
        self.assertEqual(block["title"], "합본 제목")
        self.assertIsNone(block["duration_sec"])       # 파일 부재 → 추측 금지
        self.assertEqual(state["ep88"]["approval"]["status"], "APPROVED")
        self.assertEqual(state["ep88"]["status"], "QC_PASSED_READY")
        self.assertEqual(state["ep88"]["combined"]["title"], "합본 제목")
        self.assertIn("recorded_at", state["ep88"]["combined"])

    def test_creates_block_when_absent(self):
        with TemporaryDirectory() as tmp:
            sp = self._seed(tmp, {})
            pipeline.record_combined(88, "신규", None)
            state = json.loads(sp.read_text(encoding="utf-8"))
        self.assertEqual(state["ep88"]["combined"]["title"], "신규")
        self.assertIsNone(state["ep88"]["combined"]["duration_sec"])
        self.assertIsNone(state["ep88"]["combined"]["video_path"])

    def test_merges_media_url_and_aidrive_without_losing_duration(self):
        with TemporaryDirectory() as tmp:
            sp = self._seed(tmp, {
                "ep88": {"combined": {"title": "t", "duration_sec": 60.859,
                                      "recorded_at": "2026-10-20T08:00:00+09:00"}}
            })
            pipeline.record_combined(88, "t", str(Path(tmp) / "absent.mp4"))
            block = pipeline.record_combined(
                88, "t", str(Path(tmp) / "absent.mp4"),
                media_url="https://x/88-public",
                aidrive_path="/hero-shorts/media/ep88_final_voiced.mp4")
            state = json.loads(sp.read_text(encoding="utf-8"))
        self.assertEqual(block["duration_sec"], 60.859)          # 이전 실측값 유지
        self.assertEqual(block["media_url"], "https://x/88-public")
        self.assertEqual(block["aidrive_path"], "/hero-shorts/media/ep88_final_voiced.mp4")
        self.assertEqual(state["ep88"]["combined"]["duration_sec"], 60.859)


if __name__ == "__main__":
    unittest.main()
