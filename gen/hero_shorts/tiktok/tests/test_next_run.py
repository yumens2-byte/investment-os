"""next_run — 다음 실행 시각 해석·주입 회귀 테스트(B안 후속, 2026-10-15).

핵심 회귀 대상
    - 크론은 KST 로 해석한다(근거: gsk schedule list 실측 3건이 KST 해석과 일치).
    - 출처 우선순위 PIN > schedule > cron > cache.
    - 어느 출처도 실패해도 예외를 올리지 않고 기존 env 값을 지우지 않는다.
"""
import json
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gen.hero_shorts.tiktok import next_run as nx

KST = ZoneInfo("Asia/Seoul")


def kst(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST).astimezone(UTC)


def unpin(monkeypatch, tmp_path):
    monkeypatch.delenv(nx.ENV_PIN, raising=False)
    monkeypatch.delenv(nx.ENV_NEXT_RUN, raising=False)
    monkeypatch.delenv(nx.ENV_SOURCE, raising=False)
    monkeypatch.setenv(nx.ENV_CACHE, str(tmp_path / "next_run.json"))


def sched_payload(*entries):
    return {"data": {"scheduled_skills": list(entries)}}


def sched_entry(next_at, active=True, skill=nx.SKILL_NAME, name="Hero Shorts 러너3"):
    return {"skill_name": skill, "is_active": active, "next_run_at": next_at, "name": name,
            "scheduled_action_id": "act-1",
            "schedule_config": {"type": "CRON", "value": nx.DEFAULT_CRON}}


# ------------------------------------------------------------------ 크론 계산
def test_cron_next_weekday_kst():
    # 2026-10-15(목) 22:31 KST → 다음 발화는 10-20(화) 10:30 KST
    got = nx.cron_next(nx.DEFAULT_CRON, now=kst(2026, 10, 15, 22, 31))
    assert nx._iso(got) == "2026-10-20T01:30:00.000Z"


def test_cron_next_during_run_skips_today():
    # 러너가 10:32 에 돌면 그날 10:30 은 이미 지났다 → 다음은 10-22(목)
    got = nx.cron_next(nx.DEFAULT_CRON, now=kst(2026, 10, 20, 10, 32))
    assert nx._iso(got) == "2026-10-22T01:30:00.000Z"


def test_cron_next_minute_resolution_boundary():
    got = nx.cron_next("30 10 * * 2,4", now=kst(2026, 10, 20, 10, 29))
    assert nx._iso(got) == "2026-10-20T01:30:00.000Z"


def test_parse_cron_steps_and_names():
    spec = nx.parse_cron("*/15 8-10 * * mon")
    assert spec["minute"] == {0, 15, 30, 45}
    assert spec["hour"] == {8, 9, 10}
    assert spec["dow"] == {1}


def test_parse_cron_rejects_bad_input():
    bad = ("30 10 * *", "30 10 * * 2,4,5,6,7,8", "70 10 * * *",
           "10-5 10 * * *", "*/0 10 * * *", "완전히 잘못된 크론")
    for expr in bad:
        with pytest.raises(ValueError):
            nx.parse_cron(expr)


def test_sunday_alias_7_normalized():
    assert nx.parse_cron("0 0 * * 7")["dow"] == {0}


# ------------------------------------------------------------------ 스케줄러 조회
def test_from_schedule_picks_earliest_active(monkeypatch):
    payload = sched_payload(sched_entry("2026-10-22T01:32:33Z"),
                            sched_entry("2026-10-20T01:32:33Z"))
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (payload, None))
    dt, ev, err = nx.from_schedule(now=kst(2026, 10, 15, 22, 31))
    assert err is None
    assert nx._iso(dt) == "2026-10-20T01:32:33.000Z"
    assert ev["cron"] == nx.DEFAULT_CRON


def test_from_schedule_ignores_foreign_and_inactive(monkeypatch):
    payload = sched_payload(sched_entry("2026-10-20T01:32:33Z", active=False),
                            sched_entry("2026-10-20T01:32:33Z", skill="other-skill"))
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (payload, None))
    dt, ev, err = nx.from_schedule(now=kst(2026, 10, 15, 22, 31))
    assert dt is None
    assert "활성 스케줄 없음" in err
    assert len(ev["schedule_candidates"]) == 1


def test_from_schedule_rejects_past_value(monkeypatch):
    payload = sched_payload(sched_entry("2026-10-14T01:32:33Z"))
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (payload, None))
    dt, ev, err = nx.from_schedule(now=kst(2026, 10, 15, 22, 31))
    assert dt is None and "cron 폴백" in err
    assert ev["stale_schedule_value"] == "2026-10-14T01:32:33.000Z"


# ------------------------------------------------------------------ 해석 우선순위
def test_resolve_prefers_schedule_over_cron(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    payload = sched_payload(sched_entry("2026-10-20T01:32:33Z"))
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (payload, None))
    res = nx.resolve(now=kst(2026, 10, 15, 22, 31))
    assert res["source"] == "schedule"
    assert res["next_run_at"] == "2026-10-20T01:32:33.000Z"


def test_resolve_cron_fallback_when_schedule_fails(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (None, "gsk 없음"))
    res = nx.resolve(now=kst(2026, 10, 15, 22, 31))
    assert res["source"] == "cron"
    assert res["next_run_at"] == "2026-10-20T01:30:00.000Z"
    assert "gsk 없음" in res["reason"]


def test_resolve_pin_wins_and_skips_query(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    monkeypatch.setenv(nx.ENV_PIN, "1")
    monkeypatch.setenv(nx.ENV_NEXT_RUN, "2026-11-01T00:00:00.000Z")
    calls = []
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (calls.append(1), (None, "x"))[1])
    res = nx.resolve(now=kst(2026, 10, 15, 22, 31))
    assert res["source"] == "pin"
    assert res["next_run_at"] == "2026-11-01T00:00:00.000Z"
    assert calls == []


def test_resolve_unknown_when_cron_bad_and_no_cache(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (None, "gsk 없음"))
    monkeypatch.setenv(nx.ENV_CRON, "완전히 잘못된 크론")
    res = nx.resolve(now=kst(2026, 10, 15, 22, 31))
    assert res["source"] == "none" and res["next_run_at"] is None
    assert "gsk 없음" in res["reason"]


# ------------------------------------------------------------------ 주입(apply)
def test_apply_sets_env_and_writes_cache(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (None, "gsk 없음"))
    res = nx.apply(now=kst(2026, 10, 15, 22, 31), force=True)
    assert res["reused"] is False
    assert os.environ[nx.ENV_NEXT_RUN] == "2026-10-20T01:30:00.000Z"
    assert os.environ[nx.ENV_SOURCE] == "cron"
    assert res["cache_written"]
    cached = nx.load_cache()
    assert cached["next_run_at"] == "2026-10-20T01:30:00.000Z"
    assert cached["stale"] is False


def test_apply_without_force_reuses_existing(monkeypatch, tmp_path):
    monkeypatch.delenv(nx.ENV_PIN, raising=False)
    monkeypatch.setenv(nx.ENV_NEXT_RUN, "2026-10-20T01:32:33.000Z")
    monkeypatch.setenv(nx.ENV_SOURCE, "schedule")
    calls = []
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (calls.append(1), (None, "x"))[1])
    res = nx.apply(now=kst(2026, 10, 15, 22, 31))
    assert res["reused"] is True and res["source"] == "schedule" and calls == []


def test_apply_force_recomputes_over_stale_env(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    monkeypatch.setenv(nx.ENV_NEXT_RUN, "2026-01-01T00:00:00.000Z")
    payload = sched_payload(sched_entry("2026-10-20T01:32:33Z"))
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (payload, None))
    res = nx.apply(now=kst(2026, 10, 15, 22, 31), force=True)
    assert res["reused"] is False
    assert os.environ[nx.ENV_NEXT_RUN] == "2026-10-20T01:32:33.000Z"


def test_apply_keeps_env_when_every_source_fails(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    monkeypatch.setenv(nx.ENV_NEXT_RUN, "2030-01-01T00:00:00.000Z")
    monkeypatch.setenv(nx.ENV_CRON, "잘못된 크론")
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (None, "gsk 없음"))
    res = nx.apply(now=kst(2026, 10, 15, 22, 31), force=True)
    assert os.environ[nx.ENV_NEXT_RUN] == "2030-01-01T00:00:00.000Z"
    assert "유지" in res["reason"]


def test_apply_pin_is_never_overwritten(monkeypatch):
    monkeypatch.setenv(nx.ENV_PIN, "1")
    monkeypatch.setenv(nx.ENV_NEXT_RUN, "2026-11-01T00:00:00.000Z")
    res = nx.apply(force=True)
    assert res["source"] == "pin" and res["reused"] is True
    assert os.environ[nx.ENV_NEXT_RUN] == "2026-11-01T00:00:00.000Z"


# ------------------------------------------------------------------ 캐시·표시
def test_stale_cache_is_rejected(monkeypatch, tmp_path):
    unpin(monkeypatch, tmp_path)
    p = tmp_path / "next_run.json"
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat().replace("+00:00", "Z")
    p.write_text(json.dumps({"version": 1, "next_run_at": "2026-10-01T00:00:00.000Z",
                             "resolved_at": old, "source": "cron"}), encoding="utf-8")
    assert nx.load_cache()["stale"] is True
    monkeypatch.setenv(nx.ENV_CRON, "잘못된 크론")
    monkeypatch.setattr(nx, "fetch_schedule", lambda **kw: (None, "gsk 없음"))
    res = nx.resolve(now=kst(2026, 10, 15, 22, 31))
    assert res["next_run_at"] is None
    assert "오래됨" in res["reason"]


def test_report_line_unknown_and_known():
    assert "미상" in nx.report_line({"next_run_at": None, "reason": "해석 실패"})
    line = nx.report_line({"next_run_at": "2026-10-20T01:30:00.000Z",
                           "source": "cron", "tz": "Asia/Seoul"})
    assert "2026-10-20 10:30 KST" in line and "화" in line and "cron" in line
