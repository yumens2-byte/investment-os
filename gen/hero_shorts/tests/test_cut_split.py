"""tests/test_cut_split.py — 컷 분할 검증 (3편 고정 규격)."""
import pytest

from gen.hero_shorts.cut_split import (
    TOTAL_SEGMENTS,
    split_plan,
    total_duration,
)


def _plan(n=7):
    return [{"cut_no": i, "role": f"R{i}", "prompt": f"p{i}"} for i in range(1, n + 1)]


def test_split_segments_fixed():
    segs = split_plan(_plan())
    assert [s["cut_nos"] for s in segs] == [[1, 2, 3], [4, 5], [6, 7]]
    assert [s["total_duration_sec"] for s in segs] == [30, 20, 20]
    assert TOTAL_SEGMENTS == 3


def test_total_duration_matches_source():
    segs = split_plan(_plan())
    assert total_duration(segs) == 70


def test_series_markers():
    segs = split_plan(_plan())
    assert [s["series_marker"] for s in segs] == ["1/3", "2/3", "3/3"]


def test_reject_non_seven():
    with pytest.raises(ValueError):
        split_plan(_plan(6))
    with pytest.raises(ValueError):
        split_plan(_plan(8))


def test_reject_missing_cut_no():
    plan = _plan()
    plan[3]["cut_no"] = 99  # 4번 결손
    with pytest.raises(ValueError):
        split_plan(plan)


def test_order_preserved():
    segs = split_plan(_plan())
    flat = [c["cut_no"] for s in segs for c in s["cuts"]]
    assert flat == [1, 2, 3, 4, 5, 6, 7]
