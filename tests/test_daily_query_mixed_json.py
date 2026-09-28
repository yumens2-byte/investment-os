"""Existing report readers must accept historical JSON strings and new JSONB objects."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import supabase_query


class Rows:
    def __init__(self, rows):
        self.data = rows


class Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *args):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        return Rows(self.rows)


class Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "daily_analysis"
        return Query(self.rows)


def test_etf_trend_accepts_historical_strings_and_new_objects():
    rows = [
        {"analysis_date": "2026-09-29", "etf_allocation": {"TLT": 60}},
        {"analysis_date": "2026-09-28", "etf_allocation": json.dumps({"TLT": 40})},
    ]
    with patch.object(supabase_query, "_get_client", return_value=Client(rows)):
        result = supabase_query.get_etf_trend(days=2)
    assert result["has_data"]
    assert result["TLT"] == [40, 60]


def test_score_trend_accepts_historical_strings_and_new_objects():
    rows = [
        {"analysis_date": "2026-09-29", "market_score": {"growth_score": 4}},
        {"analysis_date": "2026-09-28", "market_score": json.dumps({"growth_score": 2})},
    ]
    with patch.object(supabase_query, "_get_client", return_value=Client(rows)):
        result = supabase_query.get_score_trend(days=2)
    assert result["has_data"]
    assert result["growth"] == [2, 4]
