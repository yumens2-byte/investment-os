"""
collectors/news_rss.py (v1.5.0)
================================
기존 단일 Google News RSS 수집기.
v1.5.0부터 rss_extended.py가 주 수집기이며,
본 모듈은 rss_extended의 경량 wrapper로 유지한다.
하위 호환성: collect_news_sentiment() 시그니처 유지.
"""
import logging
from collectors.rss_extended import collect_extended_sentiment

logger = logging.getLogger(__name__)


def collect_news_sentiment() -> dict:
    """
    뉴스 감성 수집 — rss_extended.py로 위임.
    기존 호출 코드(run_market.py) 변경 없이 동작.
    """
    result = collect_extended_sentiment()

    # 하위 호환: 기존 키 보장
    return {
        "total_headlines": result.get("total_headlines", 0),
        "bullish_count": result.get("bullish_count", 0),
        "bearish_count": result.get("bearish_count", 0),
        "neutral_count": result.get("neutral_count", 0),
        "sentiment_score": result.get("net_weighted_score", 0.0),
        "news_sentiment": result.get("news_sentiment", "Neutral"),
        # v1.5.0 추가 필드
        "sources_ok": result.get("sources_ok", 0),
        "sources_fail": result.get("sources_fail", 0),
        "source_detail": result.get("source_detail", []),
    }
