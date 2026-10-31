"""assemble.py 테스트 — 실제 ffmpeg 로 소형 합성 입력을 합본."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from gen.nuna_weekly import assemble
from gen.nuna_weekly.assemble import AssembleError, build, select_segments
from gen.nuna_weekly.config import Segment, segments
from gen.nuna_weekly.models import Asset, Script, ScriptSegment

# 짧은 테스트용 컷 시트 (S8 = 클립 5초 + 엔드카드 1초)
SEGS = [
    Segment("S1", 0, 1, "clip", "none"),
    Segment("S2", 1, 2.5, "image_card", "week"),
    Segment("S6", 2.5, 3.5, "image", "none"),
    Segment("S8", 3.5, 9.5, "clip", "disclaimer"),
]


def _ff(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]


def _probe(p: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                        "-show_format", str(p)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _clip(p: Path, sec: float) -> Path:
    _ff("-f", "lavfi", "-i", f"testsrc=size=320x240:rate=24:duration={sec}", "-pix_fmt", "yuv420p", str(p))
    return p


def _wav(p: Path, sec: float, freq: int = 440) -> Path:
    _ff("-f", "lavfi", "-i", f"sine=frequency={freq}:duration={sec}:sample_rate=24000", "-ac", "1", str(p))
    return p


@pytest.fixture()
def inputs(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    img = src / "s2.png"
    Image.new("RGB", (800, 600), (40, 90, 160)).save(img)
    img6 = src / "s6.png"
    Image.new("RGB", (600, 900), (160, 90, 40)).save(img6)
    images = {"S2": Asset("image", "img_S2", str(img)), "S6": Asset("image", "img_S6", str(img6))}
    clips = {"S1": Asset("clip", "clip_S1", str(_clip(src / "c1.mp4", 2.0))),
             "S8": Asset("clip", "clip_S8", str(_clip(src / "c8.mp4", 2.0)))}  # 5초보다 짧음 → tpad
    audio = {
        "S1": Asset("audio", "tts_S1", str(_wav(src / "a1.wav", 0.6))),   # 짧음 → apad
        "S2": Asset("audio", "tts_S2", str(_wav(src / "a2.wav", 2.0))),   # 김 → atrim
        "S6": Asset("audio", "tts_S6", str(_wav(src / "a6.wav", 1.0))),
        "S8": Asset("audio", "tts_S8", str(_wav(src / "a8.wav", 4.0))),
    }
    script = Script(
        episode_id="t", title="테스트",
        segments=[ScriptSegment("S1", 1, "인트로"),
                  ScriptSegment("S2", 1.5, "주간", card_title="이번 주", card_lines=["S&P500 +1.2%", "나스닥 -0.8%"]),
                  ScriptSegment("S6", 1, "키워드"),
                  ScriptSegment("S8", 6, "투자 참고용 정보이며 투자 권유가 아닙니다")],
        captions={})
    return images, clips, audio, script, tmp_path / "out"


def test_select_segments_variants():
    segs70 = segments(70)
    assert [s.seg_id for s in select_segments("main", segs70)] == [s.seg_id for s in segs70]
    fb = select_segments("fb_cut", segs70)
    assert "S6" not in [s.seg_id for s in fb]
    assert sum(s.seconds for s in fb) == pytest.approx(60.0)
    with pytest.raises(AssembleError):
        select_segments("bogus", segs70)


@pytest.mark.parametrize(("variant", "expected"), [("main", 9.5), ("fb_cut", 8.5)])
def test_build_spec_and_duration(inputs, variant, expected):
    images, clips, audio, script, out_dir = inputs
    out = build(variant, SEGS, script, images, clips, audio, out_dir)
    assert out == out_dir / f"{variant}.mp4" and out.is_file()
    info = _probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["codec_name"], v["pix_fmt"]) == (1080, 1920, "h264", "yuv420p")
    assert v["avg_frame_rate"] in ("30/1", "30000/1000")
    assert a["codec_name"] == "aac" and a["sample_rate"] == "48000"
    assert abs(float(info["format"]["duration"]) - expected) <= 0.1
    # +faststart: moov 가 mdat 앞
    head = out.read_bytes()[:200000]
    assert head.find(b"moov") != -1 and head.find(b"moov") < head.find(b"mdat")


def test_asset_key_convention_fallback(inputs):
    images, clips, audio, script, out_dir = inputs
    images = {a.key: a for a in images.values()}
    clips = {a.key: a for a in clips.values()}
    audio = {a.key: a for a in audio.values()}
    out = build("fb_cut", SEGS[:2], script, images, clips, audio, out_dir)
    assert abs(float(_probe(out)["format"]["duration"]) - 2.5) <= 0.1


def test_missing_asset_raises(inputs):
    images, clips, audio, script, out_dir = inputs
    del audio["S2"]
    with pytest.raises(AssembleError, match="S2"):
        build("main", SEGS, script, images, clips, audio, out_dir)


def test_ffmpeg_failure_stderr_tail(inputs):
    images, clips, audio, script, out_dir = inputs
    bad = out_dir.parent / "src" / "broken.png"
    bad.write_bytes(b"not an image" * 10)
    images["S2"] = Asset("image", "img_S2", str(bad))
    with pytest.raises(AssembleError) as ei:
        build("main", SEGS, script, images, clips, audio, out_dir)
    msg = str(ei.value)
    assert "ffmpeg 실패" in msg
    assert len(msg.split("| ", 1)[1]) <= assemble.STDERR_TAIL


# --------------------------------------------------------------------------- #
# DN2026_0086 전환·자막·음량·시세 띠
# --------------------------------------------------------------------------- #
def _lufs(p: Path) -> float:
    from gen.nuna_weekly.qc import integrated_lufs

    v = integrated_lufs(p)
    assert v is not None
    return v


def test_news_build_keeps_length_and_writes_subtitles(inputs):
    from gen.nuna_weekly.models import DayFacts, Facts

    images, clips, audio, script, out = inputs
    facts = Facts(episode_id="t", source="direct", fetched_at="", week_sp500_chg=1.2,
                  days=[DayFacts("2026-10-26", sp500_chg=0.5, vix=16.0), DayFacts("2026-10-27", closed=True)])
    p = build("main", SEGS, script, images, clips, audio, out, facts=facts)
    info = _probe(p)
    assert abs(float(info["format"]["duration"]) - 9.5) <= 0.1
    ass = (out / "_work_main" / "subs.ass").read_text(encoding="utf-8")
    assert ass.count("Dialogue:") >= 4 and "\\pos(540," in ass and "주간" in ass
    assert (out / "_work_main" / "ticker.png").is_file() and (out / "_work_main" / "bug.png").is_file()
    assert -16.5 <= _lufs(p) <= -11.5


def test_subtitle_chunks_and_speech_spans(tmp_path):
    chunks = assemble.subtitle_chunks("이번 주 S&P 지수는 1.58%, 나스닥은 2.29% 올랐습니다. 다음 주에는 유가 흐름을 지켜봐야 합니다.")
    assert chunks[0] == "이번 주 S&P 지수는 1.58%," and all(len(c) <= assemble.SUB_CHUNK_MAX or " " not in c
                                                          for c in chunks)
    wav = tmp_path / "s.wav"
    _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1.0:sample_rate=24000", "-af", "apad=pad_dur=1.5",
        "-ac", "1", str(wav))
    spans = assemble.speech_spans(wav, 2.5)
    assert spans[0][0] == 0.0 and 0.9 <= spans[0][1] <= 1.1 and len(spans) == 1


def test_chart_bars_from_facts():
    from gen.nuna_weekly.models import DayFacts, Facts

    f = Facts(episode_id="t", source="direct", fetched_at="",
              days=[DayFacts("2026-10-26", sp500_chg=0.7), DayFacts("2026-10-27", closed=True),
                    DayFacts("2026-10-28", sp500_chg=-0.28)])
    assert assemble.chart_bars(f) == [("월", 0.7), ("화", None), ("수", -0.28)]
    assert assemble.chart_bars(None) is None
