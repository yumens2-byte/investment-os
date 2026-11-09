"""
tests/test_gold_value.py — 금으로 잰 화폐가치 패널 (DN2026_0109). 외부 호출 없음(STD-TEST-14).

T-G01 계산식 · G02 월평균 최소 거래일 · G03 확정값(오늘 행 제외) · G04 환율 공백 · G05 의심값 제외
G06 '비슷'·원 위치 문장 · G07 금지 표현·종목 노출 · G08 패널 실패 시 본 문구 바이트 동일
G09 백필 상한·종료 · G10 HTTP·형식 사유 코드 · G11 dry_run·shadow 발행 0 · G12 store 경계·main.yml
"""
from __future__ import annotations

import json
import pathlib
import random
from datetime import UTC, date, datetime, timedelta

import pytest
import yaml

from kr_sleeve import config, formatter, gold_value
from kr_sleeve import run as kr_run
from kr_sleeve.gold_value import (
    GC_GOLD,
    GOLD_DB,
    GOLD_INSUFFICIENT,
    GOLD_SOURCE_FORMAT,
    GOLD_SOURCE_HTTP,
    GOLD_STALE,
    JPYKRW,
    KRX_GOLD,
    USDKRW,
    GoldSkip,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)          # KST 11-09(월) 11:00
FORBIDDEN = ("화폐 폭락", "원화는 휴지", "금으로 사라", "집값")


def _weekdays(start: date, end: date) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _const(days: list[str], v: float) -> dict[str, float]:
    return {d: v for d in days}


# PoC 42577605104 월평균 실측값(2021-10 · 2026-10)과 1개월 국제 금 실측값
M0 = _weekdays(date(2021, 10, 1), date(2021, 10, 29))
M1 = _weekdays(date(2026, 10, 1), date(2026, 10, 30))
RECENT = _weekdays(date(2026, 10, 1), date(2026, 11, 6))


def _hist(**over) -> dict[str, dict]:
    krx = {**_const(M0, 67633.68421052632), **_const(RECENT, 176704.0)}
    usd = {**_const(M0, 1182.4736842105262), **_const(RECENT, 1337.24)}
    jpy = {**_const(M0, 1044.1473684210525), **_const(RECENT, 846.7625)}
    gc = {d: 4140.7 for d in RECENT if d <= "2026-10-07"} | {d: 4074.9 for d in RECENT if d > "2026-10-07"}
    h = {KRX_GOLD.key: krx, USDKRW.key: usd, JPYKRW.key: jpy, GC_GOLD.key: gc}
    h.update(over)
    return h


# ── T-G01 계산식 ─────────────────────────────────────────────
def test_compute_reproduces_poc_monthly():
    p = gold_value.compute(_hist(), NOW)
    lg = p["long"]
    assert (lg["from_month"], lg["to_month"]) == ("2021-10", "2026-10")
    assert lg["grams"]["krw"] == {"then": 1478.55, "now": 565.92, "pct": -61.72}
    assert lg["grams"]["usd"]["pct"] == -56.72 and lg["grams"]["jpy"]["pct"] == -68.96
    sh = p["short"]
    assert (sh["from"], sh["to"]) == ("2026-10-07", "2026-11-06")
    assert sh["gold_usd_pct"] == -1.59
    # 환율이 고정이면 세 통화 모두 금값 변화의 역수만큼 움직인다
    assert sh["value_pct"]["usd"] == round((4140.7 / 4074.9 - 1) * 100, 2)


def test_grams_formulas():
    d = gold_value.grams_domestic(100_000.0, 1300.0, 900.0)
    assert d == {"krw": 1000.0, "usd": 130.0, "jpy": 90.0}
    i = gold_value.grams_intl(3110.34768, 1300.0, 900.0)
    assert round(i["usd"], 6) == 100.0
    assert round(i["krw"], 4) == round(1e8 / (3110.34768 * 1300.0) * config.OZ_G, 4)


# ── T-G02 월평균 최소 거래일 ─────────────────────────────────
def test_month_with_too_few_days_skips():
    thin = {**_const(M0[:9], 67633.0), **_const(RECENT, 176704.0)}
    with pytest.raises(GoldSkip) as e:
        gold_value.compute(_hist(**{KRX_GOLD.key: thin}), NOW)
    assert e.value.code == GOLD_INSUFFICIENT


def test_month_window_fixed_rule():
    assert gold_value.month_window(datetime(2026, 1, 3, tzinfo=UTC)) == ("2025-12", "2020-12")
    assert gold_value.month_window(NOW) == ("2026-10", "2021-10")


# ── T-G03 확정값 ─────────────────────────────────────────────
def test_confirmed_excludes_today_kst_and_ct():
    assert gold_value._confirmed(KRX_GOLD, "2026-11-09T00:00:00+09:00", NOW) is None
    assert gold_value._confirmed(KRX_GOLD, "2026-11-06T00:00:00+09:00", NOW) == "2026-11-06"
    assert gold_value._confirmed(USDKRW, "2026-11-09", NOW) is None
    # NOW = 시카고 11-08(일) 20:00 → 11-08 행은 진행 중
    assert gold_value._confirmed(GC_GOLD, "2026-11-08T20:00:00-06:00", NOW) is None
    assert gold_value._confirmed(GC_GOLD, "2026-11-06T16:00:00-06:00", NOW) == "2026-11-06"


def test_stale_series_skips():
    old = {d: v for d, v in _hist()[GC_GOLD.key].items() if d <= "2026-10-20"}
    with pytest.raises(GoldSkip) as e:
        gold_value.compute(_hist(**{GC_GOLD.key: old}), NOW)
    assert e.value.code == GOLD_STALE


# ── T-G04 환율 공백 ──────────────────────────────────────────
def test_fx_gap_over_limit_skips():
    usd = {d: v for d, v in _hist()[USDKRW.key].items() if not ("2026-10-01" <= d <= "2026-10-07")}
    with pytest.raises(GoldSkip) as e:
        gold_value.compute(_hist(**{USDKRW.key: usd}), NOW)
    assert e.value.code == GOLD_INSUFFICIENT


# ── T-G05 의심값 제외 ────────────────────────────────────────
def test_clean_range_and_spike():
    rows = {"2026-11-02": 170_000.0, "2026-11-03": 9_000.0, "2026-11-04": 230_000.0, "2026-11-05": 171_000.0}
    assert gold_value.clean(KRX_GOLD, rows) == {"2026-11-02": 170_000.0, "2026-11-05": 171_000.0}


def test_clean_keeps_level_shift_and_far_apart_months():
    shift = {"2026-11-02": 100_000.0, "2026-11-03": 120_000.0, "2026-11-04": 121_000.0}
    assert gold_value.clean(KRX_GOLD, shift) == shift
    far = {"2021-10-29": 67_000.0, "2026-10-01": 176_000.0, "2026-10-02": 177_000.0}
    assert gold_value.clean(KRX_GOLD, far) == far


def test_clean_drops_latest_outlier():
    rows = {"2026-11-05": 170_000.0, "2026-11-06": 250_000.0}
    assert gold_value.clean(KRX_GOLD, rows) == {"2026-11-05": 170_000.0}


def test_jump_warning_in_window():
    gc = dict(_hist()[GC_GOLD.key])
    gc["2026-10-20"] = 4074.9 * 0.9
    p = gold_value.compute(_hist(**{GC_GOLD.key: gc}), NOW)
    assert p["short"]["jump_warning"]


# ── T-G06 표기 ───────────────────────────────────────────────
@pytest.mark.parametrize("v,expected", [
    ({"usd": 1.61, "jpy": 1.35, "krw": -0.06}, "원은 달러·엔보다 약했습니다"),
    ({"usd": -1.0, "jpy": -2.0, "krw": 0.5}, "원은 달러·엔보다 강했습니다"),
    ({"usd": 1.0, "jpy": 1.1, "krw": 1.05}, "원은 달러·엔과 비슷했습니다"),
    ({"usd": 1.0, "jpy": 3.0, "krw": 1.1}, "원은 달러와 비슷했고 엔보다 약했습니다"),
    ({"usd": 3.0, "jpy": -1.0, "krw": 1.0}, "원은 달러보다 약했고 엔보다 강했습니다"),
])
def test_won_position(v, expected):
    assert gold_value.won_position(v) == expected


def test_fmt_pct_one_decimal_minus_sign():
    assert gold_value.fmt_pct(-61.72) == "−61.7%"
    assert gold_value.fmt_pct(1.61) == "+1.6%"


def test_panel_texts_match_approved_sample():
    p = gold_value.compute(_hist(), NOW)
    tg = gold_value.panel_telegram(p)
    assert tg[2] == "  5년 전  1,479g" and tg[3] == "  지금    566g   (−61.7%)"
    assert "21년 10월 평균 → 26년 10월 평균" in tg[1]
    assert "+ = 같은 돈으로 살 수 있는 금 증가" in tg[7]
    x = gold_value.panel_x(p)
    assert x[0] == "🪙 금으로 재면 1억 원: 5년 전 1,479g → 지금 566g (−61.7%)"
    assert x[1].startswith("1개월 금 기준 가치: 달러 +1.6% · 엔 +1.6% · 원 +1.6%")


# ── T-G07 금지 표현·종목 노출 ────────────────────────────────
def test_no_forbidden_phrases_or_codes():
    p = gold_value.compute(_hist(), NOW)
    text = "\n".join(gold_value.panel_telegram(p) + gold_value.panel_facebook(p) + gold_value.panel_x(p))
    for w in FORBIDDEN:
        assert w not in text
    for etf in config.KR_ETFS:
        assert etf.code not in text and etf.internal_name not in text


# ── 가짜 네이버 응답 ─────────────────────────────────────────
class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


def _fake_prices(hist: dict[str, dict], calls: list | None = None, today_rows=True):
    by_code = {KRX_GOLD.code: (KRX_GOLD, hist[KRX_GOLD.key]), GC_GOLD.code: (GC_GOLD, hist[GC_GOLD.key]),
               USDKRW.code: (USDKRW, hist[USDKRW.key]), JPYKRW.code: (JPYKRW, hist[JPYKRW.key])}

    def get(url, params=None, headers=None, timeout=None):
        if "fchart" in url:
            from test_kr_sleeve import _http_ok
            return _http_ok(date(2026, 10, 2))(url, params=params, headers=headers, timeout=timeout)
        s, rows = by_code[params["reutersCode"]]
        if calls is not None:
            calls.append((s.key, params["page"]))
        days = sorted(rows, reverse=True)
        if today_rows:
            days = ["2026-11-09", *days]
        size = params["pageSize"]
        chunk = days[(params["page"] - 1) * size: params["page"] * size]
        if not chunk:
            return _Resp(200, json.dumps({"isSuccess": True, "result": []}))

        def ts(d):
            if s.tz == "CT":
                return f"{d}T16:00:00-06:00"
            return f"{d}T00:00:00+09:00" if s.category == "metals" else d
        res = [{"localTradedAt": ts(d), "closePrice": f"{rows.get(d, 1.0):,}"} for d in chunk]
        return _Resp(200, json.dumps({"isSuccess": True, "result": res}))
    return get


def _long_hist() -> dict[str, dict]:
    """60개월 전 월까지 페이지를 넘겨야 닿는 이력(백필 경로)."""
    h = _hist()
    filler = _weekdays(date(2021, 11, 1), date(2026, 9, 30))
    for key, v in ((KRX_GOLD.key, 120_000.0), (USDKRW.key, 1300.0), (JPYKRW.key, 950.0)):
        h[key] = {**h[key], **_const(filler, v)}
    return h


# ── T-G09 백필 ───────────────────────────────────────────────
def test_sync_memory_backfills_until_need_from_and_excludes_today():
    calls, sleeps = [], []
    hist = gold_value.sync_history(NOW, _fake_prices(_long_hist(), calls), None, sleeps.append)
    assert min(hist[KRX_GOLD.key]) <= "2021-10-01"
    assert "2026-11-09" not in hist[KRX_GOLD.key] and "2026-11-09" not in hist[USDKRW.key]
    assert [c for c in calls if c[0] == GC_GOLD.key] == [(GC_GOLD.key, 1)]
    assert max(p for k, p in calls if k == KRX_GOLD.key) <= config.GOLD_BACKFILL_MAX_PAGES
    assert len(sleeps) == len(calls) - 4
    p = gold_value.compute(hist, NOW)
    assert p["long"]["grams"]["krw"]["now"] == 565.92


def test_backfill_page_cap(monkeypatch):
    monkeypatch.setattr(config, "GOLD_BACKFILL_MAX_PAGES", 3)
    calls = []
    gold_value.sync_history(NOW, _fake_prices(_long_hist(), calls), None, lambda s: None)
    assert max(p for k, p in calls if k == KRX_GOLD.key) == 3


def test_backfill_stops_on_empty_or_unsuccessful_page():
    def get(url, params=None, headers=None, timeout=None):
        if params["page"] == 1:
            return _Resp(200, json.dumps({"isSuccess": True, "result": [
                {"localTradedAt": "2026-11-06T00:00:00+09:00", "closePrice": "176,690"}]}))
        return _Resp(200, json.dumps({"isSuccess": False, "result": None}))
    hist = gold_value.sync_history(NOW, get, None, lambda s: None)
    assert hist[KRX_GOLD.key] == {"2026-11-06": 176690.0}


# ── T-G10 사유 코드 ──────────────────────────────────────────
@pytest.mark.parametrize("resp,code", [
    (_Resp(503, ""), GOLD_SOURCE_HTTP),
    (_Resp(200, "<html>"), GOLD_SOURCE_FORMAT),
    (_Resp(200, json.dumps({"isSuccess": False})), GOLD_SOURCE_FORMAT),
    (_Resp(200, json.dumps({"isSuccess": True, "result": [{"closePrice": "1"}]})), GOLD_SOURCE_FORMAT),
])
def test_build_panel_reason_codes(resp, code):
    p = gold_value.build_panel(NOW, lambda *a, **k: resp, None, lambda s: None)
    assert p == {"status": "skipped", "reason": code, "detail": p["detail"]}


def test_build_panel_never_raises():
    def boom(*a, **k):
        raise RuntimeError("x")
    p = gold_value.build_panel(NOW, boom, None, lambda s: None)
    assert p["status"] == "skipped" and p["reason"] == "RuntimeError"


# ── T-G08·G11 run 통합 ───────────────────────────────────────
FIXTURE = ROOT / "tests" / "fixtures" / "core_data_morning_20261005.json"
NOW_FIX = datetime(2026, 10, 4, 20, 30, tzinfo=UTC)


def _run(tmp_path, mode, http, **kw):
    from test_kr_sleeve import FakePub
    core = tmp_path / "core_data.json"
    core.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    pub = FakePub()
    code, res = kr_run.run(str(core), str(tmp_path / "out.json"), now=NOW_FIX, mode=mode, http_get=http,
                           pub=pub, sleep=lambda s: None, rng=random.Random(0), clock=lambda: NOW_FIX, **kw)
    return code, res, pub


def test_run_panel_failure_keeps_texts_byte_identical(tmp_path):
    from test_kr_sleeve import _http_ok
    code, res, pub = _run(tmp_path, "dry_run", _http_ok(date(2026, 10, 2)))
    assert code == 0 and res["status"] == "computed"
    assert res["gold_panel"]["status"] == "skipped"
    ctx = {"result": res["result"], "quotes": res["kr_quotes"], "regime": res["regime"],
           "risk_level": res["risk_level"], "us_session": formatter.us_session_label(
               datetime.fromisoformat(res["input_timestamp"]))}
    assert res["texts"]["telegram"] == formatter.format_telegram(ctx)
    assert res["texts"]["facebook"] == formatter.format_facebook(ctx)
    assert res["texts"]["x"] == formatter.format_x(ctx, random.Random(0))
    assert "금으로" not in json.dumps(res["texts"], ensure_ascii=False)


def test_run_panel_ok_appends_to_all_channels_dry_run_no_publish(tmp_path, monkeypatch):
    # NOW_FIX(2026-10-04) 기준 월 창: 2026-09 vs 2021-09
    monkeypatch.setattr(gold_value, "month_window", lambda now: ("2026-10", "2021-10"))
    monkeypatch.setattr(config, "GOLD_MAX_STALE_DAYS", 400)
    fake = _fake_prices(_long_hist(), today_rows=False)
    monkeypatch.setattr(gold_value, "_confirmed", lambda s, ts, now: ts[:10] if ts[:10] < "2026-11-09" else None)
    code, res, pub = _run(tmp_path, "dry_run", fake, gold_http_get=fake)
    assert code == 0 and res["gold_panel"]["status"] == "ok"
    t = res["texts"]
    assert "🪙 <b>금으로 잰 화폐가치</b>" in t["telegram"] and t["telegram"].endswith(formatter.DISCLAIMER)
    assert t["telegram"].count("투자 권유 아님") == 1
    assert "🪙 금으로 잰 화폐가치" in t["facebook"]
    assert "🪙 금으로 재면 1억 원" in t["x"] and "1개월 금 기준 가치" in t["x"]
    assert pub.calls == [] and pub.notes == []


def test_run_shadow_injected_store_does_not_touch_real_db(tmp_path, monkeypatch):
    from test_kr_sleeve import FakeStore, _http_ok

    def no_db():
        raise AssertionError("GoldStore 생성 금지")
    monkeypatch.setattr(gold_value, "GoldStore", no_db)
    store = FakeStore()
    code, res, pub = _run(tmp_path, "shadow", _http_ok(date(2026, 10, 2)), store_factory=lambda: store)
    assert code == 0 and res["status"] == "computed" and pub.calls == []


def test_gold_store_factory_failure_is_isolated(tmp_path):
    from test_kr_sleeve import _http_ok

    def bad():
        raise RuntimeError("no env")
    code, res, _ = _run(tmp_path, "dry_run", _http_ok(date(2026, 10, 2)), gold_store_factory=bad)
    assert res["status"] == "computed" and res["gold_panel"]["reason"] == GOLD_DB


# ── T-G12 store·main.yml ─────────────────────────────────────
class _Q:
    def __init__(self, log, data=None):
        self.log, self.data = log, data or []

    def __getattr__(self, name):
        def f(*a, **k):
            self.log.append((name, a, k))
            return self
        return f

    def execute(self):
        return type("R", (), {"data": self.data})()


class _Client:
    def __init__(self, data=None):
        self.log, self.data = [], data

    def table(self, name):
        self.log.append(("table", (name,), {}))
        return _Q(self.log, self.data)


def test_gold_store_upsert_and_month_bounds():
    c = _Client()
    gold_value.GoldStore(c).upsert_rows("usdkrw", {"2026-11-06": 1363.5})
    up = [x for x in c.log if x[0] == "upsert"][0]
    assert up[2] == {"on_conflict": "day,series"} and up[1][0][0]["series"] == "usdkrw"
    assert gold_value._month_bounds("2025-12") == ("2025-12-01", "2026-01-01")
    c2 = _Client([{"day": "2021-10-01", "close": "67000"}])
    rows = gold_value.GoldStore(c2).load("krx_gold_krw_g", "2021-10-01", "2026-10")
    assert rows == {"2021-10-01": 67000.0}
    assert ("lt", ("day", "2021-11-01"), {}) in c2.log and ("lt", ("day", "2026-11-01"), {}) in c2.log


def test_sync_with_store_skips_backfill_when_covered():
    class S:
        def __init__(self):
            self.up = []

        def upsert_rows(self, k, rows):
            self.up.append(k)

        def count_month(self, k, m):
            return 20

        def load(self, k, since, m1):
            return _hist()[k]

        def load_recent(self, k, days):
            return {}
    calls = []
    st = S()
    hist = gold_value.sync_history(NOW, _fake_prices(_long_hist(), calls), st, lambda s: None)
    assert all(p == 1 for _, p in calls) and len(calls) == 4
    assert gold_value.compute(hist, NOW)["long"]["grams"]["krw"]["now"] == 565.92


def test_main_yml_runs_gold_tests_and_jobs_unchanged():
    y = yaml.safe_load((ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8"))
    steps = y["jobs"]["pilot_test"]["steps"]
    kr = [s for s in steps if s.get("name") == "KR Sleeve tests"][0]
    assert kr["run"] == "python -m pytest -q tests/test_kr_sleeve.py tests/test_gold_value.py"


def test_schema_sql_matches_series_keys():
    sql = (ROOT / "db" / "gold_value_schema.sql").read_text(encoding="utf-8")
    for s in gold_value.ALL_SERIES:
        assert f"'{s.key}'" in sql
    assert "DISABLE ROW LEVEL SECURITY" in sql
