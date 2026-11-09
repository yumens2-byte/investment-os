"""
kr_sleeve/store.py — KR 슬리브 Supabase 기록 (DN2026_0108)

테이블(db/kr_sleeve_schema.sql): kr_sleeve_daily, kr_sleeve_publish.
호출 패턴: get_client().table(...).select/insert/upsert/update(...).execute() (STD-DB-01).
기록은 shadow·live 모드에서만 한다. dry_run에서는 이 모듈을 부르지 않는다.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

DAILY = "kr_sleeve_daily"
PUBLISH = "kr_sleeve_publish"
DONE_STATUSES = ("computed", "published")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SleeveStore:
    def __init__(self, client=None):
        if client is None:
            from db.supabase_client import get_client
            client = get_client()
        self.client = client

    # ── 일별 결과 ───────────────────────────────────────────
    def get_daily(self, run_date: str) -> dict | None:
        res = (self.client.table(DAILY).select("run_date,status,reason")
               .eq("run_date", run_date).limit(1).execute())
        return res.data[0] if res.data else None

    def already_done(self, run_date: str) -> bool:
        row = self.get_daily(run_date)
        return bool(row and row.get("status") in DONE_STATUSES)

    def upsert_daily(self, row: dict) -> None:
        """published 행은 덮어쓰지 않는다(R4 W2: 동시 실행이 발행 기록을 지우는 것 방지)."""
        current = self.get_daily(row["run_date"])
        if current and current.get("status") == "published":
            logger.warning("[KRSleeve] %s 이미 published — daily 덮어쓰기 생략", row["run_date"])
            return
        payload = {**row, "updated_at": _now_iso()}
        self.client.table(DAILY).upsert(payload, on_conflict="run_date").execute()

    def set_daily_status(self, run_date: str, status: str, reason: str | None = None) -> None:
        (self.client.table(DAILY).update({"status": status, "reason": reason, "updated_at": _now_iso()})
         .eq("run_date", run_date).execute())

    # ── 채널별 발행 (STD-PUB-01: pending 선기록 → 발행 → 결과) ──
    def claim_publish(self, run_date: str, channel: str) -> bool:
        """pending 행을 만든다. 이미 행이 있으면(어떤 상태든) False — 자동 재발행하지 않는다(STD-PUB-03)."""
        existing = (self.client.table(PUBLISH).select("status")
                    .eq("run_date", run_date).eq("channel", channel).limit(1).execute())
        if existing.data:
            logger.info("[KRSleeve] %s %s 기존 발행 행 status=%s → 건너뜀",
                        run_date, channel, existing.data[0].get("status"))
            return False
        self.client.table(PUBLISH).insert({
            "run_date": run_date, "channel": channel, "status": "pending", "updated_at": _now_iso(),
        }).execute()
        return True

    def finish_publish(self, run_date: str, channel: str, status: str,
                       post_id: str | None = None, reason: str | None = None) -> None:
        (self.client.table(PUBLISH)
         .update({"status": status, "post_id": post_id, "reason": reason, "updated_at": _now_iso()})
         .eq("run_date", run_date).eq("channel", channel).execute())
