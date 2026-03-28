"""
collectors/reddit_client.py (v1.5.0 — DEPRECATED)
===================================================
Reddit API 2023년 유료화로 인해 본 모듈은 비활성화되었다.
대체 모듈: collectors/rss_extended.py

하위 호환성 유지를 위해 collect_reddit_sentiment()는
"available: False" 고정값을 반환한다.
run_market.py는 이 값을 무시하고 RSS 감성만 사용한다.
"""
import logging

logger = logging.getLogger(__name__)


def collect_reddit_sentiment() -> dict:
    """
    DEPRECATED — Reddit API 유료화로 비활성화.
    항상 available=False 반환.
    """
    logger.info("[Reddit] 비활성화됨 (유료화) — rss_extended.py 사용")
    return {
        "available": False,
        "reddit_sentiment": "Unknown",
        "total_posts": 0,
        "bullish_count": 0,
        "bearish_count": 0,
        "sentiment_score": 0,
        "reason": "Reddit API deprecated due to paid tier requirement (2023.06)",
    }
