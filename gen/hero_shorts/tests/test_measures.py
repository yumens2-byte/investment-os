"""tests/test_measures.py — 컷 실측 길이 영속화(생성 시점) + 타임스탬프 연동 검증."""
import json
import subprocess

import pytest

from gen.hero_shorts import generator as G
from gen.hero_shorts import timestamps as T


def _make_clip(path, dur):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=720x1280:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
         "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def test_validate_output_persists_measures(tmp_path):
    clip = tmp_path / "cut1.mp4"
    _make_clip(clip, 8)
    m = G._validate_output(clip, 10, backend="fal")
    assert m["duration_sec"] == pytest.approx(8.0, abs=0.2)
    assert m["requested_duration_sec"] == 10
    assert m["backend"] == "fal"
    # 사이드카가 실제로 디스크에 존재해야 한다(핵심 회귀 방지)
    mp = G._measures_path(clip)
    assert mp.exists()
    on_disk = json.loads(mp.read_text(encoding="utf-8"))
    assert on_disk["duration_sec"] == m["duration_sec"]
    assert on_disk["delta_sec"] == m["delta_sec"]


def test_validate_output_returns_and_still_raises_on_bad(tmp_path):
    clip = tmp_path / "tiny.mp4"
    _make_clip(clip, 1)
    with pytest.raises(RuntimeError):
        G._validate_output(clip, 10, backend="fal")
    # 실패해도 기록은 남는다(과금분 확인용 증적)
    assert G._measures_path(clip).exists()


def test_read_measures_absent_returns_none(tmp_path):
    clip = tmp_path / "nope.mp4"
    _make_clip(clip, 3)
    assert T.read_measures(clip) is None  # 사이드카 없음 → 추측하지 않고 None


def test_record_prefers_generated_measures(tmp_path):
    """생성 시점 기록이 있으면 그 값을 채택하고 source 를 fal_measures 로 남긴다."""
    clip = tmp_path / "cut1.mp4"
    _make_clip(clip, 8)
    G._validate_output(clip, 10, backend="fal")
    payload = T.record_cut_timestamps([str(clip)], tmp_path / "ts.json", episode=1)
    cut = payload["cuts"][0]
    assert cut["source"] == "fal_measures"
    assert payload["source"] == "fal_measures"
    assert cut["drift_sec"] is not None
    assert cut["duration_sec"] == pytest.approx(8.0, abs=0.2)


def test_record_falls_back_to_ffprobe(tmp_path):
    clip = tmp_path / "cut1.mp4"
    _make_clip(clip, 5)
    payload = T.record_cut_timestamps([str(clip)], tmp_path / "ts.json", episode=1)
    assert payload["cuts"][0]["source"] == "ffprobe"
    assert payload["cuts"][0]["drift_sec"] is None


def test_record_mixed_sources(tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    _make_clip(a, 6)
    _make_clip(b, 7)
    G._validate_output(a, 6, backend="fal")  # a 만 기록 보유
    payload = T.record_cut_timestamps([str(a), str(b)], tmp_path / "ts.json")
    assert payload["source"] == "mixed"
    assert payload["sources"] == ["fal_measures", "ffprobe"]
    assert payload["total_duration_sec"] == pytest.approx(13.0, abs=0.5)


def test_measures_path_convention_matches():
    """generator 와 timestamps 의 사이드카 경로 규약이 일치해야 한다."""
    p = "/x/ep87_cut1.mp4"
    assert str(G._measures_path(p)) == str(T.measures_path(p))
