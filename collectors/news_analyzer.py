"""
collectors/news_analyzer.py (B-16)
=====================================
Gemini 기반 뉴스 심층 분석

RSS 헤드라인을 Gemini Flash-Lite에 일괄 전달하여:
  - 핵심 이슈 Top 3 (topic + impact + confidence)
  - 종합 감성 (bullish / bearish / neutral)
  - 핵심 리스크 요약

기존 키워드 감성(rss_extended.py)은 유지 — fallback + 보조.
Gemini 실패 시 빈 분석 결과 반환 (시스템 영향 없음).

모델: Gemini 2.5 Flash-Lite (1,000 RPD — 하루 6세션 충분)
"""
import logging
from typing import List

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are a financial news analyst specializing in US markets.
Rules:
1. Respond ONLY in valid JSON format
2. Analyze headlines objectively — no investment advice
3. Focus on market-moving events
4. Assess impact on major indices (S&P 500, Nasdaq, bonds, commodities)
5. Confidence: 0.0~1.0 (0.5 = uncertain, 0.9+ = very confident)"""


def analyze_headlines(headlines: List[str]) -> dict:
    """
    RSS 헤드라인을 Gemini로 심층 분석.

    Args:
        headlines: RSS 수집된 헤드라인 리스트 (최대 30개)

    Returns:
        {
          "success": True/False,
          "source": "gemini" | "fallback",
          "top_issues": [
            {"topic": "Fed rate decision", "impact": "bullish", "confidence": 0.85,
             "summary": "연준 금리 인하 기대감 확대"},
            ...
          ],
          "overall_sentiment": "bearish",
          "key_risk": "중동 지정학 리스크로 유가 추가 상승 가능",
          "headline_count": 30,
        }
    """
    if not headlines:
        logger.info("[NewsAnalyzer] 헤드라인 없음 → 스킵")
        return _empty_result(0)

    from core.gemini_gateway import call, is_available

    if not is_available():
        logger.info("[NewsAnalyzer] Gemini 미설정 → 스킵")
        return _empty_result(len(headlines))

    # 헤드라인 30개로 제한
    headlines = headlines[:30]

    prompt = _build_prompt(headlines)

    result = call(
        prompt=prompt,
        model="flash-lite",
        system_instruction=SYSTEM_INSTRUCTION,
        max_tokens=800,
        temperature=0.3,  # 분석은 낮은 창의성
        response_json=True,
        fallback_value=None,
    )

    if result["success"] and result.get("data"):
        data = result["data"]
        analysis = _parse_response(data, len(headlines))
        logger.info(
            f"[NewsAnalyzer] Gemini 분석 완료 | "
            f"이슈 {len(analysis.get('top_issues', []))}건 | "
            f"감성 {analysis.get('overall_sentiment', '?')}"
        )
        return analysis

    # Gemini 실패 → 빈 결과 (기존 키워드 감성 유지)
    logger.warning(f"[NewsAnalyzer] Gemini 실패 → fallback: {result.get('error', '?')[:80]}")
    return _empty_result(len(headlines))


def _build_prompt(headlines: List[str]) -> str:
    """헤드라인 목록으로 프롬프트 생성"""
    headlines_text = "\n".join(f"- {h}" for h in headlines)

    return f"""Analyze the following {len(headlines)} financial news headlines and return a JSON response.

[Headlines]
{headlines_text}

Return JSON with this exact structure:
{{
  "top_issues": [
    {{"topic": "short topic name", "impact": "bullish|bearish|neutral", "confidence": 0.0-1.0, "summary": "Korean 1-line summary"}},
    {{"topic": "...", "impact": "...", "confidence": ..., "summary": "..."}},
    {{"topic": "...", "impact": "...", "confidence": ..., "summary": "..."}}
  ],
  "overall_sentiment": "bullish|bearish|neutral",
  "key_risk": "Korean 1-line key risk summary"
}}

Requirements:
- top_issues: exactly 3 most market-moving issues
- summary: must be in Korean
- key_risk: must be in Korean
- impact: bullish = positive for stocks, bearish = negative, neutral = unclear
- confidence: 0.5 = uncertain, 0.8+ = confident, 0.9+ = very confident"""


def _parse_response(data: dict, headline_count: int) -> dict:
    """Gemini JSON 응답을 정규화"""
    top_issues = data.get("top_issues", [])

    # top_issues 검증 및 정규화
    validated_issues = []
    for issue in top_issues[:3]:
        if not isinstance(issue, dict):
            continue
        validated_issues.append({
            "topic": str(issue.get("topic", "Unknown"))[:50],
            "impact": str(issue.get("impact", "neutral")).lower(),
            "confidence": min(1.0, max(0.0, float(issue.get("confidence", 0.5)))),
            "summary": str(issue.get("summary", ""))[:80],
        })

    sentiment = str(data.get("overall_sentiment", "neutral")).lower()
    if sentiment not in ("bullish", "bearish", "neutral"):
        sentiment = "neutral"

    return {
        "success": True,
        "source": "gemini",
        "top_issues": validated_issues,
        "overall_sentiment": sentiment,
        "key_risk": str(data.get("key_risk", ""))[:100],
        "headline_count": headline_count,
    }


def _empty_result(headline_count: int) -> dict:
    """빈 분석 결과 (Gemini 미사용/실패 시)"""
    return {
        "success": False,
        "source": "fallback",
        "top_issues": [],
        "overall_sentiment": "",
        "key_risk": "",
        "headline_count": headline_count,
    }
