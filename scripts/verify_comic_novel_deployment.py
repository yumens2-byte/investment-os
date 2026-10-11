"""Read-only production connection check; never posts or changes database rows."""
import json
import os
from urllib.parse import urlparse

import requests


def main():
    report = {"status": "FAILED"}
    try:
        if urlparse(os.environ["SUPABASE_URL"]).hostname != "ccomoimhhttaklfadaos.supabase.co":
            raise ValueError("unexpected_database_project")
        from db.comic_novel_channel_store import ComicNovelChannelStore
        from config.settings import FACE_GRAPH_BASE, FACE_PAGE_ID, FACE_PAGE_TOKEN
        from weekend.comic_novel_fb import run_facebook_novel

        store = ComicNovelChannelStore()
        store.client.table("comic_novel_channel_history").select("id").limit(1).execute()
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
        preview = run_facebook_novel(dry_run=True, enabled=True, store=store)
        if preview["status"] not in {"PREVIEW", "SKIPPED_NO_DATA", "SKIPPED_WEEK_DONE", "HOLD_X_UNCONFIRMED"}:
            raise ValueError("preview_blocked")
        report.update(status="VERIFIED", database_project="ccomoimhhttaklfadaos",
                      page_identity_verified=True, preview_status=preview["status"],
                      source_publish_date=preview.get("source_publish_date"),
                      database_writes=0, public_posts=0)
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
