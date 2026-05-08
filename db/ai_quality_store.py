"""
db/ai_quality_store.py
=======================
AI 톤 생성 품질 로그 적재 (x_formatter v1.5.0~).

목적:
  - 매 AI 트윗/스레드 생성 시도를 ai_quality_log 테이블에 1로우 INSERT
  - 적재 실패해도 절대 발행 차단 안 함 (장애 격리)

설계 패턴:
  - db/dq_store.py / db/daily_store.py 일관성 유지
  - lazy-init 클라이언트 (db.supabase_client.get_client 사용)
  - exception swallow — 실패는 warning 로그만

변경이력:
  v1.0.0 (2026-05-06) 신설.
"""
from __future__ import annotations

import logging
from typing import Optional

VERSION = "1.0.0"

logger = logging.getLogger(__name__)
logger.info(f"[AIQualityStore] v{VERSION} 로드")

_TABLE = "ai_quality_log"


def _get_client():
    """기존 daily_store 패턴과 동일."""
    from db.supabase_client import get_client
    return get_client()


def log_ai_attempt(
    *,
    session: str,
    mode: str,                         # "tweet" | "thread_post"
    attempt: int,
    tone_spec=None,                    # core.tone_policy.ToneSpec | None
    output_text: Optional[str] = None,
    validation=None,                   # core.ai_output_validator.ValidationResult | None
    success: bool = False,
    fallback_used: bool = False,
    gemini_meta: Optional[dict] = None,
) -> bool:
    """
    AI 트윗/스레드 생성 1회 시도 적재.

    실패해도 raise 안 함 — 발행 파이프라인 차단 금지.

    Args:
        session:        "morning" | "intraday" | "close" | "full" | "narrative"
        mode:           "tweet" | "thread_post"
        attempt:        1, 2, 3
        tone_spec:      ToneSpec (None이면 메타 컬럼 모두 NULL)
        output_text:    생성된 텍스트 (None이면 미생성/실패)
        validation:     ValidationResult (None이면 검증 미수행 또는 fallback)
        success:        실제 발행에 사용됐는지
        fallback_used:  템플릿 fallback으로 떨어졌는지
        gemini_meta:    {"model": ..., "key_used": ..., "error": ...}

    Returns:
        True  — 적재 성공
        False — 적재 실패 (로그만, 호출자에게 영향 없음)
    """
    try:
        row = _build_row(
            session=session,
            mode=mode,
            attempt=attempt,
            tone_spec=tone_spec,
            output_text=output_text,
            validation=validation,
            success=success,
            fallback_used=fallback_used,
            gemini_meta=gemini_meta or {},
        )

        _get_client().table(_TABLE).insert(row).execute()
        return True

    except Exception as e:
        # 중요: 절대 raise 안 함. 발행 파이프라인 차단 금지.
        logger.warning(
            f"[AIQualityStore] 적재 실패 (무시): "
            f"session={session} attempt={attempt} mode={mode} | "
            f"{type(e).__name__}: {e}"
        )
        return False


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

def _build_row(
    *,
    session: str,
    mode: str,
    attempt: int,
    tone_spec,
    output_text: Optional[str],
    validation,
    success: bool,
    fallback_used: bool,
    gemini_meta: dict,
) -> dict:
    """
    INSERT용 row dict 생성. None 값은 그대로 NULL로 매핑.

    스키마 매핑:
      ai_quality_log.session         = session
      ai_quality_log.mode            = mode
      ai_quality_log.attempt         = attempt
      ai_quality_log.risk_level      = tone_spec.risk_level
      ai_quality_log.regime          = tone_spec.regime
      ai_quality_log.persona         = tone_spec.persona
      ai_quality_log.tone_name       = tone_spec.tone_name
      ai_quality_log.text_length     = len(output_text)
      ai_quality_log.awkwardness     = validation.awkwardness_score
      ai_quality_log.passed          = validation.passed
      ai_quality_log.failure_reason  = validation.failure_reason
      ai_quality_log.flags           = validation.to_flags_jsonb()
      ai_quality_log.output_preview  = output_text[:80]
      ai_quality_log.fallback_used   = fallback_used
      ai_quality_log.success         = success
      ai_quality_log.gemini_model    = gemini_meta["model"]
      ai_quality_log.gemini_key_used = gemini_meta["key_used"]
      ai_quality_log.gemini_error    = gemini_meta["error"]
    """
    row: dict = {
        "session":       session,
        "mode":          mode,
        "attempt":       attempt,
        "fallback_used": fallback_used,
        "success":       success,
    }

    # ToneSpec 메타
    if tone_spec is not None:
        try:
            meta = tone_spec.to_meta()
            row["risk_level"] = meta.get("risk_level")
            row["regime"]     = meta.get("regime")
            row["persona"]    = meta.get("persona")
            row["tone_name"]  = meta.get("tone_name")
        except Exception as e:
            logger.warning(f"[AIQualityStore] ToneSpec 추출 실패 (무시): {e}")

    # 출력 텍스트
    if output_text is not None:
        row["text_length"]    = len(output_text)
        row["output_preview"] = output_text[:80]

    # 검증 결과
    if validation is not None:
        try:
            row["awkwardness"]    = float(validation.awkwardness_score)
            row["passed"]         = bool(validation.passed)
            row["failure_reason"] = validation.failure_reason
            row["flags"]          = validation.to_flags_jsonb()
        except Exception as e:
            logger.warning(f"[AIQualityStore] ValidationResult 추출 실패 (무시): {e}")

    # Gemini 메타
    if gemini_meta:
        row["gemini_model"]    = gemini_meta.get("model")
        row["gemini_key_used"] = gemini_meta.get("key_used")
        row["gemini_error"]    = gemini_meta.get("error")

    return row
