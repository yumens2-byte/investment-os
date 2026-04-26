"""
Investment OS — Viral Performance Tracker v1.0.0

매 30분 cron으로 실행:
1. 측정 사이클 — 24h/48h/68h/80h 마일스톤 도래 트윗의 X metrics 회수
2. 폐기 결정 — 80h 도래 트윗 임계값 평가 + 6단계 안전 가드 + Deleter 위임

설계 원칙:
- 측정 윈도우 ±15분 (cron 30분 간격 커버)
- 마일스톤별 배치 X API 호출 (최대 100개/배치)
- 안전 가드 6단계 (grace_period / high_score / protected / daily_limit /
  manual_review / threshold)
- 모드 4종 (off / monitor / dry_run / full)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import tweepy

from engines.viral_deleter import execute_deletion
from engines.viral_targeting_loader import is_fallback, load_policy
from db.viral_log_store import (
    count_today_auto_deletions,
    get_pending_evaluation_logs,
    get_recent_active_logs,
)
from db.viral_metrics_store import (
    PerformanceMetric,
    get_metric_by_milestone,
    save_metric,
)

VERSION = "1.0.0"

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────

WINDOW_MINUTES = 15
EVAL_MILESTONE_HOURS = 80
X_BATCH_MAX = 100

MODE_OFF = "off"
MODE_MONITOR = "monitor"
MODE_DRY_RUN = "dry_run"
MODE_FULL = "full"
VALID_MODES = (MODE_OFF, MODE_MONITOR, MODE_DRY_RUN, MODE_FULL)


# ─────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class DeleteDecision:
    """폐기 결정 결과."""

    should_delete: bool
    reason: str
    detail: dict[str, Any] | None = None


@dataclass
class TrackerSummary:
    """1회 실행 요약 (운영 지표용)."""

    mode: str
    candidates_total: int = 0
    measurements_taken: int = 0
    measurements_failed: int = 0
    eval_targets: int = 0
    decisions_delete: int = 0
    decisions_keep: int = 0
    safety_blocks: dict[str, int] = field(default_factory=dict)
    deletions_executed: int = 0
    deletions_failed: int = 0


# ─────────────────────────────────────────────────────────────────────────
# X API Client
# ─────────────────────────────────────────────────────────────────────────


_x_client: tweepy.Client | None = None


def _get_x_client() -> tweepy.Client:
    """metrics 조회용 tweepy.Client. non_public_metrics는 user context 필수."""
    global _x_client
    if _x_client is None:
        _x_client = tweepy.Client(
            consumer_key=os.getenv("X_API_KEY"),
            consumer_secret=os.getenv("X_API_SECRET"),
            access_token=os.getenv("X_ACCESS_TOKEN"),
            access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
            wait_on_rate_limit=False,
        )
    return _x_client


# ─────────────────────────────────────────────────────────────────────────
# 시간 윈도우 판정 (순수 함수 — 단위 테스트 가능)
# ─────────────────────────────────────────────────────────────────────────


def hours_since(created_at: datetime, now: datetime | None = None) -> float:
    """발행 후 경과시간 (시간 단위, 소수점 포함)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (now - created_at).total_seconds() / 3600.0


def is_in_milestone_window(
    elapsed_hours: float,
    milestone_hours: int,
    window_minutes: int = WINDOW_MINUTES,
) -> bool:
    """경과시간이 마일스톤 ±window_minutes 윈도우 내인지."""
    window_h = window_minutes / 60.0
    return abs(elapsed_hours - milestone_hours) <= window_h


def find_due_milestone(
    elapsed_hours: float,
    schedule: list[dict[str, Any]],
    window_minutes: int = WINDOW_MINUTES,
) -> int | None:
    """현재 경과시간에 해당하는 마일스톤 찾기."""
    for entry in schedule:
        ms_hours = int(entry.get("hours", 0))
        if is_in_milestone_window(elapsed_hours, ms_hours, window_minutes):
            return ms_hours
    return None


# ─────────────────────────────────────────────────────────────────────────
# X API 배치 조회
# ─────────────────────────────────────────────────────────────────────────


def fetch_metrics_batch(tweet_ids: list[str]) -> dict[str, dict[str, Any]]:
    """X API에서 여러 트윗 metrics를 한 번에 조회."""
    result: dict[str, dict[str, Any]] = {}
    if not tweet_ids:
        return result

    for i in range(0, len(tweet_ids), X_BATCH_MAX):
        batch = tweet_ids[i : i + X_BATCH_MAX]
        try:
            client = _get_x_client()
            response = client.get_tweets(
                ids=batch,
                tweet_fields=[
                    "public_metrics",
                    "non_public_metrics",
                    "organic_metrics",
                    "created_at",
                ],
                user_auth=True,
            )

            data_list = response.data or []
            for tweet_obj in data_list:
                tid = str(tweet_obj.id)
                result[tid] = {
                    "status": "success",
                    "raw": _tweet_obj_to_dict(tweet_obj),
                }

            errors = getattr(response, "errors", None) or []
            for err in errors:
                tid = str(err.get("resource_id", ""))
                if not tid:
                    continue
                title = err.get("title", "")
                if "Not Found" in title or "deleted" in title.lower():
                    result[tid] = {"status": "tweet_deleted", "raw": err}
                else:
                    result[tid] = {"status": "error", "raw": err}

        except tweepy.TooManyRequests:
            logger.warning(
                "[Tracker] X API rate limit (batch %d~%d) → 다음 cron 재시도",
                i,
                i + len(batch),
            )
            for tid in batch:
                if tid not in result:
                    result[tid] = {
                        "status": "rate_limited",
                        "raw": {"error": "rate_limited"},
                    }
        except Exception as e:
            logger.error(
                "[Tracker] X API 배치 조회 실패 (batch %d~%d): %s",
                i,
                i + len(batch),
                e,
            )
            for tid in batch:
                if tid not in result:
                    result[tid] = {"status": "error", "raw": {"error": str(e)}}

    return result


def _tweet_obj_to_dict(tweet_obj: Any) -> dict[str, Any]:
    """tweepy Tweet 객체 → dict 변환."""
    return {
        "data": {
            "id": str(tweet_obj.id),
            "public_metrics": getattr(tweet_obj, "public_metrics", {}) or {},
            "non_public_metrics": getattr(tweet_obj, "non_public_metrics", {}) or {},
            "organic_metrics": getattr(tweet_obj, "organic_metrics", {}) or {},
        }
    }


# ─────────────────────────────────────────────────────────────────────────
# 안전 가드 6단계 (순수 함수)
# ─────────────────────────────────────────────────────────────────────────


def evaluate_for_deletion(
    log: dict[str, Any],
    metric: dict[str, Any] | None,
    policy_auto_delete: dict[str, Any],
    today_deleted_count: int,
) -> DeleteDecision:
    """6단계 안전 가드 + 임계값 평가. 첫 차단에서 즉시 종료."""
    safety = policy_auto_delete.get("safety", {}) or {}
    thresholds = policy_auto_delete.get("thresholds", {}) or {}

    # 1) Grace period
    created_at = log.get("created_at")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
    if not isinstance(created_at, datetime):
        return DeleteDecision(False, "no_created_at")

    elapsed = hours_since(created_at)
    grace = float(safety.get("grace_period_hours", 24))
    if elapsed < grace:
        return DeleteDecision(
            False, "grace_period", {"elapsed": elapsed, "grace": grace}
        )

    # 2) High score 보호
    high_score_threshold = int(safety.get("protect_high_score", 85))
    viral_score = int(log.get("viral_score") or 0)
    if viral_score >= high_score_threshold:
        return DeleteDecision(
            False,
            "high_score_protected",
            {"score": viral_score, "threshold": high_score_threshold},
        )

    # 3) 수동 보호
    if log.get("protected"):
        return DeleteDecision(False, "manually_protected")

    # 4) 일일 상한
    daily_limit = int(safety.get("daily_delete_limit", 5))
    if today_deleted_count >= daily_limit:
        return DeleteDecision(
            False,
            "daily_limit_reached",
            {"today": today_deleted_count, "limit": daily_limit},
        )

    # 5) 측정값 검증
    if metric is None or metric.get("fetch_status") != "success":
        return DeleteDecision(False, "no_valid_metric")

    impression = metric.get("impression_count")
    er = metric.get("engagement_rate")
    if impression is None or er is None:
        return DeleteDecision(False, "incomplete_metric")

    manual_review_below = int(safety.get("require_manual_review_below", 50))
    min_imp = int(thresholds.get("min_impressions", 50))
    min_er = float(thresholds.get("min_engagement_rate", 0.005))
    decision_logic = thresholds.get("decision_logic", "AND")

    imp_below_min = impression < min_imp
    er_below_min = er < min_er

    if decision_logic == "AND":
        threshold_failed = imp_below_min and er_below_min
    else:
        threshold_failed = imp_below_min or er_below_min

    if threshold_failed and impression < manual_review_below:
        return DeleteDecision(
            False,
            "queued_for_manual_review",
            {"impression": impression, "below": manual_review_below},
        )

    # 6) 임계값 최종 평가
    if threshold_failed:
        return DeleteDecision(
            True,
            "low_performance",
            {
                "impression": impression,
                "engagement_rate": er,
                "min_impressions": min_imp,
                "min_engagement_rate": min_er,
                "logic": decision_logic,
            },
        )

    return DeleteDecision(False, "performing_within_threshold")


# ─────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────────────


def main() -> TrackerSummary:
    """cron 진입점. 매 30분 실행."""
    logger.info("[Tracker] v%s 시작", VERSION)

    policy = load_policy()
    if is_fallback(policy):
        logger.warning("[Tracker] fallback 정책 사용 중 → 운영자 확인 필요")

    auto_delete = policy.get("auto_delete", {}) or {}
    mode = (
        os.getenv("VIRAL_AUTO_DELETE", auto_delete.get("enabled_mode", MODE_OFF))
        .strip()
        .lower()
    )

    if mode not in VALID_MODES:
        logger.error("[Tracker] 잘못된 모드 %s → off로 동작", mode)
        mode = MODE_OFF

    summary = TrackerSummary(mode=mode)

    if mode == MODE_OFF:
        logger.info("[Tracker] mode=off → 종료")
        return summary

    channels = auto_delete.get("channels", {}) or {}
    if not channels.get("x_twitter", False):
        logger.info("[Tracker] x_twitter 채널 비활성 → 종료")
        return summary

    # ─── Step 1. 측정 사이클 ──────────────────────────────
    schedule = auto_delete.get("measurement_schedule", []) or []
    candidates = get_recent_active_logs(within_hours=96)
    summary.candidates_total = len(candidates)

    logger.info(
        "[Tracker] 측정 후보 %d건 (mode=%s, schedule=%s)",
        len(candidates),
        mode,
        [e.get("hours") for e in schedule],
    )

    milestone_groups: dict[int, list[dict[str, Any]]] = {}
    for log in candidates:
        created_at = log.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
            except ValueError:
                continue

        elapsed = hours_since(created_at)
        due_ms = find_due_milestone(elapsed, schedule)
        if due_ms is None:
            continue

        existing = get_metric_by_milestone(log["log_id"], due_ms)
        if existing and existing.get("fetch_status") == "success":
            continue

        log["_elapsed_hours"] = round(elapsed, 2)
        milestone_groups.setdefault(due_ms, []).append(log)

    for ms_hours, logs in milestone_groups.items():
        tweet_ids = [str(log["tweet_id"]) for log in logs]
        logger.info(
            "[Tracker] 마일스톤 %dh 배치 측정 %d건", ms_hours, len(tweet_ids)
        )

        batch_result = fetch_metrics_batch(tweet_ids)

        for log in logs:
            tid = str(log["tweet_id"])
            res = batch_result.get(tid, {"status": "error", "raw": {}})
            status = res["status"]
            raw = res["raw"]

            if status == "success":
                metric = PerformanceMetric.from_x_response(
                    log_id=log["log_id"],
                    tweet_id=tid,
                    milestone_hours=ms_hours,
                    hours_since_publish=log["_elapsed_hours"],
                    x_response=raw,
                )
                summary.measurements_taken += 1
            else:
                err_str = raw.get("error") if isinstance(raw, dict) else str(raw)
                metric = PerformanceMetric.from_error(
                    log_id=log["log_id"],
                    tweet_id=tid,
                    milestone_hours=ms_hours,
                    hours_since_publish=log["_elapsed_hours"],
                    fetch_status=status,
                    fetch_error=str(err_str)[:500],
                )
                summary.measurements_failed += 1

            save_metric(metric)

    # ─── Step 2. 폐기 결정 (mode != monitor 시에만) ────────
    if mode == MODE_MONITOR:
        logger.info("[Tracker] mode=monitor → 폐기 결정 스킵 (%s)", _summarize(summary))
        return summary

    eval_targets = get_pending_evaluation_logs(min_hours=EVAL_MILESTONE_HOURS)
    summary.eval_targets = len(eval_targets)
    today_deleted = count_today_auto_deletions()

    logger.info(
        "[Tracker] 폐기 결정 평가 대상 %d건 (오늘 자동삭제 %d건)",
        len(eval_targets),
        today_deleted,
    )

    for log in eval_targets:
        metric = get_metric_by_milestone(log["log_id"], EVAL_MILESTONE_HOURS)
        if metric is None:
            logger.warning(
                "[Tracker] 80h 측정 없음 log_id=%s → 평가 스킵", log["log_id"]
            )
            summary.safety_blocks["no_metric"] = (
                summary.safety_blocks.get("no_metric", 0) + 1
            )
            continue

        decision = evaluate_for_deletion(log, metric, auto_delete, today_deleted)

        if decision.should_delete:
            summary.decisions_delete += 1

            if today_deleted >= int(
                auto_delete.get("safety", {}).get("daily_delete_limit", 5)
            ):
                summary.safety_blocks["daily_limit_reached"] = (
                    summary.safety_blocks.get("daily_limit_reached", 0) + 1
                )
                continue

            del_result = execute_deletion(
                log_id=log["log_id"],
                tweet_id=log["tweet_id"],
                delete_reason=decision.reason,
                mode=mode,
            )

            if del_result.success:
                summary.deletions_executed += 1
                if mode == MODE_FULL:
                    today_deleted += 1
            else:
                summary.deletions_failed += 1
        else:
            summary.decisions_keep += 1
            summary.safety_blocks[decision.reason] = (
                summary.safety_blocks.get(decision.reason, 0) + 1
            )

    logger.info("[Tracker] v%s 완료 — %s", VERSION, _summarize(summary))
    return summary


def _summarize(s: TrackerSummary) -> str:
    """로그용 요약 문자열."""
    return (
        f"mode={s.mode} candidates={s.candidates_total} "
        f"measured={s.measurements_taken} failed={s.measurements_failed} "
        f"eval={s.eval_targets} delete={s.decisions_delete} keep={s.decisions_keep} "
        f"executed={s.deletions_executed} exec_failed={s.deletions_failed} "
        f"safety_blocks={dict(s.safety_blocks)}"
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    main()
