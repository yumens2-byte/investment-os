"""No-network regression tests for X-dependent Facebook source eligibility."""
import sys
import types
from unittest.mock import Mock

import pytest

from weekend import comic_novel
from db import daily_store


@pytest.fixture
def pipeline(monkeypatch):
    cached = {"status": "generated", "novel_text": "original story", "title": "title",
              "episode_range": "EP.1~EP.2", "x_thread": ["header", "body"], "tg_html": "body"}
    get = Mock(return_value=cached)
    save = Mock(return_value=True)
    update = Mock(return_value=True)
    claim = Mock(return_value=True)
    monkeypatch.setattr(daily_store, "claim_novel_x", claim)
    monkeypatch.setattr(daily_store, "get_novel", get)
    monkeypatch.setattr(daily_store, "save_novel", save)
    monkeypatch.setattr(daily_store, "update_novel_status", update)
    evidence = types.SimpleNamespace(get_x_evidence=Mock(return_value=None), record_x_publication=Mock(return_value=True))
    monkeypatch.setitem(sys.modules, "db.comic_novel_channel_store", types.SimpleNamespace(ComicNovelChannelStore=lambda: evidence))
    thread = Mock(return_value={"success": True, "dry_run": False, "tweet_ids": ["123", "124"], "published_count": 2})
    image = Mock(return_value={"success": True, "dry_run": False, "tweet_id": "123"})
    monkeypatch.setitem(sys.modules, "publishers.x_publisher", types.SimpleNamespace(publish_thread=thread, publish_tweet_with_image=image))
    tg = Mock()
    monkeypatch.setitem(sys.modules, "publishers.telegram_publisher", types.SimpleNamespace(send_message=tg, send_photo=Mock()))
    vote = Mock()
    monkeypatch.setitem(sys.modules, "engines.character_vote", types.SimpleNamespace(publish_vote=vote, get_latest_winner=lambda: None))
    cover = Mock(return_value=None)
    monkeypatch.setattr(comic_novel, "_generate_cover_image", cover)
    return types.SimpleNamespace(cached=cached, get=get, save=save, update=update, evidence=evidence,
                                 thread=thread, image=image, tg=tg, vote=vote, cover=cover, claim=claim)


def test_confirmed_full_thread_records_evidence_then_published(pipeline):
    result = comic_novel.publish_novel_episode(dry_run=False)
    assert result["success"] and result["tweet_ids"] == ["123", "124"]
    assert [call.args[1] for call in pipeline.update.call_args_list] == ["published"]
    pipeline.claim.assert_called_once()
    assert pipeline.evidence.record_x_publication.call_args.kwargs["cover_asset_uri"] is None
    assert pipeline.evidence.record_x_publication.call_args.kwargs["novel_text"] == "original story"


@pytest.mark.parametrize("reply", [
    {"success": False, "tweet_ids": ["123"], "dry_run": False},
    {"success": True, "tweet_ids": ["DRY_RUN", "DRY_RUN"], "dry_run": True},
    {"success": True, "tweet_ids": ["123"], "dry_run": False},
    {"success": True, "tweet_ids": ["123", "123"], "dry_run": False},
])
def test_partial_or_simulated_thread_never_marks_published(pipeline, reply):
    pipeline.thread.return_value = reply
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_X_RECONCILE"
    pipeline.evidence.record_x_publication.assert_not_called()
    pipeline.tg.assert_not_called()
    pipeline.vote.assert_not_called()
    assert "published" not in [call.args[1] for call in pipeline.update.call_args_list]


def test_cover_success_with_reply_failure_is_partial(pipeline):
    pipeline.cover.return_value = "/tmp/cover.png"
    pipeline.thread.return_value = {"success": False, "tweet_ids": [], "dry_run": False}
    result = comic_novel.publish_novel_episode(dry_run=False)
    assert result["status"] == "HOLD_X_RECONCILE" and result["tweet_ids"] == ["123"]
    assert pipeline.update.call_args.args[1] == "x_partial"
    pipeline.evidence.record_x_publication.assert_not_called()


def test_evidence_failure_after_real_x_is_db_hold(pipeline):
    pipeline.evidence.record_x_publication.return_value = False
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_DB_CONFIRM"
    pipeline.update.assert_not_called()
    pipeline.tg.assert_not_called()


@pytest.mark.parametrize("status, outcome", [("published", "SKIPPED_ALREADY_PUBLISHED"), ("x_publishing", "HOLD_X_RECONCILE"), ("x_partial", "HOLD_X_RECONCILE"), ("x_hold", "HOLD_X_RECONCILE")])
def test_cached_publication_guard_prevents_reposting(pipeline, status, outcome):
    pipeline.cached["status"] = status
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == outcome
    pipeline.thread.assert_not_called()
    pipeline.cover.assert_not_called()
    pipeline.save.assert_not_called()


def test_cached_dry_run_has_no_effects(pipeline):
    assert comic_novel.publish_novel_episode(dry_run=True)["status"] == "DRY_RUN"
    for mock in (pipeline.save, pipeline.update, pipeline.claim, pipeline.evidence.record_x_publication, pipeline.thread, pipeline.cover, pipeline.tg, pipeline.vote):
        mock.assert_not_called()


def test_cache_read_failure_stops_generation(pipeline, monkeypatch):
    pipeline.get.side_effect = RuntimeError("db down")
    generation = Mock()
    monkeypatch.setattr(comic_novel, "novelify_episodes", generation)
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_DB_READ"
    generation.assert_not_called()
    pipeline.thread.assert_not_called()


def prepare_miss(pipeline, monkeypatch):
    pipeline.get.return_value = None
    monkeypatch.setattr(comic_novel, "get_episodes_from_notion", lambda: [{"episode_no": 1, "title": "title", "page_id": "page"}])
    monkeypatch.setattr(comic_novel, "novelify_episodes", lambda *a, **k: {**pipeline.cached, "success": True})
    ctx = types.SimpleNamespace(table=lambda *a: ctx, select=lambda *a: ctx, in_=lambda *a: ctx,
                                execute=lambda: types.SimpleNamespace(data=[]))
    monkeypatch.setitem(sys.modules, "db.supabase_client", types.SimpleNamespace(get_client=lambda: ctx))


def test_cache_save_failure_stops_x(pipeline, monkeypatch):
    prepare_miss(pipeline, monkeypatch)
    pipeline.save.return_value = False
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_DB_SAVE"
    pipeline.thread.assert_not_called()
    pipeline.cover.assert_not_called()


def test_miss_dry_run_does_not_store_generated_novel(pipeline, monkeypatch):
    prepare_miss(pipeline, monkeypatch)
    assert comic_novel.publish_novel_episode(dry_run=True)["status"] == "DRY_RUN"
    pipeline.save.assert_not_called()
    pipeline.update.assert_not_called()
    pipeline.thread.assert_not_called()


def test_status_write_failure_after_evidence_returns_db_hold(pipeline):
    pipeline.update.return_value = False
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_DB_CONFIRM"
    pipeline.evidence.record_x_publication.assert_called_once()
    pipeline.vote.assert_not_called()


def test_existing_evidence_never_reposts_generated_cache(pipeline):
    pipeline.evidence.get_x_evidence.return_value = {"x_post_ids": ["123"]}
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_DB_CONFIRM"
    pipeline.thread.assert_not_called()


def test_daily_store_strict_read_propagates_db_failure(monkeypatch):
    monkeypatch.setattr(daily_store, "_get_client", Mock(side_effect=RuntimeError("unavailable")))
    with pytest.raises(RuntimeError, match="unavailable"):
        daily_store.get_novel("2026-10-12", strict=True)
    assert daily_store.get_novel("2026-10-12") is None


def test_daily_store_empty_mutation_is_not_success(monkeypatch):
    query = types.SimpleNamespace(insert=lambda *a, **k: query, update=lambda *a, **k: query,
                                  eq=lambda *a, **k: query, execute=lambda: types.SimpleNamespace(data=[]))
    client = types.SimpleNamespace(table=lambda *a: query)
    monkeypatch.setattr(daily_store, "_get_client", lambda: client)
    assert not daily_store.update_novel_status("2026-10-12", "published")
    assert not daily_store.save_novel("2026-10-12", "2026-W42", "EP.1", "title", "story", ["story"], "story")


def test_lost_atomic_claim_prevents_any_x_side_effect(pipeline):
    pipeline.claim.return_value = False
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_DB_SAVE"
    pipeline.cover.assert_not_called()
    pipeline.thread.assert_not_called()
    pipeline.image.assert_not_called()
    pipeline.evidence.record_x_publication.assert_not_called()


def test_claim_is_single_atomic_conditional_transition(monkeypatch):
    """Two runners read generated; only one UPDATE still matches generated."""
    persisted = {"status": "generated"}
    filters = []
    transitions = []
    class Query:
        def update(self, values):
            self.values = values
            return self
        def eq(self, key, value):
            self.conditions = getattr(self, "conditions", []) + [(key, value)]
            return self
        def execute(self):
            filters.append(self.conditions)
            if persisted["status"] != dict(self.conditions)["status"]:
                return types.SimpleNamespace(data=[])
            persisted.update(self.values)
            transitions.append(self.values)
            return types.SimpleNamespace(data=[{"publish_date": "2026-10-12", **persisted}])
    monkeypatch.setattr(daily_store, "_get_client", lambda: types.SimpleNamespace(table=lambda *a: Query()))
    assert daily_store.claim_novel_x("2026-10-12") is True
    assert daily_store.claim_novel_x("2026-10-12") is False
    assert filters == [[("publish_date", "2026-10-12"), ("status", "generated")]] * 2
    assert transitions == [{"status": "x_publishing"}]


def test_reported_thread_count_mismatch_is_not_confirmed(pipeline):
    pipeline.thread.return_value = {"success": True, "dry_run": False, "tweet_ids": ["123", "124"], "published_count": 1}
    assert comic_novel.publish_novel_episode(dry_run=False)["status"] == "HOLD_X_RECONCILE"
    pipeline.evidence.record_x_publication.assert_not_called()


def test_cover_and_complete_reply_ids_are_saved_in_order(pipeline):
    pipeline.cover.return_value = "/tmp/cover.png"
    pipeline.thread.return_value = {"success": True, "dry_run": False, "tweet_ids": ["124"], "published_count": 1}
    result = comic_novel.publish_novel_episode(dry_run=False)
    assert result["success"] is True
    assert result["tweet_ids"] == ["123", "124"]
    pipeline.thread.assert_called_once_with(["body"], reply_to="123")
    assert pipeline.evidence.record_x_publication.call_args.kwargs["post_ids"] == ["123", "124"]


def test_cache_miss_insert_conflict_cannot_reset_claim_or_immutable_content(monkeypatch):
    """A late runner's cache MISS must never overwrite the winner's source row."""
    existing = {"publish_date": "2026-10-12", "status": "x_publishing", "novel_text": "winner original",
                "publish_fb_data": "2026-10-16", "x_content_hash": "verified-hash"}
    original = dict(existing)
    class Query:
        def insert(self, row):
            self.row = row
            return self
        def execute(self):
            if self.row["publish_date"] == existing["publish_date"]:
                raise RuntimeError("23505 duplicate key unique publish_date")
            existing.update(self.row)
            return types.SimpleNamespace(data=[dict(existing)])
    monkeypatch.setattr(daily_store, "_get_client", lambda: types.SimpleNamespace(table=lambda *a: Query()))
    assert not daily_store.save_novel("2026-10-12", "2026-W42", "EP.1", "new title", "loser changed", ["post"], "body")
    assert existing == original
