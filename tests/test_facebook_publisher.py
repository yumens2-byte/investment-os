"""FB-1 (2026-10-05): Facebook 발행 모듈 단위 테스트.

대상: publishers/facebook_publisher.py · publishers/fb_formatter.py · core/fb_history.py
실제 HTTP 요청은 하지 않는다 (requests.post 는 전부 mock).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import fb_history
from publishers import facebook_publisher as fbp
from publishers.fb_formatter import to_facebook_text

TOKEN = "EAAtesttoken1234567890abcdefXYZ"
PAGE = "1234567890"


def _resp(status: int, body=None, text: str = ""):
    r = MagicMock()
    r.status_code = status
    if body is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = body
    r.text = text or str(body)
    return r


@pytest.fixture
def live():
    """DRY_RUN=false + 페이지 설정 완료 상태."""
    with patch.object(fbp, "DRY_RUN", False), \
         patch.object(fbp, "FACE_PAGE_ID", PAGE), \
         patch.object(fbp, "FACE_PAGE_TOKEN", TOKEN):
        yield


@pytest.fixture
def small_png(tmp_path):
    p = tmp_path / "dash.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    return str(p)


# ── fb_formatter ──────────────────────────────────────────────
class TestFormatter:
    def test_strips_tags_and_tg_only_line(self):
        src = (
            "🌅 <b>Morning Brief</b>\n\n"
            "<i>요약 문장</i>\n\n\n\n"
            "💎 <i>풀버전 대시보드 → 유료 채널</i>\n\n"
            "#ETF #투자"
        )
        out = to_facebook_text(src)
        assert "<" not in out and ">" not in out.replace("→", "")
        assert "유료 채널" not in out
        assert "\n\n\n" not in out
        assert out.startswith("🌅 Morning Brief")
        assert out.endswith("#ETF #투자")

    def test_unescape_entities(self):
        assert to_facebook_text("S&amp;P &lt;500&gt;") == "S&P <500>"

    def test_empty(self):
        assert to_facebook_text("") == ""
        assert to_facebook_text("<b></b>") == ""


# ── facebook_publisher ───────────────────────────────────────
class TestPublisher:
    def test_dry_run_blocks_http_and_file_open(self, small_png):
        with patch.object(fbp, "DRY_RUN", True), \
             patch.object(fbp, "FACE_PAGE_ID", PAGE), \
             patch.object(fbp, "FACE_PAGE_TOKEN", TOKEN), \
             patch.object(fbp.requests, "post") as post, \
             patch("builtins.open") as file_open:
            res = fbp.publish_page_post("본문", small_png, "morning")
        post.assert_not_called()
        file_open.assert_not_called()
        assert res["dry_run"] and res["success"] and res["status"] == "dry_run"
        assert res["kind"] == "photo"

    def test_not_configured(self):
        with patch.object(fbp, "DRY_RUN", False), \
             patch.object(fbp, "FACE_PAGE_ID", ""), \
             patch.object(fbp, "FACE_PAGE_TOKEN", ""), \
             patch.object(fbp.requests, "post") as post:
            res = fbp.publish_page_post("본문", None, "morning")
        post.assert_not_called()
        assert res["status"] == "failed" and res["reason"] == "not_configured"

    def test_feed_when_no_image(self, live):
        with patch.object(fbp.requests, "post", return_value=_resp(200, {"id": "P_1"})) as post:
            res = fbp.publish_page_post("본문", None, "morning")
        assert post.call_count == 1
        url = post.call_args.args[0]
        assert url == f"{fbp.FACE_GRAPH_BASE}/{PAGE}/feed"
        assert post.call_args.kwargs["data"]["message"] == "본문"
        assert "files" not in post.call_args.kwargs
        assert res["success"] is True and res["status"] == "ok"
        assert res["kind"] == "feed" and res["post_id"] == "P_1"

    def test_photo_with_caption(self, live, small_png):
        body = {"id": "PHOTO_1", "post_id": "PAGE_POST_1"}
        with patch.object(fbp.requests, "post", return_value=_resp(200, body)) as post:
            res = fbp.publish_page_post("캡션", small_png, "full")
        url = post.call_args.args[0]
        assert url == f"{fbp.FACE_GRAPH_BASE}/{PAGE}/photos"
        assert post.call_args.kwargs["data"]["caption"] == "캡션"
        assert "message" not in post.call_args.kwargs["data"]
        assert "source" in post.call_args.kwargs["files"]
        assert res["status"] == "ok" and res["kind"] == "photo"
        assert res["post_id"] == "PAGE_POST_1"

    def test_oversize_image_falls_back_to_feed(self, live, small_png):
        with patch.object(fbp, "FACE_PHOTO_MAX_BYTES", 10), \
             patch.object(fbp.requests, "post", return_value=_resp(200, {"id": "P_2"})) as post:
            res = fbp.publish_page_post("본문", small_png, "full")
        assert post.call_args.args[0].endswith("/feed")
        assert res["kind"] == "feed" and res["status"] == "ok"

    def test_missing_image_falls_back_to_feed(self, live):
        with patch.object(fbp.requests, "post", return_value=_resp(200, {"id": "P_3"})) as post:
            res = fbp.publish_page_post("본문", "/nonexistent/x.png", "full")
        assert post.call_args.args[0].endswith("/feed")
        assert res["kind"] == "feed"

    @pytest.mark.parametrize("http,code,reason", [
        (400, 190, "auth_error"),
        (401, None, "auth_error"),
        (403, 200, "permission_error"),
        (400, 80001, "rate_limited"),
        (400, 100, "api_error"),
    ])
    def test_4xx_is_failed_without_retry(self, live, http, code, reason):
        body = {"error": {"code": code, "message": f"bad access_token={TOKEN}"}}
        with patch.object(fbp.requests, "post", return_value=_resp(http, body)) as post:
            res = fbp.publish_page_post("본문", None, "morning")
        assert post.call_count == 1
        assert res["status"] == "failed" and res["reason"] == reason
        assert res["error_code"] == code

    def test_5xx_is_unknown(self, live):
        with patch.object(fbp.requests, "post", return_value=_resp(500, {"error": {"code": 2}})) as post:
            res = fbp.publish_page_post("본문", None, "morning")
        assert post.call_count == 1
        assert res["status"] == "unknown" and not res["success"]

    def test_read_timeout_is_unknown(self, live):
        with patch.object(fbp.requests, "post", side_effect=requests.exceptions.ReadTimeout("t")) as post:
            res = fbp.publish_page_post("본문", None, "morning")
        assert post.call_count == 1
        assert res["status"] == "unknown"

    def test_connect_timeout_is_failed(self, live):
        with patch.object(fbp.requests, "post", side_effect=requests.exceptions.ConnectTimeout("c")):
            res = fbp.publish_page_post("본문", None, "morning")
        assert res["status"] == "failed" and res["reason"] == "connect_timeout"

    def test_200_without_id_is_unknown(self, live):
        with patch.object(fbp.requests, "post", return_value=_resp(200, {})):
            res = fbp.publish_page_post("본문", None, "morning")
        assert res["status"] == "unknown"

    def test_empty_text_not_posted(self, live):
        with patch.object(fbp.requests, "post") as post:
            res = fbp.publish_page_post("   ", None, "morning")
        post.assert_not_called()
        assert res["status"] == "failed" and res["reason"] == "empty_text"

    def test_token_never_logged(self, live, caplog):
        caplog.set_level("DEBUG")
        body = {"error": {"code": 190, "message": f"invalid access_token={TOKEN} {TOKEN}"}}
        with patch.object(fbp.requests, "post", return_value=_resp(400, body)):
            fbp.publish_page_post("본문", None, "morning")
        with patch.object(fbp.requests, "post",
                          side_effect=requests.exceptions.ReadTimeout(f"url?access_token={TOKEN}")):
            fbp.publish_page_post("본문", None, "morning")
        assert TOKEN not in caplog.text
        assert "***" in caplog.text


# ── fb_history ───────────────────────────────────────────────
class TestHistory:
    def test_block_same_session_same_kst_day(self, tmp_path):
        p = tmp_path / "fb_history.json"
        now = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)   # KST 10/06 09:00
        assert fb_history.is_blocked("morning", now=now, path=p) == (False, "")
        fb_history.record("morning", "ok", "본문", post_id="P1", kind="feed", now=now, path=p)
        blocked, why = fb_history.is_blocked("morning", now=now, path=p)
        assert blocked and "fb_ok_exists:2026-10-06:morning" == why
        # 다른 세션은 허용
        assert fb_history.is_blocked("narrative", now=now, path=p)[0] is False
        # 다음 KST 날짜는 허용
        assert fb_history.is_blocked("morning", now=now + timedelta(days=1), path=p)[0] is False

    def test_unknown_also_blocks(self, tmp_path):
        p = tmp_path / "fb_history.json"
        now = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
        fb_history.record("full", "unknown", "본문", now=now, path=p)
        assert fb_history.is_blocked("full", now=now, path=p)[0] is True

    def test_kst_date_boundary(self, tmp_path):
        # UTC 10/05 21:36 = KST 10/06 06:36 (morning cron)
        now = datetime(2026, 10, 5, 21, 36, tzinfo=timezone.utc)
        assert fb_history.publication_date(now) == "2026-10-06"

    def test_corrupt_file_is_empty(self, tmp_path):
        p = tmp_path / "fb_history.json"
        p.write_text("{broken", encoding="utf-8")
        assert fb_history.load(p) == []
        assert fb_history.is_blocked("morning", path=p) == (False, "")

    def test_keeps_max_records(self, tmp_path):
        p = tmp_path / "fb_history.json"
        with patch.object(fb_history, "FB_HISTORY_MAX", 3):
            for i in range(5):
                fb_history.record("morning", "ok", f"t{i}", post_id=str(i), path=p)
        assert [r["post_id"] for r in fb_history.load(p)] == ["2", "3", "4"]
