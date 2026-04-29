"""
collectors/crypto_funding.py (v1.0.0)
======================================
Phase 3-A — BTC Funding Rate 멀티 거래소 Failover 수집

목적:
  - Binance API (fapi.binance.com)가 GitHub Actions IP에서 HTTP 451 차단됨
  - 미국 정부 컴플라이언스 우회 절대 금지 (메모리 #22 / Notion 보안 정책)
  - OKX → Bybit 순으로 fallback, 모두 실패 시 leverage_overheating은 Basis 단독 운영

API 정보:
  [1순위] OKX
    URL: https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP
    인증: 불필요
    응답: {code: "0", msg: "", data: [{fundingRate, nextFundingRate, fundingTime, ...}]}

  [2순위] Bybit
    URL: https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=1
    인증: 불필요
    응답: {retCode: 0, retMsg: "OK", result: {list: [{fundingRate, fundingRateTimestamp, ...}]}}
    서버: 싱가포르 AWS (apse1-az3) → US IP 차단 가능성 낮음

  [절대 금지] Binance — fapi.binance.com 호출 절대 금지
    HTTP 451 (Unavailable For Legal Reasons) — 미국 정부 차단
    우회 시 컴플라이언스 위반

캐싱 전략:
  cache_key="crypto:funding:btcusdt"
  TTL 30분 (8h 갱신 주기지만 실시간 정밀도 위해 짧게)
  실패 시 stale cache fallback
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

# ──────────────────────────────────────────────────────────────
# 거래소 endpoint 설정
# ──────────────────────────────────────────────────────────────

_OKX_URL = "https://www.okx.com/api/v5/public/funding-rate"
_OKX_INSTRUMENT = "BTC-USDT-SWAP"

_BYBIT_URL = "https://api.bybit.com/v5/market/funding/history"
_BYBIT_SYMBOL = "BTCUSDT"

_TIMEOUT_SEC = 10
_RETRY_COUNT = 1   # 거래소당 최대 1회 재시도 (다음 거래소로 빠르게 전환)
_RETRY_BACKOFF = [2]

# 캐시
_CACHE_KEY = "crypto:funding:btcusdt"
_CACHE_TTL_MINUTES = 30


# ──────────────────────────────────────────────────────────────
# OKX 호출 + 파싱
# ──────────────────────────────────────────────────────────────

def _fetch_okx() -> Optional[dict]:
    """
    OKX BTC-USDT-SWAP funding rate 조회.

    Returns:
        dict: 정규화된 funding 데이터 (성공 시)
        None: 실패
    """
    try:
        import requests
    except ImportError:
        logger.error("[CryptoFunding/OKX] requests 모듈 미설치")
        return None

    params = {"instId": _OKX_INSTRUMENT}

    for attempt in range(_RETRY_COUNT + 1):
        try:
            resp = requests.get(_OKX_URL, params=params, timeout=_TIMEOUT_SEC)
            if resp.status_code == 200:
                raw = resp.json()
                return _parse_okx(raw)

            if resp.status_code in (429, 503):
                logger.warning(
                    f"[CryptoFunding/OKX] HTTP {resp.status_code} "
                    f"(attempt {attempt+1}/{_RETRY_COUNT+1})"
                )
                if attempt < _RETRY_COUNT:
                    time.sleep(_RETRY_BACKOFF[attempt])
                    continue

            logger.warning(f"[CryptoFunding/OKX] HTTP {resp.status_code} — 비재시도")
            return None

        except Exception as e:
            logger.warning(f"[CryptoFunding/OKX] 호출 예외: {e}")
            if attempt < _RETRY_COUNT:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue

    return None


def _parse_okx(raw: dict) -> Optional[dict]:
    """OKX 응답 파싱 → 정규화된 dict."""
    if not raw or not isinstance(raw, dict):
        return None

    code = raw.get("code")
    if code != "0":
        logger.warning(f"[CryptoFunding/OKX] code={code} msg={raw.get('msg')}")
        return None

    data = raw.get("data", [])
    if not data or not isinstance(data, list):
        logger.warning("[CryptoFunding/OKX] data 배열 없음")
        return None

    item = data[0]
    rate_str = item.get("fundingRate", "")
    if not rate_str:
        logger.warning("[CryptoFunding/OKX] fundingRate 없음")
        return None

    try:
        rate_decimal = float(rate_str)
    except (TypeError, ValueError):
        logger.warning(f"[CryptoFunding/OKX] fundingRate 변환 실패: {rate_str}")
        return None

    rate_pct = rate_decimal * 100
    rate_apr = rate_pct * 3 * 365   # 8h × 3회 × 365일

    # nextFundingTime (ms → ISO)
    next_ft_ms = item.get("nextFundingTime")
    next_ft_iso = None
    if next_ft_ms:
        try:
            next_ft_iso = datetime.fromtimestamp(
                int(next_ft_ms) / 1000, tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError):
            pass

    return {
        "funding_rate_8h": round(rate_pct, 5),
        "funding_rate_apr": round(rate_apr, 2),
        "mark_price": None,    # OKX funding endpoint는 mark price 미제공
        "index_price": None,
        "next_funding_time": next_ft_iso,
    }


# ──────────────────────────────────────────────────────────────
# Bybit 호출 + 파싱
# ──────────────────────────────────────────────────────────────

def _fetch_bybit() -> Optional[dict]:
    """
    Bybit BTCUSDT linear funding history 조회 (최근 1건).

    Returns:
        dict: 정규화된 funding 데이터 (성공 시)
        None: 실패
    """
    try:
        import requests
    except ImportError:
        logger.error("[CryptoFunding/Bybit] requests 모듈 미설치")
        return None

    params = {
        "category": "linear",
        "symbol": _BYBIT_SYMBOL,
        "limit": 1,
    }

    for attempt in range(_RETRY_COUNT + 1):
        try:
            resp = requests.get(_BYBIT_URL, params=params, timeout=_TIMEOUT_SEC)
            if resp.status_code == 200:
                raw = resp.json()
                return _parse_bybit(raw)

            if resp.status_code in (429, 503):
                logger.warning(
                    f"[CryptoFunding/Bybit] HTTP {resp.status_code} "
                    f"(attempt {attempt+1}/{_RETRY_COUNT+1})"
                )
                if attempt < _RETRY_COUNT:
                    time.sleep(_RETRY_BACKOFF[attempt])
                    continue

            logger.warning(f"[CryptoFunding/Bybit] HTTP {resp.status_code}")
            return None

        except Exception as e:
            logger.warning(f"[CryptoFunding/Bybit] 호출 예외: {e}")
            if attempt < _RETRY_COUNT:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue

    return None


def _parse_bybit(raw: dict) -> Optional[dict]:
    """Bybit 응답 파싱 → 정규화된 dict."""
    if not raw or not isinstance(raw, dict):
        return None

    ret_code = raw.get("retCode")
    if ret_code != 0:
        logger.warning(
            f"[CryptoFunding/Bybit] retCode={ret_code} msg={raw.get('retMsg')}"
        )
        return None

    result = raw.get("result", {})
    items = result.get("list", []) if isinstance(result, dict) else []
    if not items:
        logger.warning("[CryptoFunding/Bybit] list 비어있음")
        return None

    item = items[0]
    rate_str = item.get("fundingRate", "")
    if not rate_str:
        logger.warning("[CryptoFunding/Bybit] fundingRate 없음")
        return None

    try:
        rate_decimal = float(rate_str)
    except (TypeError, ValueError):
        logger.warning(f"[CryptoFunding/Bybit] fundingRate 변환 실패: {rate_str}")
        return None

    rate_pct = rate_decimal * 100
    rate_apr = rate_pct * 3 * 365

    # fundingRateTimestamp (ms → ISO) — 이전 정산 시각
    ts_ms = item.get("fundingRateTimestamp")
    ts_iso = None
    if ts_ms:
        try:
            ts_iso = datetime.fromtimestamp(
                int(ts_ms) / 1000, tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError):
            pass

    return {
        "funding_rate_8h": round(rate_pct, 5),
        "funding_rate_apr": round(rate_apr, 2),
        "mark_price": None,    # Bybit funding-history는 mark price 미제공
        "index_price": None,
        "next_funding_time": ts_iso,   # Bybit는 last funding timestamp만 제공
    }


# ──────────────────────────────────────────────────────────────
# 통합 진입점 (Failover 체인)
# ──────────────────────────────────────────────────────────────

def get_btc_funding_rate() -> dict:
    """
    BTC Funding Rate 멀티 거래소 Failover 수집.

    Failover 우선순위:
      1. 캐시 (TTL 30분 내)
      2. OKX
      3. Bybit
      4. Stale cache (만료된 캐시도 사용)
      5. 완전 실패 → success=False

    Returns:
        dict: {
            "success": bool,
            "funding_rate_8h": float | None,
            "funding_rate_apr": float | None,
            "mark_price": float | None,
            "index_price": float | None,
            "next_funding_time": str | None,
            "source": str,           # cache | okx | bybit | stale_cache | fallback
            "exchange": str | None,  # 실제 사용 거래소
            "error": str | None,
        }

    절대 금지: Binance API (HTTP 451 + 컴플라이언스 위반)
    """
    logger.info(f"[CryptoFunding v{VERSION}] BTC Funding Rate Failover 수집 시작")

    # 1. 캐시 확인
    try:
        from db.api_cache_store import get_cache
        cached = get_cache(_CACHE_KEY)
        if cached and cached.get("success"):
            logger.info(
                f"[CryptoFunding] 캐시 HIT → exchange={cached.get('exchange')} "
                f"rate={cached.get('funding_rate_8h')}%"
            )
            return {**cached, "source": "cache"}
    except Exception as e:
        logger.warning(f"[CryptoFunding] 캐시 조회 실패 (무시): {e}")

    # 2. OKX 시도 (1순위)
    okx_data = _fetch_okx()
    if okx_data:
        result = {
            "success": True,
            **okx_data,
            "source": "okx",
            "exchange": "okx",
            "error": None,
        }
        _save_cache(result)
        logger.info(
            f"[CryptoFunding] OKX 수집 성공: rate={result['funding_rate_8h']}% "
            f"(APR {result['funding_rate_apr']}%)"
        )
        return result

    logger.warning("[CryptoFunding] OKX 실패 → Bybit fallback")

    # 3. Bybit 시도 (2순위)
    bybit_data = _fetch_bybit()
    if bybit_data:
        result = {
            "success": True,
            **bybit_data,
            "source": "bybit",
            "exchange": "bybit",
            "error": None,
        }
        _save_cache(result)
        logger.info(
            f"[CryptoFunding] Bybit 수집 성공: rate={result['funding_rate_8h']}% "
            f"(APR {result['funding_rate_apr']}%)"
        )
        return result

    logger.warning("[CryptoFunding] OKX + Bybit 모두 실패")

    # 4. Stale cache fallback
    try:
        from db.api_cache_store import get_stale_cache
        stale = get_stale_cache(_CACHE_KEY)
        if stale and stale.get("success"):
            logger.warning(
                f"[CryptoFunding] stale 캐시 fallback → "
                f"exchange={stale.get('exchange')} "
                f"rate={stale.get('funding_rate_8h')}%"
            )
            return {**stale, "source": "stale_cache"}
    except Exception as e:
        logger.warning(f"[CryptoFunding] stale 캐시 조회 실패 (무시): {e}")

    # 5. 완전 실패 → leverage_overheating은 Basis 단독 운영으로 처리됨
    logger.warning(
        "[CryptoFunding] 모든 소스 실패 → success=False "
        "(leverage_overheating은 Basis 단독 모드로 처리)"
    )
    return {
        "success": False,
        "funding_rate_8h": None,
        "funding_rate_apr": None,
        "mark_price": None,
        "index_price": None,
        "next_funding_time": None,
        "source": "fallback",
        "exchange": None,
        "error": "OKX + Bybit + stale cache 모두 실패",
    }


def _save_cache(result: dict) -> None:
    """캐시 저장 (best effort, 실패 무시)."""
    try:
        from db.api_cache_store import set_cache
        set_cache(
            _CACHE_KEY, result,
            source="crypto_funding",
            ttl_minutes=_CACHE_TTL_MINUTES,
        )
    except Exception as e:
        logger.warning(f"[CryptoFunding] 캐시 저장 실패 (무시): {e}")
