"""
collectors/yahoo_finance.py (v1.5.1)
=====================================
Yahoo Finance 실시간 시장 데이터 수집.
yfinance 실패 시 requests 직접 호출로 fallback.
수집 실패 시 None 반환 — validator에서 차단.
"""
import logging
import time
from typing import Optional
from config.settings import TICKER_MAP, ETF_CORE, ETF_SIGNAL

logger = logging.getLogger(__name__)

# 수집 실패 기본값 (validator가 감지해서 발행 차단하는 센티넬 값)
_FALLBACK_SENTINEL = {
    "sp500": None,
    "nasdaq": None,
    "vix": None,
    "us10y": None,
    "oil": None,
    "dollar_index": None,
}


def _fetch_with_yfinance(ticker_symbol: str, mode: str = "change") -> Optional[float]:
    """yfinance로 데이터 수집 (change=변동률, price=현재가)"""
    try:
        import yfinance as yf
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="5d")
        if hist.empty or len(hist) < 2:
            return None
        if mode == "price":
            return float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])
        curr = float(hist["Close"].iloc[-1])
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100, 2)
    except Exception as e:
        logger.warning(f"[YF] yfinance 실패 {ticker_symbol}: {e}")
        return None


def _fetch_with_requests(ticker_symbol: str, mode: str = "change") -> Optional[float]:
    """
    requests + Yahoo Finance v8 API 직접 호출 (yfinance 실패 시 fallback).
    비공식 API이나 yfinance보다 안정적인 경우 있음.
    """
    try:
        import requests
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        params = {"interval": "1d", "range": "5d"}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        result = resp.json()
        closes = result["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None
        if mode == "price":
            return round(float(closes[-1]), 4)
        prev, curr = closes[-2], closes[-1]
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100, 2)
    except Exception as e:
        logger.warning(f"[YF] requests fallback 실패 {ticker_symbol}: {e}")
        return None


def _fetch(ticker_symbol: str, mode: str = "change") -> Optional[float]:
    """yfinance → requests 순서로 시도"""
    val = _fetch_with_yfinance(ticker_symbol, mode)
    if val is not None:
        return val
    logger.info(f"[YF] requests fallback 시도: {ticker_symbol}")
    time.sleep(0.5)  # 연속 요청 방지
    return _fetch_with_requests(ticker_symbol, mode)


def collect_market_snapshot() -> dict:
    """
    시장 스냅샷 수집.
    수집 실패 필드는 None 유지 — validator에서 감지해 발행 차단.
    """
    logger.info("[YF] 시장 스냅샷 수집 시작")

    sp500_chg  = _fetch(TICKER_MAP["SP500"],   "change")
    nasdaq_chg = _fetch(TICKER_MAP["NASDAQ"],  "change")
    vix        = _fetch(TICKER_MAP["VIX"],     "price")
    us10y      = _fetch(TICKER_MAP["US10Y"],   "price")
    oil        = _fetch(TICKER_MAP["OIL"],     "price")
    dxy        = _fetch(TICKER_MAP["DXY"],     "price")

    # ^TNX는 실제 금리값 (예: 4.39) — 간혹 10배로 오는 경우 보정
    if us10y is not None and us10y > 20:
        us10y = us10y / 10

    snapshot = {
        "sp500":        sp500_chg,
        "nasdaq":       nasdaq_chg,
        "vix":          vix,
        "us10y":        us10y,
        "oil":          oil,
        "dollar_index": dxy,
    }

    # 수집 성공률 로그
    collected = sum(1 for v in snapshot.values() if v is not None)
    total = len(snapshot)
    logger.info(
        f"[YF] 스냅샷 수집 완료: {collected}/{total}개 성공 | "
        f"SPY {snapshot['sp500']}% | VIX {snapshot['vix']}"
    )
    if collected < 3:
        logger.error(f"[YF] 수집 실패 필드 과다 ({total - collected}개) — 발행 차단 예정")

    return snapshot


def collect_etf_prices() -> dict:
    """ETF 가격 및 변동률 수집"""
    logger.info("[YF] ETF 가격 수집 시작")
    result = {}
    for etf in ETF_CORE + ETF_SIGNAL:
        symbol = TICKER_MAP.get(etf, etf)
        price  = _fetch(symbol, "price")
        change = _fetch(symbol, "change")
        result[etf] = {
            "price":      price  if price  is not None else 0.0,
            "change_pct": change if change is not None else 0.0,
        }
    logger.info(f"[YF] ETF 수집 완료: {len(result)}개")
    return result
