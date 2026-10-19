"""
gen/nuna_weekly/cards.py — 구간 카드 PNG 렌더러 (RGBA 오버레이 / 면책 엔드카드)
================================================================================
    render_card(kind, title, lines, out_png, size=(1080, 1920)) -> Path | None

    kind
      week | day | keywords | risk — 하단 약 40% 영역의 반투명 남색 라운드 패널
                                     (제목 1줄 + 본문 최대 4줄 + 회색 출처 각주), 나머지 투명
      disclaimer                   — 전면 불투명 남색 엔드카드(config.DISCLAIMER + 브랜드명)
      none                         — 카드 없음 → None 반환(파일 미생성)

폰트: Noto Sans CJK 후보 경로 → `fc-match` 순으로 탐색, 없으면 CardError (fail-closed).
      한글이 깨진 영상을 내보내지 않기 위해 DejaVu 등 비한글 폰트로 대체하지 않는다.
텍스트 폭: 940px 이내 — 글자 크기를 최소 크기까지 줄이고, 그래도 넘치면 줄바꿈한다.
"""
from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import DISCLAIMER

VERSION = "1.0.0"
log = logging.getLogger(__name__)


class CardError(RuntimeError):
    """카드 렌더링 불가(폰트 없음·알 수 없는 kind 등) — fail-closed."""


OVERLAY_KINDS = ("week", "day", "keywords", "risk")
ALL_KINDS = (*OVERLAY_KINDS, "disclaimer", "none")

TEXT_MAX_W = 940
MAX_LINES = 4
TITLE_PX = 64
LINE_PX = 52
FOOTER_PX = 28
MIN_SCALE = 0.7                     # 글자 축소 하한(기본 크기 대비)

FOOTER_TEXT = "데이터: 장 마감 기준 · 자체 수집"
BRAND_TEXT = "Iron Nuna 주간 브리핑"

NAVY_PANEL = (12, 24, 52, 214)      # 반투명 남색(알파 ~84%)
NAVY_OPAQUE = (12, 24, 52, 255)
WHITE = (255, 255, 255, 255)
GRAY = (170, 178, 192, 255)
GREEN = (46, 204, 113, 255)
RED = (235, 77, 75, 255)

PANEL_MARGIN_X = 40
PANEL_PAD_Y = 40
PANEL_RADIUS = 36
PANEL_BOTTOM_RATIO = 0.90           # 패널 하단 = 높이의 90% (플랫폼 UI 여백)
ROW_GAP = 18

# (경로, ttc index) — index 1 = Noto Sans CJK KR (fc-match 결과와 동일)
FONT_CANDIDATES_BOLD: tuple[tuple[str, int], ...] = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 1),
    ("/usr/share/fonts/truetype/noto/NotoSansCJKkr-Bold.otf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf", 0),
)
FONT_CANDIDATES_REGULAR: tuple[tuple[str, int], ...] = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 1),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 1),
    ("/usr/share/fonts/truetype/noto/NotoSansCJKkr-Regular.otf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf", 0),
)

_SIGNED_PCT_RE = re.compile(r"(?<![\w.])([+\-−])\d+(?:\.\d+)?\s*%")


def _fc_match(bold: bool) -> tuple[str, int] | None:
    """fc-match 로 Noto Sans CJK KR 파일·index 조회. 실패 시 None."""
    pattern = "Noto Sans CJK KR:bold" if bold else "Noto Sans CJK KR:regular"
    try:
        r = subprocess.run(["fc-match", "-f", "%{file}|%{index}", pattern],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    file_part, _, idx_part = r.stdout.strip().partition("|")
    # fc-match 는 일치 폰트가 없어도 기본 폰트를 돌려주므로 Noto CJK/KR 만 인정한다
    name = Path(file_part).name.lower()
    if not Path(file_part).is_file() or "noto" not in name or ("cjk" not in name and "kr" not in name):
        return None
    try:
        return file_part, int(idx_part or 0)
    except ValueError:
        return file_part, 0


@lru_cache(maxsize=2)
def find_font(bold: bool) -> tuple[str, int]:
    """한글 폰트 (경로, index). 없으면 CardError."""
    for path, idx in (FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REGULAR):
        if Path(path).is_file():
            return path, idx
    hit = _fc_match(bold)
    if hit:
        return hit
    if not bold:
        # Regular 이 없으면 Bold 로라도 렌더(한글 표시 보장이 우선)
        for path, idx in FONT_CANDIDATES_BOLD:
            if Path(path).is_file():
                return path, idx
    raise CardError("한글 폰트(Noto Sans CJK) 없음 — fonts-noto-cjk 설치 필요")


def _font(bold: bool, px: int) -> ImageFont.FreeTypeFont:
    path, idx = find_font(bold)
    try:
        return ImageFont.truetype(path, px, index=idx)
    except OSError as e:
        raise CardError(f"폰트 로드 실패: {Path(path).name} ({e})") from e


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    return draw.textlength(text, font=font)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """공백 단위 우선, 한 단어가 넘치면 글자 단위로 자르는 탐욕적 줄바꿈."""
    rows: list[str] = []
    cur = ""
    for word in text.split(" "):
        cand = word if not cur else f"{cur} {word}"
        if _text_w(draw, cand, font) <= max_w:
            cur = cand
            continue
        if cur:
            rows.append(cur)
            cur = ""
        # 단어 자체가 넘치면 글자 단위
        for ch in word:
            if _text_w(draw, cur + ch, font) <= max_w or not cur:
                cur += ch
            else:
                rows.append(cur)
                cur = ch
    if cur:
        rows.append(cur)
    return rows or [""]


def fit_text(draw: ImageDraw.ImageDraw, text: str, bold: bool, base_px: int,
             max_w: int = TEXT_MAX_W) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """폭 max_w 에 맞춘 (폰트, 줄 목록). 축소 → 최소 크기에서도 넘치면 줄바꿈."""
    min_px = max(12, int(base_px * MIN_SCALE))
    px = base_px
    while px >= min_px:
        f = _font(bold, px)
        if _text_w(draw, text, f) <= max_w:
            return f, [text]
        px -= 2
    f = _font(bold, min_px)
    return f, _wrap(draw, text, f, max_w)


def line_color(text: str) -> tuple[int, int, int, int]:
    """부호 있는 퍼센트(+1.2% / -0.8%)가 있으면 첫 값의 부호로 녹색/적색, 없으면 흰색."""
    m = _SIGNED_PCT_RE.search(text or "")
    if not m:
        return WHITE
    return GREEN if m.group(1) == "+" else RED


def _row_h(font: ImageFont.FreeTypeFont) -> int:
    asc, desc = font.getmetrics()
    return asc + desc


def _render_overlay(title: str, lines: Sequence[str], size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if len(lines) > MAX_LINES:
        log.warning("[NunaWeekly:cards] 본문 %d줄 → 앞 %d줄만 사용", len(lines), MAX_LINES)
    body = [str(x) for x in list(lines)[:MAX_LINES] if str(x).strip()]

    # (폰트, 텍스트, 색) 행 목록 구성
    rows: list[tuple[ImageFont.FreeTypeFont, str, tuple[int, int, int, int]]] = []
    if title.strip():
        f, parts = fit_text(draw, title.strip(), True, TITLE_PX)
        rows += [(f, p, WHITE) for p in parts]
    for ln in body:
        f, parts = fit_text(draw, ln.strip(), False, LINE_PX)
        color = line_color(ln)
        rows += [(f, p, color) for p in parts]
    ff, fparts = fit_text(draw, FOOTER_TEXT, False, FOOTER_PX)
    footer_rows = [(ff, p, GRAY) for p in fparts]

    content_h = sum(_row_h(f) for f, _, _ in rows) + ROW_GAP * max(0, len(rows) - 1)
    footer_h = sum(_row_h(f) for f, _, _ in footer_rows)
    panel_h = PANEL_PAD_Y * 2 + content_h + (ROW_GAP * 2 if rows else 0) + footer_h
    bottom = int(h * PANEL_BOTTOM_RATIO)
    top = max(0, bottom - panel_h)
    if panel_h > int(h * 0.42):
        log.warning("[NunaWeekly:cards] 패널 높이 %dpx — 하단 40%% 초과", panel_h)

    draw.rounded_rectangle((PANEL_MARGIN_X, top, w - PANEL_MARGIN_X, bottom),
                           radius=PANEL_RADIUS, fill=NAVY_PANEL)
    x = (w - TEXT_MAX_W) // 2
    y = top + PANEL_PAD_Y
    for f, text, color in rows:
        draw.text((x, y), text, font=f, fill=color)
        y += _row_h(f) + ROW_GAP
    if rows:
        y += ROW_GAP
    for f, text, color in footer_rows:
        draw.text((x, y), text, font=f, fill=color)
        y += _row_h(f)
    return img


def _render_disclaimer(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, NAVY_OPAQUE)
    draw = ImageDraw.Draw(img)
    rows: list[tuple[ImageFont.FreeTypeFont, str, tuple[int, int, int, int]]] = []
    fb, bparts = fit_text(draw, BRAND_TEXT, True, TITLE_PX)
    rows += [(fb, p, WHITE) for p in bparts]
    fd, dparts = fit_text(draw, DISCLAIMER, False, LINE_PX)
    rows += [(fd, p, GRAY) for p in dparts]
    gap = ROW_GAP * 3
    total = sum(_row_h(f) for f, _, _ in rows) + gap * (len(rows) - 1)
    y = (h - total) // 2
    for f, text, color in rows:
        tw = _text_w(draw, text, f)
        draw.text(((w - tw) / 2, y), text, font=f, fill=color)
        y += _row_h(f) + gap
    return img


def render_card(kind: str, title: str, lines: Sequence[str], out_png: str | Path,
                size: tuple[int, int] = (1080, 1920)) -> Path | None:
    """카드 PNG 생성. kind='none' 이면 None. 실패 시 CardError."""
    log.info("[NunaWeekly:cards] v%s 시작 kind=%s", VERSION, kind)
    if kind not in ALL_KINDS:
        raise CardError(f"알 수 없는 카드 종류: {kind}")
    if kind == "none":
        return None
    if size[0] < TEXT_MAX_W + 2 * PANEL_MARGIN_X:
        raise CardError(f"카드 폭 부족: {size[0]}px (최소 {TEXT_MAX_W + 2 * PANEL_MARGIN_X}px)")
    img = _render_disclaimer(size) if kind == "disclaimer" else _render_overlay(title or "", lines or [], size)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    log.info("[NunaWeekly:cards] 완료 %s (%dx%d)", out.name, size[0], size[1])
    return out
