"""
tests/test_alert_followup.py — D안 Alert 사후 리포트 (20케이스)
"""
from __future__ import annotations

import sys
import pytest

sys.path.insert(0, ".")

from engines import alert_followup as af  # noqa: E402


def _alert(atype="VIX", level="L2", tweet_id="111", date="2026-08-09"):
    return {
        "id": "a1", "alert_date": date, "alert_type": atype,
        "alert_level": level, "trigger_value": "", "tweet_id": tweet_id,
    }


def _then(vix=35.0, oil=95.0, spy_chg=-3.1, us10y=4.4):
    return {"snapshot_date": "2026-08-09", "vix": vix, "oil_wti": oil,
            "spy_change": spy_chg, "us10y": us10y}


def _now(vix=31.0, oil=98.0, sp500=0.8):
    return {"vix": vix, "oil": oil, "sp500": sp500}


# ─────────────────────────────────────────────────────────────
# _is_valid_tweet_id (6)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", None, "FAIL", "SKIP_X", "EMOTION", "DRY_RUN"])
def test_invalid_ids(bad):
    assert af._is_valid_tweet_id(bad) is False


def test_valid_id():
    assert af._is_valid_tweet_id("1234567890") is True


# ─────────────────────────────────────────────────────────────
# build_followup_text 분기 (9)
# ─────────────────────────────────────────────────────────────

def test_vix_calm():
    text = af.build_followup_text(_alert("VIX"), _then(vix=35.0), _now(vix=30.0))
    assert text is not None
    assert "VIX" in text and "35.0" in text and "30.0" in text
    assert "투자 참고 정보" in text


def test_vix_worse():
    text = af.build_followup_text(_alert("VIX"), _then(vix=30.0), _now(vix=36.0))
    assert text is not None and "+20.0%" in text


def test_vix_flat():
    text = af.build_followup_text(_alert("VIX"), _then(vix=30.0), _now(vix=30.3))
    assert text is not None


def test_oil_branch_uses_oil_values():
    text = af.build_followup_text(
        _alert("OIL_L2"), _then(oil=101.0), _now(oil=97.0)
    )
    assert text is not None and "WTI" in text and "$101.0" in text


def test_spy_branch_uses_change_narrative():
    text = af.build_followup_text(
        _alert("SPY_L2"), _then(spy_chg=-3.1), _now(sp500=0.8)
    )
    assert text is not None and "-3.1%" in text and "+0.8%" in text


def test_missing_then_value_returns_none():
    text = af.build_followup_text(_alert("VIX"), _then(vix=None), _now(vix=30.0))
    assert text is None


def test_missing_now_value_returns_none():
    text = af.build_followup_text(_alert("OIL"), _then(oil=100.0), {"oil": None})
    assert text is None


def test_spy_missing_change_returns_none():
    text = af.build_followup_text(
        _alert("SPY"), _then(spy_chg=None), _now(sp500=0.5)
    )
    assert text is None


def test_other_type_falls_back_to_vix():
    text = af.build_followup_text(
        _alert("FED_SHOCK"), _then(vix=32.0), _now(vix=29.0)
    )
    assert text is not None and "VIX" in text


# ─────────────────────────────────────────────────────────────
# find_followup_targets (3) — _get_client mock
# ─────────────────────────────────────────────────────────────

class _FakeExec:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        def _chain(*a, **k):
            return self
        return _chain

    def execute(self):
        return _FakeExec(self._data)


def test_targets_filters_invalid_tweet_ids(monkeypatch):
    rows = [_alert(tweet_id="EMOTION"), _alert(tweet_id="222")]

    class _Client:
        def table(self, name):
            return _FakeQuery(rows)

    monkeypatch.setattr(af, "_get_client", lambda: _Client())
    targets = af.find_followup_targets()
    assert len(targets) == 1 and targets[0]["tweet_id"] == "222"


def test_targets_caps_at_max(monkeypatch):
    rows = [_alert(tweet_id=str(i)) for i in range(100, 106)]

    class _Client:
        def table(self, name):
            return _FakeQuery(rows)

    monkeypatch.setattr(af, "_get_client", lambda: _Client())
    assert len(af.find_followup_targets()) == af.MAX_PER_RUN


def test_targets_query_failure_returns_empty(monkeypatch):
    class _Client:
        def table(self, name):
            raise RuntimeError("db down")

    monkeypatch.setattr(af, "_get_client", lambda: _Client())
    assert af.find_followup_targets() == []


# ─────────────────────────────────────────────────────────────
# run_followup 가드 (2)
# ─────────────────────────────────────────────────────────────

def test_run_skips_without_snapshot():
    result = af.run_followup({})
    assert result["success"] is True and result["reason"] == "no_snapshot"


def test_run_no_targets(monkeypatch):
    monkeypatch.setattr(af, "find_followup_targets", lambda: [])
    result = af.run_followup(_now())
    assert result["success"] is True and result["processed"] == 0
