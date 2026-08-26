"""
tests/test_alert_state_backend.py (v1.0.0)
===========================================
T-4 Alert 상태 백엔드 테스트 — 모드 강등 / 판정 등가성 boundary / 폴백 / 병행기록.
실행: python tests/test_alert_state_backend.py  (외부 API 호출 없음)
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test")

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


def test_mode_resolution():
    """모드 판정 + 오입력 file 강등"""
    import core.alert_state_backend as b
    for raw, expect in [
        ("file", "file"), ("dual", "dual"), ("supabase", "supabase"),
        ("SUPABASE", "supabase"), (" dual ", "dual"),
        ("true", "file"), ("db", "file"), ("", "file"),
    ]:
        os.environ["ALERT_STATE_BACKEND"] = raw
        check(f"mode '{raw}' → {expect}", b.get_mode() == expect)
    os.environ["ALERT_STATE_BACKEND"] = "file"


def test_decide_send_boundaries():
    """_decide_send 판정 등가 boundary — v1.7.0 규칙"""
    from core.alert_history import _decide_send
    now = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)

    def ts(minutes_ago):
        return (now - timedelta(minutes=minutes_ago)).isoformat()

    cases = [
        # (last_level, last_ts, level, expect_send, label)
        ("L1", ts(1),    "L2", True,  "악화 L1→L2 즉시"),
        ("L2", ts(1),    "L3", True,  "악화 L2→L3 즉시"),
        ("L2", ts(60),   "L1", False, "완화 1h 차단(<2h)"),
        ("L2", ts(119),  "L1", False, "완화 119분 차단"),
        ("L2", ts(121),  "L1", True,  "완화 121분 허용(>2h)"),
        ("L1", ts(60),   "L1", False, "동일 1h 차단(<4h)"),
        ("L1", ts(239),  "L1", False, "동일 239분 차단"),
        ("L1", ts(241),  "L1", True,  "동일 241분 허용(>4h)"),
        ("L1", "broken", "L1", True,  "파싱 오류 → 허용(보수)"),
        ("L2", "broken", "L1", True,  "완화+파싱 오류 → 허용"),
    ]
    for last_level, last_ts, level, expect, label in cases:
        send, _reason = _decide_send(last_level, last_ts, level, now=now)
        check(f"decide: {label}", send is expect)


def test_file_db_equivalence():
    """동일 입력에 대해 파일 경로/DB 경로 판정 일치 (mock DB)"""
    import core.alert_history as ah
    import core.alert_state_backend as b
    now = datetime.now(timezone.utc)

    scenarios = [
        ("L1", (now - timedelta(hours=1)).isoformat(),  "L2"),
        ("L2", (now - timedelta(hours=1)).isoformat(),  "L1"),
        ("L2", (now - timedelta(hours=3)).isoformat(),  "L1"),
        ("L1", (now - timedelta(hours=3)).isoformat(),  "L1"),
        ("L1", (now - timedelta(hours=5)).isoformat(),  "L1"),
    ]
    orig = b.db_fetch_last
    try:
        for last_level, last_ts, level in scenarios:
            file_send, _ = ah._decide_send(last_level, last_ts, level)
            b.db_fetch_last = lambda t, channel="main", _l=last_level, _t=last_ts: {
                "level": _l, "created_at": _t, "tweet_id": "", "vix_level": None
            }
            db_send, _ = ah._db_decide("VIX", level)
            check(f"등가: {last_level}@{last_ts[-13:]}→{level}", file_send == db_send)
    finally:
        b.db_fetch_last = orig


def test_supabase_mode_fallback(tmp_dir):
    """supabase 모드 DB 예외 → 파일 폴백 (기존 파일 판정 결과 반환)"""
    import core.alert_history as ah
    import core.alert_state_backend as b

    ah.ALERT_HISTORY_FILE = tmp_dir / "alert_history.json"
    os.environ["ALERT_STATE_BACKEND"] = "supabase"
    orig = b.db_fetch_last

    def boom(*a, **k):
        raise ConnectionError("db down")

    try:
        b.db_fetch_last = boom
        send, reason = ah.should_send("VIX", "L1")
        check("폴백: 이력 없음 → 최초 발송", send is True and "최초" in reason)

        # 파일에 4h 이내 동일 등급 기록 → 폴백 판정이 차단해야 함
        os.environ["ALERT_STATE_BACKEND"] = "file"
        ah.record_alert("VIX", "L1", "T1", "preview")
        os.environ["ALERT_STATE_BACKEND"] = "supabase"
        b.db_fetch_last = boom
        send2, _ = ah.should_send("VIX", "L1")
        check("폴백: 파일 4h 쿨다운 차단", send2 is False)
    finally:
        b.db_fetch_last = orig
        os.environ["ALERT_STATE_BACKEND"] = "file"


def test_dual_record_and_no_side_effect(tmp_dir):
    """dual: 파일 판정 기준 유지 + db_record 병행 호출 / db_record 예외 무전파"""
    import core.alert_history as ah
    import core.alert_state_backend as b

    ah.ALERT_HISTORY_FILE = tmp_dir / "alert_history2.json"
    os.environ["ALERT_STATE_BACKEND"] = "dual"
    calls = []
    orig_rec, orig_fetch = b.db_record, b.db_fetch_last
    try:
        b.db_record = lambda *a, **k: calls.append((a, k)) or True
        b.db_fetch_last = lambda *a, **k: None
        ah.record_alert("OIL", "L1", "T2", "p")
        check("dual: db_record 병행 호출", len(calls) == 1)
        check("dual: channel=main", calls[0][1].get("channel") == "main")

        send, _ = ah.should_send("OIL", "L2")   # 파일 기준: 악화 → True
        check("dual: 발송 판정은 파일 기준", send is True)

        # db_record 내부 예외가 record_alert를 중단시키지 않음 (실 db_record 사용)
        b.db_record = orig_rec
        import db.supabase_client as sc
        sc._client = None
        os.environ["SUPABASE_URL"] = ""           # get_client 실패 유도
        try:
            ah.record_alert("OIL", "L2", "T3", "p")
            check("dual: db 실패에도 파일 기록 지속", True)
        except Exception:  # noqa: BLE001 — 무전파 검증 목적
            check("dual: db 실패에도 파일 기록 지속", False)
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        sc._client = None
    finally:
        b.db_record, b.db_fetch_last = orig_rec, orig_fetch
        os.environ["ALERT_STATE_BACKEND"] = "file"


def test_countdown_equivalence():
    """카운트다운: DB 오늘 기록 → 차단 / 어제 기록 → 허용 / 예외 → 파일 폴백"""
    import core.alert_history as ah
    import core.alert_state_backend as b

    os.environ["ALERT_STATE_BACKEND"] = "supabase"
    orig = b.db_fetch_last_countdown
    now = datetime.now(timezone.utc)
    try:
        b.db_fetch_last_countdown = lambda v: {
            "level": "L1", "created_at": now.isoformat(), "vix_level": v
        }
        send, _ = ah.should_send_countdown(25)
        check("countdown: 오늘 기록 차단", send is False)

        b.db_fetch_last_countdown = lambda v: {
            "level": "L1",
            "created_at": (now - timedelta(days=1, hours=10)).isoformat(),
            "vix_level": v,
        }
        send2, _ = ah.should_send_countdown(25)
        check("countdown: 전일 기록 허용", send2 is True)

        def boom(v):
            raise ConnectionError("db down")
        b.db_fetch_last_countdown = boom
        send3, _ = ah.should_send_countdown(27)
        check("countdown: 예외 → 파일 폴백 동작", isinstance(send3, bool))
    finally:
        b.db_fetch_last_countdown = orig
        os.environ["ALERT_STATE_BACKEND"] = "file"


def test_parse_db_timestamp():
    from core.alert_state_backend import parse_db_timestamp
    t1 = parse_db_timestamp("2026-08-27T12:00:00+00:00")
    t2 = parse_db_timestamp("2026-08-27T12:00:00Z")
    t3 = parse_db_timestamp("2026-08-27T12:00:00")
    check("ts: +00:00 aware", t1.tzinfo is not None)
    check("ts: Z 정규화", t2 == t1)
    check("ts: naive → UTC 부여", t3.tzinfo is not None and t3 == t1)


def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    test_mode_resolution()
    test_decide_send_boundaries()
    test_file_db_equivalence()
    test_supabase_mode_fallback(tmp)
    test_dual_record_and_no_side_effect(tmp)
    test_countdown_equivalence()
    test_parse_db_timestamp()
    total = PASS + FAIL
    print("=" * 60)
    print(f"결과: {PASS}/{total} PASS" + ("" if FAIL == 0 else f" — FAIL {FAIL}건"))
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
