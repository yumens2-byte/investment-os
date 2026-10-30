"""
gen/nuna_weekly/clips.py — 오프닝·엔딩 클립 생성 (단계 clips → CLIPPED)
======================================================================
generate_clips(episode_id, script, segs, images, fal, budget, out_dir) -> dict[seg_id, Asset]
    visual == "clip" 구간(S1, S8) — kling image-to-video, 5초, 오디오 생성 금지.
    시작 이미지: S1 ← images["S2"].remote_url, S8 ← images["S7"].remote_url
    generate_audio 는 반드시 False (단가 config.PRICE_CLIP_USD_PER_SEC 전제) — 아니면 호출 전 ClipError.
    결과 검증: ffprobe 길이 CLIP_SECONDS ±0.5s, 파일 >10KB.
    이미 검증 통과한 파일(clip_<seg>.mp4)이 있으면 재사용(재과금 없음).
    jobs(원장 fal_pending) 에 제출 기록이 있으면 재제출 없이 재조회(fal_jobs.run_paid).
    대기 상한: config.fal_clip_max_wait_sec() (기본 900s).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import config
from .backends.tts import TTSError, probe_seconds
from .fal_jobs import FalJobs, run_paid
from .images import canon_short, load_canon, read_meta, write_meta
from .models import Asset, Script

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.clips")

START_IMAGE_FROM = {"S1": "S2", "S8": "S7"}
DURATION_TOLERANCE = 0.5
MIN_BYTES = 10 * 1024


class ClipError(RuntimeError):
    """클립 생성·검증 실패."""


def build_payload(prompt: str, start_image_url: str) -> dict[str, Any]:
    return {"prompt": canon_short() + prompt.strip(), "start_image_url": start_image_url,
            "duration": str(config.CLIP_SECONDS), "generate_audio": False,
            "negative_prompt": load_canon()["negative_prompt"]}


def verify_clip(path: Path) -> float:
    if not path.is_file() or path.stat().st_size <= MIN_BYTES:
        raise ClipError(f"{path.name} 크기 미달(≤10KB)")
    try:
        sec = probe_seconds(path)
    except TTSError as e:
        raise ClipError(f"{path.name} ffprobe 실패: {e}") from None
    if abs(sec - config.CLIP_SECONDS) > DURATION_TOLERANCE:
        raise ClipError(f"{path.name} 길이 {sec:.2f}s — 목표 {config.CLIP_SECONDS}s ±{DURATION_TOLERANCE}")
    return sec


def generate_clips(episode_id: str, script: Script, segs: Sequence[config.Segment], images: Mapping[str, Asset],
                   fal: Any, budget: Any, out_dir: Path, jobs: FalJobs | None = None) -> dict[str, Asset]:
    targets = [s for s in segs if s.visual == "clip"]
    log.info("[NunaWeekly:clips] v%s 시작 · %s · backend=%s · %d개",
             VERSION, episode_id, getattr(fal, "name", "?"), len(targets))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_id = {s.seg_id: s for s in script.segments}
    out: dict[str, Asset] = {}
    for seg in targets:
        src_id = START_IMAGE_FROM.get(seg.seg_id)
        if src_id is None:
            raise ClipError(f"{seg.seg_id} 시작 이미지 매핑 없음")
        ss = by_id.get(seg.seg_id)
        if ss is None or not ss.clip_prompt.strip():
            raise ClipError(f"{seg.seg_id} clip_prompt 없음")
        path = out_dir / f"clip_{seg.seg_id}.mp4"
        if path.is_file():
            try:
                sec = verify_clip(path)
                meta = read_meta(path)
                out[seg.seg_id] = Asset(kind="clip", key=f"clip_{seg.seg_id}", local_path=str(path),
                                        remote_url=meta.get("remote_url"), request_id=meta.get("request_id"),
                                        seconds=round(sec, 3))
                log.info("%s 기존 클립 재사용 (재과금 없음)", seg.seg_id)
                continue
            except ClipError:
                log.warning("%s 기존 클립 검증 실패 → 재생성", seg.seg_id)
        src = images.get(src_id)
        if src is None or not src.remote_url:
            raise ClipError(f"{seg.seg_id} 시작 이미지({src_id}) remote_url 없음")
        payload = build_payload(ss.clip_prompt, src.remote_url)
        if payload.get("generate_audio") is not False:
            raise ClipError("generate_audio 는 False 여야 함 — 단가 전제 위반")
        pending = out_dir / f"clip_{seg.seg_id}.fal_pending.json"
        units = config.CLIP_SECONDS
        res = run_paid(fal, budget, jobs, f"clip_{seg.seg_id}", step="clips", endpoint=config.FAL_CLIP_ENDPOINT,
                       payload=payload, out_path=path, pending_path=pending, units=units, unit="sec",
                       est_usd=units * config.PRICE_CLIP_USD_PER_SEC, max_wait_sec=config.fal_clip_max_wait_sec())
        sec = verify_clip(path)
        write_meta(path, res.remote_url, res.request_id)
        out[seg.seg_id] = Asset(kind="clip", key=f"clip_{seg.seg_id}", local_path=str(path),
                                remote_url=res.remote_url, request_id=res.request_id, seconds=round(sec, 3))
        log.info("%s 클립 완료 %.2fs request_id=%s", seg.seg_id, sec, res.request_id)
    return out
