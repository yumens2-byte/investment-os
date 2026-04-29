"""
collectors/binance_funding.py (v1.0.0)
=======================================
Phase 3 — Binance BTC Funding Rate 수집

목적:
  - Crypto.com Basis와 결합하여 leverage_overheating 시그널 생성
  - 글로벌 BTC 선물 시장의 롱-숏 비용 측정

API 정보:
  - URL: GET https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT
  - 인증: 불필요 (완전 공개 API)
  - Rate Limit: 500/5min/IP (충분)
  - 8시간 주기 갱신 (UTC 00:00, 08:00, 16:00)

캐싱 전략:
  - TTL 30분 (8h 갱신 주기지만 markPrice 변동 반영)
  - cache_key: "binance:funding:btcusdt"
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

# Binance Futures API
_API_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
_SYMBOL = "BTCUSDT"
_TIMEOUT_SEC = 10
_RETRY_COUNT = 2
_RETRY_BACKOFF = [2, 5]

# 캐시
_CACHE_KEY = "binance:funding:btcusdt"
_CACHE_TTL_MINUTES = 30


def _http_get_binance() -> Optional[dict]:
    """Binance Premium Index API 호출 (재시도 포함)."""
    try:
        import requests
    except ImportError:
        logger.error("[BinanceFunding] requests 모듈 미설치")
        return None

    params = {"symbol": _SYMBOL}

    for attempt in range(_RETRY_COUNT + 1):
        try:
            resp = requests.get(_API_URL, params=params, timeout=_TIMEOUT_SEC)

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 418):
                logger.warning(
                    f"[BinanceFunding] HTTP {resp.status_code} "
                    f"(attempt {attempt+1}/{_RETRY_COUNT+1})"
                )
                if attempt < _RETRY_COUNT:
                    wait = _RETRY_BACKOFF[attempt]
                    time.sleep(wait)
                    continue

            logger.warning(f"[BinanceFunding] HTTP {resp.status_code}")
            return None

        except Exception as e:
            logger.warning(
                f"[BinanceFunding] 호출 예외 "
                f"(attempt {attempt+1}/{_RETRY_COUNT+1}): {e}"
            )
            if attempt < _RETRY_COUNT:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue

    return None


def _parse_response(raw: dict) -> Optional[dict]:
    """Binance Premium Index 응답 파싱."""
    if not raw or not isinstance(raw, dict):
        return None

    try:
        rate_str = raw.get("lastFundingRate", "")
        if not rate_str:
            logger.warning("[BinanceFunding] lastFundingRate 필드 없음")
            return None

        # rate는 소수 형태 (예: 0.00010000 = 0.01%)
        rate_decimal = float(rate_str)
        rate_pct = rate_decimal * 100

        # 연환산: 8h × 3 (=일) × 365 = 1095배
        rate_apr = rate_pct * 3 * 365

        mark = raw.get("markPrice")
        index = raw.get("indexPrice")
        if mark is not None:
            try:
                mark = float(mark)
            except (TypeError, ValueError):
                mark = None
        if index is not None:
            try:
                index = float(index)
            except (TypeError, ValueError):
                index = None

        next_ft_ms = raw.get("nextFundingTime")
        if next_ft_ms:
            try:
                next_ft_iso = datetime.fromtimestamp(
                    int(next_ft_ms) / 1000, tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError):
                next_ft_iso = None
        else:
            next_ft_iso = None

        return {
            "funding_rate_8h": round(rate_pct, 5),
            "funding_rate_apr": round(rate_apr, 2),
            "mark_price": mark,
            "index_price": index,
            "next_funding_time": next_ft_iso,
        }

    except Exception as e:
        logger.warning(f"[BinanceFunding] 파싱 실패: {e}")
        return None


def get_btc_funding_rate() -> dict:
    """
    Binance BTC Funding Rate 수집 (캐싱 + graceful).

    Returns:
        dict: {
            "success": bool,
            "funding_rate_8h": float,
            "funding_rate_apr": float,
            "mark_price": float,
            "index_price": float,
            "next_funding_time": str,
            "source": str,
            "error": str | None,
        }
    """
    logger.info(f"[BinanceFunding v{VERSION}] BTC Funding Rate 수집 시작")

    # 1. 캐시 확인
    try:
        from db.api_cache_store import get_cache
        cached = get_cache(_CACHE_KEY)
        if cached and cached.get("success"):
            logger.info(
                f"[BinanceFunding] 캐시 HIT → "
                f"rate={cached.get('funding_rate_8h')}% "
                f"(APR {cached.get('funding_rate_apr')}%)"
            )
            return {**cached, "source": "cache"}
    except Exception as e:
        logger.warning(f"[BinanceFunding] 캐시 조회 실패 (무시): {e}")

    # 2. API 호출
    raw = _http_get_binance()
    parsed = _parse_response(raw) if raw else None

    if parsed:
        result = {
            "success": True,
            "funding_rate_8h": parsed["funding_rate_8h"],
            "funding_rate_apr": parsed["funding_rate_apr"],
            "mark_price": parsed["mark_price"],
            "index_price": parsed["index_price"],
            "next_funding_time": parsed["next_funding_time"],
            "source": "binance",
            "error": None,
        }

        # 3. 캐시 저장
        try:
            from db.api_cache_store import set_cache
            set_cache(
                _CACHE_KEY, result,
                source="binance",
                ttl_minutes=_CACHE_TTL_MINUTES,
            )
        except Exception as e:
            logger.warning(f"[BinanceFunding] 캐시 저장 실패 (무시): {e}")

        logger.info(
            f"[BinanceFunding] 수집 완료: "
            f"rate={result['funding_rate_8h']}% (APR {result['funding_rate_apr']}%)"
        )
        return result

    # 4. API 실패 → stale cache fallback
    try:
        from db.api_cache_store import get_stale_cache
        stale = get_stale_cache(_CACHE_KEY)
        if stale and stale.get("success"):
            logger.warning(
                f"[BinanceFunding] stale 캐시 fallback → "
                f"rate={stale.get('funding_rate_8h')}%"
            )
            return {**stale, "source": "stale_cache"}
    except Exception as e:
        logger.warning(f"[BinanceFunding] stale 캐시 조회 실패 (무시): {e}")

    # 5. 완전 실패
    logger.warning("[BinanceFunding] 모든 소스 실패 → success=False 반환")
    return {
        "success": False,
        "funding_rate_8h": None,
        "funding_rate_apr": None,
        "mark_price": None,
        "index_price": None,
        "next_funding_time": None,
        "source": "fallback",
        "error": "API 호출 + 캐시 모두 실패",
    }
