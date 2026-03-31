"""
comic/compositor.py
Investment Comic v2.0 — Pillow 최종 이미지 합성

변경사항 (v1.x → v2.0):
  - 워터마크 하단 고정 (계정명 + 에피소드 번호)
  - 일일(2×2), 주간(4×2) 그리드 분기
  - 리스크 레벨별 테두리 색상
"""

import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

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
X_ACCOUNT      = "@InvestmentComic"  # TODO: 실제 계정명으로 교체


def _bytes_to_pil(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def _get_font(size: int) -> ImageFont.ImageFont:
    """폰트 로드 (없으면 기본 폰트)"""
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


def compose_final_image(
    image_results: list[dict],
    story: dict,
    comic_type: str,
    risk_level: str,
    episode_no: int
) -> bytes:
    """
    컷 이미지 → 최종 1080×1080 합성

    Args:
        image_results: generate_images() 반환값
        story: generate_story() 반환값
        comic_type: 'daily' | 'weekly'
        risk_level: 'LOW' | 'MEDIUM' | 'HIGH'
        episode_no: 에피소드 번호

    Returns:
        최종 이미지 bytes (PNG)
    """
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
    logger.info(f"[Compositor] 합성 완료 — {comic_type}, Ep.{episode_no}")
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
    """하단 워터마크 바"""
    w, h = OUTPUT_SIZE
    wm_h = 48

    # 반투명 배경
    overlay = Image.new("RGBA", (w, wm_h), (0, 0, 0, WATERMARK_ALPHA))
    canvas.paste(
        Image.new("RGB", (w, wm_h), WATERMARK_BG),
        (0, h - wm_h)
    )

    font_sm = _get_font(16)
    font_md = _get_font(18)

    # 왼쪽: 계정명
    draw.text((12, h - wm_h + 15), X_ACCOUNT, font=font_md, fill="#60a5fa")

    # 중앙: 제목 (truncate)
    title_short = title[:30] + "…" if len(title) > 30 else title
    draw.text((w // 2 - 120, h - wm_h + 15), title_short, font=font_sm, fill="#e0f0ff")

    # 오른쪽: 에피소드 번호
    ep_text = f"Ep.{episode_no}"
    draw.text((w - 80, h - wm_h + 15), ep_text, font=font_md, fill="#10b981")
