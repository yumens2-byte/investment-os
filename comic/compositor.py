"""
comic/compositor.py
Investment Comic v2.2 — Pillow 최종 이미지 합성

변경사항 (v2.1 → v2.2):
  - _get_font_kr()에 NanumGothic 경로 우선 추가 (fonts-nanum 패키지 5MB)
  - fonts-noto-cjk(61MB) 캐시 미스 시 GitHub Actions 다운로드 시간 절약
  - main.yml fonts-nanum 교체와 함께 적용 (2026-04-07)
  - 기존 NotoSansCJK 경로는 fallback으로 유지 (다른 환경 호환성)

변경사항 (v2.0 → v2.1):
  - 한글 폰트 경로 추가 (Noto Sans CJK → 하단 바 한글 깨짐 수정)
  - 한글/영어 폰트 분리 (_get_font_kr / _get_font)
"""

import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

VERSION = "2.2.0"

# ── 상수 ─────────────────────────────────────────────────

OUTPUT_SIZE    = (1080, 1080)
GRID_DAILY     = (2, 2)     # cols × rows
GRID_WEEKLY    = (4, 2)
BORDER_COLORS  = {
    "LOW":    "#10b981",   # 초록
    "MEDIUM": "#f59e0b",   # 주황
    "HIGH":   "#ef4444",   # 빨강
}
WATERMARK_BG   = "#000000"
WATERMARK_ALPHA = 160      # 0~255
X_ACCOUNT      = "@InvestmentComic"


def _bytes_to_pil(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def _get_font(size: int) -> ImageFont.ImageFont:
    """영어 폰트 로드 (계정명, 에피소드 번호 등)"""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in font_paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _get_font_kr(size: int) -> ImageFont.ImageFont:
    """한글 폰트 로드 (제목 등 한글 텍스트용)

    탐색 우선순위:
      1. NanumGothic (fonts-nanum, 5MB) ← v2.2 신규 primary
      2. Noto Sans CJK (fonts-noto-cjk, 61MB) ← 기존 fallback
      3. Noto Sans KR (별도 설치 시)
      4. macOS 시스템 폰트
      5. DejaVu (한글 X, 크래시 방지용 최후 수단)
    """
    font_paths = [
        # ── v2.2 primary: fonts-nanum (Ubuntu, 5MB) ──
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",

        # ── fallback 1: fonts-noto-cjk (Ubuntu, 61MB) ──
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJKkr-Bold.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",

        # ── fallback 2: Noto Sans KR (별도 설치 시) ──
        "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",

        # ── fallback 3: macOS ──
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",

        # ── 최후 수단: 한글은 깨지지만 크래시 방지 ──
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    logger.warning("[Compositor] 한글 폰트 미발견 — load_default() 사용 (한글 깨짐 가능)")
    return ImageFont.load_default()


def compose_final_image(
    image_results: list[dict],
    story: dict,
    comic_type: str,
    risk_level: str,
    episode_no: int
) -> bytes:
    """
    컷 이미지 → 최종 1080×1080 합성

    HTML 엔진 결과(html_engine=True)인 경우 합성 없이 그대로 반환
    """
    # HTML 엔진 결과 감지 → 합성 없이 바로 반환
    if image_results and image_results[0].get("html_engine"):
        logger.info("[Compositor] HTML 엔진 결과 — 합성 생략, 직접 반환")
        return image_results[0]["image_bytes"]
    grid_cols, grid_rows = GRID_DAILY if comic_type == "daily" else GRID_WEEKLY
    border_color = BORDER_COLORS.get(risk_level, "#3b82f6")

    # 캔버스 생성
    canvas = Image.new("RGB", OUTPUT_SIZE, color="#1a1a2e")
    draw   = ImageDraw.Draw(canvas)

    # 셀 크기 계산 (패딩 포함)
    padding    = 8
    wm_height  = 48   # 하단 워터마크 영역 높이
    usable_h   = OUTPUT_SIZE[1] - wm_height - (padding * (grid_rows + 1))
    usable_w   = OUTPUT_SIZE[0] - (padding * (grid_cols + 1))
    cell_w     = usable_w  // grid_cols
    cell_h     = usable_h  // grid_rows

    # 컷 배치
    for idx, result in enumerate(image_results):
        if idx >= grid_cols * grid_rows:
            break

        col = idx % grid_cols
        row = idx // grid_cols
        x   = padding + col * (cell_w + padding)
        y   = padding + row * (cell_h + padding)

        try:
            cut_img = _bytes_to_pil(result["image_bytes"])
            cut_img = cut_img.resize((cell_w, cell_h), Image.LANCZOS)
        except Exception as e:
            logger.warning(f"[Compositor] Cut #{result['cut_number']} 이미지 로드 실패: {e}")
            cut_img = Image.new("RGB", (cell_w, cell_h), "#2d2d2d")

        canvas.paste(cut_img, (x, y))

        # 컷 번호 라벨
        _draw_cut_label(draw, x + 6, y + 6, result["cut_number"])

    # 리스크 레벨 테두리
    _draw_border(draw, border_color, thickness=6)

    # 하단 워터마크
    _draw_watermark(draw, canvas, episode_no, story["title"])

    # 최종 PNG 반환
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    logger.info(f"[Compositor v{VERSION}] 합성 완료 — {comic_type}, Ep.{episode_no}")
    return buf.read()


def _draw_cut_label(draw: ImageDraw.Draw, x: int, y: int, cut_no: int) -> None:
    """컷 번호 라벨"""
    font = _get_font(18)
    text = f"# {cut_no}"
    draw.rectangle([x, y, x + 36, y + 24], fill="#000000aa")
    draw.text((x + 4, y + 3), text, font=font, fill="#ffffff")


def _draw_border(draw: ImageDraw.Draw, color: str, thickness: int = 4) -> None:
    """전체 캔버스 테두리"""
    w, h = OUTPUT_SIZE
    for i in range(thickness):
        draw.rectangle([i, i, w - 1 - i, h - 1 - i], outline=color)


def _draw_watermark(
    draw: ImageDraw.Draw,
    canvas: Image.Image,
    episode_no: int,
    title: str
) -> None:
    """하단 워터마크 바 — 한글 폰트 적용"""
    w, h = OUTPUT_SIZE
    wm_h = 48

    # 반투명 배경
    overlay = Image.new("RGBA", (w, wm_h), (0, 0, 0, WATERMARK_ALPHA))
    canvas.paste(
        Image.new("RGB", (w, wm_h), WATERMARK_BG),
        (0, h - wm_h)
    )

    font_sm = _get_font_kr(16)   # ← 한글 폰트 (제목용)
    font_md = _get_font(18)      # ← 영어 폰트 (계정명, 에피소드)

    # 왼쪽: 계정명 (영어)
    draw.text((12, h - wm_h + 15), X_ACCOUNT, font=font_md, fill="#60a5fa")

    # 중앙: 제목 — 한글 폰트 사용 (truncate)
    title_short = title[:30] + "…" if len(title) > 30 else title
    # 제목 텍스트 중앙 정렬
    try:
        bbox = font_sm.getbbox(title_short)
        title_w = bbox[2] - bbox[0]
    except Exception:
        title_w = len(title_short) * 14  # fallback 추정
    title_x = max(200, (w - title_w) // 2)  # 계정명과 겹치지 않게
    draw.text((title_x, h - wm_h + 15), title_short, font=font_sm, fill="#e0f0ff")

    # 오른쪽: 에피소드 번호 (영어)
    ep_text = f"Ep.{episode_no}"
    draw.text((w - 80, h - wm_h + 15), ep_text, font=font_md, fill="#10b981")
