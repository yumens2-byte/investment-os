"""Durable novel channel state. RPC writes fail closed; no public posting here."""
from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timedelta, timezone


def source_content_hash(title: str, episode_range: str, novel_text: str) -> str:
    return hashlib.sha256(f"{title}\n{episode_range}\n{novel_text}".encode()).hexdigest()


class ComicNovelChannelStore:
    def __init__(self, client=None):
        self.gateway = None
        if client is None and os.getenv("NOVEL_USE_GITHUB_OIDC", "false").lower() == "true":
            from db.comic_novel_gateway import ComicNovelGateway
            self.gateway = ComicNovelGateway()
            self.client = None
            return
        if client is None:
            from db.supabase_client import get_client
            client = get_client()
        self.client = client

    def _rpc(self, name, params):
        result = (self.gateway.call(name, params) if self.gateway is not None
                  else self.client.rpc(name, params).execute().data)
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
        if not isinstance(result, dict):
            raise RuntimeError(f"{name}: invalid RPC response")
        return result

    def select_oldest_pending(self, today_kst):
        if self.gateway is not None:
            return self.gateway.call("select_oldest_pending", {"today_kst": str(today_kst)})
        rows = (self.client.table("comic_novels")
                .select("id,publish_date,title,episode_range,novel_text,status,publish_fb_data")
                .is_("publish_fb_data", "null").eq("status", "published")
                .lte("publish_date", str(today_kst)).order("publish_date").order("id")
                .limit(1).execute().data)
        return rows[0] if rows else None

    def get_x_evidence(self, source_date):
        if self.gateway is not None:
            return self.gateway.call("get_x_evidence", {"source_date": str(source_date)})
        rows = (self.client.table("comic_novel_channel_history").select("*")
                .eq("channel", "x").eq("source_date", str(source_date))
                .eq("status", "ok").limit(1).execute().data)
        if rows:
            return rows[0]
        legacy = (self.client.table("comic_novel_legacy_x_confirmations").select("*")
                  .eq("source_date", str(source_date)).limit(1).execute().data)
        return dict(legacy[0], status="ok", verification_basis="legacy_published_status") if legacy else {}

    def get_week_state(self, target, slot):
        if self.gateway is not None:
            return self.gateway.call("get_week_state", {"target": target, "slot": str(slot)})
        # Any unresolved send blocks all later slots; runner restarts are not leases.
        q = (self.client.table("comic_novel_channel_history").select("*")
             .eq("channel", "facebook").eq("target_key", target))
        rows = q.in_("status", ["sending", "unknown", "claimed"]).order("id").limit(1).execute().data
        if rows:
            return rows[0]
        start = date.fromisoformat(str(slot))
        end = start + timedelta(days=7)
        rows = (self.client.table("comic_novel_channel_history").select("*")
                .eq("channel", "facebook").eq("target_key", target)
                .eq("slot_week_start", str(slot)).eq("status", "ok").limit(1).execute().data)
        if rows:
            return rows[0]
        # Reconciliation may confirm a prior slot actually posted this week.
        rows = (self.client.table("comic_novels").select("publish_date,publish_fb_data")
                .gte("publish_fb_data", start.isoformat()).lt("publish_fb_data", end.isoformat())
                .limit(1).execute().data)
        return {"status": "ok", "reason": "actual_week_done"} if rows else {}

    def verify_history_access(self):
        if self.gateway is not None:
            return self.gateway.call("verify_history_access", {})
        self.client.table("comic_novel_channel_history").select("id").limit(1).execute()
        return True

    def claim_slot(self, source_date, target, slot, run_id, content_hash, source_hash):
        return self._rpc("novel_fb_claim", {"p_source_date": str(source_date), "p_target": target,
                         "p_slot": str(slot), "p_run_id": run_id, "p_content_hash": content_hash,
                         "p_source_hash": source_hash})

    def mark_sending(self, claim_id, content_hash):
        result = self._rpc("novel_fb_mark_sending", {"p_claim_id": claim_id, "p_content_hash": content_hash})
        if result.get("status") != "sending":
            raise RuntimeError("send not claimed")
        return result

    def finalize_publication(self, claim_id, post_id, confirmed_at):
        result = self._rpc("novel_fb_finalize", {"p_claim_id": claim_id, "p_post_id": post_id,
                           "p_confirmed_at": self._iso_time(confirmed_at)})
        if result.get("status") != "ok":
            raise RuntimeError("publication not committed")
        return result

    def mark_result(self, claim_id, status, error_type):
        if status not in ("failed", "unknown"):
            raise ValueError("invalid send result")
        return self._rpc("novel_fb_mark_result", {"p_claim_id": claim_id, "p_status": status,
                                                 "p_error_type": error_type})

    def reconcile_publication(self, claim_id, post_id, confirmed_at, actor, note):
        if not actor.strip() or not note.strip():
            raise ValueError("reconciliation evidence and actor required")
        result = self._rpc("novel_fb_finalize", {"p_claim_id": claim_id, "p_post_id": post_id,
                         "p_confirmed_at": self._iso_time(confirmed_at), "p_actor": actor, "p_note": note})
        if result.get("status") != "ok":
            raise RuntimeError("reconciliation not committed")
        return result

    def release_publication(self, claim_id, actor, note):
        if not actor.strip() or not note.strip():
            raise ValueError("not-posted evidence and actor required")
        result = self._rpc("novel_fb_release", {"p_claim_id": claim_id,
                           "p_actor": actor, "p_note": note})
        if result.get("status") != "failed":
            raise RuntimeError("publication release not committed")
        return result

    def record_x_publication(self, source_date, post_ids, expected_count, title,
                             episode_range, novel_text, cover_asset_uri=None):
        if (not isinstance(expected_count, int) or isinstance(expected_count, bool)
                or expected_count <= 0 or len(post_ids) != expected_count
                or len(set(post_ids)) != expected_count
                or any(not isinstance(v, str) or not v.isdigit() or int(v) <= 0 for v in post_ids)):
            raise ValueError("X publication is not fully confirmed")
        result = self._rpc("novel_x_confirm", {
            "p_source_date": str(source_date), "p_post_ids": post_ids,
            "p_expected_count": expected_count,
            "p_source_hash": source_content_hash(title, episode_range, novel_text),
            "p_cover_uri": cover_asset_uri,
        })
        if result.get("status") != "ok":
            raise RuntimeError("X evidence not committed")
        return True

    @staticmethod
    def _iso_time(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            raise ValueError("timezone-aware confirmation timestamp required")
        return parsed.astimezone(timezone.utc).isoformat()
