"""
engines/prediction_league.py
================================
C안: 주간 예측 리그 — 월요일 발제 → 토요일 정산.

VERSION = "1.0.0"

v1.0.0 (2026-08-10):
  - 신규. 투표 방식②(좋아요 투표) — 마스터 승인 (2026-08-10).
  - baseline/정산가 소스: collectors.yahoo_finance.collect_spy_sma()["spy_price"]
    (일봉 마지막 종가 — 월요일 실행 시 금요일 종가, 토요일 실행 시 금요일 종가).
    daily_snapshots에는 SPY 절대가 미보유(spy_change만) → 직접 수집 + 라운드
    테이블에 양측 값 영구 기록으로 정합성/감사추적 보장.
  - 수집 실패 시 미발제/미정산 (지침 6: 추측값 금지).
  - 참여 집계: 옵션 트윗 2개의 like_count
    (viral_performance_tracker.fetch_metrics_batch 재사용).
    집계 실패 시 집계 문구만 생략하고 정산은 발행.

실행: .github/workflows/prediction_league.yml (월/토 cron, 기존 파일 무접촉)
테이블: public.prediction_rounds (마이그레이션 create_prediction_rounds)
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

FLAT_THRESHOLD_PCT = 0.05     # |변화율| < 0.05% → flat
VOID_AFTER_DAYS = 7           # 발제 후 7일 초과 미정산 → void

# 안티봇: 문구 풀
_OPEN_TEMPLATES = [
    ("🎯 이번 주 예측 리그 개막!\n\n금요일 종가 기준, SPY는 오를까요 내릴까요?\n"
     "(직전 종가 ${baseline:,.2f})\n\n아래 트윗에 ❤️로 투표 → 토요일 결과 발표 🏁"),
    ("📊 주간 SPY 예측 오픈\n\n기준선: ${baseline:,.2f} (직전 종가)\n"
     "이번 주 금요일, 이 선 위일까 아래일까?\n\n❤️ 한 번이면 투표 완료. 정산은 토요일 👇"),
    ("🔮 월요일의 질문: 이번 주 SPY 방향은?\n\n출발선 ${baseline:,.2f}\n\n"
     "상승/하락 트윗에 ❤️로 베팅하세요.\n토요일에 성적표 나갑니다 🧾"),
]
_OPT_UP_TEMPLATES = [
    "📈 상승에 한 표\n\n금요일 종가 > ${baseline:,.2f} 예상이면 ❤️",
    "📈 UP — 이번 주는 오른다\n\n동의하면 ❤️로 투표",
]
_OPT_DOWN_TEMPLATES = [
    "📉 하락에 한 표\n\n금요일 종가 < ${baseline:,.2f} 예상이면 ❤️",
    "📉 DOWN — 이번 주는 내린다\n\n동의하면 ❤️로 투표",
]
_SETTLE_RESULT = {
    "up":   ["📈 결과: 상승!", "📈 이번 주 승자: 상승론자"],
    "down": ["📉 결과: 하락!", "📉 이번 주 승자: 하락론자"],
    "flat": ["➖ 결과: 보합 (±0.05% 이내)", "➖ 무승부 — 시장이 제자리걸음"],
}
_SETTLE_FOOTERS = [
    "다음 주 월요일, 새 라운드로 돌아옵니다 🔄",
    "월요일에 다시 만나요 — 다음 예측 준비하세요 🎯",
    "새로운 한 주, 새로운 베팅. 월요일에 계속 🏁",
]
_DISCLAIMER = "\n\n⚠️ 투자 참고 정보, 투자 권유 아님"


def _get_client():
    """Supabase 클라이언트 (마스터 패턴 — 지연 import)."""
    from db.supabase_client import get_client
    return get_client()


def _is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() != "false"


def _week_monday(now_kst: datetime | None = None) -> str:
    """이번 주 월요일 날짜 (KST) — 라운드 키."""
    now = now_kst or datetime.now(KST)
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def _collect_spy_price() -> float | None:
    """SPY 절대가 수집 (일봉 마지막 종가). 실패 시 None."""
    try:
        from collectors.yahoo_finance import collect_spy_sma
        data = collect_spy_sma()
        price = data.get("spy_price")
        if price is not None:
            return float(price)
    except Exception as e:
        logger.warning(f"[PredLeague] SPY 수집 실패: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────
# 1. 발제 — run_prediction_open (월요일)
# ─────────────────────────────────────────────────────────────────────────

def run_prediction_open() -> dict:
    """월요일 발제. 금주 라운드 기존재 시 skip (멱등)."""
    logger.info(f"[PredLeague] v{VERSION} open 시작")
    week_key = _week_monday()

    # 멱등
    try:
        existing = (
            _get_client().table("prediction_rounds")
            .select("id").eq("week_key", week_key).execute()
        )
        if existing.data:
            logger.info(f"[PredLeague] {week_key} 라운드 기존재 → skip")
            return {"success": False, "reason": "already_open"}
    except Exception as e:
        logger.warning(f"[PredLeague] 기존재 확인 실패 (진행): {e}")

    baseline = _collect_spy_price()
    if baseline is None:
        logger.warning("[PredLeague] baseline 수집 실패 → 이번 회차 미발제 (지침 6)")
        return {"success": False, "reason": "baseline_unavailable"}

    open_text = random.choice(_OPEN_TEMPLATES).format(baseline=baseline) + _DISCLAIMER
    up_text = random.choice(_OPT_UP_TEMPLATES).format(baseline=baseline)
    down_text = random.choice(_OPT_DOWN_TEMPLATES).format(baseline=baseline)

    try:
        from publishers.x_publisher import publish_thread
        h = publish_thread([open_text])
        h_ids = h.get("tweet_ids", [])
        open_id = str(h_ids[0]) if h_ids else None
        if not open_id:
            return {"success": False, "reason": "open_tweet_failed"}

        o = publish_thread([up_text, down_text], reply_to=open_id)
        o_ids = [str(t) for t in o.get("tweet_ids", [])]
    except Exception as e:
        logger.warning(f"[PredLeague] 발제 발행 실패: {e}")
        return {"success": False, "reason": f"publish_failed: {e}"}

    if _is_dry_run() or open_id == "DRY_RUN":
        logger.info("[PredLeague] DRY_RUN → prediction_rounds 저장 skip")
        return {"success": True, "dry_run": True, "baseline": baseline}

    option_tweets = {}
    if len(o_ids) >= 1:
        option_tweets["up"] = o_ids[0]
    if len(o_ids) >= 2:
        option_tweets["down"] = o_ids[1]

    settle_date = (
        datetime.strptime(week_key, "%Y-%m-%d") + timedelta(days=4)
    ).strftime("%Y-%m-%d")  # 금요일

    try:
        _get_client().table("prediction_rounds").insert({
            "week_key": week_key,
            "question": "SPY weekly up/down",
            "metric": "SPY",
            "baseline_value": baseline,
            "baseline_date": datetime.now(KST).strftime("%Y-%m-%d"),
            "settle_date": settle_date,
            "open_tweet_id": open_id,
            "option_tweets": option_tweets,
            "status": "open",
        }).execute()
        logger.info(
            f"[PredLeague] 발제 완료: week={week_key} baseline=${baseline:,.2f} "
            f"open={open_id} options={option_tweets}"
        )
    except Exception as e:
        logger.warning(f"[PredLeague] 라운드 저장 실패: {e}")
        return {"success": False, "reason": f"db_failed: {e}"}

    return {"success": True, "week_key": week_key, "baseline": baseline,
            "open_tweet_id": open_id}


# ─────────────────────────────────────────────────────────────────────────
# 2. 정산 — run_prediction_settle (토요일)
# ─────────────────────────────────────────────────────────────────────────

def judge(baseline: float, final: float) -> str:
    """판정: |변화율| < 0.05% → flat, 그 외 up/down.

    round(6): 부동소수점 오차 제거 — (100.05-100)/100*100 = 0.04999...
    가 경계값 0.05%를 flat으로 오판하는 것을 방지.
    """
    if baseline == 0:
        return "flat"
    pct = round((final - baseline) / abs(baseline) * 100.0, 6)
    if abs(pct) < FLAT_THRESHOLD_PCT:
        return "flat"
    return "up" if pct > 0 else "down"


def _collect_vote_stats(option_tweets: dict[str, str]) -> str:
    """옵션 트윗 like 집계 문구. 실패 시 빈 문자열 (정산은 계속)."""
    try:
        from engines.viral_performance_tracker import fetch_metrics_batch
        ids = [tid for tid in option_tweets.values() if tid]
        if not ids:
            return ""
        metrics = fetch_metrics_batch(ids)
        likes: dict[str, int] = {}
        for side, tid in option_tweets.items():
            m = metrics.get(str(tid), {})
            if m.get("status") != "success":
                return ""
            raw = m.get("raw", {}) or {}
            public = (raw.get("data", {}) or {}).get("public_metrics", {}) or {}
            likes[side] = int(public.get("like_count") or 0)
        total = sum(likes.values())
        if total <= 0:
            return ""
        up_pct = round(likes.get("up", 0) / total * 100)
        return (
            f"\n\n🗳️ 참여 결과: 상승 {up_pct}% vs 하락 {100 - up_pct}% "
            f"(총 {total}표)"
        )
    except Exception as e:
        logger.warning(f"[PredLeague] 투표 집계 실패 (문구 생략): {e}")
        return ""


def run_prediction_settle() -> dict:
    """토요일 정산. open 라운드 → settled/void."""
    logger.info(f"[PredLeague] v{VERSION} settle 시작")

    try:
        rows = (
            _get_client().table("prediction_rounds")
            .select("id, week_key, baseline_value, settle_date, open_tweet_id, "
                    "option_tweets, created_at")
            .eq("status", "open")
            .order("week_key")
            .execute()
        )
    except Exception as e:
        logger.warning(f"[PredLeague] open 조회 실패: {e}")
        return {"success": False, "reason": f"query_failed: {e}"}

    if not rows.data:
        logger.info("[PredLeague] 정산 대상 없음")
        return {"success": True, "settled": 0}

    settled = 0
    for row in rows.data:
        if _settle_one(row):
            settled += 1
    return {"success": True, "settled": settled}


def _settle_one(row: dict[str, Any]) -> bool:
    round_id = row["id"]
    week_key = str(row.get("week_key", ""))

    # void 판정: settle_date + VOID_AFTER_DAYS 초과
    try:
        settle_dt = datetime.strptime(str(row["settle_date"]), "%Y-%m-%d")
        settle_dt = settle_dt.replace(tzinfo=KST)
        if datetime.now(KST) > settle_dt + timedelta(days=VOID_AFTER_DAYS):
            _update(round_id, {"status": "void"})
            logger.warning(f"[PredLeague] {week_key} 정산 기한 초과 → void")
            return False
    except Exception:
        pass

    final = _collect_spy_price()
    if final is None:
        logger.warning(f"[PredLeague] {week_key} 종가 수집 실패 → open 유지 (재시도)")
        return False

    baseline = float(row["baseline_value"])
    result = judge(baseline, final)
    pct = (final - baseline) / abs(baseline) * 100.0 if baseline else 0.0

    vote_line = _collect_vote_stats(row.get("option_tweets") or {})
    header = random.choice(_SETTLE_RESULT[result])
    footer = random.choice(_SETTLE_FOOTERS)
    text = (
        f"{header}\n\n"
        f"기준선 ${baseline:,.2f} → 금요일 종가 ${final:,.2f} ({pct:+.2f}%)"
        f"{vote_line}\n\n{footer}{_DISCLAIMER}"
    )

    tweet_id = None
    try:
        from publishers.x_publisher import publish_thread
        reply_to = row.get("open_tweet_id")
        pub = publish_thread([text], reply_to=str(reply_to) if reply_to else None)
        ids = pub.get("tweet_ids", [])
        tweet_id = str(ids[0]) if ids else None
    except Exception as e:
        logger.warning(f"[PredLeague] 정산 발행 실패: {e}")

    if tweet_id is None:
        return False

    if tweet_id == "DRY_RUN":
        logger.info(f"[PredLeague] DRY_RUN → {week_key} 상태 유지")
        return False

    _update(round_id, {
        "status": "settled",
        "result": result,
        "final_value": final,
        "settle_tweet_id": tweet_id,
        "settled_at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info(
        f"[PredLeague] {week_key} 정산 완료: {result} "
        f"(${baseline:,.2f}→${final:,.2f}) tweet={tweet_id}"
    )
    return True


def _update(round_id: Any, payload: dict[str, Any]) -> None:
    try:
        _get_client().table("prediction_rounds").update(payload).eq(
            "id", round_id
        ).execute()
    except Exception as e:
        logger.warning(f"[PredLeague] 상태 갱신 실패 (id={round_id}): {e}")
