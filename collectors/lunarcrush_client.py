"""
collectors/lunarcrush_client.py (v1.2.0)
=========================================
Phase 1A — LunarCrush AI/LLM Endpoint 클라이언트 (캐싱 포함)

변경이력:
  v1.2.0 (2026-04-07) ★ Base URL 전면 교체: lunarcrush.com/api4/public → lunarcrush.ai
                       무료 플랜 REST(/api4/public/topic/...)는 402 차단 → AI/LLM 전용
                       endpoint(lunarcrush.ai/topic/{topic}.json)로 우회
                       응답 스키마 완전 변경 → 파서 재작성:
                         data.metrics.sentiment["1w"] (현재 sentiment, weighted avg)
                         data.metrics.sentiment.avg  (1년 평균, fallback)
                         data.ai_summary.supportive[].title/percent
                         data.ai_summary.critical[].title/percent
                       단일 endpoint (fallback 불필요, lunarcrush.ai/.json 안정적)
                       검증: curl로 200 OK + sentiment 72 정상 추출 확인 (2026-04-07)
                       외부 인터페이스(get_btc_sentiment) 시그니처 무변경
                         → run_market.py / json_builder.py 수정 불필요
  v1.1.0 (2026-04-07) [DEPRECATED] /coins/btc/v1 → /topic/bitcoin/v1, fallback 추가
                       무료 플랜에서 /api4/public/* 전 경로 402 확인됨
  v1.0.0 초기 버전

용도:
  - T4-4 BTC Social Sentiment 시그널 수집
  - 무료 플랜 한도 대응 캐싱

API:
  - Base URL: https://lunarcrush.ai
  - 인증: Authorization: Bearer {API_KEY}
  - Endpoint: /topic/bitcoin.json (LLM-optimized JSON)
  - 응답 크기: ~15KB (sentiment + ai_summary + metrics + influencers + posts)
  - 무료 플랜에서 마스킹 없이 모든 핵심 메트릭 노출 확인 (2026-04-07 검증)
  - Cloudflare 캐싱 사용 (응답 빠름)

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

VERSION = "1.2.0"

# LunarCrush AI/LLM Endpoint (lunarcrush.com REST와 별개 도메인)
_BASE_URL = "https://lunarcrush.ai"
_ENDPOINT = "/topic/bitcoin.json"
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
    LunarCrush AI Endpoint 호출 (인증 + 재시도).

    Returns:
        dict: 응답 JSON
        None: 실패
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

            if resp.status_code == 402:
                logger.error(
                    f"[LunarCrush] Payment Required (402): {endpoint} — "
                    f"무료 플랜 권한 부족 (lunarcrush.ai endpoint도 차단됨)"
                )
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

            try:
                return resp.json()
            except ValueError as e:
                logger.warning(f"[LunarCrush] JSON 파싱 실패: {e}")
                return None

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


def _parse_sentiment_value(raw: dict) -> Optional[float]:
    """
    LunarCrush AI endpoint 응답에서 sentiment 값 추출.

    응답 스키마 (lunarcrush.ai/topic/bitcoin.json, 2026-04-07 검증):
      data.metrics.sentiment["1w"]      (현재 1주 평균 sentiment, weighted)
      data.metrics.sentiment.avg        (1년 평균, fallback)
      data.metrics.sentiment["1w_previous"]  (지난주 sentiment, fallback 2)

    Returns:
        float (0~100): sentiment 점수
        None: 추출 실패
    """
    # 후보 1: data.metrics.sentiment["1w"] — 가장 최근 (1주 평균)
    try:
        sent_obj = raw.get("data", {}).get("metrics", {}).get("sentiment", {})
        if isinstance(sent_obj, dict):
            val = sent_obj.get("1w")
            if isinstance(val, (int, float)):
                logger.debug(f"[LunarCrush] sentiment 추출: data.metrics.sentiment['1w']={val}")
                return float(val)
    except Exception as e:
        logger.debug(f"[LunarCrush] 후보1 실패: {e}")

    # 후보 2: data.metrics.sentiment.avg — 1년 평균
    try:
        sent_obj = raw.get("data", {}).get("metrics", {}).get("sentiment", {})
        if isinstance(sent_obj, dict):
            val = sent_obj.get("avg")
            if isinstance(val, (int, float)):
                logger.debug(f"[LunarCrush] sentiment 추출: data.metrics.sentiment.avg={val}")
                return float(val)
    except Exception as e:
        logger.debug(f"[LunarCrush] 후보2 실패: {e}")

    # 후보 3: data.metrics.sentiment["1w_previous"]
    try:
        sent_obj = raw.get("data", {}).get("metrics", {}).get("sentiment", {})
        if isinstance(sent_obj, dict):
            val = sent_obj.get("1w_previous")
            if isinstance(val, (int, float)):
                logger.debug(f"[LunarCrush] sentiment 추출: data.metrics.sentiment['1w_previous']={val}")
                return float(val)
    except Exception as e:
        logger.debug(f"[LunarCrush] 후보3 실패: {e}")

    # 후보 4: data.sentiment.network 평균 (네트워크별 sentiment)
    try:
        network = raw.get("data", {}).get("sentiment", {}).get("network", {})
        if isinstance(network, dict) and network:
            values = [v for v in network.values() if isinstance(v, (int, float))]
            if values:
                avg = sum(values) / len(values)
                logger.debug(f"[LunarCrush] sentiment 추출: data.sentiment.network 평균={avg:.1f} ({len(values)}개)")
                return float(avg)
    except Exception as e:
        logger.debug(f"[LunarCrush] 후보4 실패: {e}")

    # 후보 5: data.sentiment.interactions {positive, negative, neutral} → 백분율 계산
    try:
        inter = raw.get("data", {}).get("sentiment", {}).get("interactions", {})
        if isinstance(inter, dict):
            pos = inter.get("positive", 0)
            neg = inter.get("negative", 0)
            neu = inter.get("neutral", 0)
            total = pos + neg + neu
            if total > 0:
                # weighted: positive=100, neutral=50, negative=0
                weighted = (pos * 100 + neu * 50) / total
                logger.debug(f"[LunarCrush] sentiment 추출: interactions weighted={weighted:.1f}")
                return float(weighted)
    except Exception as e:
        logger.debug(f"[LunarCrush] 후보5 실패: {e}")

    # 모두 실패
    try:
        keys = list(raw.get("data", {}).keys()) if isinstance(raw.get("data"), dict) else "N/A"
    except Exception:
        keys = "?"
    logger.warning(f"[LunarCrush] sentiment 파싱 실패 — data 키: {keys}")
    return None


def _parse_sentiment_themes(raw: dict) -> tuple[str, str]:
    """
    LunarCrush AI endpoint 응답에서 supportive/critical themes 추출.

    응답 스키마 (lunarcrush.ai/topic/bitcoin.json, 2026-04-07 검증):
      data.ai_summary.supportive: [
        {"title": "...", "percent": 30, "description": "..."}, ...
      ]
      data.ai_summary.critical: [
        {"title": "...", "percent": 16, "description": "..."}, ...
      ]

    Returns:
        (supportive_str, critical_str) — 각각 200자 이내
    """
    supportive = ""
    critical = ""

    # 후보 1: data.ai_summary.supportive / critical (lunarcrush.ai 기본 형식)
    try:
        ai_summary = raw.get("data", {}).get("ai_summary", {})
        if isinstance(ai_summary, dict):
            sup_list = ai_summary.get("supportive", [])
            crit_list = ai_summary.get("critical", [])

            if isinstance(sup_list, list) and sup_list:
                parts = []
                for t in sup_list[:3]:
                    if isinstance(t, dict):
                        title = t.get("title", "?")
                        percent = t.get("percent", 0)
                        parts.append(f"{title} ({percent}%)")
                supportive = "; ".join(parts)

            if isinstance(crit_list, list) and crit_list:
                parts = []
                for t in crit_list[:3]:
                    if isinstance(t, dict):
                        title = t.get("title", "?")
                        percent = t.get("percent", 0)
                        parts.append(f"{title} ({percent}%)")
                critical = "; ".join(parts)
    except Exception as e:
        logger.debug(f"[LunarCrush] themes 파싱 실패 (ai_summary): {e}")

    # 후보 2: data.ai_summary.whatsup (텍스트 요약, fallback)
    if not supportive and not critical:
        try:
            whatsup = raw.get("data", {}).get("ai_summary", {}).get("whatsup", "")
            if isinstance(whatsup, str) and whatsup:
                supportive = whatsup[:200]
        except Exception:
            pass

    # 길이 제한 (DB 저장 대비)
    return supportive[:200], critical[:200]


def _fetch_btc_topic(use_cache: bool = True) -> Optional[dict]:
    """
    BTC Topic 데이터 수집 (캐시 우선).

    Args:
        use_cache: True이면 캐시 확인, False이면 API 직접 호출

    Returns:
        dict: LunarCrush 응답 (raw JSON)
        None: 실패
    """
    from db.api_cache_store import get_cache, set_cache, get_stale_cache

    cache_key = "lunarcrush:topic:bitcoin"

    # 1. 캐시 확인
    if use_cache:
        cached = get_cache(cache_key)
        if cached:
            return cached

    # 2. API 호출 (단일 endpoint, fallback 불필요)
    logger.info(f"[LunarCrush] AI endpoint 호출: {_BASE_URL}{_ENDPOINT}")
    raw = _http_get(_ENDPOINT)

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

    외부 인터페이스 (run_market.py / json_builder.py가 호출):
    이 함수의 시그니처와 반환 dict 키는 v1.1.0과 100% 동일하다.

    Returns:
        dict:
          {
            "success": bool,
            "sentiment":          float | None,  # 0~100
            "state":              "Bullish" | "Neutral" | "Bearish" | "Unknown",
            "score":              int,  # 1~3
            "themes_supportive":  str,
            "themes_critical":    str,
            "source":             "api" | "cache" | "stale" | "fallback",
            "error":              str | None,
          }
    """
    logger.info(f"[LunarCrush v{VERSION}] T4-4 BTC Sentiment 수집 시작")

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

    # Themes 파싱
    supportive, critical = _parse_sentiment_themes(raw)

    logger.info(
        f"[LunarCrush] T4-4 완료: sentiment={sentiment:.1f}% → {state} (score={score})"
    )

    return {
        "success": True,
        "sentiment": round(sentiment, 1),
        "state": state,
        "score": score,
        "themes_supportive": supportive,
        "themes_critical": critical,
        "source": "api",
        "error": None,
    }
