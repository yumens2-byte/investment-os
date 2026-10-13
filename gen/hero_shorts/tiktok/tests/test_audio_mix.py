"""tests/test_audio_mix.py — BGM 덕킹·효과음 믹싱 검증 (ffmpeg 합성 오디오, 0원)."""
import subprocess

import numpy as np
import pytest

from gen.hero_shorts.tiktok import audio_mix as AM


def _video_with_voice(path, dur=6.0, voice_hz=300):
    """'내레이션' 대역(300Hz) 오디오를 가진 영상 — 틱톡 세그먼트 모사."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=720x1280:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency={voice_hz}:duration={dur}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-c:a", "aac",
         "-shortest", str(path)], check=True)


def _tone(path, dur=6.0, hz=3500):
    """BGM 대역(3500Hz) — 내레이션(300Hz)과 대역이 겹치지 않게 분리해 검출 가능."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency={hz}:duration={dur}",
         "-c:a", "pcm_s16le", str(path)], check=True)


def _band_energy(path, hz, tol=200):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                          "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=False)
    a = np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32)
    if a.size == 0:
        return 0.0
    w = np.hanning(a.size)
    sp = np.abs(np.fft.rfft(a * w))
    f = np.fft.rfftfreq(a.size, 1 / 16000)
    tot = (sp ** 2).sum() + 1e-12
    return float((sp[(f >= hz - tol) & (f <= hz + tol)] ** 2).sum() / tot)


def test_mix_bgm_adds_bgm_band(tmp_path):
    v = tmp_path / "seg.mp4"
    b = tmp_path / "bgm.wav"
    o = tmp_path / "out.mp4"
    _video_with_voice(v)
    _tone(b)
    before = _band_energy(v, 3500)
    AM.mix_bgm(v, b, o)
    after = _band_energy(o, 3500)
    assert after > before + 0.0005, f"BGM 대역 에너지가 안 늘었다: {before} → {after}"


def test_mix_bgm_preserves_duration_and_voice(tmp_path):
    v = tmp_path / "seg.mp4"
    b = tmp_path / "bgm.wav"
    o = tmp_path / "out.mp4"
    _video_with_voice(v, dur=6.0)
    _tone(b, dur=2.0)          # BGM 이 짧아도 loop 로 채워져 길이 보존돼야 함
    AM.mix_bgm(v, b, o)
    assert abs(AM.probe_duration(v) - AM.probe_duration(o)) < 0.6
    assert _band_energy(o, 300) > 0.005, "내레이션(300Hz) 대역이 사라졌다"


def test_mix_bgm_fail_closed_missing(tmp_path):
    v = tmp_path / "seg.mp4"
    _video_with_voice(v)
    with pytest.raises(RuntimeError):
        AM.mix_bgm(v, tmp_path / "nope.wav", tmp_path / "o.mp4")


def test_mix_bgm_fail_closed_zero_byte(tmp_path):
    v = tmp_path / "seg.mp4"
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    _video_with_voice(v)
    with pytest.raises(RuntimeError):
        AM.mix_bgm(v, empty, tmp_path / "o.mp4")


def test_mix_bgm_requires_audio_stream(tmp_path):
    novid = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=320x240:rate=10:duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", str(novid)], check=True)
    b = tmp_path / "bgm.wav"
    _tone(b, dur=3.0)
    with pytest.raises(RuntimeError):
        AM.mix_bgm(novid, b, tmp_path / "o.mp4")


def test_add_sfx_overlays(tmp_path):
    v = tmp_path / "seg.mp4"
    s = tmp_path / "sfx.wav"
    o = tmp_path / "out.mp4"
    _video_with_voice(v, dur=6.0)
    _tone(s, dur=1.0, hz=6000)
    before = _band_energy(v, 6000)
    AM.add_sfx(v, [{"path": str(s), "at": 2.0}], o)
    after = _band_energy(o, 6000)
    assert after > before + 0.0002, f"효과음 대역이 안 늘었다: {before} → {after}"


def test_add_sfx_rejects_empty_events(tmp_path):
    v = tmp_path / "seg.mp4"
    _video_with_voice(v)
    with pytest.raises(ValueError):
        AM.add_sfx(v, [], tmp_path / "o.mp4")


def test_add_sfx_fail_closed_missing_file(tmp_path):
    v = tmp_path / "seg.mp4"
    _video_with_voice(v)
    with pytest.raises(RuntimeError):
        AM.add_sfx(v, [{"path": str(tmp_path / "no.wav"), "at": 1.0}], tmp_path / "o.mp4")


def test_mix_segment_bgm_and_sfx(tmp_path):
    v = tmp_path / "seg.mp4"
    b = tmp_path / "bgm.wav"
    s = tmp_path / "sfx.wav"
    o = tmp_path / "final.mp4"
    _video_with_voice(v, dur=6.0)
    _tone(b, dur=6.0, hz=3500)
    _tone(s, dur=0.5, hz=6000)
    AM.mix_segment(v, o, bgm=b, sfx=[{"path": str(s), "at": 3.0}])
    assert o.exists()
    assert _band_energy(o, 3500) > 0.0005, "BGM 미반영"
    assert abs(AM.probe_duration(v) - AM.probe_duration(o)) < 0.6


def test_mix_segment_requires_something(tmp_path):
    v = tmp_path / "seg.mp4"
    _video_with_voice(v)
    with pytest.raises(ValueError):
        AM.mix_segment(v, tmp_path / "o.mp4")
