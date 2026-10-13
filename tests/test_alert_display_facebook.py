"""Regression: CPI empty snapshot, precision and free-alert Facebook mirroring.

All HTTP, Telegram and image generation calls are mocked; no live posts.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings
from core import alert_history
from engines import alert_engine as engine
from publishers import alert_facebook as fb
from publishers.alert_formatter import format_alert_tweet

SNAP = {"sp500": 0.04, "vix": 15.31, "us10y": 5.22, "oil": 80.04}
NOW = datetime(2026, 10, 13, 19, 50, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def forbid_live_http(monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", Mock(side_effect=AssertionError("live HTTP forbidden in regression tests")))


def signal(kind="CPI_HOT", snapshot=None):
    return engine.AlertSignal(kind, "L1", "test", SNAP.copy() if snapshot is None else snapshot)


@pytest.mark.parametrize("kind", ["CPI_HOT", "SPY", "FED_SHOCK", "OIL"])
def test_display_two_decimal_places(kind):
    text = format_alert_tweet(signal(kind))
    assert "SPY +0.04%" in text
    assert "VIX 15.31" in text
    assert "SPY +0.0%" not in text
    if kind == "OIL":
        assert "WTI $80.04" in text


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "bad", True])
def test_missing_invalid_values_are_not_zero(value):
    text = format_alert_tweet(signal(snapshot={"sp500": value}))
    assert "SPY N/A" in text and "VIX N/A" in text and "US10Y N/A" in text


def test_true_zero_remains_zero():
    assert "SPY +0.00%" in format_alert_tweet(signal(snapshot={**SNAP, "sp500": 0}))


def test_cpi_and_other_detectors_carry_collected_snapshot():
    alerts = engine.run_alert_engine(
        SNAP, {}, fred_data={"cpi_yoy": 3.71, "core_cpi_yoy": 2.76,
                             "spread_2y10y_bp": -51, "sofr_spread": 0.6},
        tier2_data={"move_index": 141},
        spy_sma_data={"spy_price": 90, "spy_sma50": 95, "spy_sma200": 100},
    )
    assert {a.alert_type for a in alerts} >= {"CPI_HOT", "YIELD_SPREAD_DEEP", "SOFR_STRESS", "MOVE_SPIKE", "SMA200_BREAK"}
    for alert in alerts:
        assert alert.snapshot["sp500"] == 0.04
        assert "SPY +0.04%" in format_alert_tweet(alert)
    assert SNAP == {"sp500": 0.04, "vix": 15.31, "us10y": 5.22, "oil": 80.04}
    assert next(a for a in alerts if a.alert_type == "CPI_HOT").x_eligible is False


def test_threshold_compares_unrounded_value():
    assert engine._spy_alert({"sp500": -2.499}) is None
    assert engine._spy_alert({"sp500": -2.5}).level == "L1"


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FACE_ENABLED", True)
    monkeypatch.setattr(settings, "FACE_SESSIONS", ("alert",))
    monkeypatch.setattr(settings, "PUBLISH_CHANNEL", "all")
    monkeypatch.setattr(settings, "DRY_RUN", False)
    post = Mock(return_value={"status": "ok", "success": True, "post_id": "P1", "kind": "photo"})
    monkeypatch.setattr(fb, "publish_page_post", post)
    return post, tmp_path / "fb_alert_history.json"


def test_same_plain_text_and_image(configured):
    post, path = configured
    text = "🚨 <b>Investment OS Alert</b>\n\n" + format_alert_tweet(signal())
    result = fb.publish_alert(text, "CPI_HOT", "L1", "meme.png", now=NOW, path=path)
    assert result["status"] == "ok"
    post.assert_called_once_with(text.replace("<b>", "").replace("</b>", ""), image_path="meme.png", session="alert")


def test_cooldown_and_later_notification(configured):
    post, path = configured
    fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)
    assert fb.publish_alert("text", "CPI_HOT", "L1", now=NOW + timedelta(minutes=10), path=path)["status"] == "skipped"
    assert fb.publish_alert("text", "CPI_HOT", "L1", now=NOW + timedelta(hours=4), path=path)["status"] == "ok"
    assert post.call_count == 2


def test_other_types_and_upgrade_not_blocked(configured):
    post, path = configured
    fb.publish_alert("text", "SPY", "L1", now=NOW, path=path)
    fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)
    fb.publish_alert("text", "SPY", "L2", now=NOW, path=path)
    assert post.call_count == 3


def test_unknown_blocks_retry_even_next_day(configured):
    post, path = configured
    post.return_value = {"status": "unknown"}
    fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)
    assert fb.publish_alert("text", "CPI_HOT", "L1", now=NOW + timedelta(days=1), path=path)["status"] == "held"
    assert post.call_count == 1


def test_definitive_failure_can_retry(configured):
    post, path = configured
    post.return_value = {"status": "failed"}
    fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)
    fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)
    assert post.call_count == 2


def test_exception_preserves_reservation(configured):
    post, path = configured
    post.side_effect = RuntimeError("interrupted POST")
    assert fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)["status"] == "held"
    assert json.loads(path.read_text())[-1]["status"] == "unknown"
    fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)
    assert post.call_count == 1


def test_corrupt_history_blocks_http(configured):
    post, path = configured
    path.write_text("{broken")
    assert fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)["status"] == "held"
    post.assert_not_called()


@pytest.mark.parametrize("name,value", [("FACE_ENABLED", False), ("FACE_SESSIONS", ("morning",)), ("PUBLISH_CHANNEL", "x")])
def test_existing_channel_switches_respected(configured, monkeypatch, name, value):
    post, path = configured
    monkeypatch.setattr(settings, name, value)
    assert fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)["status"] == "skipped"
    post.assert_not_called()
    assert not path.exists()


def test_dry_run_does_not_save_reservation(configured, monkeypatch):
    post, path = configured
    monkeypatch.setattr(settings, "DRY_RUN", True)
    post.return_value = {"status": "dry_run"}
    assert fb.publish_alert("text", "CPI_HOT", "L1", now=NOW, path=path)["status"] == "dry_run"
    assert not path.exists()


@pytest.mark.parametrize("tg_fails", [False, True])
def test_full_cpi_run_mirrors_payload_without_x(configured, monkeypatch, tmp_path, tg_fails):
    import run_alert
    post, path = configured
    monkeypatch.setattr(settings, "PUBLISHED_DIR", tmp_path)
    monkeypatch.setattr(run_alert, "DRY_RUN", True)  # bypass wall-clock window only
    monkeypatch.setattr(run_alert, "_load_prev_snapshot", lambda: {})
    monkeypatch.setattr(run_alert, "_load_x_alert_history", lambda: {})
    monkeypatch.setattr(run_alert, "_save_x_alert_history", lambda _: None)
    monkeypatch.setattr(alert_history, "should_send", lambda *_: (True, "test"))
    monkeypatch.setattr(alert_history, "record_alert", Mock())

    def module(name, **members):
        obj = ModuleType(name)
        obj.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, obj)

    tg_photo = Mock(return_value=[{"ok": True}])
    if tg_fails:
        tg_photo.side_effect = RuntimeError("Telegram unavailable")
    tg_message = Mock(return_value=[{"ok": True}])
    x = Mock()
    module("collectors", __path__=[])
    module("collectors.yahoo_finance", collect_market_snapshot=lambda: SNAP.copy(), collect_tier2_market_data=lambda: {}, collect_spy_sma=lambda: {})
    module("collectors.news_rss", collect_news_sentiment=lambda: {})
    module("collectors.fred_client", collect_macro_data=lambda: {"cpi_yoy": 3.71, "core_cpi_yoy": 2.76}, detect_macro_changes=lambda *_: [])
    module("core.dlq", get_queue_size=lambda: 0, process_queue=Mock())
    module("engines.alert_followup", run_followup=lambda _: {"success": True, "processed": 0})
    module("core.json_builder", load_core_data=lambda: {"data": {}})
    module("publishers.telegram_publisher", send_photo=tg_photo, send_message=tg_message)
    module("publishers.x_publisher", publish_tweet=x, post_alert_tweet=x, publish_tweet_with_image=x)
    module("publishers.econ_event_formatter", format_econ_event=Mock(), format_econ_event_telegram=Mock())
    module("comic", __path__=[])
    module("comic.meme_generator", generate_meme=lambda **_: "meme.png")
    run_alert.run()
    x.assert_not_called()
    caption = tg_photo.call_args.kwargs["caption"]
    assert "SPY +0.04%" in caption and "VIX 15.31" in caption
    assert post.call_args.args[0] == caption.replace("<b>", "").replace("</b>", "")
    assert post.call_args.kwargs["image_path"] == "meme.png"


def test_workflow_enables_alert_session_and_persists_dedicated_history():
    import yaml
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/main.yml").read_text())
    job = workflow["jobs"]["alert_check"]
    assert job["env"]["FACE_SESSIONS"].endswith(",alert")
    steps = job["steps"]
    restore = next(i for i, s in enumerate(steps) if s.get("name") == "Restore Facebook alert history")
    run = next(i for i, s in enumerate(steps) if s.get("run") == "python main.py alert")
    save = next(i for i, s in enumerate(steps) if s.get("name") == "Save Facebook alert history")
    assert restore < run < save and steps[save]["if"] == "always()"
