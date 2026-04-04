"""
publishers/translator.py (C-11)
================================
Gemini 기반 다국어 번역 모듈

한국어 TG 콘텐츠를 영어/일본어로 번역하여
동일 무료 채널에 함께 발행.

Gemini 실패 시 스킵 (한국어 발행에 영향 없음).
"""
import logging
import os

logger = logging.getLogger(__name__)

# 다국어 발행 ON/OFF (환경변수, 기본 true)
MULTILINGUAL_ENABLED = os.getenv("MULTILINGUAL_ENABLED", "true").lower() in ("true", "1", "yes")


def translate_text(text: str, target_lang: str) -> str:
    """
    한국어 텍스트를 대상 언어로 번역.

    Args:
        text: 한국어 원문
        target_lang: "en" (영어) 또는 "ja" (일본어)

    Returns:
        번역된 텍스트 (실패 시 빈 문자열)
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return ""

        lang_name = {"en": "English", "ja": "Japanese"}.get(target_lang, "English")
        lang_flag = {"en": "🇺🇸", "ja": "🇯🇵"}.get(target_lang, "")

        prompt = (
            f"아래 한국어 투자 분석 콘텐츠를 {lang_name}로 번역해줘.\n"
            f"조건:\n"
            f"- ETF/지표 이름은 원문 유지 (SPY, VIX, WTI, XLE 등)\n"
            f"- 투자 용어는 해당 언어 관례대로 (강세장=bullish market 등)\n"
            f"- 이모지 유지\n"
            f"- 해시태그는 영어로 변환 (#ETF #Investment #OilShock)\n"
            f"- HTML 태그가 있으면 유지 (<b>, <i> 등)\n"
            f"- 번역문만 출력, 설명 없이\n\n"
            f"원문:\n{text}"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=500,
            temperature=0.3,
        )

        if result.get("success"):
            translated = result["text"].strip()
            translated = translated.strip('"').strip("'").strip("`")
            if len(translated) > 20:
                logger.info(
                    f"[Translator] {target_lang} 번역 완료 ({len(translated)}자)"
                )
                return translated

        logger.warning(f"[Translator] {target_lang} 번역 실패")

    except Exception as e:
        logger.warning(f"[Translator] {target_lang} 번역 실패 (무시): {e}")

    return ""


def publish_multilingual(korean_text: str) -> dict:
    """
    한국어 TG 텍스트를 영어/일본어로 번역하여 동일 무료 채널에 발행.

    Args:
        korean_text: 한국어 원문 (TG 포맷)

    Returns:
        {"en": True/False, "ja": True/False}
    """
    results = {"en": False, "ja": False}

    if not MULTILINGUAL_ENABLED:
        logger.info("[Translator] 다국어 비활성화 → 스킵")
        return results

    from publishers.telegram_publisher import send_message

    # 영어 번역 + 같은 무료 채널 발행
    en_text = translate_text(korean_text, "en")
    if en_text:
        try:
            send_message(f"🇺🇸 <b>English</b>\n\n{en_text}", channel="free")
            results["en"] = True
            logger.info("[Translator] EN 발행 완료 (무료 채널)")
        except Exception as e:
            logger.warning(f"[Translator] EN 발행 실패: {e}")

    # 일본어 번역 + 같은 무료 채널 발행
    ja_text = translate_text(korean_text, "ja")
    if ja_text:
        try:
            send_message(f"🇯🇵 <b>日本語</b>\n\n{ja_text}", channel="free")
            results["ja"] = True
            logger.info("[Translator] JP 발행 완료 (무료 채널)")
        except Exception as e:
            logger.warning(f"[Translator] JP 발행 실패: {e}")

    return results
