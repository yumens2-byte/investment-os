"""
gen/nuna_weekly/calendar_gate.py — 회차·발행일·미국 거래일 판정 (마스터 결정: 미국 시장 기준)
=============================================================================================
    회차      = 실행일(KST)의 ISO 주차 → episode_id "nuna-YYYY-Www"
    발행일    = 같은 ISO 주의 토요일 (DN2026_0086 — 목요일에서 변경)
    대상 거래일 = 같은 주 월~금 (미국 달력 날짜) 중 미국 휴장 제외 (DN2026_0086 — 월~수에서 확대)
    스킵      = 일요일 실행 / 월~금 전부 휴장. FORCE_RUN 은 스킵을 무시한다.
    한국 공휴일은 판정하지 않는다.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from config.us_market_holidays import is_us_market_holiday

from . import config
from .models import GateResult

VERSION = "1.1.0"
log = logging.getLogger("nuna_weekly.calendar_gate")

# DN2026_0086: 토요일 생성·발행 — 일요일만 스킵
SKIP_WEEKEND = "일요일 미발행"
SKIP_ALL_HOLIDAYS = "월~금 전부 미국 휴장"
US_DAYS_PER_WEEK = 5
PUBLISH_WEEKDAY = 6  # ISO 토요일


def episode_id_for(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"nuna-{iso_year}-W{iso_week:02d}"


def week_candidates(d: date) -> list[date]:
    """d 가 속한 ISO 주의 월~금."""
    monday = d - timedelta(days=d.isoweekday() - 1)
    return [monday + timedelta(days=i) for i in range(US_DAYS_PER_WEEK)]


def publish_date_for(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1) + timedelta(days=PUBLISH_WEEKDAY - 1)


def evaluate(run_date_kst: date, force: bool | None = None) -> GateResult:
    log.info("[NunaWeekly:calendar_gate] v%s 시작 · 실행일 %s", VERSION, run_date_kst.isoformat())
    force = config.force_run() if force is None else force

    episode_id = episode_id_for(run_date_kst)
    publish_date = publish_date_for(run_date_kst)
    us_days: list[date] = []
    holidays: dict[str, str] = {}
    for d in week_candidates(run_date_kst):
        is_holiday, name = is_us_market_holiday(d)
        if is_holiday:
            holidays[d.isoformat()] = name
        else:
            us_days.append(d)

    reason: str | None = None
    if run_date_kst.isoweekday() == 7:
        reason = SKIP_WEEKEND
    elif not us_days:
        reason = SKIP_ALL_HOLIDAYS

    if reason and force:
        log.warning("FORCE_RUN — 스킵 사유 무시: %s", reason)
        reason = None

    result = GateResult(ok=reason is None, episode_id=episode_id, publish_date=publish_date,
                        us_days=us_days, holidays=holidays, skip_reason=reason)
    log.info("게이트 판정: %s ok=%s 발행일 %s 거래일 %d 휴장 %s 사유=%s", episode_id, result.ok,
             publish_date.isoformat(), len(us_days), holidays or "-", reason or "-")
    return result
