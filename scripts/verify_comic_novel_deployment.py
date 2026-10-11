"""Read-only production connection check; never posts or changes database rows."""
import json
import os
import base64
from urllib.parse import urlparse

import requests


def main():
    report = {"status": "FAILED"}
    try:
        report["check"] = "database_project"
        if urlparse(os.environ["SUPABASE_URL"]).hostname != "ccomoimhhttaklfadaos.supabase.co":
            raise ValueError("unexpected_database_project")
        key = os.environ["SUPABASE_KEY"]
        try:
            part = key.split(".")[1]
            role = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("role")
            report["database_key_role"] = role if role in {"anon", "authenticated", "service_role"} else "other"
        except (ValueError, IndexError, KeyError):
            report["database_key_role"] = "non_jwt"
        from db.comic_novel_channel_store import ComicNovelChannelStore
        from config.settings import FACE_GRAPH_BASE, FACE_PAGE_ID, FACE_PAGE_TOKEN
        from weekend.comic_novel_fb import run_facebook_novel

        store = ComicNovelChannelStore()
        report["check"] = "database_history_read"
        store.verify_history_access()
        report["check"] = "page_identity"
        if not FACE_PAGE_ID or not FACE_PAGE_TOKEN:
            raise ValueError("missing_page_credentials")
        response = requests.get(
            f"{FACE_GRAPH_BASE}/me",
            params={"fields": "id"},
            headers={"Authorization": f"Bearer {FACE_PAGE_TOKEN}"},
            timeout=20,
        )
        payload = response.json()
        if response.status_code != 200 or str(payload.get("id", "")) != FACE_PAGE_ID:
            raise ValueError("page_identity_not_verified")
        report["check"] = "source_preview"
        preview = run_facebook_novel(dry_run=True, enabled=True, store=store)
        if preview["status"] not in {"PREVIEW", "SKIPPED_NO_DATA", "SKIPPED_WEEK_DONE", "HOLD_X_UNCONFIRMED"}:
            raise ValueError("preview_blocked")
        report.update(status="VERIFIED", database_project="ccomoimhhttaklfadaos",
                      page_identity_verified=True, preview_status=preview["status"],
                      source_publish_date=preview.get("source_publish_date"),
                      database_writes=0, public_posts=0)
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        code = getattr(exc, "code", "")
        if isinstance(code, str) and code.isalnum() and len(code) <= 16:
            report["error_code"] = code
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
