"""
collectors/yahoo_finance.py (v1.5.2)
=====================================
v1.5.2: GitHub Actions 환경 멈춤 문제 수정
- yfinance 전체에 signal 기반 타임아웃 적용 (무한 대기 차단)
- requests fallback도 timeout=10 강제
- 수집 불가 시 None 반환 → validator 차단
"""
import logging
import signal
import time
from typing import Optional
from config.settings import TICKER_MAP, ETF_CORE, ETF_SIGNAL

logger = logging.getLogger(__name__)

# 티커 1개당 최대 대기 시간 (초)
_FETCH_TIMEOUT_SEC = 15


class _TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _TimeoutError("fetch timeout")


def _run_with_timeout(fn, timeout_sec: int):
    """
    signal.alarm 기반 타임아웃.
    Windows에서는 signal.SIGALRM 미지원 → 타임아웃 없이 실행.
    """
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)
        try:
            return fn()
        finally:
            signal.alarm(0)
    except AttributeError:
        # Windows: SIGALRM 없음 → 그냥 실행
        return fn()
    except _TimeoutError:
        logger.warning(f"[YF] {timeout_sec}초 타임아웃 초과")
        return None


def _fetch_with_yfinance(ticker_symbol: str, mode: str = "change") -> Optional[float]:
    """yfinance로 데이터 수집 — 타임아웃 적용"""
    def _do():
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

    return _run_with_timeout(_do, _FETCH_TIMEOUT_SEC)


def _fetch_with_requests(ticker_symbol: str, mode: str = "change") -> Optional[float]:
    """requests로 Yahoo Finance v8 API 직접 호출 (fallback)"""
    try:
        import requests
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        params = {"interval": "1d", "range": "5d"}
        resp = requests.get(
            url, headers=headers, params=params,
            timeout=10  # 반드시 timeout 명시
        )
        if resp.status_code != 200:
            logger.warning(f"[YF] requests HTTP {resp.status_code}: {ticker_symbol}")
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
    """yfinance → requests 순서로 시도. 둘 다 실패 시 None."""
    val = _fetch_with_yfinance(ticker_symbol, mode)
    if val is not None:
        return val
    logger.info(f"[YF] requests fallback 시도: {ticker_symbol}")
    time.sleep(0.3)
    return _fetch_with_requests(ticker_symbol, mode)


def collect_market_snapshot() -> dict:
    """
    시장 스냅샷 수집.
    수집 실패 필드 → None 유지 → validator에서 FAIL 처리.
    """
    logger.info("[YF] 시장 스냅샷 수집 시작")

    sp500_chg  = _fetch(TICKER_MAP["SP500"],  "change")
    nasdaq_chg = _fetch(TICKER_MAP["NASDAQ"], "change")
    vix        = _fetch(TICKER_MAP["VIX"],    "price")
    us10y      = _fetch(TICKER_MAP["US10Y"],  "price")
    oil        = _fetch(TICKER_MAP["OIL"],    "price")
    dxy        = _fetch(TICKER_MAP["DXY"],    "price")

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

    collected = sum(1 for v in snapshot.values() if v is not None)
    total = len(snapshot)
    logger.info(f"[YF] 스냅샷 완료: {collected}/{total}개 | SPY={snapshot['sp500']} VIX={snapshot['vix']}")
    if collected < 3:
        logger.error(f"[YF] 수집 실패 과다 ({total-collected}개 None) — validator 차단 예정")

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


def collect_fx_rates() -> dict:
    """
    FX 환율 3종 수집 (yfinance 무료 — 추가 API 비용 없음)
    - USD/KRW : KRW=X
    - EUR/USD : EURUSD=X
    - USD/JPY : JPY=X

    Returns:
        {"usdkrw": float, "eurusd": float, "usdjpy": float}
        수집 실패 시 해당 필드 None
    """
    logger.info("[YF_FX] FX 환율 수집 시작")

    fx_tickers = {
        "usdkrw": "KRW=X",
        "eurusd":  "EURUSD=X",
        "usdjpy":  "JPY=X",
    }

    result = {}
    for key, ticker in fx_tickers.items():
        val = _fetch(ticker, "price")
        if val is not None:
            result[key] = round(float(val), 4)
            logger.info(f"[YF_FX] {key.upper()} = {result[key]}")
        else:
            result[key] = None
            logger.warning(f"[YF_FX] {key.upper()} 수집 실패 → None")

    logger.info(f"[YF_FX] FX 수집 완료: {result}")
    return result


def collect_crypto_prices() -> dict:
    """
    BTC/ETH 실시간 가격 수집 (yfinance)

    Returns:
        {
          "btc_usd": 85000.0,
          "eth_usd": 3200.0,
          "btc_change_pct": -1.2,   # 24h 등락률
          "eth_change_pct": 0.8,
        }
    """
    TICKERS = {
        "btc_usd": "BTC-USD",
        "eth_usd": "ETH-USD",
    }
    result = {}

    for key, ticker in TICKERS.items():
        try:
            price, change = _fetch_price_and_change(ticker)
            coin = key.split("_")[0]
            result[key] = price
            result[f"{coin}_change_pct"] = change
        except Exception as e:
            logger.warning(f"[YF_CRYPTO] {ticker} 수집 실패: {e}")
            result[key] = None
            coin = key.split("_")[0]
            result[f"{coin}_change_pct"] = 0.0

    if result.get("btc_usd"):
        logger.info(
            f"[YF_CRYPTO] BTC=${result.get('btc_usd',0):,.0f} "
            f"({result.get('btc_change_pct',0):+.2f}%) | "
            f"ETH=${result.get('eth_usd',0):,.0f} "
            f"({result.get('eth_change_pct',0):+.2f}%)"
        )
    return result


def _fetch_price_and_change(ticker: str):
    """yfinance fallback 방식으로 가격 + 24h 등락률 수집"""
    import requests as req
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2d"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = req.get(url, headers=headers, timeout=10)
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            prev, curr = closes[-2], closes[-1]
            change = round((curr - prev) / prev * 100, 2)
            return round(curr, 2), change
        elif len(closes) == 1:
            return round(closes[-1], 2), 0.0
    except Exception as e:
        logger.warning(f"[YF_CRYPTO] {ticker} fallback 실패: {e}")
    return None, 0.0


# ──────────────────────────────────────────────────────────────
# Tier 2 수집 함수 (2026-04-01 추가)
# 목적: 분석 엔진 고도화를 위한 추가 데이터 소스 수집
# 비용: 전부 yfinance 무료 데이터
# ──────────────────────────────────────────────────────────────

def collect_tier2_market_data() -> dict:
    """
    Tier 2 분석 엔진용 추가 시장 데이터 수집
    ──────────────────────────────────────────
    수집 항목:
      - RSP 등락률:  균등가중 S&P500 ETF (Market Breadth 판단)
      - SPY 등락률:  시총가중 S&P500 ETF (RSP와 비교용)
      - VIX3M 가격:  3개월 VIX (Vol Term Structure 판단)
      - EEM 등락률:  신흥국 ETF (EM Stress 판단)

    Returns:
        {
          "rsp_change_pct": float,  # RSP 일간 등락률 (%)
          "spy_change_pct": float,  # SPY 일간 등락률 (%)
          "vix3m": float,           # VIX3M 현재 가격
          "eem_change_pct": float,  # EEM 일간 등락률 (%)
        }
        수집 실패 필드는 None — 엔진에서 안전하게 처리
    """
    logger.info("[YF_T2] Tier 2 시장 데이터 수집 시작")

    result = {}

    # T2-1: RSP (Invesco S&P500 Equal Weight) — Market Breadth
    rsp_chg = _fetch(TICKER_MAP.get("RSP", "RSP"), "change")
    result["rsp_change_pct"] = rsp_chg
    if rsp_chg is not None:
        logger.info(f"[YF_T2] RSP 등락률: {rsp_chg:+.2f}%")
    else:
        logger.warning("[YF_T2] RSP 수집 실패 → None")

    # T2-1: SPY (비교 대상) — RSP-SPY 스프레드 산출용
    spy_chg = _fetch(TICKER_MAP.get("SPY", "SPY"), "change")
    result["spy_change_pct"] = spy_chg
    if spy_chg is not None:
        logger.info(f"[YF_T2] SPY 등락률: {spy_chg:+.2f}%")
    else:
        logger.warning("[YF_T2] SPY 수집 실패 → None")

    # T2-2: VIX3M (CBOE 3-Month Volatility Index)
    vix3m = _fetch(TICKER_MAP.get("VIX3M", "^VIX3M"), "price")
    result["vix3m"] = vix3m
    if vix3m is not None:
        logger.info(f"[YF_T2] VIX3M: {vix3m:.2f}")
    else:
        logger.warning("[YF_T2] VIX3M 수집 실패 → None")

    # T2-5: EEM (iShares MSCI Emerging Markets ETF)
    eem_chg = _fetch(TICKER_MAP.get("EEM", "EEM"), "change")
    result["eem_change_pct"] = eem_chg
    if eem_chg is not None:
        logger.info(f"[YF_T2] EEM 등락률: {eem_chg:+.2f}%")
    else:
        logger.warning("[YF_T2] EEM 수집 실패 → None")

    collected = sum(1 for v in result.values() if v is not None)
    logger.info(f"[YF_T2] Tier 2 수집 완료: {collected}/{len(result)}개")
    return result


# ──────────────────────────────────────────────────────────────
# Tier 2 확장 수집 함수 (2026-04-01 추가)
# ──────────────────────────────────────────────────────────────

def collect_tier2_market_data() -> dict:
    """
    [Tier 2] 분석 엔진 고도화용 추가 시장 데이터 수집
    ──────────────────────────────────────────────────
    수집 항목:
      - RSP (균등가중 S&P500) 등락률 → Market Breadth 시그널 (T2-1)
      - SPY 등락률 → RSP와 비교용
      - VIX3M (3개월 VIX) 가격 → Vol Term Structure 시그널 (T2-2)
      - EEM (신흥국 ETF) 등락률 → EM Stress 시그널 (T2-5)

    Returns:
        {
          "rsp_change":  float,  # RSP 일간 등락률 (%)
          "spy_change":  float,  # SPY 일간 등락률 (%)
          "vix3m":       float,  # VIX3M 가격 (포인트)
          "eem_change":  float,  # EEM 일간 등락률 (%)
        }
        수집 실패 시 해당 필드 None
    """
    logger.info("[YF_T2] Tier 2 시장 데이터 수집 시작")

    result = {}

    # T2-1: RSP (균등가중 S&P500) — Market Breadth 산출용
    result["rsp_change"] = _fetch(TICKER_MAP.get("RSP", "RSP"), "change")

    # T2-1: SPY — RSP와 비교하여 breadth spread 계산
    result["spy_change"] = _fetch(TICKER_MAP.get("SPY", "SPY"), "change")

    # T2-2: VIX3M (3개월 VIX) — VIX/VIX3M 비율로 기간구조 판단
    result["vix3m"] = _fetch(TICKER_MAP.get("VIX3M", "^VIX3M"), "price")

    # T2-5: EEM (신흥국 ETF) — 신흥국 스트레스 감지
    result["eem_change"] = _fetch(TICKER_MAP.get("EEM", "EEM"), "change")

    # ── Tier 3 추가 수집 (2026-04-01) ──
    # T3-1: SOXX (반도체 ETF) — AI 모멘텀 판별용
    result["soxx_change"] = _fetch(TICKER_MAP.get("SOXX", "SOXX"), "change")

    # T3-1: QQQ — SOXX와 비교하여 AI 리더십 판단
    result["qqq_change"] = _fetch(TICKER_MAP.get("QQQ", "QQQ"), "change")

    # T3-3: KRE (지역은행 ETF) — 은행 스트레스 감지
    result["kre_change"] = _fetch(TICKER_MAP.get("KRE", "KRE"), "change")

    collected = sum(1 for v in result.values() if v is not None)
    logger.info(
        f"[YF_T2] 수집 완료: {collected}/{len(result)}개 | "
        f"RSP={result.get('rsp_change')} SPY={result.get('spy_change')} "
        f"VIX3M={result.get('vix3m')} EEM={result.get('eem_change')} "
        f"SOXX={result.get('soxx_change')} KRE={result.get('kre_change')}"
    )
    return result
