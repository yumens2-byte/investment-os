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
import subprocess
import unittest
from unittest import mock
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # gen/ 루트

from gen.hero_shorts import canon, cutplanner, generator, assemble_qc, voiceover  # noqa: E402
from gen.hero_shorts import ledger, pipeline, publish_runtime  # noqa: E402

TRACKER_ENV = {
    "HERO_SHORTS_TRACKER_DB_ID": "db-test",
    "HERO_SHORTS_TRACKER_VIEW_URL": "https://example.com/notion/view",
    "HERO_SHORTS_TRACKER_DATA_SOURCE_ID": "ds-test",
}

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

    def test_world_canon_and_forbidden_keywords(self):
        p = canon.full_prompt("A hero lands on a rooftop", ["EDT"],
                              "low-angle", "bass")
        self.assertIn("modern financial district", p)
        self.assertIn("grounded contemporary city realism", p)
        self.assertIn("medieval castle", canon.FORBIDDEN_GLOBAL)


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

    def test_role_contracts_strengthened_for_quality(self):
        plan = cutplanner.plan_episode(EP86)
        by_role = {c["role"]: c["prompt"] for c in plan}
        self.assertIn("modern financial district", by_role["ESCALATE"])
        self.assertIn("crowds", by_role["ESCALATE"])
        self.assertIn("small in frame", by_role["ESCALATE"])
        self.assertIn("shockwave", by_role["CRISIS"])
        self.assertIn("debris", by_role["CRISIS"])
        self.assertIn("two contrasting zones", by_role["TURN"])
        self.assertIn("lighting back up", by_role["TURN"])
        self.assertIn("dawn", by_role["RESOLVE"])
        self.assertIn("recovering skyline", by_role["RESOLVE"])

    def test_plan_reuses_single_prompt_per_cut(self):
        with patch("gen.hero_shorts.cutplanner.build_cut_prompt", wraps=cutplanner.build_cut_prompt) as mocked:
            plan = cutplanner.plan_episode(EP86)
        self.assertEqual(len(plan), 6)
        self.assertEqual(mocked.call_count, 6)
        for cut in plan:
            self.assertEqual(cut["request"]["prompt"], cut["prompt"])


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


class TestVoiceOver(unittest.TestCase):
    """대사 계획·더미 TTS·믹싱 경로 검증."""

    def test_build_dialogue_plan_matches_cut_count(self):
        plan = {
            "episode": 86,
            "title": EP86["title"],
            "type": EP86["type"],
            "outcome": EP86["outcome"],
            "cuts": cutplanner.plan_episode(EP86),
        }
        payload = voiceover.build_dialogue_plan(plan)
        self.assertEqual(len(payload["cuts"]), 6)
        self.assertEqual(payload["cuts"][0]["role"], "ESTABLISH_THREAT")
        self.assertTrue(payload["cuts"][0]["text"])
        self.assertLessEqual(payload["cuts"][0]["target_duration_sec"], 10)

    def test_dummy_tts_and_mix_create_audible_video(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            plan = {
                "episode": 86,
                "title": EP86["title"],
                "type": EP86["type"],
                "outcome": EP86["outcome"],
                "cuts": cutplanner.plan_episode(EP86)[:2],
            }
            plan_file = tmp / "plan.json"
            plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            voice_plan_file = tmp / "voice_plan.json"
            voiceover.save_dialogue_plan(plan_file, voice_plan_file)
            manifest = voiceover.synthesize_tts_clips(voice_plan_file, tmp / "audio", backend="dummy")
            self.assertTrue(Path(manifest["merged_audio"]).exists())
            self.assertGreater(manifest["merged_duration_sec"], 19)
            self.assertLess(manifest["merged_duration_sec"], 21)
            for clip in manifest["clips"]:
                self.assertGreaterEqual(clip["duration_sec"], 9.9)
                self.assertLessEqual(clip["duration_sec"], 10.1)

            cuts = []
            for i, c in enumerate(plan["cuts"]):
                cuts.append(generator.generate("dummy", c["request"], tmp / f"cut{i}.mp4"))
            final = assemble_qc.assemble(cuts, tmp / "final.mp4")
            mixed = voiceover.mix_voice_over(final, manifest["merged_audio"], tmp / "final_voiced.mp4")
            audio_info = assemble_qc.detect_audio_presence(mixed)
            self.assertTrue(audio_info["audible"])


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

    def test_missing_tracker_config_refused(self):
        with patch.dict(os.environ, {
            "HERO_SHORTS_TRACKER_DB_ID": "",
            "HERO_SHORTS_TRACKER_VIEW_URL": "",
            "HERO_SHORTS_TRACKER_DATA_SOURCE_ID": "",
        }, clear=False):
            with patch.object(ledger, "TRACKER_DB_ID", ""), \
                 patch.object(ledger, "TRACKER_VIEW_URL", ""), \
                 patch.object(ledger, "TRACKER_DATA_SOURCE_ID", ""), \
                 patch.object(ledger, "TRACKER_DATA_SOURCE_URL", None):
                self.assertRaises(RuntimeError, ledger._require_tracker_config)

    def test_source_defaults_restored(self):
        """v2.5.2 — HERO_* 운영 식별값 소스 기본값 복원 검증."""
        import gen.hero_shorts.pipeline as pipeline_mod
        self.assertTrue(ledger.TRACKER_DB_ID)
        self.assertTrue(ledger.TRACKER_VIEW_URL)
        self.assertTrue(ledger.TRACKER_DATA_SOURCE_ID)
        self.assertTrue(ledger.TRACKER_DATA_SOURCE_URL)
        self.assertTrue(pipeline_mod.DEFAULT_ZERNIO_ACCOUNT_ID)
        self.assertTrue(pipeline_mod.DEFAULT_ALERT_EMAIL)
        self.assertTrue(pipeline_mod.DEFAULT_FROM_ACCOUNT)

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
            **TRACKER_ENV,
            "NOTION_API_TOKEN": "token-api",
            "NOTION_TOKEN": "token-legacy",
            "NOTION_API_KEY": "token-key",
        }, clear=False):
            with patch.object(ledger, "TRACKER_DB_ID", "db-test"), \
                 patch.object(ledger, "TRACKER_VIEW_URL", "https://example.com/notion/view"), \
                 patch.object(ledger, "TRACKER_DATA_SOURCE_ID", "ds-test"), \
                 patch.object(ledger, "TRACKER_DATA_SOURCE_URL", "collection://ds-test"), \
                 patch("urllib.request.urlopen", fake_urlopen):
                rows = ledger.query_tracker_rows(86)

        self.assertEqual(
            seen["url"],
            "https://api.notion.com/v1/data_sources/ds-test/query",
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

    def test_assemble_paths_from_plan_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plan.json"
            p.write_text(json.dumps({
                "episode": 86,
                "cuts": [{"cut_no": 1}, {"cut_no": 2}]
            }, ensure_ascii=False), encoding="utf-8")
            paths = pipeline._assemble_paths_from_input(str(p), 86)
            self.assertTrue(paths[0].endswith("ep86_cut1.mp4"))
            self.assertTrue(paths[1].endswith("ep86_cut2.mp4"))

    def test_qc_falls_back_to_plan_meta_when_ledger_fails(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            plan = {
                "episode": 86,
                "title": EP86["title"],
                "type": EP86["type"],
                "outcome": EP86["outcome"],
                "cuts": cutplanner.plan_episode(EP86)[:3],
            }
            plan_file = tmp / "plan.json"
            plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            cuts = [generator.generate("dummy", c["request"], tmp / f"cut{i}.mp4") for i, c in enumerate(plan["cuts"])]
            final = assemble_qc.assemble(cuts, tmp / "final.mp4")
            with patch("gen.hero_shorts.ledger.load_episode", side_effect=RuntimeError("boom")):
                qc = pipeline.cmd_qc(str(plan_file), final)
            self.assertTrue(qc["G1"])
            self.assertTrue(qc["G2"])
            self.assertTrue(qc["G4"])


class TestPublishRuntime(unittest.TestCase):
    def test_create_post_args_now(self):
        payload = publish_runtime.create_post_args(
            text="caption",
            media_url="https://www.genspark.ai/api/files/s/example",
            account_id="acc1",
        )
        self.assertEqual(payload["text"], "caption")
        self.assertEqual(payload["media_urls"], ["https://www.genspark.ai/api/files/s/example"])
        self.assertEqual(payload["account_ids"], ["acc1"])
        self.assertTrue(payload["ai_generated"])
        self.assertNotIn("schedule_at", payload)

    def test_create_post_args_schedule(self):
        payload = publish_runtime.create_post_args(
            text="caption",
            media_url="https://www.genspark.ai/api/files/s/example",
            account_id="acc1",
            schedule_at="2026-09-24T09:30:00+09:00",
            ai_generated=False,
        )
        self.assertEqual(payload["schedule_at"], "2026-09-24T09:30:00+09:00")
        self.assertFalse(payload["ai_generated"])

    def test_normalize_status_published(self):
        post = {
            "status": "publishing",
            "platforms": [{
                "platform": "instagram",
                "status": "published",
                "platformPostUrl": "https://www.instagram.com/reel/abc/",
            }],
        }
        status, url = publish_runtime._normalize_status(post, "instagram")
        self.assertEqual(status, "published")
        self.assertEqual(url, "https://www.instagram.com/reel/abc/")

    def test_normalize_status_failed(self):
        post = {
            "status": "publishing",
            "platforms": [{
                "platform": "instagram",
                "status": "failed",
                "errorMessage": "download failed",
            }],
        }
        status, url = publish_runtime._normalize_status(post, "instagram")
        self.assertEqual(status, "failed")
        self.assertIsNone(url)

    def test_collect_errors(self):
        post = {
            "status": "error",
            "platforms": [{
                "platform": "instagram",
                "status": "failed",
                "error": "x",
                "errorMessage": "y",
            }],
        }
        errors = publish_runtime._collect_errors(post)
        self.assertTrue(any("instagram: x" == e for e in errors))
        self.assertTrue(any("instagram: y" == e for e in errors))
        self.assertTrue(any("aggregate: status=error" == e for e in errors))


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

    def test_duplicate_post_blocked(self):
        old = pipeline.STATE_FILE
        try:
            with tempfile.TemporaryDirectory() as d:
                pipeline.STATE_FILE = Path(d) / "publish_state.json"
                pipeline.save_state({"ep86": {"status": "published", "zernio_post_id": "post-1"}})
                plan_file = Path(d) / "plan.json"
                plan_file.write_text(json.dumps({
                    "episode": 86,
                    "title": EP86["title"],
                    "type": EP86["type"],
                    "outcome": EP86["outcome"],
                    "cuts": cutplanner.plan_episode(EP86)[:1],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                with patch("gen.hero_shorts.assemble_qc.validate_publish_assets", return_value=True):
                    self.assertRaises(
                        RuntimeError,
                        pipeline.cmd_zernio_publish,
                        str(plan_file),
                        "v.mp4",
                        __file__,
                        "https://example.com/video.mp4",
                        {"G1": True, "G2": True, "G3": {"w": 720, "h": 1280, "duration": 10, "audio": 1}, "G4": True},
                        None,
                        "acc1",
                    )
        finally:
            pipeline.STATE_FILE = old

    def test_g5_caption_without_disclaimer_refused(self):
        req = {"prompt": "x", "duration": 30}
        v = generator.generate("dummy", req, Path(self.__class__.__name__) / "g5.mp4")
        self.assertRaises(RuntimeError, assemble_qc.validate_publish_assets,
                          v, "시장 만화 1화", "https://example.com/v.mp4", "2026-09-23T10:00:00")

    def test_g5_require_audible_audio_refused(self):
        req = {"prompt": "x", "duration": 30}
        v = generator.generate("dummy", req, Path(self.__class__.__name__) / "g5_voice.mp4")
        with patch("gen.hero_shorts.assemble_qc.detect_audio_presence", return_value={
            "audible": False,
            "mean_volume_db": -91.0,
            "max_volume_db": -91.0,
        }):
            self.assertRaises(RuntimeError, assemble_qc.validate_publish_assets,
                              v,
                              "⚠️ 투자 참고 정보, 투자 권유 아님",
                              "https://example.com/v.mp4",
                              "2026-09-23T10:00:00",
                              True)


class TestFalTimeoutRecovery(unittest.TestCase):
    """fal 타임아웃 후 회수/재개 로직 검증."""

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in (
            "FAL_AI_KEY", "FAL_ENDPOINT", "FAL_MAX_POLLS", "FAL_POLL_INTERVAL_SEC"
        )}
        os.environ["FAL_AI_KEY"] = "test-key"
        os.environ["FAL_ENDPOINT"] = "minimax/h3/text-to-video"
        os.environ["FAL_MAX_POLLS"] = "2"
        os.environ["FAL_POLL_INTERVAL_SEC"] = "0.01"
        self.req = cutplanner.plan_episode(EP86)[3]["request"]
        self.tmpdir = tempfile.TemporaryDirectory()
        self.out_path = Path(self.tmpdir.name) / "cut4.mp4"

    def tearDown(self):
        self.tmpdir.cleanup()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_timeout_saves_pending_file(self):
        submit = {
            "request_id": "req-timeout",
            "status_url": "https://status/req-timeout",
            "response_url": "https://response/req-timeout",
        }
        queued = {"status": "IN_QUEUE"}

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def read(self, *args, **kwargs):
                return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        calls = {"poll": 0}

        def fake_urlopen(req, timeout=60):
            if req.full_url == "https://queue.fal.run/minimax/h3/text-to-video":
                return FakeResponse(submit)
            if req.full_url == "https://status/req-timeout":
                calls["poll"] += 1
                return FakeResponse(queued)
            raise AssertionError(f"unexpected url: {req.full_url}")

        with patch("urllib.request.urlopen", fake_urlopen), \
             patch("time.sleep", lambda *_: None), \
             patch("gen.hero_shorts.generator.budget_check", lambda *_args, **_kwargs: None):
            with self.assertRaises(TimeoutError):
                generator._fal_call(self.req, self.out_path)

        pending_path = self.out_path.with_name(self.out_path.name + ".fal_pending.json")
        self.assertTrue(pending_path.exists())
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        self.assertEqual(pending["request_id"], "req-timeout")
        self.assertEqual(pending["last_status"], "IN_QUEUE")
        self.assertEqual(pending["poll_count"], 2)
        self.assertEqual(calls["poll"], 2)

    def test_resume_uses_pending_without_resubmit(self):
        pending = {
            "request_id": "req-resume",
            "status_url": "https://status/req-resume",
            "response_url": "https://response/req-resume",
            "endpoint": "minimax/h3/text-to-video",
            "request_signature": generator._request_signature(self.req),
            "submitted_at": "2026-09-23T16:40:00Z",
            "last_status": "IN_PROGRESS",
            "poll_count": 7,
        }
        pending_path = self.out_path.with_name(self.out_path.name + ".fal_pending.json")
        pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def read(self, *args, **kwargs):
                return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        seen = []

        def fake_urlopen(req, timeout=60):
            seen.append(req.full_url)
            if req.full_url == "https://queue.fal.run/minimax/h3/text-to-video":
                raise AssertionError("resume 경로에서 재제출이 발생하면 안 됨")
            if req.full_url == "https://status/req-resume":
                return FakeResponse({"status": "COMPLETED"})
            if req.full_url == "https://response/req-resume":
                return FakeResponse({"video": {"url": "https://cdn.example.com/cut4.mp4"}})
            raise AssertionError(f"unexpected url: {req.full_url}")

        def fake_urlretrieve(url, out_path):
            Path(out_path).write_bytes(b"fake-video")
            return str(out_path), None

        with patch("urllib.request.urlopen", fake_urlopen), \
             patch("urllib.request.urlretrieve", fake_urlretrieve), \
             patch("time.sleep", lambda *_: None), \
             patch("gen.hero_shorts.generator._validate_output", lambda *_args, **_kwargs: None), \
             patch("gen.hero_shorts.generator.budget_record", lambda *_args, **_kwargs: None):
            result = generator._fal_call(self.req, self.out_path)

        self.assertEqual(result, str(self.out_path))
        self.assertFalse(pending_path.exists())
        self.assertIn("https://status/req-resume", seen)
        self.assertIn("https://response/req-resume", seen)




class TestV253Hardening(unittest.TestCase):
    """v2.5.3 — plan 캐논 셀프체크 / G5 URL 실가용성 / G4 정규화 / QC 상세화."""

    def test_plan_blocks_missing_canon_tokens(self):
        from gen.hero_shorts import canon as C, cutplanner, ledger as ledger_mod, pipeline as P
        ep = {"episode": "Ep87", "title": "t", "type": "BATTLE", "outcome": "Tactical Victory"}
        plan = cutplanner.plan_episode(ep)
        missing = [x for x in C.PROMPT_TOKENS if x not in "\n".join(c["prompt"] for c in plan)]
        self.assertEqual(missing, [])
        # G2 셀프체크는 플랜 전체 조인 기준 — 전 컷에서 토큰 제거해야 차단 대상이 된다
        broken = [{**c, "prompt": c["prompt"].replace("modern financial district", "cyber city")}
                  for c in plan]
        with patch.object(ledger_mod, "load_episode", return_value=ep), \
             patch.object(cutplanner, "plan_episode", return_value=broken):
            self.assertRaises(RuntimeError, P.cmd_plan, 87, "/tmp/opencode/v253test")

    def test_check_media_url_reachable(self):
        import urllib.error
        from gen.hero_shorts import assemble_qc

        class R:
            status = 200
            headers = {"Content-Type": "video/mp4"}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with patch("urllib.request.urlopen", lambda req, timeout=15: R()):
            res = assemble_qc.check_media_url_reachable("https://example.com/v.mp4")
        self.assertTrue(res["reachable"])
        self.assertEqual(res["http_status"], 200)

        def fake405(req, timeout=15):
            if req.get_method() == "HEAD":
                raise urllib.error.HTTPError("u", 405, "m", {}, None)
            r = R(); r.status = 206
            return r
        with patch("urllib.request.urlopen", fake405):
            res = assemble_qc.check_media_url_reachable("https://example.com/v.mp4")
        self.assertEqual(res["http_status"], 206)

        def fake404(req, timeout=15):
            raise urllib.error.HTTPError("u", 404, "nf", {}, None)
        with patch("urllib.request.urlopen", fake404):
            self.assertRaises(RuntimeError, assemble_qc.check_media_url_reachable, "https://example.com/x.mp4")

        self.assertRaises(RuntimeError, assemble_qc.check_media_url_reachable, "ftp://bad")

    def test_g4_normalized_and_detailed_error(self):
        from gen.hero_shorts import assemble_qc
        prompts = ["webtoon-anime style. EDT. 9:16. No subtitles. modern financial district."]
        probe = (True, {"w": 720, "h": 1280, "duration": 60.0, "audio": 1})
        with patch.object(assemble_qc, "ffprobe_check", return_value=probe):
            ok = assemble_qc.qc_4gates(
                prompts, "dummy.mp4",
                {"title": "t", "type": "BATTLE", "outcome": "Tactical Victory"},
                plan_meta={"title": " t  ", "type": "battle", "outcome": "tactical victory"})
        self.assertTrue(ok["G4"])
        with patch.object(assemble_qc, "ffprobe_check", return_value=probe):
            try:
                assemble_qc.qc_4gates(
                    prompts, "dummy.mp4",
                    {"title": "t", "type": "BATTLE", "outcome": "Tactical Victory"},
                    plan_meta={"title": "t", "type": "BATTLE", "outcome": "Hero Tactical Victory"})
                self.fail("예외 미발생")
            except RuntimeError as e:
                self.assertIn("원장불일치=outcome", str(e))
                self.assertIn("QC 게이트 실패", str(e))


class TestV260Integration(unittest.TestCase):
    """v2.6.0 — ④에피소드 자동결정 ⑤캡션 ⑥업로드 ⑦원장내구화 ⑧트래커 역동기화."""

    def test_choose_next_episode(self):
        rows = [
            {"번호": 88, "발행 상태": "", "특이사항": ""},
            {"번호": 87, "발행 상태": "진행", "특이사항": ""},
            {"번호": 86, "발행 상태": "완료", "특이사항": ""},
            {"번호": 85, "발행 상태": "", "특이사항": "논리적 폐기"},
        ]
        self.assertEqual(ledger.choose_next_episode(rows), 87)
        with self.assertRaises(KeyError):
            ledger.choose_next_episode([{"번호": 86, "발행 상태": "완료"}])

    def test_find_next_episode_uses_full_query(self):
        with patch.object(ledger, "query_tracker_rows", return_value=[{"번호": 90, "발행 상태": "대기"}]) as q:
            self.assertEqual(ledger.find_next_episode(), 90)
            q.assert_called_once_with(None)

    def test_build_caption_deterministic_and_safe(self):
        from gen.hero_shorts import caption as cap_mod
        plan = {"title": "t", "type": "BATTLE", "outcome": "o",
                "cuts": [{"cut_no": 1, "role": "ESCALATE"}, {"cut_no": 2, "role": "TURN"}]}
        text = cap_mod.build_caption(plan)
        self.assertEqual(text.splitlines()[0], "t")
        self.assertEqual(text.count("- "), 2)
        self.assertIn("투자 권유 아님", text)
        for w in ("buy", "sell", "guaranteed", "무조건"):
            self.assertNotIn(w, text)
        self.assertEqual(text, cap_mod.build_caption(plan))

    def test_upload_media_parses_gsk_output(self):
        import subprocess as sp
        from gen.hero_shorts import mediashare
        outs = [json.dumps({"url": "https://www.genspark.ai/api/files/s/abc"}),
                json.dumps({"data": {"url": "https://x.test/pub?token=1"}})]

        def fake_run(args, capture_output=True, text=True, timeout=300):
            return sp.CompletedProcess(args, 0, stdout=outs.pop(0), stderr="")

        with patch.object(mediashare.subprocess, "run", fake_run):
            res = mediashare.upload_media("gen/data/out/ep86_final_voiced.mp4")
        self.assertEqual(res["wrapper_url"], "https://www.genspark.ai/api/files/s/abc")
        self.assertEqual(res["public_url"], "https://x.test/pub?token=1")

    def test_statestore_sync_and_restore(self):
        import subprocess as sp
        import tempfile as tf
        from gen.hero_shorts import statestore
        calls = []

        def fake_run(args, capture_output=True, text=True, timeout=300):
            calls.append(args)
            return sp.CompletedProcess(args, 0, stdout="{}", stderr="")

        with patch.object(statestore.subprocess, "run", fake_run):
            with tf.TemporaryDirectory() as d:
                f = Path(d) / "publish_state.json"
                f.write_text("{}", encoding="utf-8")
                self.assertEqual(statestore.sync_states([f]), [str(f)])
                self.assertIn("--upload_path", calls[0])
                statestore.restore_if_missing([f])          # 존재 → 스킵
                self.assertEqual(len(calls), 1)
                f.unlink()
                statestore.restore_if_missing([f])          # 부재 → 복원 시도
                self.assertIn("download", calls[-1])

    def test_update_publish_status_patches_row(self):
        seen = {}

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"id": "row-1"}).encode()

        def fake(req, timeout=60):
            seen["method"] = req.get_method()
            seen["url"] = req.full_url
            seen["body"] = req.data
            return R()

        with patch.dict(os.environ, {"NOTION_API_TOKEN": "tok"}):
            with patch("urllib.request.urlopen", fake):
                self.assertTrue(ledger.update_publish_status("row-1"))
        self.assertEqual(seen["method"], "PATCH")
        self.assertIn("pages/row-1", seen["url"])
        self.assertIn("발행 상태", seen["body"].decode("utf-8"))

    def test_update_publish_status_without_token_fails_clearly(self):
        self.assertRaises(RuntimeError, ledger.update_publish_status, "row-1", "완료", token=None)

    def test_cmd_plan_persists_source_row(self):
        ep = {"episode": "Ep87", "title": "t", "type": "BATTLE", "outcome": "o",
              "source": {"kind": "tracker_db", "row_id": "row-87"}}
        import gen.hero_shorts.pipeline as pipeline_mod
        with patch.object(ledger, "load_episode", return_value=ep):
            out = pipeline_mod.cmd_plan(87, "/tmp/opencode/v260plan")
        payload = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(payload["source"]["row_id"], "row-87")

    def test_zernio_publish_records_tracker_sync(self):
        import subprocess as sp
        import tempfile as tf
        import gen.hero_shorts.pipeline as P
        from gen.hero_shorts import publish_runtime
        d = Path(tf.mkdtemp())
        plan_f = d / "p.json"
        plan_f.write_text(json.dumps({
            "episode": 87, "title": "t", "type": "BATTLE", "outcome": "o",
            "source": {"row_id": "row-87"}, "cuts": []}), encoding="utf-8")
        captured = {}

        def fake_pub(**kw):
            return {"outcome": "published", "post_id": "p1", "post_url": "https://u", "snapshot": None}

        with patch.object(P, "load_state", return_value={}), \
             patch.object(P, "save_state", lambda s: captured.update(s)), \
             patch.object(publish_runtime, "create_and_monitor_post", fake_pub), \
             patch.object(ledger, "update_publish_status", return_value=True) as ups:
            res = P.cmd_zernio_publish(
                plan_file=str(plan_f),
                final_video="/tmp/verify-main/gen/data/out/ep86_final_voiced.mp4",
                caption_file="/tmp/opencode/ep86-post-final/ep86_caption_ko.txt",
                media_url="https://example.com/v.mp4",
                qc_result={"G1": True, "G2": True, "G3": True, "G4": True},
                require_audible_audio=False, require_media_reachable=False)
        self.assertEqual(res["outcome"], "published")
        self.assertEqual(captured["ep87"]["tracker_sync"], "done")
        self.assertIn("caption_file", captured["ep87"])
        ups.assert_called_once_with("row-87")


class TestV261Pilot(unittest.TestCase):
    """v2.6.1 — 발행 원장 보호 가드 + 원장 경로 격리(리허설 지원)."""

    def test_cmd_publish_guard_protects_published_record(self):
        import gen.hero_shorts.pipeline as P
        published = {"zernio_post_id": "p-old", "status": "published"}
        with patch.object(P, "load_state", return_value={"ep86": published}):
            self.assertRaises(RuntimeError, P.cmd_publish,
                              "gen/data/out/ep86_plan.json",
                              "gen/data/out/ep86_final_voiced.mp4",
                              {"G1": True, "G2": True, "G3": True, "G4": True})
        # 신규 회차(기록 없음)는 통과 — save_state만 격리
        plan_p = Path("/tmp/opencode/v261_plan.json")
        plan_p.write_text(json.dumps({"episode": 99, "title": "t", "type": "BATTLE", "outcome": "o"}), encoding="utf-8")
        saved = {}
        with patch.object(P, "load_state", return_value={}), \
             patch.object(P, "save_state", lambda s: saved.update(s)):
            rec = P.cmd_publish(str(plan_p), "dummy.mp4", {"G1": True, "G2": True, "G3": True, "G4": True})
        self.assertEqual(rec["status"], "QC_PASSED_READY")

    def test_state_file_env_override(self):
        import gen.hero_shorts.pipeline as P
        iso = "/tmp/opencode/v261_state/publish_state.json"
        Path(iso).unlink(missing_ok=True)             # 재실행 격리(파일럿 발견 결함 수정)
        with patch.dict(os.environ, {"HERO_STATE_FILE": iso}):
            self.assertEqual(P.load_state(), {})             # 격리 원장은 비어 있음
            P.save_state({"epX": {"status": "QC_PASSED_READY"}})
            self.assertEqual(P.load_state()["epX"]["status"], "QC_PASSED_READY")
            self.assertTrue(Path(iso).exists())
        Path(iso).unlink(missing_ok=True)             # 테스트 산출 정리


class TestV261AutoselectFallback(unittest.TestCase):
    """v2.6.1 — 전체 조회 0행 환경(gsk SQL 무필터 미지원)의 순차 탐색 fallback."""

    def test_find_next_episode_sequential_probe(self):
        rows88 = [{"번호": 88, "발행 상태": "대기", "특이사항": "", "id": "r88"}]
        with patch.object(ledger, "query_tracker_rows",
                          side_effect=[[], [], rows88]) as q:      # 전체조회, Ep87, Ep88
            self.assertEqual(ledger.find_next_episode(base=86), 88)
        self.assertEqual(q.call_args_list[1][0][0], 87)
        self.assertEqual(q.call_args_list[2][0][0], 88)

    def test_find_next_episode_skips_discarded_and_completed(self):
        discarded = [{"번호": 87, "발행 상태": "", "특이사항": "논리적 폐기", "id": "r87"}]
        done = [{"번호": 88, "발행 상태": "완료", "특이사항": "", "id": "r88"}]
        next_ep = [{"번호": 89, "발행 상태": "대기", "특이사항": "", "id": "r89"}]
        with patch.object(ledger, "query_tracker_rows",
                          side_effect=[[], discarded, done, next_ep]):
            self.assertEqual(ledger.find_next_episode(base=86), 89)

    def test_find_next_episode_no_base_no_rows_raises_clearly(self):
        with patch.object(ledger, "query_tracker_rows", return_value=[]):
            self.assertRaises(RuntimeError, ledger.find_next_episode)

class TestV270ApprovalGate(unittest.TestCase):
    """v2.7.0 — 텔레그램 승인 게이트(2페이즈): 요청→결정→가드 발행. 전부 mock 오프라인."""

    def _plan(self, tmp, ep=87):
        p = tmp / f"ep{ep}_plan.json"
        p.write_text(json.dumps({"episode": ep, "title": "테스트", "type": "시장분석",
                                 "outcome": "Victory", "source": {}, "cuts": []}), encoding="utf-8")
        return str(p)

    def test_config_missing_fails_closed(self):
        import os
        from unittest.mock import patch
        from gen.hero_shorts import approval
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "HERO_TELEGRAM_CHAT_ID": ""}):
            with self.assertRaises(RuntimeError) as cm:
                approval.request_approval(87, "t", "c", "https://example.com/v.mp4")
            self.assertIn("fail-closed", str(cm.exception))


    def test_config_alias_paid_channel_id(self):
        import os
        from unittest.mock import patch
        from gen.hero_shorts import approval
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "T", "HERO_TELEGRAM_CHAT_ID": "",
                                     "TELEGRAM_PAID_CHANNEL_ID": "-100999"}), \
             patch("gen.hero_shorts.approval._call_api") as api:
            api.return_value = {"message_id": 3}
            block = approval.request_approval(87, "제목", "캡션", "https://example.com/v.mp4")
        self.assertEqual(block["chat_id"], "-100999")          # 기존 공용변수 별칭 동작

    def test_request_approval_sends_buttons_and_records(self):
        import os
        from unittest.mock import patch
        from gen.hero_shorts import approval
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "T", "HERO_TELEGRAM_CHAT_ID": "42"}), \
             patch("gen.hero_shorts.approval._call_api") as api:
            api.return_value = {"message_id": 7}
            block = approval.request_approval(87, "제목", "캡션", "https://example.com/v.mp4")
            self.assertEqual(block["status"], "READY_FOR_APPROVAL")
            self.assertEqual(block["chat_id"], "42")
            self.assertEqual(block["message_id"], 7)
            methods = [c.args[1] for c in api.call_args_list]
            self.assertIn("sendMessage", methods)
            kb = json.loads(api.call_args_list[0].kwargs["params"]["reply_markup"])
            row = kb["inline_keyboard"][0]
            self.assertIn("승인", row[0]["text"])
            self.assertEqual(row[0]["callback_data"], "hs:approve:ep87")
            self.assertEqual(row[1]["callback_data"], "hs:hold:ep87")

    def test_poll_callbacks_allowlist_and_parse(self):
        import os
        from unittest.mock import patch
        from gen.hero_shorts import approval
        updates = [
            {"update_id": 5, "callback_query": {"id": "cb1", "data": "hs:approve:ep87",
             "from": {"username": "master"}, "message": {"message_id": 7, "chat": {"id": 42}}}},
            {"update_id": 6, "callback_query": {"id": "cb2", "data": "hs:approve:ep87",
             "message": {"message_id": 7, "chat": {"id": 999}}}},   # 허용 밖 채팅
            {"update_id": 7, "callback_query": {"id": "cb3", "data": "garbage",
             "message": {"message_id": 7, "chat": {"id": 42}}}},    # 형식 불량
        ]
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "T", "HERO_TELEGRAM_CHAT_ID": "42"}), \
             patch("gen.hero_shorts.approval._call_api") as api:
            api.side_effect = lambda token, method, params=None, **kw: updates
            decisions = approval.poll_callbacks()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["ep"], 87)
        self.assertEqual(decisions[0]["decision"], "APPROVED")
        self.assertEqual(decisions[0]["decided_by"], "master")

    def test_resolve_approves_and_idempotent(self):
        import os
        from unittest.mock import patch
        from gen.hero_shorts import approval
        state = {"ep87": {"title": "t", "status": "QC_PASSED_READY",
                          "approval": {"status": "READY_FOR_APPROVAL", "requested_at": "2026-09-25T08:00:00+09:00"}}}
        cb = {"update_id": 5, "callback_query": {"id": "cb1", "data": "hs:approve:ep87",
              "from": {"username": "master"}, "message": {"message_id": 7, "chat": {"id": 42}}}}
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "T", "HERO_TELEGRAM_CHAT_ID": "42"}), \
             patch("gen.hero_shorts.approval._call_api") as api:
            api.side_effect = lambda token, method, params=None, **kw: ([cb] if method == "getUpdates" and params and params.get("timeout") == 0 and params.get("offset") is None else {"ok": True} if isinstance(params, dict) else [])
            summary = approval.resolve_pending(state)
            self.assertEqual(summary["decisions"][0]["decision"], "APPROVED")
            self.assertEqual(state["ep87"]["approval"]["status"], "APPROVED")
            self.assertTrue(state["ep87"]["approval"]["decided_at"])
            summary2 = approval.resolve_pending(state)          # 멱등 — decided_at 존재
        self.assertEqual(summary2, {"pending": 0, "decisions": []})

    def test_resolve_hold_decision(self):
        import os
        from unittest.mock import patch
        from gen.hero_shorts import approval
        state = {"ep88": {"approval": {"status": "READY_FOR_APPROVAL", "requested_at": "x"}}}
        cb = {"update_id": 9, "callback_query": {"id": "cb9", "data": "hs:hold:ep88",
              "from": {"username": "master"}, "message": {"message_id": 8, "chat": {"id": 42}}}}
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "T", "HERO_TELEGRAM_CHAT_ID": "42"}), \
             patch("gen.hero_shorts.approval._call_api") as api:
            api.side_effect = lambda token, method, params=None, **kw: ([cb] if method == "getUpdates" and isinstance(params, dict) and params.get("timeout") == 0 and params.get("offset") is None else {"ok": True})
            summary = approval.resolve_pending(state)
        self.assertEqual(state["ep88"]["approval"]["status"], "HOLD")
        self.assertEqual(summary["decisions"][0]["decision"], "HOLD")

    def _prep_publish(self, tmp):
        plan = tmp / "plan.json"
        plan.write_text(json.dumps({"episode": 87, "title": "t", "type": "분석",
                                    "outcome": "V", "source": {}, "cuts": []}), encoding="utf-8")
        cap = tmp / "cap.txt"
        cap.write_text("캡션", encoding="utf-8")
        return str(plan), str(cap)

    def test_publish_blocked_without_approval(self):
        import os
        import tempfile
        from pathlib import Path as _P
        from unittest.mock import patch
        from gen.hero_shorts import pipeline
        with tempfile.TemporaryDirectory() as td:
            tmp = _P(td)
            plan, cap = self._prep_publish(tmp)
            sf = tmp / "publish_state.json"
            sf.write_text(json.dumps({"ep87": {"approval": {"status": "READY_FOR_APPROVAL", "requested_at": "x"}}}), encoding="utf-8")
            with patch.dict(os.environ, {"HERO_STATE_FILE": str(sf), "HERO_STATE_SYNC": "0"}), \
                 patch("gen.hero_shorts.publish_runtime.create_and_monitor_post") as pub, \
                 patch("gen.hero_shorts.assemble_qc.validate_publish_assets"), \
                 patch("gen.hero_shorts.assemble_qc.check_media_url_reachable"):
                with self.assertRaises(RuntimeError) as cm:
                    pipeline.cmd_zernio_publish(plan, "v.mp4", cap, "https://example.com/v.mp4",
                                                qc_result={"G1": 1, "G2": 1, "G3": 1, "G4": 1})
                self.assertIn("승인 게이트", str(cm.exception))
                pub.assert_not_called()

    def test_publish_blocked_gate_env_without_request(self):
        import os
        import tempfile
        from pathlib import Path as _P
        from unittest.mock import patch
        from gen.hero_shorts import pipeline
        with tempfile.TemporaryDirectory() as td:
            tmp = _P(td)
            plan, cap = self._prep_publish(tmp)
            sf = tmp / "publish_state.json"
            sf.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"HERO_STATE_FILE": str(sf), "HERO_STATE_SYNC": "0",
                                         "HERO_APPROVAL_REQUIRED": "1"}), \
                 patch("gen.hero_shorts.publish_runtime.create_and_monitor_post") as pub, \
                 patch("gen.hero_shorts.assemble_qc.validate_publish_assets"), \
                 patch("gen.hero_shorts.assemble_qc.check_media_url_reachable"):
                with self.assertRaises(RuntimeError) as cm:
                    pipeline.cmd_zernio_publish(plan, "v.mp4", cap, "https://example.com/v.mp4",
                                                qc_result={"G1": 1, "G2": 1, "G3": 1, "G4": 1})
                self.assertIn("승인 게이트", str(cm.exception))
                pub.assert_not_called()

    def test_publish_allowed_with_approval(self):
        import os
        import tempfile
        from pathlib import Path as _P
        from unittest.mock import patch
        from gen.hero_shorts import pipeline
        with tempfile.TemporaryDirectory() as td:
            tmp = _P(td)
            plan, cap = self._prep_publish(tmp)
            sf = tmp / "publish_state.json"
            sf.write_text(json.dumps({"ep87": {"approval": {"status": "APPROVED", "decided_at": "y"}}}), encoding="utf-8")
            with patch.dict(os.environ, {"HERO_STATE_FILE": str(sf), "HERO_STATE_SYNC": "0"}), \
                 patch("gen.hero_shorts.publish_runtime.create_and_monitor_post") as pub, \
                 patch("gen.hero_shorts.assemble_qc.validate_publish_assets"), \
                 patch("gen.hero_shorts.assemble_qc.check_media_url_reachable"), \
                 patch("gen.hero_shorts.ledger.update_publish_status"):
                pub.return_value = {"outcome": "published", "post_id": "p1", "post_url": "u1", "snapshot": None}
                result = pipeline.cmd_zernio_publish(plan, "v.mp4", cap, "https://example.com/v.mp4",
                                                     qc_result={"G1": 1, "G2": 1, "G3": 1, "G4": 1})
            self.assertEqual(result["outcome"], "published")
            saved = json.loads(sf.read_text(encoding="utf-8"))
            self.assertEqual(saved["ep87"]["approval"]["status"], "APPROVED")   # 승인 이력 보존
            self.assertEqual(saved["ep87"]["status"], "published")

    def test_publish_backward_compat_no_gate(self):
        import os
        import tempfile
        from pathlib import Path as _P
        from unittest.mock import patch
        from gen.hero_shorts import pipeline
        with tempfile.TemporaryDirectory() as td:
            tmp = _P(td)
            plan, cap = self._prep_publish(tmp)
            sf = tmp / "publish_state.json"
            sf.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"HERO_STATE_FILE": str(sf), "HERO_STATE_SYNC": "0"}), \
                 patch("gen.hero_shorts.publish_runtime.create_and_monitor_post") as pub, \
                 patch("gen.hero_shorts.assemble_qc.validate_publish_assets"), \
                 patch("gen.hero_shorts.assemble_qc.check_media_url_reachable"), \
                 patch("gen.hero_shorts.ledger.update_publish_status"):
                pub.return_value = {"outcome": "published", "post_id": "p2", "post_url": "u2", "snapshot": None}
                result = pipeline.cmd_zernio_publish(plan, "v.mp4", cap, "https://example.com/v.mp4",
                                                     qc_result={"G1": 1, "G2": 1, "G3": 1, "G4": 1})
            self.assertEqual(result["outcome"], "published")                     # 기존 수동 흐름 유지

class TestV280TtsUpgrade(unittest.TestCase):
    """v2.8.0 — 나레이션 고정화자·2인 대사·BGM 덕킹·QC 확장."""

    def _plan(self, tmp, with_dialogue=False):
        cuts = [
            {"cut_no": i, "role": role, "request": {"prompt": f"p{i}", "duration": 8,
                                                    "resolution": "1080x1920", "aspect_ratio": "9:16"}}
            for i, role in enumerate(["ESTABLISH_THREAT", "TURN"], start=1)
        ]
        if with_dialogue:
            for c in cuts:
                c["dialogue"] = {"lines": [
                    {"role": "NARRATOR", "text": "시장이 조용히 움직인다."},
                    {"role": "CHAR1", "text": "[sarcastic] 또 처음이네."},
                ]}
        plan_file = tmp / "plan.json"
        plan_file.write_text(json.dumps({
            "episode": 88, "title": "테스트", "type": "SWING", "outcome": "HOLD", "cuts": cuts,
        }, ensure_ascii=False), encoding="utf-8")
        return plan_file

    def test_resolve_cast_mapping(self):
        self.assertEqual(voiceover.parse_cast("CHAR1=Eve,CHAR2=Terry"), {"CHAR1": "Eve", "CHAR2": "Terry"})
        self.assertEqual(voiceover.parse_cast(""), {})
        self.assertEqual(voiceover.speaker_for_line("NARRATOR"), os.environ.get("HERO_TTS_SPEAKER") or "Austin")

    def test_build_dialogue_plan_v2_lines(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            os.environ["HERO_TTS_CAST"] = "CHAR1=Eve"
            try:
                payload = voiceover.build_dialogue_plan(json.loads(self._plan(tmp, with_dialogue=True).read_text(encoding="utf-8")))
            finally:
                os.environ.pop("HERO_TTS_CAST", None)
            lines = payload["cuts"][0]["lines"]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["speaker"], os.environ.get("HERO_TTS_SPEAKER") or "Austin")
            self.assertEqual(lines[1]["speaker"], "Eve")

    def test_character_line_without_cast_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            os.environ.pop("HERO_TTS_CAST", None)
            payload = json.loads(self._plan(tmp, with_dialogue=True).read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                voiceover.build_dialogue_plan(payload)

    def test_auto_dialogue_env_optin(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            os.environ["HERO_TTS_DIALOGUE"] = "1"
            os.environ["HERO_TTS_CAST"] = "CHAR1=Samara"
            try:
                payload = voiceover.build_dialogue_plan(json.loads(self._plan(tmp).read_text(encoding="utf-8")))
            finally:
                os.environ.pop("HERO_TTS_DIALOGUE", None)
                os.environ.pop("HERO_TTS_CAST", None)
            self.assertEqual(len(payload["cuts"][0]["lines"]), 2)
            self.assertIn("[", payload["cuts"][0]["lines"][1]["text"])  # 감정 태그 포함 단상

    def test_gsk_tts_dialogue_params_format(self):
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = cmd
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"RIFF-fake")
            class R: returncode = 0; stdout = ""; stderr = ""
            return R()
        with mock.patch.object(voiceover.subprocess, "run", side_effect=fake_run):
            voiceover._gsk_tts_dialogue(
                [{"role": "NARRATOR", "text": "a", "speaker": "Austin"},
                 {"role": "CHAR1", "text": "b", "speaker": "Eve"}], "/tmp/v280_dlg.wav")
        cmd = calls["cmd"]
        self.assertIn("Speaker1: a", cmd[2])
        self.assertIn("Speaker2: b", cmd[2])
        params = json.loads(cmd[cmd.index("-p") + 1])
        self.assertEqual(params["speakers"], [
            {"speaker": "Speaker1", "voice_name": "Austin"},
            {"speaker": "Speaker2", "voice_name": "Eve"},
        ])

    def test_gsk_tts_dialogue_more_than_two_fails(self):
        with self.assertRaises(RuntimeError):
            voiceover._gsk_tts_dialogue(
                [{"text": "a", "speaker": "Austin"}, {"text": "b", "speaker": "Eve"},
                 {"text": "c", "speaker": "Terry"}], "/tmp/v280_x.wav")

    def test_gsk_tts_tier_and_stability_params(self):
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = cmd
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"RIFF-fake")
            class R: returncode = 0; stdout = ""; stderr = ""
            return R()
        old = (os.environ.get("HERO_TTS_TIER"), os.environ.get("HERO_TTS_STABILITY"))
        os.environ["HERO_TTS_TIER"] = "turbo"
        os.environ["HERO_TTS_STABILITY"] = "0.65"
        try:
            with mock.patch.object(voiceover.subprocess, "run", side_effect=fake_run):
                voiceover._gsk_tts("나레이션", "/tmp/v280_n.wav", speaker="Austin")
        finally:
            for k, v in (("HERO_TTS_TIER", old[0]), ("HERO_TTS_STABILITY", old[1])):
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        params = json.loads(calls["cmd"][calls["cmd"].index("-p") + 1])
        self.assertEqual(params["tier"], "turbo")
        self.assertEqual(params["stability"], 0.65)

    def test_generate_bgm_forces_instrumental_and_duration(self):
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = cmd
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"RIFF-fake")
            class R: returncode = 0; stdout = ""; stderr = ""
            return R()
        with mock.patch.object(voiceover.subprocess, "run", side_effect=fake_run):
            voiceover.generate_bgm("/tmp/v280_bgm.mp3", 45, prompt="dark tension, 90 BPM")
        self.assertEqual(calls["cmd"][calls["cmd"].index("-m") + 1], "elevenlabs/music")
        self.assertIn("instrumental only", calls["cmd"][2])
        params = json.loads(calls["cmd"][calls["cmd"].index("-p") + 1])
        self.assertEqual(params["duration"], 45)

    def test_generate_bgm_empty_prompt_fails_closed(self):
        os.environ.pop("HERO_BGM_PROMPT", None)
        with self.assertRaises(RuntimeError):
            voiceover.generate_bgm("/tmp/v280_bgm.mp3", 30)

    def test_dummy_multiline_concat_distinct_freqs(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            a = tmp / "l0.wav"; b = tmp / "l1.wav"
            voiceover._dummy_tts("가나다", a, 2.0, freq_offset=0)
            voiceover._dummy_tts("가나다", b, 2.0, freq_offset=90)
            self.assertNotEqual(voiceover.probe_duration(a), 0)
            merged = tmp / "m.wav"
            voiceover.concat_audio([str(a), str(b)], merged)
            self.assertGreaterEqual(voiceover.probe_duration(merged), 3.9)

    def test_mix_with_bgm_ducking(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            video = tmp / "v.mp4"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=256x256:rate=15:duration=4",
                            "-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], capture_output=True)
            voice = tmp / "voice.wav"; bgm = tmp / "bgm.wav"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=4", str(voice)],
                           capture_output=True)
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=90:duration=6", str(bgm)],
                           capture_output=True)
            out = tmp / "voiced.mp4"
            voiceover.mix_voice_over(video, voice, out, bgm_audio=bgm)
            self.assertTrue(out.exists())
            self.assertTrue(voiceover.has_audio_stream(out))
            self.assertLess(abs(voiceover.probe_duration(out) - 4.0), 1.0)
            qc = voiceover.verify_audio_outputs(out)
            self.assertTrue(qc["audible"])

    def test_mix_bgm_missing_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            video = tmp / "v.mp4"; voice = tmp / "voice.wav"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], capture_output=True)
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=1", str(voice)],
                           capture_output=True)
            with self.assertRaises(RuntimeError):
                voiceover.mix_voice_over(video, voice, tmp / "o.mp4", bgm_audio=tmp / "nope.wav")

    def test_verify_audio_outputs_flags_silence(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            silent = tmp / "silence.wav"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1", str(silent)],
                           capture_output=True)
            qc = voiceover.verify_audio_outputs(silent)
            self.assertFalse(qc["audible"])

    def test_synthesize_dummy_backend_with_lines_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            os.environ["HERO_TTS_CAST"] = "CHAR1=Eve"
            try:
                plan_file = self._plan(tmp, with_dialogue=True)
                vp = voiceover.save_dialogue_plan(plan_file, tmp / "vp.json")
                manifest = voiceover.synthesize_tts_clips(vp, tmp, backend="dummy")
            finally:
                os.environ.pop("HERO_TTS_CAST", None)
            self.assertEqual(len(manifest["clips"][0]["lines"]), 2)
            self.assertTrue(Path(manifest["merged_audio"]).exists())
            self.assertIsNone(manifest["bgm_file"])  # dummy 백엔드는 BGM 미생성(비용 0 경로)

unittest.main(verbosity=2)
