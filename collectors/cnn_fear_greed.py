"""
collectors/cnn_fear_greed.py (v1.0.0)
======================================
Phase 3 — CNN Stock Market Fear & Greed Index 수집

목적:
  - alternative.me F&G(crypto)와 별개의 주식 시장 감성 지표
  - 기존 macro_engine risk_score 계산식의 구조 결함 정정에 사용
    (기존 crypto F&G가 주식 시장 risk_score에 20% 가중치로 잘못 사용됨)

API 정보:
  - URL: https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{YYYY-MM-DD}
  - 비공식 API (User-Agent 헤더 필수)
  - 인증 불필요
  - 응답: fear_and_greed.score, fear_and_greed.rating + 7개 하위 지표

캐싱 전략:
  1. 정상 조회: api_cache 테이블 HIT 우선
  2. 캐시 miss: API 호출 → 캐시 저장 (TTL 60분)
  3. API 실패: stale cache 반환 (graceful degradation)
  4. 캐시 없음 + API 실패: success=False 반환 (파이프라인 비차단)

호출 빈도:
  - morning 세션 1회 / 일
  - 캐시 TTL 60분 → full/narrative 세션은 캐시 HIT 활용
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

# CNN Fear & Greed Index API
_BASE_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_TIMEOUT_SEC = 10
_RETRY_COUNT = 2
_RETRY_BACKOFF = [2, 5]

# 캐시
_CACHE_KEY = "cnn:fearandgreed:current"
_CACHE_TTL_MINUTES = 60

# User-Agent (CNN API 차단 회피용 필수)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _http_get_cnn() -> Optional[dict]:
    """CNN Fear & Greed API 호출 (재시도 포함)."""
    try:
        import requests
    except ImportError:
        logger.error("[CNN_FG] requests 모듈 미설치")
        return None

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"{_BASE_URL}/{today_str}"

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://edition.cnn.com/markets/fear-and-greed",
    }

    for attempt in range(_RETRY_COUNT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=_TIMEOUT_SEC)

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 503):
                logger.warning(
                    f"[CNN_FG] HTTP {resp.status_code} (attempt {attempt+1}/"
                    f"{_RETRY_COUNT+1})"
                )
                if attempt < _RETRY_COUNT:
                    wait = _RETRY_BACKOFF[attempt]
                    logger.info(f"[CNN_FG] {wait}초 대기 후 재시도")
                    time.sleep(wait)
                    continue

            logger.warning(
                f"[CNN_FG] HTTP {resp.status_code} — 비재시도 오류"
            )
            return None

        except Exception as e:
            logger.warning(
                f"[CNN_FG] 호출 예외 (attempt {attempt+1}/{_RETRY_COUNT+1}): {e}"
            )
            if attempt < _RETRY_COUNT:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue

    return None


def _parse_response(raw: dict) -> Optional[dict]:
    """CNN 응답 파싱."""
    if not raw or not isinstance(raw, dict):
        return None

    fg = raw.get("fear_and_greed")
    if not fg or not isinstance(fg, dict):
        logger.warning("[CNN_FG] fear_and_greed 키 없음")
        return None

    score = fg.get("score")
    if score is None:
        logger.warning("[CNN_FG] score 필드 없음")
        return None

    try:
        score = float(score)
    except (TypeError, ValueError):
        logger.warning(f"[CNN_FG] score 변환 실패: {fg.get('score')}")
        return None

    rating = fg.get("rating", "neutral")
    previous_close = fg.get("previous_close")
    if previous_close is not None:
        try:
            previous_close = float(previous_close)
        except (TypeError, ValueError):
            previous_close = None

    timestamp = fg.get("timestamp", "")

    return {
        "value": round(score, 1),
        "rating": str(rating).lower(),
        "previous_close": previous_close,
        "timestamp": timestamp,
    }


def collect_cnn_fg() -> dict:
    """
    CNN Stock Market Fear & Greed Index 수집 (캐싱 + graceful).

    Returns:
        dict: {
            "success": bool,
            "value": float,
            "rating": str,
            "previous_close": float,
            "timestamp": str,
            "source": str,
            "error": str | None,
        }
    """
    logger.info(f"[CNN_FG v{VERSION}] CNN F&G 수집 시작")

    # 1. 캐시 확인
    try:
        from db.api_cache_store import get_cache
        cached = get_cache(_CACHE_KEY)
        if cached and cached.get("success"):
            logger.info(
                f"[CNN_FG] 캐시 HIT → {cached.get('value')} ({cached.get('rating')})"
            )
            return {**cached, "source": "cache"}
    except Exception as e:
        logger.warning(f"[CNN_FG] 캐시 조회 실패 (무시): {e}")

    # 2. API 호출
    raw = _http_get_cnn()
    parsed = _parse_response(raw) if raw else None

    if parsed:
        result = {
            "success": True,
            "value": parsed["value"],
            "rating": parsed["rating"],
            "previous_close": parsed["previous_close"],
            "timestamp": parsed["timestamp"],
            "source": "cnn",
            "error": None,
        }

        # 3. 캐시 저장
        try:
            from db.api_cache_store import set_cache
            set_cache(
                _CACHE_KEY, result,
                source="cnn",
                ttl_minutes=_CACHE_TTL_MINUTES,
            )
        except Exception as e:
            logger.warning(f"[CNN_FG] 캐시 저장 실패 (무시): {e}")

        logger.info(
            f"[CNN_FG] 수집 완료: value={result['value']} rating={result['rating']}"
        )
        return result

    # 4. API 실패 → stale cache fallback
    try:
        from db.api_cache_store import get_stale_cache
        stale = get_stale_cache(_CACHE_KEY)
        if stale and stale.get("success"):
            logger.warning(
                f"[CNN_FG] stale 캐시 fallback → "
                f"{stale.get('value')} ({stale.get('rating')})"
            )
            return {**stale, "source": "stale_cache"}
    except Exception as e:
        logger.warning(f"[CNN_FG] stale 캐시 조회 실패 (무시): {e}")

    # 5. 완전 실패
    logger.warning("[CNN_FG] 모든 소스 실패 → success=False 반환")
    return {
        "success": False,
        "value": None,
        "rating": "Unknown",
        "previous_close": None,
        "timestamp": None,
        "source": "fallback",
        "error": "API 호출 + 캐시 모두 실패",
    }
