"""
gen/hero_shorts/tests/test_publish_targets_v2.py — 유튜브·쓰레드 타깃 계층 테스트 (W5~W8)
=======================================================================================
설계서 §9 테스트 계획 T1~T13 중 **오프라인으로 검증 가능한 전건**.
발행(네트워크)은 하지 않는다 — transport 는 주입식 opener/runner 로 대체한다.

T8 이 최우선이다: HTTP 207 부분실패를 성공으로 기록하면 안 된다(설계서 §6.2 · 리스크 "높음").
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gen.hero_shorts.publish_targets import (  # noqa: E402
    caption_plan,
    common_gates,
    get_target,
    ledger_v2,
    planner,
    registry,
)
from gen.hero_shorts.publish_targets import threads as threads_pkg  # noqa: E402
from gen.hero_shorts.publish_targets import youtube as youtube_pkg  # noqa: E402
from gen.hero_shorts.publish_targets.base import media_facts, ep_label  # noqa: E402
from gen.hero_shorts.publish_targets.transport import (  # noqa: E402
    UnsupportedPlatformError,
    ZernioCliTransport,
    ZernioRestTransport,
    build_request,
    decompose_response,
)


def make_record(**over):
    """실측 ep88 구조를 따른 기준 레코드(제목만 결함 없는 값)."""
    rec = {
        "title": "물러서지 않는 두 전선",
        "type": "AFTERMATH",
        "status": "QC_PASSED_READY",
        "qc": {"G1": True, "G2": True,
               "G3": {"w": 720, "h": 1280, "duration": 30.433, "audio": 1}, "G4": True},
        "combined": {"title": "물러서지 않는 두 전선", "duration_sec": 30.433,
                     "media_url": "https://example.invalid/ep89.mp4",
                     "video_path": None},
        "approval": {"status": "APPROVED"},
    }
    rec.update(over)
    return rec


class FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._b = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeCP:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# --------------------------------------------------------------------------- #
# T1 — 공통 게이트
# --------------------------------------------------------------------------- #
class CommonGateTest(unittest.TestCase):
    def test_t1_each_common_gate_fails_independently(self):
        base = dict(media_url="https://x/y.mp4", duration_sec=30.0, ratio="9:16",
                    media_kind="video", texts=["⚠️ 투자 참고 정보, 투자 권유 아님"],
                    text_hits=[], approval_status="APPROVED")
        self.assertTrue(common_gates.run_common_gates(**base)["ok"])
        for key, bad in (("media_url", None), ("duration_sec", 0), ("ratio", "16:9"),
                         ("media_kind", "image")):
            with self.subTest(key=key):
                r = common_gates.run_common_gates(**{**base, key: bad})
                self.assertFalse(r["ok"])
                self.assertIn("C-G1", r["failed"])
        r = common_gates.run_common_gates(**{**base, "texts": ["면책문 없음 본문"]})
        self.assertIn("C-G2", r["failed"])
        r = common_gates.run_common_gates(**{**base, "texts": ["무조건 오른다"]})
        self.assertIn("C-G3", r["failed"])
        r = common_gates.run_common_gates(**{**base, "text_hits": ["화면 자막"]})
        self.assertIn("C-G4", r["failed"])
        r = common_gates.run_common_gates(**{**base, "approval_status": "HOLD"})
        self.assertIn("C-G5", r["failed"])


# --------------------------------------------------------------------------- #
# T2·T3 — 유튜브 게이트
# --------------------------------------------------------------------------- #
class YoutubeGateTest(unittest.TestCase):
    def test_t2_label_only_title_blocked(self):
        """F6 실물 — ep88 레코드 title 이 문자 그대로 'Ep88' 이다."""
        g = youtube_pkg.gates.gate_yg3("Ep88", "Ep88")
        self.assertFalse(g["ok"])
        self.assertTrue(any("회차 태그 단독" in p for p in g["problems"]))
        for variant in ("ep 88", "#EP-88", "Ep88 ", "EP88"):
            with self.subTest(variant=variant):
                self.assertFalse(youtube_pkg.gates.gate_yg3(variant, "Ep88")["ok"])
        # 정상 제목은 통과
        self.assertTrue(youtube_pkg.gates.gate_yg3("Ep88 물러서지 않는 두 전선", "Ep88")["ok"])

    def test_t2b_source_title_checked_not_only_composed(self):
        """합성 제목이 'Ep88 Ep88' 이 되어도 **원본**이 라벨이면 차단돼야 한다."""
        g = youtube_pkg.gates.gate_yg3("Ep88 Ep88", "Ep88", source_title="Ep88")
        self.assertFalse(g["ok"])
        self.assertTrue(any("데이터 결함" in p for p in g["problems"]))
        # 원본이 정상이면 합성 제목만으로는 차단하지 않는다(오탐 방지)
        self.assertTrue(youtube_pkg.gates.gate_yg3(
            "Ep88 물러서지 않는 두 전선", "Ep88",
            source_title="물러서지 않는 두 전선")["ok"])

    def test_t3_duration_boundary(self):
        for value, ok in ((179, True), (180, True), (181, False)):
            with self.subTest(duration=value):
                self.assertEqual(youtube_pkg.gates.gate_yg1(value, "9:16")["ok"], ok)

    def test_yg4_yg5_yg6_limits(self):
        self.assertFalse(youtube_pkg.gates.gate_yg4("unlisted")["ok"])
        self.assertTrue(youtube_pkg.gates.gate_yg4("unlisted",
                                                   regular_publish=False)["ok"])
        self.assertFalse(youtube_pkg.gates.gate_yg4("bogus")["ok"])
        self.assertTrue(youtube_pkg.gates.gate_yg5(["a" * 100])["ok"])
        self.assertFalse(youtube_pkg.gates.gate_yg5(["a" * 101])["ok"])
        self.assertFalse(youtube_pkg.gates.gate_yg5(["a" * 100] * 6)["ok"])
        self.assertTrue(youtube_pkg.gates.gate_yg6("가" * 5000)["ok"])
        self.assertFalse(youtube_pkg.gates.gate_yg6("가" * 5001)["ok"])

    def test_yg2_synthetic_media_required(self):
        self.assertTrue(youtube_pkg.gates.gate_yg2(True)["ok"])
        self.assertFalse(youtube_pkg.gates.gate_yg2(False)["ok"])
        self.assertFalse(youtube_pkg.gates.gate_yg2(None)["ok"])

    def test_youtube_title_first_line_matches(self):
        proj = caption_plan.build_projections(make_record(), ep_label="Ep89")["youtube"]
        self.assertTrue(youtube_pkg.payload.first_line_matches_title(proj))

    def test_youtube_full_gate_pass(self):
        proj = caption_plan.build_projections(make_record(), ep_label="Ep89")["youtube"]
        g = youtube_pkg.gates.run_youtube_gates(
            duration_sec=30.4, ratio="9:16", title=proj["title"], ep_label_value="Ep89",
            visibility="public", tags=proj["tags"], description=proj["content"])
        self.assertTrue(g["ok"], g["failed"])


# --------------------------------------------------------------------------- #
# T4·T5 — 쓰레드 게이트
# --------------------------------------------------------------------------- #
class ThreadsGateTest(unittest.TestCase):
    def test_t4_text_boundary(self):
        for n, ok in ((499, True), (500, True), (501, False)):
            with self.subTest(length=n):
                self.assertEqual(threads_pkg.gates.gate_thg2("가" * n)["ok"], ok)

    def test_t5_topic_tag_forbidden_chars(self):
        self.assertTrue(threads_pkg.gates.gate_thg3("JCUniverse")["ok"])
        for bad in ("JCU.niverse", "A&B", "", "가" * 51):
            with self.subTest(tag=bad):
                self.assertFalse(threads_pkg.gates.gate_thg3(bad)["ok"])

    def test_thg1_ratio_and_duration(self):
        self.assertTrue(threads_pkg.gates.gate_thg1(300, "9:16")["ok"])
        self.assertFalse(threads_pkg.gates.gate_thg1(301, "9:16")["ok"])
        for ratio in ("9:16", "16:9", "1:1"):
            self.assertTrue(threads_pkg.gates.gate_thg1(10, ratio)["ok"])
        self.assertFalse(threads_pkg.gates.gate_thg1(10, "4:5")["ok"])

    def test_thg4_and_thg5(self):
        self.assertTrue(threads_pkg.gates.gate_thg4(1024 ** 3)["ok"])
        self.assertFalse(threads_pkg.gates.gate_thg4(1024 ** 3 + 1)["ok"])
        self.assertFalse(threads_pkg.gates.gate_thg4(10, video_codec="hevc")["ok"])
        self.assertTrue(threads_pkg.gates.gate_thg5(None)["ok"])
        self.assertFalse(threads_pkg.gates.gate_thg5("가" * 501)["ok"])

    def test_thq_quota_blocks(self):
        self.assertTrue(threads_pkg.gates.gate_thq(249)["ok"])
        self.assertFalse(threads_pkg.gates.gate_thq(250)["ok"])


# --------------------------------------------------------------------------- #
# T6·T7 — 캡션 3투영
# --------------------------------------------------------------------------- #
class CaptionPlanTest(unittest.TestCase):
    def test_t6_deterministic(self):
        a = caption_plan.build_projections(make_record(), ep_label="Ep89")
        b = caption_plan.build_projections(make_record(), ep_label="Ep89")
        self.assertEqual(json.dumps(a, ensure_ascii=False, sort_keys=True),
                         json.dumps(b, ensure_ascii=False, sort_keys=True))

    def test_t7_threads_compression_then_over_limit(self):
        # 훅 포함 시 500자 초과 → 훅 제거로 해결되는 구간(T=420, 훅=40자)
        text, compressions, over = caption_plan.threads_content("가" * 420, hook="나" * 40)
        self.assertIn("hook_removed", compressions)
        self.assertFalse(over)
        self.assertLessEqual(len(text), caption_plan.THREADS_TEXT_LIMIT)

        # 훅 제거로도 해결 불가 → 압축 단계 전진 후 over_limit=True 반환(판정은 TH-G2)
        text2, compressions2, over2 = caption_plan.threads_content("가" * 600, hook=None)
        self.assertTrue(over2)
        self.assertIn("hashtags_removed", compressions2)
        self.assertFalse(threads_pkg.gates.gate_thg2(text2)["ok"])
        self.assertTrue(threads_pkg.gates.gate_thg2(text2)["length"] > 500)

    def test_yt_title_truncates_keeping_ep_title(self):
        title = caption_plan.youtube_title("Ep89", "나" * 200)
        self.assertLessEqual(len(title), caption_plan.YOUTUBE_TITLE_LIMIT)
        self.assertTrue(title.startswith("Ep89 "))

    def test_no_title_raises(self):
        with self.assertRaises(ValueError):
            caption_plan.build_projections({"title": ""}, ep_label="Ep89")

    def test_topic_tag_sanitized(self):
        self.assertEqual(caption_plan.sanitize_topic_tag("JCU.niverse"), "JCUniverse")
        self.assertEqual(caption_plan.sanitize_topic_tag("A&B"), "AB")


# --------------------------------------------------------------------------- #
# T8 — 전송 어댑터: HTTP 207 부분실패를 성공으로 기록하지 않는다 (최우선)
# --------------------------------------------------------------------------- #
class TransportPartialFailureTest(unittest.TestCase):
    def setUp(self):
        os.environ["ZERNIO_API_KEY"] = "test-key-not-a-real-one"

    def tearDown(self):
        os.environ.pop("ZERNIO_API_KEY", None)

    def _transport(self, status, payload):
        return ZernioRestTransport(api_key="k", opener=lambda req, timeout=None: FakeResp(status, payload))

    def test_t8_207_partial_failure_not_recorded_as_success(self):
        body = {"data": {"post": {"_id": "p1", "status": "failed", "platforms": [
            {"platform": "youtube", "status": "published",
             "platformPostUrl": "https://youtu.be/abcdefghijk"},
            {"platform": "threads", "status": "failed",
             "error": "Param text must be at most 500 characters long."},
        ]}}}
        resp = self._transport(207, body).send({}, platforms=["youtube", "threads"])
        self.assertTrue(resp.ok_envelope)          # 2xx 다 — 함정
        self.assertTrue(resp.partial_failure)
        d = decompose_response(resp, ["youtube", "threads"])
        self.assertEqual(d["youtube"]["outcome"], "published")
        self.assertEqual(d["threads"]["outcome"], "failed")

    def test_t8_207_missing_entry_never_published(self):
        """유튜브 엔트리가 없는 207 — 다른 플랫폼 성공을 승계하면 안 된다."""
        body = {"data": {"post": {"_id": "p2", "status": "failed", "platforms": [
            {"platform": "threads", "status": "failed", "error": "boom"},
        ]}}}
        resp = self._transport(207, body).send({}, platforms=["youtube", "threads"])
        d = decompose_response(resp, ["youtube", "threads"])
        self.assertNotEqual(d["youtube"]["outcome"], "published")
        self.assertEqual(d["youtube"]["outcome"], "failed")

    def test_youtube_success_requires_youtube_host(self):
        body = {"data": {"post": {"_id": "p3", "status": "published", "platforms": [
            {"platform": "youtube", "status": "published",
             "platformPostUrl": "https://www.instagram.com/reel/xyz"},
        ]}}}
        resp = self._transport(200, body).send({}, platforms=["youtube"])
        d = decompose_response(resp, ["youtube"])
        self.assertEqual(d["youtube"]["outcome"], "pending")   # 호스트 불일치 → 미확정

    def test_http_4xx_is_failure(self):
        resp = self._transport(400, {"error": "bad"}).send({}, platforms=["youtube"])
        d = decompose_response(resp, ["youtube"])
        self.assertEqual(d["youtube"]["outcome"], "failed")

    def test_cli_transport_rejects_youtube_and_threads(self):
        t = ZernioCliTransport(runner=lambda a: FakeCP())
        for plat in ("youtube", "threads"):
            with self.subTest(platform=plat):
                with self.assertRaises(UnsupportedPlatformError):
                    t.send({}, platforms=[plat])

    def test_build_request_shape(self):
        body = build_request("https://x/y.mp4", [{"platform": "youtube"}])
        self.assertEqual(body["mediaItems"], [{"url": "https://x/y.mp4", "type": "video"}])
        self.assertTrue(body["publishNow"])
        self.assertEqual(planner.__name__.split(".")[-1], "planner")


# --------------------------------------------------------------------------- #
# T9·T10 — 플래너 (멱등성)
# --------------------------------------------------------------------------- #
class PlannerTest(unittest.TestCase):
    def setUp(self):
        os.environ["HERO_YOUTUBE_ACCOUNT_ID"] = "yt-account-fixture"
        os.environ["HERO_THREADS_ACCOUNT_ID"] = "th-account-fixture"
        # TH-G4 파일 크기는 실측 주입 — 미측정이면 게이트가 차단한다(fail-closed).
        self.targets = planner.make_targets(
            ["youtube", "threads"], threads={"file_bytes": 10 * 1024 * 1024})

    def tearDown(self):
        os.environ.pop("HERO_YOUTUBE_ACCOUNT_ID", None)
        os.environ.pop("HERO_THREADS_ACCOUNT_ID", None)

    def test_t9_only_unpublished_target_is_pending(self):
        rec = make_record(targets={
            "youtube": {"status": "published", "attempts": []},
            "threads": {"status": "failed", "attempts": [{"at": "x", "outcome": "failed"}]},
        })
        p = planner.plan_episode(rec, "ep89", targets=self.targets,
                                 platforms=["youtube", "threads"])
        self.assertEqual(p["pending"], ["threads"])
        self.assertEqual(p["skipped"], ["youtube"])
        self.assertTrue(p["plans"]["threads"].payload)

    def test_t10_both_published_nothing_pending(self):
        rec = make_record(targets={
            "youtube": {"status": "published", "attempts": []},
            "threads": {"status": "published", "attempts": []},
        })
        p = planner.plan_episode(rec, "ep89", targets=self.targets,
                                 platforms=["youtube", "threads"])
        self.assertEqual(p["pending"], [])
        self.assertEqual(sorted(p["skipped"]), ["threads", "youtube"])

    def test_retry_cap_excludes_target(self):
        rec = make_record(targets={
            "youtube": {"status": "failed", "attempts": [{"at": "1"}, {"at": "2"}, {"at": "3"}]},
        })
        p = planner.plan_episode(rec, "ep89", targets=self.targets,
                                 platforms=["youtube", "threads"])
        self.assertNotIn("youtube", p["pending"])
        self.assertIn("youtube", p["skipped"])

    def test_blocked_target_has_no_payload_and_is_not_sent(self):
        rec = make_record(targets={}, approval={"status": "HOLD"})   # C-G5 실패
        p = planner.plan_episode(rec, "ep89", targets=self.targets,
                                 platforms=["youtube", "threads"])
        self.assertEqual(p["pending"], [])
        self.assertEqual(sorted(p["blocked"]), ["threads", "youtube"])
        self.assertEqual(p["plans"]["youtube"].payload, {})

        class Boom:
            def send(self, *a, **k):
                raise AssertionError("게이트 미통과인데 전송했다")

        batch = planner.publish_batch(p["plans"], transport=Boom(),
                                      media_url="https://x/y.mp4",
                                      platforms=["youtube", "threads"])
        self.assertEqual(batch["sent"], [])
        self.assertEqual({r.outcome for r in batch["results"].values()}, {"blocked"})

    def test_missing_account_fails_closed(self):
        os.environ.pop("HERO_YOUTUBE_ACCOUNT_ID", None)
        rec = make_record()
        p = planner.plan_episode(rec, "ep89", targets=self.targets,
                                 platforms=["youtube"])
        plan = p["plans"]["youtube"]
        self.assertFalse(plan.gate["ok"])
        self.assertIn("HERO_YOUTUBE_ACCOUNT_ID", " ".join(plan.gate["problems"]))
        self.assertTrue(plan.gate["blocked"])

    def test_ep88_like_label_title_is_blocked_for_youtube_only(self):
        """제목이 'Ep88' 인 회차 — 유튜브만 차단, 쓰레드는 통과(설계 의도)."""
        rec = make_record(title="Ep88")
        p = planner.plan_episode(rec, "ep88", targets=self.targets,
                                 platforms=["youtube", "threads"])
        self.assertIn("youtube", p["blocked"])
        self.assertIn("threads", p["pending"])

    def test_summary_counts(self):
        rec = make_record()
        p = planner.plan_episode(rec, "ep89", targets=self.targets,
                                 platforms=["youtube", "threads"])

        class OneOk:
            def send(self, body, platforms=(), **kwargs):
                from gen.hero_shorts.publish_targets.transport import TransportResponse
                return TransportResponse(
                    transport="fake", http_status=207,
                    body={"data": {"post": {"_id": "p9", "status": "failed", "platforms": [
                        {"platform": "youtube", "status": "published",
                         "platformPostUrl": "https://youtu.be/abcdefghijk"},
                        {"platform": "threads", "status": "failed", "error": "boom"},
                    ]}}})

        batch = planner.publish_batch(p["plans"], transport=OneOk(),
                                      media_url="https://x/y.mp4",
                                      platforms=["youtube", "threads"])
        s = planner.summary(p["plans"], batch["results"])
        self.assertEqual(s["published"], ["youtube"])
        self.assertEqual(s["failed"], ["threads"])


# --------------------------------------------------------------------------- #
# T11·T12 — 원장 v2 마이그레이션
# --------------------------------------------------------------------------- #
class LedgerV2Test(unittest.TestCase):
    def _write(self, doc):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump(doc, tmp, ensure_ascii=False)
        tmp.close()
        return tmp.name

    def test_t11_migrate_is_idempotent(self):
        path = self._write({
            "ep88": {"title": "Ep88", "tiktok": {"status": "published", "post_id": "z1"},
                     "combined": {"duration_sec": 30.4}},
        })
        first = ledger_v2.migrate(path, apply=True)
        self.assertTrue(first["changed"])
        self.assertTrue(first["tiktok_ok"])
        second = ledger_v2.migrate(path, apply=True)
        self.assertFalse(second["changed"])
        self.assertEqual(second["report"]["targets_added"], [])
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(doc["_schema"], 2)
        self.assertEqual(sorted(doc["ep88"]["targets"].keys()), ["threads", "youtube"])
        self.assertEqual(doc["ep88"]["tiktok"]["post_id"], "z1")
        os.unlink(path)
        os.path.exists(path + ".bak") and os.unlink(path + ".bak")

    def test_t12_tiktok_block_change_aborts(self):
        before = {"ep88": {"tiktok": {"status": "published"}}}
        after = {"ep88": {"tiktok": {"status": "pending"}}}
        with self.assertRaises(RuntimeError):
            ledger_v2.assert_tiktok_unchanged(ledger_v2.tiktok_hashes(before),
                                             ledger_v2.tiktok_hashes(after))

    def test_plan_pending_skips_blocked_and_published(self):
        doc = {"ep1": {"targets": {"youtube": {"status": "published", "attempts": []},
                                   "threads": {"status": "blocked", "attempts": []}}},
               "ep2": {"targets": {"youtube": {"status": "pending", "attempts": []},
                                   "threads": {"status": "failed", "attempts": []}}}}
        pending = ledger_v2.plan_pending(doc)
        self.assertEqual([(p["ep"], p["platform"]) for p in pending],
                         [("ep2", "youtube"), ("ep2", "threads")])


# --------------------------------------------------------------------------- #
# T13 — 검증 (URL → ID)
# --------------------------------------------------------------------------- #
class VerifyTest(unittest.TestCase):
    def test_t13_youtube_video_id_extraction(self):
        f = youtube_pkg.verify.extract_video_id
        for url in ("https://youtu.be/abcdefghijk",
                    "https://www.youtube.com/watch?v=abcdefghijk&t=1",
                    "https://www.youtube.com/shorts/abcdefghijk",
                    "https://m.youtube.com/embed/abcdefghijk"):
            with self.subTest(url=url):
                self.assertEqual(f(url), "abcdefghijk")
        for bad in (None, "", "https://example.com/abcdefghijk",
                    "https://youtu.be/short", "https://www.instagram.com/reel/x"):
            with self.subTest(url=bad):
                self.assertIsNone(f(bad))

    def test_threads_code_extraction_is_marked_unconfirmed(self):
        f = threads_pkg.verify.extract_post_code
        self.assertEqual(f("https://www.threads.net/@tiger/post/ABC123xyz"), "ABC123xyz")
        self.assertIsNone(f("https://example.com/@tiger/post/ABC123xyz"))
        self.assertFalse(threads_pkg.verify.URL_FORMAT_CONFIRMED)

    def test_verify_records_mismatch(self):
        from gen.hero_shorts.publish_targets.base import TargetResult
        rec = {"targets": {"youtube": {"platform_post_id": "zzzzzzzzzzz",
                                      "platform_post_url": None}}}
        res = TargetResult(platform="youtube", outcome="published",
                           platform_post_url="https://youtu.be/abcdefghijk")
        out = youtube_pkg.verify.verify(rec, res)
        self.assertEqual(out["platform_post_id"], "abcdefghijk")
        self.assertTrue(any("!=" in p for p in out["problems"]))


# --------------------------------------------------------------------------- #
# 레지스트리 · 실측 헬퍼
# --------------------------------------------------------------------------- #
class RegistryAndFactsTest(unittest.TestCase):
    def test_registry_exposes_both_targets(self):
        self.assertEqual(sorted(registry().keys()), ["threads", "youtube"])
        self.assertEqual(get_target("youtube").platform, "youtube")
        self.assertEqual(get_target("threads").env_account_key, "HERO_THREADS_ACCOUNT_ID")
        with self.assertRaises(ValueError):
            get_target("tiktok")

    def test_ep_label_and_media_facts(self):
        self.assertEqual(ep_label("ep89"), "Ep89")
        self.assertEqual(ep_label("weird"), "weird")
        self.assertEqual(media_facts(make_record())["ratio"], "9:16")
        self.assertEqual(media_facts(make_record())["duration_sec"], 30.433)
        self.assertIsNone(media_facts({})["duration_sec"])


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# T14 — Zernio REST 스펙 정합성 (2026-10-18 docs.zernio.com 1차 출처 확정분)
#   이 클래스는 "코드가 실제 API 스펙과 어긋나지 않는가"를 고정한다.
#   2026-10-18 이전 코드는 mediaUrls · scheduledAt · platformSpecificData.text 를
#   쓰고 있었고 셋 다 스펙과 달랐다 — 재발 방지용 잠금이다.
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, payload=b"{}", status=200):
        self._payload, self.status = payload, status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SpecConformanceTest(unittest.TestCase):
    def _proj(self, platform, ep="Ep89"):
        return caption_plan.build_projections(make_record(), ep_label=ep)[platform]

    # --- 요청 봉투 ---------------------------------------------------------- #
    def test_media_items_not_media_urls(self):
        body = build_request("https://x/y.mp4", [{"platform": "youtube"}])
        self.assertIn("mediaItems", body)
        self.assertNotIn("mediaUrls", body)
        self.assertEqual(body["mediaItems"][0]["url"], "https://x/y.mp4")
        self.assertEqual(body["mediaItems"][0]["type"], "video")

    def test_media_url_must_be_https(self):
        from gen.hero_shorts.publish_targets.transport import TransportError
        with self.assertRaises(TransportError):
            build_request("http://x/y.mp4", [{"platform": "youtube"}])

    def test_scheduled_for_and_timezone_not_scheduled_at(self):
        body = build_request("https://x/y.mp4", [{"platform": "youtube"}],
                             publish_now=True,
                             scheduled_for="2026-10-20T10:30:00+09:00",
                             timezone="Asia/Seoul")
        self.assertEqual(body["scheduledFor"], "2026-10-20T10:30:00+09:00")
        self.assertEqual(body["timezone"], "Asia/Seoul")
        self.assertNotIn("scheduledAt", body)
        self.assertNotIn("publishNow", body)   # 스펙: 둘 중 하나만

    def test_publish_now_default_path(self):
        body = build_request("https://x/y.mp4", [{"platform": "youtube"}])
        self.assertTrue(body["publishNow"])
        self.assertNotIn("scheduledFor", body)

    def test_entry_without_platform_rejected(self):
        from gen.hero_shorts.publish_targets.transport import TransportError
        with self.assertRaises(TransportError):
            build_request("https://x/y.mp4", [{"accountId": "x"}])

    # --- 플랫폼 엔트리 배치 -------------------------------------------------- #
    def test_youtube_entry_layout(self):
        e = youtube_pkg.payload.platform_entry("yt-acc", self._proj("youtube"))
        self.assertEqual(e["platform"], "youtube")
        self.assertEqual(e["accountId"], "yt-acc")
        self.assertIn("customContent", e)
        psd = e["platformSpecificData"]
        for k in ("title", "visibility", "categoryId", "madeForKids",
                  "containsSyntheticMedia"):
            self.assertIn(k, psd)
        self.assertNotIn("description", psd)   # 스펙: 설명은 content 다
        self.assertNotIn("tags", psd)          # 스펙: 태그는 최상위다

    def test_threads_entry_layout(self):
        e = threads_pkg.payload.platform_entry("th-acc", self._proj("threads"))
        self.assertEqual(e["platform"], "threads")
        self.assertIn("customContent", e)
        self.assertNotIn("text", e.get("platformSpecificData") or {})
        self.assertEqual(e["platformSpecificData"]["topic_tag"], "JCUniverse")

    def test_threads_content_within_500(self):
        txt = threads_pkg.payload.content_text(self._proj("threads"))
        self.assertLessEqual(len(txt), 500)

    def test_threads_over_500_rejected(self):
        with self.assertRaises(ValueError):
            threads_pkg.payload.content_text({"content": "가" * 501})

    # --- 유튜브 제목 · 태그 -------------------------------------------------- #
    def test_youtube_content_first_line_is_title(self):
        proj = self._proj("youtube")
        txt = youtube_pkg.payload.content_text(proj)
        self.assertEqual(txt.splitlines()[0].strip(), proj["title"])

    def test_youtube_tags_truncation_rules(self):
        tags = youtube_pkg.payload.tags_of(
            {"title": "T", "content": "T",
             "tags": ["a" * 101, "b" * 10, "c" * 500]})
        self.assertEqual(tags, ["b" * 10])   # 101자 탈락 · 합계 500자에서 중단

    def test_youtube_first_comment_limit_is_10000(self):
        proj = self._proj("youtube")
        ok = youtube_pkg.payload.platform_specific_data(proj, first_comment="x" * 10000)
        self.assertEqual(len(ok["firstComment"]), 10000)
        with self.assertRaises(ValueError):
            youtube_pkg.payload.platform_specific_data(proj, first_comment="x" * 10001)

    # --- 응답 판정 · 멱등 ---------------------------------------------------- #
    def test_partial_is_terminal_failure(self):
        from gen.hero_shorts.publish_targets import transport as tm
        self.assertIn("partial", tm.TERMINAL_FAIL)
        self.assertNotIn("partial", tm.TERMINAL_OK)

    def test_idempotency_key_length_guard(self):
        from gen.hero_shorts.publish_targets.transport import (
            ZernioRestTransport, TransportError)
        t = ZernioRestTransport(api_key="sk_test", opener=lambda *a, **k: _FakeResp())
        with self.assertRaises(TransportError):
            t.send({"platforms": []}, platforms=[], idempotency_key="k" * 256)

    def test_endpoint_path_and_auth_header(self):
        from gen.hero_shorts.publish_targets.transport import ZernioRestTransport
        seen = {}

        def opener(req, timeout=None):
            seen["req"] = req
            return _FakeResp(b'{"post": {"_id": "p1", "platforms": []}}')

        t = ZernioRestTransport(api_key="sk_test", opener=opener)
        t.send({"platforms": [{"platform": "youtube"}]},
               platforms=["youtube"], idempotency_key="hero-ep89")
        req = seen["req"]
        self.assertEqual(req.full_url, "https://zernio.com/api/v1/posts")
        self.assertEqual(req.get_method(), "POST")
        h = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(h.get("idempotency-key"), "hero-ep89")
        self.assertTrue(h.get("authorization", "").startswith("Bearer "))
