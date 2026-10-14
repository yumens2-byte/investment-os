"""v2.7.4 (G-1/G-2) — 승인 콜백 폴링: 처리 콜백 확정 + 롱폴링 대기."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gen.hero_shorts import approval  # noqa: E402


def _cb(uid, data, chat="-1003587684034"):
    return {
        "update_id": uid,
        "callback_query": {
            "id": f"cb{uid}", "data": data,
            "from": {"username": "master"},
            "message": {"message_id": 1175, "chat": {"id": chat}},
        },
    }


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_PAID_CHANNEL_ID", "-1003587684034")


def test_valid_callback_is_acked(env, monkeypatch):
    """유효 콜백도 처리 후 확정(ack)되어 재조회되지 않는다(G-2)."""
    acks = []
    monkeypatch.setattr(approval, "_call_api", lambda *a, **k: [_cb(7, "hs:approve:ep88")])
    monkeypatch.setattr(approval, "_ack", lambda token, uid: acks.append(uid))
    out = approval.poll_callbacks()
    assert [d["decision"] for d in out] == ["APPROVED"]
    assert out[0]["ep"] == 88
    assert acks == [7]


def test_valid_callback_not_acked_when_disabled(env, monkeypatch):
    acks = []
    monkeypatch.setattr(approval, "_call_api", lambda *a, **k: [_cb(7, "hs:approve:ep88")])
    monkeypatch.setattr(approval, "_ack", lambda token, uid: acks.append(uid))
    approval.poll_callbacks(ack_valid=False)
    assert acks == []


def test_invalid_callback_acked_and_dropped(env, monkeypatch):
    acks = []
    monkeypatch.setattr(approval, "_call_api", lambda *a, **k: [_cb(9, "xx:bad")])
    monkeypatch.setattr(approval, "_ack", lambda token, uid: acks.append(uid))
    assert approval.poll_callbacks() == []
    assert acks == [9]


def test_other_chat_is_rejected(env, monkeypatch):
    monkeypatch.setattr(approval, "_call_api", lambda *a, **k: [_cb(3, "hs:approve:ep88", chat="-999")])
    monkeypatch.setattr(approval, "_ack", lambda token, uid: None)
    assert approval.poll_callbacks() == []


def test_wait_sec_returns_empty_after_deadline(env, monkeypatch):
    """롱폴링이 빈 응답을 반복하면 wait_sec 경과 후 빈 목록으로 복귀한다(무한 대기 금지)."""
    ticks = iter([1000.0, 1000.0, 1006.0])          # deadline계산, 루프진입, 데드라인경과
    monkeypatch.setattr(approval.time, "time", lambda: next(ticks, 1006.0))
    monkeypatch.setattr(approval, "_call_api", lambda *a, **k: [])
    assert approval.poll_callbacks(wait_sec=5) == []


def test_wait_sec_returns_callback_immediately(env, monkeypatch):
    monkeypatch.setattr(approval, "_call_api", lambda *a, **k: [_cb(11, "hs:hold:ep88")])
    monkeypatch.setattr(approval, "_ack", lambda token, uid: None)
    out = approval.poll_callbacks(wait_sec=60)
    assert [d["decision"] for d in out] == ["HOLD"]
