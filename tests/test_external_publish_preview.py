"""PUB-04: preview must never cross X/Telegram publishing boundaries."""

import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publishers import telegram_publisher as tg
from publishers import x_publisher as xp
from core import dlq


def test_telegram_preview_blocks_message_photo_and_document_http():
    with patch.object(tg, "DRY_RUN", True), \
         patch.object(tg, "BOT_TOKEN", "configured-test-token"), \
         patch.object(tg, "FREE_CHAT_ID", "free-test"), \
         patch.object(tg, "PAID_CHAT_ID", "paid-test"), \
         patch.object(tg.requests, "post") as post, \
         patch("builtins.open") as file_open:
        results = [
            tg.send_message("preview", channel="both"),
            tg.send_photo("missing.png", channel="paid"),
            tg.send_document("missing.pdf", channel="paid"),
        ]

    post.assert_not_called()
    file_open.assert_not_called()
    assert [r[0]["kind"] for r in results] == ["message", "photo", "document"]
    assert all(r == [{"skipped": True, "dry_run": True,
                       "channel": "both" if i == 0 else "paid",
                       "kind": kind}]
               for i, (r, kind) in enumerate(zip(results, ("message", "photo", "document"))))


def test_x_preview_blocks_client_and_media_upload():
    with patch.object(xp, "DRY_RUN", True), \
         patch.object(xp, "_get_client") as client, \
         patch.object(xp, "_get_v1_api") as media:
        tweet = xp.publish_tweet("preview")
        image = xp.publish_tweet_with_image("preview", "missing.png")
        thread = xp.publish_thread(["first", "second"])

    client.assert_not_called()
    media.assert_not_called()
    assert tweet["dry_run"] and image["dry_run"] and thread["dry_run"]
    assert thread["published_count"] == 2


def test_telegram_live_preserves_per_channel_partial_result_without_real_http():
    class Response:
        def __init__(self, ok):
            self.ok = ok

        def json(self):
            return {"ok": self.ok}

    with patch.object(tg, "DRY_RUN", False), \
         patch.object(tg, "BOT_TOKEN", "configured-test-token"), \
         patch.object(tg, "FREE_CHAT_ID", "free-test"), \
         patch.object(tg, "PAID_CHAT_ID", "paid-test"), \
         patch.object(tg.requests, "post", side_effect=[Response(True), Response(False)]) as post, \
         patch("core.dlq.enqueue") as enqueue:
        result = tg.send_message("[DLQ] isolated test", channel="both")

    assert [r["ok"] for r in result] == [True, False]
    assert [c.kwargs["json"]["chat_id"] for c in post.call_args_list] == ["free-test", "paid-test"]
    enqueue.assert_not_called()


def test_preview_does_not_consume_existing_delivery_queue(tmp_path):
    queue_path = tmp_path / "dlq.json"
    item = {"id": "pending", "type": "x_tweet", "payload": {"text": "keep"},
            "retry_count": 0, "max_retries": 3}
    queue_path.write_text(json.dumps([item]), encoding="utf-8")
    with patch.object(dlq, "DRY_RUN", True), \
         patch.object(dlq, "DLQ_PATH", queue_path), \
         patch.object(dlq, "_auto_register_handlers") as handlers:
        result = dlq.process_queue()

    handlers.assert_not_called()
    assert result["skipped"] is True and result["remaining"] == 1
    assert json.loads(queue_path.read_text(encoding="utf-8")) == [item]
