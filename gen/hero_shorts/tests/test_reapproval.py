"""승인 재발행(reapproval) 테스트 — 감지·가드·원본보존·dry-run fail-closed."""
import datetime as dt
import json
import pathlib

import pytest

from gen.hero_shorts import reapproval as ra

UTC = dt.timezone.utc


def at(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def entry(status="READY_FOR_APPROVAL", requested="2026-10-13T00:00:00Z",
          media="https://x/m.mp4", decided=None, tiktok=None, **kw):
    ap = {"status": status, "requested_at": requested, "message_id": 111,
          "media_url": media, "chat_id": "-100"}
    if decided:
        ap["decided_at"] = decided
    e = {"approval": ap, "combined": {"title": "제목", "duration_sec": 30.0, "media_url": media}}
    if tiktok:
        e["tiktok"] = tiktok
    e.update(kw)
    return e


def fake_sender(new_ts="2026-10-16T01:00:00+09:00"):
    calls = []

    def sender(ep, title, caption, media_url, video_path):
        calls.append({"ep": ep, "title": title, "media_url": media_url, "video": video_path})
        return {"status": "READY_FOR_APPROVAL", "requested_at": new_ts,
                "chat_id": "-100", "message_id": 999, "media_url": media_url}

    sender.calls = calls
    return sender


# ── 감지 ──────────────────────────────────────────────────────────────────
def test_is_pending_true_for_undecided():
    assert ra.is_pending(entry()) is True


def test_is_pending_false_when_decided():
    assert ra.is_pending(entry(decided="2026-10-15T01:00:00Z")) is False


def test_is_pending_false_for_approved():
    assert ra.is_pending(entry(status="APPROVED")) is False


def test_is_pending_false_without_approval_block():
    assert ra.is_pending({"combined": {}}) is False


def test_find_stale_flags_beyond_24h():
    st = {"ep89": entry()}
    rows = ra.find_stale(st, now=at("2026-10-15T01:00:00Z"))
    assert rows[0]["callback_at_risk"] is True and rows[0]["age_hours"] == 49.0


def test_find_stale_within_window_not_flagged():
    st = {"ep89": entry(requested="2026-10-15T00:00:00Z")}
    rows = ra.find_stale(st, now=at("2026-10-15T01:00:00Z"))
    assert rows[0]["callback_at_risk"] is False


def test_find_stale_skips_decided_and_published():
    st = {"ep89": entry(decided="2026-10-15T01:00:00Z"), "ep90": entry(status="APPROVED")}
    assert ra.find_stale(st, now=at("2026-10-15T01:00:00Z")) == []


def test_find_stale_ep_filter():
    st = {"ep89": entry(), "ep90": entry()}
    rows = ra.find_stale(st, now=at("2026-10-15T01:00:00Z"), eps={90})
    assert [r["ep"] for r in rows] == [90]


def test_find_stale_reports_missing_requested_at():
    st = {"ep89": entry(requested=None)}
    rows = ra.find_stale(st, now=at("2026-10-15T01:00:00Z"))
    assert rows[0]["age_hours"] is None and rows[0]["callback_at_risk"] is False


# ── 가드 ──────────────────────────────────────────────────────────────────
def test_plan_action_for_stale_beyond_window():
    st = {"ep89": entry()}
    p = ra.build_plan(st, now=at("2026-10-15T01:00:00Z"))
    assert p["action_count"] == 1 and p["actions"][0]["ep"] == 89


def test_plan_skips_within_window():
    st = {"ep89": entry(requested="2026-10-15T00:00:00Z")}
    p = ra.build_plan(st, now=at("2026-10-15T01:00:00Z"))
    assert p["action_count"] == 0 and "24h 창 내" in p["skipped"][0]["skip_reason"]


def test_plan_skips_already_published():
    st = {"ep89": entry(tiktok={"status": "published"})}
    p = ra.build_plan(st, now=at("2026-10-15T01:00:00Z"))
    assert p["action_count"] == 0 and "이미 발행" in p["skipped"][0]["skip_reason"]


def test_plan_skips_at_max_requests():
    e = entry()
    e["approval"]["rerequests"] = [{"at": "2026-10-14T00:00:00Z"}] * 3
    p = ra.build_plan({"ep89": e}, now=at("2026-10-15T01:00:00Z"), max_requests=3)
    assert p["action_count"] == 0 and "상한 도달" in p["skipped"][0]["skip_reason"]


def test_plan_skips_within_cooldown():
    e = entry()
    e["approval"]["rerequests"] = [{"at": "2026-10-14T20:00:00Z"}]
    p = ra.build_plan({"ep89": e}, now=at("2026-10-15T01:00:00Z"), cooldown_hours=24.0)
    assert p["action_count"] == 0 and "쿨다운" in p["skipped"][0]["skip_reason"]


def test_plan_allows_after_cooldown_elapsed():
    e = entry()
    e["approval"]["rerequests"] = [{"at": "2026-10-13T20:00:00Z"}]
    p = ra.build_plan({"ep89": e}, now=at("2026-10-15T01:00:00Z"), cooldown_hours=24.0)
    assert p["action_count"] == 1


def test_plan_skips_when_no_media_at_all():
    e = entry(media=None)
    e["combined"]["media_url"] = None
    p = ra.build_plan({"ep89": e}, now=at("2026-10-15T01:00:00Z"))
    assert p["action_count"] == 0 and "미디어 해석 실패" in p["skipped"][0]["skip_reason"]


def test_plan_read_only():
    st = {"ep89": entry()}
    before = json.dumps(st, sort_keys=True)
    ra.build_plan(st, now=at("2026-10-15T01:00:00Z"))
    assert json.dumps(st, sort_keys=True) == before


# ── 미디어 해석 ───────────────────────────────────────────────────────────
def test_resolve_media_prefers_local_file(tmp_path):
    v = tmp_path / "ep89.mp4"
    v.write_bytes(b"x" * 100)
    e = entry()
    e["combined"]["video_path"] = str(v)
    path, url, how = ra.resolve_media(89, e)
    assert path == str(v) and "로컬 영상" in how


def test_resolve_media_uses_drive_restorer(tmp_path):
    dst = tmp_path / "restored.mp4"
    dst.write_bytes(b"y" * 100)
    e = entry()
    e["combined"]["aidrive_path"] = "/hero-shorts/media/ep89_final_voiced.mp4"
    path, url, how = ra.resolve_media(89, e, restorer=lambda src, dest: str(dst))
    assert path == str(dst) and "AI Drive 복원" in how


def test_resolve_media_falls_back_to_link():
    e = entry(media="https://x/m.mp4")
    path, url, how = ra.resolve_media(89, e)
    assert path is None and url == "https://x/m.mp4" and "링크" in how


def test_resolve_media_oversized_local_falls_back(tmp_path):
    v = tmp_path / "big.mp4"
    v.write_bytes(b"z")
    e = entry(media="https://x/m.mp4")
    e["combined"]["video_path"] = str(v)
    # 크기 상한을 낮춰 강제 폴백
    import unittest.mock as mock
    with mock.patch.object(ra, "VIDEO_MAX_BYTES", 0):
        path, url, how = ra.resolve_media(89, e)
    assert path is None and "링크" in how


# ── 재발행 ────────────────────────────────────────────────────────────────
def test_re_request_preserves_original_once():
    e = entry()
    ra.re_request(e, 89, "u", None, fake_sender())
    first = e["approval"]["original_request"]
    assert first["message_id"] == 111
    ra.re_request(e, 89, "u", None, fake_sender("2026-10-17T01:00:00+09:00"))
    assert e["approval"]["original_request"] == first      # 덮어쓰지 않는다


def test_re_request_appends_history_and_count():
    e = entry()
    ra.re_request(e, 89, "u", None, fake_sender())
    assert e["approval"]["rerequest_count"] == 1
    ra.re_request(e, 89, "u", None, fake_sender("2026-10-17T01:00:00+09:00"))
    assert e["approval"]["rerequest_count"] == 2
    assert [r["at"] for r in e["approval"]["rerequests"]] == [
        "2026-10-16T01:00:00+09:00", "2026-10-17T01:00:00+09:00"]


def test_re_request_resets_window_and_decision():
    e = entry(decided="2026-10-15T00:00:00Z")
    e["approval"]["decided_by"] = "master"
    ra.re_request(e, 89, "u", None, fake_sender())
    ap = e["approval"]
    assert ap["decided_at"] is None and ap["decided_by"] is None
    assert ap["requested_at"] == "2026-10-16T01:00:00+09:00"
    assert ap["rerequest_of"] == "2026-10-13T00:00:00Z"


def test_apply_plan_sends_and_reports():
    st = {"ep89": entry()}
    p = ra.build_plan(st, now=at("2026-10-15T01:00:00Z"))
    sender = fake_sender()
    new, res = ra.apply_plan(st, p, sender)
    assert res["sent"][0]["message_id"] == 999 and not res["failed"]
    assert new["ep89"]["approval"]["message_id"] == 999
    assert st["ep89"]["approval"]["message_id"] == 111      # 원본 불변


def test_apply_plan_captures_sender_failure():
    st = {"ep89": entry()}
    p = ra.build_plan(st, now=at("2026-10-15T01:00:00Z"))

    def boom(*a, **k):
        raise RuntimeError("텔레그램 400")

    new, res = ra.apply_plan(st, p, boom)
    assert res["failed"][0]["ep"] == 89 and "400" in res["failed"][0]["error"]


# ── CLI ───────────────────────────────────────────────────────────────────
def _write(tmp_path, st):
    f = tmp_path / "publish_state.json"
    f.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return f


def test_main_dry_run_needed_exit_three(tmp_path):
    f = _write(tmp_path, {"ep89": entry()})
    assert ra.main(["--state", str(f), "--now", "2026-10-15T01:00:00Z"]) == ra.EXIT_NEEDED


def test_main_nothing_to_do_exit_zero(tmp_path):
    f = _write(tmp_path, {"ep88": entry(tiktok={"status": "published"})})
    assert ra.main(["--state", str(f), "--now", "2026-10-15T01:00:00Z"]) == ra.EXIT_OK


def test_main_missing_state_exit_four(tmp_path):
    assert ra.main(["--state", str(tmp_path / "nope.json")]) == ra.EXIT_UNKNOWN


def test_main_dry_run_does_not_write(tmp_path):
    f = _write(tmp_path, {"ep89": entry()})
    before = f.read_text(encoding="utf-8")
    ra.main(["--state", str(f), "--now", "2026-10-15T01:00:00Z"])
    assert f.read_text(encoding="utf-8") == before
    assert not (tmp_path / "publish_state.json.bak").exists()


def test_main_send_writes_ledger_and_backup(tmp_path, monkeypatch):
    f = _write(tmp_path, {"ep89": entry()})
    import gen.hero_shorts.approval as ap_mod
    import gen.hero_shorts.statestore as ss
    sender = fake_sender()
    monkeypatch.setattr(ap_mod, "request_approval", sender)
    monkeypatch.setattr(ss, "sync_states", lambda paths: None)
    rc = ra.main(["--state", str(f), "--now", "2026-10-15T01:00:00Z", "--send"])
    assert rc == ra.EXIT_OK
    assert sender.calls[0]["ep"] == 89
    after = json.loads(f.read_text(encoding="utf-8"))
    assert after["ep89"]["approval"]["message_id"] == 999
    assert (tmp_path / "publish_state.json.bak").exists()


def test_main_send_failure_exit_five(tmp_path, monkeypatch):
    f = _write(tmp_path, {"ep89": entry()})
    import gen.hero_shorts.approval as ap_mod
    monkeypatch.setattr(ap_mod, "request_approval",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ra.main(["--state", str(f), "--now", "2026-10-15T01:00:00Z", "--send"]) == ra.EXIT_SEND_FAIL
    assert json.loads(f.read_text(encoding="utf-8"))["ep89"]["approval"]["message_id"] == 111


def test_main_json_reports_plan(tmp_path, capsys):
    f = _write(tmp_path, {"ep89": entry()})
    ra.main(["--state", str(f), "--now", "2026-10-15T01:00:00Z", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"]["action_count"] == 1 and payload["result"] is None


def test_report_names_ep_and_mode():
    p = ra.build_plan({"ep89": entry()}, now=at("2026-10-15T01:00:00Z"))
    txt = ra.format_report(p)
    assert "DRY-RUN" in txt and "ep89" in txt


def test_retention_matches_telegram_docs():
    assert ra.RETENTION_HOURS == 24.0
