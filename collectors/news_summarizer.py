"""
collectors/news_summarizer.py
================================
Claude API를 활용한 뉴스 헤드라인 3줄 요약

입력: RSS에서 수집한 헤드라인 리스트
출력: {
  "summary": ["요약1", "요약2", "요약3"],
  "implication": "ETF 투자 시사점 1줄",
  "raw": "전체 요약 텍스트"
}
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"
MAX_HEADLINES = 15  # 비용 절감: 상위 15개만 사용


def summarize_headlines(headlines: list) -> Optional[dict]:
    """
    뉴스 헤드라인 3줄 요약 + ETF 시사점

    Args:
        headlines: 영문 헤드라인 리스트

    Returns:
        요약 dict 또는 None (API 실패/미설정 시)
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("[NewsSummarizer] ANTHROPIC_API_KEY 미설정 — 스킵")
        return None

    if not headlines:
        logger.warning("[NewsSummarizer] 헤드라인 없음 — 스킵")
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("[NewsSummarizer] anthropic 패키지 미설치 — 스킵")
        return None

    # 상위 N개 헤드라인만 사용
    top_headlines = headlines[:MAX_HEADLINES]
    headlines_text = "\n".join(f"- {h}" for h in top_headlines)

    prompt = f"""다음 미국 금융/경제 뉴스 헤드라인을 분석해서 아래 형식으로만 답하세요.

헤드라인:
{headlines_text}

형식 (정확히 이 형식만, 다른 말 없이):
1. [핵심 요약 15자 이내]
2. [핵심 요약 15자 이내]
3. [핵심 요약 15자 이내]
시사점: [ETF 투자 영향 20자 이내]"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        text = message.content[0].text.strip()

        # 파싱
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        summaries = []
        implication = ""

        for line in lines:
            if line.startswith(("1.", "2.", "3.")):
                cleaned = line[2:].strip().lstrip(".")
                summaries.append(cleaned)
            elif line.startswith("시사점:"):
                implication = line.replace("시사점:", "").strip()

        if not summaries:
            return None

        result = {
            "summary":     summaries[:3],
            "implication": implication,
            "raw":         text,
        }
        logger.info(f"[NewsSummarizer] 요약 완료: {len(summaries)}줄")
        return result

    except Exception as e:
        logger.warning(f"[NewsSummarizer] Claude API 실패: {e}")
        return None
