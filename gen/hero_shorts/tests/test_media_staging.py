"""tests/test_media_staging.py — 스테이징 업로드 경로(D) 회귀.

블로커#3(원장 래퍼 URL 403, 승인 A 실측 2026-10-18)의 해소 경로 검증.
스펙 1차 출처: docs.zernio.com/media/get-media-presigned-url · /guides/media-uploads.

네트워크 호출 0 — opener 를 주입해 요청 봉투를 검증한다. 발행은 일절 수행하지 않는다.
"""
from __future__ import annotations

import json
import os
import urllib.error

import pytest

from gen.hero_shorts.publish_targets import runner as R
from gen.hero_shorts.publish_targets.transport import (
    STAGING_CONTENT_TYPES,
    TransportError,
    ZernioRestTransport,
    guess_content_type,
    is_genspark_wrapper,
)

API_KEY = "sk_" + ("ab" * 32)          # sk_ + 64hex = 67자 (스펙 형태, 값은 가짜)
PRESIGN_BODY = {
    "uploadUrl": "https://storage.example/put?sig=1h-credential",
    "publicUrl": "https://cdn.example/staged/ep88_final_voiced.mp4",
    "key": "staging/ep88_final_voiced.mp4",
    "expiresIn": 3600,
}


# --------------------------------------------------------------------------- #
# 페이크 — opener 주입 (네트워크 0)
# --------------------------------------------------------------------------- #
class FakeResp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """순서대로 응답을 꺼내 쓴다. 요청 봉투는 self.requests 에 쌓인다."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_transport(opener):
    return ZernioRestTransport(api_key=API_KEY, base="https://zernio.example/api/v1",
                               opener=opener)


def _err(status, body):
    return urllib.error.HTTPError("u", status, "e", hdrs=None,
                                  fp=__import__("io").BytesIO(json.dumps(body).encode()))


# --------------------------------------------------------------------------- #
# presign
# --------------------------------------------------------------------------- #
def test_presign_request_shape_and_parsing():
    op = FakeOpener(FakeResp(200, json.dumps(PRESIGN_BODY).encode()))
    pre = make_transport(op).presign_media("ep88_final_voiced.mp4", "video/mp4",
                                           size=9455069)
    req = op.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://zernio.example/api/v1/media/presign"
    body = json.loads(req.data.decode())
    assert body == {"filename": "ep88_final_voiced.mp4", "contentType": "video/mp4",
                    "size": 9455069}
    assert req.headers["Authorization"].endswith(API_KEY) or \
        req.headers["Authorization"] == f"Bearer {API_KEY}" or \
        req.get_header("Authorization") == f"Bearer {API_KEY}"
    assert pre["public_url"] == PRESIGN_BODY["publicUrl"]
    assert pre["upload_url"] == PRESIGN_BODY["uploadUrl"]
    assert pre["expires_in"] == 3600


def test_presign_rejects_generic_mime_before_network():
    op = FakeOpener()                       # 빈 opener — 호출되면 IndexError 로 테스트 실패
    with pytest.raises(TransportError, match="contentType"):
        make_transport(op).presign_media("x.bin", "application/octet-stream", size=1)


def test_presign_400_surfaces_code():
    op = FakeOpener(_err(400, {"error": "bad contentType", "code": "INVALID_FIELD_VALUE"}))
    with pytest.raises(TransportError, match="INVALID_FIELD_VALUE"):
        make_transport(op).presign_media("a.mp4", "video/mp4", size=10)


def test_presign_missing_fields_is_spec_mismatch():
    op = FakeOpener(FakeResp(200, b'{"ok":true}'))
    with pytest.raises(TransportError, match="스펙 불일치"):
        make_transport(op).presign_media("a.mp4", "video/mp4")


# --------------------------------------------------------------------------- #
# PUT
# --------------------------------------------------------------------------- #
def test_put_has_no_auth_header_and_exact_bytes(tmp_path):
    f = tmp_path / "v.mp4"
    payload = b"\x00\x01video-bytes-9MB-stand-in"
    f.write_bytes(payload)
    op = FakeOpener(FakeResp(200, b"", headers={"ETag": '"abc123"'}))
    put = make_transport(op).put_media_file("https://storage.example/put?sig=1",
                                            f, "video/mp4")
    req = op.requests[0]
    assert req.get_method() == "PUT"
    auth = req.headers.get("Authorization") or req.get_header("Authorization")
    assert auth is None, "스토리지 서명 URL 에 Bearer 를 붙이면 안 된다(스펙)"
    assert req.headers.get("Content-type") == "video/mp4" or \
        req.get_header("Content-type") == "video/mp4"
    assert req.data == payload
    assert put == {"http_status": 200, "etag": '"abc123"', "bytes": len(payload)}


def test_put_non_2xx_raises(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    op = FakeOpener(_err(403, {}))
    with pytest.raises(TransportError, match="403"):
        make_transport(op).put_media_file("https://s/p", f, "video/mp4")


# --------------------------------------------------------------------------- #
# stage_media_file (전체 오케스트레이션)
# --------------------------------------------------------------------------- #
def test_stage_media_file_end_to_end(tmp_path):
    f = tmp_path / "ep88_final_voiced.mp4"
    f.write_bytes(b"MP4-FIXTURE" * 8)
    op = FakeOpener(FakeResp(200, json.dumps(PRESIGN_BODY).encode()),
                    FakeResp(200, b"", headers={"ETag": '"e1"'}))
    out = make_transport(op).stage_media_file(f)
    assert out["public_url"] == PRESIGN_BODY["publicUrl"]
    assert out["size_bytes"] == f.stat().st_size
    assert out["content_type"] == "video/mp4"
    assert len(out["sha256"]) == 64
    assert out["put_status"] == 200
    assert "upload_url" not in out, "1시간짜리 자격증명을 결과에 남기지 않는다"


def test_stage_rejects_unknown_extension_before_network(tmp_path):
    f = tmp_path / "v.bin"
    f.write_bytes(b"x")
    op = FakeOpener()
    with pytest.raises(TransportError, match="fail-closed"):
        make_transport(op).stage_media_file(f)


def test_stage_rejects_missing_file(tmp_path):
    op = FakeOpener()
    with pytest.raises(TransportError, match="파일 없음"):
        make_transport(op).stage_media_file(tmp_path / "nope.mp4")


# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #
def test_guess_content_type_uses_spec_table():
    assert guess_content_type("a.mp4") == "video/mp4"
    assert guess_content_type("A.MOV") == "video/quicktime"
    with pytest.raises(TransportError):
        guess_content_type("a.exe")


def test_is_genspark_wrapper():
    assert is_genspark_wrapper("https://www.genspark.ai/api/files/s/ioJNCDQU") is True
    assert is_genspark_wrapper("https://cdn.example/a.mp4") is False
    assert is_genspark_wrapper(None) is False
    assert is_genspark_wrapper("") is False


def test_validate_media_sends_url_only():
    op = FakeOpener(FakeResp(200, json.dumps({"valid": True, "size": 9455069}).encode()))
    resp = make_transport(op).validate_media("https://cdn.example/a.mp4")
    req = op.requests[0]
    assert req.full_url.endswith("/tools/validate/media")
    assert json.loads(req.data.decode()) == {"url": "https://cdn.example/a.mp4"}
    assert resp.body["valid"] is True


def test_spec_table_has_mp4():
    assert STAGING_CONTENT_TYPES[".mp4"] == "video/mp4"


# --------------------------------------------------------------------------- #
# 러너 연결 — stage 서브커맨드 (원장 쓰기 규율)
# --------------------------------------------------------------------------- #
LEDGER0 = {
    "_schema": 2,
    "ep88": {
        "title": "Ep88",
        "status": "QC_PASSED_READY",
        "combined": {"media_url": "https://www.genspark.ai/api/files/s/ioJNCDQU",
                     "duration_sec": 61.0},
        "tiktok": {"status": "published", "segment_1": {"post_id": "x"}},
    },
}


@pytest.fixture
def staged_env(tmp_path, monkeypatch):
    """원장 사본 + make_transport 주입(FakeOpener)."""
    led = tmp_path / "publish_state.json"
    led.write_text(json.dumps(LEDGER0, ensure_ascii=False), encoding="utf-8")
    f = tmp_path / "ep88_final_voiced.mp4"
    f.write_bytes(b"MP4-FIXTURE" * 8)

    op = FakeOpener(FakeResp(200, json.dumps(PRESIGN_BODY).encode()),
                    FakeResp(200, b"", headers={"ETag": '"e1"'}))
    monkeypatch.setattr(R, "make_transport", lambda kind="rest", **kw: make_transport(op))
    return {"ledger": led, "file": f, "opener": op}


def _stage(tmp_path, args):
    # --ledger 는 메인 파서 인자 — 서브커맨드보다 앞에 와야 한다(repo CLI 계약).
    return R.main(["--ledger", str(tmp_path / "publish_state.json"),
                   "stage", "--ep", "ep88",
                   "--file", str(tmp_path / "ep88_final_voiced.mp4")] + args)


def test_runner_stage_dry_keeps_ledger_unchanged(tmp_path, staged_env, capsys):
    before = staged_env["ledger"].read_bytes()
    rc = _stage(tmp_path, [])
    assert rc == R.EXIT_OK
    assert staged_env["ledger"].read_bytes() == before, "--write 없이 원장이 변했다"
    out = capsys.readouterr().out
    assert PRESIGN_BODY["publicUrl"] in out


def test_runner_stage_write_updates_media_url_and_preserves_tiktok(tmp_path, staged_env):
    rc = _stage(tmp_path, ["--write"])
    assert rc == R.EXIT_OK
    doc = json.loads(staged_env["ledger"].read_text(encoding="utf-8"))
    rec = doc["ep88"]
    assert rec["combined"]["media_url"] == PRESIGN_BODY["publicUrl"]
    assert rec["combined"]["media_url_prev"] == \
        "https://www.genspark.ai/api/files/s/ioJNCDQU", "구값 출처를 보존해야 한다"
    assert rec["combined"]["media_staged"]["sha256"] and \
        rec["combined"]["media_staged"]["size_bytes"] == (tmp_path / "ep88_final_voiced.mp4").stat().st_size
    assert "upload_url" not in json.dumps(doc), "자격증명이 원장에 새면 안 된다"
    assert rec["tiktok"] == LEDGER0["ep88"]["tiktok"], "틱톡 블록은 불변이다"


def test_runner_stage_missing_ep_fails_closed(tmp_path, staged_env):
    rc = R.main(["--ledger", str(staged_env["ledger"]),
                 "stage", "--ep", "ep99", "--file", str(staged_env["file"])])
    assert rc == R.EXIT_GATE_BLOCKED


def test_runner_stage_bad_file_fails_closed(tmp_path, staged_env, capsys):
    missing = tmp_path / "nope.mp4"
    rc = R.main(["--ledger", str(staged_env["ledger"]),
                 "stage", "--ep", "ep88", "--file", str(missing)])
    assert rc == R.EXIT_TRANSPORT
    assert "스테이징 실패" in capsys.readouterr().err
