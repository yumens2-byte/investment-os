"""
db/viral_metrics_store.py
================================
C-20 viral_performance_metrics 테이블 DAO.

VERSION = "1.1.0"

v1.1.0 (2026-04-26):
  - [긴급] SQLAlchemy 패턴 → supabase-py 패턴 전면 재작성
  - 마스터 시스템 db/supabase_client.py(get_client()) 100% 준수
  - 외부 인터페이스(함수 시그니처) 무변경 → tracker 영향 없음
  - UPSERT 키: (log_id, milestone_hours) 복합 UNIQUE 제약 활용

v1.0.0 (2026-04-26): 초기 버전 (SQLAlchemy 기반, deprecated)

함수 목록:
  save_metric()              — UPSERT (log_id, milestone_hours)
  get_latest_metric()        — 가장 최근 마일스톤 측정값 (성공만)
  get_metric_by_milestone()  — 특정 (log_id, milestone) 측정값
"""
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

VERSION = "1.1.0"


def _get_client():
    """Supabase 클라이언트 가져오기 (마스터 패턴)."""
    from db.supabase_client import get_client
    return get_client()


# ─────────────────────────────────────────────────────────────────────────
# 데이터 클래스 (외부 인터페이스 — tracker가 사용)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class PerformanceMetric:
    """측정 결과 데이터 클래스."""

    log_id: str
    tweet_id: str
    milestone_hours: int
    hours_since_publish: float
    impression_count: int | None
    like_count: int | None
    reply_count: int | None
    retweet_count: int | None
    quote_count: int | None
    bookmark_count: int | None
    url_link_clicks: int | None
    user_profile_clicks: int | None
    engagement_rate: float | None
    api_response_raw: dict[str, Any] | None
    fetch_status: str
    fetch_error: str | None = None

    @classmethod
    def from_x_response(
        cls,
        log_id: str,
        tweet_id: str,
        milestone_hours: int,
        hours_since_publish: float,
        x_response: dict[str, Any],
    ) -> "PerformanceMetric":
        """X API 응답에서 PerformanceMetric 생성."""
        data = x_response.get("data", {}) if isinstance(x_response, dict) else {}
        public = data.get("public_metrics", {}) or {}
        non_public = data.get("non_public_metrics", {}) or {}

        impression = non_public.get("impression_count")
        like = public.get("like_count")
        reply = public.get("reply_count")
        retweet = public.get("retweet_count")
        quote = public.get("quote_count")

        engagement_rate: float | None = None
        if impression and impression > 0:
            engagement_sum = (
                (like or 0) + (reply or 0) + (retweet or 0) + (quote or 0)
            )
            engagement_rate = round(engagement_sum / impression, 5)

        return cls(
            log_id=log_id,
            tweet_id=tweet_id,
            milestone_hours=milestone_hours,
            hours_since_publish=hours_since_publish,
            impression_count=impression,
            like_count=like,
            reply_count=reply,
            retweet_count=retweet,
            quote_count=quote,
            bookmark_count=public.get("bookmark_count"),
            url_link_clicks=non_public.get("url_link_clicks"),
            user_profile_clicks=non_public.get("user_profile_clicks"),
            engagement_rate=engagement_rate,
            api_response_raw=x_response if isinstance(x_response, dict) else None,
            fetch_status="success",
        )

    @classmethod
    def from_error(
        cls,
        log_id: str,
        tweet_id: str,
        milestone_hours: int,
        hours_since_publish: float,
        fetch_status: str,
        fetch_error: str,
    ) -> "PerformanceMetric":
        """오류 케이스 (rate_limited, tweet_deleted, expired, error)."""
        return cls(
            log_id=log_id,
            tweet_id=tweet_id,
            milestone_hours=milestone_hours,
            hours_since_publish=hours_since_publish,
            impression_count=None,
            like_count=None,
            reply_count=None,
            retweet_count=None,
            quote_count=None,
            bookmark_count=None,
            url_link_clicks=None,
            user_profile_clicks=None,
            engagement_rate=None,
            api_response_raw=None,
            fetch_status=fetch_status,
            fetch_error=fetch_error,
        )


# ─────────────────────────────────────────────────────────────────────────
# 1. UPSERT — save_metric
# ─────────────────────────────────────────────────────────────────────────


def save_metric(metric: PerformanceMetric) -> str | None:
    """
    UPSERT. 동일 (log_id, milestone_hours) 조합은 갱신.
    UNIQUE INDEX uq_perf_log_milestone(log_id, milestone_hours) 활용.

    Returns: metric_id (UUID 문자열) | None
    """
    try:
        row = {
            "log_id":              metric.log_id,
            "tweet_id":            metric.tweet_id,
            "milestone_hours":     metric.milestone_hours,
            "hours_since_publish": metric.hours_since_publish,
            "impression_count":    metric.impression_count,
            "like_count":          metric.like_count,
            "reply_count":         metric.reply_count,
            "retweet_count":       metric.retweet_count,
            "quote_count":         metric.quote_count,
            "bookmark_count":      metric.bookmark_count,
            "url_link_clicks":     metric.url_link_clicks,
            "user_profile_clicks": metric.user_profile_clicks,
            "engagement_rate":     metric.engagement_rate,
            "api_response_raw":    metric.api_response_raw,    # supabase-py: dict → jsonb
            "fetch_status":        metric.fetch_status,
            "fetch_error":         metric.fetch_error,
        }

        # 복합 키 UPSERT — supabase-py 지원: "log_id,milestone_hours"
        result = (
            _get_client()
            .table("viral_performance_metrics")
            .upsert(row, on_conflict="log_id,milestone_hours")
            .execute()
        )

        if result.data and len(result.data) > 0:
            metric_id = result.data[0].get("metric_id")
            logger.info(
                f"[ViralMetricsStore v{VERSION}] UPSERT log={metric.log_id} "
                f"milestone={metric.milestone_hours}h status={metric.fetch_status}"
            )
            return metric_id

        logger.warning("[ViralMetricsStore] UPSERT 응답에 data 없음")
        return None

    except Exception as e:
        logger.warning(
            f"[ViralMetricsStore] UPSERT 실패 log={metric.log_id} "
            f"milestone={metric.milestone_hours}h: {e}"
        )
        return None


# ─────────────────────────────────────────────────────────────────────────
# 2. SELECT — 조회 함수
# ─────────────────────────────────────────────────────────────────────────


def get_latest_metric(log_id: str) -> dict[str, Any] | None:
    """
    특정 log의 가장 늦은 마일스톤 측정값 (성공 케이스만).
    fetch_status='success' 필터링 후 milestone_hours DESC 정렬.
    """
    try:
        result = (
            _get_client()
            .table("viral_performance_metrics")
            .select(
                "metric_id, log_id, tweet_id, milestone_hours, hours_since_publish, "
                "impression_count, like_count, reply_count, retweet_count, "
                "quote_count, bookmark_count, url_link_clicks, user_profile_clicks, "
                "engagement_rate, fetch_status, fetch_error, measured_at"
            )
            .eq("log_id", log_id)
            .eq("fetch_status", "success")
            .order("milestone_hours", desc=True)
            .order("measured_at", desc=True)
            .limit(1)
            .execute()
        )

        if result.data and len(result.data) > 0:
            return result.data[0]
        return None

    except Exception as e:
        logger.warning(f"[ViralMetricsStore] get_latest 실패 log={log_id}: {e}")
        return None


def get_metric_by_milestone(
    log_id: str, milestone_hours: int
) -> dict[str, Any] | None:
    """특정 (log_id, milestone) 측정값 조회 (성공/실패 무관)."""
    try:
        result = (
            _get_client()
            .table("viral_performance_metrics")
            .select(
                "metric_id, log_id, tweet_id, milestone_hours, hours_since_publish, "
                "impression_count, like_count, reply_count, retweet_count, "
                "quote_count, bookmark_count, url_link_clicks, user_profile_clicks, "
                "engagement_rate, fetch_status, fetch_error, measured_at"
            )
            .eq("log_id", log_id)
            .eq("milestone_hours", milestone_hours)
            .limit(1)
            .execute()
        )

        if result.data and len(result.data) > 0:
            return result.data[0]
        return None

    except Exception as e:
        logger.warning(
            f"[ViralMetricsStore] get_by_milestone 실패 "
            f"log={log_id} ms={milestone_hours}: {e}"
        )
        return None
