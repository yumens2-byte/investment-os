"""
gen/hero_shorts/tiktok/publish.py — 틱톡 단일 편 발행 (Zernio)
=============================================================
게이트 미통과 편은 발행하지 않는다(fail-closed).
AIGC 고지(ai_generated=True)는 필수 — T-G2.
계정 ID 는 HERO_TIKTOK_ACCOUNT_ID 환경변수 또는 인자로 주입(설계서 부록 A).
"""
import logging
import os

from .. import publish_runtime as pr
from . import account as tk_account
from . import gates as tk_gates
from . import manifest as tk_manifest

log = logging.getLogger("tiktok.publish")

VERSION = "1.2.0"
PLATFORM = "tiktok"


def resolve_account_id(account_id=None):
    aid = account_id or os.environ.get("HERO_TIKTOK_ACCOUNT_ID")
    if not aid:
        raise RuntimeError(
            "틱톡 계정 ID 미설정 — Zernio 대시보드에서 틱톡 연결 후 "
            "HERO_TIKTOK_ACCOUNT_ID 를 주입하세요(설계서 부록 A)."
        )
    return aid


def resolve_privacy_level(privacy_level=None):
    """환경변수 HERO_TIKTOK_PRIVACY_LEVEL > 기본값(PUBLIC_TO_EVERYONE)."""
    level = privacy_level or os.environ.get("HERO_TIKTOK_PRIVACY_LEVEL") \
        or tk_gates.DEFAULT_PRIVACY_LEVEL
    if level not in tk_gates.VALID_PRIVACY_LEVELS:
        raise ValueError(
            f"미정의 privacy_level '{level}' — 허용: {list(tk_gates.VALID_PRIVACY_LEVELS)}"
        )
    return level


def _as_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


_MEDIA_ERR_HINTS = (
    "extension", "content type", "media", "url", "unsupported", "download",
    "fetch", "400", "415", "422",
)


def _looks_like_media_error(exc):
    """D-14 — 미디어 URL 문제로 보이는 실패만 재시도 대상으로 판별."""
    return any(h in str(exc).lower() for h in _MEDIA_ERR_HINTS)


def publish_segment(segment, account_id=None, schedule_at=None, max_wait_sec=900,
                    poll_interval_sec=30, ai_generated=True, privacy_level=None,
                    preflight=None, fallback_media_url=None):
    """매니페스트 세그먼트 1편 발행. 게이트 미통과 시 RuntimeError.

    D-14: `fallback_media_url` 이 주어지면 1차 실패가 미디어 URL 문제로 보일 때
    그 URL 로 **딱 1회** 재시도한다. 무한 재시도는 하지 않는다(멱등성 원칙 유지).
    """
    pr.assert_publish_allowed(platform=PLATFORM)
    if not tk_manifest.segment_gate_ok(segment):
        raise RuntimeError(
            f"게이트 미통과 — segment {segment.get('segment_no')} 발행 차단"
        )
    media_url = segment.get("media_url")
    draft = _as_bool(segment.get("draft"), False)
    level = None if draft else resolve_privacy_level(
        privacy_level or segment.get("privacy_level")
    )
    # 발행 직전 T-G4 재검증 — 매니페스트를 거치지 않은 호출도 차단한다.
    # 게이트가 계정 확인보다 먼저 실행되어야 한다(자격 이전에 규율 검증).
    tg4 = tk_gates.gate_tg4(level, draft=draft)
    if not tg4["ok"]:
        raise RuntimeError(f"T-G4 실패 — 발행 차단: {tg4['problems']}")
    if not media_url:
        raise RuntimeError("media_url 없음 — 업로드 단계(upload_media)를 먼저 수행하세요")
    aid = resolve_account_id(account_id)
    # 발행 전 토큰 유효성 관측 — 무효 상태면 차단(설계 v1.0).
    # gates → preflight → media_url → 발행 순서: 규율 검증이 자격 검사보다 먼저.
    if preflight is None:
        preflight = tk_account.preflight_enabled()
    if preflight:
        health = tk_account.assert_token_ready(aid)
        log.info("틱톡 preflight: status=%s 남은=%sh", health.status, health.hours_left)
    def _attempt(url):
        log.info(
            "틱톡 발행 요청: segment=%s account=%s privacy=%s draft=%s url=%s",
            segment.get("segment_no"), aid, level, draft, url,
        )
        return pr.create_and_monitor_post(
            text=segment["caption"],
            media_url=url,
            account_id=aid,
            schedule_at=schedule_at,
            platform=PLATFORM,
            ai_generated=ai_generated,
            max_wait_sec=max_wait_sec,
            poll_interval_sec=poll_interval_sec,
            tiktok_privacy_level=level,
            tiktok_draft=draft,
            tiktok_allow_comments=segment.get("allow_comments", True),
            tiktok_allow_duet=segment.get("allow_duet", True),
            tiktok_allow_stitch=segment.get("allow_stitch", True),
        )

    try:
        return _attempt(media_url)
    except Exception as exc:
        if not fallback_media_url or fallback_media_url == media_url \
                or not _looks_like_media_error(exc):
            raise
        log.warning("발행 1차 실패(미디어 URL 추정) — 대체 URL 로 1회 재시도: %s", exc)
        result = _attempt(fallback_media_url)
        result["media_url_used"] = fallback_media_url
        result["media_url_fallback_used"] = True
        return result
