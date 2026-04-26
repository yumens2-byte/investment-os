"""
Investment OS — Viral Log Store v1.1.0

viral_logs 테이블 DAO.
- save_log() : 콘텐츠 생성 후 INSERT (재시도 후보 포함)
- update_log_published() : X/TG 발행 완료 시 호출
- update_log_protected() : 운영자 수동 보호 플래그
- update_log_deleted() : Auto-Pruning 위임 (삭제 마킹)
- find_duplicate_within_hours() : NFR-03 중복 차단
- get_recent_active_logs() : Auto-Pruning Tracker가 측정 후보 조회용
- get_log_by_id() : 단건 조회
- count_today_auto_deletions() : 오늘 자동 삭제 건수 (daily_limit 검증)  [v1.1.0]
- get_pending_evaluation_logs() : 80h 평가 대상 조회                     [v1.1.0]
- list_deleted_today() : 오늘 자동 삭제 전체 목록 (운영 모니터링)         [v1.1.0]

v1.1.0 (2026-04-26):
  - Auto-Pruning 전용 쿼리 3개 추가 (DAO 분리 원칙)
  - viral_metrics_store에 임시 배치된 count_today_auto_deletions 이전
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db.supabase_client import get_engine

VERSION = "1.1.0"

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ViralLog:
    """Supabase viral_logs INSERT용 페이로드."""

    publish_date: str                   # 'YYYY-MM-DD'
    session: str                        # 'morning' | 'evening'
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
# INSERT
# ─────────────────────────────────────────────────────────────────────────

_INSERT_SQL = text("""
INSERT INTO viral_logs (
    publish_date, session, target_segment, conflict_axis, candidate_no,
    is_published, viral_score, score_shock, score_relatability,
    score_commentability, score_safety, needs_image, image_generated,
    opt_a, opt_b, condition_text, cta_used, disclaimer_used,
    tweet_id, thread_ids, tg_message_id, reasoning_json, discard_reason,
    policy_version
) VALUES (
    :publish_date, :session, :target_segment, :conflict_axis, :candidate_no,
    :is_published, :viral_score, :score_shock, :score_relatability,
    :score_commentability, :score_safety, :needs_image, :image_generated,
    :opt_a, :opt_b, :condition_text, :cta_used, :disclaimer_used,
    :tweet_id, CAST(:thread_ids AS jsonb), :tg_message_id,
    CAST(:reasoning_json AS jsonb), :discard_reason, :policy_version
)
RETURNING log_id
""")


def save_log(log: ViralLog) -> str | None:
    """INSERT 후 log_id (UUID 문자열) 반환. 실패 시 None."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                _INSERT_SQL,
                {
                    "publish_date": log.publish_date,
                    "session": log.session,
                    "target_segment": log.target_segment,
                    "conflict_axis": log.conflict_axis,
                    "candidate_no": log.candidate_no,
                    "is_published": log.is_published,
                    "viral_score": log.viral_score,
                    "score_shock": log.score_shock,
                    "score_relatability": log.score_relatability,
                    "score_commentability": log.score_commentability,
                    "score_safety": log.score_safety,
                    "needs_image": log.needs_image,
                    "image_generated": log.image_generated,
                    "opt_a": log.opt_a,
                    "opt_b": log.opt_b,
                    "condition_text": log.condition_text,
                    "cta_used": log.cta_used,
                    "disclaimer_used": log.disclaimer_used,
                    "tweet_id": log.tweet_id,
                    "thread_ids": json.dumps(log.thread_ids, ensure_ascii=False),
                    "tg_message_id": log.tg_message_id,
                    "reasoning_json": json.dumps(
                        log.reasoning_json, ensure_ascii=False
                    ),
                    "discard_reason": log.discard_reason,
                    "policy_version": log.policy_version,
                },
            ).fetchone()
            log_id = str(row[0]) if row else None
            logger.info(
                "[ViralLogStore] INSERT 완료 log_id=%s segment=%s axis=%s "
                "score=%d published=%s",
                log_id,
                log.target_segment,
                log.conflict_axis,
                log.viral_score,
                log.is_published,
            )
            return log_id
    except SQLAlchemyError as e:
        logger.error(
            "[ViralLogStore] INSERT 실패 segment=%s axis=%s: %s",
            log.target_segment,
            log.conflict_axis,
            e,
        )
        return None


# ─────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────

_UPDATE_PUBLISHED_SQL = text("""
UPDATE viral_logs
SET
    is_published    = true,
    tweet_id        = :tweet_id,
    thread_ids      = CAST(:thread_ids AS jsonb),
    tg_message_id   = :tg_message_id,
    image_generated = COALESCE(:image_generated, image_generated)
WHERE log_id = :log_id
RETURNING log_id
""")


def update_log_published(
    log_id: str,
    tweet_id: str | None,
    thread_ids: list[str] | None = None,
    tg_message_id: str | None = None,
    image_generated: bool | None = None,
) -> bool:
    """발행 결과 채워넣기."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                _UPDATE_PUBLISHED_SQL,
                {
                    "log_id": log_id,
                    "tweet_id": tweet_id,
                    "thread_ids": json.dumps(thread_ids or [], ensure_ascii=False),
                    "tg_message_id": tg_message_id,
                    "image_generated": image_generated,
                },
            ).fetchone()
            ok = row is not None
            if ok:
                logger.info(
                    "[ViralLogStore] PUBLISHED 마킹 log_id=%s tweet_id=%s",
                    log_id,
                    tweet_id,
                )
            return ok
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] UPDATE published 실패 log_id=%s: %s", log_id, e)
        return False


_UPDATE_PROTECTED_SQL = text("""
UPDATE viral_logs SET protected = :protected WHERE log_id = :log_id
RETURNING log_id
""")


def update_log_protected(log_id: str, protected: bool) -> bool:
    """운영자 수동 보호 플래그."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                _UPDATE_PROTECTED_SQL,
                {"log_id": log_id, "protected": protected},
            ).fetchone()
            return row is not None
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] UPDATE protected 실패 log_id=%s: %s", log_id, e)
        return False


_UPDATE_DELETED_SQL = text("""
UPDATE viral_logs
SET
    is_deleted    = :is_deleted,
    deleted_at    = CASE WHEN :is_deleted THEN NOW() ELSE deleted_at END,
    delete_reason = :delete_reason,
    delete_mode   = :delete_mode
WHERE log_id = :log_id
RETURNING log_id
""")


def update_log_deleted(
    log_id: str,
    is_deleted: bool,
    delete_mode: str,
    delete_reason: str | None = None,
) -> bool:
    """삭제 결과 마킹. mode='dry_run' 시 is_deleted=False로 호출."""
    if delete_mode not in ("auto", "manual", "dry_run"):
        logger.error(
            "[ViralLogStore] 잘못된 delete_mode=%s log_id=%s", delete_mode, log_id
        )
        return False

    try:
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                _UPDATE_DELETED_SQL,
                {
                    "log_id": log_id,
                    "is_deleted": is_deleted,
                    "delete_mode": delete_mode,
                    "delete_reason": delete_reason,
                },
            ).fetchone()
            ok = row is not None
            if ok:
                logger.info(
                    "[ViralLogStore] DELETE 마킹 log_id=%s mode=%s reason=%s",
                    log_id,
                    delete_mode,
                    delete_reason,
                )
            return ok
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] UPDATE deleted 실패 log_id=%s: %s", log_id, e)
        return False


# ─────────────────────────────────────────────────────────────────────────
# SELECT
# ─────────────────────────────────────────────────────────────────────────


_FIND_DUPLICATE_SQL = text("""
SELECT log_id, created_at
FROM viral_logs
WHERE target_segment = :segment
  AND conflict_axis = :axis
  AND is_published = true
  AND is_deleted = false
  AND created_at >= NOW() - (:hours || ' hours')::INTERVAL
ORDER BY created_at DESC
LIMIT 1
""")


def find_duplicate_within_hours(
    segment: str, axis: str, hours: int = 24
) -> dict[str, Any] | None:
    """NFR-03: 동일 segment×axis 조합이 N시간 내 발행되었는지 확인."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                _FIND_DUPLICATE_SQL,
                {"segment": segment, "axis": axis, "hours": str(hours)},
            ).fetchone()
            if not row:
                return None
            return _row_to_dict(row)
    except SQLAlchemyError as e:
        logger.error(
            "[ViralLogStore] find_duplicate 실패 segment=%s axis=%s: %s",
            segment,
            axis,
            e,
        )
        return None


_GET_RECENT_ACTIVE_LOGS_SQL = text("""
SELECT
    log_id, publish_date, session, target_segment, conflict_axis,
    viral_score, tweet_id, thread_ids, protected, is_deleted, created_at
FROM viral_logs
WHERE is_published = true
  AND is_deleted = false
  AND tweet_id IS NOT NULL
  AND created_at >= NOW() - (:hours || ' hours')::INTERVAL
ORDER BY created_at DESC
""")


def get_recent_active_logs(within_hours: int = 96) -> list[dict[str, Any]]:
    """Auto-Pruning Tracker가 측정 후보를 조회 (기본 96h = 80h + 여유)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                _GET_RECENT_ACTIVE_LOGS_SQL, {"hours": str(within_hours)}
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] get_recent_active_logs 실패: %s", e)
        return []


_GET_LOG_BY_ID_SQL = text("""
SELECT
    log_id, publish_date, session, target_segment, conflict_axis,
    candidate_no, is_published, viral_score,
    score_shock, score_relatability, score_commentability, score_safety,
    needs_image, image_generated,
    opt_a, opt_b, condition_text, cta_used, disclaimer_used,
    tweet_id, thread_ids, tg_message_id,
    reasoning_json, discard_reason, policy_version,
    protected, is_deleted, deleted_at, delete_reason, delete_mode,
    created_at
FROM viral_logs
WHERE log_id = :log_id
""")


def get_log_by_id(log_id: str) -> dict[str, Any] | None:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(_GET_LOG_BY_ID_SQL, {"log_id": log_id}).fetchone()
            if not row:
                return None
            return _row_to_dict(row)
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] get_log_by_id 실패 log_id=%s: %s", log_id, e)
        return None


# ─────────────────────────────────────────────────────────────────────────
# v1.1.0 — Auto-Pruning 전용 쿼리 (DataOps+Engineer, 2026-04-26)
# ─────────────────────────────────────────────────────────────────────────


_COUNT_TODAY_AUTO_DELETIONS_SQL = text("""
SELECT COUNT(*)
FROM viral_logs
WHERE is_deleted = true
  AND delete_mode = 'auto'
  AND deleted_at >= CURRENT_DATE AT TIME ZONE 'Asia/Seoul'
""")


def count_today_auto_deletions() -> int:
    """오늘(KST 자정 기준) 자동 삭제된 건수. 실패 시 9999 반환 (보수적 차단)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(_COUNT_TODAY_AUTO_DELETIONS_SQL).fetchone()
            return int(row[0]) if row else 0
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] count_today_auto_deletions 실패: %s", e)
        return 9999


_GET_PENDING_EVALUATION_SQL = text("""
SELECT
    log_id, publish_date, session, target_segment, conflict_axis,
    viral_score, tweet_id, thread_ids, protected, created_at
FROM viral_logs
WHERE is_published   = true
  AND is_deleted     = false
  AND tweet_id       IS NOT NULL
  AND created_at <= NOW() - (:min_hours || ' hours')::INTERVAL
  AND created_at >= NOW() - (:max_hours || ' hours')::INTERVAL
ORDER BY created_at ASC
""")


def get_pending_evaluation_logs(
    min_hours: int = 80, max_hours: int = 96
) -> list[dict[str, Any]]:
    """폐기 결정 평가 대상 조회 (80h ≤ 경과 ≤ 96h)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                _GET_PENDING_EVALUATION_SQL,
                {"min_hours": str(min_hours), "max_hours": str(max_hours)},
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] get_pending_evaluation_logs 실패: %s", e)
        return []


_LIST_DELETED_TODAY_SQL = text("""
SELECT
    log_id, tweet_id, target_segment, conflict_axis,
    viral_score, delete_mode, delete_reason, deleted_at
FROM viral_logs
WHERE is_deleted = true
  AND deleted_at >= CURRENT_DATE AT TIME ZONE 'Asia/Seoul'
ORDER BY deleted_at DESC
""")


def list_deleted_today() -> list[dict[str, Any]]:
    """오늘 자동 삭제 전체 목록 (운영 모니터링용)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(_LIST_DELETED_TODAY_SQL).fetchall()
            return [_row_to_dict(r) for r in rows]
    except SQLAlchemyError as e:
        logger.error("[ViralLogStore] list_deleted_today 실패: %s", e)
        return []


# ─────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Row → dict 변환."""
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    for k, v in d.items():
        if isinstance(v, Decimal):
            d[k] = float(v)
        elif isinstance(v, UUID):
            d[k] = str(v)
        elif isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat()
    return d
