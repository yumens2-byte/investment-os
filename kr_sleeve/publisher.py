"""
kr_sleeve/publisher.py — 채널별 1회 시도 발행 (DN2026_0108)

  - 쓰기 API는 자동 재시도하지 않는다. 응답이 확정되지 않으면 unknown(STD-PUB-03).
    기존 publishers.x_publisher.publish_tweet 은 실패 시 재시도하므로 쓰지 않고,
    같은 env 이름(config.settings)으로
    tweepy Client를 만들어 create_tweet 1회만 호출한다.
  - Telegram·Facebook은 기존 공개 함수(send_message, publish_page_post)를 쓴다. 둘 다 1회 시도다.
  - 이중 잠금: 이 모듈은 KR_SLEEVE_MODE=live일 때만 불리고, 여기서 기존 DRY_RUN(config.settings.is_dry_run)도
    다시 확인한다. 기존 DRY_RUN=true면 어떤 채널도 외부 호출하지 않는다.
반환: {"status": ok|failed|unknown|dry_run|disabled, "post_id": str|None, "reason": str|None}
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _r(status: str, post_id: str | None = None, reason: str | None = None) -> dict:
    return {"status": status, "post_id": post_id, "reason": reason}


def _global_dry_run() -> bool:
    from config.settings import is_dry_run
    return is_dry_run()


# ── X ───────────────────────────────────────────────────────
def publish_x(text: str, client_factory=None) -> dict:
    if _global_dry_run():
        return _r("dry_run", reason="DRY_RUN=true")
    try:
        import tweepy
    except ImportError:
        return _r("failed", reason="tweepy_missing")
    from config import settings

    if client_factory is None:
        if not all([settings.X_API_KEY, settings.X_API_SECRET,
                    settings.X_ACCESS_TOKEN, settings.X_ACCESS_TOKEN_SECRET]):
            return _r("failed", reason="not_configured")

        def client_factory():
            return tweepy.Client(
                consumer_key=settings.X_API_KEY,
                consumer_secret=settings.X_API_SECRET,
                access_token=settings.X_ACCESS_TOKEN,
                access_token_secret=settings.X_ACCESS_TOKEN_SECRET,
            )

    try:
        client = client_factory()
    except Exception as e:  # noqa: BLE001 — 클라이언트 생성 실패는 미전송 확정
        return _r("failed", reason=f"client_error:{type(e).__name__}")
    try:
        resp = client.create_tweet(text=text)
    except tweepy.errors.HTTPException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code is not None and code < 500:
            return _r("failed", reason=f"http_{code}")      # 서버가 거절 → 게시 안 됨
        return _r("unknown", reason=f"http_{code}")          # 5xx → 게시됐을 수 있음
    except Exception as e:  # noqa: BLE001 — 전송 후 응답 미확정(타임아웃·연결 끊김 등)
        return _r("unknown", reason=f"request_error:{type(e).__name__}")
    try:
        tweet_id = str(resp.data["id"])
    except (AttributeError, KeyError, TypeError):
        return _r("unknown", reason="no_tweet_id")
    return _r("ok", post_id=tweet_id)


# ── Telegram ────────────────────────────────────────────────
def _tg_result(results: list) -> dict:
    if not results:
        return _r("failed", reason="not_configured")
    if all(r.get("dry_run") for r in results):
        return _r("dry_run", reason="DRY_RUN=true")
    if all(r.get("ok") for r in results):
        ids = [str((r.get("result") or {}).get("message_id", "")) for r in results]
        return _r("ok", post_id=",".join(i for i in ids if i) or None)
    if any("error" in r for r in results):       # 예외(전송 여부 미확정)
        return _r("unknown", reason="request_exception")
    return _r("failed", reason="api_not_ok")


def publish_telegram(html: str, channel: str = "free", sender=None) -> dict:
    if _global_dry_run():
        return _r("dry_run", reason="DRY_RUN=true")
    if sender is None:
        from publishers.telegram_publisher import send_message as sender
    return _tg_result(sender(html, channel=channel, parse_mode="HTML"))


# ── Facebook ────────────────────────────────────────────────
def publish_facebook(text: str, poster=None) -> dict:
    if _global_dry_run():
        return _r("dry_run", reason="DRY_RUN=true")
    from config.settings import FACE_ENABLED
    if not FACE_ENABLED:
        return _r("disabled", reason="FACE_ENABLED=false")
    if poster is None:
        from publishers.facebook_publisher import publish_page_post as poster
    res = poster(text, session="kr_sleeve")
    status = res.get("status")
    if status not in ("ok", "failed", "unknown", "dry_run"):
        status = "unknown"
    return _r(status, post_id=res.get("post_id") or None, reason=res.get("reason"))


# ── 운영자 방 (비공개) ───────────────────────────────────────
def notify_operator(html: str, sender=None) -> dict:
    """운영자 전용 방(TELEGRAM_PAID_CHANNEL_ID, 10-21 운영자 방 전환)으로만 보낸다(STD-OBS-06)."""
    try:
        return publish_telegram(html, channel="paid", sender=sender)
    except Exception as e:  # noqa: BLE001 — 알림 실패가 파이프라인 결과를 바꾸지 않게 한다
        logger.error("[KRSleeve] 운영자 알림 실패: %s", type(e).__name__)
        return _r("failed", reason=f"notify_error:{type(e).__name__}")
