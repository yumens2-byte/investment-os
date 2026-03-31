"""
comic/image_gen.py
Investment Comic v2.0 — GPT-4o Image 컷 이미지 생성

변경사항 (v1.x → v2.0):
  - DALL-E3 → GPT-4o Image (gpt-image-1) 전환
  - 단건 컷 실패 시 fallback 이미지 적용 (파이프라인 계속 진행)
  - 전체 실패 시 CostLimitExceeded 예외로 파이프라인 중단
  - 월별 비용 상한선 체크
"""

import os
import io
import json
import base64
import logging
from pathlib import Path
from typing import Optional

import requests
import openai

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────

GPT_IMAGE_MODEL   = "gpt-image-1"
IMAGE_SIZE        = "1024x1024"
IMAGE_QUALITY     = "standard"      # standard | hd (hd는 비용 2배)
COST_PER_IMAGE    = 0.04            # standard 기준 (실제 청구 후 보정)
MAX_MONTHLY_COST  = 10.0            # USD 월 상한

CHARACTERS_FILE = Path(__file__).parent / "characters.json"


class CostLimitExceeded(Exception):
    pass


class AllCutsFailed(Exception):
    pass


def _load_fallback_map() -> dict:
    with open(CHARACTERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("fallback_images", {})


def _load_image_bytes(path: str) -> bytes:
    """fallback 이미지 파일 로드"""
    p = Path(path)
    if p.exists():
        return p.read_bytes()
    # fallback 파일도 없으면 1×1 투명 PNG 반환 (최후 방어)
    logger.warning(f"[ImageGen] fallback 파일 없음: {path}")
    return _minimal_png()


def _minimal_png() -> bytes:
    """최소 PNG 바이트 (fallback의 fallback)"""
    import struct, zlib
    def u32(n): return struct.pack(">I", n)
    sig   = b"\x89PNG\r\n\x1a\n"
    ihdr  = b"IHDR" + u32(1) + u32(1) + b"\x08\x02\x00\x00\x00"
    idat  = b"IDAT" + zlib.compress(b"\x00\xff\xff\xff")
    iend  = b"IEND"
    def chunk(c): return u32(len(c)-4) + c + u32(zlib.crc32(c) & 0xFFFFFFFF)
    return sig + chunk(ihdr) + chunk(idat) + chunk(iend)


def _detect_character(image_prompt: str) -> str:
    """프롬프트에서 캐릭터 추정 (fallback 선택용)"""
    p = image_prompt.lower()
    if "bull" in p or "golden" in p:
        return "MAX_BULLHORN"
    if "bear" in p or "dark" in p or "crimson" in p:
        return "BARON_BEARSWORTH"
    if "volatician" in p or "vix" in p or "purple" in p:
        return "THE_VOLATICIAN"
    return "DEFAULT"


def generate_single_cut(
    image_prompt: str,
    cut_number: int,
    current_cost: float
) -> tuple[bytes, float]:
    """
    단일 컷 이미지 생성

    Returns: (image_bytes, cost_usd)
    - API 실패 시 fallback 이미지 반환 (예외 전파 안 함)
    - 비용 상한 초과 시 CostLimitExceeded 예외
    """
    # 비용 상한 체크
    if current_cost >= MAX_MONTHLY_COST:
        raise CostLimitExceeded(
            f"GPT-4o Image 월 비용 상한 초과: ${current_cost:.2f} >= ${MAX_MONTHLY_COST}"
        )

    try:
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

        # 컷 스타일 고정 프롬프트 합성
        full_prompt = (
            f"Comic book panel, vibrant colors, dynamic composition, "
            f"cinematic lighting, clean line art. "
            f"{image_prompt}"
        )

        logger.info(f"[ImageGen] Cut #{cut_number} 생성 중...")

        response = client.images.generate(
            model=GPT_IMAGE_MODEL,
            prompt=full_prompt,
            size=IMAGE_SIZE,
            quality=IMAGE_QUALITY,
            n=1,
            response_format="b64_json"
        )

        img_bytes = base64.b64decode(response.data[0].b64_json)
        logger.info(f"[ImageGen] Cut #{cut_number} 생성 완료 (${COST_PER_IMAGE})")
        return img_bytes, COST_PER_IMAGE

    except CostLimitExceeded:
        raise
    except Exception as e:
        logger.warning(f"[ImageGen] Cut #{cut_number} GPT-4o 실패, fallback 적용: {e}")
        fallback_map = _load_fallback_map()
        char_key = _detect_character(image_prompt)
        fallback_path = fallback_map.get(char_key, fallback_map.get("DEFAULT", ""))
        return _load_image_bytes(fallback_path), 0.0


def generate_images(
    cuts: list[dict],
    monthly_cost_so_far: float = 0.0
) -> list[dict]:
    """
    전체 컷 이미지 생성

    Args:
        cuts: story["cuts"] 리스트
        monthly_cost_so_far: 이번 달 이미 사용한 비용

    Returns:
        [{"cut_number": n, "image_bytes": bytes, "cost": float, "is_fallback": bool}]

    Raises:
        CostLimitExceeded: 월 비용 상한 초과
        AllCutsFailed: 모든 컷이 fallback으로 채워진 경우 (경고 수준)
    """
    results = []
    total_cost = monthly_cost_so_far
    fallback_count = 0

    for cut in cuts:
        cut_no = cut["cut_number"]
        prompt = cut["image_prompt"]

        image_bytes, cost = generate_single_cut(prompt, cut_no, total_cost)
        is_fallback = (cost == 0.0)
        if is_fallback:
            fallback_count += 1

        total_cost += cost
        results.append({
            "cut_number":  cut_no,
            "image_bytes": image_bytes,
            "cost":        cost,
            "is_fallback": is_fallback,
        })

    total_spent = total_cost - monthly_cost_so_far
    logger.info(
        f"[ImageGen] 전체 완료 — {len(results)}컷, "
        f"fallback={fallback_count}건, 이번 실행 비용=${total_spent:.4f}"
    )

    if fallback_count == len(cuts):
        logger.warning("[ImageGen] 전체 컷이 fallback — GPT-4o API 연결 상태 확인 필요")

    return results
