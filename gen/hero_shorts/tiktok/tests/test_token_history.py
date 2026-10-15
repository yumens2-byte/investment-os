"""B안(token_history) + A안(차단 경보) 회귀 테스트 — 2026-10-15.

결정론을 위해 now= 주입과 HERO_TOKEN_HISTORY_FILE 경로 주입을 쓴다(네트워크 0).
"""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gen.hero_shorts.tiktok import account as tk_account      # noqa: E402
from gen.hero_shorts.tiktok import token_history as th        # noqa: E402

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def health(status="ok", expires_at=None, hours_left=23.0, ok=None, problems=None):
    return tk_account.TokenHealth(
        (status == "ok") if ok is None else ok, status, "acc-1", "tiger182725",
        expires_at, hours_left, problems or [], 2.0, profile_id="prof-1")


@pytest.fixture
def hist(tmp_path, monkeypatch):
    p = tmp_path / "tiktok_token_history.json"
    monkeypatch.setenv(th.ENV_PATH, str(p))
    monkeypatch.delenv(th.ENV_NEXT_RUN, raising=False)
    return p


# ─────────────────────── 적재·병합 ───────────────────────

def test_record_appends_then_merges_same_expiry(hist):
    exp = iso(T0 + timedelta(days=1))
    r1 = th.record(health(expires_at=exp, hours_left=24.0), source="run1", now=T0)
    assert r1["appended"] is True and r1["observations"] == 1

    r2 = th.record(health(expires_at=exp, hours_left=20.0), source="run2",
                   now=T0 + timedelta(hours=4))
    assert r2["appended"] is False, "동일 (expires_at,status) 는 병합되어야 한다"
    obs = th.load()["observations"]
    assert len(obs) == 1
    assert obs[0]["runs"] == 2
    assert obs[0]["hours_left_min"] == 20.0, "최소 잔여시간 보존"
    assert obs[0]["hours_left_last"] == 20.0
    assert obs[0]["sources"] == ["run1", "run2"]


def test_record_detects_renewal_and_interval(hist):
    e1 = iso(T0 + timedelta(hours=1))
    e2 = iso(T0 + timedelta(hours=25, minutes=41))
    th.record(health(status="expired", expires_at=e1, hours_left=-0.26, ok=False), now=T0)
    r = th.record(health(expires_at=e2, hours_left=24.0), now=T0 + timedelta(hours=1, minutes=7))

    assert r["renewal_detected"] is True
    rn = r["last_renewal"]
    assert rn["from_expires_at"] == e1 and rn["to_expires_at"] == e2
    assert rn["expiry_delta_hours"] == pytest.approx(24.68, abs=0.02)
    assert rn["observed_gap_hours"] == pytest.approx(1.12, abs=0.02)
    assert rn["regression"] is False


def test_record_never_raises_when_path_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(th.ENV_PATH, str(blocker / "hist.json"))
    res = th.record(health(expires_at=iso(T0), hours_left=1.0))
    assert res["saved"] is False, "쓰기 불가 경로에서도 예외를 올리지 않고 False 반환"


# ─────────────────────── 분석·판정 ───────────────────────

def test_summarize_streak_and_gap_break(hist):
    # 3회 연속 정상 갱신(24h 간격) 후 1회 40h 공백 → 최근부터 연속 계산이 끊긴다
    for i, h in enumerate([0, 24, 48, 88]):
        th.record(health(expires_at=iso(T0 + timedelta(hours=h + 24)), hours_left=24.0),
                  now=T0 + timedelta(hours=h))
    s = th.summarize(th.load()["observations"])
    assert s["renewals"] == 3
    assert s["median_interval_hours"] == 24.0
    assert s["max_observed_gap_hours"] == 40.0
    assert s["consecutive_renewals"] == 0, "40h 공백이 최근 구간이면 연속 성공은 0"
    assert s["streak_broken_at"]


def test_verify_until_verdicts(hist):
    exp = iso(T0 + timedelta(hours=24))
    th.record(health(expires_at=exp, hours_left=24.0), now=T0)

    # 목표가 현재 만료 이전 → ok
    v = th.verify_until(th.load()["observations"], iso(T0 + timedelta(hours=12)))
    assert v["verdict"] == "ok" and v["renewals_needed"] == 0

    # 갱신 이력 0 → 필요 4회 대비 partial(이력 부족)
    v = th.verify_until(th.load()["observations"], iso(T0 + timedelta(hours=24 * 5)))
    assert v["verdict"] == "partial" and v["renewals_needed"] >= 4
    assert v["consecutive_renewals"] == 0

    # 갱신 4회 관측 → proven
    for i in range(4):
        th.record(health(expires_at=iso(T0 + timedelta(hours=24 * (i + 2))), hours_left=24.0),
                  now=T0 + timedelta(hours=24 * (i + 1)))
    v = th.verify_until(th.load()["observations"], iso(T0 + timedelta(hours=24 * 7)))
    assert v["consecutive_renewals"] >= 4
    assert v["verdict"] == "proven"


def test_verify_until_unknown_without_expiry(hist):
    th.record(health(status="unavailable", expires_at=None, hours_left=None, ok=False), now=T0)
    v = th.verify_until(th.load()["observations"], iso(T0 + timedelta(days=1)))
    assert v["verdict"] == "unknown"


# ─────────────────────── 경보 중복 억제 ───────────────────────

def test_should_alert_throttle(hist, monkeypatch):
    monkeypatch.setenv("HERO_TIKTOK_ALERT_THROTTLE_BLOCKING_HOURS", "1")
    monkeypatch.setenv("HERO_TIKTOK_ALERT_THROTTLE_WARN_HOURS", "6")

    assert th.should_alert("expired", blocking=True, now=T0)[0] is True
    th.record_alert("expired", "telegram", True, now=T0)
    assert th.should_alert("expired", blocking=True, now=T0 + timedelta(minutes=30))[0] is False
    assert th.should_alert("expired", blocking=True, now=T0 + timedelta(hours=2))[0] is True

    th.record_alert("expiring", "telegram", True, now=T0)
    assert th.should_alert("expiring", blocking=False, now=T0 + timedelta(hours=3))[0] is False
    assert th.should_alert("expiring", blocking=False, now=T0 + timedelta(hours=7))[0] is True

    assert th.should_alert("ok", blocking=False, now=T0)[0] is False


# ─────────────────────── 소급(backfill) ───────────────────────

def test_backfill_parses_json_and_text_lines(hist):
    log_text = "\n".join([
        '2026-10-14 21:26:13 INFO tiktok.account | {"status": "expiring", '
        '"expires_at": "2026-10-15T12:26:47.660Z"}',
        "2026-10-15 21:44:00 ERROR tiktok.account | 만료: 2026-10-15T12:26:47.660Z 남은 -0.26h",
    ])
    res = th.backfill([{"name": "runner3.log", "text": log_text}])
    assert res["added"] == 2, "상태가 다르면 별개 관측(expiring → expired)"

    log_json = ('2026-10-15 22:07:50 INFO | {"tokenExpiresAt": "2026-10-16T13:07:48.886Z", '
                '"status": "ok"}')
    th.backfill([("runner3b.log", log_json)])
    obs = th.load()["observations"]
    assert [o["expires_at"] for o in obs] == ["2026-10-15T12:26:47.660Z",
                                              "2026-10-15T12:26:47.660Z",
                                              "2026-10-16T13:07:48.886Z"]
    rn = th.renewals(obs)
    assert len(rn) == 1 and rn[0]["expiry_delta_hours"] == pytest.approx(24.68, abs=0.02)


def test_backfill_without_line_timestamp_leaves_first_seen_none(hist):
    th.backfill([{"name": "bare.log", "text": 'expires_at: 2026-10-16T13:07:48.886Z'}])
    obs = th.load()["observations"]
    assert obs[0]["first_seen"] is None, "로그에 시각이 없으면 추정으로 채우지 않는다"
    assert obs[0]["backfilled"] is True


# ─────────────────────── A안: 차단 경보 ───────────────────────

def test_assert_token_ready_alerts_before_raising_on_blocking(hist, monkeypatch):
    sent = []

    def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(tk_account, "send_telegram", fake_send)
    expired = {"_id": "acc-1", "platform": "tiktok", "username": "tiger182725",
               "profileId": {"_id": "prof-1"},
               "tokenExpiresAt": "2026-10-15T12:26:47.660Z",
               "isActive": True, "needsReconnection": False}

    with pytest.raises(RuntimeError) as err:
        tk_account.assert_token_ready(account_id="acc-1", accounts=[expired],
                                      now=datetime(2026, 10, 15, 12, 44, tzinfo=UTC))
    assert "expired" in str(err.value)
    assert sent, "차단 상태에서도 경보가 발송되어야 한다(A안 핵심)"
    assert "zernio.com/dashboard/connections" in sent[0], "재연결 경로 명시"
    assert "prof-1" in sent[0], "프로필 ID 포함"
    # 이력 적재 + 경보 이력
    data = th.load()
    assert data["observations"][0]["status"] == "expired"
    assert data["observations"][0]["profile_id"] == "prof-1"
    assert data["alerts"][0]["status"] == "expired"


def test_assert_token_ready_warn_only_throttles(hist, monkeypatch):
    sent = []
    monkeypatch.setattr(tk_account, "send_telegram", lambda text: (sent.append(text), True)[1])
    monkeypatch.setenv("HERO_TIKTOK_ALERT_THROTTLE_WARN_HOURS", "6")
    soon = {"_id": "acc-1", "platform": "tiktok", "username": "tiger182725",
            "profileId": {"_id": "prof-1"},
            "tokenExpiresAt": "2026-10-15T13:07:48.886Z",
            "isActive": True, "needsReconnection": False}
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

    tk_account.assert_token_ready(account_id="acc-1", accounts=[soon], warn_hours=2.0, now=now)
    tk_account.assert_token_ready(account_id="acc-1", accounts=[soon], warn_hours=2.0,
                                  now=now + timedelta(minutes=10))
    assert len(sent) == 1, "동일 warn 상태는 억제 창 안에서 1회만 발송"
    assert "CRON" not in sent[0]


def test_ok_status_never_alerts(hist, monkeypatch):
    sent = []
    monkeypatch.setattr(tk_account, "send_telegram", lambda text: (sent.append(text), True)[1])
    ok_acc = {"_id": "acc-1", "platform": "tiktok", "username": "tiger182725",
              "profileId": {"_id": "prof-1"},
              "tokenExpiresAt": "2026-10-16T13:07:48.886Z",
              "isActive": True, "needsReconnection": False}
    h = tk_account.assert_token_ready(account_id="acc-1", accounts=[ok_acc],
                                      now=datetime(2026, 10, 15, 12, 0, tzinfo=UTC))
    assert h.status == tk_account.OK and sent == []
