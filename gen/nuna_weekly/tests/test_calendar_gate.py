"""calendar_gate.py 단위 테스트 — 네트워크 없음."""
from __future__ import annotations

import re
from datetime import date

import pytest

from gen.nuna_weekly import calendar_gate


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("FORCE_RUN", raising=False)


def test_normal_week():
    # DN2026_0086: 토요일 생성·발행, 월~금 대상
    r = calendar_gate.evaluate(date(2026, 10, 24))  # 토
    assert r.ok and r.skip_reason is None
    assert r.episode_id == "nuna-2026-W43"
    assert re.match(r"^nuna-[0-9]{4}-W[0-9]{2}$", r.episode_id)
    assert r.publish_date == date(2026, 10, 24)
    assert r.us_days == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21),
                         date(2026, 10, 22), date(2026, 10, 23)]
    assert r.holidays == {}


def test_monday_run_publish_is_saturday():
    r = calendar_gate.evaluate(date(2026, 10, 19))
    assert r.publish_date == date(2026, 10, 24)


def test_thanksgiving_friday_is_trading_day():
    r = calendar_gate.evaluate(date(2026, 11, 28))  # 토, 추수감사절 주
    assert r.ok and r.publish_date == date(2026, 11, 28)
    assert r.holidays == {"2026-11-26": "Thanksgiving Day"}
    assert date(2026, 11, 27) in r.us_days and len(r.us_days) == 4


def test_holiday_removed():
    r = calendar_gate.evaluate(date(2026, 9, 10))  # Labor Day 주
    assert r.ok
    assert r.holidays == {"2026-09-07": "Labor Day"}
    assert date(2026, 9, 7) not in r.us_days and len(r.us_days) == 4


def test_sunday_skip_and_force():
    r = calendar_gate.evaluate(date(2026, 10, 25))
    assert not r.ok and r.skip_reason == calendar_gate.SKIP_WEEKEND
    assert calendar_gate.evaluate(date(2026, 10, 25), force=True).ok
    assert calendar_gate.evaluate(date(2026, 10, 24)).ok  # 토요일은 정규 실행일


def test_force_env(monkeypatch):
    monkeypatch.setenv("FORCE_RUN", "true")
    assert calendar_gate.evaluate(date(2026, 10, 25)).ok


def test_all_holidays(monkeypatch):
    monkeypatch.setattr(calendar_gate, "is_us_market_holiday", lambda d: (True, "X"))
    r = calendar_gate.evaluate(date(2026, 10, 22))
    assert not r.ok and r.skip_reason == calendar_gate.SKIP_ALL_HOLIDAYS and r.us_days == []
    assert calendar_gate.evaluate(date(2026, 10, 22), force=True).ok


def test_iso_year_boundary():
    r = calendar_gate.evaluate(date(2027, 1, 2))  # 토, ISO 2026-W53
    assert r.episode_id == "nuna-2026-W53"
    assert r.publish_date == date(2027, 1, 2)
    assert r.us_days[-1] == date(2027, 1, 1) or "2027-01-01" in r.holidays
