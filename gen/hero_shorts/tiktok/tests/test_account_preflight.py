"""tests/test_account_preflight.py — 틱톡 토큰 preflight 판정 검증."""
from datetime import UTC, datetime, timedelta

import pytest

from gen.hero_shorts.tiktok import account as A

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
AID = "6ace2290d59b32a54897e526"


def _acct(hours_from_now, active=True, reconnect=False, expires="auto"):
    if expires == "auto":
        expires = (NOW + timedelta(hours=hours_from_now)).isoformat().replace("+00:00", "Z")
    return {
        "_id": AID, "platform": "tiktok", "username": "tiger182725",
        "isActive": active, "needsReconnection": reconnect,
        "tokenExpiresAt": expires, "lastSuccessfulPublishAt": None,
    }


def _check(account, warn_hours=12.0):
    return A.check_token_health(account_id=AID, warn_hours=warn_hours,
                                now=NOW, accounts=[account])


def test_ok_when_plenty_of_time():
    h = _check(_acct(48))
    assert h.status == A.OK and h.ok is True and h.blocking is False
    assert h.hours_left == pytest.approx(48.0, abs=0.01)


def test_expiring_when_inside_warn_window():
    h = _check(_acct(6))
    assert h.status == A.EXPIRING
    assert h.blocking is False      # 경고 전용 — 운영 차단 아님


def test_expired_when_past_expiry():
    h = _check(_acct(-1))
    assert h.status == A.EXPIRED and h.blocking is True


def test_needs_reconnect_takes_priority():
    h = _check(_acct(48, reconnect=True))
    assert h.status == A.NEEDS_RECONNECT and h.blocking is True


def test_inactive_account_blocked():
    h = _check(_acct(48, active=False))
    assert h.status == A.INACTIVE and h.blocking is True


def test_missing_account_blocked():
    h = A.check_token_health(account_id="nope", now=NOW, accounts=[_acct(48)])
    assert h.status == A.MISSING and h.blocking is True


def test_missing_expiry_is_fail_closed():
    h = _check(_acct(48, expires=None))
    assert h.status == A.EXPIRED and h.blocking is True


def test_last_successful_publish_at_is_ignored():
    """회귀 — lastSuccessfulPublishAt=None 은 정상 판정을 뒤집지 않는다(오탐 방지)."""
    acct = _acct(48)
    assert acct["lastSuccessfulPublishAt"] is None
    assert _check(acct).status == A.OK


def test_assert_raises_only_on_blocking():
    with pytest.raises(RuntimeError):
        A.assert_token_ready(account_id=AID, now=NOW, accounts=[_acct(-1)])
    # 만료 임박은 차단하지 않는다
    health = A.assert_token_ready(account_id=AID, now=NOW, accounts=[_acct(6)])
    assert health.status == A.EXPIRING


def test_preflight_enabled_env(monkeypatch):
    monkeypatch.delenv(A.PREFLIGHT_ENV, raising=False)
    assert A.preflight_enabled() is True
    monkeypatch.setenv(A.PREFLIGHT_ENV, "0")
    assert A.preflight_enabled() is False


def test_warn_hours_env_override(monkeypatch):
    monkeypatch.setenv(A.WARN_HOURS_ENV, "3")
    assert A.resolve_warn_hours() == 3.0
    assert A.check_token_health(account_id=AID, now=NOW, accounts=[_acct(6)]).status == A.OK


def test_fetch_unavailable_is_non_blocking(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("gsk")
    monkeypatch.setattr(A, "fetch_accounts", _boom)
    h = A.check_token_health(account_id="A")
    assert h.status == A.UNAVAILABLE
    assert h.blocking is False
    assert h.ok is True
