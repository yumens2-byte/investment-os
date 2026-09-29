"""Behavioral checks for the read-only missed Morning Brief detector."""

from datetime import datetime, timezone

from core.morning_watchdog import classify


NOW = datetime(2026, 9, 29, 1, 15, tzinfo=timezone.utc)


def run(run_id, event, created):
    return {"id": run_id, "event": event, "created_at": created}


def job(conclusion, started="2026-09-28T21:38:00Z", status="completed", name="Morning Brief"):
    return {"name": name, "conclusion": conclusion, "status": status, "started_at": started}


def test_alert_success_is_not_morning_success():
    result = classify(NOW, [run(1, "schedule", "2026-09-29T00:48:00Z")], {1: [job("success", name="Alert Check"), job("skipped")]})
    assert result["status"] == "MISSED_SCHEDULE"


def test_manual_success_does_not_erase_missing_schedule():
    result = classify(NOW, [run(2, "workflow_dispatch", "2026-09-29T00:05:00Z")], {2: [job("success", "2026-09-29T00:06:00Z")]})
    assert result["status"] == "MANUAL_RUN_SUCCEEDED_UNVERIFIED"
    assert result["manual_run_id"] == 2


def test_schedule_late_success_and_inflight():
    runs = [run(3, "schedule", "2026-09-28T23:00:00Z")]
    assert classify(NOW, runs, {3: [job("success", "2026-09-28T23:01:00Z")]})["status"] == "LATE_SUCCESS"
    assert classify(NOW, runs, {3: [job(None, "2026-09-28T23:01:00Z", "in_progress")]})["status"] == "LATE_RUNNING"


def test_schedule_on_time_and_failure():
    runs = [run(4, "schedule", "2026-09-28T21:37:00Z")]
    assert classify(NOW, runs, {4: [job("success")]})["status"] == "ON_TIME"
    assert classify(NOW, runs, {4: [job("failure")]})["status"] == "SCHEDULE_FAILED"


def test_incomplete_evidence_is_unknown():
    runs = [run(4, "schedule", "2026-09-28T21:37:00Z")]
    assert classify(NOW, runs, {}, complete=False)["status"] == "UNKNOWN"
    assert classify(NOW, runs, {}, complete=True)["status"] == "UNKNOWN"


def test_before_grace_and_weekend():
    assert classify(datetime(2026, 9, 28, 21, 50, tzinfo=timezone.utc), [], {})["status"] == "NOT_DUE"
    assert classify(datetime(2026, 10, 3, 1, 15, tzinfo=timezone.utc), [], {})["status"] == "NOT_DUE"
