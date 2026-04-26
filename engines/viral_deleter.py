"""
Investment OS — Viral Deleter v1.0.0

X 트윗 삭제 실행 모듈. viral_performance_tracker가 폐기 결정 후 위임.

핵심 책임:
- mode=full → X API DELETE 실행 + DB 마킹
- mode=dry_run → DB 로깅만 (실제 삭제 없음)
- mode=monitor → 호출되지 않음 (Tracker에서 차단)
- 스레드인 경우 첫 트윗만 삭제 (X가 자식 트윗 자동 처리)
- 모든 결과는 viral_logs.is_deleted/delete_mode/delete_reason 업데이트

안전 원칙:
- X API 호출 성공 후에만 DB 마킹 (트랜잭션성)
- 실패 시 retry 없음 (다음 cron 사이클에서 재시도)
- 삭제 전 원본 데이터(opt_a, opt_b 등) 영구 보관 (감사 추적)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import tweepy

from db.viral_log_store import update_log_deleted

VERSION = "1.0.0"

logger = logging.getLogger(__name__)


@dataclass
class DeletionResult:
    """삭제 실행 결과 (호출 측 통계용)."""

    log_id: str
    tweet_id: str
    mode: str
    success: bool
    action_taken: str    # 'deleted' | 'logged_dry_run' | 'failed'
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────
# X API Client (싱글톤)
# ─────────────────────────────────────────────────────────────────────────


_x_client: tweepy.Client | None = None


def _get_x_client() -> tweepy.Client:
    """tweepy.Client 싱글톤. user context 인증 (delete_tweet 필수)."""
    global _x_client
    if _x_client is None:
        consumer_key = os.getenv("X_API_KEY")
        consumer_secret = os.getenv("X_API_SECRET")
        access_token = os.getenv("X_ACCESS_TOKEN")
        access_token_secret = os.getenv("X_ACCESS_TOKEN_SECRET")

        if not all([consumer_key, consumer_secret, access_token, access_token_secret]):
            raise RuntimeError(
                "[ViralDeleter] X API 인증 정보 누락 — 환경변수 확인 필요"
            )

        _x_client = tweepy.Client(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
            wait_on_rate_limit=False,
        )
    return _x_client


# ─────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────────────


def execute_deletion(
    log_id: str,
    tweet_id: str,
    delete_reason: str,
    mode: str,
) -> DeletionResult:
    """트윗 삭제 실행."""
    if mode not in ("full", "dry_run"):
        logger.error(
            "[ViralDeleter] 잘못된 mode=%s (full/dry_run만 허용) log_id=%s",
            mode,
            log_id,
        )
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode=mode,
            success=False,
            action_taken="failed",
            error=f"invalid_mode:{mode}",
        )

    if mode == "dry_run":
        return _execute_dry_run(log_id, tweet_id, delete_reason)
    return _execute_full_deletion(log_id, tweet_id, delete_reason)


def _execute_dry_run(
    log_id: str, tweet_id: str, delete_reason: str
) -> DeletionResult:
    """실제 삭제 없이 DB에만 'would_delete' 로그 기록."""
    db_ok = update_log_deleted(
        log_id=log_id,
        is_deleted=False,
        delete_mode="dry_run",
        delete_reason=f"would_delete:{delete_reason}",
    )

    logger.info(
        "[ViralDeleter] DRY_RUN — 시뮬레이션 log_id=%s tweet_id=%s reason=%s",
        log_id,
        tweet_id,
        delete_reason,
    )

    return DeletionResult(
        log_id=log_id,
        tweet_id=tweet_id,
        mode="dry_run",
        success=db_ok,
        action_taken="logged_dry_run" if db_ok else "failed",
        error=None if db_ok else "db_update_failed",
    )


def _execute_full_deletion(
    log_id: str, tweet_id: str, delete_reason: str
) -> DeletionResult:
    """X API 호출하여 실제 삭제 + DB 마킹."""
    try:
        client = _get_x_client()
    except RuntimeError as e:
        logger.error("[ViralDeleter] X 클라이언트 초기화 실패: %s", e)
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode="full",
            success=False,
            action_taken="failed",
            error=str(e),
        )

    # 1) X API DELETE
    try:
        response = client.delete_tweet(id=tweet_id)
        x_deleted = bool(
            response and response.data and response.data.get("deleted")
        )
        if not x_deleted:
            logger.error(
                "[ViralDeleter] X API 삭제 응답 비정상 log_id=%s tweet_id=%s",
                log_id,
                tweet_id,
            )
            return DeletionResult(
                log_id=log_id,
                tweet_id=tweet_id,
                mode="full",
                success=False,
                action_taken="failed",
                error="x_api_response_not_deleted",
            )
    except tweepy.NotFound:
        logger.warning(
            "[ViralDeleter] X에 트윗 없음 (이미 삭제) → DB만 동기화 "
            "log_id=%s tweet_id=%s",
            log_id,
            tweet_id,
        )
        update_log_deleted(
            log_id=log_id,
            is_deleted=True,
            delete_mode="auto",
            delete_reason=f"already_gone:{delete_reason}",
        )
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode="full",
            success=True,
            action_taken="deleted",
            error=None,
        )
    except tweepy.TooManyRequests:
        logger.warning(
            "[ViralDeleter] X API rate limit log_id=%s tweet_id=%s "
            "→ 다음 cron 재시도",
            log_id,
            tweet_id,
        )
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode="full",
            success=False,
            action_taken="failed",
            error="rate_limited",
        )
    except tweepy.Forbidden as e:
        logger.error(
            "[ViralDeleter] X API 권한 거부 log_id=%s tweet_id=%s: %s",
            log_id,
            tweet_id,
            e,
        )
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode="full",
            success=False,
            action_taken="failed",
            error=f"forbidden:{e}",
        )
    except Exception as e:
        logger.error(
            "[ViralDeleter] X API 알 수 없는 오류 log_id=%s tweet_id=%s: %s",
            log_id,
            tweet_id,
            e,
        )
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode="full",
            success=False,
            action_taken="failed",
            error=f"x_api_error:{e}",
        )

    # 2) DB UPDATE
    db_ok = update_log_deleted(
        log_id=log_id,
        is_deleted=True,
        delete_mode="auto",
        delete_reason=delete_reason,
    )

    if db_ok:
        logger.info(
            "[ViralDeleter] 트윗 삭제 + DB 마킹 완료 log_id=%s tweet_id=%s reason=%s",
            log_id,
            tweet_id,
            delete_reason,
        )
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode="full",
            success=True,
            action_taken="deleted",
            error=None,
        )
    else:
        logger.critical(
            "[ViralDeleter] X 삭제 성공 / DB 마킹 실패 — 수동 보정 필요 "
            "log_id=%s tweet_id=%s",
            log_id,
            tweet_id,
        )
        return DeletionResult(
            log_id=log_id,
            tweet_id=tweet_id,
            mode="full",
            success=False,
            action_taken="failed",
            error="db_update_failed_after_x_deletion",
        )
