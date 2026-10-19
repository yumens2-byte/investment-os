"""zernio.py 단위 테스트 — 가짜 세션, 네트워크 없음."""
from __future__ import annotations

import json
import logging

import pytest

from gen.nuna_weekly import zernio
from gen.nuna_weekly.zernio import DummyZernioClient, ZernioClient, ZernioError, decompose

KEY = "sk_" + "a1" * 32


class Resp:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code = status
        self.text = "" if body is None else json.dumps(body)
        self.headers = headers or {}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": data, "headers": dict(headers or {})})
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", KEY)
    monkeypatch.setenv("ZERNIO_API_SUB_KEY", "sk_" + "b2" * 32)
    monkeypatch.delenv("ZERNIO_API_BASE", raising=False)


def test_key_alias_and_base(monkeypatch):
    s = FakeSession([Resp(200, {"accounts": []})])
    c = ZernioClient("sub", session=s)
    c.accounts_health()
    assert s.calls[0]["url"] == "https://zernio.com/api/v1/accounts/health"
    assert s.calls[0]["headers"]["Authorization"] == "Bearer sk_" + "b2" * 32
    monkeypatch.setenv("ZERNIO_API_BASE", "https://example.test/api/v1/")
    assert ZernioClient("main", session=FakeSession([])).base == "https://example.test/api/v1"
    with pytest.raises(ZernioError):
        ZernioClient("other", session=FakeSession([]))


def test_key_lazy_missing(monkeypatch):
    monkeypatch.delenv("ZERNIO_API_KEY")
    c = ZernioClient("main", session=FakeSession([Resp(200, {})]))
    from gen.nuna_weekly.secret_env import SecretMissingError
    with pytest.raises(SecretMissingError):
        c.accounts_health()


def test_stage_file_no_upload_url_no_auth_on_put(tmp_path, caplog):
    f = tmp_path / "ep.mp4"
    f.write_bytes(b"\x00" * 100)
    s = FakeSession([
        Resp(200, {"uploadUrl": "https://s3.test/put?X-Sig=SECRET", "publicUrl": "https://cdn.test/ep.mp4",
                   "key": "k1", "expiresIn": 3600}),
        Resp(200),
    ])
    c = ZernioClient("main", session=s)
    with caplog.at_level(logging.INFO):
        out = c.stage_file(f)
    assert set(out) == {"public_url", "sha256", "size", "key"}
    assert out["public_url"] == "https://cdn.test/ep.mp4" and out["size"] == 100
    pres = json.loads(s.calls[0]["data"])
    assert pres == {"filename": "ep.mp4", "contentType": "video/mp4", "size": 100}
    put = s.calls[1]
    assert put["method"] == "PUT" and "Authorization" not in put["headers"]
    assert "SECRET" not in caplog.text and KEY not in caplog.text
    assert out["sha256"][:12] in caplog.text


def test_presign_rejects_bad_type_and_missing_urls():
    c = ZernioClient("main", session=FakeSession([Resp(200, {"key": "x"})]))
    with pytest.raises(ZernioError):
        c.presign("a.bin", "application/octet-stream", 10)
    with pytest.raises(ZernioError):
        c.presign("a.mp4", "video/mp4", 10)


def test_validate_media():
    c = ZernioClient("main", session=FakeSession([Resp(200, {"valid": True, "size": 5})]))
    assert c.validate_media("https://cdn.test/a.mp4")["valid"] is True


def test_create_post_requires_key():
    c = ZernioClient("main", session=FakeSession([]))
    with pytest.raises(ZernioError):
        c.create_post({}, "")
    with pytest.raises(ZernioError):
        c.create_post({}, "x" * 256)
    with pytest.raises(ZernioError):
        c.create_post({"publishNow": True}, "k")


def test_create_post_409_retry_after_capped():
    slept = []
    s = FakeSession([Resp(409, {"code": "idempotency_conflict"}, {"Retry-After": "120"}),
                     Resp(200, {"post": {"_id": "p1"}})])
    c = ZernioClient("main", session=s, sleep=slept.append)
    status, body = c.create_post({"platforms": []}, "ep:main")
    assert status == 200 and body["post"]["_id"] == "p1"
    assert slept == [60.0]
    assert all(x["headers"]["Idempotency-Key"] == "ep:main" for x in s.calls)


def test_create_post_409_other_code_no_retry():
    s = FakeSession([Resp(409, {"code": "duplicate", "existingPostId": "e1"})])
    c = ZernioClient("main", session=s, sleep=lambda _: None)
    status, _ = c.create_post({"platforms": []}, "k")
    assert status == 409 and len(s.calls) == 1


def test_get_post_and_errors():
    c = ZernioClient("main", session=FakeSession([Resp(200, {"post": {"_id": "p"}}), Resp(500, {})]))
    assert c.get_post("p")["post"]["_id"] == "p"
    with pytest.raises(ZernioError):
        c.get_post("p")


def test_network_error_redacted():
    class Boom:
        def request(self, *a, **k):
            raise ConnectionError(f"fail https://x.test/a?token={KEY}")
    c = ZernioClient("main", session=Boom())
    with pytest.raises(ZernioError) as ei:
        c.accounts_health()
    assert KEY not in str(ei.value)


# ── decompose ──
def test_decompose_207_partial():
    body = {"post": {"_id": "p", "status": "partial", "platforms": [
        {"platform": "instagram", "status": "published", "platformPostUrl": "https://instagram.com/p/1"},
        {"platform": "threads", "status": "failed", "errorMessage": "bad"}]}}
    out = decompose(["instagram", "threads", "youtube"], 207, body)
    assert out["instagram"].status == "published"
    assert out["threads"].status == "failed" and out["threads"].error == "bad"
    assert out["youtube"].status == "failed" and "207" in out["youtube"].error


def test_decompose_youtube_host_check():
    body = {"post": {"_id": "p", "platforms": [
        {"platform": "youtube", "status": "published", "platformPostUrl": "https://facebook.com/x"}]}}
    assert decompose(["youtube"], 200, body)["youtube"].status == "pending"


def test_decompose_scheduled():
    body = {"data": {"post": {"id": "p9", "scheduledFor": "2026-10-26T08:30:00+09:00", "status": "scheduled",
                              "platforms": [{"platform": "facebook", "status": "pending"}]}}}
    r = decompose(["facebook", "youtube"], 200, body)
    assert r["facebook"].status == "scheduled" and r["facebook"].zernio_post_id == "p9"
    assert r["youtube"].status == "pending"


def test_decompose_http_error_no_entries():
    r = decompose(["instagram"], 400, {"error": {"code": "validation_error"}})
    assert r["instagram"].status == "failed" and "validation_error" in r["instagram"].error


def test_dummy_deterministic(tmp_path):
    d1, d2 = DummyZernioClient("main"), DummyZernioClient("main")
    b = {"platforms": [{"platform": "threads"}], "scheduledFor": "x"}
    assert d1.create_post(b, "k") == d2.create_post(b, "k")
    pid = d1.create_post(b, "k")[1]["post"]["_id"]
    assert d1.get_post(pid)["post"]["platforms"][0]["status"] == "published"
    assert ("create_post", "k", b) in d1.calls
    with pytest.raises(ZernioError):
        d1.create_post(b, "")


def test_version():
    assert zernio.VERSION == "1.0.0"
