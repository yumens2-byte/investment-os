"""watchdog — 무발동 감지 회귀 테스트(2026-10-15).

핵심 회귀 대상
    - 신호가 하나도 안 읽히면 missed 가 아니라 unknown 이다(침묵 != 증거).
    - Actions 지연(실측 4h42m)을 오판하지 않도록 유예가 넉넉하다.
    - 기대 시각 이후 증거가 있으면 fresh.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gen.hero_shorts.tiktok import watchdog as wd

KST = ZoneInfo("Asia/Seoul")


def kst(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST).astimezone(UTC)


def sig(name, available=True, last_at=None, **kw):
    out = {"name": name, "available": available, "reason": None, "last_at": last_at}
    out.update(kw)
    return out


# ------------------------------------------------------------------ 크론 직전
def test_cron_previous_is_last_occurrence_not_next():
    now = kst(2026, 10, 15, 22, 59)  # 목요일
    prev = wd.cron_previous(wd.SKILL_CRON, now=now)
    assert wd._iso(prev) == "2026-10-15T01:30:00Z"  # 목 10:30 KST
    prev_act = wd.cron_previous(wd.ACTIONS_CRON, now=now, tz_name=wd.ACTIONS_CRON_TZ)
    assert wd._iso(prev_act) == "2026-10-15T01:00:00Z"


def test_cron_previous_before_first_weekday():
    now = kst(2026, 10, 19, 8, 0)  # 월요일 → 직전은 목 10-15
    prev = wd.cron_previous(wd.SKILL_CRON, now=now)
    assert wd._iso(prev) == "2026-10-15T01:30:00Z"


# ------------------------------------------------------------------ 판정
def test_fresh_when_activity_after_due():
    now = kst(2026, 10, 15, 12, 0)
    res = wd.decide(now, expect="skill", signals={
        "token_history": sig("token_history", last_at="2026-10-15T03:00:00Z"),
        "ledger": sig("ledger", last_at="2026-10-14T02:00:00Z"),
        "schedule": sig("schedule", next_run_at="2026-10-20T01:32:33Z", overdue=False)})
    assert res["verdict"] == wd.FRESH and res["last_activity_source"] == "token_history"


def test_ok_inside_grace_window():
    now = kst(2026, 10, 20, 11, 0)  # 기대 10:30 + 30분
    res = wd.decide(now, expect="skill", signals={
        "token_history": sig("token_history", last_at="2026-10-15T03:00:00Z"),
        "schedule": sig("schedule", next_run_at="2026-10-22T01:32:33Z", overdue=False)})
    assert res["verdict"] == wd.OK and res["readable_signals"]


def test_missed_after_grace_without_evidence():
    now = kst(2026, 10, 20, 14, 0)  # 기대 10:30 + 3.5h > 유예 2h
    res = wd.decide(now, expect="skill", signals={
        "token_history": sig("token_history", last_at="2026-10-15T03:00:00Z"),
        "schedule": sig("schedule", next_run_at="2026-10-20T01:32:33Z", overdue=True)})
    assert res["verdict"] == wd.MISSED
    assert any("과거" in p for p in res["problems"])


def test_unknown_when_no_signal_readable():
    now = kst(2026, 10, 20, 14, 0)
    res = wd.decide(now, expect="skill", signals={
        "token_history": sig("token_history", available=False, reason="이력 없음"),
        "ledger": sig("ledger", available=False, reason="원장 없음"),
        "schedule": sig("schedule", available=False, reason="gsk 부재")})
    assert res["verdict"] == wd.UNKNOWN and res["partial"] is True
    assert "침묵은 증거가 아니다" in res["reason"]


def test_actions_delay_not_flagged_as_missed():
    # 실측 형태: 기대 01:00Z, 실제 발화 05:42Z — 4h42m 지연은 유예 6h 안이다.
    now = kst(2026, 10, 15, 7, 0)  # 22:00Z 전날? 아니고 10-15 07:00 KST = 10-14T22:00Z
    res = wd.decide(kst(2026, 10, 15, 6, 0), expect="actions", signals={
        "actions": sig("actions", last_at="2026-10-15T05:42:36Z")})
    assert res["verdict"] == wd.FRESH
    # 기대(10-15T01:00Z) + 3h, 활동은 기대 이전 → 유예 6h 안이므로 ok
    res2 = wd.decide(kst(2026, 10, 15, 13, 0), expect="actions", signals={
        "actions": sig("actions", last_at="2026-10-13T01:05:00Z")})
    assert res2["verdict"] == wd.OK


def test_actions_missed_beyond_six_hours():
    # 기대(UTC 10-15T01:00Z) + 10h — 유예 6h 밖
    res = wd.decide(kst(2026, 10, 15, 20, 0), expect="actions", signals={
        "actions": sig("actions", last_at="2026-10-14T01:05:00Z")})
    assert res["verdict"] == wd.MISSED


def test_actions_cron_is_utc_not_kst():
    """같은 크론 문자열이라도 Actions 는 UTC — KST 로 해석하면 하루 어긋난다."""
    now = kst(2026, 10, 15, 22, 59)
    assert wd._iso(wd.cron_previous(wd.ACTIONS_CRON, now=now, tz_name=wd.ACTIONS_CRON_TZ)) \
        == "2026-10-15T01:00:00Z"
    assert wd._iso(wd.cron_previous(wd.ACTIONS_CRON, now=now, tz_name="Asia/Seoul")) \
        == "2026-10-14T16:00:00Z"  # KST 로 잘못 해석한 값 — 회귀 방지용 대조


def test_partial_coverage_flagged():
    now = kst(2026, 10, 20, 14, 0)
    res = wd.decide(now, expect="skill", signals={
        "token_history": sig("token_history", last_at="2026-10-15T03:00:00Z"),
        "ledger": sig("ledger", available=False, reason="원장 없음"),
        "schedule": sig("schedule", available=False, reason="gsk 부재")})
    assert res["partial"] is True and res["verdict"] == wd.MISSED


# ------------------------------------------------------------------ 신호 수집
def test_signal_actions_filters_hero_workflow():
    def opener(url, token):
        return {"workflow_runs": [
            {"name": wd.HERO_WORKFLOW_NAME, "created_at": "2026-10-15T05:42:36Z",
             "event": "schedule", "conclusion": "success"},
            {"name": "다른 워크플로우", "created_at": "2026-12-01T00:00:00Z"}]}
    s = wd.signal_actions(kst(2026, 10, 15, 22, 0), repo="o/r", token="t", opener=opener)
    assert s["available"] is True and s["last_at"] == "2026-10-15T05:42:36Z"
    assert len(s["runs"]) == 1


def test_signal_actions_unavailable_without_repo(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    s = wd.signal_actions(kst(2026, 10, 15, 22, 0), repo=None)
    assert s["available"] is False and "GITHUB_REPOSITORY" in s["reason"]


def test_signal_schedule_overdue_true():
    payload = {"data": {"scheduled_skills": [
        {"skill_name": wd.SKILL_NAME, "is_active": True, "state": "active",
         "next_run_at": "2026-10-15T01:32:33Z"}]}}
    s = wd.signal_schedule(kst(2026, 10, 20, 12, 0), lister=lambda: payload)
    assert s["available"] is True and s["overdue"] is True


def test_signal_schedule_ignores_foreign_skill():
    payload = {"data": {"scheduled_skills": [
        {"skill_name": "other", "is_active": True, "next_run_at": "2026-01-01T00:00:00Z"}]}}
    s = wd.signal_schedule(kst(2026, 10, 20, 12, 0), lister=lambda: payload)
    assert s["available"] is True and s["overdue"] is None


def test_signal_token_history_last_seen():
    doc = {"observations": [{"first_seen": "2026-10-15T13:22:46Z", "last_seen": "2026-10-15T13:22:46Z"}]}
    s = wd.signal_token_history(kst(2026, 10, 15, 22, 0), loader=lambda: doc)
    assert s["available"] is True and s["last_at"] == "2026-10-15T13:22:46Z"


def test_signal_ledger_collects_tiktok_stamps():
    state = {"ep88": {"tiktok": {"published_at": "2026-10-15T01:38:23.101Z",
                                 "verification": {"published_at": "2026-10-15T01:38:23.101Z"}}},
             "ep87": {"tiktok": {"published_at": "2026-10-13T02:00:00Z"}}}
    s = wd.signal_ledger(kst(2026, 10, 15, 22, 0), loader=lambda: state)
    assert s["available"] is True and s["last_at"] == "2026-10-15T01:38:23.101Z"
    assert s["entries"] == 2


def test_signal_ledger_unavailable_when_missing():
    s = wd.signal_ledger(kst(2026, 10, 15, 22, 0), loader=lambda: None)
    assert s["available"] is False and "원장 없음" in s["reason"]


# ------------------------------------------------------------------ 경보·리포트
def test_alert_silent_unless_missed(monkeypatch):
    called = []
    monkeypatch.setattr(wd, "alert", wd.alert)
    assert wd.alert({"verdict": wd.OK}) == "(경보 없음)"
    assert wd.alert({"verdict": wd.FRESH}) == "(경보 없음)"
    assert called == []


def test_format_report_includes_signals_and_coverage():
    res = wd.decide(kst(2026, 10, 20, 14, 0), expect="skill", signals={
        "token_history": sig("token_history", last_at="2026-10-15T03:00:00Z"),
        "schedule": sig("schedule", available=False, reason="gsk 부재")})
    text = wd.format_report(res)
    assert "MISSED" in text and "불가(gsk 부재)" in text and "부분 감시" in text


def test_exit_codes_mapping():
    assert wd.EXIT_CODES[wd.OK] == 0 and wd.EXIT_CODES[wd.FRESH] == 0
    assert wd.EXIT_CODES[wd.MISSED] == 3 and wd.EXIT_CODES[wd.UNKNOWN] == 4
