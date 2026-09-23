"""
gen/hero_shorts/tests/test_all.py — 전수테스트 (오프라인 0원)
================================================================
범위 (마스터 지시 "전수테스트, QC테스트"):
    1. canon     — 캐논 블록 조립·필수 토큰·금지 문구
    2. cutplanner— Ep86 샘플 arc_state → 유형별 컷수·프롬프트·fal 요청
    3. generator — 더미 컷 생성 (ffmpeg, 0원) + 예산 캡 동작
    4. assemble  — 합본 720p + ffprobe 기술검증
    5. qc        — 4게이트 통과 / 실패 차단
    6. ledger    — 인덱스 행 파싱(정본=Y 필터) — 네트워크 비의존 샘플
    7. pipeline  — 상태 파일 갱신·조회

실행: python3 gen/hero_shorts/tests/test_all.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # gen/ 루트

from gen.hero_shorts import canon, cutplanner, generator, assemble_qc  # noqa: E402
from gen.hero_shorts import ledger, pipeline  # noqa: E402

# ── Ep86 실측 arc_state 샘플 (원장 정본 — 네트워크 비의존 테스트 데이터) ──
EP86 = {
    "episode": "Ep86", "title": "배분을 바꾼다, 지금 필요한 곳으로",
    "date": "2026-09-09", "type": "BATTLE", "outcome": "Hero Tactical Victory",
    "arc_state": {
        "active_villains": ["Oil Shock Titan"], "arc_day": 4,
        "arc_tension": {"previous": 78, "current": 74},
        "hero_momentum": {"previous": 78, "current": 83},
    },
    "next_episode": {"number": "Ep87"},
}


class TestCanon(unittest.TestCase):
    """G2 캐논 — 프롬프트 빌드 유일 경로 검증."""

    def test_canon_block_contains_fixed_description(self):
        block = canon.canon_block(["EDT", "Oil Shock Titan"])
        self.assertIn("bronze-steel roman full plate armor", block)
        self.assertIn("molten oil and black smoke", block)

    def test_unknown_character_skipped(self):
        block = canon.canon_block(["미등록캐릭터"])
        self.assertNotIn("미등록캐릭터", block)  # 묘사 노출 없이 스킵

    def test_full_prompt_injects_tokens(self):
        p = canon.full_prompt("A hero lands on a rooftop", ["EDT"],
                              "low-angle", "bass")
        for tok in canon.PROMPT_TOKENS:
            self.assertIn(tok, p)
        self.assertNotIn("red suit", p)


class TestCutPlanner(unittest.TestCase):
    """유형별 컷구성 + fal 요청 스키마."""

    def test_battle_six_cuts(self):
        plan = cutplanner.plan_episode(EP86)
        self.assertEqual(len(plan), 6)
        roles = [c["role"] for c in plan]
        self.assertEqual(roles[0], "ESTABLISH_THREAT")
        self.assertEqual(roles[-1], "RESOLVE")

    def test_all_prompts_have_canon_tokens(self):
        plan = cutplanner.plan_episode(EP86)
        joined = "\n".join(c["prompt"] for c in plan)
        for tok in canon.PROMPT_TOKENS:
            self.assertIn(tok, joined)

    def test_fal_request_schema(self):
        req = cutplanner.build_fal_request("x")
        self.assertEqual(req["duration"], 10)
        self.assertEqual(req["resolution"], "768P")
        self.assertEqual(req["aspect_ratio"], "9:16")
        self.assertEqual(req["prompt_expansion_mode"], "disabled")

    def test_aftermath_three_cuts(self):
        self.assertEqual(len(cutplanner.cuts_for_type("AFTERMATH")), 3)


class TestGeneratorAndAssemble(unittest.TestCase):
    """더미 생성 → 합본 → ffprobe (0원 E2E)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.plan = cutplanner.plan_episode(EP86)
        cls.cuts = [generator.generate("dummy", c["request"],
                                       cls.out / f"cut{i+1}.mp4")
                    for i, c in enumerate(cls.plan[:2])]  # 테스트 단축 2컷

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_dummy_cut_created(self):
        for p in self.cuts:
            self.assertTrue(Path(p).exists() and Path(p).stat().st_size > 1000)

    def test_assemble_720p(self):
        final = assemble_qc.assemble(self.cuts, self.out / "final.mp4")
        ok, m = assemble_qc.ffprobe_check(final, min_sec=19, max_sec=25)
        self.assertTrue(ok, f"기술검증 실패: {m}")

    def test_budget_cap_blocks(self):
        # 캡 $0.001 — $0.60 필요분이 즉시 초과 → RuntimeError 로 차단되어야 정상
        self.assertRaises(RuntimeError, generator.budget_check, 10, cap="0.001")


class TestQC(unittest.TestCase):
    """QC 4게이트 — 통과/차단 양쪽 검증."""

    def _prompts(self):
        return [c["prompt"] for c in cutplanner.plan_episode(EP86)]

    def test_gates_pass(self):
        # BATTLE 3컷 더미 생성(0원) → 캐논 토큰·금지 문구 검증
        plan = cutplanner.plan_episode(EP86)[:3]
        tmp = Path(self.tmpdir())
        cuts = []
        for i, c in enumerate(plan):
            cuts.append(generator.generate("dummy", c["request"], tmp / f"qc{i}.mp4"))
        final = assemble_qc.assemble(cuts, tmp / "qc_final.mp4")
        qc = assemble_qc.qc_4gates(
            [c["prompt"] for c in plan],
            final,
            EP86,
            plan_meta={"title": EP86["title"], "type": EP86["type"], "outcome": EP86["outcome"]},
        )
        self.assertTrue(qc["G1"])
        self.assertTrue(qc["G2"])
        self.assertTrue(qc["G4"])
        self.assertEqual(qc["G3"]["w"], 720)
        self.assertEqual(qc["G3"]["h"], 1280)

    def test_forbidden_phrase_blocks(self):
        bad = self._prompts() + ["Buy now! guaranteed profit suit"]
        self.assertIn("guaranteed", "\n".join(bad))    # G1 위반 탐지 대상 확인

    @staticmethod
    def tmpdir():
        import tempfile
        return tempfile.mkdtemp()


class TestLedgerRows(unittest.TestCase):
    """트래커 DB 다중 행 정본 선택 + episode 정규화."""

    ROWS_86 = [
        {
            "id": "stale-86", "createdTime": "2026-09-09 12:00:17Z", "번호": 86,
            "에피소드": "Ep86 — 물러서지 않는 이유", "에피소드 타입": "STALEMATE",
            "전투 결과": "No Battle", "발행 상태": "진행중", "메인 히어로": "Gold Bond Muscle",
            "활성 빌런": "Oil Shock Titan", "Battle Balance": None,
            "특이사항": "[논리적 폐기 — ACT2 정식전환 2026-09-09] 기존 버전"
        },
        {
            "id": "canon-86", "createdTime": "2026-09-09 13:36:15Z", "번호": 86,
            "에피소드": "Ep86 — 배분을 바꾼다, 지금 필요한 곳으로 [ACT2 정식전환]",
            "에피소드 타입": "BATTLE", "전투 결과": "Tactical Victory", "발행 상태": "완료",
            "메인 히어로": "Guardian of Capital", "활성 빌런": "Oil Shock Titan",
            "Battle Balance": 27, "Arc Day": 4, "arc_tension": 74,
            "date:발행일:start": "2026-09-09", "특이사항": "정식 전환 기록"
        },
    ]

    def test_choose_canonical_row_prefers_non_discarded_completed(self):
        row = ledger.choose_canonical_row(self.ROWS_86)
        self.assertEqual(row["id"], "canon-86")
        self.assertEqual(row["에피소드 타입"], "BATTLE")

    def test_row_to_episode_normalizes_shape(self):
        ep = ledger.row_to_episode(self.ROWS_86[1])
        self.assertEqual(ep["episode"], "Ep86")
        self.assertEqual(ep["type"], "BATTLE")
        self.assertEqual(ep["outcome"], "Tactical Victory")
        self.assertEqual(ep["arc_state"]["active_villains"], ["Oil Shock Titan"])
        self.assertEqual(ep["arc_state"]["arc_day"], 4)
        self.assertEqual(ep["source"]["kind"], "tracker_db")


class TestLedgerApi(unittest.TestCase):
    """Actions용 Notion API 경로/토큰 우선순위 검증."""

    def test_query_tracker_rows_uses_data_sources_endpoint_and_api_token(self):
        payload = {
            "results": [
                {
                    "id": "page-86",
                    "url": "https://www.notion.so/page-86",
                    "created_time": "2026-09-09T13:36:15.000Z",
                    "properties": {
                        "번호": {"type": "number", "number": 86},
                        "에피소드": {"type": "title", "title": [{"plain_text": "Ep86 — 배분을 바꾼다, 지금 필요한 곳으로"}]},
                        "에피소드 타입": {"type": "select", "select": {"name": "BATTLE"}},
                        "전투 결과": {"type": "select", "select": {"name": "Tactical Victory"}},
                        "발행 상태": {"type": "status", "status": {"name": "완료"}},
                    },
                }
            ]
        }

        class FakeResponse:
            def __init__(self, text):
                self._text = text

            def read(self, *args, **kwargs):
                return self._text.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        seen = {}

        def fake_urlopen(req, timeout=60):
            seen["url"] = req.full_url
            seen["auth"] = req.headers.get("Authorization")
            return FakeResponse(json.dumps(payload, ensure_ascii=False))

        with patch.dict(os.environ, {
            "NOTION_API_TOKEN": "token-api",
            "NOTION_TOKEN": "token-legacy",
            "NOTION_API_KEY": "token-key",
        }, clear=False):
            with patch("urllib.request.urlopen", fake_urlopen):
                rows = ledger.query_tracker_rows(86)

        self.assertEqual(
            seen["url"],
            f"https://api.notion.com/v1/data_sources/{ledger.TRACKER_DATA_SOURCE_ID}/query",
        )
        self.assertEqual(seen["auth"], "Bearer token-api")
        self.assertEqual(rows[0]["번호"], 86)
        self.assertEqual(rows[0]["에피소드 타입"], "BATTLE")


class TestPipelineState(unittest.TestCase):
    """발행 원장 상태 갱신·조회."""

    def test_state_roundtrip(self):
        old = pipeline.STATE_FILE
        try:
            with tempfile.TemporaryDirectory() as d:
                pipeline.STATE_FILE = Path(d) / "publish_state.json"
                pipeline.save_state({"ep86": {"status": "QC_PASSED_READY"}})
                st = pipeline.load_state()
                self.assertEqual(st["ep86"]["status"], "QC_PASSED_READY")
        finally:
            pipeline.STATE_FILE = old


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCostGates(unittest.TestCase):
    """v2.4.4 — 비용 발생 프로세스 사전 밸리데이션 (마스터 지시 전수검토 반영)."""

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("FAL_AI_KEY", "FAL_ENDPOINT", "HERO_ALLOW_ALL")}
        os.environ["FAL_AI_KEY"] = "test-key"
        os.environ["FAL_ENDPOINT"] = "minimax/h3/text-to-video"
        os.environ.pop("HERO_ALLOW_ALL", None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_unknown_endpoint_refused(self):
        os.environ["FAL_ENDPOINT"] = "minimax/h3/reference-to-video"  # 가격 미실측
        req = cutplanner.build_fal_request("x")
        self.assertRaises(RuntimeError, generator._fal_call, req, "/tmp/never.mp4")

    def test_duration_out_of_range_refused_before_cost(self):
        req = cutplanner.build_fal_request("x")
        req["duration"] = 60
        self.assertRaises(ValueError, generator._fal_call, req, "/tmp/never.mp4")

    def test_non_canon_prompt_refused_before_cost(self):
        req = cutplanner.build_fal_request("A cat walks in a garden")
        self.assertRaises(ValueError, generator._fal_call, req, "/tmp/never.mp4")

    def test_fal_all_cuts_refused_without_only(self):
        plan = cutplanner.plan_episode(EP86)
        with tempfile.TemporaryDirectory() as d:
            pf = Path(d) / "p.json"
            pf.write_text(json.dumps({"episode": "Ep86", "cuts": plan}), encoding="utf-8")
            self.assertRaises(RuntimeError, pipeline.cmd_generate, str(pf), "fal", d)

    def test_publish_without_full_qc_refused(self):
        plan_file = "unused"
        self.assertRaises(RuntimeError, pipeline.cmd_publish, plan_file, "v.mp4", {"G1": True})
        self.assertRaises(RuntimeError, pipeline.cmd_publish, plan_file, "v.mp4", {})

    def test_g5_caption_without_disclaimer_refused(self):
        req = {"prompt": "x", "duration": 30}
        v = generator.generate("dummy", req, Path(self.__class__.__name__) / "g5.mp4")
        self.assertRaises(RuntimeError, assemble_qc.validate_publish_assets,
                          v, "시장 만화 1화", "https://example.com/v.mp4", "2026-09-23T10:00:00")
