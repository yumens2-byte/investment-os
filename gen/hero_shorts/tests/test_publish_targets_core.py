"""tests/test_publish_targets_core.py — W1~W4 검증 (공통 게이트 · 캡션 3투영 · 원장 v2).

설계서 §9 의 T1(C-G) · T6·T7(캡션) · T11·T12(원장 v2) 대응.
발행은 일절 수행하지 않는다.
"""
import copy
import json

import pytest

from gen.hero_shorts import migrate_ledger_v2 as M
from gen.hero_shorts.publish_targets import caption_plan as CP
from gen.hero_shorts.publish_targets import common_gates as CG
from gen.hero_shorts.publish_targets import ledger_v2 as L


# --------------------------------------------------------------------------- #
# T1 — 공통 게이트 C-G1 ~ C-G5
# --------------------------------------------------------------------------- #
def _base(**over):
    kw = dict(media_url="https://example.com/a.mp4", duration_sec=61.0, ratio="9:16",
              texts=[CP.DISCLAIMER], text_hits=[], approval_status="APPROVED")
    kw.update(over)
    return CG.run_common_gates(**kw)


def test_cg_all_pass():
    r = _base()
    assert r["ok"] is True and r["failed"] == []


@pytest.mark.parametrize("over,failed", [
    ({"media_url": None}, "C-G1"),
    ({"duration_sec": 0}, "C-G1"),
    ({"ratio": "16:9"}, "C-G1"),
    ({"texts": ["면책문 없는 본문"]}, "C-G2"),
    ({"texts": [CP.DISCLAIMER + " 무조건 오른다"]}, "C-G3"),
    ({"text_hits": ["Ep89"]}, "C-G4"),
    ({"approval_status": "HOLD"}, "C-G5"),
    ({"approval_status": None}, "C-G5"),
])
def test_cg_failures(over, failed):
    r = _base(**over)
    assert r["ok"] is False and failed in r["failed"]


def test_cg5_accepts_lowercase():
    assert CG.gate_cg5("approved")["ok"] is True


# --------------------------------------------------------------------------- #
# T6·T7 — 캡션 3투영 (결정론 · 500자 압축)
# --------------------------------------------------------------------------- #
def _rec(title="배분을 바꾼다, 지금 필요한 곳으로", status="QC_PASSED_READY"):
    return {"title": title, "status": status, "approval": {"status": "APPROVED"}}


def test_projections_deterministic():
    a = CP.build_projections(_rec(), ep_label="Ep89", hook="시장은 지금을 묻는다")
    b = CP.build_projections(_rec(), ep_label="Ep89", hook="시장은 지금을 묻는다")
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == \
           json.dumps(b, ensure_ascii=False, sort_keys=True)


def test_youtube_title_prefixed_and_limited():
    p = CP.build_projections(_rec(), ep_label="Ep89")
    assert p["youtube"]["title"].startswith("Ep89 ")
    assert "배분을 바꾼다" in p["youtube"]["title"]
    long_rec = _rec(title="가" * 300)
    p2 = CP.build_projections(long_rec, ep_label="Ep89")
    assert len(p2["youtube"]["title"]) <= CP.YOUTUBE_TITLE_LIMIT
    assert p2["youtube"]["title"].startswith("Ep89 ")


def test_youtube_first_line_is_title_not_ep_only():
    """F6 회귀 방지 — 첫 줄이 'Ep89' 단독이면 제목이 Ep89 이 된다."""
    p = CP.build_projections(_rec(), ep_label="Ep89")
    first = p["youtube"]["content"].split("\n", 1)[0].strip()
    assert first == p["youtube"]["title"]
    assert first != "Ep89"


def test_youtube_tags_are_stripped_array():
    p = CP.build_projections(_rec(), ep_label="Ep89")
    tags = p["youtube"]["tags"]
    assert tags and all(not t.startswith("#") for t in tags)
    assert sum(len(t) for t in tags) <= CP.YOUTUBE_TAG_TOTAL_LIMIT


def test_threads_under_limit_no_compression():
    p = CP.build_projections(_rec(), ep_label="Ep89")
    assert p["threads"]["over_limit"] is False
    assert p["threads"]["compressions"] == []
    assert len(p["threads"]["content"]) <= CP.THREADS_TEXT_LIMIT


def test_threads_hook_removed_first():
    p = CP.build_projections(_rec(), ep_label="Ep89", hook="훅" * 500)
    th = p["threads"]
    assert th["over_limit"] is False
    assert th["compressions"] == ["hook_removed"]
    assert "훅" * 500 not in th["content"]


def test_threads_over_limit_reported_not_raised():
    """제목이 한도를 넘으면 캡션 생성기는 차단하지 않고 over_limit 만 알린다(TH-G2 판정)."""
    p = CP.build_projections(_rec(title="나" * 600), ep_label="Ep89")
    assert p["threads"]["over_limit"] is True
    assert "hashtags_removed" in p["threads"]["compressions"]


def test_topic_tag_sanitized():
    assert CP.sanitize_topic_tag("JCU.niverse&주식") == "JCUniverse주식"
    assert CP.sanitize_topic_tag("") == CP.DEFAULT_TOPIC_TAG
    assert len(CP.sanitize_topic_tag("가" * 100)) == CP.TOPIC_TAG_LIMIT


def test_projection_requires_title():
    with pytest.raises(ValueError):
        CP.build_projections({"title": ""})


# --------------------------------------------------------------------------- #
# T11·T12 — 원장 v2 (멱등 · 틱톡 블록 무손실)
# --------------------------------------------------------------------------- #
def _ledger(tmp_path, eps=("ep87", "ep88")):
    doc = {"_ledger": {"rebuilt_at": "2026-10-15T06:28:00+09:00"}, "_schema": 1}
    for i, ep in enumerate(eps):
        doc[ep] = {
            "title": f"제목 {ep}", "status": "published", "channel": "instagram",
            "tiktok": {"status": "published", "post_id": f"post{i}",
                       "segments": [{"segment_no": 1, "post_id": f"s{i}"}]},
        }
    p = tmp_path / "publish_state.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return p, doc


def test_migrate_adds_targets_and_schema(tmp_path):
    p, _ = _ledger(tmp_path)
    r = L.migrate(p, apply=True)
    assert r["changed"] is True and r["tiktok_ok"] is True
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["_schema"] == 2
    for ep in ("ep87", "ep88"):
        assert set(d[ep]["targets"]) == {"youtube", "threads"}
        assert L.target_status(d[ep], "youtube") == "pending"


def test_migrate_is_idempotent(tmp_path):
    p, _ = _ledger(tmp_path)
    L.migrate(p, apply=True)
    r2 = L.migrate(p, apply=True)
    assert r2["changed"] is False and r2["report"]["already_v2"] is True
    assert r2["report"]["targets_added"] == []


def test_migrate_preserves_tiktok_bytes(tmp_path):
    p, _ = _ledger(tmp_path)
    before = L.tiktok_hashes(json.loads(p.read_text(encoding="utf-8")))
    L.migrate(p, apply=True)
    after = L.tiktok_hashes(json.loads(p.read_text(encoding="utf-8")))
    assert before == after
    assert before  # 해시가 실제로 계산됐다


def test_migrate_preview_writes_nothing(tmp_path):
    p, _ = _ledger(tmp_path)
    original = p.read_text(encoding="utf-8")
    r = L.migrate(p, apply=False)
    assert r["changed"] is True and r["written"] is None
    assert p.read_text(encoding="utf-8") == original


def test_tiktok_change_detected():
    p, doc = _ledger(__import__("pathlib").Path("/tmp"))
    before = L.tiktok_hashes(doc)
    tampered = copy.deepcopy(doc)
    tampered["ep88"]["tiktok"]["status"] = "failed"
    after = L.tiktok_hashes(tampered)
    with pytest.raises(RuntimeError):
        L.assert_tiktok_unchanged(before, after)


def test_migrate_cli_exit6_on_tamper(tmp_path, monkeypatch):
    p, _ = _ledger(tmp_path)

    def _boom(*a, **kw):
        raise RuntimeError("틱톡 블록 변경 감지 — 중단(무손실 보존 위반): ['ep88']")

    monkeypatch.setattr(L, "migrate", _boom)
    assert M.main(["--ledger", str(p), "--apply"]) == 6


def test_plan_pending_excludes_published(tmp_path):
    p, _ = _ledger(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["ep87"]["targets"] = {"youtube": {"status": "published", "attempts": [{}]},
                            "threads": {"status": "failed", "attempts": [{}]}}
    d["ep88"]["targets"] = {"youtube": {"status": "published", "attempts": [{}]},
                            "threads": {"status": "published", "attempts": [{}]}}
    pend = L.plan_pending(d)
    assert [(x["ep"], x["platform"]) for x in pend] == [("ep87", "threads")]


def test_plan_pending_respects_attempt_cap(tmp_path):
    p, _ = _ledger(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["ep87"]["targets"] = {"youtube": {"status": "failed", "attempts": [{}, {}, {}]},
                            "threads": {"status": "blocked", "attempts": [{}]}}
    d["ep88"]["targets"] = {"youtube": {"status": "published", "attempts": [{}]},
                            "threads": {"status": "published", "attempts": [{}]}}
    assert L.plan_pending(d) == []


def test_set_target_appends_attempts():
    rec = {"targets": {"youtube": {"status": "failed", "attempts": [{"a": 1}]}}}
    res = {"status": "published", "post_id": "x", "attempts": [{"a": 2}]}
    block = L.set_target(rec, "youtube", res)
    assert [a["a"] for a in block["attempts"]] == [1, 2]
    assert block["status"] == "published"


def test_real_ledger_roundtrip_preview():
    """실제 원장 대상 미리보기 — 쓰기 없음. 원장 파일이 없으면 건너뛴다."""
    import pathlib

    if not pathlib.Path("gen/data/publish_state.json").exists():
        pytest.skip("원장 파일 없음")
    before = pathlib.Path("gen/data/publish_state.json").read_bytes()
    r = L.migrate("gen/data/publish_state.json", apply=False)
    assert r["tiktok_ok"] is True
    assert pathlib.Path("gen/data/publish_state.json").read_bytes() == before
