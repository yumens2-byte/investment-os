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
    r = calendar_gate.evaluate(date(2026, 10, 22))  # 목
    assert r.ok and r.skip_reason is None
    assert r.episode_id == "nuna-2026-W43"
    assert re.match(r"^nuna-[0-9]{4}-W[0-9]{2}$", r.episode_id)
    assert r.publish_date == date(2026, 10, 22)
    assert r.us_days == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    assert r.holidays == {}


def test_monday_run_publish_is_thursday():
    r = calendar_gate.evaluate(date(2026, 10, 19))
    assert r.publish_date == date(2026, 10, 22)


def test_holiday_removed():
    r = calendar_gate.evaluate(date(2026, 9, 10))  # Labor Day 주
    assert r.ok
    assert r.holidays == {"2026-09-07": "Labor Day"}
    assert date(2026, 9, 7) not in r.us_days and len(r.us_days) == 2


def test_weekend_skip_and_force():
    r = calendar_gate.evaluate(date(2026, 10, 24))
    assert not r.ok and r.skip_reason == calendar_gate.SKIP_WEEKEND
    assert calendar_gate.evaluate(date(2026, 10, 24), force=True).ok


def test_force_env(monkeypatch):
    monkeypatch.setenv("FORCE_RUN", "true")
    assert calendar_gate.evaluate(date(2026, 10, 25)).ok


def test_all_holidays(monkeypatch):
    monkeypatch.setattr(calendar_gate, "is_us_market_holiday", lambda d: (True, "X"))
    r = calendar_gate.evaluate(date(2026, 10, 22))
    assert not r.ok and r.skip_reason == calendar_gate.SKIP_ALL_HOLIDAYS and r.us_days == []
    assert calendar_gate.evaluate(date(2026, 10, 22), force=True).ok


def test_iso_year_boundary():
    r = calendar_gate.evaluate(date(2027, 1, 1))  # 금, ISO 2026-W53
    assert r.episode_id == "nuna-2026-W53"
    assert r.publish_date == date(2026, 12, 31)
