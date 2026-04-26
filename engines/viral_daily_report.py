"""
Investment OS — Viral Daily Report v1.0.0

매일 KST 09:00 cron으로 실행:
1. 전일 viral_logs 집계 (발행/폐기/세그먼트 분포)
2. 전일~3일 전 metrics 집계 (24h/48h/68h/80h 평균)
3. 자동 삭제 통계
4. Notion 운영 현황 페이지에 자식 페이지로 리포트 발행
5. Telegram 무료 채널에 요약 발송

설계 원칙:
- 마스터의 메모리: Notion 운영 현황 = 3339208cbdc3810a83cdc8612944e30d
- 운영 점검 즉시 가능
- "보호 후보" 일일 알림 (impression 50 미만 + viral_score 70 이상)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

try:
    from db.supabase_client import get_engine
except ImportError as ie:
    raise RuntimeError(
        "db.supabase_client.get_engine() import 실패. "
        "마스터 시스템 모듈 명세 확인 필요."
    ) from ie

VERSION = "1.0.0"

KST = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)

# Notion 운영 현황 페이지 (마스터 메모리)
NOTION_OPS_PAGE_ID = "3339208cbdc3810a83cdc8612944e30d"


@dataclass
class DailySummary:
    """리포트 1일분 요약."""

    target_date: str
    published_count: int = 0
    discarded_count: int = 0
    discard_rate: float = 0.0
    avg_viral_score: float = 0.0
    segment_distribution: dict[str, int] = field(default_factory=dict)
    axis_distribution: dict[str, int] = field(default_factory=dict)
    auto_deleted_today: int = 0
    safety_blocks_today: dict[str, int] = field(default_factory=dict)
    avg_impression_24h: float | None = None
    avg_impression_80h: float | None = None
    avg_engagement_rate_80h: float | None = None
    protected_candidates: list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# 집계 SQL
# ─────────────────────────────────────────────────────────────────────────


_SQL_PUBLISHED_COUNT = text("""
SELECT
    COUNT(*) FILTER (WHERE is_published = true) AS published,
    COUNT(*) FILTER (WHERE is_published = false) AS discarded,
    AVG(viral_score) FILTER (WHERE is_published = true) AS avg_score
FROM viral_logs
WHERE publish_date = :target_date
""")


_SQL_SEGMENT_DIST = text("""
SELECT target_segment, COUNT(*) AS cnt
FROM viral_logs
WHERE publish_date = :target_date AND is_published = true
GROUP BY target_segment
ORDER BY cnt DESC
""")


_SQL_AXIS_DIST = text("""
SELECT conflict_axis, COUNT(*) AS cnt
FROM viral_logs
WHERE publish_date = :target_date AND is_published = true
GROUP BY conflict_axis
ORDER BY cnt DESC
""")


_SQL_AUTO_DELETED = text("""
SELECT COUNT(*) AS cnt
FROM viral_logs
WHERE is_deleted = true
  AND delete_mode = 'auto'
  AND deleted_at::date = :target_date
""")


_SQL_AVG_METRICS = text("""
SELECT
    AVG(impression_count) FILTER (WHERE milestone_hours = 24)::numeric(10,1) AS avg_imp_24,
    AVG(impression_count) FILTER (WHERE milestone_hours = 80)::numeric(10,1) AS avg_imp_80,
    AVG(engagement_rate) FILTER (WHERE milestone_hours = 80)::numeric(7,5) AS avg_er_80
FROM viral_performance_metrics m
JOIN viral_logs l ON m.log_id = l.log_id
WHERE l.publish_date = :target_date
  AND m.fetch_status = 'success'
""")


_SQL_PROTECTED_CANDIDATES = text("""
SELECT
    l.log_id, l.tweet_id, l.target_segment, l.conflict_axis,
    l.viral_score, m.impression_count, m.engagement_rate
FROM viral_logs l
JOIN viral_performance_metrics m ON l.log_id = m.log_id
WHERE l.is_published = true
  AND l.is_deleted = false
  AND l.protected = false
  AND m.milestone_hours = 80
  AND m.fetch_status = 'success'
  AND l.viral_score >= 70
  AND m.impression_count < 50
  AND l.created_at >= NOW() - INTERVAL '5 days'
ORDER BY l.created_at DESC
LIMIT 10
""")


# ─────────────────────────────────────────────────────────────────────────
# 메인 집계
# ─────────────────────────────────────────────────────────────────────────


def collect_daily_summary(target_date: str) -> DailySummary:
    """target_date(YYYY-MM-DD) 기준 1일치 요약."""
    summary = DailySummary(target_date=target_date)

    try:
        engine = get_engine()
        with engine.connect() as conn:
            # 1) 발행/폐기 카운트
            row = conn.execute(
                _SQL_PUBLISHED_COUNT, {"target_date": target_date}
            ).fetchone()
            if row:
                summary.published_count = int(row[0] or 0)
                summary.discarded_count = int(row[1] or 0)
                summary.avg_viral_score = float(row[2] or 0.0)
                total = summary.published_count + summary.discarded_count
                if total > 0:
                    summary.discard_rate = round(
                        summary.discarded_count / total, 3
                    )

            # 2) 세그먼트 분포
            for r in conn.execute(
                _SQL_SEGMENT_DIST, {"target_date": target_date}
            ):
                summary.segment_distribution[str(r[0])] = int(r[1])

            # 3) 갈등축 분포
            for r in conn.execute(_SQL_AXIS_DIST, {"target_date": target_date}):
                summary.axis_distribution[str(r[0])] = int(r[1])

            # 4) 오늘 자동 삭제 건수
            row = conn.execute(
                _SQL_AUTO_DELETED, {"target_date": target_date}
            ).fetchone()
            summary.auto_deleted_today = int(row[0] or 0) if row else 0

            # 5) metrics 평균
            row = conn.execute(
                _SQL_AVG_METRICS, {"target_date": target_date}
            ).fetchone()
            if row:
                summary.avg_impression_24h = (
                    float(row[0]) if row[0] is not None else None
                )
                summary.avg_impression_80h = (
                    float(row[1]) if row[1] is not None else None
                )
                summary.avg_engagement_rate_80h = (
                    float(row[2]) if row[2] is not None else None
                )

            # 6) 보호 후보
            for r in conn.execute(_SQL_PROTECTED_CANDIDATES):
                summary.protected_candidates.append(
                    {
                        "log_id": str(r[0]),
                        "tweet_id": str(r[1]),
                        "segment": str(r[2]),
                        "axis": str(r[3]),
                        "viral_score": int(r[4]),
                        "impression": int(r[5]) if r[5] is not None else 0,
                        "er": float(r[6]) if r[6] is not None else 0.0,
                    }
                )

    except SQLAlchemyError as e:
        logger.error("[ViralDailyReport] 집계 실패: %s", e)

    return summary


# ─────────────────────────────────────────────────────────────────────────
# Notion + Telegram 발송
# ─────────────────────────────────────────────────────────────────────────


def build_notion_content(summary: DailySummary) -> str:
    """Notion 페이지 본문 (Markdown)."""
    s = summary
    seg_lines = "\n".join(
        f"- {k}: {v}건" for k, v in sorted(s.segment_distribution.items())
    ) or "- (발행 없음)"
    axis_lines = "\n".join(
        f"- {k}: {v}건" for k, v in sorted(s.axis_distribution.items())
    ) or "- (발행 없음)"

    if s.protected_candidates:
        protected_lines = "\n".join(
            f"- log_id=`{p['log_id'][:8]}...` segment={p['segment']} "
            f"score={p['viral_score']} imp={p['impression']} er={p['er']:.4f}"
            for p in s.protected_candidates
        )
    else:
        protected_lines = "- (해당 없음)"

    avg_imp_24 = (
        f"{s.avg_impression_24h:.1f}" if s.avg_impression_24h is not None else "—"
    )
    avg_imp_80 = (
        f"{s.avg_impression_80h:.1f}" if s.avg_impression_80h is not None else "—"
    )
    avg_er_80 = (
        f"{s.avg_engagement_rate_80h:.4f}"
        if s.avg_engagement_rate_80h is not None
        else "—"
    )

    return (
        f"# Viral Daily Report — {s.target_date}\n\n"
        f"> 자동 생성 (viral_daily_report.py v{VERSION}) | 매일 09:00 KST\n\n"
        f"## 발행 요약\n\n"
        f"- 발행: **{s.published_count}건**\n"
        f"- 폐기: {s.discarded_count}건 (폐기율 {s.discard_rate * 100:.1f}%)\n"
        f"- 평균 viral_score: {s.avg_viral_score:.1f}\n\n"
        f"## 세그먼트 분포\n\n{seg_lines}\n\n"
        f"## 갈등축 분포\n\n{axis_lines}\n\n"
        f"## 성과 지표\n\n"
        f"- 평균 impression (24h): **{avg_imp_24}**\n"
        f"- 평균 impression (80h): **{avg_imp_80}**\n"
        f"- 평균 engagement_rate (80h): **{avg_er_80}**\n\n"
        f"## 자동 폐기\n\n"
        f"- 오늘 자동 삭제: **{s.auto_deleted_today}건**\n\n"
        f"## 보호 후보 (운영자 검토 필요)\n\n"
        f"_high score(>=70) + low impression(<50)인 발행물._\n\n"
        f"{protected_lines}\n"
    )


def build_telegram_summary(summary: DailySummary) -> str:
    """TG 무료 채널 요약 (HTML)."""
    s = summary
    avg_imp_24 = (
        f"{s.avg_impression_24h:.0f}" if s.avg_impression_24h is not None else "—"
    )

    return (
        f"📊 <b>Viral Daily Report — {s.target_date}</b>\n\n"
        f"📤 발행 {s.published_count}건 / 폐기 {s.discarded_count}건 "
        f"({s.discard_rate * 100:.0f}%)\n"
        f"⭐️ 평균 score {s.avg_viral_score:.1f}\n"
        f"👀 24h imp {avg_imp_24}\n"
        f"🗑 자동삭제 {s.auto_deleted_today}건\n"
        f"⚠️ 보호후보 {len(s.protected_candidates)}건\n\n"
        f"📝 상세: Notion 운영 현황 페이지 자식\n"
    )


def publish_to_notion(summary: DailySummary) -> str | None:
    """Notion 운영 현황 페이지 하위에 자식 페이지로 발행."""
    try:
        import requests

        api_key = os.getenv("NOTION_API_KEY")
        if not api_key:
            logger.warning("[ViralDailyReport] NOTION_API_KEY 없음 → Notion 스킵")
            return None

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        content_md = build_notion_content(summary)
        blocks = []
        for line in content_md.split("\n"):
            if not line.strip():
                continue
            if line.startswith("# "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_1",
                        "heading_1": {
                            "rich_text": [
                                {"type": "text", "text": {"content": line[2:]}}
                            ]
                        },
                    }
                )
            elif line.startswith("## "):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [
                                {"type": "text", "text": {"content": line[3:]}}
                            ]
                        },
                    }
                )
            else:
                blocks.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "text": {"content": line[:1900]}}
                            ]
                        },
                    }
                )

        body = {
            "parent": {"page_id": NOTION_OPS_PAGE_ID},
            "properties": {
                "title": {
                    "title": [
                        {
                            "text": {
                                "content": f"Viral Daily Report — {summary.target_date}"
                            }
                        }
                    ]
                }
            },
            "children": blocks[:100],
        }

        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json=body,
            timeout=30,
        )
        if r.status_code == 200:
            page_id = r.json().get("id")
            logger.info("[ViralDailyReport] Notion 페이지 생성 완료 page=%s", page_id)
            return page_id
        else:
            logger.error(
                "[ViralDailyReport] Notion API 실패 status=%d body=%s",
                r.status_code,
                r.text[:200],
            )
            return None
    except Exception as e:
        logger.error("[ViralDailyReport] Notion 발행 예외: %s", e)
        return None


def publish_to_telegram(summary: DailySummary) -> bool:
    """TG 무료 채널에 요약 발송."""
    try:
        from publishers.telegram_publisher import send_message

        send_message(build_telegram_summary(summary), channel="free")
        logger.info("[ViralDailyReport] TG 발송 완료")
        return True
    except Exception as e:
        logger.warning("[ViralDailyReport] TG 발송 실패: %s", e)
        return False


def main(target_date: str | None = None) -> dict[str, Any]:
    """cron 진입점. 매일 09:00 KST. target_date 미지정 시 어제."""
    logger.info("[ViralDailyReport] v%s 시작", VERSION)

    if target_date is None:
        yesterday = (datetime.now(KST) - timedelta(days=1)).date()
        target_date = yesterday.strftime("%Y-%m-%d")

    summary = collect_daily_summary(target_date)
    logger.info(
        "[ViralDailyReport] 집계 완료 published=%d discarded=%d auto_deleted=%d",
        summary.published_count,
        summary.discarded_count,
        summary.auto_deleted_today,
    )

    notion_page_id = publish_to_notion(summary)
    tg_ok = publish_to_telegram(summary)

    return {
        "success": True,
        "target_date": target_date,
        "published_count": summary.published_count,
        "discarded_count": summary.discarded_count,
        "auto_deleted_today": summary.auto_deleted_today,
        "notion_page_id": notion_page_id,
        "tg_ok": tg_ok,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    result = main()
    print(result)
