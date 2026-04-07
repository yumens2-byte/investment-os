"""
collectors/lunarcrush_client.py (v1.0.0)
=========================================
Phase 1A — LunarCrush REST API 클라이언트 (캐싱 포함)

용도:
  - T4-4 BTC Social Sentiment 시그널 수집
  - 무료 플랜 한도 (100 req/day) 대응 캐싱

API:
  - Base URL: https://lunarcrush.com/api4/public
  - 인증: Authorization: Bearer {API_KEY}
  - 무료 한도: 4 req/min, 100 req/day
  - 데이터: Market data only (Galaxy Score/AltRank 마스킹)
  - 가용 필드: sentiment %, social_dominance, themes 텍스트

캐싱 전략:
  1. 정상 조회: api_cache 테이블 HIT 우선
  2. 캐시 miss: API 호출 → 캐시 저장 (TTL 60분)
  3. Rate limit (429): stale cache 반환
  4. 캐시 없음 + API 실패: None + state="Unknown"

시그널 판정:
  sentiment > 70 → Bullish (score=1)
  50 ~ 70        → Neutral (score=2)
  < 50           → Bearish (score=3)
"""
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

# LunarCrush Public API v4
_BASE_URL = "https://lunarcrush.com/api4/public"
_TIMEOUT_SEC = 10
_RETRY_COUNT = 2
_RETRY_BACKOFF = [2, 5]

# 캐시 TTL
_CACHE_TTL_MINUTES = 60

# 환경변수
_API_KEY_ENV = "LUNAR_CRUSH_API_KEY"


def _get_api_key() -> Optional[str]:
    """LunarCrush API 키 로드"""
    key = os.getenv(_API_KEY_ENV, "").strip()
    if not key:
        logger.warning(f"[LunarCrush] {_API_KEY_ENV} 환경변수 미설정")
        return None
    return key


def _http_get(endpoint: str) -> Optional[dict]:
    """
    LunarCrush API 호출 (인증 + 재시도).

    Args:
        endpoint: API path (예: "/coins/btc/v1")

    Returns:
        dict: 응답 JSON
        None: 실패 (네트워크/4xx/5xx)
        {"rate_limit": True}: Rate limit 초과 (특수값)
    """
    import requests

    api_key = _get_api_key()
    if not api_key:
        return None

    url = f"{_BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "InvestmentOS/1.0 (+https://github.com/yumens2-byte/investment-os)",
        "Accept": "application/json",
    }

    for attempt in range(_RETRY_COUNT):
        try:
            resp = requests.get(url, headers=headers, timeout=_TIMEOUT_SEC)

            if resp.status_code == 429:
                logger.warning(f"[LunarCrush] Rate limit 초과 (429): {endpoint}")
                return {"rate_limit": True}

            if resp.status_code == 401:
                logger.error(f"[LunarCrush] 인증 실패 (401) — API 키 확인 필요")
                return None

            if resp.status_code != 200:
                logger.warning(
                    f"[LunarCrush] {endpoint} HTTP {resp.status_code} "
                    f"(attempt {attempt + 1}/{_RETRY_COUNT})"
                )
                if attempt < _RETRY_COUNT - 1:
                    time.sleep(_RETRY_BACKOFF[attempt])
                    continue
                return None

            return resp.json()

        except requests.Timeout:
            logger.warning(
                f"[LunarCrush] {endpoint} 타임아웃 "
                f"(attempt {attempt + 1}/{_RETRY_COUNT})"
            )
            if attempt < _RETRY_COUNT - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            return None

        except Exception as e:
            logger.warning(f"[LunarCrush] {endpoint} 예외: {e}")
            return None

    return None


def _parse_sentiment_themes(raw: dict) -> tuple[str, str]:
    """
    LunarCrush 응답에서 supportive/critical themes 추출.

    무료 플랜에서도 sentiment themes는 텍스트로 제공됨.
    응답 구조가 다양할 수 있어 여러 키 후보 시도.

    Returns:
        (supportive_str, critical_str) — 200자 이내 각각
    """
    supportive = ""
    critical = ""

    # 후보 경로 1: data.sentiment.supportive_themes
    try:
        sentiment_obj = raw.get("data", {}).get("sentiment", {})
        if isinstance(sentiment_obj, dict):
            supportive_list = sentiment_obj.get("supportive_themes", [])
            critical_list = sentiment_obj.get("critical_themes", [])

            if supportive_list:
                supportive = "; ".join(
                    f"{t.get('name', '?')} ({t.get('percent', 0)}%)"
                    for t in supportive_list[:3]
                    if isinstance(t, dict)
                )
            if critical_list:
                critical = "; ".join(
                    f"{t.get('name', '?')} ({t.get('percent', 0)}%)"
                    for t in critical_list[:3]
                    if isinstance(t, dict)
                )
    except Exception as e:
        logger.debug(f"[LunarCrush] themes 파싱 실패 (경로 1): {e}")

    # 후보 경로 2: data.themes (단순 리스트)
    if not supportive and not critical:
        try:
            themes = raw.get("data", {}).get("themes", {})
            if isinstance(themes, dict):
                supportive = str(themes.get("bullish", ""))[:200]
                critical = str(themes.get("bearish", ""))[:200]
        except Exception as e:
            logger.debug(f"[LunarCrush] themes 파싱 실패 (경로 2): {e}")

    # 길이 제한 (DB 저장 대비)
    return supportive[:200], critical[:200]


def _parse_sentiment_value(raw: dict) -> Optional[float]:
    """LunarCrush 응답에서 sentiment 값 추출 (여러 경로 시도)"""

    # 후보 1: data.sentiment (숫자)
    try:
        val = raw.get("data", {}).get("sentiment")
        if isinstance(val, (int, float)):
            return float(val)
    except Exception:
        pass

    # 후보 2: data.sentiment.value
    try:
        val = raw.get("data", {}).get("sentiment", {}).get("value")
        if isinstance(val, (int, float)):
            return float(val)
    except Exception:
        pass

    # 후보 3: data.sentiment.current
    try:
        val = raw.get("data", {}).get("sentiment", {}).get("current")
        if isinstance(val, (int, float)):
            return float(val)
    except Exception:
        pass

    return None


def _fetch_btc_topic(use_cache: bool = True) -> Optional[dict]:
    """
    BTC Topic 데이터 수집 (캐시 우선).

    Args:
        use_cache: True이면 캐시 확인, False이면 API 직접 호출

    Returns:
        dict: LunarCrush 응답 (data 필드)
        None: 실패
    """
    from db.api_cache_store import get_cache, set_cache, get_stale_cache

    cache_key = "lunarcrush:coin:btc"

    # 1. 캐시 확인
    if use_cache:
        cached = get_cache(cache_key)
        if cached:
            return cached

    # 2. API 호출 — 코인 엔드포인트 우선
    # LunarCrush API4 구조: /coins/{symbol}/v1
    raw = _http_get("/coins/btc/v1")

    # Rate limit 시 stale fallback
    if isinstance(raw, dict) and raw.get("rate_limit"):
        logger.warning("[LunarCrush] Rate limit → stale cache 시도")
        stale = get_stale_cache(cache_key)
        if stale:
            return stale
        return None

    # API 실패 시 stale fallback
    if raw is None:
        logger.warning("[LunarCrush] API 실패 → stale cache 시도")
        stale = get_stale_cache(cache_key)
        if stale:
            return stale
        return None

    # 3. 캐시 저장
    set_cache(
        cache_key=cache_key,
        value=raw,
        source="lunarcrush",
        ttl_minutes=_CACHE_TTL_MINUTES,
    )

    return raw


def get_btc_sentiment() -> dict:
    """
    T4-4 BTC Social Sentiment 시그널 수집.

    Returns:
        dict:
          {
            "success": bool,
            "sentiment":       float | None,  # 0~100
            "state":           "Bullish" | "Neutral" | "Bearish" | "Unknown",
            "score":           int,  # 1~3
            "themes_supportive": str,
            "themes_critical":   str,
            "source":          "api" | "cache" | "stale" | "fallback",
            "error":           str | None,
          }
    """
    logger.info("[LunarCrush] T4-4 BTC Sentiment 수집 시작")

    raw = _fetch_btc_topic(use_cache=True)

    if raw is None:
        logger.warning("[LunarCrush] T4-4 수집 실패 — Unknown 반환")
        return {
            "success": False,
            "sentiment": None,
            "state": "Unknown",
            "score": 2,
            "themes_supportive": "",
            "themes_critical": "",
            "source": "fallback",
            "error": "API 실패 + 캐시 없음",
        }

    # Sentiment 값 파싱
    sentiment = _parse_sentiment_value(raw)
    if sentiment is None:
        logger.warning("[LunarCrush] sentiment 값 추출 실패")
        return {
            "success": False,
            "sentiment": None,
            "state": "Unknown",
            "score": 2,
            "themes_supportive": "",
            "themes_critical": "",
            "source": "fallback",
            "error": "sentiment 필드 파싱 실패",
        }

    # State 판정
    if sentiment > 70:
        state, score = "Bullish", 1
    elif sentiment >= 50:
        state, score = "Neutral", 2
    else:
        state, score = "Bearish", 3

    # Themes 파싱 (가능한 경우)
    supportive, critical = _parse_sentiment_themes(raw)

    logger.info(
        f"[LunarCrush] T4-4 완료: sentiment={sentiment}% → {state} (score={score})"
    )

    return {
        "success": True,
        "sentiment": sentiment,
        "state": state,
        "score": score,
        "themes_supportive": supportive,
        "themes_critical": critical,
        "source": "api",  # 실제 source는 캐시 로직에서 구분 필요 (향후)
        "error": None,
    }
