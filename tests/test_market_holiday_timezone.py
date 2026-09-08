from datetime import date, datetime
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import us_market_holidays as holidays


def test_default_date_uses_kst_for_monday_morning_workflow():
    # Sunday 21:36 UTC is Monday 06:36 KST.
    with patch.object(holidays, "datetime") as mocked_datetime:
        mocked_datetime.now.return_value = datetime(2026, 9, 14, 6, 36)
        should_skip, reason = holidays.should_skip_market_session()

    assert should_skip is False
    assert reason == ""


def test_explicit_weekend_date_is_still_skipped():
    should_skip, reason = holidays.should_skip_market_session(date(2026, 9, 13))
    assert should_skip is True
    assert "주말" in reason
