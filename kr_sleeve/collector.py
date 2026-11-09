"""
kr_sleeve/collector.py — 국내 ETF 일봉 수집 (DN2026_0108)

경로: 네이버 차트 XML (fchart.stock.naver.com/sise.nhn, timeframe=day).
  PoC 2026-11-09 GitHub Actions 실측 성공(run 42551279354·42551398005). 비공식 경로 — 약관·SLA 없음.
  응답 item data 형식: YYYYMMDD|시가|고가|저가|종가|거래량
규칙:
  - 종목당 HTTP 1회. 실패는 재시도하지 않고 SleeveSkip(SOURCE_HTTP)로 올린다(읽기지만 비용·차단 위험 최소화).
  - 오늘 봉은 KRX 마감 후(15:40 KST~)에만 완료 봉으로 본다. 장중 실행이면 직전 거래일 종가를 쓴다.
  - 실패 값은 0.0이 아니라 None(STD-DATA-02). 등락률·SMA·거래량은 기록·표시용이며 비중 계산에 쓰지 않는다.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from kr_sleeve import config
from kr_sleeve.errors import (
    PRICE_OUT_OF_RANGE,
    SOURCE_FORMAT,
    SOURCE_HTTP,
    SOURCE_INSUFFICIENT,
    SOURCE_STALE,
    SleeveSkip,
)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


@dataclass(frozen=True)
class Bar:
    day: date
    close: float
    volume: int | None


@dataclass(frozen=True)
class Quote:
    code: str
    as_of: str               # 마지막 완료 봉 날짜 YYYY-MM-DD
    close: float
    prev_close: float
    change_pct: float
    volume: int | None
    sma5: float | None
    sma20: float | None
    completed_bars: int

    def to_dict(self) -> dict:
        return asdict(self)


def parse_fchart(xml_text: str) -> list[Bar]:
    """fchart XML → 날짜 오름차순 Bar 목록. 형식이 다르면 SleeveSkip(SOURCE_FORMAT)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SleeveSkip(SOURCE_FORMAT, f"XML 파싱 실패: {e}") from e
    bars: list[Bar] = []
    for item in root.iter("item"):
        parts = (item.get("data") or "").split("|")
        if len(parts) < 6:
            raise SleeveSkip(SOURCE_FORMAT, f"필드 수 {len(parts)} < 6")
        raw_day, raw_close, raw_vol = parts[0], parts[4], parts[5]
        try:
            day = datetime.strptime(raw_day, "%Y%m%d").date()
        except ValueError as e:
            raise SleeveSkip(SOURCE_FORMAT, f"날짜 형식 오류: {raw_day[:12]!r}") from e
        if raw_close in ("", "null"):
            continue  # 종가 없는 봉(거래 정지 등)은 건너뛴다
        try:
            close = float(raw_close)
            volume = int(raw_vol) if raw_vol not in ("", "null") else None
        except ValueError as e:
            raise SleeveSkip(SOURCE_FORMAT, f"숫자 형식 오류: {raw_close[:12]!r}") from e
        bars.append(Bar(day, close, volume))
    if not bars:
        raise SleeveSkip(SOURCE_FORMAT, "item 없음")
    for prev, cur in zip(bars, bars[1:], strict=False):
        if cur.day <= prev.day:
            raise SleeveSkip(SOURCE_FORMAT, f"날짜 단조 증가 위반: {prev.day} → {cur.day}")
    return bars


def completed_bars(bars: list[Bar], now_kst: datetime) -> list[Bar]:
    """장중이면 오늘 봉(진행 중)을 뺀다."""
    today = now_kst.date()
    done_today = (now_kst.hour, now_kst.minute) >= config.KRX_CLOSE_DONE
    return [b for b in bars if b.day < today or (b.day == today and done_today)]


MARKET_OPEN, MARKET_CLOSED, MARKET_UNKNOWN = "open", "closed", "unknown"


def kr_market_state(bars: list[Bar], now_kst: datetime) -> str:
    """오늘 국내 시장 상태.
    주말 → closed. 평일 개장 확인 시각(09:10 KST) 이후: 오늘 봉이 있으면 open, 없으면 closed(휴장).
    그 전에는 봉으로 알 수 없으므로 unknown — 발행 게이트는 unknown을 발행 불가로 본다(default-deny,
    R4 C1: 장 시작 전 평일 휴장일 오발행 방지.
    휴장 캘린더 의존성은 기존 requirements 불변을 위해 넣지 않음)."""
    if now_kst.weekday() >= 5:
        return MARKET_CLOSED
    if (now_kst.hour, now_kst.minute) < config.KRX_OPEN_CHECK:
        return MARKET_UNKNOWN
    return MARKET_OPEN if any(b.day == now_kst.date() for b in bars) else MARKET_CLOSED


def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return round(sum(values[-n:]) / n, 2)


def build_quote(code: str, bars: list[Bar], now_kst: datetime) -> Quote:
    done = completed_bars(bars, now_kst)
    if len(done) < config.MIN_COMPLETED_BARS:
        raise SleeveSkip(SOURCE_INSUFFICIENT, f"{code} 완료 봉 {len(done)}개")
    last, prev = done[-1], done[-2]
    if (now_kst.date() - last.day).days > config.SOURCE_MAX_STALE_DAYS:
        raise SleeveSkip(SOURCE_STALE, f"{code} 마지막 완료 봉 {last.day}")
    if last.close <= 0 or prev.close <= 0:
        raise SleeveSkip(PRICE_OUT_OF_RANGE, f"{code} 종가 ≤ 0")
    change = (last.close - prev.close) / prev.close * 100
    if abs(change) > config.PRICE_LIMIT_PCT:
        raise SleeveSkip(PRICE_OUT_OF_RANGE, f"{code} 전일 대비 {change:+.1f}%")
    closes = [b.close for b in done]
    return Quote(
        code=code,
        as_of=last.day.isoformat(),
        close=last.close,
        prev_close=prev.close,
        change_pct=round(change, 2),
        volume=last.volume,
        sma5=_sma(closes, 5),
        sma20=_sma(closes, 20),
        completed_bars=len(done),
    )


def fetch_fchart(code: str, http_get: Callable = requests.get) -> str:
    params = {"symbol": code, "timeframe": "day", "count": config.FCHART_COUNT, "requestType": 0}
    try:
        resp = http_get(FCHART_URL, params=params, headers=_UA, timeout=config.HTTP_TIMEOUT_SEC)
    except requests.RequestException as e:
        raise SleeveSkip(SOURCE_HTTP, f"{code} {type(e).__name__}") from e
    if resp.status_code != 200:
        raise SleeveSkip(SOURCE_HTTP, f"{code} HTTP {resp.status_code}")
    return resp.text


def collect(now_utc: datetime, http_get: Callable = requests.get) -> tuple[dict[str, Quote], str]:
    """2종 시세와 오늘 국내 시장 상태. 1종이라도 실패하면 SleeveSkip(전체 skipped, DN2026_0108 D-6)."""
    now_kst = now_utc.astimezone(KST)
    quotes: dict[str, Quote] = {}
    states = []
    for etf in config.KR_ETFS:
        bars = parse_fchart(fetch_fchart(etf.code, http_get))
        quotes[etf.code] = build_quote(etf.code, bars, now_kst)
        states.append(kr_market_state(bars, now_kst))
        logger.info("[KRSleeve] 시세 %s as_of=%s close=%s chg=%s%%",
                    etf.short, quotes[etf.code].as_of, quotes[etf.code].close, quotes[etf.code].change_pct)
    if MARKET_CLOSED in states:
        return quotes, MARKET_CLOSED
    if MARKET_UNKNOWN in states:
        return quotes, MARKET_UNKNOWN
    return quotes, MARKET_OPEN
