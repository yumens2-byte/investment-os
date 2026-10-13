"""Mirror free Telegram alerts to Facebook; isolate failures and ambiguous writes.

Dedicated fb_alert_history.json avoids the one-post-per-day brief history.
Confirmed posts use the alert cooldown; ambiguous writes remain held for manual
verification. Persist an unknown reservation before HTTP to survive runner loss.
"""
import json
import logging
from datetime import datetime, timezone

from config import settings
from core import fb_history
from core.alert_history import _decide_send
from publishers.facebook_publisher import publish_page_post
from publishers.fb_formatter import to_facebook_text

logger = logging.getLogger(__name__)


def publish_alert(text, alert_type, level, image_path=None, *, now=None, path=None):
    """Facebook result dict; never propagate failures into Telegram/X delivery."""
    if not settings.channel_allows("face"):
        return {"status": "skipped", "reason": "channel_disabled"}
    if not settings.FACE_ENABLED and settings.PUBLISH_CHANNEL != "face":
        return {"status": "skipped", "reason": "face_disabled"}
    if "alert" not in settings.FACE_SESSIONS:
        return {"status": "skipped", "reason": "session_disabled"}

    current = now or datetime.now(timezone.utc)
    history_path = path or (settings.PUBLISHED_DIR / "fb_alert_history.json")
    session = f"alert:{alert_type}"
    fb_text = to_facebook_text(text)
    try:
        if settings.DRY_RUN:
            return publish_page_post(fb_text, image_path=image_path, session="alert")
        # Fail closed for corrupt/unreadable state: an earlier POST may exist.
        records = []
        if history_path.exists():
            records = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
                raise ValueError("invalid Facebook alert history")
        previous = next((r for r in reversed(records) if r.get("session") == session), None)
        if previous:
            if previous.get("status") == "unknown":
                return {"status": "held", "reason": "previous_post_unknown"}
            if previous.get("status") == "ok":
                datetime.fromisoformat(previous["timestamp"])
                send, reason = _decide_send(
                    previous.get("level", level), previous["timestamp"], level, now=current
                )
                if not send:
                    return {"status": "skipped", "reason": reason}

        def record(status, result=None):
            result = result or {}
            # Store level for the same upgrade/downgrade policy as Telegram alerts.
            rec = {
                "timestamp": current.isoformat(), "session": session,
                "publication_date": fb_history.publication_date(current),
                "status": status, "level": level,
                "content_hash": fb_history.content_hash(fb_text),
                "post_id": result.get("post_id", ""), "kind": result.get("kind", ""),
            }
            records.append(rec)
            fb_history._save(records, history_path)

        record("unknown")  # write-ahead reservation, never blindly retry a POST
        result = publish_page_post(fb_text, image_path=image_path, session="alert")
        status = result.get("status", "unknown")
        if status in ("ok", "failed"):
            record(status, result)
        logger.info("[AlertFacebook] %s/%s status=%s post_id=%s",
                    alert_type, level, status, result.get("post_id", ""))
        return result
    except Exception as exc:
        # Log only type; exception text may contain credentials from dependencies.
        logger.warning("[AlertFacebook] %s/%s held: %s", alert_type, level, type(exc).__name__)
        return {"status": "held", "reason": "delivery_or_history_error"}
