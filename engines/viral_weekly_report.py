"""
engines/viral_weekly_report.py
================================
B안: 주간 딜레마 성적표 — viral_logs + viral_performance_metrics 기반
"이번 주 가장 뜨거웠던 선택 Top3" X/TG 발행.

VERSION = "1.0.0"

v1.0.0 (2026-08-10):
  - 신규. AI 호출 0회 / 신규 테이블 0개.
  - 조회 패턴: engines/viral_daily_report.py의 2단 조회(logs → metrics in_) 준수.
  - 데이터 3건 미만이면 미발행(조용히 skip) — 지침 6(추측값 금지).

호출: run_weekend._run_saturday() 말미 독립 try/except 블록.
"""
from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

KST = timezone(timedelta(hours=9))

# 발행 최소 건수 (미만이면 skip)
MIN_ITEMS = 3
TOP_LIMIT = 3

# 안티봇: 문구 풀 (동일 문구 반복 금지)
_HEADERS = [
    "🏆 이번 주 가장 뜨거웠던 선택 Top3",
    "🔥 지난 7일, 여러분이 가장 반응한 딜레마 Top3",
    "📊 주간 결산 — 댓글창이 불탔던 선택지",
    "🏆 한 주 정산: 최다 반응 A/B Top3",
    "🔥 이번 주 타임라인을 흔든 선택 3가지",
]
_FOOTERS = [
    "다음 주도 매일 아침, 새로운 선택이 갑니다 👀",
    "당신의 선택은 몇 위였나요? 다음 주에 또 만나요 🙌",
    "월요일 아침, 새 딜레마로 돌아옵니다 ⏰",
    "다음 주 1위는 당신의 댓글이 정합니다 👇",
    "새로운 한 주, 더 어려운 선택으로 찾아갑니다 🎯",
]


def _get_client():
    """Supabase 클라이언트 (마스터 패턴 — 지연 import)."""
    from db.supabase_client import get_client
    return get_client()


def _is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() != "false"


def _force_run() -> bool:
    return os.environ.get("FORCE_RUN", "false").lower() == "true"


# ─────────────────────────────────────────────────────────────────────────
# 1. 조회 — 최근 7일 발행분 + metrics JOIN
# ─────────────────────────────────────────────────────────────────────────

def collect_weekly_top(days: int = 7, limit: int = TOP_LIMIT) -> list[dict[str, Any]]:
    """
    최근 N일 발행 딜레마를 engagement_rate 기준 Top N으로 반환.

    metrics는 milestone 80h(fetch_status=success) 우선, 없으면 48h fallback.
    반환 항목: {opt_a, opt_b, engagement_rate, reply_count, like_count,
               impression_count, milestone_hours}
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        log_result = (
            _get_client()
            .table("viral_logs")
            .select("log_id, opt_a, opt_b, tweet_id, created_at")
            .eq("is_published", True)
            .eq("is_deleted", False)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        candidates = log_result.data or []
        if not candidates:
            logger.info("[ViralWeekly] 최근 7일 발행 로그 없음")
            return []

        log_ids = [c["log_id"] for c in candidates]

        # 80h 우선 → 없으면 48h fallback (log_id 단위)
        metrics_by_log: dict[str, dict] = {}
        for milestone in (80, 48):
            missing = [lid for lid in log_ids if lid not in metrics_by_log]
            if not missing:
                break
            m_result = (
                _get_client()
                .table("viral_performance_metrics")
                .select(
                    "log_id, milestone_hours, impression_count, like_count, "
                    "reply_count, engagement_rate"
                )
                .in_("log_id", missing)
                .eq("milestone_hours", milestone)
                .eq("fetch_status", "success")
                .execute()
            )
            for m in (m_result.data or []):
                metrics_by_log[m["log_id"]] = m

        items: list[dict[str, Any]] = []
        for c in candidates:
            m = metrics_by_log.get(c["log_id"])
            if m is None:
                continue
            items.append({
                "opt_a": c.get("opt_a") or "",
                "opt_b": c.get("opt_b") or "",
                "engagement_rate": float(m.get("engagement_rate") or 0.0),
                "reply_count": m.get("reply_count"),
                "like_count": m.get("like_count"),
                "impression_count": m.get("impression_count"),
                "milestone_hours": m.get("milestone_hours"),
            })

        # engagement_rate DESC, 동률 시 reply_count DESC
        items.sort(
            key=lambda x: (x["engagement_rate"], x["reply_count"] or 0),
            reverse=True,
        )
        return items[:limit]

    except Exception as e:
        logger.warning(f"[ViralWeekly] 주간 Top 조회 실패: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────
# 2. 포맷 — 트윗/TG 텍스트 조립
# ─────────────────────────────────────────────────────────────────────────

def _shorten(text: str, max_len: int = 40) -> str:
    text = (text or "").strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _format_item_line(rank: int, item: dict[str, Any]) -> str:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    medal = medals.get(rank, f"{rank}.")
    a = _shorten(item["opt_a"])
    b = _shorten(item["opt_b"])
    stats: list[str] = []
    if item.get("reply_count") is not None:
        stats.append(f"💬{item['reply_count']}")
    if item.get("like_count") is not None:
        stats.append(f"❤️{item['like_count']}")
    # impression은 non_public — 미제공(None) 시 표기 생략
    if item.get("impression_count") is not None:
        stats.append(f"👀{item['impression_count']}")
    stat_str = " · ".join(stats)
    line = f"{medal} {a}\n   vs {b}"
    if stat_str:
        line += f"\n   {stat_str}"
    return line


def build_scoreboard_tweet(items: list[dict[str, Any]]) -> tuple[str, str]:
    """(X 트윗, TG HTML) 텍스트 반환."""
    header = random.choice(_HEADERS)
    footer = random.choice(_FOOTERS)

    lines = [header, ""]
    for i, item in enumerate(items, 1):
        lines.append(_format_item_line(i, item))
        lines.append("")
    lines.append(footer)
    lines.append("")
    lines.append("⚠️ 투자 참고 정보, 투자 권유 아님")
    tweet = "\n".join(lines)
    if len(tweet) > 3900:
        tweet = tweet[:3897] + "..."

    tg_lines = [f"<b>{header}</b>", ""]
    for i, item in enumerate(items, 1):
        tg_lines.append(_format_item_line(i, item))
        tg_lines.append("")
    tg_lines.append(footer)
    tg_text = "\n".join(tg_lines)

    return tweet, tg_text


# ─────────────────────────────────────────────────────────────────────────
# 3. 메인 — run_weekly_scoreboard
# ─────────────────────────────────────────────────────────────────────────

def run_weekly_scoreboard() -> dict:
    """B안 메인. 토요일 run_weekend에서 호출."""
    logger.info(f"[ViralWeekly] v{VERSION} 시작")

    # 토요일 가드 (수동 재실행 중복 방지, FORCE_RUN 시 우회)
    if not _force_run():
        weekday = datetime.now(KST).weekday()
        if weekday != 5:
            logger.info(f"[ViralWeekly] 토요일 아님(weekday={weekday}) → skip")
            return {"success": False, "reason": "not_saturday"}

    items = collect_weekly_top()
    if len(items) < MIN_ITEMS:
        logger.info(
            f"[ViralWeekly] 데이터 부족 ({len(items)}/{MIN_ITEMS}건) → 미발행"
        )
        return {"success": False, "reason": "insufficient_data", "count": len(items)}

    tweet, tg_text = build_scoreboard_tweet(items)

    tweet_id = "SKIP"
    try:
        from publishers.x_publisher import publish_tweet
        pub = publish_tweet(tweet)
        tweet_id = pub.get("tweet_id", "FAIL")
        logger.info(f"[ViralWeekly] X 발행: {tweet_id}")
    except Exception as e:
        logger.warning(f"[ViralWeekly] X 발행 실패: {e}")
        tweet_id = "FAIL"

    try:
        from publishers.telegram_publisher import send_message
        send_message(tg_text, channel="free")
        logger.info("[ViralWeekly] TG 발행 완료")
    except Exception as e:
        logger.warning(f"[ViralWeekly] TG 발행 실패: {e}")

    return {
        "success": tweet_id not in ("FAIL",),
        "type": "weekly_scoreboard",
        "tweet_id": tweet_id,
        "count": len(items),
    }
