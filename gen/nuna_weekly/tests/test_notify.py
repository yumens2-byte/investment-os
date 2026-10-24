"""notify.py 단위 테스트 — 가짜 세션, 네트워크 없음."""
from __future__ import annotations

import logging
from datetime import datetime

import pytest

from gen.nuna_weekly import config
from gen.nuna_weekly.notify import NotifyError, TelegramClient, build_generated

TOKEN = "1234567890:" + "A" * 35


class Resp:
    def __init__(self, body, status=200):
        self._b = body
        self.status_code = status

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, responses=None, admins=None, chat=None):
        self.responses = list(responses or [])
        self.calls = []
        self.chat = chat or {"id": -100123456, "type": "channel"}
        self.admins = admins if admins is not None else [
            {"status": "creator", "user": {"id": 111, "is_bot": False}},
            {"status": "administrator", "user": {"id": 222, "is_bot": False}}]

    def post(self, url, json=None, data=None, files=None, timeout=None):
        self.calls.append({"method": url.rsplit("/", 1)[-1], "url": url, "json": json, "data": data,
                           "files": files})
        if url.endswith("/getChat"):
            return Resp({"ok": True, "result": self.chat})
        if url.endswith("/getChatAdministrators"):
            return Resp({"ok": True, "result": self.admins})
        return self.responses.pop(0) if self.responses else Resp({"ok": True, "result": {"message_id": 7}})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_PAID_CHANNEL_ID", "-100123456")


SCHED = datetime(2026, 10, 26, 8, 30, tzinfo=config.KST)


def test_build_generated_without_admin():
    plan = build_generated("2026-W43", SCHED, set())
    assert plan["reply_markup"] is None and "보류 버튼 비활성: 채널 관리자 미확인" in plan["caption"]
    assert plan["caption"].startswith("[NUNA 주간 브리핑] 2026-W43 생성 완료 · 발행 예정 2026-10-26 08:30 KST")


def test_notify_dry_no_network(tmp_path):
    s = FakeSession()
    c = TelegramClient(session=s)
    out = c.notify_generated("2026-W43", None, SCHED, dry=True)
    assert s.calls == [] and out["method"] == "sendMessage"
    assert out["reply_markup"] is None  # dry-run은 관리자 조회도 하지 않는다


def test_notify_send_video(tmp_path):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"0" * 10)
    s = FakeSession()
    out = TelegramClient(session=s).notify_generated("E1", v, SCHED, dry=False)
    call = next(c for c in s.calls if c["method"] == "sendVideo")
    assert "video" in call["files"]
    assert "reply_markup" in call["data"] and out["message_id"] == 7


def test_notify_oversize_text_only(tmp_path, monkeypatch):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"0" * 10)
    monkeypatch.setattr(config, "TELEGRAM_VIDEO_MAX_BYTES", 5)
    s = FakeSession()
    TelegramClient(session=s).notify_generated("E1", v, SCHED, dry=False)
    call = next(c for c in s.calls if c["method"] == "sendMessage")
    assert call["json"]["text"].startswith("[NUNA")


def test_no_admin_stops_notification():
    s = FakeSession(admins=[])
    with pytest.raises(NotifyError, match="보류 권한"):
        TelegramClient(session=s).notify_generated("E1", None, SCHED, dry=False)
    assert not any(c["method"] == "sendMessage" for c in s.calls)


def _upd(uid, from_id, data, chat_id=-100123456):
    return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "data": data,
                                                  "from": {"id": from_id, "username": "secretname"},
                                                  "message": {"chat": {"id": chat_id}}}}


def test_poll_hold_accept_and_reject(caplog):
    updates = [_upd(1, 999, "nuna:hold:E1"), _upd(2, 111, "nuna:hold:E2"), _upd(3, 222, "nuna:hold:E1")]
    s = FakeSession([Resp({"ok": True, "result": updates})])
    with caplog.at_level(logging.INFO):
        out = TelegramClient(session=s).poll_hold("E1")
    assert out["by"] == "222" and out["at"].endswith("+09:00")
    assert next(c for c in s.calls if c["method"] == "getUpdates")["json"]["allowed_updates"] == ["callback_query"]
    answers = [c["json"]["text"] for c in s.calls if c["method"] == "answerCallbackQuery"]
    assert answers == ["권한 없음", "보류 접수"]
    assert "id=999" in caplog.text and "secretname" not in caplog.text
    assert TOKEN not in caplog.text
    # 다른 봇 사용자(hero) 콜백을 소비하지 않도록 offset ack 없음
    assert all("offset" not in (c["json"] or {}) for c in s.calls)


def test_poll_hold_none():
    s = FakeSession([Resp({"ok": True, "result": [_upd(1, 999, "nuna:hold:E1")]})])
    assert TelegramClient(session=s).poll_hold("E1") is None


def test_admin_in_different_chat_cannot_hold():
    s = FakeSession([Resp({"ok": True, "result": [_upd(1, 111, "nuna:hold:E1", -999)]})])
    assert TelegramClient(session=s).poll_hold("E1") is None


def test_admin_revocation_is_refreshed():
    s = FakeSession([Resp({"ok": True, "result": [_upd(1, 222, "nuna:hold:E1")]}),
                     Resp({"ok": True, "result": True}),
                     Resp({"ok": True, "result": [_upd(2, 222, "nuna:hold:E1")]})])
    c = TelegramClient(session=s)
    assert c.poll_hold("E1")["by"] == "222"
    s.admins = [{"status": "creator", "user": {"id": 111, "is_bot": False}}]
    assert c.poll_hold("E1") is None


def test_private_chat_only_recipient_can_hold():
    s = FakeSession([Resp({"ok": True, "result": [_upd(1, 999, "nuna:hold:E1", 111),
                                                  _upd(2, 111, "nuna:hold:E1", 111)]})],
                    chat={"id": 111, "type": "private"})
    assert TelegramClient(session=s).poll_hold("E1")["by"] == "111"
    assert not any(c["method"] == "getChatAdministrators" for c in s.calls)


def test_admin_query_error_stops_hold_poll():
    class Denied(FakeSession):
        def post(self, url, **kwargs):
            if url.endswith("/getChatAdministrators"):
                return Resp({"ok": False, "description": "forbidden"}, 403)
            return super().post(url, **kwargs)

    s = Denied()
    with pytest.raises(NotifyError, match="403"):
        TelegramClient(session=s).poll_hold("E1")
    assert not any(c["method"] == "getUpdates" for c in s.calls)


@pytest.mark.parametrize("row", [
    {"status": "member", "user": {"id": 111, "is_bot": False}},
    {"status": "administrator", "user": {"id": 111, "is_bot": True}},
    {"status": "administrator", "user": {"id": -100123456, "is_bot": False}},
])
def test_invalid_admin_records_fail_closed(row):
    with pytest.raises(NotifyError, match="보류 권한"):
        TelegramClient(session=FakeSession(admins=[row])).poll_hold("E1")


def test_error_redacts_token():
    class Boom:
        def post(self, url, **k):
            raise ConnectionError(f"failed {url}")
    c = TelegramClient(session=Boom())
    with pytest.raises(NotifyError) as ei:
        c.poll_hold("E1")
    assert TOKEN not in str(ei.value)


def test_send_report():
    s = FakeSession([Resp({"ok": False, "description": "bad"}, 400)])
    c = TelegramClient(session=s)
    assert c.send_report("x", dry=True) is True and s.calls == []
    assert c.send_report("x", dry=False) is False
    assert c.send_report("x", dry=False) is True


def test_missing_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    from gen.nuna_weekly.secret_env import SecretMissingError
    with pytest.raises(SecretMissingError):
        TelegramClient(session=FakeSession())
