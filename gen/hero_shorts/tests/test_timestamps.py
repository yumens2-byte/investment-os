"""tests/test_timestamps.py — 컷 타임스탬프 기록/경계 검증."""
import json

import pytest

from gen.hero_shorts import timestamps as T
from gen.hero_shorts.tiktok import split_media as SM


def _payload(durs, ep=87):
    cuts = []
    cur = 0.0
    for i, d in enumerate(durs, start=1):
        cuts.append({"cut_no": i, "path": f"c{i}.mp4",
                     "start": round(cur, 3), "end": round(cur + d, 3),
                     "duration_sec": d})
        cur += d
    return {"episode": ep, "cut_count": len(cuts),
            "total_duration_sec": round(cur, 3), "source": "ffprobe", "cuts": cuts}


def test_group_bounds_variable_durations():
    # 컷 길이가 서로 다를 때 경계가 누적 오프셋을 따르는지(균등분할이면 실패)
    p = _payload([8.0, 12.0, 9.5, 11.5, 10.0, 9.87])
    bounds = T.group_bounds(p, ((1, 2), (3, 4), (5, 6)))
    assert bounds == [(0.0, 20.0), (20.0, 41.0), (41.0, 60.87)]
    # 균등 분할 경계(10.145 간격)와 달라야 한다
    assert bounds[0][1] != pytest.approx(60.866667 / 6 * 2, abs=0.01)


def test_group_bounds_rejects_missing_cut():
    p = _payload([10.0, 10.0])
    with pytest.raises(ValueError):
        T.group_bounds(p, ((1, 2), (3, 4)))


def test_group_bounds_single_cut_group():
    p = _payload([7.0, 8.0, 9.0])
    assert T.group_bounds(p, ((1, 1), (2, 3))) == [(0.0, 7.0), (7.0, 24.0)]


def test_record_rejects_empty():
    with pytest.raises(ValueError):
        T.record_cut_timestamps([], "/tmp/x.json")


def test_roundtrip(tmp_path):
    p = _payload([10.0, 10.5])
    f = tmp_path / "ts.json"
    f.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    assert T.load_timestamps(str(f))["cut_count"] == 2


def test_split_media_signature_has_precise_path():
    # 정밀 경로가 존재하고, 폴백 경로는 method 로 구분되는지
    assert hasattr(SM, "split_by_timestamps")
    assert hasattr(SM, "split_by_groups")
    assert SM.VERSION >= "1.1.0"
