"""
core/alert_state_backend.py (v1.0.0)
=====================================
Alert 쿨다운 상태 Supabase 백엔드 (T-4, 2026-08-27).

역할:
  - ALERT_STATE_BACKEND 모드 판정 (file / dual / supabase)
  - public.os_alert_history 테이블 기록/조회

모드 정책:
  file     (기본) : 기존 파일 판정 — DB 미사용 (동작 무변경)
  dual            : 파일 판정 + DB 병행기록 + 판정 일치율 대조 로그 (Shadow)
  supabase        : DB 판정 + 파일 병행기록 유지 (env 1줄 롤백 보장)
  유효값 외 입력  : file 강등 + CONFIG WARNING (KR REPLY_MODE 오입력 실사고 준용)

설계 원칙:
  - db_record()   : 절대 raise 하지 않음 (기록 실패가 발행 흐름을 중단시키지 않음.
                    파일 기록은 호출측에서 항상 병행 유지되므로 안전)
  - db_fetch_last(): 예외를 전파함 — 호출측이 "DB 예외 → 파일 폴백"과
                    "DB 정상 + 기록 없음(None) → 최초 발송"을 구분해야 하기 때문
  - created_at    : 클라이언트 UTC 타임스탬프를 명시 기록 (파일 이력과 시각 기준 통일)

테이블 (마이그레이션 S0):
  public.os_alert_history (append-only)
    alert_type / level / channel('main'|'x_emotion') / tweet_id / preview
    / vix_level / created_at
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

TABLE_NAME = "os_alert_history"
VALID_MODES = ("file", "dual", "supabase")

# 강등 경고 1회 출력 플래그 (매 판정마다 로그 반복 방지)
_invalid_mode_warned = False


def get_mode() -> str:
    """
    ALERT_STATE_BACKEND 모드 반환.
    유효값(file/dual/supabase) 외 입력은 file 강등 + CONFIG WARNING(1회).
    """
    global _invalid_mode_warned
    raw = os.getenv("ALERT_STATE_BACKEND", "file").strip().lower()
    if raw in VALID_MODES:
        return raw
    if not _invalid_mode_warned:
        logger.warning(
            f"[AlertStateBackend] CONFIG WARNING — "
            f"ALERT_STATE_BACKEND 유효값 아님: '{raw}' → 'file' 강등 "
            f"(유효값: {VALID_MODES})"
        )
        _invalid_mode_warned = True
    return "file"


def _get_client():
    """Supabase 클라이언트 (기존 규약 패턴 — db.supabase_client.get_client 단일 경로)"""
    from db.supabase_client import get_client
    return get_client()


def db_record(
    alert_type: str,
    level: str,
    tweet_id: str = "",
    preview: str = "",
    channel: str = "main",
    vix_level: int | None = None,
    created_at: str | None = None,
) -> bool:
    """
    os_alert_history INSERT (append-only).
    실패 시 로그만 남기고 False 반환 — 절대 raise 하지 않음.
    """
    try:
        row = {
            "alert_type": alert_type,
            "level":      level,
            "channel":    channel,
            "tweet_id":   str(tweet_id)[:40] if tweet_id else "",
            "preview":    (preview or "")[:120],
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
        if vix_level is not None:
            row["vix_level"] = int(vix_level)

        _get_client().table(TABLE_NAME).insert(row).execute()
        logger.info(
            f"[AlertStateBackend] DB 기록: {alert_type}/{level} channel={channel}"
        )
        return True
    except Exception as e:  # noqa: BLE001 — 발행 무중단 설계 (기록 실패 격리)
        logger.warning(
            f"[AlertStateBackend] DB 기록 실패 (파일 이력 유지 — 무시): {e}"
        )
        return False


def db_fetch_last(alert_type: str, channel: str = "main") -> dict | None:
    """
    (alert_type, channel) 최신 1건 조회.

    Returns:
        dict {"level", "tweet_id", "created_at", "vix_level"} — 기록 존재 시
        None — DB 정상 + 기록 없음 (호출측 "최초 발송" 판정)

    Raises:
        Exception — DB 접근 실패 (호출측 파일 폴백 트리거)
    """
    result = (
        _get_client()
        .table(TABLE_NAME)
        .select("level, tweet_id, created_at, vix_level")
        .eq("alert_type", alert_type)
        .eq("channel", channel)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def db_fetch_last_countdown(vix_level: int) -> dict | None:
    """
    VIX_COUNTDOWN + 해당 레벨 최신 1건 조회.
    append-only 특성상 "최신 1건이 오늘" ⟺ "오늘 기록 존재" (판정 등가).

    Raises:
        Exception — DB 접근 실패 (호출측 파일 폴백 트리거)
    """
    result = (
        _get_client()
        .table(TABLE_NAME)
        .select("level, tweet_id, created_at, vix_level")
        .eq("alert_type", "VIX_COUNTDOWN")
        .eq("channel", "main")
        .eq("vix_level", int(vix_level))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def parse_db_timestamp(ts_str: str) -> datetime:
    """
    DB created_at(ISO) → aware datetime(UTC 기준) 파싱.
    'Z' 표기 방어적 정규화 + naive 반환 시 UTC 부여.
    """
    ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts
