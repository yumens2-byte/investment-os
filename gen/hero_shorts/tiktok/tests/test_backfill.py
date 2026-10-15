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


# ── 회귀: 대상 밖 회차 소실 금지(2026-10-15 실측 결함) ──────────────────────
def test_apply_preserves_episodes_outside_target():
    led = ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])
    led["ep88"] = {"title": "Ep88", "tiktok": {"status": "published", "post_id": "keep88"}}
    led["_ledger"] = {"rebuilt_at": "x"}
    plan = bf.build_plan(led, [live("r", "111", "2026-10-15T01:38:23.101Z")], eps=["ep86"])
    new, _ = bf.apply_plan(led, plan)
    assert "ep88" in new and new["ep88"]["tiktok"]["post_id"] == "keep88"
    assert "_ledger" in new


def test_out_writes_full_ledger_not_subset(tmp_path, monkeypatch):
    led = ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])
    led["ep88"] = {"title": "Ep88", "tiktok": {"status": "published"}}
    path = tmp_path / "publish_state.json"
    path.write_text(json.dumps(led), encoding="utf-8")
    out = tmp_path / "recovered.json"
    monkeypatch.setattr(bf.recover, "list_tiktok_posts",
                        lambda **k: [live("r", "111", "2026-10-15T01:38:23.101Z")])
    bf.main(["--ledger", str(path), "--ep", "ep86", "--out", str(out)])
    written = json.loads(out.read_text(encoding="utf-8"))
    assert set(written) == {"ep86", "ep88"}
    assert written["ep86"]["tiktok"]["segments"][0]["post_id"] == "r"
    assert written["ep88"]["tiktok"]["status"] == "published"


# ── 미러 ──────────────────────────────────────────────────────────────────
class _FakeProc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def test_mirror_uses_overwrite_true(tmp_path):
    f = tmp_path / "publish_state.json"
    f.write_text("{}", encoding="utf-8")
    seen = {}

    def fake(args, timeout=180):
        seen["args"] = args
        return _FakeProc(0, json.dumps({"data": {"download_url": "https://d/x"}}))

    import unittest.mock as mock
    with mock.patch.object(bf, "_gsk_run", fake):
        res = bf.mirror_to_drive(f)
    assert res["ok"] is True and res["link"] == "https://d/x"
    assert seen["args"][:2] == ["aidrive", "upload"]
    assert "--overwrite" in seen["args"] and "true" in seen["args"]
    assert bf.DRIVE_PATH in seen["args"]


def test_mirror_failure_is_reported_not_swallowed(tmp_path):
    f = tmp_path / "publish_state.json"
    f.write_text("{}", encoding="utf-8")
    import unittest.mock as mock
    with mock.patch.object(bf, "_gsk_run", lambda a, timeout=180: _FakeProc(1, "", "boom")):
        res = bf.mirror_to_drive(f)
    assert res["ok"] is False and "rc=1" in res["error"]


def test_mirror_without_apply_or_out_is_refused(tmp_path, monkeypatch):
    led = ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])
    path = tmp_path / "publish_state.json"
    path.write_text(json.dumps(led), encoding="utf-8")
    monkeypatch.setattr(bf.recover, "list_tiktok_posts",
                        lambda **k: [live("r", "111", "2026-10-15T01:38:23.101Z")])
    called = {"n": 0}
    monkeypatch.setattr(bf, "mirror_to_drive",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert bf.main(["--ledger", str(path), "--mirror"]) == bf.EXIT_MIRROR
    assert called["n"] == 0
    assert json.loads(path.read_text(encoding="utf-8")) == led  # 원장 무변경


def test_apply_then_mirror_happy_path(tmp_path, monkeypatch):
    led = ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])
    path = tmp_path / "publish_state.json"
    path.write_text(json.dumps(led), encoding="utf-8")
    monkeypatch.setattr(bf.recover, "list_tiktok_posts",
                        lambda **k: [live("r", "111", "2026-10-15T01:38:23.101Z")])
    mirrored = []
    monkeypatch.setattr(bf, "mirror_to_drive",
                        lambda t, *a, **k: mirrored.append(str(t)) or {"ok": True, "link": "u"})
    assert bf.main(["--ledger", str(path), "--apply", "--mirror"]) == bf.EXIT_OK
    assert mirrored == [str(path)]
    assert json.loads(path.read_text(encoding="utf-8"))["ep86"]["tiktok"]["segments"][0]["post_id"] == "r"


def test_mirror_exit_code_is_five_on_failure(tmp_path, monkeypatch):
    led = ledger_one(segs=[seg(None, "w", "111", "2026-01-01T00:00:00Z")])
    path = tmp_path / "publish_state.json"
    path.write_text(json.dumps(led), encoding="utf-8")
    monkeypatch.setattr(bf.recover, "list_tiktok_posts",
                        lambda **k: [live("r", "111", "2026-10-15T01:38:23.101Z")])
    monkeypatch.setattr(bf, "mirror_to_drive", lambda *a, **k: {"ok": False, "error": "x"})
    assert bf.main(["--ledger", str(path), "--apply", "--mirror"]) == bf.EXIT_MIRROR


def test_runner_forwards_mirror_flag(monkeypatch):
    """CLI 배선 회귀 — 러너가 --mirror 를 backfill 로 넘기는지."""
    import argparse
    from gen.hero_shorts.tiktok import runner
    captured = {}
    import gen.hero_shorts.tiktok.backfill as bfm
    monkeypatch.setattr(bfm, "main", lambda argv: (captured.__setitem__("argv", argv), 0)[1])
    ns = argparse.Namespace(ledger="L", backfill_limit=30, ep=["ep86"], account_id=None,
                            apply=True, out=None, mirror=True, as_json=False)
    assert runner.cmd_backfill(ns) == 0
    assert "--mirror" in captured["argv"] and "--apply" in captured["argv"]


def test_mirror_runs_even_when_nothing_to_correct(tmp_path, monkeypatch):
    """교정 0건이어도 --mirror 는 실행돼야 한다(조기 return 결함 회귀 방지)."""
    led = ledger_one(segs=[seg(None, "6acfa1cb9310ba390d4a1e8a", "7696548206833929480",
                              "2026-10-14T15:38:04.402Z")])
    tk = led["ep86"]["tiktok"]
    tk.update({"post_id": "6acfa1cb9310ba390d4a1e8a", "platform_post_id": "7696548206833929480",
               "platform_post_url": "https://www.tiktok.com/@tiger182725/video/7696548206833929480",
               "post_url": "https://www.tiktok.com/@tiger182725/video/7696548206833929480",
               "published_at": "2026-10-14T15:38:04.402Z"})
    path = tmp_path / "publish_state.json"
    path.write_text(json.dumps(led), encoding="utf-8")
    monkeypatch.setattr(bf.recover, "list_tiktok_posts",
                        lambda **k: [live("6acfa1cb9310ba390d4a1e8a", "7696548206833929480",
                                          "2026-10-14T15:38:04.402Z")])
    calls = []
    monkeypatch.setattr(bf, "mirror_to_drive",
                        lambda t, *a, **k: calls.append(str(t)) or {"ok": True})
    rc = bf.main(["--ledger", str(path), "--apply", "--mirror"])
    assert rc == bf.EXIT_OK
    assert calls == [str(path)]


def test_module_imports_subprocess_for_mirror():
    """회귀: _gsk_run 이 subprocess 를 쓰는데 import 가 없어 미러가 NameError 로 죽었다.
    기존 미러 테스트는 _gsk_run 을 몽키패치해 이 결함을 못 잡았다(실측 2026-10-16)."""
    import subprocess as sp
    assert bf.subprocess is sp
