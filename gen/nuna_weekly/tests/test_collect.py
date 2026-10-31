"""collect.py 단위 테스트 — 가짜 세션·MemoryStore, 네트워크 없음."""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

import pytest

from gen.nuna_weekly import calendar_gate, config
from gen.nuna_weekly.collect import CollectError, build_facts
from gen.nuna_weekly.db import MemoryStore

# DN2026_0086: 대상 거래일 월~금(10-19~10-23), 토요일(10-24) 생성
BAR_DATES = [date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20),
             date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
CLOSES = {
    "^GSPC": [6000.0, 6010.0, 6070.1, 6040.0, 6100.0, 6120.0, 6150.0],
    "^IXIC": [20000.0, 20100.0, 20200.0, 19900.0, 20300.0, 20400.0, 20500.0],
    "^VIX": [18.0, 17.5, 16.0, 19.0, 17.2, 16.5, 15.9],
    "^TNX": [4.1, 4.12, 4.15, 4.2, 4.18, 4.16, 4.14],
    "CL=F": [70.0, 71.0, 72.5, 71.8, 73.0, 74.0, 75.5],
    "DX-Y.NYB": [99.0, 99.2, 99.5, 99.1, 98.9, 98.7, 98.5],
}


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 13, 30, tzinfo=UTC).timestamp())


def chart(symbol, closes=None, dates=None):
    dates = dates or BAR_DATES
    return {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"},
                                  "timestamp": [_ts(d) for d in dates],
                                  "indicators": {"quote": [{"close": closes or CLOSES[symbol]}]}}]}}


class Resp:
    def __init__(self, body, status=200):
        self._b = body
        self.status_code = status

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, override=None):
        self.override = override or {}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        symbol = url.rsplit("/", 1)[-1]
        self.calls.append({"symbol": symbol, "params": params, "headers": headers, "timeout": timeout})
        o = self.override.get(symbol)
        if o is not None:
            r = o.pop(0) if isinstance(o, list) else o
            if isinstance(r, Exception):
                raise r
            return r
        return Resp(chart(symbol))


def daily():
    return {
        "daily_news": [
            {"news_date": "2026-10-20", "top_headlines": json.dumps(["  Fed  holds ", "Tech rally", "Fed holds"]),
             "top_issues": [{"topic": "Fed", "summary": "금리 동결"}, "유가"]},
            {"news_date": "2026-10-22", "top_headlines": ["Tech rally", "X" * 300, 5, "a", "b", "c", "d"],
             "top_issues": None},
            {"news_date": "2026-10-25", "top_headlines": ["미래 헤드라인"]},
        ],
        "daily_analysis": [{"analysis_date": "2026-10-21", "regime": "Risk-On", "risk_level": "LOW"},
                           {"analysis_date": "2026-10-26", "regime": "Future", "risk_level": "HIGH"}],
        "daily_snapshots": [
            {"snapshot_date": "2026-10-20", "spy_change": 1.0, "nasdaq_change": 0.5, "vix": 16.0,
             "us10y": 4.15, "oil_wti": 72.5, "dollar_index": 99.5, "fear_greed": 55, "fear_greed_label": "Greed",
             "usdkrw": 1400.0},
            {"snapshot_date": "2026-10-21", "spy_change": -0.5, "nasdaq_change": -1.49, "vix": 19.0,
             "us10y": 4.2, "oil_wti": 71.8, "dollar_index": 99.1, "fear_greed": 50, "fear_greed_label": "Neutral",
             "usdkrw": 1410.0},
            {"snapshot_date": "2026-10-22", "spy_change": 1.0, "nasdaq_change": 2.01, "vix": 17.2,
             "us10y": 4.18, "oil_wti": 73.0, "dollar_index": 98.9, "fear_greed": 58, "fear_greed_label": "Greed",
             "usdkrw": 1415.0},
            {"snapshot_date": "2026-10-23", "spy_change": 0.33, "nasdaq_change": 0.49, "vix": 16.5,
             "us10y": 4.16, "oil_wti": 74.0, "dollar_index": 98.7, "fear_greed": 59, "fear_greed_label": "Greed",
             "usdkrw": 1418.0},
            {"snapshot_date": "2026-10-24", "spy_change": 0.49, "nasdaq_change": 0.49, "vix": 15.9,
             "us10y": 4.14, "oil_wti": 75.5, "dollar_index": 98.5, "fear_greed": 60, "fear_greed_label": "Greed",
             "usdkrw": 1420.5},
        ],
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("FORCE_RUN", raising=False)
    monkeypatch.delenv("NUNA_MARKET_SOURCE", raising=False)


NOW = datetime(2026, 10, 24, 6, 0, tzinfo=config.KST)


def test_direct_full():
    gate = calendar_gate.evaluate(date(2026, 10, 24))
    s = FakeSession()
    f = build_facts(gate, MemoryStore(daily()), session=s, now_kst=NOW, sleep=lambda x: None)
    assert f.source == "direct" and f.episode_id == "nuna-2026-W43"
    assert [c["symbol"] for c in s.calls] == list(config.TICKERS.values())
    c0 = s.calls[0]
    assert c0["params"] == {"interval": "1d", "range": "1mo"} and c0["timeout"] == 10
    assert "Chrome/120.0.0.0" in c0["headers"]["User-Agent"]
    mon, tue, wed, thu, fri = f.days
    assert mon.us_date == "2026-10-19" and mon.sp500_close == 6070.1
    assert mon.sp500_chg == round((6070.1 - 6010) / 6010 * 100, 2)
    assert tue.nasdaq_chg == round((19900 - 20200) / 20200 * 100, 2)
    assert wed.vix == 17.2 and wed.vix_chg == round((17.2 - 19) / 19 * 100, 2)
    assert wed.wti == 73.0 and wed.dxy == 98.9 and wed.us10y == 4.18
    assert thu.sp500_close == 6120.0 and fri.sp500_chg == round((6150 - 6120) / 6120 * 100, 2)
    assert f.week_sp500_chg == round((6150 - 6010) / 6010 * 100, 2)
    assert f.week_nasdaq_chg == round((20500 - 20100) / 20100 * 100, 2)
    assert f.regime == "Risk-On" and f.regime_date == "2026-10-21"
    assert f.fear_greed == 60 and f.fear_greed_label == "Greed" and f.usdkrw == 1420.5
    texts = [h["text"] for h in f.headlines]
    assert texts[:2] == ["Fed holds", "Tech rally"]
    assert len(texts) == len(set(texts)) and all(len(t) <= 200 for t in texts)
    assert "미래 헤드라인" not in texts
    # 10-22 행은 상위 5개만(5 는 문자열 아님 → 제외): Tech rally(중복), X*300, a, b
    assert texts[2] == "X" * 200 and texts[-1] == "b"
    assert f.top_issues == ["Fed: 금리 동결", "유가"]
    assert f.fetched_at.startswith("2026-10-24T06:00:00+09:00")


def test_holiday_week_closed_day():
    gate = calendar_gate.evaluate(date(2026, 9, 10))
    dates = [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10),
             date(2026, 9, 11)]
    ov = {sym: Resp(chart(sym, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dates)) for sym in config.TICKERS.values()}
    f = build_facts(gate, MemoryStore(), session=FakeSession(ov), now_kst=NOW, sleep=lambda x: None)
    assert f.days[0].closed and f.days[0].holiday_name == "Labor Day" and f.days[0].sp500_chg is None
    assert f.days[1].sp500_chg == 50.0
    assert f.week_sp500_chg == 200.0  # 9/11 종가 6 vs 9/4 종가 2
    assert f.headlines == [] and f.regime is None and f.fear_greed is None


def test_required_missing_fail_closed():
    gate = calendar_gate.evaluate(date(2026, 10, 24))
    ov = {"^VIX": Resp(chart("^VIX", CLOSES["^VIX"][:6], BAR_DATES[:6]))}
    with pytest.raises(CollectError, match="vix"):
        build_facts(gate, MemoryStore(), session=FakeSession(ov), now_kst=NOW, sleep=lambda x: None)


def test_retry_then_success_and_exhaust():
    gate = calendar_gate.evaluate(date(2026, 10, 24))
    sleeps = []
    ov = {"^GSPC": [Resp({}, 503), ConnectionError("x"), Resp(chart("^GSPC"))]}
    f = build_facts(gate, MemoryStore(), session=FakeSession(ov), now_kst=NOW, sleep=sleeps.append)
    assert f.days[0].sp500_close == 6070.1 and len(sleeps) == 2
    ov = {"^GSPC": [Resp({}, 503)] * 3}
    with pytest.raises(CollectError, match="sp500"):
        build_facts(gate, MemoryStore(), session=FakeSession(ov), now_kst=NOW, sleep=lambda x: None)


def test_shape_error_names_ticker_only():
    gate = calendar_gate.evaluate(date(2026, 10, 24))
    ov = {"^IXIC": Resp({"chart": {"result": None, "secret": "zzz"}})}
    with pytest.raises(CollectError) as ei:
        build_facts(gate, MemoryStore(), session=FakeSession(ov), now_kst=NOW, sleep=lambda x: None)
    assert "nasdaq" in str(ei.value) and "zzz" not in str(ei.value)


def test_optional_failure_is_none():
    gate = calendar_gate.evaluate(date(2026, 10, 24))
    ov = {"CL=F": [Resp({}, 500)] * 3}
    f = build_facts(gate, MemoryStore(), session=FakeSession(ov), now_kst=NOW, sleep=lambda x: None)
    assert all(d.wti is None for d in f.days) and f.days[0].sp500_chg is not None


def test_supabase_mode(monkeypatch, caplog):
    monkeypatch.setenv("NUNA_MARKET_SOURCE", "supabase")
    gate = calendar_gate.evaluate(date(2026, 10, 24))
    with caplog.at_level(logging.WARNING):
        f = build_facts(gate, MemoryStore(daily()), now_kst=NOW)
    assert "신선도" in caplog.text
    assert f.source == "supabase"
    assert f.days[0].sp500_chg == 1.0 and f.days[1].nasdaq_chg == -1.49 and f.days[2].dxy == 98.9
    assert f.days[0].sp500_close is None and f.week_sp500_chg is None


def test_supabase_missing_row(monkeypatch):
    monkeypatch.setenv("NUNA_MARKET_SOURCE", "supabase")
    gate = calendar_gate.evaluate(date(2026, 10, 24))
    d = daily()
    d["daily_snapshots"] = d["daily_snapshots"][:2]
    with pytest.raises(CollectError, match="2026-10-21"):
        build_facts(gate, MemoryStore(d), now_kst=NOW)
