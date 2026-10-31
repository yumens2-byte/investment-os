"""
gen/nuna_weekly/assemble.py — 구간별 조각 생성 → 연결 → 오디오 합성 (ffmpeg)
==============================================================================
    build(variant, segs, script, images, clips, audio, out_dir) -> Path   (<out_dir>/<variant>.mp4)
    select_segments(variant, segs) -> list[Segment]

출력 규격: 1080x1920 · 30fps · H.264 yuv420p · AAC 48kHz · +faststart.

구간 visual 별 조각(명목 길이 = seg.seconds, 다음 조각과 전환할 조각은 XFADE_SEC 만큼 길게 렌더):
    image_card — 이미지 확대 후 구간별 움직임 패턴(줌인·좌우 이동·줌아웃·상하 이동, 이징) + 카드 등장 애니메이션
    image      — 위와 동일, 카드 없음
    clip       — 클립 scale/crop·30fps, 길면 자르고 짧으면 마지막 프레임 복제(tpad). card(breaking 등)가 있으면 등장 애니메이션
    S8(card=disclaimer) — 클립 CLIP_SECONDS(5초) + 마무리 조각 (seconds-5)초:
                          S7 이미지가 있으면 움직이는 배경 + 면책 띠, 없으면 면책 엔드카드(정지)

DN2026_0086 (v1.1.0) 뉴스 방송 연출:
    조각 사이 XFADE_SEC 전환(xfade) — 총 길이는 명목 길이 합과 같다.
    전 구간 공통 오버레이: 채널 로고·배지(cards.render_bug) + 흐르는 시세 띠(cards.render_ticker, facts 수치만).
    자막: 구간 내레이션을 발화 구간(silencedetect)에 글자 수 비례로 배치한 ASS, 카드 패널 바로 위.
    음량: loudnorm I=-14 LUFS(TP -1.5).

자산 dict 의 키는 seg_id(images["S2"]) 또는 Asset.key 규약(images["img_S2"]·clips["clip_S1"]·audio["tts_S3"]).
오디오 = audio[seg_id] wav 를 순서대로 각 구간 길이로 정확히 맞춰(apad+atrim) 연결.
variant: main = 전 구간, fb_cut = S6 제외.
모든 ffmpeg 호출은 리스트 인자·returncode 확인, 실패 시 AssembleError(stderr 끝 500자).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .cards import (
    TICKER_Y,
    CardError,
    render_bug,
    render_card,
    render_disclaimer_band,
    render_ticker,
    ticker_items,
)
from .config import CLIP_SECONDS, VIDEO_FPS, VIDEO_H, VIDEO_W, Segment
from .models import Asset, Facts, Script, ScriptSegment

VERSION = "1.1.0"
log = logging.getLogger(__name__)

VARIANTS = ("main", "fb_cut")
FB_CUT_DROP = ("S6",)
DURATION_TOL_SEC = 0.1
KB_ZOOM_END = 1.08
STDERR_TAIL = 500

# DN2026_0086 연출 상수
XFADE_SEC = 0.4                     # 조각 전환 길이
PIECE_WORKERS = 3                   # 조각 병렬 렌더 상한
XFADE_KIND = "smoothleft"
BG_SCALE = 1.5                      # 움직임 떨림 완화용 확대 배율(이미지를 크게 만든 뒤 zoompan)
CARD_IN_DELAY = 0.15                # 카드 등장 시작(초)
CARD_IN_SEC = 0.5                   # 카드 등장 길이(초)
CARD_SLIDE_PX = 120                 # 카드가 아래에서 올라오는 거리
TICKER_SPEED = 150                  # 시세 띠 이동 속도(px/초)
LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11"
SUB_PX = 70
SUB_GAP_PX = 40                     # 자막 아래 끝과 카드 패널 위 사이 간격
SUB_DEFAULT_Y = int(VIDEO_H * 0.62)
SUB_CHUNK_MAX = 22                  # 자막 한 덩어리 최대 글자 수
SPEECH_DB = -35
SPEECH_MIN_SIL = 0.25
# 움직임 패턴 (z0, z1, x0, x1, y0, y1) — x·y 는 확대 여유폭 대비 위치(0~1)
MOTIONS: tuple[tuple[float, float, float, float, float, float], ...] = (
    (1.00, 1.15, .5, .5, .40, .40),    # 줌인
    (1.12, 1.12, .0, 1., .35, .35),    # 좌→우 이동
    (1.15, 1.00, .5, .5, .30, .45),    # 줌아웃 + 하강
    (1.12, 1.12, 1., .0, .35, .35),    # 우→좌 이동
    (1.05, 1.15, .5, .5, .55, .25),    # 줌인 + 상승
)

_X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-r", str(VIDEO_FPS), "-video_track_timescale", str(VIDEO_FPS * 1000)]
# DN2026_0086: 조각은 최종 합성에서 다시 인코딩되므로 빠른 고화질 중간본(ultrafast·crf 16)으로 만든다
_X264_PIECE = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
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


def _still(idx: int, fmt: str = "rgba") -> str:
    """정지 PNG 입력을 한 번만 디코딩해 반복(loop 필터). -loop 1 입력은 매 프레임 PNG 를 다시 디코딩해 느리다."""
    return f"[{idx}:v]format={fmt},loop=loop=-1:size=1:start=0,setpts=N/{VIDEO_FPS}/TB"


def _trim_png(png: Path) -> tuple[Path, int, int]:
    """전체 화면 크기 RGBA PNG 를 불투명 영역으로 잘라 (경로, x, y) 반환 — overlay 계산량을 줄인다."""
    with Image.open(png) as im:
        bbox = im.getchannel("A").getbbox()
        if bbox is None or (bbox[2] - bbox[0], bbox[3] - bbox[1]) == im.size:
            return png, 0, 0
        x, y = bbox[0] - bbox[0] % 2, bbox[1] - bbox[1] % 2
        out = png.with_name(png.stem + "_trim.png")
        im.crop((x, y, bbox[2], bbox[3])).save(out, "PNG")
    return out, x, y


def _card_overlay_filter(bg: str, card_idx: int, out: str, x: int = 0, y: int = 0) -> str:
    """카드 PNG(잘린 영역, 원래 위치 x·y)를 아래에서 CARD_SLIDE_PX 만큼 올라오며 페이드인(CARD_IN_SEC)."""
    d, t0 = CARD_IN_SEC, CARD_IN_DELAY
    return (f"{_still(card_idx)},fade=t=in:st={t0}:d={d}:alpha=1[c];"
            f"[{bg}][c]overlay=x={x}:y='{y}+{CARD_SLIDE_PX}*pow(max(0,1-max(0,t-{t0})/{d}),2)':shortest=1,"
            f"format=yuv420p[{out}]")


def _motion_zoompan(n: int, motion: tuple[float, float, float, float, float, float]) -> str:
    z0, z1, x0, x1, y0, y1 = motion
    e = f"(0.5-0.5*cos(PI*on/{max(1, n - 1)}))"
    z = f"({z0}+({z1}-{z0})*{e})"
    x = f"(iw-iw/zoom)*({x0}+({x1}-{x0})*{e})"
    y = f"(ih-ih/zoom)*({y0}+({y1}-{y0})*{e})"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={VIDEO_W}x{VIDEO_H}:fps={VIDEO_FPS}"


def _image_piece(img: Path, seconds: float, card_png: Path | None, out: Path,
                 motion: tuple[float, float, float, float, float, float] = MOTIONS[0]) -> Path:
    n = _frames(seconds)
    bw, bh = int(VIDEO_W * BG_SCALE), int(VIDEO_H * BG_SCALE)
    bg = (f"[0:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},setsar=1,"
          f"{_motion_zoompan(n, motion)}")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(img)]
    if card_png is not None:
        trimmed, cx, cy = _trim_png(card_png)
        cmd += ["-i", str(trimmed)]
        fc = f"{bg}[bg];" + _card_overlay_filter("bg", 1, "v", cx, cy)
    else:
        fc = f"{bg},format=yuv420p[v]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-an", *_X264_PIECE, str(out)]
    _run(cmd, f"이미지 조각 {out.name}")
    return out


def _clip_piece(clip: Path, seconds: float, out: Path, card_png: Path | None = None) -> Path:
    n = _frames(seconds)
    vf = (f"{_fill_crop()},fps={VIDEO_FPS},tpad=stop_mode=clone:stop_duration={seconds:.3f},"
          f"trim=end_frame={n},setpts=PTS-STARTPTS")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(clip)]
    if card_png is not None:
        trimmed, cx, cy = _trim_png(card_png)
        cmd += ["-i", str(trimmed),
                "-filter_complex", f"[0:v]{vf}[bg];" + _card_overlay_filter("bg", 1, "v", cx, cy),
                "-map", "[v]"]
    else:
        cmd += ["-vf", f"{vf},format=yuv420p"]
    _run(cmd + ["-frames:v", str(n), "-an", *_X264_PIECE, str(out)], f"클립 조각 {out.name}")
    return out


def _still_piece(png: Path, seconds: float, out: Path) -> Path:
    n = _frames(seconds)
    fc = f"[0:v]{_fill_crop()},loop=loop=-1:size=1:start=0,setpts=N/{VIDEO_FPS}/TB,format=yuv420p[v]"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(png), "-filter_complex", fc, "-map", "[v]",
          "-frames:v", str(n), "-an", *_X264_PIECE, str(out)], f"엔드카드 조각 {out.name}")
    return out


def _script_seg(script: Script, seg_id: str) -> ScriptSegment:
    for s in script.segments:
        if s.seg_id == seg_id:
            return s
    raise AssembleError(f"{seg_id}: 대본 구간 없음")


def _render(kind: str, title: str, lines: Sequence[str], out: Path,
            chart: Sequence[tuple[str, float | None]] | None = None) -> Path:
    try:
        p = render_card(kind, title, lines, out, (VIDEO_W, VIDEO_H), chart=chart)
    except CardError as e:
        raise AssembleError(f"카드 렌더 실패({kind}): {e}") from e
    if p is None:
        raise AssembleError(f"카드 종류 none 에 카드 렌더 요청: {out.name}")
    return p


WEEKDAY_SHORT = ("월", "화", "수", "목", "금")


def chart_bars(facts: Facts | None) -> list[tuple[str, float | None]] | None:
    """월~금 S&P500 일별 등락(%) — facts 값 그대로, 휴장은 None."""
    if facts is None or not facts.days:
        return None
    return [(WEEKDAY_SHORT[i] if i < len(WEEKDAY_SHORT) else d.us_date[5:],
             None if d.closed else d.sp500_chg) for i, d in enumerate(facts.days)]


class _Piece:
    """조각 1개 — 명목 길이(초)와 렌더 함수(길이 인자) + 자막 위치용 카드 PNG."""

    def __init__(self, render, seconds: float, card_png: Path | None = None) -> None:
        self.render = render
        self.seconds = seconds
        self.card_png = card_png


def _seg_pieces(seg: Segment, script: Script, images: dict[str, Asset], clips: dict[str, Asset],
                work: Path, motion_idx: int = 0, facts: Facts | None = None) -> list[_Piece]:
    sid = seg.seg_id
    motion = MOTIONS[motion_idx % len(MOTIONS)]
    if seg.card == "disclaimer":
        clip = _asset_path(clips, sid, "클립")
        if seg.seconds <= CLIP_SECONDS:
            return [_Piece(lambda L: _clip_piece(clip, L, work / f"{sid}_clip.mp4"), seg.seconds)]
        end_sec = round(seg.seconds - CLIP_SECONDS, 3)
        pieces = [_Piece(lambda L: _clip_piece(clip, L, work / f"{sid}_clip.mp4"), CLIP_SECONDS)]
        bg = _optional_asset(images, "S7")       # DN2026_0086 정지 엔드카드 대신 움직이는 배경 + 면책 띠
        if bg is not None:
            band = render_disclaimer_band(work / f"band_{sid}.png", (VIDEO_W, VIDEO_H))
            pieces.append(_Piece(lambda L: _image_piece(bg, L, band, work / f"{sid}_end.mp4", motion),
                                 end_sec, band))
        else:
            card = _render("disclaimer", "", [], work / f"card_{sid}.png")
            pieces.append(_Piece(lambda L: _still_piece(card, L, work / f"{sid}_end.mp4"), end_sec))
        return pieces
    card_png: Path | None = None
    if seg.card not in ("none", "disclaimer"):
        ss = _script_seg(script, sid)
        card_png = _render(seg.card, ss.card_title, ss.card_lines, work / f"card_{sid}.png",
                           chart=chart_bars(facts) if seg.card == "chart" else None)
    if seg.visual == "clip":
        clip = _asset_path(clips, sid, "클립")
        return [_Piece(lambda L: _clip_piece(clip, L, work / f"{sid}.mp4", card_png), seg.seconds, card_png)]
    if seg.visual in ("image", "image_card"):
        img = _asset_path(images, sid, "이미지")
        png = card_png if seg.visual == "image_card" else None
        return [_Piece(lambda L: _image_piece(img, L, png, work / f"{sid}.mp4", motion), seg.seconds, png)]
    raise AssembleError(f"{sid}: 미지원 visual '{seg.visual}'")


def _optional_asset(table: dict[str, Asset], seg_id: str) -> Path | None:
    a = table.get(seg_id) or table.get(_KEY_PREFIX["이미지"] + seg_id)
    if a is None or not Path(a.local_path).is_file():
        return None
    return Path(a.local_path)


# --------------------------------------------------------------------------- #
# 자막 (DN2026_0086)
# --------------------------------------------------------------------------- #
_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")


def speech_spans(wav: Path, seconds: float) -> list[tuple[float, float]]:
    """wav 안 발화 구간 목록(0~seconds). 감지 실패 시 [(0, seconds)]."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(wav), "-af",
                        f"silencedetect=n={SPEECH_DB}dB:d={SPEECH_MIN_SIL}", "-f", "null", "-"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return [(0.0, seconds)]
    starts = [float(x) for x in _SIL_START.findall(r.stderr)]
    ends = [float(x) for x in _SIL_END.findall(r.stderr)]
    spans: list[tuple[float, float]] = []
    cur = 0.0
    for i, st in enumerate(starts):
        if st - cur > 0.05:
            spans.append((cur, min(st, seconds)))
        cur = ends[i] if i < len(ends) else seconds
    if seconds - cur > 0.05:
        spans.append((cur, seconds))
    spans = [(a, b) for a, b in spans if b > a and a < seconds]
    return spans or [(0.0, seconds)]


def subtitle_chunks(text: str, max_len: int = SUB_CHUNK_MAX) -> list[str]:
    """문장부호(, . ? !) 뒤에서 끊고, 그래도 길면 공백에서 반으로 나눈다."""
    parts = [p.strip() for p in re.split(r"(?<=[,.?!])\s+", " ".join(text.split())) if p.strip()]
    out: list[str] = []
    for p in parts:
        stack = [p]
        while stack:
            cur = stack.pop(0)
            if len(cur) <= max_len or " " not in cur:
                out.append(cur)
                continue
            mid = len(cur) // 2
            cut = min((i for i, ch in enumerate(cur) if ch == " "), key=lambda i: abs(i - mid))
            stack[:0] = [cur[:cut].strip(), cur[cut + 1:].strip()]
    return out


def _ass_time(x: float) -> str:
    x = max(0.0, x)
    h, r = divmod(x, 3600)
    m, sec = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def _ass_text(t: str) -> str:
    return t.replace("\\", "＼").replace("{", "(").replace("}", ")")


def _panel_top(card_png: Path | None) -> int | None:
    if card_png is None or not Path(card_png).is_file():
        return None
    with Image.open(card_png) as im:
        bbox = im.getchannel("A").getbbox()
    return None if bbox is None else bbox[1]


def build_ass(entries: list[tuple[float, str, list[tuple[float, float]], int | None]], path: Path) -> Path:
    """entries: (구간 시작 초, 내레이션, 발화 구간(구간 기준), 카드 패널 위 y) → ASS 파일."""
    head = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\n"
            f"PlayResX: {VIDEO_W}\nPlayResY: {VIDEO_H}\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            # BorderStyle 3 = 글자 뒤 불투명 상자(OutlineColour, 남색 알파 0x30) — 밝은 배경에서도 읽히게
            f"Style: Sub,Noto Sans CJK KR,{SUB_PX},&H00FFFFFF,&H000000FF,&H3034180C,&H00000000,-1,0,0,0,"
            "100,100,0,0,3,14,0,2,70,70,40,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events: list[str] = []
    for start, narration, spans, panel_top in entries:
        chunks = subtitle_chunks(narration)
        if not chunks:
            continue
        total = sum(b - a for a, b in spans) or 1.0
        chars = sum(len(c) for c in chunks) or 1
        y = (panel_top - SUB_GAP_PX) if panel_top else SUB_DEFAULT_Y

        def at(x: float, spans: list[tuple[float, float]] = spans) -> float:
            acc = 0.0
            for a, b in spans:
                if x <= acc + (b - a):
                    return a + (x - acc)
                acc += b - a
            return spans[-1][1]

        cursor = 0.0
        for c in chunks:
            dur = total * len(c) / chars
            t0, t1 = start + at(cursor), start + at(cursor + dur)
            cursor += dur
            events.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Sub,,0,0,0,,"
                          f"{{\\an2\\pos({VIDEO_W // 2},{y})\\fad(100,80)}}{_ass_text(c)}")
    path.write_text(head + "\n".join(events) + "\n", encoding="utf-8")
    return path


def _concat_video(pieces: list[Path], out: Path) -> Path:
    """(구) 하드컷 연결 — 조각 1개일 때만 쓴다."""
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in pieces), encoding="utf-8")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c", "copy", str(out)], "영상 연결")
    return out


def _ff_path(p: Path) -> str:
    """필터 인자용 경로 이스케이프(: \\ ' ,)."""
    s = p.resolve().as_posix()
    for ch in ("\\", ":", "'", ",", "[", "]"):
        s = s.replace(ch, "\\" + ch)
    return s


def _compose(rendered: list[tuple[Path, float]], total: float, work: Path, ass: Path | None,
             facts: Facts | None, xfade: float) -> Path:
    """조각 전환(xfade) + 로고·시세 띠 + 자막을 한 번에 인코딩."""
    out = work / "video_only.mp4"
    bug, bx, by = _trim_png(render_bug(work / "bug.png", (VIDEO_W, VIDEO_H)))
    tick = render_ticker(ticker_items(facts), work / "ticker.png")
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p, _ in rendered:
        cmd += ["-i", str(p)]
    n = len(rendered)
    cmd += ["-i", str(bug)]
    if tick is not None:
        cmd += ["-i", str(tick[0])]
    fc: list[str] = []
    prev = "[0:v]"
    off = 0.0
    for i in range(1, n):
        off += rendered[i - 1][1]
        fc.append(f"{prev}[{i}:v]xfade=transition={XFADE_KIND}:duration={xfade}:offset={off:.3f}[x{i}]")
        prev = f"[x{i}]"
    fc.append(f"{_still(n)}[bug];{prev}[bug]overlay={bx}:{by}:shortest=1[o1]")
    prev = "[o1]"
    if tick is not None:
        fc.append(f"{_still(n + 1)}[tk];{prev}[tk]overlay=x='-mod(t*{TICKER_SPEED},{tick[1]})':"
                  f"y={TICKER_Y}:shortest=1[o2]")
        prev = "[o2]"
    if ass is not None:
        fc.append(f"{prev}ass='{_ff_path(ass)}'[o3]")
        prev = "[o3]"
    fc.append(f"{prev}format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-frames:v", str(_frames(total)), "-an",
            *_X264, str(out)]
    _run(cmd, "영상 합성(전환·로고·시세·자막)")
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
    fc = (";".join(parts) + f";{labels}concat=n={len(segs)}:v=0:a=1,{LOUDNESS},"
          "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]")
    total = sum(s.seconds for s in segs)
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}",
            "-movflags", "+faststart", str(out)]
    _run(cmd, "오디오 합성")
    return out


def build(variant: str, segs: Sequence[Segment], script: Script, images: dict[str, Asset],
          clips: dict[str, Asset], audio: dict[str, Asset], out_dir: str | Path,
          facts: Facts | None = None, subtitles: bool = True) -> Path:
    log.info("[NunaWeekly:assemble] v%s 시작 variant=%s", VERSION, variant)
    chosen = select_segments(variant, segs)
    if not chosen:
        raise AssembleError("구간 없음")
    out_dir = Path(out_dir)
    work = out_dir / f"_work_{variant}"
    work.mkdir(parents=True, exist_ok=True)

    plan: list[_Piece] = []
    sub_entries: list[tuple[float, str, list[tuple[float, float]], int | None]] = []
    t = 0.0
    motion_idx = 0
    for seg in chosen:
        pieces = _seg_pieces(seg, script, images, clips, work, motion_idx, facts)
        motion_idx += sum(1 for _ in pieces)
        plan += pieces
        if subtitles:
            ss = next((x for x in script.segments if x.seg_id == seg.seg_id), None)
            if ss is not None and ss.narration.strip():
                wav = _asset_path(audio, seg.seg_id, "오디오")
                top = next((_panel_top(pc.card_png) for pc in pieces if pc.card_png is not None), None)
                sub_entries.append((t, ss.narration, speech_spans(wav, seg.seconds), top))
        t += seg.seconds

    total = round(sum(pc.seconds for pc in plan), 3)
    xfade = min(XFADE_SEC, 0.4 * min(pc.seconds for pc in plan)) if len(plan) > 1 else 0.0
    lengths = [round(pc.seconds + (xfade if i < len(plan) - 1 else 0.0), 3) for i, pc in enumerate(plan)]
    # 조각은 서로 독립 — zoompan 은 단일 스레드라 병렬 렌더로 단축(작업자 수 ≤ CPU 수, 최대 PIECE_WORKERS)
    workers = max(1, min(PIECE_WORKERS, os.cpu_count() or 1, len(plan)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        paths = list(pool.map(lambda pair: pair[0].render(pair[1]), zip(plan, lengths, strict=True)))
    rendered: list[tuple[Path, float]] = [(path, pc.seconds) for path, pc in zip(paths, plan, strict=True)]
    log.info("[NunaWeekly:assemble] 조각 %d개 렌더 완료 (전환 %.2fs)", len(rendered), xfade)

    ass = build_ass(sub_entries, work / "subs.ass") if sub_entries else None
    silent = _compose(rendered, total, work, ass, facts, xfade) if rendered else None
    if silent is None:
        raise AssembleError("조각 없음")
    out = _mux(silent, chosen, audio, out_dir / f"{variant}.mp4")

    expected = round(sum(s.seconds for s in chosen), 3)
    actual = probe_duration(out)
    if abs(actual - expected) > DURATION_TOL_SEC:
        raise AssembleError(f"길이 불일치: 실측 {actual:.3f}s / 기대 {expected:.3f}s")
    log.info("[NunaWeekly:assemble] 완료 %s (%.3fs, %d구간)", out.name, actual, len(chosen))
    return out
