"""backends/fal.py 단위 테스트 — 가짜 세션·DummyFal, 네트워크 없음."""
from __future__ import annotations

import json
import logging

import pytest

from gen.nuna_weekly import config
from gen.nuna_weekly.backends.fal import DummyFal, FalError, FalQueueBackend, request_signature
from gen.nuna_weekly.backends.tts import probe_seconds

KEY = "fal-key-0123456789abcdef"
SUB = {"request_id": "req-123", "status_url": "https://queue.test/s/req-123",
       "response_url": "https://queue.test/r/req-123"}
IMG_URL = "https://cdn.test/out.png?token=SECRET123"


class Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code = status
        self._body = body
        self.content = content
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, statuses, result, submit=SUB, fail_submit=False):
        self.statuses = list(statuses)
        self.result = result
        self.submit = submit
        self.fail_submit = fail_submit
        self.log: list[tuple[str, str]] = []
        self.headers: list[dict] = []

    def request(self, method, url, headers=None, timeout=None, data=None):
        self.log.append((method, url))
        self.headers.append(headers or {})
        if method == "POST":
            if self.fail_submit:
                return Resp(403, {"detail": "forbidden"})
            return Resp(200, self.submit)
        if url == SUB["status_url"]:
            return Resp(200, {"status": self.statuses.pop(0)})
        if url == SUB["response_url"]:
            return Resp(200, self.result)
        raise AssertionError(url)

    def get(self, url, timeout=None):
        self.log.append(("DL", url))
        return Resp(200, content=b"PNGDATA" * 10)


def backend(sess, **kw):
    return FalQueueBackend(session=sess, sleep=lambda s: None, poll_interval=0.01, **kw)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("FAL_AI_KEY", KEY)


def test_submit_poll_download(tmp_path, caplog):
    sess = FakeSession(["IN_QUEUE", "IN_PROGRESS", "COMPLETED"], {"images": [{"url": IMG_URL}]})
    out, pend = tmp_path / "a.png", tmp_path / "a.pending.json"
    with caplog.at_level(logging.DEBUG):
        r = backend(sess).run(config.FAL_IMAGE_ENDPOINT, {"prompt": "p"}, out, pend)
    assert r.request_id == "req-123" and r.remote_url == IMG_URL and out.read_bytes().startswith(b"PNG")
    assert sess.log[0] == ("POST", f"{config.FAL_QUEUE_BASE}/{config.FAL_IMAGE_ENDPOINT}")
    assert sess.headers[0]["Authorization"] == f"Key {KEY}"
    assert not pend.exists()
    assert "SECRET123" not in caplog.text and KEY not in caplog.text and "req-123" in caplog.text


def test_pending_written_before_poll_and_resume(tmp_path):
    payload = {"prompt": "p", "duration": "5"}
    out, pend = tmp_path / "c.mp4", tmp_path / "c.pending.json"
    sess = FakeSession(["IN_PROGRESS"], {})
    with pytest.raises(FalError, match="타임아웃"):
        backend(sess, max_polls=1).run(config.FAL_CLIP_ENDPOINT, payload, out, pend)
    job = json.loads(pend.read_text())
    assert job["signature"] == request_signature(payload) and job["request_id"] == "req-123"
    assert set(job) >= {"endpoint", "signature", "request_id", "status_url", "response_url", "created_at"}
    # 재실행 — 재제출 없이 재개
    sess2 = FakeSession(["COMPLETED"], {"video": {"url": "https://cdn.test/v.mp4"}})
    r = backend(sess2).run(config.FAL_CLIP_ENDPOINT, payload, out, pend)
    assert all(m != "POST" for m, _ in sess2.log)
    assert r.remote_url == "https://cdn.test/v.mp4" and not pend.exists()


def test_signature_mismatch_resubmits(tmp_path):
    pend = tmp_path / "p.json"
    pend.write_text(json.dumps({**SUB, "endpoint": config.FAL_IMAGE_ENDPOINT, "signature": "other"}))
    sess = FakeSession(["COMPLETED"], {"images": [{"url": IMG_URL}]})
    backend(sess).run(config.FAL_IMAGE_ENDPOINT, {"prompt": "p"}, tmp_path / "x.png", pend)
    assert sess.log[0][0] == "POST"


def test_failed_status(tmp_path):
    sess = FakeSession(["FAILED"], {})
    with pytest.raises(FalError, match="FAILED"):
        backend(sess).run(config.FAL_IMAGE_ENDPOINT, {"prompt": "p"}, tmp_path / "x.png", tmp_path / "p.json")


def test_http_error_redacted(tmp_path):
    sess = FakeSession([], {}, fail_submit=True)
    with pytest.raises(FalError) as ei:
        backend(sess).run(config.FAL_IMAGE_ENDPOINT, {"prompt": "p"}, tmp_path / "x.png", tmp_path / "p.json")
    assert "403" in str(ei.value) and KEY not in str(ei.value)


def test_requires_key(monkeypatch):
    monkeypatch.delenv("FAL_AI_KEY")
    from gen.nuna_weekly.secret_env import SecretMissingError

    with pytest.raises(SecretMissingError):
        FalQueueBackend(session=object())


def test_signature_order_independent():
    assert request_signature({"a": 1, "b": 2}) == request_signature({"b": 2, "a": 1})


def test_dummy_image_and_video(tmp_path):
    from PIL import Image

    d = DummyFal()
    r = d.run(config.FAL_IMAGE_ENDPOINT, {"prompt": "p"}, tmp_path / "i.png", tmp_path / "i.p.json")
    with Image.open(r.path) as im:
        assert im.size == (1080, 1920) and im.format == "PNG"
    assert r.remote_url == "https://dummy.invalid/i.png" and r.request_id.startswith("dummy-")
    assert len(r.request_id) == len("dummy-") + 8
    v = d.run(config.FAL_CLIP_ENDPOINT, {"prompt": "p", "duration": "5"}, tmp_path / "v.mp4", tmp_path / "v.p.json")
    assert abs(probe_seconds(v.path) - 5) < 0.1
