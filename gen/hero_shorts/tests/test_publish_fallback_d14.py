"""D-14 회귀 — 발행 실패 시 대체 media_url 로 딱 1회만 재시도한다.

2026-10-15 실측: Zernio 가 토큰 URL 을 "no file extension and no stored content type"
으로 거부 → 래퍼 URL 로 성공. 미디어 URL 문제에 한해 자동 재시도가 유효하다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gen.hero_shorts.tiktok import publish as tk_publish


def _seg(**over):
    seg = {"segment_no": 1, "caption": "c", "media_url": "https://a/old.mp4",
           "gates": {"ok": True}, "privacy_level": "PUBLIC_TO_EVERYONE"}
    seg.update(over)
    return seg


def test_media_error_hint_classification():
    assert tk_publish._looks_like_media_error(
        Exception("no file extension and no stored content type"))
    assert tk_publish._looks_like_media_error(Exception("media download failed"))
    assert not tk_publish._looks_like_media_error(Exception("invalid privacy level"))


def test_retries_once_with_fallback(monkeypatch):
    calls = []

    def fake_call(**kw):
        calls.append(kw["media_url"])
        if len(calls) == 1:
            raise RuntimeError("no file extension and no stored content type")
        return {"outcome": "published", "post_url": "https://tiktok/x"}

    monkeypatch.setattr(tk_publish.pr, "create_and_monitor_post", fake_call)
    monkeypatch.setattr(tk_publish.tk_account, "preflight_enabled", lambda: False)
    monkeypatch.setattr(tk_publish.tk_gates, "gate_tg4", lambda *a, **k: {"ok": True})

    r = tk_publish.publish_segment(_seg(), account_id="acc",
                                   fallback_media_url="https://b/new.mp4")
    assert calls == ["https://a/old.mp4", "https://b/new.mp4"], calls
    assert r["media_url_fallback_used"] is True


def test_no_retry_without_fallback(monkeypatch):
    calls = []

    def fake_call(**kw):
        calls.append(kw["media_url"])
        raise RuntimeError("media download failed")

    monkeypatch.setattr(tk_publish.pr, "create_and_monitor_post", fake_call)
    monkeypatch.setattr(tk_publish.tk_account, "preflight_enabled", lambda: False)
    monkeypatch.setattr(tk_publish.tk_gates, "gate_tg4", lambda *a, **k: {"ok": True})
    try:
        tk_publish.publish_segment(_seg(), account_id="acc")
        raise AssertionError("예외가 나야 한다")
    except RuntimeError:
        pass
    assert len(calls) == 1


def test_no_retry_on_non_media_error(monkeypatch):
    calls = []

    def fake_call(**kw):
        calls.append(kw["media_url"])
        raise RuntimeError("invalid privacy level for this creator")

    monkeypatch.setattr(tk_publish.pr, "create_and_monitor_post", fake_call)
    monkeypatch.setattr(tk_publish.tk_account, "preflight_enabled", lambda: False)
    monkeypatch.setattr(tk_publish.tk_gates, "gate_tg4", lambda *a, **k: {"ok": True})
    try:
        tk_publish.publish_segment(_seg(), account_id="acc",
                                   fallback_media_url="https://b/new.mp4")
        raise AssertionError("예외가 나야 한다")
    except RuntimeError:
        pass
    assert len(calls) == 1, "미디어 오류가 아니면 재시도 금지"


def test_max_one_retry(monkeypatch):
    calls = []

    def fake_call(**kw):
        calls.append(kw["media_url"])
        raise RuntimeError("media download failed")

    monkeypatch.setattr(tk_publish.pr, "create_and_monitor_post", fake_call)
    monkeypatch.setattr(tk_publish.tk_account, "preflight_enabled", lambda: False)
    monkeypatch.setattr(tk_publish.tk_gates, "gate_tg4", lambda *a, **k: {"ok": True})
    try:
        tk_publish.publish_segment(_seg(), account_id="acc",
                                   fallback_media_url="https://b/new.mp4")
    except RuntimeError:
        pass
    assert len(calls) == 2, f"최대 1회 재시도여야 한다: {calls}"
