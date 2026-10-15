"""원장 소급 복구(backfill) 테스트 — 정본 매칭·fail-closed·멱등·원값 보존."""
import json

import pytest

from gen.hero_shorts.tiktok import backfill as bf


def live(_id, pid, pub, status="published"):
    return {"_id": _id, "platformPostUrl": f"https://www.tiktok.com/@tiger182725/video/{pid}",
            "publishedAt": pub, "status": status}


def ledger_one(ep="ep86", mode="combined", segs=None, extra=None):
    rec = {"title": "t", "status": "published", "channel": "instagram",
           "tiktok": {"status": "published", "mode": mode, "segment_count": len(segs or []),
                      "segments": segs or []}}
    if extra:
        rec.update(extra)
    return {ep: rec}


def seg(no, post_id, pid, pub, url=None):
    return {"segment_no": no, "post_id": post_id, "platform_post_id": pid,
            "platform_post_url": url or f"https://www.tiktok.com/@tiger182725/video/{pid}",
            "published_at": pub, "outcome": "published"}


# ── 불일치 탐지 ────────────────────────────────────────────────────────────
def test_detects_post_id_off_by_one():
    led = ledger_one(segs=[seg(None, "6acfa1cb9310ba390d4a1e8b", "7696548206833929480",
                              "2026-10-14T15:38:04.402Z")])
    posts = [live("6acfa1cb9310ba390d4a1e8a", "7696548206833929480", "2026-10-14T15:38:04.402Z")]
    plan = bf.build_plan(led, posts)
    row = plan["records"][0]["segments"][0]
    assert row["post_id_ok"] is False
    assert row["proposed"]["post_id"] == "6acfa1cb9310ba390d4a1e8a"
    assert row["matched_by"] == "platform_post_id"


def test_clean_segment_reports_no_corrections():
    led = ledger_one(segs=[seg(1, "good00000000000000000001", "111",
                               "2026-10-15T01:38:23.101Z")])
    posts = [live("good00000000000000000001", "111", "2026-10-15T01:38:23.101Z")]
    plan = bf.build_plan(led, posts)
    assert plan["total_corrections"] == 0


def test_fail_closed_unknown_platform_id_keeps_ledger_value():
    led = ledger_one(segs=[seg(1, "x", "999999", "2026-01-01T00:00:00Z")])
    posts = [live("other", "111", "2026-10-15T01:00:00Z")]
    plan = bf.build_plan(led, posts)
    row = plan["records"][0]["segments"][0]
    assert row["proposed"] == {}
    assert any("미매칭" in p for p in row["problems"])


def test_missing_platform_post_id_is_a_problem():
    led = ledger_one(segs=[{"segment_no": 1, "post_id": "a"}])
    plan = bf.build_plan(led, [live("a", "111", "2026-10-15T01:00:00Z")])
    row = plan["records"][0]["segments"][0]
    assert any("매칭 키 부재" in p for p in row["problems"])


def test_published_at_drift_detected():
    led = ledger_one(segs=[seg(1, "aaa", "111", "2026-10-15T02:24:00Z")])
    plan = bf.build_plan(led, [live("aaa", "111", "2026-10-15T01:38:23.101Z")])
    row = plan["records"][0]["segments"][0]
    assert row["published_at_ok"] is False
    assert row["proposed"]["published_at"] == "2026-10-15T01:38:23.101Z"


# ── flat 필드 ──────────────────────────────────────────────────────────────
def test_single_segment_fills_flat_fields():
    led = ledger_one(segs=[seg(None, "aaa", "111", "2026-10-15T01:38:23.101Z")])
    plan = bf.build_plan(led, [live("aaa", "111", "2026-10-15T01:38:23.101Z")])
    flat = plan["records"][0]["flat_add"]
    assert flat["post_id"] == "aaa"
    assert flat["post_url"] == flat["platform_post_url"]
    assert flat["published_at"] == "2026-10-15T01:38:23.101Z"
    assert set(bf.FLAT_FIELDS) == set(flat)


def test_multi_segment_does_not_invent_flat():
    led = ledger_one(mode="split", segs=[seg(1, "a1", "111", "2026-10-13T13:22:19.213Z"),
                                         seg(2, "a2", "222", "2026-10-13T13:23:20.991Z")])
    posts = [live("a1", "111", "2026-10-13T13:22:19.213Z"), live("a2", "222", "2026-10-13T13:23:20.991Z")]
    plan = bf.build_plan(led, posts)
    r = plan["records"][0]
    assert r["flat_add"] == {}
    assert any("다중 세그먼트" in a for a in r["ambiguous"])


def test_duplicate_segment_no_flagged_ambiguous():
    led = ledger_one(mode="split", segs=[seg(1, "a1", "111", "2026-10-13T13:22:19.213Z"),
                                         seg(1, "b1", "222", "2026-10-13T12:50:46.252Z")])
    posts = [live("a1", "111", "2026-10-13T13:22:19.213Z"), live("b1", "222", "2026-10-13T12:50:46.252Z")]
    plan = bf.build_plan(led, posts)
    assert any("segment_no 중복" in a for a in plan["records"][0]["ambiguous"])


# ── 적용 ───────────────────────────────────────────────────────────────────
def test_apply_never_mutates_input_ledger():
    led = ledger_one(segs=[seg(None, "aaa_wrong", "111", "2026-10-15T01:38:23.101Z")])
    before = json.dumps(led, sort_keys=True)
    plan = bf.build_plan(led, [live("aaa_right", "111", "2026-10-15T01:38:23.101Z")])
    bf.apply_plan(led, plan)
    assert json.dumps(led, sort_keys=True) == before


def test_apply_writes_canonical_and_preserves_original():
    led = ledger_one(segs=[seg(None, "aaa_wrong", "111", "2026-10-15T02:24:00Z")])
    plan = bf.build_plan(led, [live("aaa_right", "111", "2026-10-15T01:38:23.101Z")])
    new, applied = bf.apply_plan(led, plan)
    s = new["ep86"]["tiktok"]["segments"][0]
    assert s["post_id"] == "aaa_right"
    assert s["post_id_was"] == "aaa_wrong"
    assert s["published_at"] == "2026-10-15T01:38:23.101Z"
    assert s["published_at_was"] == "2026-10-15T02:24:00Z"
    assert new["ep86"]["tiktok"]["backfill"]["source"] == bf.CANONICAL_SOURCE
    assert new["ep86"]["tiktok"]["published_at_source"] == "platform"
    assert applied["corrected_segments"] >= 2


def test_apply_is_idempotent():
    led = ledger_one(segs=[seg(None, "aaa_wrong", "111", "2026-10-15T02:24:00Z")])
    posts = [live("aaa_right", "111", "2026-10-15T01:38:23.101Z")]
    new, _ = bf.apply_plan(led, bf.build_plan(led, posts))
    again, applied2 = bf.apply_plan(new, bf.build_plan(new, posts))
    assert applied2["corrected_segments"] == 0
    assert applied2["flat_filled"] == 0
    assert again["ep86"]["tiktok"]["segments"][0]["post_id"] == "aaa_right"


def test_apply_preserves_unrelated_fields():
    led = ledger_one(segs=[seg(None, "w", "111", "2026-10-15T02:24:00Z")])
    led["ep86"]["approval"] = {"status": "APPROVED", "message_id": 1175}
    led["ep86"]["qc"] = {"G1": True}
    new, _ = bf.apply_plan(led, bf.build_plan(led, [live("r", "111", "2026-10-15T01:38:23.101Z")]))
    assert new["ep86"]["approval"]["message_id"] == 1175
    assert new["ep86"]["qc"] == {"G1": True}
    assert new["ep86"]["channel"] == "instagram"


def test_split_history_segments_are_audited():
    led = ledger_one(mode="combined", segs=[seg(None, "aaa", "111", "2026-10-15T01:00:00Z")])
    led["ep86"]["tiktok"]["split_history"] = {"mode": "split", "segments": [
        seg(1, "hhh_wrong", "555", "2026-10-13T13:33:35.736Z")]}
    posts = [live("aaa", "111", "2026-10-15T01:00:00Z"), live("hhh_right", "555", "2026-10-13T13:33:35.736Z")]
    plan = bf.build_plan(led, posts)
    sources = [r["source"] for r in plan["records"][0]["segments"]]
    assert "split_history" in sources
    row = [r for r in plan["records"][0]["segments"] if r["source"] == "split_history"][0]
    assert row["proposed"]["post_id"] == "hhh_right"


def test_no_tiktok_history_is_not_a_target():
    plan = bf.build_plan({"ep01": {"title": "x"}}, [live("a", "111", "2026-10-15T01:00:00Z")])
    assert any("발행 이력 없음" in p for p in plan["records"][0]["problems"])


def test_ledger_meta_key_skipped_as_target():
    plan = bf.build_plan({"_ledger": {"x": 1}, "ep86": {"tiktok": {"segments": []}}},
                         [live("a", "111", "2026-10-15T01:00:00Z")])
    assert [r["ep"] for r in plan["records"]] == ["ep86"]


def test_report_mentions_ep_and_proposals():
    led = ledger_one(segs=[seg(None, "aaa_wrong", "111", "2026-10-15T02:24:00Z")])
    plan = bf.build_plan(led, [live("aaa_right", "111", "2026-10-15T01:38:23.101Z")])
    txt = bf.format_report(plan)
    assert "ep86" in txt and "aaa_right" in txt


def test_main_reports_query_failure_without_touching_ledger(tmp_path, monkeypatch):
    led = tmp_path / "publish_state.json"
    led.write_text(json.dumps(ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])),
                   encoding="utf-8")
    before = led.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("gsk zernio 실패")

    monkeypatch.setattr(bf.recover, "list_tiktok_posts", boom)
    rc = bf.main(["--ledger", str(led)])
    assert rc == bf.EXIT_QUERY
    assert led.read_text(encoding="utf-8") == before


def test_main_empty_query_is_fail_closed(tmp_path, monkeypatch):
    led = tmp_path / "publish_state.json"
    led.write_text(json.dumps(ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])),
                   encoding="utf-8")
    monkeypatch.setattr(bf.recover, "list_tiktok_posts", lambda **k: [])
    assert bf.main(["--ledger", str(led)]) == bf.EXIT_QUERY


def test_main_dry_run_exit_code_and_no_write(tmp_path, monkeypatch):
    led = tmp_path / "publish_state.json"
    led.write_text(json.dumps(ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])),
                   encoding="utf-8")
    before = led.read_text(encoding="utf-8")
    monkeypatch.setattr(bf.recover, "list_tiktok_posts",
                        lambda **k: [live("r", "111", "2026-10-15T01:38:23.101Z")])
    assert bf.main(["--ledger", str(led)]) == bf.EXIT_DRIFT
    assert led.read_text(encoding="utf-8") == before
    assert not (tmp_path / "publish_state.json.bak").exists()


def test_main_apply_writes_backup_and_ledger(tmp_path, monkeypatch):
    led = tmp_path / "publish_state.json"
    led.write_text(json.dumps(ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])),
                   encoding="utf-8")
    monkeypatch.setattr(bf.recover, "list_tiktok_posts",
                        lambda **k: [live("r", "111", "2026-10-15T01:38:23.101Z")])
    assert bf.main(["--ledger", str(led), "--apply"]) == bf.EXIT_OK
    bak = tmp_path / "publish_state.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text(encoding="utf-8"))["ep86"]["tiktok"]["segments"][0]["post_id"] == "w"
    after = json.loads(led.read_text(encoding="utf-8"))
    assert after["ep86"]["tiktok"]["segments"][0]["post_id"] == "r"


def test_canon_source_is_list_posts():
    assert bf.CANONICAL_SOURCE == "zernio.list_posts"
