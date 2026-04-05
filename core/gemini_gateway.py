"""
core/gemini_gateway.py (B-14 v3.0)
=====================================
Gemini API 호출 공통 모듈 — google-genai SDK

기능:
  - Main/Sub/Sub2 무료 키 자동 전환 (429 Rate Limit 시)
  - 무료 3키 전부 실패 시 유료 Pay 키 fallback (최후 수단)
  - 지수 백오프 재시도 (최대 3회)
  - DLQ 저장 (전부 실패 시)
  - 모델 선택 (flash-lite / flash / pro)
  - 이미지 생성 (gemini-2.5-flash-image)
  - JSON 응답 파싱 지원

환경변수:
  GEMINI_API_KEY          — 메인 키 (무료)
  GEMINI_API_SUB_KEY      — 서브 키 (무료, 한도 초과 시 자동 전환)
  GEMINI_API_SUB_SUB_KEY  — 서브2 키 (무료, 서브 키도 초과 시 자동 전환)
  GEMINI_API_SUB_PAY_KEY  — 유료 키 (무료 3키 전부 실패 시 최후 fallback)

키 전환 순서: main → sub → sub2 → pay (유료)
※ pay 키는 실제 과금 발생 — 무료 3키 전부 429일 때만 사용

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
GEMINI_API_SUB_PAY_KEY = os.getenv("GEMINI_API_SUB_PAY_KEY", "")  # 유료 키

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
    """Main/Sub/Sub2/Pay 키 리스트 구성 (무료 3키 → 유료 1키 순서)"""
    keys = []
    # ── 무료 키 (우선 사용) ──
    if GEMINI_API_KEY:
        keys.append(("main", GEMINI_API_KEY, False))      # (라벨, 키, 유료여부)
    if GEMINI_API_SUB_KEY:
        keys.append(("sub", GEMINI_API_SUB_KEY, False))
    if GEMINI_API_SUB_SUB_KEY:
        keys.append(("sub2", GEMINI_API_SUB_SUB_KEY, False))
    # ── 유료 키 (최후 fallback) ──
    if GEMINI_API_SUB_PAY_KEY:
        keys.append(("pay", GEMINI_API_SUB_PAY_KEY, True))
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
    Gemini API 텍스트 호출 (Main → Sub → Sub2 → Pay 자동 전환 + DLQ)

    Returns:
        {
          "success": True/False,
          "text": "응답 텍스트",
          "data": {...},
          "model": "gemini-2.5-flash-lite",
          "key_used": "main" | "sub" | "sub2" | "pay",
          "paid": False | True,
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

    for key_label, api_key, is_paid in keys:
        # ── 유료 키 진입 시 경고 로그 ──
        if is_paid:
            logger.warning(f"[GeminiGW] ⚠️ 무료 키 전부 실패 → 유료 키({key_label}) 사용")

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
                    "paid": is_paid,
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

                # ── 유료 키 사용 시 명시 로그 ──
                paid_tag = " [💰PAID]" if is_paid else ""
                logger.info(
                    f"[GeminiGW] 성공{paid_tag} | model={model_name} | key={key_label} "
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

    logger.error(f"[GeminiGW] 전부 실패 (무료+유료) | model={model_name} | error={last_error[:200]}")

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
        "paid": False,
        "error": error,
    }


def is_available() -> bool:
    return bool(GEMINI_API_KEY)


def generate_image(prompt: str, output_path: str = None) -> dict:
    """
    Gemini 이미지 생성 — Main/Sub/Sub2/Pay 키 자동 전환
    무료 3키 전부 429 → 유료 Pay 키 fallback
    """
    from google.genai import types

    keys = _build_keys()
    if not keys:
        return {"success": False, "image_bytes": None, "image_path": None,
                "key_used": "", "paid": False, "error": "GEMINI_API_KEY 미설정"}

    last_error = ""

    for key_label, api_key, is_paid in keys:
        # ── 유료 키 진입 시 경고 로그 ──
        if is_paid:
            logger.warning(f"[GeminiGW] ⚠️ 이미지 무료 키 전부 실패 → 유료 키({key_label}) 사용")

        # ── generate_content API ──
        try:
            client = _get_client(api_key)
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
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
                        # ── 유료 키 사용 시 명시 로그 ──
                        paid_tag = " [💰PAID]" if is_paid else ""
                        logger.info(
                            f"[GeminiGW] 이미지 생성 성공{paid_tag} | key={key_label} | size={len(img_bytes)}"
                        )
                        return {
                            "success": True,
                            "image_bytes": img_bytes,
                            "image_path": output_path,
                            "key_used": key_label,
                            "paid": is_paid,
                            "error": "",
                        }
        except Exception as e2:
            err2 = str(e2)
            if "429" in err2 or "quota" in err2.lower() or "RESOURCE_EXHAUSTED" in err2:
                logger.warning(f"[GeminiGW] 이미지(이미지 429 | key={key_label} → 다음 키")
                continue
            last_error = err2
            logger.warning(f"[GeminiGW] 이미지(이미지 실패 | key={key_label} | {err2[:80]}")

    logger.warning(f"[GeminiGW] 이미지 전부 실패 (무료+유료) | error={last_error[:100]}")
    return {"success": False, "image_bytes": None, "image_path": None,
            "key_used": "", "paid": False, "error": last_error}


# ──────────────────────────────────────────────────────────────
# C-13: Gemini Vision — 이미지 입력 분석 (2026-04-04)
# ──────────────────────────────────────────────────────────────

def analyze_image(
    image_path: str,
    prompt: str,
    model: str = "flash-lite",
    response_json: bool = True,
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> dict:
    """
    이미지 + 텍스트 프롬프트 → Gemini Vision 분석.
    Main/Sub/Sub2/Pay 키 자동 전환.
    """
    from google.genai import types

    if not GEMINI_API_KEY:
        return _fail_result("GEMINI_API_KEY 미설정")

    # 이미지 파일 읽기
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
    except Exception as e:
        return _fail_result(f"이미지 파일 읽기 실패: {e}")

    if len(image_bytes) < 100:
        return _fail_result("이미지 파일이 너무 작음")

    # mime type 판별
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    model_name = MODEL_MAP.get(model, MODEL_MAP["flash-lite"])
    keys = _build_keys()

    for key_label, api_key, is_paid in keys:
        # ── 유료 키 진입 시 경고 로그 ──
        if is_paid:
            logger.warning(f"[GeminiGW] ⚠️ Vision 무료 키 전부 실패 → 유료 키({key_label}) 사용")

        try:
            client = _get_client(api_key)

            contents = [
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                prompt,
            ]

            config = types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            )

            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )

            text = response.text.strip() if response.text else ""

            if not text:
                logger.warning(f"[GeminiGW] Vision 빈 응답 | key={key_label}")
                continue

            result = {
                "success": True,
                "text": text,
                "data": None,
                "model": model_name,
                "key_used": key_label,
                "paid": is_paid,
                "error": None,
            }

            # JSON 파싱
            if response_json:
                try:
                    import json
                    clean = text.strip()
                    if clean.startswith("```"):
                        clean = clean.split("\n", 1)[-1]
                    if clean.endswith("```"):
                        clean = clean.rsplit("```", 1)[0]
                    result["data"] = json.loads(clean.strip())
                except (json.JSONDecodeError, ValueError):
                    result["data"] = None

            # ── 유료 키 사용 시 명시 로그 ──
            paid_tag = " [💰PAID]" if is_paid else ""
            logger.info(
                f"[GeminiGW] Vision 성공{paid_tag} | model={model_name} | "
                f"key={key_label} | len={len(text)}"
            )
            return result

        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower() or "RESOURCE_EXHAUSTED" in err:
                logger.warning(f"[GeminiGW] Vision 429 | key={key_label} → 다음 키")
                continue
            logger.warning(f"[GeminiGW] Vision 실패 | key={key_label} | {err[:80]}")
            return _fail_result(err)

    return _fail_result("Vision 전부 실패 (무료+유료 429)")
