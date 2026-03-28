"""
collectors/yahoo_finance.py
Yahoo Finance에서 실시간 시장 데이터를 수집한다.
yfinance 비공식 API 사용 — 장기 운영 시 Polygon.io로 교체 권장.
"""
import logging
from typing import Optional
import yfinance as yf
from config.settings import TICKER_MAP, ETF_CORE, ETF_SIGNAL

logger = logging.getLogger(__name__)


def _fetch_price(ticker_symbol: str) -> Optional[float]:
    """단일 티커 현재가 또는 변동률 조회"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="2d")
        if hist.empty or len(hist) < 1:
            logger.warning(f"[YF] 데이터 없음: {ticker_symbol}")
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.error(f"[YF] 조회 실패 {ticker_symbol}: {e}")
        return None


def _fetch_change_pct(ticker_symbol: str) -> Optional[float]:
    """전일 대비 변동률(%) 조회"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="5d")
        if hist.empty or len(hist) < 2:
            logger.warning(f"[YF] 변동률 계산 불가: {ticker_symbol}")
            return None
        prev_close = float(hist["Close"].iloc[-2])
        curr_close = float(hist["Close"].iloc[-1])
        if prev_close == 0:
            return None
        return round((curr_close - prev_close) / prev_close * 100, 2)
    except Exception as e:
        logger.error(f"[YF] 변동률 실패 {ticker_symbol}: {e}")
        return None


def collect_market_snapshot() -> dict:
    """
    시장 스냅샷 수집
    Returns: market_snapshot dict (10_output_schema.md 기준)
    """
    logger.info("[YF] 시장 스냅샷 수집 시작")

    sp500_chg = _fetch_change_pct(TICKER_MAP["SP500"])
    nasdaq_chg = _fetch_change_pct(TICKER_MAP["NASDAQ"])
    vix = _fetch_price(TICKER_MAP["VIX"])
    us10y = _fetch_price(TICKER_MAP["US10Y"])
    oil = _fetch_price(TICKER_MAP["OIL"])
    dxy = _fetch_price(TICKER_MAP["DXY"])

    # us10y는 ^TNX 기준으로 10배 나누기 필요 (yfinance 반환값이 4.39 형태면 그대로)
    if us10y and us10y > 20:
        us10y = us10y / 10

    snapshot = {
        "sp500": sp500_chg if sp500_chg is not None else 0.0,
        "nasdaq": nasdaq_chg if nasdaq_chg is not None else 0.0,
        "vix": vix if vix is not None else 20.0,
        "us10y": us10y if us10y is not None else 4.0,
        "oil": oil if oil is not None else 75.0,
        "dollar_index": dxy if dxy is not None else 100.0,
    }

    logger.info(f"[YF] 스냅샷 수집 완료: SPY {snapshot['sp500']:+.2f}% | VIX {snapshot['vix']:.2f}")
    return snapshot


def collect_etf_prices() -> dict:
    """
    ETF 가격 및 변동률 수집 (ETF Score 계산용)
    Returns: {ticker: {"price": float, "change_pct": float}}
    """
    logger.info("[YF] ETF 가격 수집 시작")
    result = {}

    for etf in ETF_CORE + ETF_SIGNAL:
        price = _fetch_price(TICKER_MAP.get(etf, etf))
        change = _fetch_change_pct(TICKER_MAP.get(etf, etf))
        result[etf] = {
            "price": price if price is not None else 0.0,
            "change_pct": change if change is not None else 0.0,
        }
        logger.debug(f"[YF] {etf}: {result[etf]['change_pct']:+.2f}%")

    logger.info(f"[YF] ETF 수집 완료: {len(result)}개")
    return result
