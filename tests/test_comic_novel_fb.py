"""Consumer safety tests: mock durable store and zero real Graph/DB writes."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from weekend.comic_novel_fb import (
    format_facebook_novel, run_facebook_novel, source_content_hash, verify_x_publication,
)

FRIDAY = datetime(2026, 10, 16, 13, 17, tzinfo=timezone.utc)
SOURCE = {"publish_date": "2026-04-05", "title": "힘의 의미", "episode_range": "EP01~04",
          "novel_text": "옛 시장의 이야기\n\nEDT: ‘말’\n***\n원문", "status": "published"}


@pytest.fixture
def store():
    store = Mock()
    store.get_week_state.return_value = {}
    store.select_oldest_pending.return_value = dict(SOURCE)
    store.get_x_evidence.return_value = {
        "status": "ok", "post_ids": ["100", "101"], "expected_count": 2,
        "confirmed_count": 2, "source_content_hash": source_content_hash(SOURCE),
    }
    store.claim_slot.return_value = {"id": 1, "status": "claimed"}
    return store


@pytest.fixture
def publisher():
    return Mock(return_value={"status": "ok", "post_id": "123_456"})


def run(store, publisher, **kwargs):
    options = dict(store=store, publisher=publisher, clock=lambda: FRIDAY,
                   enabled=True, dry_run=False, target="page", notifier=Mock(), run_id="test-run")
    options.update(kwargs)
    return run_facebook_novel(**options)


def assert_no_writes(store, publisher):
    publisher.assert_not_called()
    for name in ("claim_slot", "mark_sending", "finalize_publication", "mark_result"):
        getattr(store, name).assert_not_called()


def test_empty_queue_skips_without_writes(store, publisher):
    store.select_oldest_pending.return_value = None
    result = run(store, publisher)
    assert result["status"] == "SKIPPED_NO_DATA" and result["exit_code"] == 0
    assert_no_writes(store, publisher)


def test_preview_body_and_hash_are_preserved_without_writes(store, publisher):
    result = run(store, publisher, dry_run=True)
    assert result["status"] == "PREVIEW"
    assert SOURCE["novel_text"] in result["preview"]
    assert result["source_content_hash"] == source_content_hash(SOURCE)
    assert_no_writes(store, publisher)


def test_one_oldest_candidate_sent_once_with_durable_sending_before_graph(store, publisher):
    calls = []
    store.mark_sending.side_effect = lambda *a: calls.append("sending")
    publisher.side_effect = lambda *a, **k: (calls.append("graph") or {"status": "ok", "post_id": "123_456"})
    result = run(store, publisher)
    assert result["status"] == "PUBLISHED"
    assert calls == ["sending", "graph"]
    store.select_oldest_pending.assert_called_once_with("2026-10-16")
    assert publisher.call_count == 1
    store.claim_slot.assert_called_once_with("2026-04-05", "page", "2026-10-12", "test-run", result["content_hash"], result["source_content_hash"])
    store.finalize_publication.assert_called_once_with(1, "123_456", "2026-10-16T22:17:00+09:00")


@pytest.mark.parametrize("state", ["ok", "sending", "unknown", "claimed", "failed"])
def test_completed_or_unresolved_week_stops_before_selection(store, publisher, state):
    store.get_week_state.return_value = {"status": state, "id": 2}
    result = run(store, publisher)
    assert result["status"] == ("SKIPPED_WEEK_DONE" if state == "ok" else "HOLD_UNRESOLVED")
    store.select_oldest_pending.assert_not_called()
    assert_no_writes(store, publisher)


@pytest.mark.parametrize("change", [
    {"status": "failed"}, {"post_ids": ["100", "DRY_RUN"]}, {"confirmed_count": 1},
    {"expected_count": 3}, {"post_ids": ["100", "100"]}, {"post_ids": ["", "101"]},
    {"post_ids": ["0", "101"]}, {"source_content_hash": "changed"}, {"expected_count": True},
])
def test_x_failed_partial_dry_fake_or_changed_source_is_held(store, publisher, change):
    store.get_x_evidence.return_value.update(change)
    result = run(store, publisher)
    assert result["status"] == "HOLD_X_UNCONFIRMED"
    assert_no_writes(store, publisher)


@pytest.mark.parametrize("bad_source", [{"novel_text": " "}, {"title": None}, {"episode_range": []}])
def test_invalid_source_held_without_skipping_to_newer_row(store, publisher, bad_source):
    store.select_oldest_pending.return_value.update(bad_source)
    store.get_x_evidence.return_value["source_content_hash"] = source_content_hash(store.select_oldest_pending.return_value)
    result = run(store, publisher)
    assert result["status"] == "HOLD_INVALID_CONTENT"
    assert store.select_oldest_pending.call_count == 1
    assert_no_writes(store, publisher)


def test_overlength_not_truncated_or_published(store, publisher):
    result = run(store, publisher, max_chars=10)
    assert result["status"] == "HOLD_CONTENT_LENGTH"
    assert_no_writes(store, publisher)


@pytest.mark.parametrize("method", ["get_week_state", "select_oldest_pending", "get_x_evidence"])
def test_db_exception_is_failure_not_empty_queue(store, publisher, method):
    getattr(store, method).side_effect = RuntimeError("must not disclose token")
    result = run(store, publisher)
    assert result["status"] == "FAILED_DB_READ"
    assert "must not disclose" not in str(result)
    assert_no_writes(store, publisher)


def test_claim_competitor_does_not_send(store, publisher):
    store.claim_slot.return_value = {"status": "blocked", "id": 9}
    result = run(store, publisher)
    assert result["status"] == "SKIPPED_CLAIM_BLOCKED"
    publisher.assert_not_called()
    store.mark_sending.assert_not_called()


def test_claim_failure_does_not_send(store, publisher):
    store.claim_slot.side_effect = RuntimeError()
    assert run(store, publisher)["status"] == "FAILED_DB_CLAIM"
    publisher.assert_not_called()


def test_sending_commit_failure_prevents_graph_request(store, publisher):
    store.mark_sending.side_effect = RuntimeError()
    assert run(store, publisher)["status"] == "HOLD_DB_SENDING"
    publisher.assert_not_called()
    store.finalize_publication.assert_not_called()


@pytest.mark.parametrize("response", [{"status": "unknown"}, {"status": "ok", "post_id": ""},
                                     {"status": "ok", "post_id": "DRY_RUN"}, {"status": "dry_run"}, None])
def test_unknown_does_not_retry_or_write_success_date(store, publisher, response):
    publisher.return_value = response
    result = run(store, publisher)
    assert result["status"] == "HOLD_UNKNOWN" and publisher.call_count == 1
    store.finalize_publication.assert_not_called()
    store.mark_result.assert_called_once_with(1, "unknown", "PublisherUnknown")


def test_publisher_exception_is_unknown_and_not_retried(store, publisher):
    publisher.side_effect = RuntimeError("after send")
    assert run(store, publisher)["status"] == "HOLD_UNKNOWN"
    assert publisher.call_count == 1
    store.finalize_publication.assert_not_called()


def test_known_failure_no_date_no_automatic_retry(store, publisher):
    publisher.return_value = {"status": "failed"}
    assert run(store, publisher)["status"] == "FAILED_PUBLISH"
    store.mark_result.assert_called_once_with(1, "failed", "PublisherFailed")
    store.finalize_publication.assert_not_called()
    assert publisher.call_count == 1


def test_db_finalize_failure_retains_sending_no_repost(store, publisher):
    store.finalize_publication.side_effect = RuntimeError()
    result = run(store, publisher)
    assert result["status"] == "HOLD_DB_COMMIT" and result["post_id"] == "123_456"
    store.mark_result.assert_not_called()  # Never clear durable sending barrier.
    assert publisher.call_count == 1
    store.get_week_state.return_value = {"status": "sending", "id": 1}
    assert run(store, publisher)["status"] == "HOLD_UNRESOLVED"
    assert publisher.call_count == 1


def test_mark_result_failure_retains_sending(store, publisher):
    publisher.return_value = {"status": "unknown"}
    store.mark_result.side_effect = RuntimeError()
    assert run(store, publisher)["status"] == "HOLD_DB_RESULT"
    assert publisher.call_count == 1


@pytest.mark.parametrize("clock,status", [
    (datetime(2026, 10, 15, 14, 59, tzinfo=timezone.utc), "SKIPPED_BEFORE_FIRST_DATE"),
    (datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc), "SKIPPED_NOT_FRIDAY"),
])
def test_initial_date_and_kst_day_gates(store, publisher, clock, status):
    assert run(store, publisher, clock=lambda: clock)["status"] == status
    store.get_week_state.assert_not_called()
    assert_no_writes(store, publisher)


def test_kst_thursday_utc_is_friday_and_year_week_is_local_monday(store, publisher):
    stamp = datetime(2026, 12, 31, 15, 1, tzinfo=timezone.utc)  # Jan 1 Friday KST
    result = run(store, publisher, clock=lambda: stamp)
    assert result["status"] == "PUBLISHED" and result["slot_week_start"] == "2026-12-28"
    assert result["publication_date"] == "2027-01-01"


def test_disabled_default_blocks_before_database(monkeypatch, store, publisher):
    monkeypatch.delenv("FACE_NOVEL_ENABLED", raising=False)
    assert run(store, publisher, enabled=None)["status"] == "SKIPPED_DISABLED"
    assert_no_writes(store, publisher)
    store.get_week_state.assert_not_called()


def test_invalid_bool_and_naive_clock_rejected(store, publisher):
    assert run(store, publisher, dry_run="false")["status"] == "FAILED_CONFIG"
    assert run(store, publisher, clock=lambda: datetime(2026, 10, 16))["status"] == "FAILED_CONFIG"
    assert_no_writes(store, publisher)


def test_alert_failure_does_not_change_published_result(store, publisher):
    notifier = Mock(side_effect=RuntimeError("Telegram down"))
    result = run(store, publisher, notifier=notifier)
    assert result["status"] == "PUBLISHED" and result["notification_error_type"] == "RuntimeError"


def test_cover_loader_reuses_evidence(store, publisher, tmp_path):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"original image")
    loader = Mock(return_value=str(image_path))
    result = run(store, publisher, cover_loader=loader)
    assert result["status"] == "PUBLISHED"
    assert publisher.call_args.kwargs["image_path"] == str(image_path)
    loader.assert_called_once()


def test_default_cover_loader_hash_mismatch_uses_text(store, publisher):
    evidence = store.get_x_evidence.return_value
    evidence.update(cover_asset_uri="storage://covers/old.png", cover_hash="wrong")
    store.client.storage.from_.return_value.download.return_value = b"changed bytes"
    assert run(store, publisher)["status"] == "PUBLISHED"
    assert publisher.call_args.kwargs["image_path"] is None


def test_format_preserves_html_symbols_and_original_date_body():
    source = dict(SOURCE, novel_text="<chapter> & symbol\n***\n옛 시장")
    assert source["novel_text"] in format_facebook_novel(source)
    assert verify_x_publication(source, {}) is False


def test_cli_not_posted_reconciliation_releases_only_with_no_graph(monkeypatch, tmp_path):
    import json
    from db import comic_novel_channel_store
    from publishers import facebook_publisher
    from weekend import comic_novel_fb
    fake_store = Mock()
    fake_store.release_publication.return_value = {"status": "failed"}
    monkeypatch.setattr(comic_novel_channel_store, "ComicNovelChannelStore", lambda: fake_store)
    graph = Mock(side_effect=AssertionError("no Graph request permitted"))
    monkeypatch.setattr(facebook_publisher, "publish_page_post", graph)
    output = tmp_path / "reconciled.json"
    code = comic_novel_fb.main(["--stage", "reconcile", "--claim-id", "1", "--not-posted",
        "--actor", "operator", "--note", "Page activity log checked; post absent", "--output", str(output)])
    assert code == 0
    assert json.loads(output.read_text())["status"] == "RECONCILED_NOT_POSTED"
    fake_store.release_publication.assert_called_once_with("1", "operator", "Page activity log checked; post absent")
    fake_store.reconcile_publication.assert_not_called()
    graph.assert_not_called()


@pytest.mark.parametrize("evidence", [[], ["--actor", "operator"], ["--actor", " ", "--note", "absence"],
                                       ["--actor", "operator", "--note", "absence", "--post-id", "123"]])
def test_cli_not_posted_missing_or_conflicting_evidence_never_writes(monkeypatch, tmp_path, evidence):
    from db import comic_novel_channel_store
    from weekend import comic_novel_fb
    fake_store = Mock()
    monkeypatch.setattr(comic_novel_channel_store, "ComicNovelChannelStore", lambda: fake_store)
    code = comic_novel_fb.main(["--stage", "reconcile", "--claim-id", "1", "--not-posted",
                               "--output", str(tmp_path / "report.json"), *evidence])
    assert code == 1
    fake_store.release_publication.assert_not_called()
    fake_store.reconcile_publication.assert_not_called()


@pytest.mark.parametrize("state", [{"status": "ok"}, {"status": "blocked"}, None])
def test_cli_release_does_not_report_success_for_unconfirmed_status(monkeypatch, tmp_path, state):
    from db import comic_novel_channel_store
    from weekend import comic_novel_fb
    fake_store = Mock()
    fake_store.release_publication.return_value = state
    monkeypatch.setattr(comic_novel_channel_store, "ComicNovelChannelStore", lambda: fake_store)
    assert comic_novel_fb.main(["--stage", "reconcile", "--claim-id", "1", "--not-posted",
        "--actor", "operator", "--note", "checked absence", "--output", str(tmp_path / "report.json")]) == 1


@pytest.mark.parametrize("state,expected", [({"status": "ok"}, 0), ({"status": "blocked"}, 1)])
def test_cli_posted_reconciliation_validates_status_without_graph(monkeypatch, tmp_path, state, expected):
    from db import comic_novel_channel_store
    from publishers import facebook_publisher
    from weekend import comic_novel_fb
    fake_store = Mock()
    fake_store.reconcile_publication.return_value = state
    monkeypatch.setattr(comic_novel_channel_store, "ComicNovelChannelStore", lambda: fake_store)
    graph = Mock(side_effect=AssertionError("no Graph request permitted"))
    monkeypatch.setattr(facebook_publisher, "publish_page_post", graph)
    assert comic_novel_fb.main(["--stage", "reconcile", "--claim-id", "1", "--post-id", "123_456",
        "--confirmed-at", "2026-10-01T13:17:00Z", "--actor", "operator", "--note", "Page post verified",
        "--output", str(tmp_path / "report.json")]) == expected
    graph.assert_not_called()
    fake_store.release_publication.assert_not_called()


@pytest.mark.parametrize("page,token,error", [("", "", "MissingPageCredentials"),
                                             ("other", "token", "PublisherTargetMismatch")])
def test_default_publisher_missing_or_mismatched_credentials_stop_before_claim(monkeypatch, store, page, token, error):
    from publishers import facebook_publisher
    monkeypatch.setattr(facebook_publisher, "DRY_RUN", False)
    monkeypatch.setattr(facebook_publisher, "FACE_PAGE_ID", page)
    monkeypatch.setattr(facebook_publisher, "FACE_PAGE_TOKEN", token)
    graph = Mock()
    monkeypatch.setattr(facebook_publisher, "publish_page_post", graph)
    result = run(store, None)
    assert result["status"] == "FAILED_CONFIG" and result["error_type"] == error
    store.claim_slot.assert_not_called()
    graph.assert_not_called()
