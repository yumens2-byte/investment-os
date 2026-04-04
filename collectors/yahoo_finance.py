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


def collect_etf_sma() -> dict:
    """
    E-1: ETF 6종목의 SMA5/SMA20 수집 — 중기 트렌드 판별.

    yfinance period="1mo" (약 22영업일) 종가 수집 → SMA5/SMA20 계산.
    SMA5 > SMA20 → "golden_cross" (상승 추세)
    SMA5 < SMA20 → "dead_cross" (하락 추세)
    else → "flat"

    Returns:
        {
          "QQQM": {"sma5": 180.5, "sma20": 178.2, "trend": "golden_cross"},
          "XLK":  {"sma5": 200.1, "sma20": 202.3, "trend": "dead_cross"},
          ...
        }
    """
    logger.info("[YF_SMA] ETF SMA 수집 시작")
    result = {}

    for etf in ETF_CORE:
        try:
            symbol = TICKER_MAP.get(etf, etf)
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")

            if hist is None or len(hist) < 5:
                # requests fallback
                closes = _fetch_closes_fallback(symbol, 22)
                if not closes or len(closes) < 5:
                    result[etf] = {"sma5": 0, "sma20": 0, "trend": "flat"}
                    continue
            else:
                closes = hist["Close"].tolist()

            # SMA 계산
            sma5 = sum(closes[-5:]) / min(5, len(closes[-5:])) if len(closes) >= 5 else 0
            sma20 = sum(closes[-20:]) / min(20, len(closes[-20:])) if len(closes) >= 20 else sma5

            # 트렌드 판별
            if sma5 > 0 and sma20 > 0:
                diff_pct = (sma5 - sma20) / sma20 * 100
                if diff_pct > 0.5:
                    trend = "golden_cross"
                elif diff_pct < -0.5:
                    trend = "dead_cross"
                else:
                    trend = "flat"
            else:
                trend = "flat"

            result[etf] = {
                "sma5": round(sma5, 2),
                "sma20": round(sma20, 2),
                "trend": trend,
            }
        except Exception as e:
            logger.warning(f"[YF_SMA] {etf} SMA 수집 실패 (무시): {e}")
            result[etf] = {"sma5": 0, "sma20": 0, "trend": "flat"}

    trends = {etf: d["trend"] for etf, d in result.items()}
    logger.info(f"[YF_SMA] 수집 완료: {trends}")
    return result


# ──────────────────────────────────────────────────────────────
# D-2: CBOE Put/Call Ratio 수집 (2026-04-04)
# ──────────────────────────────────────────────────────────────

def collect_put_call_ratio() -> dict:
    """
    D-2: CBOE Equity Put/Call Ratio 수집.

    PCR > 1.2 → Extreme Bearish (과도한 풋 매수)
    PCR > 1.0 → Bearish (풋 우세)
    PCR 0.7~1.0 → Neutral
    PCR < 0.7 → Bullish/과열 경고 (과도한 콜 매수)

    Returns:
        {"pcr": 0.85, "pcr_state": "Neutral", "pcr_score": 2}
    """
    logger.info("[YF_PCR] Put/Call Ratio 수집 시작")

    try:
        symbol = "^CPCE"
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")

        pcr = None
        if hist is not None and len(hist) > 0:
            pcr = round(float(hist["Close"].iloc[-1]), 3)
        else:
            # requests fallback
            try:
                _, pcr_val = _safe_fetch_price(symbol)
                if pcr_val is not None:
                    pcr = round(pcr_val, 3)
            except Exception:
                pass

        if pcr is None or pcr <= 0:
            logger.warning("[YF_PCR] PCR 수집 실패 → 기본값")
            return {"pcr": 0.0, "pcr_state": "Unknown", "pcr_score": 2}

        # 상태 판정
        if pcr > 1.2:
            state, score = "Extreme Bearish", 4
        elif pcr > 1.0:
            state, score = "Bearish", 3
        elif pcr >= 0.7:
            state, score = "Neutral", 2
        else:
            state, score = "Bullish (Complacency)", 1

        logger.info(f"[YF_PCR] PCR={pcr} → {state} (score={score})")
        return {"pcr": pcr, "pcr_state": state, "pcr_score": score}

    except Exception as e:
        logger.warning(f"[YF_PCR] 수집 실패 (무시): {e}")
        return {"pcr": 0.0, "pcr_state": "Unknown", "pcr_score": 2}


# ──────────────────────────────────────────────────────────────
# D-4: ETF 섹터별 자금 흐름 (거래량 트렌드) (2026-04-04)
# ──────────────────────────────────────────────────────────────

def collect_etf_volume_trend() -> dict:
    """
    D-4: ETF 6종목의 20일 평균 거래량 대비 오늘 거래량 비율.

    ratio > 1.3 → "inflow" (자금 유입 추정, 비중 상향 근거)
    ratio < 0.7 → "outflow" (자금 유출 추정, 비중 하향 근거)
    else → "normal"

    Returns:
        {
          "XLE": {"today_vol": 25000000, "avg_vol": 18000000, "ratio": 1.39, "flow": "inflow"},
          "QQQM": {"today_vol": 5000000, "avg_vol": 8000000, "ratio": 0.63, "flow": "outflow"},
          ...
        }
    """
    logger.info("[YF_VOL] ETF 거래량 트렌드 수집 시작")
    result = {}

    for etf in ETF_CORE:
        try:
            symbol = TICKER_MAP.get(etf, etf)
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")

            if hist is None or len(hist) < 5 or "Volume" not in hist.columns:
                result[etf] = {"today_vol": 0, "avg_vol": 0, "ratio": 1.0, "flow": "normal"}
                continue

            volumes = hist["Volume"].tolist()
            today_vol = volumes[-1] if volumes else 0
            avg_vol = sum(volumes[:-1]) / max(1, len(volumes) - 1) if len(volumes) > 1 else today_vol

            ratio = today_vol / max(1, avg_vol)

            if ratio > 1.3:
                flow = "inflow"
            elif ratio < 0.7:
                flow = "outflow"
            else:
                flow = "normal"

            result[etf] = {
                "today_vol": int(today_vol),
                "avg_vol": int(avg_vol),
                "ratio": round(ratio, 2),
                "flow": flow,
            }
        except Exception as e:
            logger.warning(f"[YF_VOL] {etf} 거래량 수집 실패 (무시): {e}")
            result[etf] = {"today_vol": 0, "avg_vol": 0, "ratio": 1.0, "flow": "normal"}

    flows = {etf: d["flow"] for etf, d in result.items()}
    logger.info(f"[YF_VOL] 수집 완료: {flows}")
    return result


def _fetch_closes_fallback(symbol: str, days: int = 22) -> list:
    """SMA용 종가 리스트 fallback (requests 직접 호출)"""
    try:
        import requests as req
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?range={days}d&interval=1d"
        )
        resp = req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = resp.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [c for c in closes if c is not None]
    except Exception:
        return []


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
