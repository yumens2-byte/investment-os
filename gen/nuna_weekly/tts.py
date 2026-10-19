"""
gen/nuna_weekly/tts.py — 구간별 내레이션 합성·길이 맞춤 (단계 tts → VOICED)
=========================================================================
synthesize_segments(script, segs, backend, budget, out_dir) -> list[Asset]
    구간마다: 예산 예약 → backend.synthesize → 길이 맞춤 → tts_<seg>.wav (48kHz mono s16le).
    길이 맞춤 (hero voiceover.py 의 apad/atempo 방식 이식):
        raw > target : raw/target ≤ 1.15 이면 atempo 가속, 초과면 TTSFitError (대본 재작성 필요)
        raw ≤ target : apad 로 무음 채움
        최종 -t target 으로 자른다 → 실측 ±0.02s 검증.
concat_audio(assets, out_path) -> Path, measure_seconds(path) -> float
"""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import config
from .backends.tts import TTSError, probe_seconds
from .models import Asset, Script, ScriptSegment

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.tts")

OUT_SAMPLE_RATE = 48000
MAX_ATEMPO = 1.15
FIT_TOLERANCE_SEC = 0.02


class TTSFitError(TTSError):
    """합성 길이가 목표 대비 1.15배 초과 — 가속으로 맞출 수 없음."""

    def __init__(self, seg_id: str, raw: float, target: float) -> None:
        self.seg_id, self.raw, self.target = seg_id, raw, target
        super().__init__(f"{seg_id} 내레이션 길이 초과: {raw:.2f}s > {target:.2f}s × {MAX_ATEMPO} — 대본 단축 필요")


def measure_seconds(path: Path | str) -> float:
    return probe_seconds(path)


def _ffmpeg(args: list[str], what: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise TTSError(f"{what} 실패: {r.stderr[:300]}")


def fit_to_target(raw_path: Path, out_path: Path, seg_id: str, target: float) -> float:
    """raw 를 target 초로 맞춰 48kHz mono pcm_s16le 로 저장하고 실측 초를 반환."""
    raw = measure_seconds(raw_path)
    if raw > target:
        ratio = raw / target
        if ratio > MAX_ATEMPO:
            raise TTSFitError(seg_id, raw, target)
        af = f"atempo={ratio:.5f},apad"
        log.info("%s 가속 atempo=%.4f (%.2fs → %.2fs)", seg_id, ratio, raw, target)
    else:
        af = "apad"
    _ffmpeg(["-i", str(raw_path), "-af", af, "-t", f"{target:.3f}",
             "-ar", str(OUT_SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out_path)], "오디오 길이 맞춤")
    got = measure_seconds(out_path)
    if abs(got - target) > FIT_TOLERANCE_SEC:
        raise TTSError(f"{seg_id} 길이 맞춤 오차 초과: {got:.3f}s vs {target:.3f}s")
    return got


def _est_usd(target: float, text: str) -> float:
    return (target * config.TTS_TOKENS_PER_SEC * config.PRICE_TTS_OUT_PER_MTOK / 1e6
            + len(text) * config.PRICE_TTS_IN_PER_MTOK / 1e6)


def synthesize_segments(script: Script, segs: Sequence[config.Segment], backend: Any, budget: Any,
                        out_dir: Path) -> list[Asset]:
    log.info("[NunaWeekly:tts] v%s 시작 · backend=%s · 구간 %d", VERSION, getattr(backend, "name", "?"), len(segs))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_id: dict[str, ScriptSegment] = {s.seg_id: s for s in script.segments}
    voice = config.tts_voice()
    model = config.tts_model()
    assets: list[Asset] = []
    for seg in segs:
        ss = by_id.get(seg.seg_id)
        if ss is None or not ss.narration.strip():
            raise TTSError(f"{seg.seg_id} 내레이션 없음")
        target = seg.seconds
        text = ss.narration
        raw_path = out_dir / f"tts_{seg.seg_id}.raw.wav"
        final = out_dir / f"tts_{seg.seg_id}.wav"
        with budget.charge(step="tts", provider="gemini", model=model, units=target * 25, unit="token",
                           est_usd=_est_usd(target, text)) as ch:
            res = backend.synthesize(text, voice, config.TTS_STYLE, raw_path)
            usd = (res.output_tokens * config.PRICE_TTS_OUT_PER_MTOK / 1e6
                   + res.input_tokens * config.PRICE_TTS_IN_PER_MTOK / 1e6)
            ch.actual(usd=usd, units=res.output_tokens or None, request_id=res.request_id)
        got = fit_to_target(Path(res.path), final, seg.seg_id, target)
        raw_path.unlink(missing_ok=True)
        assets.append(Asset(kind="audio", key=f"tts_{seg.seg_id}", local_path=str(final),
                            request_id=res.request_id, seconds=round(got, 3)))
        log.info("%s 음성 완료: raw %.2fs → %.2fs", seg.seg_id, res.seconds, got)
    return assets


def concat_audio(assets: Sequence[Asset], out_path: Path) -> Path:
    """구간 wav 를 순서대로 이어 붙인다 (동일 포맷 → concat demuxer, 재인코딩 s16le)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not assets:
        raise TTSError("병합할 오디오 없음")
    lst = out_path.with_suffix(".concat.txt")
    lst.write_text("\n".join(f"file '{Path(a.local_path).resolve()}'" for a in assets) + "\n", encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-ar", str(OUT_SAMPLE_RATE), "-ac", "1",
             "-c:a", "pcm_s16le", str(out_path)], "오디오 병합")
    lst.unlink(missing_ok=True)
    return out_path
