"""
db/viral_log_store.py
================================
C-20 viral_logs 테이블 DAO.

VERSION = "1.2.0"

v1.2.0 (2026-04-26):
  - [긴급] SQLAlchemy 패턴 → supabase-py 패턴 전면 재작성
  - 마스터 시스템 db/supabase_client.py(get_client()) 100% 준수
  - 마스터 시스템 daily_store.py 호출 패턴 준수
  - 외부 인터페이스(함수 시그니처) 무변경 → engines/viral_engine.py 영향 없음

v1.0.0 (2026-04-26): 초기 버전 (SQLAlchemy 기반, deprecated)

함수 목록:
  save_log()                          — INSERT
  update_log_published()              — 발행 결과 마킹
  update_log_protected()              — 운영자 수동 보호
  update_log_deleted()                — 자동/수동 삭제 마킹
  find_duplicate_within_hours()       — NFR-03 중복 차단
  get_recent_active_logs()            — Tracker 측정 후보 조회
  get_log_by_id()                     — 단건 조회
  count_today_auto_deletions()        — 오늘 자동 삭제 건수
  get_pending_evaluation_logs()       — 80h 평가 대상
  list_deleted_today()                — 오늘 자동 삭제 목록
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

VERSION = "1.2.0"

KST = timezone(timedelta(hours=9))


def _get_client():
    """Supabase 클라이언트 가져오기 (마스터 패턴)."""
    from db.supabase_client import get_client
    return get_client()


def _now_utc_iso() -> str:
    """현재 시각 ISO 문자열 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _hours_ago_iso(hours: int) -> str:
    """N시간 전 시각 ISO 문자열 (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _today_kst_str() -> str:
    """오늘 KST 자정 시각 ISO 문자열 (UTC 변환)."""
    now_kst = datetime.now(KST)
    midnight_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_kst.astimezone(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────
# 데이터 클래스 (외부 인터페이스 — viral_engine.py가 사용)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ViralLog:
    """Supabase viral_logs INSERT용 페이로드."""

    publish_date: str                   # 'YYYY-MM-DD'
    session: str                        # 'morning' | 'evening' | 'viral_c20_*'
    target_segment: str                 # 'S2_25_29' 등
    conflict_axis: str                  # 'money' | 'time' 등
    candidate_no: int = 1
    is_published: bool = False
    viral_score: int = 0
    score_shock: int | None = None
    score_relatability: int | None = None
    score_commentability: int | None = None
    score_safety: int | None = None
    needs_image: bool | None = None
    image_generated: bool | None = None
    opt_a: str | None = None
    opt_b: str | None = None
    condition_text: str | None = None
    cta_used: str | None = None
    disclaimer_used: str | None = None
    tweet_id: str | None = None
    thread_ids: list[str] = field(default_factory=list)
    tg_message_id: str | None = None
    reasoning_json: dict[str, Any] = field(default_factory=dict)
    discard_reason: str | None = None
    policy_version: str = "unknown"


# ─────────────────────────────────────────────────────────────────────────
# 1. INSERT — save_log
# ─────────────────────────────────────────────────────────────────────────


def save_log(log: ViralLog) -> str | None:
    """
    INSERT 후 log_id (UUID 문자열) 반환. 실패 시 None.
    log_id는 DB DEFAULT(gen_random_uuid())로 자동 생성됨.
    """
    try:
        row = {
            "publish_date":         log.publish_date,
            "session":              log.session,
            "target_segment":       log.target_segment,
            "conflict_axis":        log.conflict_axis,
            "candidate_no":         log.candidate_no,
            "is_published":         log.is_published,
            "viral_score":          log.viral_score,
            "score_shock":          log.score_shock,
            "score_relatability":   log.score_relatability,
            "score_commentability": log.score_commentability,
            "score_safety":         log.score_safety,
            "needs_image":          log.needs_image,
            "image_generated":      log.image_generated,
            "opt_a":                log.opt_a,
            "opt_b":                log.opt_b,
            "condition_text":       log.condition_text,
            "cta_used":             log.cta_used,
            "disclaimer_used":      log.disclaimer_used,
            "tweet_id":             log.tweet_id,
            "thread_ids":           log.thread_ids,            # supabase-py가 list → jsonb 자동 변환
            "tg_message_id":        log.tg_message_id,
            "reasoning_json":       log.reasoning_json,         # dict → jsonb
            "discard_reason":       log.discard_reason,
            "policy_version":       log.policy_version,
        }

        result = _get_client().table("viral_logs").insert(row).execute()

        if result.data and len(result.data) > 0:
            log_id = result.data[0].get("log_id")
            logger.info(
                f"[ViralLogStore v{VERSION}] INSERT 완료 log_id={log_id} "
                f"segment={log.target_segment} axis={log.conflict_axis} "
                f"score={log.viral_score} published={log.is_published}"
            )
            return log_id

        logger.warning("[ViralLogStore] INSERT 응답에 data 없음")
        return None

    except Exception as e:
        logger.warning(f"[ViralLogStore] INSERT 실패 (무시): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# 2. UPDATE — update_log_published / protected / deleted
# ─────────────────────────────────────────────────────────────────────────


def update_log_published(
    log_id: str,
    tweet_id: str | None,
    thread_ids: list[str] | None = None,
    tg_message_id: str | None = None,
    image_generated: bool | None = None,
) -> bool:
    """발행 결과 채워넣기."""
    try:
        update_data: dict[str, Any] = {
            "is_published":  True,
            "tweet_id":      tweet_id,
            "thread_ids":    thread_ids or [],
            "tg_message_id": tg_message_id,
        }
        if image_generated is not None:
            update_data["image_generated"] = image_generated

        result = (
            _get_client()
            .table("viral_logs")
            .update(update_data)
            .eq("log_id", log_id)
            .execute()
        )

        ok = bool(result.data and len(result.data) > 0)
        if ok:
            logger.info(
                f"[ViralLogStore] PUBLISHED 마킹 log_id={log_id} tweet_id={tweet_id}"
            )
        return ok

    except Exception as e:
        logger.warning(f"[ViralLogStore] UPDATE published 실패 log_id={log_id}: {e}")
        return False


def update_log_protected(log_id: str, protected: bool) -> bool:
    """운영자 수동 보호 플래그."""
    try:
        result = (
            _get_client()
            .table("viral_logs")
            .update({"protected": protected})
            .eq("log_id", log_id)
            .execute()
        )
        return bool(result.data and len(result.data) > 0)
    except Exception as e:
        logger.warning(f"[ViralLogStore] UPDATE protected 실패 log_id={log_id}: {e}")
        return False


def update_log_deleted(
    log_id: str,
    is_deleted: bool,
    delete_mode: str,
    delete_reason: str | None = None,
) -> bool:
    """삭제 결과 마킹. mode='dry_run' 시 is_deleted=False로 호출."""
    if delete_mode not in ("auto", "manual", "dry_run"):
        logger.error(
            f"[ViralLogStore] 잘못된 delete_mode={delete_mode} log_id={log_id}"
        )
        return False

    try:
        update_data: dict[str, Any] = {
            "is_deleted":    is_deleted,
            "delete_mode":   delete_mode,
            "delete_reason": delete_reason,
        }
        # is_deleted=True 시에만 deleted_at 설정 (NOW() 대신 ISO 문자열)
        if is_deleted:
            update_data["deleted_at"] = _now_utc_iso()

        result = (
            _get_client()
            .table("viral_logs")
            .update(update_data)
            .eq("log_id", log_id)
            .execute()
        )

        ok = bool(result.data and len(result.data) > 0)
        if ok:
            logger.info(
                f"[ViralLogStore] DELETE 마킹 log_id={log_id} mode={delete_mode} "
                f"reason={delete_reason}"
            )
        return ok

    except Exception as e:
        logger.warning(f"[ViralLogStore] UPDATE deleted 실패 log_id={log_id}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# 3. SELECT — 조회 함수
# ─────────────────────────────────────────────────────────────────────────


def find_duplicate_within_hours(
    segment: str, axis: str, hours: int = 24
) -> dict[str, Any] | None:
    """NFR-03: 동일 segment×axis 조합이 N시간 내 발행되었는지 확인."""
    try:
        cutoff = _hours_ago_iso(hours)
        result = (
            _get_client()
            .table("viral_logs")
            .select("log_id, created_at")
            .eq("target_segment", segment)
            .eq("conflict_axis", axis)
            .eq("is_published", True)
            .eq("is_deleted", False)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if result.data and len(result.data) > 0:
            return result.data[0]
        return None

    except Exception as e:
        logger.warning(
            f"[ViralLogStore] find_duplicate 실패 segment={segment} axis={axis}: {e}"
        )
        return None


def get_recent_active_logs(within_hours: int = 96) -> list[dict[str, Any]]:
    """Auto-Pruning Tracker가 측정 후보를 조회 (기본 96h = 80h + 여유)."""
    try:
        cutoff = _hours_ago_iso(within_hours)
        result = (
            _get_client()
            .table("viral_logs")
            .select(
                "log_id, publish_date, session, target_segment, conflict_axis, "
                "viral_score, tweet_id, thread_ids, protected, is_deleted, created_at"
            )
            .eq("is_published", True)
            .eq("is_deleted", False)
            .not_.is_("tweet_id", "null")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    except Exception as e:
        logger.warning(f"[ViralLogStore] get_recent_active_logs 실패: {e}")
        return []


def get_log_by_id(log_id: str) -> dict[str, Any] | None:
    """단건 조회."""
    try:
        result = (
            _get_client()
            .table("viral_logs")
            .select("*")
            .eq("log_id", log_id)
            .limit(1)
            .execute()
        )
        if result.data and len(result.data) > 0:
            return result.data[0]
        return None

    except Exception as e:
        logger.warning(f"[ViralLogStore] get_log_by_id 실패 log_id={log_id}: {e}")
        return None


def count_today_auto_deletions() -> int:
    """
    오늘(KST 자정 기준) 자동 삭제된 건수.
    실패 시 9999 반환 (보수적 차단 — daily_limit 검증에서 차단됨).
    """
    try:
        today_kst_iso = _today_kst_str()
        # supabase-py의 count="exact" 모드 활용
        result = (
            _get_client()
            .table("viral_logs")
            .select("log_id", count="exact")
            .eq("is_deleted", True)
            .eq("delete_mode", "auto")
            .gte("deleted_at", today_kst_iso)
            .execute()
        )
        return int(result.count) if result.count is not None else 0

    except Exception as e:
        logger.warning(f"[ViralLogStore] count_today_auto_deletions 실패: {e}")
        return 9999


def get_pending_evaluation_logs(
    min_hours: int = 80, max_hours: int = 96
) -> list[dict[str, Any]]:
    """폐기 결정 평가 대상 조회 (min_hours ≤ 경과 ≤ max_hours)."""
    try:
        # 80h ≤ 경과 → created_at ≤ NOW() - 80h
        # 96h ≥ 경과 → created_at ≥ NOW() - 96h
        upper_cutoff = _hours_ago_iso(min_hours)   # 더 최근 시점
        lower_cutoff = _hours_ago_iso(max_hours)   # 더 과거 시점

        result = (
            _get_client()
            .table("viral_logs")
            .select(
                "log_id, publish_date, session, target_segment, conflict_axis, "
                "viral_score, tweet_id, thread_ids, protected, created_at"
            )
            .eq("is_published", True)
            .eq("is_deleted", False)
            .not_.is_("tweet_id", "null")
            .gte("created_at", lower_cutoff)
            .lte("created_at", upper_cutoff)
            .order("created_at", desc=False)
            .execute()
        )
        return result.data or []

    except Exception as e:
        logger.warning(f"[ViralLogStore] get_pending_evaluation_logs 실패: {e}")
        return []


def list_deleted_today() -> list[dict[str, Any]]:
    """오늘 자동 삭제 전체 목록 (운영 모니터링용)."""
    try:
        today_kst_iso = _today_kst_str()
        result = (
            _get_client()
            .table("viral_logs")
            .select(
                "log_id, tweet_id, target_segment, conflict_axis, "
                "viral_score, delete_mode, delete_reason, deleted_at"
            )
            .eq("is_deleted", True)
            .gte("deleted_at", today_kst_iso)
            .order("deleted_at", desc=True)
            .execute()
        )
        return result.data or []

    except Exception as e:
        logger.warning(f"[ViralLogStore] list_deleted_today 실패: {e}")
        return []
