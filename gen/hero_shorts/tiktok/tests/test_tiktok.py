"""tests/test_tiktok.py — 틱톡 라인 검증 (게이트 · 캡션 · 매니페스트)."""
import pytest

from gen.hero_shorts.tiktok import caption as C
from gen.hero_shorts.tiktok import gates as G
from gen.hero_shorts.tiktok import manifest as M


def _cuts(n=7):
    return [{"cut_no": i, "role": f"R{i}"} for i in range(1, n + 1)]


# --- gates ---

def test_tg1_pass_and_ratio():
    assert G.gate_tg1(30, 60)["ok"]
    bad = G.gate_tg1(61, 60)
    assert not bad["ok"]
    assert any("상한" in p for p in bad["problems"])


def test_tg1_ratio_reject():
    r = G.gate_tg1(30, 60, ratio="1:1")
    assert not r["ok"]


def test_tg2_requires_true():
    assert G.gate_tg2(True)["ok"]
    assert not G.gate_tg2(False)["ok"]
    assert not G.gate_tg2(None)["ok"]


def test_tg3_text_free():
    assert G.gate_tg3([])["ok"]
    assert not G.gate_tg3(["SELL NOW"])["ok"]


def test_caption_gate_forbidden_and_disclaimer():
    ok_cap = "시장 만화 1/3\n투자 참고 정보, 투자 권유 아님\n#JCUniverse"
    assert G.gate_caption(ok_cap)["ok"]
    assert not G.gate_caption("내일 오른다 #JCUniverse 투자 참고")["ok"]
    assert not G.gate_caption("수익 보장 #JCUniverse")["ok"]


def test_caption_gate_world_tag_and_limit():
    no_tag = "시장 만화\n투자 참고 정보, 투자 권유 아님"
    assert not G.gate_caption(no_tag)["ok"]
    many = " ".join(f"#t{i}" for i in range(9)) + " #JCUniverse 투자 참고"
    assert not G.gate_caption(many)["ok"]


def test_run_gates_aggregate():
    r = G.run_gates("시장 만화 1/3\n투자 참고 정보, 투자 권유 아님\n#JCUniverse",
                    duration_sec=30, max_duration_sec=60,
                    privacy_level=G.DEFAULT_PRIVACY_LEVEL)
    assert r["ok"] and r["failed"] == []
    assert [x["gate"] for x in r["results"]] == ["T-G1", "T-G2", "T-G3", "T-G4", "CAPTION"]


# --- caption ---

def test_caption_segment1_uses_hook():
    seg = {"segment_no": 1, "series_marker": "1/3"}
    text = C.build_segment_caption(seg, "시장 만화", hook="자본 수호자 등장")
    assert "자본 수호자 등장" in text
    assert "#JCUniverse" in text
    assert G.gate_caption(text)["ok"]


def test_caption_segment2_link():
    seg = {"segment_no": 2, "series_marker": "2/3"}
    text = C.build_segment_caption(seg, "시장 만화")
    assert "1편에서 이어집니다" in text
    assert G.gate_caption(text)["ok"]


def test_caption_deterministic():
    seg = {"segment_no": 3, "series_marker": "3/3"}
    assert C.build_segment_caption(seg, "X") == C.build_segment_caption(seg, "X")


# --- manifest ---

def test_manifest_three_segments():
    m = M.build_manifest(_cuts(), "시장 만화", hook="훅")
    assert m["segment_count"] == 3
    assert m["total_duration_sec"] == 70
    assert m["all_gates_ok"] is True
    assert [s["total_duration_sec"] for s in m["segments"]] == [30, 20, 20]
    assert all(s["status"] == "ready" for s in m["segments"])


def test_manifest_rejects_non_7():
    with pytest.raises(ValueError):
        M.build_manifest(_cuts(6), "T")


def test_manifest_accepts_plan_dict():
    m = M.build_manifest({"cuts": _cuts()}, "T")
    assert m["segment_count"] == 3


def test_manifest_roundtrip(tmp_path):
    m = M.build_manifest(_cuts(), "T", hook="h")
    p = tmp_path / "m.json"
    M.save_manifest(str(p), m)
    assert M.load_manifest(str(p))["segment_count"] == 3


def test_manifest_gate_block_on_long():
    m = M.build_manifest(_cuts(), "T", max_duration_sec=25)
    assert m["all_gates_ok"] is False
    blocked = [s for s in m["segments"] if s["status"] == "blocked"]
    assert blocked and all(s["total_duration_sec"] > 25 for s in blocked)


# --- T-G4 공개 레벨 (privacy_level 갭 수정) ---

def test_tg4_public_ok():
    r = G.gate_tg4("PUBLIC_TO_EVERYONE")
    assert r["ok"] and r["draft"] is False


def test_tg4_missing_blocked():
    assert not G.gate_tg4(None)["ok"]
    assert not G.gate_tg4("")["ok"]


def test_tg4_unknown_level_blocked():
    assert not G.gate_tg4("PRIVATE")["ok"]


def test_tg4_business_lane_rejects_non_public():
    r = G.gate_tg4("SELF_ONLY")
    assert not r["ok"]
    assert any("Business" in p for p in r["problems"])


def test_tg4_draft_bypasses_level():
    r = G.gate_tg4("SELF_ONLY", draft=True)
    assert r["ok"] and r["draft"] is True
    assert G.gate_tg4(None, draft=True)["ok"]


def test_run_gates_includes_tg4():
    cap = "시장 만화 1/3\n투자 참고 정보, 투자 권유 아님\n#JCUniverse"
    ok = G.run_gates(cap, 30, 60, privacy_level="PUBLIC_TO_EVERYONE")
    assert ok["ok"]
    bad = G.run_gates(cap, 30, 60, privacy_level="SELF_ONLY")
    assert not bad["ok"]
    assert "T-G4" in bad["failed"]


def test_manifest_default_privacy_and_draft_flag():
    m = M.build_manifest(_cuts(), "T", hook="h")
    assert m["privacy_level"] == G.DEFAULT_PRIVACY_LEVEL
    assert all(s["privacy_level"] == G.DEFAULT_PRIVACY_LEVEL for s in m["segments"])
    assert all(s["draft"] is False for s in m["segments"])
    d = M.build_manifest(_cuts(), "T", hook="h", draft=True)
    assert d["all_gates_ok"] is True
    assert all(s["draft"] is True for s in d["segments"])


def test_manifest_non_public_blocked_on_business_lane():
    m = M.build_manifest(_cuts(), "T", hook="h", privacy_level="SELF_ONLY")
    assert m["all_gates_ok"] is False
    assert "T-G4" in m["segments"][0]["gates"]["failed"]


# --- publish_runtime 페이로드 (TikTok 필수 필드 전달) ---

def test_create_post_args_tiktok_fields():
    from gen.hero_shorts import publish_runtime as pr
    args = pr.create_post_args(
        text="cap", media_url="https://x/y.mp4", account_id="aid",
        tiktok_privacy_level="PUBLIC_TO_EVERYONE", tiktok_draft=False,
        tiktok_allow_comments=True,
    )
    assert args["tiktok_privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert args["tiktok_draft"] is False
    assert args["tiktok_allow_comments"] is True
    assert "tiktok_allow_duet" not in args  # None → 미포함


def test_create_post_args_instagram_unchanged():
    from gen.hero_shorts import publish_runtime as pr
    args = pr.create_post_args(text="cap", media_url="https://x/y.mp4", account_id="aid")
    assert set(args) == {"text", "media_urls", "account_ids", "ai_generated"}
    assert not any(k.startswith("tiktok") for k in args)


def test_resolve_privacy_level_env(monkeypatch):
    from gen.hero_shorts.tiktok import publish as P
    monkeypatch.delenv("HERO_TIKTOK_PRIVACY_LEVEL", raising=False)
    assert P.resolve_privacy_level() == G.DEFAULT_PRIVACY_LEVEL
    assert P.resolve_privacy_level("FOLLOWER_OF_CREATOR") == "FOLLOWER_OF_CREATOR"
    monkeypatch.setenv("HERO_TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")
    assert P.resolve_privacy_level() == "SELF_ONLY"
    with pytest.raises(ValueError):
        P.resolve_privacy_level("BOGUS")
    with pytest.raises(ValueError):
        monkeypatch.setenv("HERO_TIKTOK_PRIVACY_LEVEL", "BOGUS")
        P.resolve_privacy_level()


# --- 스키마 보완 (gates_ok vs gates.ok 재발 방지) ---

def test_segment_gate_ok_strict():
    assert M.segment_gate_ok({"gates": {"ok": True}}) is True
    assert M.segment_gate_ok({"gates": {"ok": False}}) is False


def test_segment_gate_ok_rejects_legacy_key():
    # 과거 폴백 스크립트가 쓴 레거시 키는 조용히 통과시키지 않는다
    with pytest.raises(ValueError) as e:
        M.segment_gate_ok({"gates_ok": True})
    assert "gates_ok" in str(e.value)


def test_segment_gate_ok_rejects_missing():
    with pytest.raises(ValueError):
        M.segment_gate_ok({})


def _split_result(durs=(20.0, 21.0, 19.87), ep=1):
    segs = []
    cur = 0.0
    for i, d in enumerate(durs, start=1):
        segs.append({"segment_no": i, "cut_nos": [i], "start": round(cur, 3),
                     "end": round(cur + d, 3), "duration_sec": d, "path": f"s{i}.mp4"})
        cur += d
    return {"method": "timestamps", "total_duration_sec": round(cur, 3),
            "cut_count": len(durs), "segments": segs}


def test_from_split_schema_matches_build_manifest():
    a = M.build_manifest(_cuts(), "T", hook="h")
    b = M.from_split(_split_result(), "T", hook="h")
    assert set(a["segments"][0]) == set(b["segments"][0]), "두 경로 스키마가 어긋난다"
    assert all(M.segment_gate_ok(s) for s in b["segments"])
    assert b["all_gates_ok"] is True
    assert b["split_method"] == "timestamps"
    assert b["segment_count"] == 3


def test_from_split_rejects_empty():
    with pytest.raises(ValueError):
        M.from_split({"method": "x", "segments": []}, "T")


def test_from_split_media_urls_injected():
    m = M.from_split(_split_result(), "T", hook="h", media_urls={1: "https://x/1.mp4"})
    assert m["segments"][0]["media_url"] == "https://x/1.mp4"
    assert m["segments"][1]["media_url"] is None


def test_from_split_gate_blocks_long_segment():
    # 상한 = 플랫폼 한도 600초(마스터 지침 2026-10-13). 초과분만 차단 대상.
    m = M.from_split(_split_result(durs=(700.0,)), "T")
    assert m["all_gates_ok"] is False
    assert m["segments"][0]["status"] == "blocked"


def test_from_split_roundtrip_usable_by_publish_gate():
    """from_split 산출물이 publish 의 게이트 판정을 통과해야 한다(실결함 재현 방지)."""
    m = M.from_split(_split_result(), "T", hook="h")
    for s in m["segments"]:
        assert M.segment_gate_ok(s) is True
