"""
engines/viral_daily_report.py
================================
C-20 일일 리포트 — 매일 KST 09:00 cron 실행.

VERSION = "1.1.0"

v1.1.0 (2026-04-26):
  - [긴급] SQLAlchemy 패턴 → supabase-py 패턴 전면 재작성
  - 복잡한 SQL 집계(PERCENTILE_CONT/FILTER) → Python 단 집계로 전환
  - 마스터 시스템 db/supabase_query.py 패턴 100% 준수

v1.0.0 (2026-04-26): 초기 버전 (SQLAlchemy 기반, deprecated)

플로우:
  1. 전일 viral_logs 조회 → Python 단에서 집계
  2. 전일 viral_performance_metrics 조회 → 평균/백분위 계산
  3. 자동 삭제 통계 집계
  4. Notion 운영 현황 페이지에 자식 페이지로 발행
  5. Telegram 무료 채널에 요약 발송

Notion 운영 현황 페이지: 3339208cbdc3810a83cdc8612944e30d
"""
import logging
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

VERSION = "1.1.0"

KST = timezone(timedelta(hours=9))

# Notion 운영 현황 페이지 (마스터 메모리)
NOTION_OPS_PAGE_ID = "3339208cbdc3810a83cdc8612944e30d"


def _get_client():
    """Supabase 클라이언트 가져오기 (마스터 패턴)."""
    from db.supabase_client import get_client
    return get_client()


# ─────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────


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
    avg_impression_24h: float | None = None
    avg_impression_80h: float | None = None
    avg_engagement_rate_80h: float | None = None
    p50_impression_80h: float | None = None
    p80_impression_80h: float | None = None
    protected_candidates: list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# 집계 함수 (Python 단 — supabase-py 단순 query만 사용)
# ─────────────────────────────────────────────────────────────────────────


def _aggregate_logs(target_date: str) -> dict[str, Any]:
    """target_date의 viral_logs 조회 + Python 집계."""
    out = {
        "published": 0,
        "discarded": 0,
        "avg_score": 0.0,
        "segments": {},
        "axes": {},
        "log_ids_published": [],
    }

    try:
        result = (
            _get_client()
            .table("viral_logs")
            .select(
                "log_id, target_segment, conflict_axis, "
                "is_published, viral_score"
            )
            .eq("publish_date", target_date)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"[ViralDailyReport] viral_logs 조회 실패: {e}")
        return out

    score_sum = 0
    for r in rows:
        if r.get("is_published"):
            out["published"] += 1
            seg = r.get("target_segment") or "unknown"
            ax = r.get("conflict_axis") or "unknown"
            out["segments"][seg] = out["segments"].get(seg, 0) + 1
            out["axes"][ax] = out["axes"].get(ax, 0) + 1
            score_sum += int(r.get("viral_score") or 0)
            out["log_ids_published"].append(r["log_id"])
        else:
            out["discarded"] += 1

    if out["published"] > 0:
        out["avg_score"] = round(score_sum / out["published"], 1)

    return out


def _aggregate_metrics(log_ids: list[str]) -> dict[str, Any]:
    """log_ids에 해당하는 metrics 조회 + Python 단 평균/백분위 계산."""
    out: dict[str, Any] = {
        "avg_imp_24": None,
        "avg_imp_80": None,
        "avg_er_80": None,
        "p50_imp_80": None,
        "p80_imp_80": None,
    }

    if not log_ids:
        return out

    try:
        # supabase-py: .in_() 으로 IN 절 처리
        result = (
            _get_client()
            .table("viral_performance_metrics")
            .select(
                "log_id, milestone_hours, fetch_status, "
                "impression_count, engagement_rate"
            )
            .in_("log_id", log_ids)
            .eq("fetch_status", "success")
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"[ViralDailyReport] metrics 조회 실패: {e}")
        return out

    imp_24: list[float] = []
    imp_80: list[float] = []
    er_80: list[float] = []

    for r in rows:
        ms = r.get("milestone_hours")
        imp = r.get("impression_count")
        er = r.get("engagement_rate")

        if ms == 24 and imp is not None:
            imp_24.append(float(imp))
        elif ms == 80:
            if imp is not None:
                imp_80.append(float(imp))
            if er is not None:
                er_80.append(float(er))

    if imp_24:
        out["avg_imp_24"] = round(sum(imp_24) / len(imp_24), 1)
    if imp_80:
        out["avg_imp_80"] = round(sum(imp_80) / len(imp_80), 1)
        if len(imp_80) >= 2:
            sorted_imp = sorted(imp_80)
            out["p50_imp_80"] = round(statistics.median(sorted_imp), 1)
            # p80 — 80번째 백분위 (정확한 PERCENTILE_CONT 모사)
            out["p80_imp_80"] = round(_percentile(sorted_imp, 0.80), 1)
        else:
            out["p50_imp_80"] = round(imp_80[0], 1)
            out["p80_imp_80"] = round(imp_80[0], 1)
    if er_80:
        out["avg_er_80"] = round(sum(er_80) / len(er_80), 5)

    return out


def _percentile(sorted_values: list[float], q: float) -> float:
    """PostgreSQL PERCENTILE_CONT 동작 모사 (선형 보간)."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # PERCENTILE_CONT: position = q * (n-1)
    pos = q * (n - 1)
    lower = int(pos)
    upper = min(lower + 1, n - 1)
    frac = pos - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def _count_auto_deleted(target_date: str) -> int:
    """target_date에 자동 삭제된 건수."""
    try:
        # deleted_at은 timestamptz → target_date 하루 범위로 조회
        # KST 자정 기준 변환
        dt_start = datetime.fromisoformat(f"{target_date}T00:00:00+09:00")
        dt_end = dt_start + timedelta(days=1)
        start_iso = dt_start.astimezone(timezone.utc).isoformat()
        end_iso = dt_end.astimezone(timezone.utc).isoformat()

        result = (
            _get_client()
            .table("viral_logs")
            .select("log_id", count="exact")
            .eq("is_deleted", True)
            .eq("delete_mode", "auto")
            .gte("deleted_at", start_iso)
            .lt("deleted_at", end_iso)
            .execute()
        )
        return int(result.count) if result.count is not None else 0

    except Exception as e:
        logger.warning(f"[ViralDailyReport] auto_deleted 조회 실패: {e}")
        return 0


def _find_protected_candidates() -> list[dict[str, Any]]:
    """high score(>=70) + low impression(<50) 보호 후보 조회."""
    try:
        # Step 1: 최근 5일 발행분 조회 (score>=70, 미보호, 미삭제)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        log_result = (
            _get_client()
            .table("viral_logs")
            .select(
                "log_id, tweet_id, target_segment, conflict_axis, "
                "viral_score, created_at"
            )
            .eq("is_published", True)
            .eq("is_deleted", False)
            .eq("protected", False)
            .gte("viral_score", 70)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        candidates = log_result.data or []
        if not candidates:
            return []

        # Step 2: 80h metrics 일괄 조회
        log_ids = [c["log_id"] for c in candidates]
        metric_result = (
            _get_client()
            .table("viral_performance_metrics")
            .select("log_id, impression_count, engagement_rate")
            .in_("log_id", log_ids)
            .eq("milestone_hours", 80)
            .eq("fetch_status", "success")
            .execute()
        )
        metrics_by_log = {m["log_id"]: m for m in (metric_result.data or [])}

        # Step 3: Python 단에서 imp<50 필터
        protected: list[dict[str, Any]] = []
        for c in candidates:
            m = metrics_by_log.get(c["log_id"])
            if m is None:
                continue
            imp = m.get("impression_count")
            if imp is not None and imp < 50:
                protected.append({
                    "log_id":      c["log_id"],
                    "tweet_id":    c["tweet_id"],
                    "segment":     c["target_segment"],
                    "axis":        c["conflict_axis"],
                    "viral_score": c["viral_score"],
                    "impression":  int(imp),
                    "er":          float(m.get("engagement_rate") or 0.0),
                })
                if len(protected) >= 10:
                    break

        return protected

    except Exception as e:
        logger.warning(f"[ViralDailyReport] protected_candidates 조회 실패: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────
# 메인 집계 함수
# ─────────────────────────────────────────────────────────────────────────


def collect_daily_summary(target_date: str) -> DailySummary:
    """target_date(YYYY-MM-DD) 기준 1일치 요약."""
    summary = DailySummary(target_date=target_date)

    # 1) 발행/폐기 + 분포 집계
    log_agg = _aggregate_logs(target_date)
    summary.published_count = log_agg["published"]
    summary.discarded_count = log_agg["discarded"]
    summary.avg_viral_score = log_agg["avg_score"]
    summary.segment_distribution = log_agg["segments"]
    summary.axis_distribution = log_agg["axes"]
    total = summary.published_count + summary.discarded_count
    if total > 0:
        summary.discard_rate = round(summary.discarded_count / total, 3)

    # 2) metrics 평균/백분위
    metric_agg = _aggregate_metrics(log_agg["log_ids_published"])
    summary.avg_impression_24h = metric_agg["avg_imp_24"]
    summary.avg_impression_80h = metric_agg["avg_imp_80"]
    summary.avg_engagement_rate_80h = metric_agg["avg_er_80"]
    summary.p50_impression_80h = metric_agg["p50_imp_80"]
    summary.p80_impression_80h = metric_agg["p80_imp_80"]

    # 3) 자동 삭제 건수
    summary.auto_deleted_today = _count_auto_deleted(target_date)

    # 4) 보호 후보
    summary.protected_candidates = _find_protected_candidates()

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
    p50 = f"{s.p50_impression_80h:.0f}" if s.p50_impression_80h is not None else "—"
    p80 = f"{s.p80_impression_80h:.0f}" if s.p80_impression_80h is not None else "—"

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
        f"- 평균 engagement_rate (80h): **{avg_er_80}**\n"
        f"- 80h impression p50: {p50} / p80: {p80}\n\n"
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
                blocks.append({
                    "object": "block",
                    "type": "heading_1",
                    "heading_1": {
                        "rich_text": [
                            {"type": "text", "text": {"content": line[2:]}}
                        ]
                    },
                })
            elif line.startswith("## "):
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [
                            {"type": "text", "text": {"content": line[3:]}}
                        ]
                    },
                })
            else:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": line[:1900]}}
                        ]
                    },
                })

        body = {
            "parent": {"page_id": NOTION_OPS_PAGE_ID},
            "properties": {
                "title": {
                    "title": [{
                        "text": {
                            "content": f"Viral Daily Report — {summary.target_date}"
                        }
                    }]
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
            logger.info(f"[ViralDailyReport] Notion 페이지 생성 완료 page={page_id}")
            return page_id
        else:
            logger.error(
                f"[ViralDailyReport] Notion API 실패 status={r.status_code} "
                f"body={r.text[:200]}"
            )
            return None

    except Exception as e:
        logger.error(f"[ViralDailyReport] Notion 발행 예외: {e}")
        return None


def publish_to_telegram(summary: DailySummary) -> bool:
    """TG 무료 채널에 요약 발송."""
    try:
        from publishers.telegram_publisher import send_message
        send_message(build_telegram_summary(summary), channel="free")
        logger.info("[ViralDailyReport] TG 발송 완료")
        return True
    except Exception as e:
        logger.warning(f"[ViralDailyReport] TG 발송 실패: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────────────


def main(target_date: str | None = None) -> dict[str, Any]:
    """cron 진입점. 매일 09:00 KST. target_date 미지정 시 어제."""
    logger.info(f"[ViralDailyReport] v{VERSION} 시작")

    if target_date is None:
        yesterday = (datetime.now(KST) - timedelta(days=1)).date()
        target_date = yesterday.strftime("%Y-%m-%d")

    summary = collect_daily_summary(target_date)
    logger.info(
        f"[ViralDailyReport] 집계 완료 published={summary.published_count} "
        f"discarded={summary.discarded_count} "
        f"auto_deleted={summary.auto_deleted_today}"
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
