"""
engines/streamer_analyzer.py (C-16)
===================================
미장 유튜버 영상 통합 요약 → 1트윗 생성.

Gemini로 유튜버 영상 제목/설명을 분석하여:
  - 전문가 시각 통합 요약 (출처 없이)
  - 전체 방향 판단 (bullish/bearish/neutral)
  - 280자 이내 1트윗 생성

RPD: +1/일 (Gemini flash-lite)
VERSION = "1.0.0"
"""
import json
import logging

logger = logging.getLogger(__name__)

# ── Gemini 프롬프트 ──────────────────────────────────────────
SUMMARY_PROMPT = """다음은 미국주식 전문 유튜버들의 최근 영상 제목과 설명입니다.
이 영상들의 핵심 시장 시각을 통합 요약해주세요.

{video_list}

조건:
1. 개별 유튜버 이름이나 채널명을 절대 언급하지 마세요.
2. "전문가들의 시각" 또는 "미장 전문가 시각 종합"으로만 표현하세요.
3. 전체 방향을 bullish/bearish/neutral 중 하나로 판단하세요.
4. 주요 의견 3~4개를 bullet point로 정리하세요.
5. 반드시 JSON으로만 응답하세요. markdown 금지.

응답 형식:
{{
  "direction": "bearish",
  "summary_points": ["VIX 상승 추세 지속 우려", "WTI $110+ 에너지 과열", "반도체 순환 종료 시그널"],
  "tweet": "🎤 미장 전문가 시각 종합\\n\\n주요 의견:\\n• VIX 상승 추세 → 변동성 확대 경계\\n• WTI $110+ → 에너지 섹터 과열 우려\\n• 반도체 순환 종료 시그널 감지\\n\\n전체 방향: 보수적 관망 우세\\n\\n⚠️ 투자 참고 정보, 투자 권유 아님"
}}
"""


def analyze_streamer_content(videos: list) -> dict:
    """
    유튜버 영상 목록 → Gemini 통합 요약 → 1트윗 생성.

    Args:
        videos: collect_youtube_summaries()의 videos 배열

    Returns:
        {
          "success": True,
          "direction": "bearish",
          "summary_points": [...],
          "tweet": "🎤 미장 전문가 시각 종합...",
          "video_count": 5,
        }
    """
    if not videos:
        return {"success": False, "error": "분석할 영상 없음"}

    # 영상 목록 텍스트 구성
    video_text_parts = []
    for i, v in enumerate(videos, 1):
        desc_part = f" — {v['description'][:200]}" if v.get("description") else ""
        video_text_parts.append(f"{i}. [{v['title']}]{desc_part}")

    video_list = "\n".join(video_text_parts)
    prompt = SUMMARY_PROMPT.format(video_list=video_list)

    # ── Gemini 호출 ──
    try:
        from core.gemini_gateway import call_gemini
        raw = call_gemini(prompt, purpose="streamer_analysis")

        if not raw:
            return _fallback_analysis(videos)

        # JSON 파싱
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(cleaned)

        # 트윗 길이 검증
        tweet = result.get("tweet", "")
        if len(tweet) > 280:
            # 280자 초과 시 축약
            tweet = tweet[:277] + "..."

        logger.info(f"[StreamerAnalyzer] 분석 완료: {result.get('direction', '?')} ({len(videos)}건)")

        return {
            "success": True,
            "direction": result.get("direction", "neutral"),
            "summary_points": result.get("summary_points", []),
            "tweet": tweet,
            "video_count": len(videos),
        }

    except json.JSONDecodeError as e:
        logger.warning(f"[StreamerAnalyzer] JSON 파싱 실패: {e}")
        return _fallback_analysis(videos)
    except Exception as e:
        logger.warning(f"[StreamerAnalyzer] Gemini 호출 실패: {e}")
        return _fallback_analysis(videos)


def _fallback_analysis(videos: list) -> dict:
    """Gemini 실패 시 제목 기반 fallback 트윗"""
    titles = [v["title"] for v in videos[:4]]
    title_text = " / ".join(titles)
    if len(title_text) > 200:
        title_text = title_text[:197] + "..."

    tweet = (
        f"🎤 미장 전문가 시각 종합\n\n"
        f"최근 주요 영상:\n{title_text}\n\n"
        f"⚠️ 투자 참고 정보, 투자 권유 아님"
    )

    return {
        "success": True,
        "direction": "neutral",
        "summary_points": titles,
        "tweet": tweet[:280],
        "video_count": len(videos),
        "fallback": True,
    }
