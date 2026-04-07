"""
db/api_cache_store.py (v1.0.0)
==============================
Phase 1A — API 응답 캐시 DAO

용도:
  - LunarCrush 무료 플랜 (100 req/day) 한도 초과 대응
  - Crypto.com 응답 선택적 캐싱
  - 캐시 만료 시 stale fallback 제공 (rate limit 대응)

사용 예:
  from db.api_cache_store import get_cache, set_cache, get_stale_cache

  # 1. 캐시 조회 (만료 확인)
  cached = get_cache("lunarcrush:topic:bitcoin")
  if cached:
      return cached

  # 2. API 호출 후 저장 (TTL 60분)
  set_cache("lunarcrush:topic:bitcoin", response_data,
            source="lunarcrush", ttl_minutes=60)

  # 3. Rate limit 시 stale fallback
  stale = get_stale_cache("lunarcrush:topic:bitcoin")
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "1.0.0"


def _get_client():
    """Supabase 클라이언트 지연 로딩 (순환 import 방지)"""
    try:
        from db.supabase_client import get_client
        return get_client()
    except Exception as e:
        logger.warning(f"[ApiCache] Supabase 클라이언트 로드 실패: {e}")
        return None


def get_cache(cache_key: str) -> Optional[dict]:
    """
    유효한 캐시 조회 (expires_at > NOW).

    Args:
        cache_key: 캐시 키 (예: "lunarcrush:topic:bitcoin")

    Returns:
        dict: 캐시된 value (JSON)
        None: 캐시 없음 또는 만료됨
    """
    client = _get_client()
    if client is None:
        return None

    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        response = (
            client.table("api_cache")
            .select("value, created_at, hit_count")
            .eq("cache_key", cache_key)
            .gt("expires_at", now_iso)
            .limit(1)
            .execute()
        )

        if not response.data:
            logger.debug(f"[ApiCache] MISS: {cache_key}")
            return None

        row = response.data[0]
        value = row.get("value")
        hit_count = row.get("hit_count", 0) + 1

        # hit_count 증가 (best effort)
        try:
            client.table("api_cache").update(
                {"hit_count": hit_count}
            ).eq("cache_key", cache_key).execute()
        except Exception as e:
            logger.debug(f"[ApiCache] hit_count 업데이트 실패: {e}")

        logger.info(f"[ApiCache] HIT: {cache_key} (hit_count={hit_count})")
        return value

    except Exception as e:
        logger.warning(f"[ApiCache] 조회 실패 {cache_key}: {e}")
        return None


def set_cache(
    cache_key: str,
    value: dict,
    source: str,
    ttl_minutes: int = 60,
) -> bool:
    """
    캐시 저장 (UPSERT).

    Args:
        cache_key: 캐시 키
        value: 저장할 JSON 데이터
        source: 데이터 소스 ("lunarcrush" | "crypto_com")
        ttl_minutes: TTL (분) — 기본 60분

    Returns:
        bool: 성공 여부
    """
    client = _get_client()
    if client is None:
        return False

    try:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ttl_minutes)

        payload = {
            "cache_key": cache_key,
            "value": value,
            "source": source,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "hit_count": 0,
        }

        (
            client.table("api_cache")
            .upsert(payload, on_conflict="cache_key")
            .execute()
        )

        logger.info(
            f"[ApiCache] SET: {cache_key} "
            f"(source={source}, ttl={ttl_minutes}분)"
        )
        return True

    except Exception as e:
        logger.warning(f"[ApiCache] 저장 실패 {cache_key}: {e}")
        return False


def get_stale_cache(cache_key: str) -> Optional[dict]:
    """
    만료된 캐시라도 최근값 반환 (rate limit fallback용).

    Args:
        cache_key: 캐시 키

    Returns:
        dict: 가장 최근 캐시된 value
        None: 캐시 자체가 없음
    """
    client = _get_client()
    if client is None:
        return None

    try:
        response = (
            client.table("api_cache")
            .select("value, created_at")
            .eq("cache_key", cache_key)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not response.data:
            logger.warning(f"[ApiCache] STALE MISS: {cache_key}")
            return None

        row = response.data[0]
        created_at = row.get("created_at", "?")
        logger.warning(
            f"[ApiCache] STALE HIT: {cache_key} "
            f"(created_at={created_at})"
        )
        return row.get("value")

    except Exception as e:
        logger.warning(f"[ApiCache] Stale 조회 실패 {cache_key}: {e}")
        return None


def cleanup_expired(days: int = 7) -> int:
    """
    N일 이전 만료된 캐시 정리 (선택적 운영).

    Args:
        days: N일 이전 데이터 삭제 (기본 7일)

    Returns:
        int: 삭제된 행 수
    """
    client = _get_client()
    if client is None:
        return 0

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        response = (
            client.table("api_cache")
            .delete()
            .lt("created_at", cutoff)
            .execute()
        )
        deleted = len(response.data) if response.data else 0
        logger.info(f"[ApiCache] Cleanup: {deleted}건 삭제 ({days}일 이전)")
        return deleted

    except Exception as e:
        logger.warning(f"[ApiCache] Cleanup 실패: {e}")
        return 0
