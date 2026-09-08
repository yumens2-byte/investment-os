from datetime import datetime, timezone
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import duplicate_checker as dc


DATA = {
    "market_regime": {
        "market_regime": "Transition",
        "market_risk_level": "MEDIUM",
    },
    "etf_analysis": {"etf_rank": {"XLE": 1, "QQQM": 2, "XLK": 3}},
    "output_helpers": {"session_type": "morning"},
}


def _record(text: str, *, day: str, session: str) -> dict:
    return {
        "content_hash": dc._compute_content_hash(text),
        "regime_hash": dc._compute_regime_hash(DATA),
        "publication_date": day,
        "session": session,
    }


def test_same_session_and_kst_day_is_duplicate():
    text = "same publication"
    history = [_record(text, day="2026-09-08", session="morning")]
    with patch.object(dc, "_load_history", return_value=history), patch.object(
        dc, "_publication_date", return_value="2026-09-08"
    ):
        assert dc.is_duplicate(text, DATA, session="morning") is True


def test_previous_trading_day_does_not_block_monday_publication():
    history = [_record("old", day="2026-09-04", session="morning")]
    with patch.object(dc, "_load_history", return_value=history), patch.object(
        dc, "_publication_date", return_value="2026-09-07"
    ):
        assert dc.is_duplicate("new", DATA, session="morning") is False


def test_other_session_does_not_block_same_day():
    history = [_record("same", day="2026-09-08", session="morning")]
    with patch.object(dc, "_load_history", return_value=history), patch.object(
        dc, "_publication_date", return_value="2026-09-08"
    ):
        assert dc.is_duplicate("same", DATA, session="full") is False


def test_legacy_global_record_does_not_block():
    history = [{"content_hash": dc._compute_content_hash("same") }]
    with patch.object(dc, "_load_history", return_value=history):
        assert dc.is_duplicate("same", DATA, session="morning") is False


def test_publication_date_rolls_over_at_kst_midnight():
    sunday_utc = datetime(2026, 9, 6, 21, 36, tzinfo=timezone.utc)
    assert dc._publication_date(sunday_utc) == "2026-09-07"
