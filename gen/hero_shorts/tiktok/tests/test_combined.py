"""tests/test_combined.py — 합본(단일 편) 발행 경로 검증.

마스터 지침(2026-10-13): 정기 발행은 3편 분할이 아니라 전편 합본 1편으로 올린다.
60초 초과는 무관 — 상한은 플랫폼 한도(TikTok/Zernio 3초~10분).
"""
import pytest

from gen.hero_shorts.tiktok import caption as tk_caption
from gen.hero_shorts.tiktok import manifest as tk_manifest

TITLE = "물러서지 않는 두 전선"


def _combined(dur, **kw):
    return tk_manifest.from_combined("out/ep.mp4", dur, TITLE, **kw)


def test_combined_single_segment():
    m = _combined(60.86)
    assert m["mode"] == "combined"
    assert m["segment_count"] == 1
    assert m["segments"][0]["segment_no"] == 1
    assert m["segments"][0]["start"] == 0.0


def test_combined_over_60s_passes():
    """60초 초과 무관 — 상한은 600초(플랫폼 한도)."""
    m = _combined(60.86)
    assert m["all_gates_ok"] is True
    assert tk_manifest.segment_gate_ok(m["segments"][0]) is True


def test_combined_upper_bound_blocked():
    m = tk_manifest.from_combined("out/ep.mp4", 700.0, TITLE, max_duration_sec=600)
    assert m["all_gates_ok"] is False
    assert m["segments"][0]["gates"]["ok"] is False


def test_combined_boundary_at_600_passes():
    m = tk_manifest.from_combined("out/ep.mp4", 600.0, TITLE, max_duration_sec=600)
    assert m["all_gates_ok"] is True


def test_combined_caption_has_no_series_marker():
    cap = _combined(60.86)["segments"][0]["caption"]
    assert "/3" not in cap
    assert "이어집니다" not in cap
    assert "#JCUniverse" in cap
    assert "투자 권유 아님" in cap


def test_combined_invalid_duration():
    with pytest.raises(ValueError):
        tk_manifest.from_combined("out/ep.mp4", 0, TITLE)


def test_combined_requires_source():
    with pytest.raises(ValueError):
        tk_manifest.from_combined(None, 60.0, TITLE)


def test_combined_caption_deterministic():
    assert tk_caption.build_combined_caption(TITLE) == tk_caption.build_combined_caption(TITLE)
    assert "1/3" not in tk_caption.build_combined_caption(TITLE)


def test_combined_media_url_without_local_video():
    m = tk_manifest.from_combined(None, 60.0, "미디어URL 합본",
                                  media_url="https://example.com/v.mp4")
    assert m["mode"] == "combined"
    assert m["segments"][0]["media_url"] == "https://example.com/v.mp4"
    assert m["segments"][0]["video_path"] is None
    assert m["all_gates_ok"] is True
