"""Consume one X-confirmed stored novel per KST Sunday; never generate content.

Runs as the Facebook job of comic_novel.yml, in parallel with the X job.

The durable store is the authority for weekly/source uniqueness. A sending row
must survive process death and is never released merely because it is old.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

KST = timezone(timedelta(hours=9))
FIRST_PUBLICATION_DATE = date(2026, 11, 1)
# Operational safety limit, not a claim about Meta's maximum message length.
DEFAULT_MAX_CHARS = 10000
FOOTER = (
    "#EDT #투자코믹스 #미국증시 #ETF투자 #투자소설\n"
    "#주식전쟁 #금융스릴러 #InvestmentComic\n\n"
    "⚠️ 본 콘텐츠는 투자 참고 정보이며, 투자 권유가 아닙니다."
)
logger = logging.getLogger(__name__)


def source_content_hash(source: dict) -> str:
    text = "\n".join(str(source.get(key) or "") for key in ("title", "episode_range", "novel_text"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_facebook_novel(source: dict) -> str:
    """Keep the stored body byte-for-byte; do not use X/TG rendered content."""
    title = source.get("title")
    body = source.get("novel_text")
    episode_range = source.get("episode_range")
    if not all(isinstance(value, str) and value.strip() for value in (title, body, episode_range)):
        raise ValueError("invalid_content")
    return f"{title}\n『{episode_range}』\n\n{body}\n\n{FOOTER}"


def verify_x_publication(source: dict, evidence: dict | None) -> bool:
    """Legacy user-confirmed published snapshot or full real IDs; unchanged source required."""
    if not evidence or evidence.get("status") != "ok":
        return False
    if evidence.get("verification_basis") == "legacy_published_status":
        return (source.get("status") == "published"
                and str(evidence.get("source_date")) == str(source.get("publish_date"))
                and bool(evidence.get("confirmed_by")) and bool(evidence.get("confirmed_at"))
                and evidence.get("source_content_hash") == source_content_hash(source))
    ids = evidence.get("post_ids")
    expected = evidence.get("expected_count")
    confirmed = evidence.get("confirmed_count")
    if type(expected) is not int or expected <= 0 or type(confirmed) is not int:
        return False
    if not isinstance(ids, list) or len(ids) != expected or confirmed != expected:
        return False
    if len(set(str(value) for value in ids)) != expected:
        return False
    if not all(isinstance(value, str) and value.isdigit() and int(value) > 0 for value in ids):
        return False
    return evidence.get("source_content_hash") == source_content_hash(source)


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name, "true" if default else "false").strip().lower()
    if value not in ("true", "false"):
        raise ValueError(f"invalid_{name.lower()}")
    return value == "true"


def _aware_now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if now.tzinfo is None:
        raise ValueError("naive_clock")
    return now.astimezone(KST)


def _default_notifier(result: dict) -> None:
    """Status only, existing TG config, one request, no content/DLQ/retry."""
    from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_FREE_CHANNEL_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_FREE_CHANNEL_ID:
        return
    import requests
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_FREE_CHANNEL_ID,
              "text": f"[Comic Novel FB] {result['status']} source={result.get('source_publish_date', '-')} run={result['run_id']}"},
        timeout=15,
    )


def _default_cover_loader(store, evidence: dict, directory: str) -> str | None:
    """Only download durable storage objects with an exact stored byte hash."""
    uri, expected_hash = evidence.get("cover_asset_uri"), evidence.get("cover_asset_hash")
    if not uri:
        return None
    if not isinstance(uri, str) or not uri.startswith("storage://") or not expected_hash:
        logger.warning("[Comic Novel FB] unsupported/unverified cover; using text")
        return None
    bucket, separator, path = uri[len("storage://"):].partition("/")
    if not separator or not bucket or not path:
        return None
    try:
        image_bytes = store.client.storage.from_(bucket).download(path)
        if hashlib.sha256(image_bytes).hexdigest() != expected_hash:
            logger.warning("[Comic Novel FB] cover hash mismatch; using text")
            return None
        image_path = Path(directory) / "cover.png"
        image_path.write_bytes(image_bytes)
        return str(image_path)
    except Exception as exc:
        logger.warning("[Comic Novel FB] cover unavailable (%s); using text", type(exc).__name__)
        return None


def run_facebook_novel(*, store=None, publisher=None, clock=None, dry_run=True,
                       enabled=None, target=None, first_date=None, max_chars=None,
                       run_id=None, notifier=None, cover_loader=None) -> dict:
    """One candidate, one Graph request. Dependency injection never bypasses gates."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    notifier = notifier or _default_notifier
    run_id = run_id or str(uuid.uuid4())
    result = {"status": "FAILED_CONFIG", "exit_code": 1, "run_id": run_id,
              "dry_run": dry_run}

    def finish(status: str, code: int, **extra) -> dict:
        result.update(status=status, exit_code=code, **extra)
        if not dry_run and (code != 0 or status == "PUBLISHED"):
            try:
                notifier(dict(result))
            except Exception as exc:
                result["notification_error_type"] = type(exc).__name__
        return result

    try:
        if type(dry_run) is not bool:
            raise ValueError("invalid_dry_run")
        enabled = _bool_env("FACE_NOVEL_ENABLED", False) if enabled is None else enabled
        if type(enabled) is not bool:
            raise ValueError("invalid_enabled")
        first_date = first_date or date.fromisoformat(os.getenv("FACE_NOVEL_FIRST_DATE", FIRST_PUBLICATION_DATE.isoformat()))
        max_chars = int(max_chars if max_chars is not None else os.getenv("FACE_NOVEL_MAX_CHARS", str(DEFAULT_MAX_CHARS)))
        if max_chars <= 0:
            raise ValueError("invalid_max_chars")
        now = _aware_now(clock)
        today = now.date()
        slot = today - timedelta(days=today.weekday())
        result.update(slot_week_start=slot.isoformat(), publication_date=today.isoformat())
        if not dry_run:
            if not enabled:
                return finish("SKIPPED_DISABLED", 0)
            if today < first_date:
                return finish("SKIPPED_BEFORE_FIRST_DATE", 0)
            if today.weekday() != 6:
                return finish("SKIPPED_NOT_SUNDAY", 0)
        target = target or os.getenv("FACE_PAGE_ID", "").strip()
        if not target:
            raise ValueError("missing_page_id")
        if store is None:
            from db.comic_novel_channel_store import ComicNovelChannelStore
            store = ComicNovelChannelStore()
    except Exception as exc:
        return finish("FAILED_CONFIG", 1, error_type=type(exc).__name__)

    try:
        week = store.get_week_state(target, slot.isoformat()) or {}
        if week.get("status") == "ok":
            return finish("SKIPPED_WEEK_DONE", 0)
        if week.get("status") in ("unknown", "sending", "claimed", "failed"):
            return finish("HOLD_UNRESOLVED", 2, claim_id=week.get("id"))
        source = store.select_oldest_pending(today.isoformat())
        if source is None:
            return finish("SKIPPED_NO_DATA", 0)
        source_date = str(source["publish_date"])
        result["source_publish_date"] = source_date
        evidence = store.get_x_evidence(source_date)
    except Exception as exc:
        return finish("FAILED_DB_READ", 1, error_type=type(exc).__name__)

    if not verify_x_publication(source, evidence):
        return finish("HOLD_X_UNCONFIRMED", 2)
    try:
        text = format_facebook_novel(source)
    except ValueError:
        return finish("HOLD_INVALID_CONTENT", 2)
    if len(text) > max_chars:
        return finish("HOLD_CONTENT_LENGTH", 2, content_length=len(text), max_chars=max_chars)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    result.update(content_hash=content_hash, source_content_hash=source_content_hash(source),
                  content_length=len(text))
    if dry_run:
        return finish("PREVIEW", 0, preview=text, cover_available=bool(evidence.get("cover_asset_uri")))
    # The shared publisher's own DRY_RUN gate must agree before any durable write.
    if publisher is None:
        from publishers import facebook_publisher
        if facebook_publisher.DRY_RUN:
            return finish("FAILED_CONFIG", 1, error_type="PublisherDryRunMismatch")
        if not facebook_publisher.FACE_PAGE_ID or not facebook_publisher.FACE_PAGE_TOKEN:
            return finish("FAILED_CONFIG", 1, error_type="MissingPageCredentials")
        if str(facebook_publisher.FACE_PAGE_ID) != str(target):
            return finish("FAILED_CONFIG", 1, error_type="PublisherTargetMismatch")
        publisher = facebook_publisher.publish_page_post
    try:
        claim = store.claim_slot(source_date, target, slot.isoformat(), run_id, content_hash, result["source_content_hash"])
        if not claim or claim.get("status") != "claimed" or not claim.get("id"):
            return finish("SKIPPED_CLAIM_BLOCKED", 0)
        claim_id = claim["id"]
        result["claim_id"] = claim_id
    except Exception as exc:
        return finish("FAILED_DB_CLAIM", 1, error_type=type(exc).__name__)

    with tempfile.TemporaryDirectory(prefix="comic-novel-fb-") as directory:
        try:
            image_path = (cover_loader or _default_cover_loader)(store, evidence, directory)
            # Claim RPC rechecks the source/evidence hash, mark_sending fixes payload hash.
            store.mark_sending(claim_id, content_hash)
        except Exception as exc:
            return finish("HOLD_DB_SENDING", 2, error_type=type(exc).__name__)
        try:
            published = publisher(text, image_path=image_path, session="comic_novel")
        except Exception as exc:
            # A custom publisher may raise after having written. Never guess "failed".
            published = {"status": "unknown", "reason": type(exc).__name__}
        status = published.get("status") if isinstance(published, dict) else "unknown"
        post_id = str(published.get("post_id") or "") if isinstance(published, dict) else ""
        if status == "ok" and not re.fullmatch(r"[1-9][0-9]*(?:_[1-9][0-9]*)?", post_id):
            status = "unknown"
        if status == "ok":
            try:
                confirmed_at = _aware_now(clock).isoformat()
                store.finalize_publication(claim_id, post_id, confirmed_at)
            except Exception as exc:
                return finish("HOLD_DB_COMMIT", 2, post_id=post_id, error_type=type(exc).__name__)
            return finish("PUBLISHED", 0, post_id=post_id, confirmed_at=confirmed_at)
        durable_status = "failed" if status == "failed" else "unknown"
        try:
            store.mark_result(claim_id, durable_status, "PublisherFailed" if durable_status == "failed" else "PublisherUnknown")
        except Exception as exc:
            return finish("HOLD_DB_RESULT", 2, error_type=type(exc).__name__)
        return finish("FAILED_PUBLISH" if durable_status == "failed" else "HOLD_UNKNOWN",
                      1 if durable_status == "failed" else 2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("preview", "publish", "reconcile"), default="preview")
    parser.add_argument("--output", default="output/comic-novel-fb-report.json")
    parser.add_argument("--claim-id", type=int)
    parser.add_argument("--post-id")
    parser.add_argument("--not-posted", action="store_true", help="Operator verified absence; release without any Graph request")
    parser.add_argument("--confirmed-at")
    parser.add_argument("--actor")
    parser.add_argument("--note")
    args = parser.parse_args(argv)
    if args.stage == "reconcile":
        # Operator-verified existing/absent posts only; never a Graph request.
        try:
            if not all((args.claim_id, args.actor, args.note)):
                raise ValueError("reconcile_evidence_required")
            if not args.actor.strip() or not args.note.strip():
                raise ValueError("reconcile_evidence_required")
            from db.comic_novel_channel_store import ComicNovelChannelStore
            if args.not_posted:
                if args.post_id or args.confirmed_at:
                    raise ValueError("conflicting_reconcile_evidence")
                state = ComicNovelChannelStore().release_publication(args.claim_id, args.actor, args.note)
                if not isinstance(state, dict) or state.get("status") != "failed":
                    raise RuntimeError("release_not_confirmed")
                result = {"status": "RECONCILED_NOT_POSTED", "exit_code": 0}
            else:
                if not all((args.post_id, args.confirmed_at)):
                    raise ValueError("reconcile_post_evidence_required")
                stamp = datetime.fromisoformat(args.confirmed_at)
                if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                    raise ValueError("invalid_confirmed_at")
                if not re.fullmatch(r"[1-9][0-9]*(?:_[1-9][0-9]*)?", args.post_id):
                    raise ValueError("invalid_post_id")
                state = ComicNovelChannelStore().reconcile_publication(args.claim_id, args.post_id, stamp.isoformat(), args.actor, args.note)
                if not isinstance(state, dict) or state.get("status") != "ok":
                    raise RuntimeError("reconcile_not_confirmed")
                result = {"status": "RECONCILED", "exit_code": 0}
        except Exception as exc:
            result = {"status": "FAILED_RECONCILE", "exit_code": 1, "error_type": type(exc).__name__}
    else:
        try:
            dry_run = args.stage == "preview" or _bool_env("DRY_RUN", True)
            result = run_facebook_novel(dry_run=dry_run)
        except ValueError as exc:
            result = {"status": "FAILED_CONFIG", "exit_code": 1, "error_type": type(exc).__name__}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
