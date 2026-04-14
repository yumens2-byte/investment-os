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

Priority A (2026-04-11):
- collect_market_snapshot(): Gold(GC=F) price + change 추가
- collect_tier2_market_data(): IWM, TLT, MOVE 추가
- collect_spy_sma(): 신규 함수 (SMA5/20/50/200)
"""
import logging
import re
import signal
import time

from typing import Optional

import yfinance as yf  # ← PCR/SMA/VOL 함수용
from config.settings import TICKER_MAP, ETF_CORE, ETF_SIGNAL

logger = logging.getLogger(__name__)

VERSION = "1.7.0"

# ─────────────────────────────────────────────────────────────────────────────
# v1.7.0: yfinance 라이브러리 로그 noise 차단
# ─────────────────────────────────────────────────────────────────────────────
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
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
            timeout=10
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

    v1.7.0: Gold(GC=F) price + change 추가
    """
    logger.info("[YF] 시장 스냅샷 수집 시작")

    sp500_chg  = _fetch(TICKER_MAP["SP500"],  "change")
    nasdaq_chg = _fetch(TICKER_MAP["NASDAQ"], "change")
    vix        = _fetch(TICKER_MAP["VIX"],    "price")
    us10y      = _fetch(TICKER_MAP["US10Y"],  "price")
    oil        = _fetch(TICKER_MAP["OIL"],    "price")
    dxy        = _fetch(TICKER_MAP["DXY"],    "price")
    # ── v1.7.0: Gold 추가 ──────────────────────────────────
    gold       = _fetch(TICKER_MAP.get("GOLD", "GC=F"), "price")
    gold_chg   = _fetch(TICKER_MAP.get("GOLD", "GC=F"), "change")
    # ── 금 가격 정수 보정 (소수점 불필요, 4761.8999 → 4762) ──
    if gold is not None:
        gold = round(gold)

    if us10y is not None and us10y > 20:
        us10y = us10y / 10

    snapshot = {
        "sp500":        sp500_chg,
        "nasdaq":       nasdaq_chg,
        "vix":          vix,
        "us10y":        us10y,
        "oil":          oil,
        "dollar_index": dxy,
        "gold":         gold,
        "gold_change":  gold_chg,
    }

    collected = sum(1 for v in snapshot.values() if v is not None)
    total = len(snapshot)
    if snapshot.get("gold_change") is not None:
        logger.info(
            f"[YF] 스냅샷 완료: {collected}/{total}개 | "
            f"SPY={snapshot['sp500']} VIX={snapshot['vix']} "
            f"Gold=${snapshot.get('gold')} ({snapshot.get('gold_change'):+.2f}%)"
        )
    else:
        logger.info(
            f"[YF] 스냅샷 완료: {collected}/{total}개 | "
            f"SPY={snapshot['sp500']} VIX={snapshot['vix']} Gold=${snapshot.get('gold')}"
        )
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
    """
    logger.info("[YF_SMA] ETF SMA 수집 시작")
    result = {}

    for etf in ETF_CORE:
        try:
            symbol = TICKER_MAP.get(etf, etf)
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")

            if hist is None or len(hist) < 5:
                closes = _fetch_closes_fallback(symbol, 22)
                if not closes or len(closes) < 5:
                    result[etf] = {"sma5": 0, "sma20": 0, "trend": "flat"}
                    continue
            else:
                closes = hist["Close"].tolist()

            sma5  = sum(closes[-5:])  / min(5,  len(closes[-5:]))  if len(closes) >= 5  else 0
            sma20 = sum(closes[-20:]) / min(20, len(closes[-20:])) if len(closes) >= 20 else sma5

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
# ──────────────────────────────────────────────────────────────

def collect_put_call_ratio() -> dict:
    """
    D-2: CBOE Equity Put/Call Ratio 수집 (v2.2)

    데이터 소스 우선순위:
      1순위: CBOE JSON API
      2순위: CBOE HTML 페이지 정규식 파싱
      3순위: Stooq.com CSV
      4순위: yfinance SPY 옵션 체인 계산 (v2.2 추가)
             → SPY put/call volume 합산으로 PCR 직접 계산
             → 1~3순위 전부 차단 시 fallback
      5순위: Unknown graceful
    """
    logger.info("[YF_PCR] Put/Call Ratio 수집 시작 (v2.2 graceful)")

    pcr = _fetch_pcr_cboe_json()
    if pcr is not None:
        result = _build_pcr_result(pcr, source="cboe_json")
        logger.info(f"[YF_PCR] PCR={pcr} → {result['pcr_state']} (source=cboe_json)")
        return result

    pcr = _fetch_pcr_cboe_html()
    if pcr is not None:
        result = _build_pcr_result(pcr, source="cboe_html")
        logger.info(f"[YF_PCR] PCR={pcr} → {result['pcr_state']} (source=cboe_html)")
        return result

    pcr = _fetch_pcr_stooq()
    if pcr is not None:
        result = _build_pcr_result(pcr, source="stooq")
        logger.info(f"[YF_PCR] PCR={pcr} → {result['pcr_state']} (source=stooq)")
        return result

    # ── 4순위: yfinance SPY 옵션 체인 계산 (v2.2 추가) ──
    pcr = _fetch_pcr_yfinance()
    if pcr is not None:
        result = _build_pcr_result(pcr, source="yfinance_spy")
        logger.info(f"[YF_PCR] PCR={pcr} → {result['pcr_state']} (source=yfinance_spy)")
        return result

    logger.info("[YF_PCR] Unknown (graceful — 모든 소스 사용 불가, 파이프라인 무영향)")
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
        records = None
        if isinstance(data, dict):
            records = data.get("data") or data.get("history")
        elif isinstance(data, list):
            records = data

        if not records or not isinstance(records, list):
            logger.debug("[YF_PCR] CBOE JSON 구조 인식 실패")
            return None

        latest = records[-1] if isinstance(records[-1], dict) else records[0]
        if not isinstance(latest, dict):
            return None

        for key in ("ratio", "pcr", "value", "close", "p/c ratio", "Close"):
            val = latest.get(key)
            if val is not None:
                try:
                    pcr = round(float(val), 3)
                    if 0.1 < pcr < 5.0:
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
    """Stooq.com에서 Equity Put/Call Ratio 수집 (CSV)"""
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

        header = lines[0].lower()
        if "date" not in header:
            logger.debug("[YF_PCR] Stooq CSV 헤더 없음")
            return None

        last = lines[-1].split(",")
        if len(last) < 5:
            return None

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


def _fetch_pcr_yfinance() -> Optional[float]:
    """
    yfinance SPY 옵션 체인으로 Put/Call Ratio 직접 계산 (v2.2 신규).

    방법:
      - SPY 가장 가까운 만기 3개의 put/call volume 합산
      - PCR = total_put_volume / total_call_volume
      - 복수 만기 합산으로 단일 만기 볼륨 부족 문제 방지

    주의:
      - CBOE 공식 Equity PCR과 방향성 동일, 절댓값은 약간 다를 수 있음
      - SPY = S&P500 대표 → 시장 전반 Put/Call 심리 반영 충분
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker("SPY")
        expirations = ticker.options
        if not expirations:
            logger.debug("[YF_PCR] yfinance SPY 만기일 없음")
            return None

        # 가장 가까운 만기 최대 3개 합산 (볼륨 충분성 확보)
        total_put  = 0.0
        total_call = 0.0
        used = 0

        for exp in expirations[:3]:
            try:
                chain      = ticker.option_chain(exp)
                put_vol    = float(chain.puts["volume"].fillna(0).sum())
                call_vol   = float(chain.calls["volume"].fillna(0).sum())
                total_put  += put_vol
                total_call += call_vol
                used += 1
            except Exception:
                continue

        if used == 0 or total_call <= 0:
            logger.debug("[YF_PCR] yfinance SPY 옵션 볼륨 없음")
            return None

        pcr = round(total_put / total_call, 3)

        # 유효 범위 검증 (0.3 ~ 3.0 사이만 허용)
        if not (0.3 < pcr < 3.0):
            logger.debug(f"[YF_PCR] yfinance PCR 범위 이상: {pcr}")
            return None

        logger.debug(
            f"[YF_PCR] yfinance 계산 완료: "
            f"put={total_put:.0f} call={total_call:.0f} PCR={pcr} (만기{used}개)"
        )
        return pcr

    except Exception as e:
        logger.debug(f"[YF_PCR] yfinance 실패: {e}")
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

    ratio > 1.3 → "inflow"
    ratio < 0.7 → "outflow"
    else → "normal"
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
    FX 환율 3종 수집 (yfinance 무료)
    - USD/KRW : KRW=X
    - EUR/USD : EURUSD=X
    - USD/JPY : JPY=X
    """
    logger.info("[YF_FX] FX 환율 수집 시작")

    fx_tickers = {
        "usdkrw": "KRW=X",
        "eurusd": "EURUSD=X",
        "usdjpy": "JPY=X",
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
            f"[YF_CRYPTO] BTC=${result.get('btc_usd', 0):,.0f} "
            f"({result.get('btc_change_pct', 0):+.2f}%) | "
            f"ETH=${result.get('eth_usd', 0):,.0f} "
            f"({result.get('eth_change_pct', 0):+.2f}%)"
        )
    return result


def _fetch_price_and_change(ticker: str):
    """yfinance fallback 방식으로 가격 + 24h 등락률 수집"""
    import requests as req
    # range=5d: 주말/공휴일 연휴에도 2개 이상 거래일 데이터 보장
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
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
# Priority A (2026-04-11): IWM, TLT, MOVE 추가
# ──────────────────────────────────────────────────────────────

def collect_tier2_market_data() -> dict:
    """
    [Tier 2 + Tier 3 + Priority A] 분석 엔진 고도화용 추가 시장 데이터 수집

    수집 항목:
      Tier 2: RSP, SPY, VIX3M, EEM
      Tier 3: SOXX, QQQ, KRE
      Priority A (v1.7.0): IWM, TLT, MOVE(실패 허용)
    """
    logger.info("[YF_T2] Tier 2/3/A 시장 데이터 수집 시작")

    result = {
        # Tier 2
        "rsp_change":  _fetch(TICKER_MAP.get("RSP",   "RSP"),    "change"),
        "spy_change":  _fetch(TICKER_MAP.get("SPY",   "SPY"),    "change"),
        "vix3m":       _fetch(TICKER_MAP.get("VIX3M", "^VIX3M"), "price"),
        "eem_change":  _fetch(TICKER_MAP.get("EEM",   "EEM"),    "change"),
        # Tier 3
        "soxx_change": _fetch(TICKER_MAP.get("SOXX",  "SOXX"),   "change"),
        "qqq_change":  _fetch(TICKER_MAP.get("QQQ",   "QQQ"),    "change"),
        "kre_change":  _fetch(TICKER_MAP.get("KRE",   "KRE"),    "change"),
        # Priority A v1.7.0 신규
        "iwm_change":  _fetch(TICKER_MAP.get("IWM",   "IWM"),    "change"),
        "tlt_change":  _fetch(TICKER_MAP.get("TLT",   "TLT"),    "change"),
        "move_index":  _fetch("^MOVE", "price"),                            # 실패 허용
    }

    # MOVE 수집 실패는 경고만 (validator FAIL 제외)
    if result.get("move_index") is None:
        logger.warning("[YF_T2] ^MOVE 수집 실패 → None 유지 (엔진에서 중립 처리)")

    for key, val in result.items():
        if val is None:
            logger.warning(f"[YF_T2] {key} 수집 실패 → None")
        else:
            logger.info(f"[YF_T2] {key} = {val}")

    collected = sum(1 for v in result.values() if v is not None)
    logger.info(f"[YF_T2] 수집 완료: {collected}/{len(result)}개")
    return result


def collect_spy_sma() -> dict:
    """
    SPY SMA 4종 수집 — Priority A v1.0.0 (2026-04-11)
    ─────────────────────────────────────────────────────
    수집: SMA5 / SMA20 / SMA50 / SMA200
    목적: 기술적 추세 판단 (골든크로스/데스크로스/200선 이탈)

    SMA200 계산을 위해 270일치 종가 수집 (영업일 여유 포함).

    Returns:
        {
          "spy_price":  float | None,
          "spy_sma5":   float | None,
          "spy_sma20":  float | None,
          "spy_sma50":  float | None,
          "spy_sma200": float | None,
        }
    """
    logger.info("[YF_SMA] SPY SMA 수집 시작 (SMA5/20/50/200)")
  

    closes = _fetch_closes_fallback("SPY", days=270)

    if not closes or len(closes) < 5:
        logger.warning(f"[YF_SMA] SPY 종가 부족({len(closes) if closes else 0}일) → 전체 None")
        return {k: None for k in ["spy_price", "spy_sma5", "spy_sma20", "spy_sma50", "spy_sma200"]}

    def _sma(n: int):
        if len(closes) < n:
            logger.warning(f"[YF_SMA] SMA{n} 계산 데이터 부족({len(closes)}일 < {n})")
            return None
        return round(sum(closes[-n:]) / n, 2)

    result = {
        "spy_price":  round(closes[-1], 2),
        "spy_sma5":   _sma(5),
        "spy_sma20":  _sma(20),
        "spy_sma50":  _sma(50),
        "spy_sma200": _sma(200),
    }

    logger.info(
        f"[YF_SMA] SPY ${result['spy_price']} | "
        f"SMA50=${result['spy_sma50']} | "
        f"SMA200=${result['spy_sma200']} | "
        f"above200={'Yes' if result['spy_price'] and result['spy_sma200'] and result['spy_price'] > result['spy_sma200'] else 'No'}"
    )
    return result

def collect_sector_etfs() -> dict:
    """
    [Priority B] B-3 섹터 로테이션 + B-4 Copper/Gold 수집 (2026-04-11)
    ─────────────────────────────────────────────────────────────────────
    섹터 ETF 6종 + 구리 선물 일간 등락률 수집.

    섹터 구분:
      방어: XLV(헬스케어), XLU(유틸리티), XLP(필수소비재)
      경기민감: XLI(산업재), XLRE(리츠), XLB(소재)
      경기선행: COPPER(HG=F) vs Gold → Copper/Gold Ratio 변화

    Returns:
        {
          "xlv_change", "xlu_change", "xli_change",
          "xlp_change", "xlre_change", "xlb_change",
          "copper_price", "copper_change"
        }
    """
    logger.info("[YF_SECTOR] 섹터 ETF + 구리 수집 시작")

    result = {
        # ── B-3: 섹터 ETF 6종 ──────────────────────────────
        "xlv_change":  _fetch(TICKER_MAP.get("XLV",  "XLV"),  "change"),
        "xlu_change":  _fetch(TICKER_MAP.get("XLU",  "XLU"),  "change"),
        "xli_change":  _fetch(TICKER_MAP.get("XLI",  "XLI"),  "change"),
        "xlp_change":  _fetch(TICKER_MAP.get("XLP",  "XLP"),  "change"),
        "xlre_change": _fetch(TICKER_MAP.get("XLRE", "XLRE"), "change"),
        "xlb_change":  _fetch(TICKER_MAP.get("XLB",  "XLB"),  "change"),
        # ── B-4: 구리 선물 ──────────────────────────────────
        "copper_price":  _fetch(TICKER_MAP.get("COPPER", "HG=F"), "price"),
        "copper_change": _fetch(TICKER_MAP.get("COPPER", "HG=F"), "change"),
    }

    for key, val in result.items():
        if val is not None:
            logger.info(f"[YF_SECTOR] {key} = {val}")
        else:
            logger.warning(f"[YF_SECTOR] {key} 수집 실패 → None")

    collected = sum(1 for v in result.values() if v is not None)
    logger.info(f"[YF_SECTOR] 수집 완료: {collected}/{len(result)}개")
    return result
