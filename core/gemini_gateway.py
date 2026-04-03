"""
core/gemini_gateway.py (B-14 v2.0)
=====================================
Gemini API 호출 공통 모듈 — google-genai SDK (신규)

기능:
  - Main/Sub/Sub2 키 자동 전환 (429 Rate Limit 시)
  - 지수 백오프 재시도 (최대 3회)
  - DLQ 저장 (전부 실패 시)
  - 모델 선택 (flash-lite / flash / pro)
  - 이미지 생성 (gemini-2.5-flash-image)
  - JSON 응답 파싱 지원

환경변수:
  GEMINI_API_KEY          — 메인 키
  GEMINI_API_SUB_KEY      — 서브 키 (한도 초과 시 자동 전환)
  GEMINI_API_SUB_SUB_KEY  — 서브2 키 (서브 키도 초과 시 자동 전환)

SDK: google-genai (신규, google-generativeai deprecated 대체)
"""
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── 환경변수 ──
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_SUB_KEY = os.getenv("GEMINI_API_SUB_KEY", "")
GEMINI_API_SUB_SUB_KEY = os.getenv("GEMINI_API_SUB_SUB_KEY", "")

# ── 모델 매핑 ──
MODEL_MAP = {
    "flash-lite": "gemini-2.5-flash-lite",
    "flash": "gemini-2.5-flash",
    "pro": "gemini-2.5-pro",
}

# ── 이미지 생성 모델 ──
IMAGE_MODEL = "gemini-2.5-flash-image"

# ── 기본 설정 ──
MAX_RETRIES = 3
BACKOFF_BASE = 2
DEFAULT_MAX_TOKENS = 1024


def _get_client(api_key: str):
    """google-genai Client 생성"""
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except ImportError:
        raise ImportError(
            "google-genai 미설치. "
            "requirements.txt에 'google-genai>=1.0.0' 추가 필요."
        )


def _build_keys() -> list:
    """Main/Sub/Sub2 키 리스트 구성"""
    keys = []
    if GEMINI_API_KEY:
        keys.append(("main", GEMINI_API_KEY))
    if GEMINI_API_SUB_KEY:
        keys.append(("sub", GEMINI_API_SUB_KEY))
    if GEMINI_API_SUB_SUB_KEY:
        keys.append(("sub2", GEMINI_API_SUB_SUB_KEY))
    return keys


def call(
    prompt: str,
    model: str = "flash-lite",
    system_instruction: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.7,
    response_json: bool = False,
    fallback_value: Optional[str] = None,
) -> dict:
    """
    Gemini API 텍스트 호출 (Main → Sub → Sub2 자동 전환 + DLQ)

    Returns:
        {
          "success": True/False,
          "text": "응답 텍스트",
          "data": {...},
          "model": "gemini-2.5-flash-lite",
          "key_used": "main" | "sub" | "sub2",
          "error": None | "에러 메시지",
        }
    """
    if not GEMINI_API_KEY:
        logger.warning("[GeminiGW] GEMINI_API_KEY 미설정 — 스킵")
        return _fail_result("GEMINI_API_KEY 미설정", fallback_value)

    from google.genai import types

    model_name = MODEL_MAP.get(model, MODEL_MAP["flash-lite"])
    keys = _build_keys()
    last_error = ""

    for key_label, api_key in keys:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                client = _get_client(api_key)

                config = types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                    system_instruction=system_instruction or None,
                )
                if response_json:
                    config.response_mime_type = "application/json"

                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )

                text = response.text.strip()

                result = {
                    "success": True,
                    "text": text,
                    "data": None,
                    "model": model_name,
                    "key_used": key_label,
                    "error": None,
                }

                if response_json:
                    try:
                        clean = text
                        if clean.startswith("```"):
                            clean = clean.split("\n", 1)[-1]
                        if clean.endswith("```"):
                            clean = clean.rsplit("```", 1)[0]
                        clean = clean.strip()
                        result["data"] = json.loads(clean)
                    except json.JSONDecodeError as je:
                        logger.warning(f"[GeminiGW] JSON 파싱 실패 (텍스트 유지): {je}")

                logger.info(
                    f"[GeminiGW] 성공 | model={model_name} | key={key_label} "
                    f"| attempt={attempt} | len={len(text)}"
                )
                return result

            except Exception as e:
                last_error = str(e)
                is_rate_limit = "429" in last_error or "RESOURCE_EXHAUSTED" in last_error

                logger.warning(
                    f"[GeminiGW] {key_label} key attempt {attempt}/{MAX_RETRIES} "
                    f"실패: {last_error[:100]}"
                )

                if is_rate_limit:
                    logger.info(f"[GeminiGW] 429 → 다음 키 전환 ({key_label})")
                    break

                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE ** attempt
                    logger.info(f"[GeminiGW] {wait}초 대기 후 재시도")
                    time.sleep(wait)

    logger.error(f"[GeminiGW] 전부 실패 | model={model_name} | error={last_error[:200]}")

    try:
        from core.dlq import enqueue
        enqueue(
            task_type="gemini_call",
            payload={
                "prompt": prompt[:500],
                "model": model,
                "system_instruction": system_instruction[:200],
            },
            error=last_error[:200],
        )
    except Exception as dlq_err:
        logger.warning(f"[GeminiGW] DLQ 저장 실패: {dlq_err}")

    return _fail_result(last_error, fallback_value)


def _fail_result(error: str, fallback_value: Optional[str] = None) -> dict:
    return {
        "success": False,
        "text": fallback_value or "",
        "data": None,
        "model": "",
        "key_used": "",
        "error": error,
    }


def is_available() -> bool:
    return bool(GEMINI_API_KEY)


def generate_image(prompt: str, output_path: str = None) -> dict:
    """
    Gemini Flash Image로 이미지 생성 — Main/Sub/Sub2 키 자동 전환

    Returns:
        {
          "success": True/False,
          "image_bytes": bytes | None,
          "image_path": str | None,
          "key_used": "main" | "sub" | "sub2",
          "error": str,
        }
    """
    from google.genai import types

    keys = _build_keys()
    if not keys:
        return {"success": False, "image_bytes": None, "image_path": None,
                "key_used": "", "error": "GEMINI_API_KEY 미설정"}

    last_error = ""

    for key_label, api_key in keys:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                client = _get_client(api_key)

                response = client.models.generate_content(
                    model=IMAGE_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )

                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        img_bytes = part.inline_data.data
                        if isinstance(img_bytes, str):
                            import base64
                            img_bytes = base64.b64decode(img_bytes)

                        if len(img_bytes) > 500:
                            if output_path:
                                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                                with open(output_path, "wb") as f:
                                    f.write(img_bytes)

                            logger.info(
                                f"[GeminiGW] 이미지 생성 성공 | key={key_label} | "
                                f"attempt={attempt} | size={len(img_bytes)}"
                            )
                            return {
                                "success": True,
                                "image_bytes": img_bytes,
                                "image_path": output_path,
                                "key_used": key_label,
                                "error": "",
                            }

                last_error = "응답에 이미지 없음"
                logger.warning(f"[GeminiGW] 이미지 없음 | key={key_label} | attempt={attempt}")
                break

            except Exception as e:
                last_error = str(e)
                err_str = str(e)

                if "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(
                        f"[GeminiGW] 이미지 429 | key={key_label} | "
                        f"attempt={attempt} → 다음 키 전환"
                    )
                    break

                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE ** attempt
                    logger.warning(
                        f"[GeminiGW] 이미지 실패 | key={key_label} | "
                        f"attempt={attempt} | {err_str[:80]} → {wait}초 대기"
                    )
                    time.sleep(wait)

    logger.warning(f"[GeminiGW] 이미지 전부 실패 | error={last_error[:100]}")
    return {"success": False, "image_bytes": None, "image_path": None,
            "key_used": "", "error": last_error}
