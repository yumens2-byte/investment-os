"""
db/supabase_client.py
Investment Comic v2.0 — Supabase 연결 클라이언트

담당: published_comics, episode_context 테이블 CRUD
"""

import os
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Supabase 클라이언트 싱글톤 반환"""
    global _client
    if _client is None:
        try:
            from supabase import create_client
            url = os.environ["SUPABASE_URL"]
            key = os.environ["SUPABASE_KEY"]
            _client = create_client(url, key)
            logger.info("[Supabase] 연결 성공")
        except KeyError as e:
            raise EnvironmentError(f"[Supabase] 환경변수 누락: {e}")
        except Exception as e:
            raise ConnectionError(f"[Supabase] 연결 실패: {e}")
    return _client


# ── published_comics ──────────────────────────────────────

def check_duplicate(publish_date: date, comic_type: str) -> bool:
    """
    당일 동일 타입 발행 여부 확인
    Returns: True = 이미 발행됨 (SKIP), False = 미발행 (진행)
    """
    try:
        result = (
            get_client()
            .table("published_comics")
            .select("id, tweet_id")
            .eq("publish_date", str(publish_date))
            .eq("comic_type", comic_type)
            .execute()
        )
        # DRY_RUN 기록은 실 발행으로 간주하지 않음
        real_records = [r for r in result.data if r.get("tweet_id") != "DRY_RUN"]
        is_dup = len(real_records) > 0
        if is_dup:
            logger.info(f"[중복체크] {publish_date} {comic_type} 이미 실 발행됨 → SKIP")
        elif len(result.data) > 0:
            logger.info(f"[중복체크] DRY_RUN 기록만 존재 → 실 발행 진행")
        return is_dup
    except Exception as e:
        # DB 조회 실패 시 안전하게 중복 아님으로 처리 (발행 시도)
        logger.warning(f"[중복체크] DB 조회 실패, 발행 진행: {e}")
        return False


def save_publish_record(
    publish_date: date,
    comic_type: str,
    episode_no: int,
    risk_level: str,
    tweet_id: Optional[str],
    cut_count: int,
    cost_usd: float,
    status: str = "SUCCESS"
) -> None:
    """발행 이력 저장"""
    try:
        get_client().table("published_comics").insert({
            "publish_date": str(publish_date),
            "comic_type":   comic_type,
            "episode_no":   episode_no,
            "risk_level":   risk_level,
            "tweet_id":     tweet_id,
            "cut_count":    cut_count,
            "cost_usd":     round(cost_usd, 4),
            "status":       status,
        }).execute()
        logger.info(f"[DB] 발행 이력 저장 완료 — episode={episode_no}, tweet={tweet_id}")
    except Exception as e:
        logger.error(f"[DB] 발행 이력 저장 실패: {e}")
        raise


def save_publish_failure(
    publish_date: date,
    comic_type: str,
    episode_no: int,
    risk_level: str,
    error_msg: str
) -> None:
    """실패 이력 저장 (운영 추적용)"""
    try:
        get_client().table("published_comics").insert({
            "publish_date": str(publish_date),
            "comic_type":   comic_type,
            "episode_no":   episode_no,
            "risk_level":   risk_level,
            "tweet_id":     None,
            "cut_count":    0,
            "cost_usd":     0,
            "status":       "FAILED",
        }).execute()
    except Exception as e:
        logger.warning(f"[DB] 실패 이력 저장 중 오류: {e}")


# ── episode_context ───────────────────────────────────────

def get_next_episode_no() -> int:
    """다음 에피소드 번호 반환"""
    try:
        result = (
            get_client()
            .table("episode_context")
            .select("episode_no")
            .order("episode_no", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["episode_no"] + 1
        return 1
    except Exception as e:
        logger.warning(f"[DB] 에피소드 번호 조회 실패, 기본값 1 사용: {e}")
        return 1


def get_recent_episodes(limit: int = 3) -> list[dict]:
    """
    Claude 프롬프트 연속성 주입용 최근 에피소드 요약
    Returns: [{"episode_no": n, "title": "...", "summary": "...", "risk_level": "..."}]
    """
    try:
        result = (
            get_client()
            .table("episode_context")
            .select("episode_no, title, summary, risk_level")
            .order("episode_no", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"[DB] 이전 에피소드 조회 실패, 빈 리스트 반환: {e}")
        return []


def save_episode_context(
    episode_no: int,
    comic_type: str,
    risk_level: str,
    title: str,
    summary: str
) -> None:
    """에피소드 컨텍스트 저장 (다음 에피소드 연속성용)"""
    try:
        get_client().table("episode_context").insert({
            "episode_no": episode_no,
            "comic_type": comic_type,
            "risk_level": risk_level,
            "title":      title,
            "summary":    summary,
        }).execute()
        logger.info(f"[DB] 에피소드 컨텍스트 저장 — Ep.{episode_no}: {title}")
    except Exception as e:
        logger.error(f"[DB] 에피소드 컨텍스트 저장 실패: {e}")
        raise
