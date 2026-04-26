"""
Investment OS — Viral Performance Metrics Store v1.0.0

viral_performance_metrics 테이블 DAO.
- save_metric(): UPSERT (log_id, milestone_hours)
- get_latest_metric(): 특정 log의 가장 최근 측정값
- get_metric_by_milestone(): 특정 마일스톤의 측정값
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db.supabase_client import get_engine

VERSION = "1.0.0"

logger = logging.getLogger(__name__)


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


_UPSERT_SQL = text("""
INSERT INTO viral_performance_metrics (
    log_id, tweet_id, milestone_hours, hours_since_publish,
    impression_count, like_count, reply_count, retweet_count,
    quote_count, bookmark_count, url_link_clicks, user_profile_clicks,
    engagement_rate, api_response_raw, fetch_status, fetch_error,
    measured_at
) VALUES (
    :log_id, :tweet_id, :milestone_hours, :hours_since_publish,
    :impression_count, :like_count, :reply_count, :retweet_count,
    :quote_count, :bookmark_count, :url_link_clicks, :user_profile_clicks,
    :engagement_rate, CAST(:api_response_raw AS jsonb), :fetch_status, :fetch_error,
    NOW()
)
ON CONFLICT (log_id, milestone_hours) DO UPDATE SET
    hours_since_publish = EXCLUDED.hours_since_publish,
    impression_count    = EXCLUDED.impression_count,
    like_count          = EXCLUDED.like_count,
    reply_count         = EXCLUDED.reply_count,
    retweet_count       = EXCLUDED.retweet_count,
    quote_count         = EXCLUDED.quote_count,
    bookmark_count      = EXCLUDED.bookmark_count,
    url_link_clicks     = EXCLUDED.url_link_clicks,
    user_profile_clicks = EXCLUDED.user_profile_clicks,
    engagement_rate     = EXCLUDED.engagement_rate,
    api_response_raw    = EXCLUDED.api_response_raw,
    fetch_status        = EXCLUDED.fetch_status,
    fetch_error         = EXCLUDED.fetch_error,
    measured_at         = EXCLUDED.measured_at
RETURNING metric_id
""")


def save_metric(metric: PerformanceMetric) -> str | None:
    """UPSERT. 동일 (log_id, milestone_hours) 조합은 갱신."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                _UPSERT_SQL,
                {
                    "log_id": metric.log_id,
                    "tweet_id": metric.tweet_id,
                    "milestone_hours": metric.milestone_hours,
                    "hours_since_publish": metric.hours_since_publish,
                    "impression_count": metric.impression_count,
                    "like_count": metric.like_count,
                    "reply_count": metric.reply_count,
                    "retweet_count": metric.retweet_count,
                    "quote_count": metric.quote_count,
                    "bookmark_count": metric.bookmark_count,
                    "url_link_clicks": metric.url_link_clicks,
                    "user_profile_clicks": metric.user_profile_clicks,
                    "engagement_rate": metric.engagement_rate,
                    "api_response_raw": (
                        json.dumps(metric.api_response_raw, ensure_ascii=False)
                        if metric.api_response_raw is not None
                        else None
                    ),
                    "fetch_status": metric.fetch_status,
                    "fetch_error": metric.fetch_error,
                },
            ).fetchone()
            metric_id = str(row[0]) if row else None
            logger.info(
                "[ViralMetricsStore] UPSERT log=%s milestone=%dh status=%s",
                metric.log_id,
                metric.milestone_hours,
                metric.fetch_status,
            )
            return metric_id
    except SQLAlchemyError as e:
        logger.error(
            "[ViralMetricsStore] UPSERT 실패 log=%s milestone=%dh: %s",
            metric.log_id,
            metric.milestone_hours,
            e,
        )
        return None


_GET_LATEST_SQL = text("""
SELECT
    metric_id, log_id, tweet_id, milestone_hours, hours_since_publish,
    impression_count, like_count, reply_count, retweet_count, quote_count,
    bookmark_count, url_link_clicks, user_profile_clicks, engagement_rate,
    fetch_status, fetch_error, measured_at
FROM viral_performance_metrics
WHERE log_id = :log_id AND fetch_status = 'success'
ORDER BY milestone_hours DESC, measured_at DESC
LIMIT 1
""")


def get_latest_metric(log_id: str) -> dict[str, Any] | None:
    """특정 log의 가장 늦은 마일스톤 측정값 (성공 케이스만)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(_GET_LATEST_SQL, {"log_id": log_id}).fetchone()
            if not row:
                return None
            return _row_to_dict(row)
    except SQLAlchemyError as e:
        logger.error("[ViralMetricsStore] get_latest 실패 log=%s: %s", log_id, e)
        return None


_GET_BY_MILESTONE_SQL = text("""
SELECT
    metric_id, log_id, tweet_id, milestone_hours, hours_since_publish,
    impression_count, like_count, reply_count, retweet_count, quote_count,
    bookmark_count, url_link_clicks, user_profile_clicks, engagement_rate,
    fetch_status, fetch_error, measured_at
FROM viral_performance_metrics
WHERE log_id = :log_id AND milestone_hours = :milestone_hours
LIMIT 1
""")


def get_metric_by_milestone(
    log_id: str, milestone_hours: int
) -> dict[str, Any] | None:
    """특정 (log_id, milestone) 측정값 조회 (성공/실패 무관)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                _GET_BY_MILESTONE_SQL,
                {"log_id": log_id, "milestone_hours": milestone_hours},
            ).fetchone()
            if not row:
                return None
            return _row_to_dict(row)
    except SQLAlchemyError as e:
        logger.error(
            "[ViralMetricsStore] get_by_milestone 실패 log=%s ms=%d: %s",
            log_id,
            milestone_hours,
            e,
        )
        return None


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Row → dict 변환."""
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    for k, v in d.items():
        if isinstance(v, Decimal):
            d[k] = float(v)
        elif isinstance(v, UUID):
            d[k] = str(v)
        elif isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat()
    return d
