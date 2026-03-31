"""
comic/image_gen.py
Investment Comic v2.0 — 이미지 생성 통합 모듈

우선순위:
  1. OPENAI_API_KEY 있음 → GPT-4o Image (gpt-image-1)
  2. API KEY 없거나 전체 실패 → HTML+Playwright 내부 엔진 ($0)
"""

import os
import base64
import json
import logging
import struct
import zlib
from pathlib import Path

logger = logging.getLogger(__name__)

GPT_IMAGE_MODEL  = "gpt-image-1"
IMAGE_SIZE       = "1024x1024"
IMAGE_QUALITY    = "standard"
COST_PER_IMAGE   = 0.04
MAX_MONTHLY_COST = 10.0
CHARACTERS_FILE  = Path(__file__).parent / "characters.json"


class CostLimitExceeded(Exception):
    pass


def _minimal_png() -> bytes:
    def u32(n): return struct.pack(">I", n)
    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = b"IHDR" + u32(1) + u32(1) + b"\x08\x02\x00\x00\x00"
    idat = b"IDAT" + zlib.compress(b"\x00\x10\x10\x10")
    iend = b"IEND"
    def chunk(c): return u32(len(c)-4) + c + u32(zlib.crc32(c) & 0xFFFFFFFF)
    return sig + chunk(ihdr) + chunk(idat) + chunk(iend)


def _load_fallback_bytes(image_prompt: str) -> bytes:
    try:
        with open(CHARACTERS_FILE, "r", encoding="utf-8") as f:
            fb = json.load(f).get("fallback_images", {})
        p = image_prompt.lower()
        key = "MAX_BULLHORN" if "bull" in p or "golden" in p else \
              "BARON_BEARSWORTH" if "bear" in p or "dark" in p else \
              "THE_VOLATICIAN" if "volatician" in p or "purple" in p else "DEFAULT"
        path = Path(fb.get(key, ""))
        if path.exists():
            return path.read_bytes()
    except Exception:
        pass
    return _minimal_png()


def generate_single_cut(image_prompt, cut_number, current_cost):
    if current_cost >= MAX_MONTHLY_COST:
        raise CostLimitExceeded(f"월 비용 상한 초과: ${current_cost:.2f}")
    try:
        import openai
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        prompt = f"Comic book panel, vibrant colors, cinematic lighting, clean line art. {image_prompt}"
        logger.info(f"[ImageGen] GPT-4o Cut #{cut_number} 생성 중...")
        response = client.images.generate(
            model=GPT_IMAGE_MODEL, prompt=prompt,
            size=IMAGE_SIZE, quality=IMAGE_QUALITY,
            n=1
        )
        # gpt-image-1은 URL 방식만 지원 (response_format 파라미터 미지원)
        import urllib.request
        img = urllib.request.urlopen(response.data[0].url).read()
        logger.info(f"[ImageGen] Cut #{cut_number} 완료 (${COST_PER_IMAGE})")
        return img, COST_PER_IMAGE
    except CostLimitExceeded:
        raise
    except Exception as e:
        logger.warning(f"[ImageGen] Cut #{cut_number} 실패 → fallback: {e}")
        return _load_fallback_bytes(image_prompt), 0.0


def _generate_via_html_engine(story, risk_level, market_data, comic_type, episode_no):
    from comic.html_image_engine import generate_html_comic
    logger.info("[ImageGen] HTML 내부 엔진 실행 ($0)")
    img_bytes = generate_html_comic(
        story=story, risk_level=risk_level,
        market_data=market_data or {},
        comic_type=comic_type or "daily",
        episode_no=episode_no or 1,
    )
    return [{"cut_number": 0, "image_bytes": img_bytes,
             "cost": 0.0, "is_fallback": False, "html_engine": True}]


def generate_images(
    cuts, monthly_cost_so_far=0.0,
    story=None, risk_level=None, market_data=None,
    comic_type=None, episode_no=None
):
    """
    메인 이미지 생성 함수
    1순위: GPT-4o Image (OPENAI_API_KEY 있을 때)
    2순위: HTML+Playwright 내부 엔진 (KEY 없거나 전체 실패 시)
    """
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_key or openai_key == "sk-test-mock":
        logger.info("[ImageGen] OPENAI_API_KEY 없음 → HTML 내부 엔진")
        return _generate_via_html_engine(story, risk_level, market_data, comic_type, episode_no)

    results, total_cost, fallback_count = [], monthly_cost_so_far, 0
    for cut in cuts:
        img, cost = generate_single_cut(cut["image_prompt"], cut["cut_number"], total_cost)
        is_fb = (cost == 0.0)
        if is_fb: fallback_count += 1
        total_cost += cost
        results.append({"cut_number": cut["cut_number"], "image_bytes": img,
                        "cost": cost, "is_fallback": is_fb})

    logger.info(f"[ImageGen] GPT-4o 완료 — {len(results)}컷, fallback={fallback_count}, 비용=${total_cost-monthly_cost_so_far:.4f}")

    if fallback_count == len(cuts):
        logger.warning("[ImageGen] GPT-4o 전체 실패 → HTML 내부 엔진")
        return _generate_via_html_engine(story, risk_level, market_data, comic_type, episode_no)

    return results
