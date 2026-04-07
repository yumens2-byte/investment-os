"""
collectors/yahoo_finance.py (v1.7.0)
=====================================
v1.7.0 (2026-04-07): 운영 로그 정리 + PCR graceful degradation
- yfinance 라이브러리 자체 로거(레벨=ERROR) 차단:
  · GitHub Actions에서 모든 ticker가 1차 yfinance 시도 → 실패 → requests fallback
    으로 정상 복구되는데, yfinance가 ERROR 로그를 ticker당 2줄씩 도배하여
    morning 로그가 60줄 이상 noise로 채워지던 문제 해결
  · logging.getLogger("yfinance").setLevel(logging.CRITICAL)
  · 실패는 우리 logger가 INFO 한 줄로 통제 ([YF] requests fallback 시도: ^GSPC)
- collect_put_call_ratio() graceful degradation:
  · 진단 결과 GitHub Actions 클라우드 IP 환경에서 CBOE/Stooq/yfinance ^CPC 모두
    안정적인 무료 수집 불가 (CBOE는 2019-10-04 이후 historical 중단,
    FRED CBOE release(rid=200)에 PCR 시리즈 부재, Stooq/yfinance는 차단/멈춤)
  · 3-layer fallback 코드는 그대로 유지 (미래 복구 가능성 보존)
  · 실패 메시지를 다중 라인 → 단일 INFO 라인으로 축소
  · publish_eligible은 PCR 없이도 True (regime/etf/risk 엔진 모두 PCR 없이 정상)
  · 미래에 새 데이터 소스가 발견되면 _fetch_pcr_xxx 함수만 추가하면 됨

v1.6.0: PCR 수집 v2.0 — CBOE 공식 + Stooq 다중 fallback (2026-04-07)
- collect_put_call_ratio() 완전 재작성
  · ^CPCE Yahoo 의존 제거 (deprecated)
  · 1순위: CBOE JSON API
  · 2순위: CBOE HTML 페이지 정규식 파싱
  · 3순위: Stooq.com CSV (^cpc.us)
  · 4순위: Unknown fallback
- 정의되지 않은 _safe_fetch_price() 호출 제거
- import re 추가
- 응답에 pcr_source 필드 추가

v1.5.2: GitHub Actions 환경 멈춤 문제 수정
- yfinance 전체에 signal 기반 타임아웃 적용 (무한 대기 차단)
- requests fallback도 timeout=10 강제
- 수집 불가 시 None 반환 → validator 차단
"""
import logging
import re
import signal
import time

from typing import Optional

import yfinance as yf  # ← PCR/SMA/VOL 함수용
from config.settings import TICKER_MAP, ETF_CORE, ETF_SIGNAL

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# v1.7.0: yfinance 라이브러리 로그 noise 차단
# ─────────────────────────────────────────────────────────────────────────────
# 마스터 환경(GitHub Actions)에서 yfinance가 ticker당 1차 시도 후 ERROR 2줄 도배
# 후 requests fallback으로 정상 복구. ERROR 로그는 사용자에게 거짓 신호이므로
# CRITICAL 이상으로 차단. 우리 logger가 [YF] requests fallback 시도 한 줄로 통제.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
# peewee, urllib3 등 yfinance 의존 라이브러리가 만들 수 있는 추가 noise도 차단
logging.getLogger("peewee").setLevel(logging.CRITICAL)

# 티커 1개당 최대 대기 시간 (초)
_FETCH_TIMEOUT_SEC = 15

# PCR 수집 소스별 timeout
_PCR_TIMEOUT_SEC = 5


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
# D-2: CBOE Put/Call Ratio 수집 v2.0 (2026-04-07)
# ^CPCE Yahoo 의존 제거 → CBOE 공식 + Stooq 다중 fallback
# ──────────────────────────────────────────────────────────────

def collect_put_call_ratio() -> dict:
    """
    D-2: CBOE Equity Put/Call Ratio 수집 (v2.0)

    데이터 소스 우선순위:
      1순위: CBOE JSON API (cdn.cboe.com)
      2순위: CBOE HTML 페이지 정규식 파싱 (www.cboe.com)
      3순위: Stooq.com CSV (^cpc.us)
      4순위: Unknown fallback

    PCR 해석:
      > 1.2  → Extreme Bearish (과도한 풋 매수, 바닥 신호)
      > 1.0  → Bearish (풋 우세)
      0.7~1.0 → Neutral (정상 범위)
      < 0.7  → Bullish (Complacency, 과도한 콜 매수, 과열 경고)

    Returns:
        {
            "pcr":        float,  # PCR 값
            "pcr_state":  str,    # 상태 라벨
            "pcr_score":  int,    # 1~4
            "pcr_source": str,    # "cboe_json" | "cboe_html" | "stooq" | "fallback"
        }
    """
    logger.info("[YF_PCR] Put/Call Ratio 수집 시작 (v2.1 graceful)")

    # ── 1순위: CBOE JSON API ──
    pcr = _fetch_pcr_cboe_json()
    if pcr is not None:
        result = _build_pcr_result(pcr, source="cboe_json")
        logger.info(f"[YF_PCR] PCR={pcr} → {result['pcr_state']} (source=cboe_json)")
        return result

    # ── 2순위: CBOE HTML 파싱 ──
    pcr = _fetch_pcr_cboe_html()
    if pcr is not None:
        result = _build_pcr_result(pcr, source="cboe_html")
        logger.info(f"[YF_PCR] PCR={pcr} → {result['pcr_state']} (source=cboe_html)")
        return result

    # ── 3순위: Stooq fallback ──
    pcr = _fetch_pcr_stooq()
    if pcr is not None:
        result = _build_pcr_result(pcr, source="stooq")
        logger.info(f"[YF_PCR] PCR={pcr} → {result['pcr_state']} (source=stooq)")
        return result

    # ── 4순위: Graceful degradation (운영 결정 2026-04-07) ──
    # GitHub Actions 클라우드 IP 환경에서 무료 PCR 수집 불가 확정.
    # 파이프라인 무영향(publish_eligible=True), 한 줄 INFO만 남김.
    logger.info("[YF_PCR] Unknown (graceful — 모든 무료 소스 사용 불가, 파이프라인 무영향)")
    return {
        "pcr": 0.0,
        "pcr_state": "Unknown",
        "pcr_score": 2,
        "pcr_source": "graceful_unknown",
    }


def _fetch_pcr_cboe_json() -> Optional[float]:
    """CBOE JSON API에서 Equity P/C Ratio 최신값 추출"""
    try:
        import requests

        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/PUT-CALL-EQUITY_history.json"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

        resp = requests.get(url, headers=headers, timeout=_PCR_TIMEOUT_SEC)
        if resp.status_code != 200:
            logger.debug(f"[YF_PCR] CBOE JSON HTTP {resp.status_code}")
            return None

        data = resp.json()
        # 응답 구조 후보: { "data": [...] } 또는 { "history": [...] } 또는 [...]
        records = None
        if isinstance(data, dict):
            records = data.get("data") or data.get("history")
        elif isinstance(data, list):
            records = data

        if not records or not isinstance(records, list):
            logger.debug("[YF_PCR] CBOE JSON 구조 인식 실패")
            return None

        # 최신 레코드 추출 (마지막 또는 첫 항목)
        latest = records[-1] if isinstance(records[-1], dict) else records[0]
        if not isinstance(latest, dict):
            return None

        # 가능한 키 후보 순회
        for key in ("ratio", "pcr", "value", "close", "p/c ratio", "Close"):
            val = latest.get(key)
            if val is not None:
                try:
                    pcr = round(float(val), 3)
                    if 0.1 < pcr < 5.0:  # 합리적 범위
                        return pcr
                except (ValueError, TypeError):
                    continue

        logger.debug("[YF_PCR] CBOE JSON 값 추출 실패")
        return None

    except Exception as e:
        logger.debug(f"[YF_PCR] CBOE JSON 실패: {e}")
        return None


def _fetch_pcr_cboe_html() -> Optional[float]:
    """CBOE daily statistics HTML 페이지에서 Equity P/C Ratio 정규식 파싱"""
    try:
        import requests

        url = "https://www.cboe.com/us/options/market_statistics/daily/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }

        resp = requests.get(url, headers=headers, timeout=_PCR_TIMEOUT_SEC)
        if resp.status_code != 200:
            logger.debug(f"[YF_PCR] CBOE HTML HTTP {resp.status_code}")
            return None

        html = resp.text

        # "EQUITY PUT/CALL RATIO" 근처의 숫자 추출
        patterns = [
            r"EQUITY\s+PUT/CALL\s+RATIO[^0-9]*([0-9]+\.[0-9]+)",
            r"Equity\s+P/C\s+Ratio[^0-9]*([0-9]+\.[0-9]+)",
            r"equity[^0-9]{0,50}?([0-9]+\.[0-9]{2,3})",
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                try:
                    pcr = round(float(match.group(1)), 3)
                    if 0.1 < pcr < 5.0:
                        return pcr
                except (ValueError, IndexError):
                    continue

        logger.debug("[YF_PCR] CBOE HTML 패턴 매칭 실패")
        return None

    except Exception as e:
        logger.debug(f"[YF_PCR] CBOE HTML 실패: {e}")
        return None


def _fetch_pcr_stooq() -> Optional[float]:
    """
    Stooq.com에서 Equity Put/Call Ratio 수집 (CSV)

    심볼: ^cpc.us
    URL: https://stooq.com/q/d/l/?s=^cpc.us&i=d
    응답: CSV (Date,Open,High,Low,Close,Volume)
    """
    try:
        import requests

        url = "https://stooq.com/q/d/l/?s=^cpc.us&i=d"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/csv",
        }

        resp = requests.get(url, headers=headers, timeout=_PCR_TIMEOUT_SEC)
        if resp.status_code != 200:
            logger.debug(f"[YF_PCR] Stooq HTTP {resp.status_code}")
            return None

        text = resp.text.strip()
        if not text:
            return None

        lines = text.split("\n")
        if len(lines) < 2:
            return None

        # 첫 줄이 헤더인지 확인
        header = lines[0].lower()
        if "date" not in header:
            logger.debug("[YF_PCR] Stooq CSV 헤더 없음")
            return None

        # 마지막 줄 = 최신 데이터
        last = lines[-1].split(",")
        if len(last) < 5:
            return None

        # CSV 컬럼: Date,Open,High,Low,Close,Volume
        try:
            close_val = float(last[4])
            pcr = round(close_val, 3)
            if 0.1 < pcr < 5.0:
                return pcr
        except (ValueError, IndexError):
            pass

        logger.debug("[YF_PCR] Stooq Close 값 파싱 실패")
        return None

    except Exception as e:
        logger.debug(f"[YF_PCR] Stooq 실패: {e}")
        return None


def _build_pcr_result(pcr: float, source: str) -> dict:
    """PCR 값을 받아 state/score 판정 결과 dict 생성"""
    if pcr > 1.2:
        state, score = "Extreme Bearish", 4
    elif pcr > 1.0:
        state, score = "Bearish", 3
    elif pcr >= 0.7:
        state, score = "Neutral", 2
    else:
        state, score = "Bullish (Complacency)", 1

    return {
        "pcr": pcr,
        "pcr_state": state,
        "pcr_score": score,
        "pcr_source": source,
    }


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
# Tier 2 + Tier 3 수집 함수 (2026-04-01 추가, 2026-04-07 통합)
# 목적: 분석 엔진 고도화를 위한 추가 데이터 소스 수집
# 비용: 전부 yfinance 무료 데이터
# ──────────────────────────────────────────────────────────────
def collect_tier2_market_data() -> dict:
    """
    [Tier 2 + Tier 3] 분석 엔진 고도화용 추가 시장 데이터 수집
    ──────────────────────────────────────────────────
    수집 항목:
      Tier 2:
        - RSP (균등가중 S&P500) 등락률 → Market Breadth (T2-1)
        - SPY 등락률 → RSP 비교 기준
        - VIX3M (3개월 VIX) → Vol Term Structure (T2-2)
        - EEM (신흥국 ETF) → EM Stress (T2-5)
      Tier 3:
        - SOXX (반도체 ETF) → AI 모멘텀 (T3-1)
        - QQQ → SOXX 비교 기준
        - KRE (지역은행 ETF) → 은행 스트레스 (T3-3)

    Returns:
        {
          "rsp_change":  float,  # %
          "spy_change":  float,  # %
          "vix3m":       float,  # 포인트
          "eem_change":  float,  # %
          "soxx_change": float,  # %
          "qqq_change":  float,  # %
          "kre_change":  float,  # %
        }
        수집 실패 시 해당 필드 None
    """
    logger.info("[YF_T2] Tier 2/3 시장 데이터 수집 시작")

    result = {
        # Tier 2
        "rsp_change":  _fetch(TICKER_MAP.get("RSP",  "RSP"),  "change"),
        "spy_change":  _fetch(TICKER_MAP.get("SPY",  "SPY"),  "change"),
        "vix3m":       _fetch(TICKER_MAP.get("VIX3M","^VIX3M"),"price"),
        "eem_change":  _fetch(TICKER_MAP.get("EEM",  "EEM"),  "change"),
        # Tier 3
        "soxx_change": _fetch(TICKER_MAP.get("SOXX", "SOXX"), "change"),
        "qqq_change":  _fetch(TICKER_MAP.get("QQQ",  "QQQ"),  "change"),
        "kre_change":  _fetch(TICKER_MAP.get("KRE",  "KRE"),  "change"),
    }

    # 개별 필드 로깅 (장애 추적용)
    for key, val in result.items():
        if val is None:
            logger.warning(f"[YF_T2] {key} 수집 실패 → None")
        else:
            logger.info(f"[YF_T2] {key} = {val}")

    collected = sum(1 for v in result.values() if v is not None)
    logger.info(f"[YF_T2] 수집 완료: {collected}/{len(result)}개")
    return result
