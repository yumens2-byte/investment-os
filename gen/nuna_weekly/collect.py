"""
gen/nuna_weekly/collect.py — 월~금 미국시장 사실(facts) 수집 (설계서 D1, DN2026_0086 월~금 확대)
=========================================================================
    모드 (config.market_source):
      direct   — 실행 시점 Yahoo v8 chart 직접 수집 (기본). 종가·전일 대비 %·주간 누적.
      supabase — daily_snapshots (미국 거래일 D ↔ snapshot_date D+1, KST 키). 변화율만.
    공통      — daily_news 헤드라인·이슈, daily_analysis 레짐, daily_snapshots 공포탐욕·환율.

    fail-closed: 거래일의 S&P500·나스닥·VIX 중 하나라도 없으면 CollectError.
    us10y·wti·dxy 는 선택 지표 — 실패 시 None 으로 두고 경고만 남긴다.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import config
from .calendar_gate import week_candidates
from .models import DayFacts, Facts, GateResult

VERSION = "1.1.0"
log = logging.getLogger("nuna_weekly.collect")

# collectors/yahoo_finance.py::_fetch_with_requests 와 동일한 URL·헤더
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
YAHOO_PARAMS = {"interval": "1d", "range": "1mo"}
HTTP_TIMEOUT = 10
MAX_RETRIES = 2                 # 최초 1회 + 재시도 2회
RETRY_BACKOFF_SEC = 2.0

REQUIRED_KEYS = ("sp500", "nasdaq", "vix")
OPTIONAL_KEYS = ("us10y", "wti", "dxy")

HEADLINES_PER_ROW = 5
HEADLINES_MAX = 12
HEADLINE_MAX_CHARS = 200
TOP_ISSUES_MAX = 6


class CollectError(RuntimeError):
    """필수 사실 수집 실패 — fail-closed."""


def require_closed_sessions(gate: GateResult, now: datetime) -> None:
    """대상 거래일(월~금) 전체가 끝나기 전에는 주간 확정 종가를 조회하지 않는다.

    정규장 16:00 NY를 보수적 기준으로 사용한다(조기 폐장도 이 시각 이후 허용).
    --date는 회차 선택일 뿐, 실제 수집 시각을 과거나 미래로 바꾸지 않는다.
    """
    if now.tzinfo is None:
        raise CollectError("수집 시각에는 시간대가 필요합니다")
    if gate.us_days:
        last = max(gate.us_days)
        ready = datetime(last.year, last.month, last.day, 16, tzinfo=config.NY)
        if now < ready:
            raise CollectError(
                f"MARKET_NOT_READY: 월~금 미국장 종료 전 · 수집 가능 시각 "
                f"{ready.astimezone(config.KST).isoformat()} 이후 · "
                "토요일 생성 또는 완료된 과거 주차 --date 사용")


# --------------------------------------------------------------------------- #
# direct — Yahoo v8 chart
# --------------------------------------------------------------------------- #
def _parse_chart(key: str, body: Any) -> list[tuple[date, float]]:
    """Yahoo chart 응답 → [(거래소 현지 날짜, 종가)] 오름차순. 형식 오류는 티커명만 담아 예외."""
    try:
        res = body["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        tz_name = (res.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York"
        if not isinstance(stamps, list) or not isinstance(closes, list) or len(stamps) != len(closes):
            raise ValueError
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = config.NY
        bars: dict[date, float] = {}
        for ts, c in zip(stamps, closes, strict=True):
            if c is None:
                continue
            value = float(c)
            if not math.isfinite(value):
                raise ValueError
            bars[datetime.fromtimestamp(int(ts), tz).date()] = value
    except Exception:
        raise CollectError(f"Yahoo 응답 형식 오류: {key}({config.TICKERS[key]})") from None
    return sorted(bars.items())


def _fetch_bars(session, key: str, sleep: Callable[[float], None]) -> list[tuple[date, float]]:
    symbol = config.TICKERS[key]
    url = YAHOO_CHART_URL.format(symbol=symbol)
    last = ""
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            sleep(RETRY_BACKOFF_SEC * attempt)
        try:
            r = session.get(url, params=YAHOO_PARAMS, headers=YAHOO_HEADERS, timeout=HTTP_TIMEOUT)
        except Exception as e:
            last = type(e).__name__
            log.warning("Yahoo 호출 실패 %s 시도 %d: %s", key, attempt + 1, last)
            continue
        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            log.warning("Yahoo 호출 실패 %s 시도 %d: %s", key, attempt + 1, last)
            continue
        try:
            body = r.json()
        except Exception:
            raise CollectError(f"Yahoo 응답 형식 오류: {key}({symbol})") from None
        return _parse_chart(key, body)
    raise CollectError(f"Yahoo 수집 실패: {key}({symbol}) {last}")


def _pct(curr: float, prev: float) -> float | None:
    if not prev:
        return None
    return round((curr - prev) / prev * 100, 2)


def _day_value(bars: list[tuple[date, float]], d: date) -> tuple[float | None, float | None]:
    """(d 종가, 직전 바 대비 %). d 바가 없으면 (None, None)."""
    for i, (bd, c) in enumerate(bars):
        if bd == d:
            prev = bars[i - 1][1] if i > 0 else None
            return round(c, 2), (_pct(c, prev) if prev is not None else None)
    return None, None


def _week_cum(bars: list[tuple[date, float]], first: date, last: date) -> float | None:
    base = [c for bd, c in bars if bd < first]
    end = [c for bd, c in bars if bd == last]
    if not base or not end:
        return None
    return _pct(end[0], base[-1])


def _collect_direct(gate: GateResult, days: list[DayFacts], session,
                    sleep: Callable[[float], None]) -> tuple[float | None, float | None]:
    if session is None:
        import requests

        session = requests.Session()
    series: dict[str, list[tuple[date, float]]] = {}
    for key in REQUIRED_KEYS:
        series[key] = _fetch_bars(session, key, sleep)
    for key in OPTIONAL_KEYS:
        try:
            series[key] = _fetch_bars(session, key, sleep)
        except CollectError as e:
            log.warning("선택 지표 수집 실패 → None: %s", e)
            series[key] = []

    for df in days:
        if df.closed:
            continue
        d = date.fromisoformat(df.us_date)
        df.sp500_close, df.sp500_chg = _day_value(series["sp500"], d)
        df.nasdaq_close, df.nasdaq_chg = _day_value(series["nasdaq"], d)
        df.vix, df.vix_chg = _day_value(series["vix"], d)
        us10y, _ = _day_value(series["us10y"], d)
        if us10y is not None and us10y > 20:      # 기존 collectors/yahoo_finance.py 보정 규칙
            us10y = round(us10y / 10, 2)
        df.us10y = us10y
        df.wti, _ = _day_value(series["wti"], d)
        df.dxy, _ = _day_value(series["dxy"], d)
        missing = [k for k, v in (("sp500", df.sp500_chg), ("nasdaq", df.nasdaq_chg), ("vix", df.vix))
                   if v is None]
        if missing:
            raise CollectError(f"{df.us_date} 필수 지표 누락: {', '.join(missing)}")

    if not gate.us_days:
        return None, None
    first, last = gate.us_days[0], gate.us_days[-1]
    return _week_cum(series["sp500"], first, last), _week_cum(series["nasdaq"], first, last)


# --------------------------------------------------------------------------- #
# supabase — daily_snapshots
# --------------------------------------------------------------------------- #
def _num(v: Any) -> float | None:
    try:
        value = float(v) if v is not None else None
        return round(value, 2) if value is not None and math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _collect_supabase(gate: GateResult, days: list[DayFacts], store) -> None:
    log.warning("supabase 모드 — daily_snapshots 에 updated_at 이 없어 수집 시점(신선도)을 검증할 수 없음")
    keys = {d.isoformat(): (d + timedelta(days=1)).isoformat() for d in gate.us_days}
    rows = store.select_by_dates("daily_snapshots", "snapshot_date", sorted(set(keys.values())))
    by_date = {str(r.get("snapshot_date")): r for r in rows}
    for df in days:
        if df.closed:
            continue
        row = by_date.get(keys.get(df.us_date, ""))
        if not row:
            raise CollectError(f"{df.us_date} daily_snapshots 행 없음 (snapshot_date {keys.get(df.us_date)})")
        df.sp500_chg = _num(row.get("spy_change"))
        df.nasdaq_chg = _num(row.get("nasdaq_change"))
        df.vix = _num(row.get("vix"))
        df.us10y = _num(row.get("us10y"))
        df.wti = _num(row.get("oil_wti"))
        df.dxy = _num(row.get("dollar_index"))
        missing = [k for k, v in (("sp500", df.sp500_chg), ("nasdaq", df.nasdaq_chg), ("vix", df.vix))
                   if v is None]
        if missing:
            raise CollectError(f"{df.us_date} 필수 지표 누락: {', '.join(missing)}")


# --------------------------------------------------------------------------- #
# 공통 — 뉴스·레짐·심리
# --------------------------------------------------------------------------- #
def _as_list(v: Any) -> list[Any]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _clean(text: Any) -> str:
    return " ".join(str(text).split())[:HEADLINE_MAX_CHARS]


def _issue_text(item: Any) -> str:
    if isinstance(item, dict):
        topic = _clean(item.get("topic") or "")
        summary = _clean(item.get("summary") or "")
        return _clean(f"{topic}: {summary}" if topic and summary else (topic or summary))
    return _clean(item) if item is not None else ""


KEY_RISK_MAX = 200


def _key_risk(rows: list[dict[str, Any]]) -> str | None:
    """가장 최근 행의 key_risk(DN2026_0086). 문자열이 아니거나 비면 None."""
    for row in reversed(rows):
        t = _clean(row.get("key_risk")) if isinstance(row.get("key_risk"), str) else ""
        if t:
            return t[:KEY_RISK_MAX]
    return None


def _collect_news(gate: GateResult, store) -> tuple[list[dict[str, str]], list[str]]:
    dates = sorted({(d + timedelta(days=1)).isoformat() for d in gate.us_days} | {gate.publish_date.isoformat()})
    rows = sorted(store.select_by_dates("daily_news", "news_date", dates), key=lambda r: str(r.get("news_date")))
    headlines: list[dict[str, str]] = []
    issues: list[str] = []
    seen_h: set[str] = set()
    seen_i: set[str] = set()
    for row in rows:
        nd = str(row.get("news_date"))
        for h in _as_list(row.get("top_headlines"))[:HEADLINES_PER_ROW]:
            if not isinstance(h, str):
                continue
            t = _clean(h)
            if t and t not in seen_h and len(headlines) < HEADLINES_MAX:
                seen_h.add(t)
                headlines.append({"date": nd, "text": t})
        for it in _as_list(row.get("top_issues")):
            t = _issue_text(it)
            if t and t not in seen_i and len(issues) < TOP_ISSUES_MAX:
                seen_i.add(t)
                issues.append(t)
    return headlines, issues


def build_facts(gate: GateResult, store, session=None, now_kst: datetime | None = None,
                sleep: Callable[[float], None] = time.sleep) -> Facts:
    log.info("[NunaWeekly:collect] v%s 시작 · %s", VERSION, gate.episode_id)
    now = now_kst or datetime.now(config.KST)
    require_closed_sessions(gate, now)
    mode = config.market_source()
    trading = {d.isoformat() for d in gate.us_days}
    days = [DayFacts(us_date=d.isoformat(), closed=d.isoformat() not in trading,
                     holiday_name=gate.holidays.get(d.isoformat(), ""))
            for d in week_candidates(gate.publish_date)]

    week_sp = week_nq = None
    if mode == "direct":
        week_sp, week_nq = _collect_direct(gate, days, session, sleep)
    else:
        _collect_supabase(gate, days, store)

    headlines, issues = _collect_news(gate, store)
    news_dates = sorted({(d + timedelta(days=1)).isoformat() for d in gate.us_days}
                        | {gate.publish_date.isoformat()})
    key_risk = _key_risk(sorted(store.select_by_dates("daily_news", "news_date", news_dates),
                                key=lambda r: str(r.get("news_date"))))
    pub = gate.publish_date.isoformat()
    analysis = store.latest_row("daily_analysis", "analysis_date", pub) or {}
    snap = store.latest_row("daily_snapshots", "snapshot_date", pub) or {}
    fg = snap.get("fear_greed")
    try:
        fear_greed = None if fg is None else int(fg)
    except (TypeError, ValueError):
        fear_greed = None

    now = now.astimezone(config.KST)
    facts = Facts(
        episode_id=gate.episode_id, source=mode, fetched_at=now.isoformat(timespec="seconds"), days=days,
        week_sp500_chg=week_sp, week_nasdaq_chg=week_nq,
        regime=analysis.get("regime"), risk_level=analysis.get("risk_level"),
        regime_date=str(analysis["analysis_date"]) if analysis.get("analysis_date") else None,
        fear_greed=fear_greed, fear_greed_label=snap.get("fear_greed_label"),
        headlines=headlines, top_issues=issues, usdkrw=_num(snap.get("usdkrw")), key_risk=key_risk,
    )
    log.info("수집 완료: 모드 %s 거래일 %d 헤드라인 %d 이슈 %d 주간 S&P %s 나스닥 %s", mode, len(gate.us_days),
             len(headlines), len(issues), week_sp, week_nq)
    return facts
