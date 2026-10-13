"""tests/test_backdata_score.py — 백데이터 사후 검증 채점 검증."""
import pytest

from gen.hero_shorts.backdata_score import (
    HIT,
    MISS,
    UNSCORED,
    aggregate,
    observed_direction,
    record_call,
    score_call,
    score_horizon,
)


def test_record_call_valid():
    c = record_call("2026-10-13", "risk_on", 72, 7800.0)
    assert c["expected_direction"] == "up"
    assert c["symbol"] == "^GSPC"


def test_record_call_bad_label():
    with pytest.raises(ValueError):
        record_call("2026-10-13", "bullish", 72, 7800.0)


def test_record_call_bad_close():
    with pytest.raises(ValueError):
        record_call("2026-10-13", "risk_on", 72, 0)


def test_observed_direction_bands():
    assert observed_direction(1.0) == "up"
    assert observed_direction(-1.0) == "down"
    assert observed_direction(0.2) == "flat"
    assert observed_direction(0.5) == "flat"    # 경계 포함
    assert observed_direction(-0.5) == "flat"   # 경계 포함


def test_score_hit_and_miss():
    c = record_call("2026-10-13", "risk_on", 80, 100.0)
    assert score_horizon(c, 1, 101.0)["verdict"] == HIT
    assert score_horizon(c, 1, 99.0)["verdict"] == MISS
    assert score_horizon(c, 1, 100.2)["verdict"] == MISS  # flat 밴드 내 → miss


def test_score_unscored_on_missing_close():
    c = record_call("2026-10-13", "risk_off", 30, 100.0)
    v = score_horizon(c, 5, None)
    assert v["verdict"] == UNSCORED
    assert score_horizon(c, 5, 90.0)["verdict"] == HIT


def test_reject_bad_horizon():
    c = record_call("2026-10-13", "risk_on", 80, 100.0)
    with pytest.raises(ValueError):
        score_horizon(c, 3, 101.0)


def test_neutral_label():
    c = record_call("2026-10-13", "neutral", 50, 100.0)
    assert score_horizon(c, 1, 100.3)["verdict"] == HIT
    assert score_horizon(c, 1, 105.0)["verdict"] == MISS


def test_score_call_and_aggregate():
    c = record_call("2026-10-13", "risk_on", 80, 100.0)
    rec = score_call(c, {1: 101.0, 5: 102.0, 20: 98.0})
    assert rec["scored_count"] == 3
    assert rec["hit_count"] == 2  # up/up/down vs expected up
    card = aggregate([rec])
    assert card["overall"]["scored"] == 3
    assert card["overall"]["hit"] == 2
    assert card["per_horizon"][20]["hit"] == 0
    assert card["unscored"] == 0


def test_aggregate_with_unscored():
    c = record_call("2026-10-13", "neutral", 50, 100.0)
    rec = score_call(c, {1: 100.1, 5: None, 20: None})
    card = aggregate([rec])
    assert card["unscored"] == 2
    assert card["overall"]["scored"] == 1
    assert card["overall"]["hit"] == 1
