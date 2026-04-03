"""
core/gemini_gateway.py (B-14)
================================
Gemini API 호출 공통 모듈

기능:
  - Main/Sub 키 자동 전환 (429 Rate Limit 시)
  - 지수 백오프 재시도 (최대 3회)
  - DLQ 저장 (전부 실패 시)
  - 모델 선택 (flash-lite / flash / pro)
  - JSON 응답 파싱 지원

환경변수:
  GEMINI_API_KEY          — 메인 키
  GEMINI_API_SUB_KEY      — 서브 키 (한도 초과 시 자동 전환)
  GEMINI_API_SUB_SUB_KEY  — 서브2 키 (서브 키도 초과 시 자동 전환)
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

# ── 기본 설정 ──
MAX_RETRIES = 3
BACKOFF_BASE = 2  # 지수 백오프: 2초, 4초, 8초
DEFAULT_MAX_TOKENS = 1024


def _get_client(api_key: str):
    """google-generativeai 클라이언트 생성"""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        return genai
    except ImportError:
        raise ImportError(
            "google-generativeai 미설치. "
            "requirements.txt에 'google-generativeai>=0.8.0' 추가 필요."
        )


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
    Gemini API 호출 (Main → Sub 자동 전환 + DLQ)

    Args:
        prompt:             사용자 프롬프트
        model:              "flash-lite" | "flash" | "pro"
        system_instruction: 시스템 프롬프트
        max_tokens:         최대 출력 토큰
        temperature:        창의성 (0.0~1.0)
        response_json:      True면 JSON 파싱 시도
        fallback_value:     전부 실패 시 반환할 기본값

    Returns:
        {
          "success": True/False,
          "text": "응답 텍스트",
          "data": {...},          # response_json=True일 때 파싱된 dict
          "model": "gemini-2.5-flash-lite",
          "key_used": "main" | "sub",
          "error": None | "에러 메시지",
        }
    """
    if not GEMINI_API_KEY:
        logger.warning("[GeminiGW] GEMINI_API_KEY 미설정 — 스킵")
        return _fail_result("GEMINI_API_KEY 미설정", fallback_value)

    model_name = MODEL_MAP.get(model, MODEL_MAP["flash-lite"])
    keys = [("main", GEMINI_API_KEY)]
    if GEMINI_API_SUB_KEY:
        keys.append(("sub", GEMINI_API_SUB_KEY))
    if GEMINI_API_SUB_SUB_KEY:
        keys.append(("sub2", GEMINI_API_SUB_SUB_KEY))

    last_error = ""

    for key_label, api_key in keys:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                genai = _get_client(api_key)
                gen_model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system_instruction or None,
                )

                generation_config = {
                    "max_output_tokens": max_tokens,
                    "temperature": temperature,
                }
                if response_json:
                    generation_config["response_mime_type"] = "application/json"

                response = gen_model.generate_content(
                    prompt,
                    generation_config=generation_config,
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

                # JSON 파싱 시도
                if response_json:
                    try:
                        # ```json ... ``` 제거
                        clean = text
                        if clean.startswith("```"):
                            clean = clean.split("\n", 1)[-1]
                        if clean.endswith("```"):
                            clean = clean.rsplit("```", 1)[0]
                        clean = clean.strip()
                        result["data"] = json.loads(clean)
                    except json.JSONDecodeError as je:
                        logger.warning(
                            f"[GeminiGW] JSON 파싱 실패 (텍스트 유지): {je}"
                        )

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

                if is_rate_limit and key_label == "main" and GEMINI_API_SUB_KEY:
                    # 429면 Sub 키로 즉시 전환 (재시도 안함)
                    logger.info("[GeminiGW] 429 → Sub Key 전환")
                    break

                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE ** attempt
                    logger.info(f"[GeminiGW] {wait}초 대기 후 재시도")
                    time.sleep(wait)

    # ── 전부 실패 → DLQ 저장 ──
    logger.error(f"[GeminiGW] 전부 실패 | model={model_name} | error={last_error[:200]}")

    try:
        from core.dlq import enqueue
        enqueue(
            task_type="gemini_call",
            payload={
                "prompt": prompt[:500],  # DLQ에는 프롬프트 앞부분만 저장
                "model": model,
                "system_instruction": system_instruction[:200],
            },
            error=last_error[:200],
        )
    except Exception as dlq_err:
        logger.warning(f"[GeminiGW] DLQ 저장 실패: {dlq_err}")

    return _fail_result(last_error, fallback_value)


def _fail_result(error: str, fallback_value: Optional[str] = None) -> dict:
    """실패 결과 반환"""
    return {
        "success": False,
        "text": fallback_value or "",
        "data": None,
        "model": "",
        "key_used": "",
        "error": error,
    }


def is_available() -> bool:
    """Gemini API 사용 가능 여부 (키 설정 확인만)"""
    return bool(GEMINI_API_KEY)


# ── 이미지 생성 모델 ──
IMAGE_MODEL = "gemini-2.5-flash-image"


def generate_image(prompt: str, output_path: str = None) -> dict:
    """
    Gemini Flash Image로 이미지 생성 — Main/Sub 키 자동 전환

    Args:
        prompt: 이미지 생성 프롬프트
        output_path: 저장 경로 (None이면 bytes만 반환)

    Returns:
        {
          "success": True/False,
          "image_bytes": bytes | None,
          "image_path": str | None,
          "key_used": "main" | "sub",
          "error": str,
        }
    """
    keys = []
    if GEMINI_API_KEY:
        keys.append(("main", GEMINI_API_KEY))
    if GEMINI_API_SUB_KEY:
        keys.append(("sub", GEMINI_API_SUB_KEY))
    if GEMINI_API_SUB_SUB_KEY:
        keys.append(("sub2", GEMINI_API_SUB_SUB_KEY))

    if not keys:
        return {"success": False, "image_bytes": None, "image_path": None,
                "key_used": "", "error": "GEMINI_API_KEY 미설정"}

    last_error = ""

    for key_label, api_key in keys:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                genai = _get_client(api_key)
                model = genai.GenerativeModel(IMAGE_MODEL)

                response = model.generate_content(
                    prompt,
                    generation_config={
                        "response_modalities": ["IMAGE", "TEXT"],
                        "max_output_tokens": 1024,
                    },
                )

                # 이미지 파트 추출
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        import base64
                        img_data = part.inline_data.data
                        img_bytes = base64.b64decode(img_data) if isinstance(img_data, str) else img_data

                        if len(img_bytes) > 500:
                            # 파일 저장
                            if output_path:
                                import os
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
                break  # 이미지 없으면 재시도 의미 없음 → 다음 키로

            except Exception as e:
                last_error = str(e)
                err_str = str(e)

                # 429 Rate Limit → 다음 키로 전환
                if "429" in err_str or "quota" in err_str.lower():
                    logger.warning(
                        f"[GeminiGW] 이미지 429 | key={key_label} | "
                        f"attempt={attempt} → 다음 키 전환"
                    )
                    break  # 이 키는 포기, 다음 키로

                # 기타 에러 → 백오프 후 재시도
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
