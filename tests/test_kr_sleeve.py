"""
tests/test_kr_sleeve.py — KR Sleeve (DN2026_0108) 단위·계약 테스트. 외부 호출 없음(STD-TEST-14).

T1 대응: T-01 비중 합 · T-02 모드 · T-03 시세 검증 · T-04 완료 봉·휴장 · T-05 입력 계약 · T-06 공개 문구
         T-07 발행 게이트 · T-08 발행 순서·선기록 · T-09 core_data 불변 · T-10 main.yml 계약
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import random
from datetime import UTC, date, datetime, timedelta, timezone
from unittest import mock

import pytest
import yaml

from kr_sleeve import config, engine, formatter, input_contract, publisher
from kr_sleeve import run as kr_run
from kr_sleeve.collector import (
    Bar,
    build_quote,
    collect,
    completed_bars,
    is_kr_market_closed_today,
    parse_fchart,
)
from kr_sleeve.errors import (
    ALREADY_DONE,
    INPUT_INVALID,
    INPUT_MISSING,
    INPUT_STALE,
    PRICE_OUT_OF_RANGE,
    SOURCE_FORMAT,
    SOURCE_HTTP,
    SOURCE_INSUFFICIENT,
    SOURCE_STALE,
    SleeveSkip,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "core_data_morning_20261005.json"
KST = timezone(timedelta(hours=9))
# 픽스처 timestamp 2026-10-04T20:20:14Z (= KST 10-05 05:20) 기준 10분 뒤
NOW_FIX = datetime(2026, 10, 4, 20, 30, tzinfo=UTC)

ALL_REGIMES = sorted(config.REGIMES)


# ── 헬퍼 ────────────────────────────────────────────────────
def _weekdays_until(last: date, n: int) -> list[date]:
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _xml(days: list[date], closes: list[float], vol: int = 1000) -> str:
    items = "".join(
        f'<item data="{d:%Y%m%d}|{c}|{c}|{c}|{c}|{vol}" />' for d, c in zip(days, closes, strict=True)
    )
    return f'<?xml version="1.0" encoding="EUC-KR"?><protocol><chartdata>{items}</chartdata></protocol>'


class _Resp:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text


def _http_ok(last_day: date, n: int = 30, base: dict | None = None):
    base = base or {"161510": 21900.0, "0060H0": 11800.0}

    def get(url, params=None, headers=None, timeout=None):
        days = _weekdays_until(last_day, n)
        b = base[params["symbol"]]
        closes = [round(b + i * 5, 1) for i in range(len(days))]
        return _Resp(200, _xml(days, closes))
    return get


def _template_alloc(regime: str) -> dict[str, int]:
    from engines.etf_engine import compute_etf_allocation
    neutral = {e: "Neutral" for e in config.US_ETFS}
    return compute_etf_allocation(regime, neutral)["allocation"]


def _envelope(**over) -> dict:
    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env.update(over)
    return env


class FakeStore:
    def __init__(self, done=False, fail_claim=(), fail_upsert=False):
        self.done, self.fail_claim, self.fail_upsert = done, set(fail_claim), fail_upsert
        self.daily, self.calls = [], []

    def already_done(self, run_date):
        self.calls.append(("already_done", run_date))
        return self.done

    def upsert_daily(self, row):
        if self.fail_upsert:
            raise RuntimeError("db down")
        self.daily.append(row)

    def set_daily_status(self, run_date, status, reason=None):
        self.calls.append(("set_status", status))

    def claim_publish(self, run_date, channel):
        if channel in self.fail_claim:
            raise RuntimeError("claim fail")
        self.calls.append(("claim", channel))
        return True

    def finish_publish(self, run_date, channel, status, post_id=None, reason=None):
        self.calls.append(("finish", channel, status))


class FakePub:
    def __init__(self, result="ok"):
        self.result, self.calls, self.notes = result, [], []

    def _mk(self, name):
        def fn(text):
            self.calls.append(name)
            return {"status": self.result, "post_id": f"{name}-1" if self.result == "ok" else None,
                    "reason": None}
        return fn

    @property
    def publish_telegram(self):
        return self._mk("telegram")

    @property
    def publish_facebook(self):
        return self._mk("facebook")

    @property
    def publish_x(self):
        return self._mk("x")

    def notify_operator(self, html):
        self.notes.append(html)
        return {"status": "ok"}


# ── T-02 모드 ───────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("dry_run", "dry_run"), ("shadow", "shadow"), ("live", "live"), ("LIVE", "live"),
    (" shadow ", "shadow"), ("", "dry_run"), (None, "dry_run"), ("lvie", "dry_run"),
    ("true", "dry_run"), ("-", "dry_run"),
])
def test_parse_mode(raw, expected):
    assert config.parse_mode(raw) == expected


# ── T-01 비중 ───────────────────────────────────────────────
@pytest.mark.parametrize("regime", ALL_REGIMES)
@pytest.mark.parametrize("k", [0, 20, 50])
def test_combine_sums_to_100(regime, k):
    us = _template_alloc(regime)
    for internal in (config.INTERNAL_A, engine.internal_b(us)):
        total = engine.combine(us, internal, k)
        assert sum(total.values()) == 100
        assert min(total.values()) >= 0
        assert set(total) == set(config.US_ETFS) | {"161510", "0060H0"}


def test_risk_on_example_matches_design():
    us = _template_alloc("Risk-On")
    assert us == {"QQQM": 35, "XLK": 25, "SPYM": 20, "XLE": 10, "ITA": 5, "TLT": 5}
    res = engine.compute(us, 20)
    assert res["total_allocation"] == {"QQQM": 28, "XLK": 20, "SPYM": 16, "XLE": 8, "ITA": 4,
                                       "TLT": 4, "161510": 10, "0060H0": 10}
    assert res["lookthrough_us_pct"] == 82.0


@pytest.mark.parametrize("regime,expected", [
    ("Risk-On", 20), ("Risk-Off", 70), ("Oil Shock", 70), ("Liquidity Crisis", 75),
    ("Recession Risk", 55), ("Stagflation Risk", 75), ("AI Bubble", 15), ("Transition", 45),
])
def test_internal_b_tracks_defensive_share(regime, expected):
    b = engine.internal_b(_template_alloc(regime))
    assert b == {"161510": expected, "0060H0": 100 - expected}


def test_us_input_not_mutated():
    us = _template_alloc("Transition")
    before = dict(us)
    engine.compute(us)
    assert us == before


def test_largest_remainder_rounding():
    us = {"QQQM": 33, "XLK": 17, "SPYM": 17, "XLE": 11, "ITA": 11, "TLT": 11}
    total = engine.combine(us, {"161510": 50, "0060H0": 50}, 15)
    assert sum(total.values()) == 100


# ── T-05 입력 계약 ───────────────────────────────────────────
def test_input_contract_fixture_ok():
    inp = input_contract.validate(_envelope(), NOW_FIX)
    assert inp.regime == "Transition" and inp.risk_level == "MEDIUM"
    assert sum(inp.us_allocation.values()) == 100


@pytest.mark.parametrize("mutate,code", [
    (lambda e: e.update(status="error"), INPUT_INVALID),
    (lambda e: e.update(command="run market (intraday)"), INPUT_INVALID),
    (lambda e: e.update(timestamp="bad"), INPUT_INVALID),
    (lambda e: e.update(timestamp="2026-10-04T20:20:14"), INPUT_INVALID),
    (lambda e: e["data"]["market_regime"].update(market_regime="Unknown"), INPUT_INVALID),
    (lambda e: e["data"]["market_regime"].update(market_risk_level="X"), INPUT_INVALID),
    (lambda e: e["data"]["etf_allocation"]["allocation"].update(QQQM=31), INPUT_INVALID),
    (lambda e: e["data"]["etf_allocation"]["allocation"].pop("TLT"), INPUT_INVALID),
    (lambda e: e["data"]["etf_allocation"]["allocation"].update(GLD=0), INPUT_INVALID),
    (lambda e: e["data"].pop("etf_allocation"), INPUT_INVALID),
])
def test_input_contract_violations(mutate, code):
    env = _envelope()
    mutate(env)
    with pytest.raises(SleeveSkip) as ei:
        input_contract.validate(env, NOW_FIX)
    assert ei.value.code == code


def test_input_contract_stale():
    late = NOW_FIX + timedelta(minutes=config.INPUT_MAX_AGE_MIN + 1)
    with pytest.raises(SleeveSkip) as ei:
        input_contract.validate(_envelope(), late)
    assert ei.value.code == INPUT_STALE


def test_input_missing_file(tmp_path):
    with pytest.raises(SleeveSkip) as ei:
        input_contract.load_core_data(str(tmp_path / "none.json"))
    assert ei.value.code == INPUT_MISSING


# ── T-03·T-04 시세 ──────────────────────────────────────────
def test_parse_fchart_ok_and_null_close_skipped():
    xml = ('<protocol><chartdata><item data="20261105|1|1|1|100|5" />'
           '<item data="20261106|1|1|1|null|null" /><item data="20261109|1|1|1|101|6" />'
           '</chartdata></protocol>')
    bars = parse_fchart(xml)
    assert [b.day.isoformat() for b in bars] == ["2026-11-05", "2026-11-09"]
    assert bars[-1].close == 101.0 and bars[-1].volume == 6


@pytest.mark.parametrize("xml", [
    "not xml",
    "<protocol><chartdata></chartdata></protocol>",
    '<protocol><chartdata><item data="20261105|1|1" /></chartdata></protocol>',
    '<protocol><chartdata><item data="2026-11-05|1|1|1|100|5" /></chartdata></protocol>',
    '<protocol><chartdata><item data="20261105|1|1|1|abc|5" /></chartdata></protocol>',
    '<protocol><chartdata><item data="20261106|1|1|1|100|5" /><item data="20261105|1|1|1|100|5" />'
    '</chartdata></protocol>',
])
def test_parse_fchart_format_errors(xml):
    with pytest.raises(SleeveSkip) as ei:
        parse_fchart(xml)
    assert ei.value.code == SOURCE_FORMAT


def _bars(days, closes):
    return [Bar(d, c, 100) for d, c in zip(days, closes, strict=True)]


def test_completed_bars_excludes_today_during_session():
    days = [date(2026, 11, 6), date(2026, 11, 9)]
    bars = _bars(days, [100, 101])
    intraday = datetime(2026, 11, 9, 11, 30, tzinfo=KST)
    after = datetime(2026, 11, 9, 15, 45, tzinfo=KST)
    assert [b.day for b in completed_bars(bars, intraday)] == [date(2026, 11, 6)]
    assert [b.day for b in completed_bars(bars, after)] == days


def test_kr_market_closed_detection():
    bars = _bars([date(2026, 11, 6)], [100])
    assert is_kr_market_closed_today(bars, datetime(2026, 11, 9, 11, 0, tzinfo=KST)) is True
    assert is_kr_market_closed_today(bars, datetime(2026, 11, 9, 7, 0, tzinfo=KST)) is False
    open_bars = _bars([date(2026, 11, 6), date(2026, 11, 9)], [100, 101])
    assert is_kr_market_closed_today(open_bars, datetime(2026, 11, 9, 11, 0, tzinfo=KST)) is False


def test_build_quote_uses_previous_close_intraday():
    days = _weekdays_until(date(2026, 11, 9), 25)
    bars = _bars(days, [100 + i for i in range(25)])
    q = build_quote("161510", bars, datetime(2026, 11, 9, 11, 0, tzinfo=KST))
    assert q.as_of == "2026-11-06" and q.close == 123 and q.prev_close == 122
    assert q.sma5 is not None and q.sma20 is not None


@pytest.mark.parametrize("closes,code", [
    ([100, 131], PRICE_OUT_OF_RANGE),
    ([100, 69], PRICE_OUT_OF_RANGE),
    ([100, 0], PRICE_OUT_OF_RANGE),
])
def test_build_quote_price_limits(closes, code):
    bars = _bars([date(2026, 11, 5), date(2026, 11, 6)], closes)
    with pytest.raises(SleeveSkip) as ei:
        build_quote("161510", bars, datetime(2026, 11, 9, 11, 0, tzinfo=KST))
    assert ei.value.code == code


def test_build_quote_stale_and_insufficient():
    old = _bars([date(2026, 10, 20), date(2026, 10, 21)], [100, 101])
    with pytest.raises(SleeveSkip) as ei:
        build_quote("161510", old, datetime(2026, 11, 9, 11, 0, tzinfo=KST))
    assert ei.value.code == SOURCE_STALE
    one = _bars([date(2026, 11, 6)], [100])
    with pytest.raises(SleeveSkip) as ei:
        build_quote("161510", one, datetime(2026, 11, 9, 11, 0, tzinfo=KST))
    assert ei.value.code == SOURCE_INSUFFICIENT


def test_collect_one_etf_failure_skips_all():
    ok = _http_ok(date(2026, 11, 6))

    def get(url, params=None, headers=None, timeout=None):
        if params["symbol"] == "0060H0":
            return _Resp(500, "")
        return ok(url, params=params)
    with pytest.raises(SleeveSkip) as ei:
        collect(datetime(2026, 11, 9, 2, 0, tzinfo=UTC), http_get=get)
    assert ei.value.code == SOURCE_HTTP


def test_collect_request_exception():
    import requests

    def get(*a, **k):
        raise requests.ConnectionError("x")
    with pytest.raises(SleeveSkip) as ei:
        collect(datetime(2026, 11, 9, 2, 0, tzinfo=UTC), http_get=get)
    assert ei.value.code == SOURCE_HTTP


# ── T-06 공개 문구 ──────────────────────────────────────────
_FORBIDDEN_PUBLIC = ("161510", "0060H0", "PLUS", "TIGER", "타이거", "플러스")


def _ctx(regime: str, k: int = 20) -> dict:
    us = _template_alloc(regime)
    q = {"161510": {"as_of": "2026-11-06", "close": 21965.0, "change_pct": 0.3},
         "0060H0": {"as_of": "2026-11-06", "close": 11855.0, "change_pct": -0.4}}
    return {"result": engine.compute(us, k), "quotes": q, "regime": regime, "risk_level": "LOW",
            "us_session": "11/06"}


@pytest.mark.parametrize("regime", ALL_REGIMES)
def test_public_texts_contract(regime):
    ctx = _ctx(regime)
    for seed in range(30):
        x = formatter.format_x(ctx, random.Random(seed))
        assert formatter.x_weighted_length(x) <= 280
        assert formatter.DISCLAIMER in x
    texts = [formatter.format_telegram(ctx), formatter.format_facebook(ctx),
             formatter.format_x(ctx, random.Random(1))]
    for t in texts:
        assert "P**S" in t and "Ti**ER" in t
        assert formatter.DISCLAIMER in t
        for word in _FORBIDDEN_PUBLIC:
            assert word not in t, (word, t)


def test_x_shows_kr_only_and_k():
    x = formatter.format_x(_ctx("Risk-On"), random.Random(0))
    assert "슬리브 20%" in x and "P**S 고배당주 10%" in x and "Ti**ER 토탈월드스탁액티브 10%" in x
    assert "QQQM" not in x


def test_telegram_has_total_lookthrough_and_dates():
    tg = formatter.format_telegram(_ctx("Risk-On"))
    assert "QQQM 28 · XLK 20 · SPYM 16 · XLE 8 · ITA 4 · TLT 4" in tg
    assert "P**S 10 · Ti**ER 10" in tg
    assert "실효 미국 주식 노출 ≈ 82%" in tg
    assert "미국 11/06 마감" in tg and "국내 가격: 11/06 종가" in tg
    assert "원화 평가 기준" in tg


def test_us_session_label():
    # KST 11-09(월) 10:11 = ET 11-08(일) 20:11 → 직전 정규장 11/06(금)
    assert formatter.us_session_label(datetime(2026, 11, 9, 1, 11, tzinfo=UTC)) == "11/06"
    # KST 11-10(화) 10:00 = ET 11-09(월) 20:00 → 11/09
    assert formatter.us_session_label(datetime(2026, 11, 10, 1, 0, tzinfo=UTC)) == "11/09"


def test_operator_text_has_codes():
    t = formatter.format_operator("skipped", SOURCE_HTTP, "0060H0 HTTP 500",
                                  {"version": "1.0.0", "mode": "shadow"})
    assert "0060H0" in t and "SOURCE_HTTP" in t


# ── run(): T-07·T-08·T-09 ───────────────────────────────────
def _write_fixture(tmp_path, ts: datetime | None = None) -> pathlib.Path:
    p = tmp_path / "core_data.json"
    env = _envelope()
    if ts is not None:
        env["timestamp"] = ts.isoformat()
    p.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    return p


def _run(tmp_path, mode, *, now=NOW_FIX, store=None, pub=None, http=None, ts=None):
    core = _write_fixture(tmp_path, ts)
    out = tmp_path / "kr_sleeve.json"
    pub = pub or FakePub()
    sleeps = []
    code, res = kr_run.run(str(core), str(out), now=now, mode=mode,
                           http_get=http or _http_ok(date(2026, 10, 2)),
                           store_factory=(lambda: store) if store else None, pub=pub,
                           sleep=sleeps.append, rng=random.Random(0))
    return code, res, pub, out, core, sleeps


def test_run_dry_run_no_db_no_publish_and_core_data_unchanged(tmp_path):
    core = tmp_path / "core_data.json"
    core.write_bytes(FIXTURE.read_bytes())
    before = hashlib.sha256(core.read_bytes()).hexdigest()
    pub = FakePub()
    code, res = kr_run.run(str(core), str(tmp_path / "o.json"), now=NOW_FIX, mode="dry_run",
                           http_get=_http_ok(date(2026, 10, 2)),
                           store_factory=lambda: pytest.fail("dry_run에서 DB 접근"), pub=pub)
    assert code == 0 and res["status"] == "computed"
    assert pub.calls == [] and pub.notes == []
    assert hashlib.sha256(core.read_bytes()).hexdigest() == before
    saved = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert saved["result"]["total_allocation"]["161510"] == 10
    assert saved["publish"]["allowed"] is False
    assert saved["run_date"] == "2026-10-05"


def test_run_shadow_records_without_publish(tmp_path):
    store = FakeStore()
    code, res, pub, *_ = _run(tmp_path, "shadow", store=store)
    assert code == 0 and res["status"] == "computed"
    assert len(store.daily) == 1 and store.daily[0]["mode"] == "shadow"
    assert pub.calls == []


def test_run_live_publishes_in_order_with_claims(tmp_path):
    store = FakeStore()
    code, res, pub, _, _, sleeps = _run(tmp_path, "live", store=store)
    assert code == 0 and res["status"] == "published"
    assert pub.calls == ["telegram", "facebook", "x"]
    claims = [c for c in store.calls if c[0] == "claim"]
    assert [c[1] for c in claims] == ["telegram", "facebook", "x"]
    assert len(sleeps) == 1 and config.X_DELAY_RANGE_SEC[0] <= sleeps[0] <= config.X_DELAY_RANGE_SEC[1]


def test_run_live_window_exceeded_blocks_publish(tmp_path):
    late = datetime(2026, 10, 4, 20, 20, 14, tzinfo=UTC) + timedelta(minutes=61)
    store = FakeStore()
    code, res, pub, *_ = _run(tmp_path, "live", store=store, now=late)
    assert res["status"] == "computed" and pub.calls == []
    assert res["publish"]["blocked_reason"].startswith("PUBLISH_WINDOW_EXCEEDED")


def test_run_live_kr_holiday_blocks_publish(tmp_path):
    # KST 10-05(월) 11:00 인데 오늘 봉이 없음 → 국내 휴장으로 보고 발행 생략(기록은 함)
    now = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
    store = FakeStore()
    code, res, pub, *_ = _run(tmp_path, "live", store=store, now=now, ts=now - timedelta(minutes=10))
    assert res["status"] == "computed" and res["kr_market_closed"] is True
    assert pub.calls == [] and res["publish"]["blocked_reason"] == "KR_MARKET_CLOSED"


def test_run_live_claim_failure_skips_that_channel(tmp_path):
    store = FakeStore(fail_claim={"facebook"})
    code, res, pub, *_ = _run(tmp_path, "live", store=store)
    assert pub.calls == ["telegram", "x"]
    assert res["publish"]["channels"]["facebook"]["status"] == "failed"
    assert pub.notes, "발행 문제는 운영자 방에 알려야 한다"


def test_run_live_unknown_result_notifies_operator(tmp_path):
    store = FakeStore()
    pub = FakePub(result="unknown")
    code, res, pub, *_ = _run(tmp_path, "live", store=store, pub=pub)
    assert res["status"] == "computed"
    assert pub.notes and "PUBLISH_FAILED" in pub.notes[-1]


def test_run_already_done_is_benign(tmp_path):
    store = FakeStore(done=True)
    code, res, pub, *_ = _run(tmp_path, "shadow", store=store)
    assert code == 0 and res["reason"] == ALREADY_DONE
    assert pub.notes == [] and store.daily == []


def test_run_source_failure_records_and_notifies(tmp_path):
    store = FakeStore()

    def bad(*a, **k):
        return _Resp(503, "")
    code, res, pub, *_ = _run(tmp_path, "shadow", store=store, http=bad)
    assert code == 0 and res["status"] == "skipped" and res["reason"] == SOURCE_HTTP
    assert store.daily and store.daily[0]["status"] == "skipped"
    assert pub.notes


def test_run_db_failure_exit_1(tmp_path):
    store = FakeStore(fail_upsert=True)
    code, res, pub, *_ = _run(tmp_path, "shadow", store=store)
    assert code == 1 and res["reason"] == "DB_WRITE_FAILED"


def test_run_input_stale_dry_run_no_notify(tmp_path):
    late = NOW_FIX + timedelta(hours=5)
    code, res, pub, out, *_ = _run(tmp_path, "dry_run", now=late)
    assert res["reason"] == INPUT_STALE and pub.notes == []
    assert out.exists()


# ── publisher 이중 잠금 ──────────────────────────────────────
def test_publisher_global_dry_run_blocks_all(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    sender = mock.Mock()
    assert publisher.publish_telegram("t", sender=sender)["status"] == "dry_run"
    assert publisher.publish_x("t", client_factory=mock.Mock())["status"] == "dry_run"
    assert publisher.publish_facebook("t", poster=sender)["status"] == "dry_run"
    sender.assert_not_called()


def test_publisher_x_status_mapping(monkeypatch):
    import tweepy
    monkeypatch.setenv("DRY_RUN", "false")

    def factory_raising(exc):
        client = mock.Mock()
        client.create_tweet.side_effect = exc
        return lambda: client

    resp403 = mock.Mock(status_code=403, reason="Forbidden", json=lambda: {})
    resp503 = mock.Mock(status_code=503, reason="Unavailable", json=lambda: {})
    assert publisher.publish_x("t", factory_raising(tweepy.errors.Forbidden(resp403)))["status"] == "failed"
    res503 = publisher.publish_x("t", factory_raising(tweepy.errors.HTTPException(resp503)))
    assert res503["status"] == "unknown"
    assert publisher.publish_x("t", factory_raising(TimeoutError()))["status"] == "unknown"
    ok = mock.Mock()
    ok.create_tweet.return_value = mock.Mock(data={"id": "123"})
    assert publisher.publish_x("t", lambda: ok) == {"status": "ok", "post_id": "123", "reason": None}
    ok.create_tweet.assert_called_once()   # 재시도 없음


def test_publisher_tg_result_mapping(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    assert publisher.publish_telegram("t", sender=lambda *a, **k: [])["status"] == "failed"
    assert publisher.publish_telegram(
        "t", sender=lambda *a, **k: [{"ok": True, "result": {"message_id": 7}}])["post_id"] == "7"
    assert publisher.publish_telegram(
        "t", sender=lambda *a, **k: [{"ok": False, "error": "x"}])["status"] == "unknown"
    assert publisher.publish_telegram(
        "t", sender=lambda *a, **k: [{"ok": False, "description": "bad"}])["status"] == "failed"


def test_publisher_facebook_disabled(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    import config.settings as s
    monkeypatch.setattr(s, "FACE_ENABLED", False)
    poster = mock.Mock()
    assert publisher.publish_facebook("t", poster=poster)["status"] == "disabled"
    poster.assert_not_called()


# ── T-10 main.yml 계약 ──────────────────────────────────────
EXISTING_JOBS = {"pilot_test", "morning", "intraday", "close", "weekly_thread",
                 "alert_check", "full_dashboard", "narrative"}
EXISTING_CRONS = ["36 21 * * 0-4", "36 2 * * 1-5", "36 9 * * 1-5",
                  "*/10 13-14 * * 1-5", "*/10 15-21 * * 0-4"]


def _main_yml() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8"))


def test_main_yml_contract():
    y = _main_yml()
    on = y.get("on", y.get(True))
    assert [c["cron"] for c in on["schedule"]] == EXISTING_CRONS   # 신규 cron 없음
    jobs = y["jobs"]
    assert set(jobs) == EXISTING_JOBS | {"kr_sleeve"}
    for name in EXISTING_JOBS:
        assert "needs" not in jobs[name]
    kr = jobs["kr_sleeve"]
    assert kr["needs"] == "morning" and kr["if"] == "needs.morning.result == 'success'"
    assert kr["env"]["KR_SLEEVE_MODE"] == ("${{ github.event_name == 'workflow_dispatch' && "
                                           "inputs.kr_sleeve_mode || vars.KR_SLEEVE_MODE || 'dry_run' }}")
    steps_text = json.dumps(kr["steps"], ensure_ascii=False)
    assert "cache/save" not in steps_text and "core-data-" not in steps_text
    assert "morning-${{ github.run_id }}" in steps_text
    opts = on["workflow_dispatch"]["inputs"]["kr_sleeve_mode"]
    assert opts["default"] == "dry_run" and opts["options"] == ["dry_run", "shadow", "live"]


def test_morning_job_still_uploads_core_data_artifact():
    steps = _main_yml()["jobs"]["morning"]["steps"]
    uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert any(s["with"]["name"] == "morning-${{ github.run_id }}"
               and "data/outputs/core_data.json" in s["with"]["path"] for s in uploads)
