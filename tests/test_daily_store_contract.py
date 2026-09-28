"""Database payload contracts, without network or writes to the production DB."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import daily_store


class RecordingTable:
    def __init__(self):
        self.rows = []

    def upsert(self, row, on_conflict):
        self.rows.append((row, on_conflict))
        return self

    def execute(self):
        return self


class RecordingClient:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return self.tables.setdefault(name, RecordingTable())


def test_analysis_stores_engine_signal_and_native_json_payload():
    client = RecordingClient()
    data = {
        "market_regime": {"market_regime": "Transition", "market_risk_level": "HIGH"},
        "trading_signal": {"trading_signal": "HEDGE"},
        "etf_allocation": {"allocation": {"TLT": 0.6, "QQQM": 0.4}},
        "market_score": {"risk": 3},
    }
    with patch.object(daily_store, "_get_client", return_value=client):
        assert daily_store.store_daily_analysis(data, regime_score=47)
    row, conflict = client.tables["daily_analysis"].rows[0]
    assert conflict == "analysis_date"
    assert row["trading_signal"] == "HEDGE"
    assert row["etf_rank"] == {"TLT": 1, "QQQM": 2}
    assert row["etf_allocation"] == {"TLT": 0.6, "QQQM": 0.4}
    assert row["market_score"] == {"risk": 3}


def test_analysis_missing_signal_does_not_write_default_hold():
    client = RecordingClient()
    with patch.object(daily_store, "_get_client", return_value=client):
        assert not daily_store.store_daily_analysis({"trading_signal": {}})
    assert client.tables == {}


def test_news_stores_rss_contract_and_native_json_payload():
    client = RecordingClient()
    rss = {
        "news_sentiment": "Bullish",
        "sentiment_score": 1.25,
        "total_headlines": 3,
        "headlines": ["one", "two", "three"],
    }
    data = {"news_summary": {"sentiment": "Neutral", "headline_count": 0},
            "news_analysis": {"top_issues": [{"topic": "rates"}]}}
    with patch.object(daily_store, "_get_client", return_value=client):
        assert daily_store.store_daily_news(data, rss)
    row, conflict = client.tables["daily_news"].rows[0]
    assert conflict == "news_date"
    assert (row["rss_sentiment"], row["rss_score"], row["rss_headline_count"]) == (
        "Bullish", 1.25, 3
    )
    assert row["top_issues"] == [{"topic": "rates"}]
    assert row["top_headlines"] == ["one", "two", "three"]


def test_news_requires_rss_contract_but_accepts_all_sources_failed_result():
    client = RecordingClient()
    with patch.object(daily_store, "_get_client", return_value=client):
        assert not daily_store.store_daily_news({}, None)
        assert daily_store.store_daily_news({}, {
            "news_sentiment": "Neutral", "sentiment_score": 0.0,
            "total_headlines": 0, "headlines": [], "sources_ok": 0,
        })
    assert len(client.tables["daily_news"].rows) == 1
