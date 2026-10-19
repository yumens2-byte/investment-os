"""
gen/nuna_weekly/assemble.py — 구간별 조각 생성 → 연결 → 오디오 합성 (ffmpeg)
==============================================================================
    build(variant, segs, script, images, clips, audio, out_dir) -> Path   (<out_dir>/<variant>.mp4)
    select_segments(variant, segs) -> list[Segment]

출력 규격: 1080x1920 · 30fps · H.264 yuv420p · AAC 48kHz · +faststart.

구간 visual 별 조각(길이 = seg.seconds, 프레임 수 = round(seconds*30)):
    image_card — 이미지 scale/crop → Ken Burns(zoompan 1.0→1.08) + 카드 PNG overlay
    image      — 위와 동일, 카드 없음
    clip       — 클립 scale/crop·30fps, 길면 자르고 짧으면 마지막 프레임 복제(tpad)
    S8(card=disclaimer) — 클립 CLIP_SECONDS(5초) + 면책 엔드카드 (seconds-5)초

자산 dict 의 키는 seg_id(images["S2"]) 또는 Asset.key 규약(images["img_S2"]·clips["clip_S1"]·audio["tts_S3"]).
오디오 = audio[seg_id] wav 를 순서대로 각 구간 길이로 정확히 맞춰(apad+atrim) 연결.
variant: main = 전 구간, fb_cut = S6 제외.
모든 ffmpeg 호출은 리스트 인자·returncode 확인, 실패 시 AssembleError(stderr 끝 500자).
"""
from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .cards import CardError, render_card
from .config import CLIP_SECONDS, VIDEO_FPS, VIDEO_H, VIDEO_W, Segment
from .models import Asset, Script, ScriptSegment

VERSION = "1.0.0"
log = logging.getLogger(__name__)

VARIANTS = ("main", "fb_cut")
FB_CUT_DROP = ("S6",)
DURATION_TOL_SEC = 0.1
KB_ZOOM_END = 1.08
STDERR_TAIL = 500

_X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-r", str(VIDEO_FPS), "-video_track_timescale", str(VIDEO_FPS * 1000)]


class AssembleError(RuntimeError):
    """합성 실패 — 단계 중단."""


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        raise AssembleError(f"{what}: 실행 불가 ({e})") from e
    if r.returncode != 0:
        tail = (r.stderr or "")[-STDERR_TAIL:]
        raise AssembleError(f"{what}: ffmpeg 실패 rc={r.returncode} | {tail}")
    return r


def _frames(seconds: float) -> int:
    n = round(seconds * VIDEO_FPS)
    if n <= 0:
        raise AssembleError(f"구간 길이 0 이하: {seconds}s")
    return n


def probe_duration(path: str | Path) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "json", str(path)], f"ffprobe {Path(path).name}")
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (KeyError, ValueError, TypeError) as e:
        raise AssembleError(f"길이 판독 실패: {Path(path).name}") from e


def select_segments(variant: str, segs: Sequence[Segment]) -> list[Segment]:
    if variant not in VARIANTS:
        raise AssembleError(f"알 수 없는 variant: {variant}")
    if variant == "fb_cut":
        return [s for s in segs if s.seg_id not in FB_CUT_DROP]
    return list(segs)


def _fill_crop() -> str:
    return (f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H},setsar=1")


_KEY_PREFIX = {"이미지": "img_", "클립": "clip_", "오디오": "tts_"}


def _asset_path(table: dict[str, Asset], seg_id: str, label: str) -> Path:
    """키는 seg_id(예: 'S2') 우선, 없으면 Asset.key 규약('img_S2'·'clip_S1'·'tts_S3')."""
    a = table.get(seg_id) or table.get(_KEY_PREFIX.get(label, "") + seg_id)
    if a is None:
        raise AssembleError(f"{seg_id}: {label} 자산 없음")
    p = Path(a.local_path)
    if not p.is_file():
        raise AssembleError(f"{seg_id}: {label} 파일 없음 ({p.name})")
    return p


def _image_piece(img: Path, seconds: float, card_png: Path | None, out: Path) -> Path:
    n = _frames(seconds)
    zoom = (f"zoompan=z='min(1+{KB_ZOOM_END - 1:.4f}*on/{max(1, n - 1)},{KB_ZOOM_END})'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={VIDEO_W}x{VIDEO_H}:fps={VIDEO_FPS}")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(img)]
    if card_png is not None:
        cmd += ["-i", str(card_png)]
        fc = f"[0:v]{_fill_crop()},{zoom}[bg];[1:v]format=rgba[c];[bg][c]overlay=0:0,format=yuv420p[v]"
    else:
        fc = f"[0:v]{_fill_crop()},{zoom},format=yuv420p[v]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-an", *_X264, str(out)]
    _run(cmd, f"이미지 조각 {out.name}")
    return out


def _clip_piece(clip: Path, seconds: float, out: Path) -> Path:
    n = _frames(seconds)
    vf = (f"{_fill_crop()},fps={VIDEO_FPS},tpad=stop_mode=clone:stop_duration={seconds:.3f},"
          f"trim=end_frame={n},setpts=PTS-STARTPTS,format=yuv420p")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf", vf, "-frames:v", str(n), "-an",
          *_X264, str(out)], f"클립 조각 {out.name}")
    return out


def _still_piece(png: Path, seconds: float, out: Path) -> Path:
    n = _frames(seconds)
    vf = f"{_fill_crop()},fps={VIDEO_FPS},format=yuv420p"
    _run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(png),
          "-vf", vf, "-frames:v", str(n), "-an", *_X264, str(out)], f"엔드카드 조각 {out.name}")
    return out


def _script_seg(script: Script, seg_id: str) -> ScriptSegment:
    for s in script.segments:
        if s.seg_id == seg_id:
            return s
    raise AssembleError(f"{seg_id}: 대본 구간 없음")


def _render(kind: str, title: str, lines: Sequence[str], out: Path) -> Path:
    try:
        p = render_card(kind, title, lines, out, (VIDEO_W, VIDEO_H))
    except CardError as e:
        raise AssembleError(f"카드 렌더 실패({kind}): {e}") from e
    if p is None:
        raise AssembleError(f"카드 종류 none 에 카드 렌더 요청: {out.name}")
    return p


def _seg_pieces(seg: Segment, script: Script, images: dict[str, Asset], clips: dict[str, Asset],
                work: Path) -> list[Path]:
    sid = seg.seg_id
    if seg.card == "disclaimer":
        clip = _asset_path(clips, sid, "클립")
        if seg.seconds <= CLIP_SECONDS:
            return [_clip_piece(clip, seg.seconds, work / f"{sid}_clip.mp4")]
        card = _render("disclaimer", "", [], work / f"card_{sid}.png")
        return [_clip_piece(clip, CLIP_SECONDS, work / f"{sid}_clip.mp4"),
                _still_piece(card, round(seg.seconds - CLIP_SECONDS, 3), work / f"{sid}_end.mp4")]
    if seg.visual == "clip":
        return [_clip_piece(_asset_path(clips, sid, "클립"), seg.seconds, work / f"{sid}.mp4")]
    if seg.visual in ("image", "image_card"):
        img = _asset_path(images, sid, "이미지")
        card_png: Path | None = None
        if seg.visual == "image_card" and seg.card != "none":
            ss = _script_seg(script, sid)
            card_png = _render(seg.card, ss.card_title, ss.card_lines, work / f"card_{sid}.png")
        return [_image_piece(img, seg.seconds, card_png, work / f"{sid}.mp4")]
    raise AssembleError(f"{sid}: 미지원 visual '{seg.visual}'")


def _concat_video(pieces: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in pieces), encoding="utf-8")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c", "copy", str(out)], "영상 연결")
    return out


def _mux(video: Path, segs: Sequence[Segment], audio: dict[str, Asset], out: Path) -> Path:
    wavs = [_asset_path(audio, s.seg_id, "오디오") for s in segs]
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
    for w in wavs:
        cmd += ["-i", str(w)]
    parts = []
    for i, s in enumerate(segs, start=1):
        parts.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:"
                     f"channel_layouts=stereo,apad,atrim=end={s.seconds:.3f},asetpts=PTS-STARTPTS[a{i}]")
    labels = "".join(f"[a{i}]" for i in range(1, len(segs) + 1))
    fc = ";".join(parts) + f";{labels}concat=n={len(segs)}:v=0:a=1[aout]"
    total = sum(s.seconds for s in segs)
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}",
            "-movflags", "+faststart", str(out)]
    _run(cmd, "오디오 합성")
    return out


def build(variant: str, segs: Sequence[Segment], script: Script, images: dict[str, Asset],
          clips: dict[str, Asset], audio: dict[str, Asset], out_dir: str | Path) -> Path:
    log.info("[NunaWeekly:assemble] v%s 시작 variant=%s", VERSION, variant)
    chosen = select_segments(variant, segs)
    if not chosen:
        raise AssembleError("구간 없음")
    out_dir = Path(out_dir)
    work = out_dir / f"_work_{variant}"
    work.mkdir(parents=True, exist_ok=True)

    pieces: list[Path] = []
    for seg in chosen:
        pieces += _seg_pieces(seg, script, images, clips, work)
        log.info("[NunaWeekly:assemble] 조각 완료 %s (%.3fs, %s/%s)", seg.seg_id, seg.seconds, seg.visual, seg.card)

    silent = _concat_video(pieces, work / "video_only.mp4")
    out = _mux(silent, chosen, audio, out_dir / f"{variant}.mp4")

    expected = round(sum(s.seconds for s in chosen), 3)
    actual = probe_duration(out)
    if abs(actual - expected) > DURATION_TOL_SEC:
        raise AssembleError(f"길이 불일치: 실측 {actual:.3f}s / 기대 {expected:.3f}s")
    log.info("[NunaWeekly:assemble] 완료 %s (%.3fs, %d구간)", out.name, actual, len(chosen))
    return out
