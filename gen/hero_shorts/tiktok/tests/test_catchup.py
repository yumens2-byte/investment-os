"""러너3 회수 계획(catchup) 테스트 — 슬롯 계산·부채·승인 압력·fail-closed."""
import datetime as dt
import json

import pytest

from gen.hero_shorts.tiktok import catchup as cu

CRON = "30 10 * * 2,4"          # 러너3 — KST 화·목 10:30
TZ = "Asia/Seoul"
UTC = dt.timezone.utc


def at(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def ep_ready(n, title="t", dur=30.0, url="https://x/m.mp4"):
    return {"approval": {"status": "APPROVED", "requested_at": "2026-10-15T01:00:00+00:00"},
            "combined": {"title": title, "duration_sec": dur, "media_url": url}}


def ep_published(n, published_at):
    return {"approval": {"status": "APPROVED"},
            "combined": {"title": "t", "duration_sec": 30.0, "media_url": "u"},
            "tiktok": {"status": "published", "published_at": published_at}}


# ── 슬롯 계산(KST 기준) ────────────────────────────────────────────────────
def test_slots_use_kst_skill_cron():
    """10:30 KST = 01:30Z — 목요일 슬롯이 UTC 로 01:30 에 잡혀야 한다."""
    after = at("2026-10-12T00:00:00Z")
    until = at("2026-10-16T00:00:00Z")
    got = [s.strftime("%Y-%m-%dT%H:%M:%SZ") for s in cu.slots_between(CRON, after, until, tz_name=TZ)]
    assert got == ["2026-10-13T01:30:00Z", "2026-10-15T01:30:00Z"]  # 화·목


def test_slots_empty_when_range_inverted():
    assert cu.slots_between(CRON, at("2026-10-16T00:00:00Z"), at("2026-10-15T00:00:00Z"), tz_name=TZ) == []


def test_slots_exclude_boundary_start():
    """(after, until] — after 자체는 제외."""
    after = at("2026-10-15T01:30:00Z")
    until = at("2026-10-15T02:00:00Z")
    assert cu.slots_between(CRON, after, until, tz_name=TZ) == []


# ── 발행 이력 ─────────────────────────────────────────────────────────────
def test_publish_events_collects_all_segments():
    st = {"ep80": {"tiktok": {"status": "published", "segments": [
        {"published_at": "2026-10-13T13:22:19.213Z"},
        {"published_at": "2026-10-13T12:50:46.252Z"}]}}}
    ev = cu.publish_events(st)
    assert ev[0]["at"] == at("2026-10-13T13:22:19.213Z")   # 세그먼트 최대값


def test_last_publish_ignores_published_without_timestamp():
    st = {"ep80": {"tiktok": {"status": "published"}},
          "ep81": {"tiktok": {"status": "published", "published_at": "2026-10-15T01:38:23.101Z"}}}
    assert cu.last_publish(st)["ep"] == 81


def test_last_publish_none_when_no_history():
    assert cu.last_publish({"ep80": {"tiktok": {"status": "failed"}}}) is None


# ── 승인 압력 ─────────────────────────────────────────────────────────────
def test_approval_pressure_flags_over_24h():
    st = {"ep90": {"approval": {"status": "PENDING", "requested_at": "2026-10-13T00:00:00Z"}}}
    rows = cu.approval_pressure(st, now=at("2026-10-15T01:00:00Z"))
    # 2026-10-13T00:00Z → 2026-10-15T01:00Z = 49h (2일 1시간)
    assert rows[0]["age_hours"] == 49.0 and rows[0]["callback_at_risk"] is True


def test_approval_pressure_skips_published():
    st = {"ep90": ep_published(90, "2026-10-15T01:38:23.101Z")}
    assert cu.approval_pressure(st, now=at("2026-10-16T00:00:00Z")) == []


# ── 판정 ──────────────────────────────────────────────────────────────────
def test_up_to_date_when_published_after_last_slot():
    st = {"ep88": ep_published(88, "2026-10-15T01:38:23.101Z")}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert p["verdict"] == cu.VERDICT_UP_TO_DATE and p["missed_slots"] == 0


def test_missed_slot_counted_when_publish_predates_slot():
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z")}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert p["missed_slots"] == 1            # 10-15(목) 01:30Z 슬롯 미발행
    assert p["verdict"] == cu.VERDICT_MISSED_RUN


def test_backlog_verdict_and_deficit():
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"),
          "ep89": ep_ready(89), "ep90": ep_ready(90)}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ,
                      max_publish=1)
    assert p["verdict"] == cu.VERDICT_BACKLOG
    assert p["publishable_count"] == 2 and p["missed_slots"] == 1
    assert p["deficit"] == 1 and p["recommended_publish"] == 1
    assert p["runs_to_clear"] == 2


def test_max_publish_widens_recommendation_but_not_beyond_deficit():
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"),
          "ep89": ep_ready(89), "ep90": ep_ready(90), "ep91": ep_ready(91)}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ,
                      max_publish=3)
    assert p["deficit"] == 1 and p["recommended_publish"] == 1     # 부채가 상한 역할
    assert p["runs_to_clear"] == 1


def test_incomplete_episode_blocks_that_slot_but_not_others():
    bad = ep_ready(89); bad["combined"]["media_url"] = None
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"), "ep89": bad,
          "ep90": ep_ready(90)}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert p["incomplete"] == [{"ep": 89, "missing": ["media_url"]}]
    assert [i["ep"] for i in p["publishable"]] == [90]
    assert any("러너1 재생성" in r for r in p["remedies"])


def test_generation_gap_when_only_incomplete():
    bad = ep_ready(89); bad["combined"]["media_url"] = None
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"), "ep89": bad}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert p["verdict"] == cu.VERDICT_GENERATION_GAP


def test_no_baseline_when_no_publish_history():
    st = {"ep90": ep_ready(90)}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert p["verdict"] == cu.VERDICT_NO_BASELINE
    assert p["last_publish"] is None and p["missed_slots"] == 0


def test_unapproved_episode_is_not_publishable():
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"),
          "ep89": {"approval": {"status": "HOLD"}, "combined": {"title": "t",
                   "duration_sec": 30.0, "media_url": "u"}}}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert p["publishable_count"] == 0 and p["stats"]["not_approved"] == 1


def test_stale_approval_surfaces_as_remedy():
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"),
          "ep89": {"approval": {"status": "PENDING", "requested_at": "2026-10-13T00:00:00Z"},
                   "combined": {"title": "t", "duration_sec": 30.0, "media_url": "u"}}}
    p = cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert p["stale_approvals"][0]["ep"] == 89
    assert any("재요청" in r for r in p["remedies"])


def test_plan_is_read_only():
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"), "ep89": ep_ready(89)}
    before = json.dumps(st, sort_keys=True)
    cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ)
    assert json.dumps(st, sort_keys=True) == before


# ── CLI ───────────────────────────────────────────────────────────────────
def test_main_up_to_date_exit_zero(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"ep88": ep_published(88, "2026-10-15T01:38:23.101Z")}), encoding="utf-8")
    assert cu.main(["--state", str(f), "--now", "2026-10-15T15:00:00Z"]) == cu.EXIT_OK


def test_main_backlog_exit_three(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"),
                             "ep89": ep_ready(89)}), encoding="utf-8")
    assert cu.main(["--state", str(f), "--now", "2026-10-15T15:00:00Z"]) == cu.EXIT_CATCHUP


def test_main_missing_state_exit_four(tmp_path):
    assert cu.main(["--state", str(tmp_path / "nope.json")]) == cu.EXIT_UNKNOWN


def test_report_contains_verdict_and_ep():
    st = {"ep88": ep_published(88, "2026-10-13T01:38:23.101Z"), "ep89": ep_ready(89, title="제목")}
    txt = cu.format_report(cu.build_plan(st, now=at("2026-10-15T15:00:00Z"), cron=CRON, tz_name=TZ))
    assert "catchup_needed" in txt and "ep89" in txt and "제목" in txt


def test_retention_constant_matches_telegram_docs():
    """Telegram Bot API: updates are not kept longer than 24 hours."""
    assert cu.APPROVAL_RETENTION_HOURS == 24.0
