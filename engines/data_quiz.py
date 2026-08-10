"""
engines/data_quiz.py
================================
F안: 데이터 퀴즈 — C-6 재설계 (마스터 승인 2026-08-10).

C-6 중단 사유(품질 저조 + 참여율 0)의 구조적 원인 2가지를 제거:
  1. Gemini 단발 생성/무게이트 → 질문 틀 8종 템플릿 x 실데이터 바인딩
     (daily_snapshots 실값이 정답 소스 — 생성 품질 문제 원천 제거, RPD +0)
  2. 일반 지식 4지선다 정답 부담 → "구간 맞히기" 감각 게임으로 장벽 완화

VERSION = "1.0.0"

v1.0.0 (2026-08-10):
  - 신규. 발행: run_data_quiz() / 정답 공개: run_quiz_answer()
    (time.sleep(1800) 배제 — 다음 세션 지연 발행. Actions 과금 방지)
  - 게이트: 정답 소스(전일 daily_snapshots) 부재 시 미발행 (지침 6)
  - 측정: viral_logs에 session='data_quiz' 적재
    → viral_performance_tracker 자동 측정 편승
  - 자동 중단 조건(운영 정책): 4주 후 평균 ER < C-20 평균의 30% → 재중단

실행: prediction_league.yml cron 편승 (화/목 발행, 익일 정답)
테이블: public.quiz_rounds (마이그레이션 create_quiz_rounds)
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

_DISCLAIMER = "\n\n⚠️ 투자 참고 정보, 투자 권유 아님"

# 질문 틀 8종: (지표 키, 라벨, 구간 경계 4개 → A/B/C/D 5구간 중 4지선다용 3경계)
# 경계는 (low, mid, high) → A: <low, B: low~mid, C: mid~high, D: >high
_QUIZ_SPECS: list[dict[str, Any]] = [
    {"key": "vix", "label": "VIX(공포지수)", "bounds": (15.0, 20.0, 25.0),
     "fmt": "{:.1f}", "unit": ""},
    {"key": "oil_wti", "label": "WTI 유가", "bounds": (70.0, 85.0, 100.0),
     "fmt": "${:.0f}", "unit": ""},
    {"key": "us10y", "label": "미국 10년물 금리", "bounds": (3.5, 4.0, 4.5),
     "fmt": "{:.2f}%", "unit": ""},
    {"key": "dollar_index", "label": "달러 인덱스(DXY)", "bounds": (98.0, 103.0, 108.0),
     "fmt": "{:.1f}", "unit": ""},
    {"key": "fear_greed", "label": "Fear & Greed 지수", "bounds": (25.0, 50.0, 75.0),
     "fmt": "{:.0f}", "unit": ""},
    {"key": "btc_usd", "label": "비트코인(BTC)", "bounds": (60000.0, 90000.0, 120000.0),
     "fmt": "${:,.0f}", "unit": ""},
    {"key": "spy_change", "label": "SPY 일간 등락률", "bounds": (-1.0, 0.0, 1.0),
     "fmt": "{:+.2f}%", "unit": ""},
    {"key": "nasdaq_change", "label": "나스닥 일간 등락률", "bounds": (-1.0, 0.0, 1.0),
     "fmt": "{:+.2f}%", "unit": ""},
]

_QUIZ_HEADERS = [
    "🧠 데이터 퀴즈 — 감으로 맞혀보세요",
    "🎯 오늘의 시장 감각 테스트",
    "🧩 숫자 하나로 보는 시장 — 퀴즈",
    "🔢 시장 감각 퀴즈 타임",
]
_QUIZ_CTAS = [
    "댓글로 A/B/C/D 남겨주세요 👇 정답은 내일 이 스레드에!",
    "정답 예상을 댓글로! 내일 정답 공개 🔓",
    "A~D 중 하나, 댓글로 베팅 👇 정답은 24시간 뒤",
]
_ANSWER_HEADERS = [
    "🔓 어제 데이터 퀴즈 정답 공개",
    "✅ 퀴즈 정답 나갑니다",
    "📢 정답 발표 — 어제의 시장 감각 퀴즈",
]


def _get_client():
    """Supabase 클라이언트 (마스터 패턴 — 지연 import)."""
    from db.supabase_client import get_client
    return get_client()


def _is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() != "false"


def _bucket(value: float, bounds: tuple[float, float, float]) -> str:
    """값 → A/B/C/D 구간 매핑."""
    low, mid, high = bounds
    if value < low:
        return "A"
    if value < mid:
        return "B"
    if value < high:
        return "C"
    return "D"


def _bucket_labels(spec: dict[str, Any]) -> dict[str, str]:
    low, mid, high = spec["bounds"]
    f = spec["fmt"]
    return {
        "A": f"{f.format(low)} 미만",
        "B": f"{f.format(low)} ~ {f.format(mid)}",
        "C": f"{f.format(mid)} ~ {f.format(high)}",
        "D": f"{f.format(high)} 이상",
    }


def _fetch_snapshot(date_str: str) -> dict[str, Any] | None:
    """daily_snapshots 단일 일자 조회."""
    try:
        rows = (
            _get_client().table("daily_snapshots")
            .select("snapshot_date, vix, oil_wti, us10y, dollar_index, "
                    "fear_greed, btc_usd, spy_change, nasdaq_change")
            .eq("snapshot_date", date_str)
            .limit(1)
            .execute()
        )
        if rows.data:
            return rows.data[0]
    except Exception as e:
        logger.warning(f"[DataQuiz] 스냅샷 조회 실패({date_str}): {e}")
    return None


def _latest_snapshot(max_back_days: int = 4) -> dict[str, Any] | None:
    """오늘부터 최대 N일 역순으로 가장 최근 스냅샷 (주말/휴장 대응)."""
    for back in range(1, max_back_days + 1):
        d = (datetime.now(KST) - timedelta(days=back)).strftime("%Y-%m-%d")
        snap = _fetch_snapshot(d)
        if snap:
            return snap
    return None


# ─────────────────────────────────────────────────────────────────────────
# 1. 발행 — run_data_quiz
# ─────────────────────────────────────────────────────────────────────────

def build_quiz(snap: dict[str, Any]) -> dict[str, Any] | None:
    """스냅샷에서 값이 존재하는 지표 중 랜덤 1개로 퀴즈 구성. 불가 시 None."""
    specs = [s for s in _QUIZ_SPECS if snap.get(s["key"]) is not None]
    if not specs:
        return None
    spec = random.choice(specs)
    value = float(snap[spec["key"]])
    answer = _bucket(value, spec["bounds"])
    labels = _bucket_labels(spec)
    date_label = str(snap.get("snapshot_date", ""))

    header = random.choice(_QUIZ_HEADERS)
    cta = random.choice(_QUIZ_CTAS)
    question = (
        f"{header}\n\n"
        f"Q. {date_label} 미장 마감 기준,\n"
        f"{spec['label']}는 어느 구간이었을까요?\n\n"
        f"A) {labels['A']}\n"
        f"B) {labels['B']}\n"
        f"C) {labels['C']}\n"
        f"D) {labels['D']}\n\n"
        f"{cta}{_DISCLAIMER}"
    )
    answer_value = spec["fmt"].format(value)
    return {
        "metric_key": spec["key"],
        "metric_label": spec["label"],
        "snapshot_date": date_label,
        "question_text": question,
        "answer": answer,
        "answer_value": answer_value,
        "answer_label": labels[answer],
    }


def run_data_quiz() -> dict:
    """퀴즈 발행 (화/목). 당일 기발행 시 skip."""
    logger.info(f"[DataQuiz] v{VERSION} 발행 시작")
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 멱등
    try:
        existing = (
            _get_client().table("quiz_rounds")
            .select("id").eq("quiz_date", today).execute()
        )
        if existing.data:
            logger.info(f"[DataQuiz] {today} 기발행 → skip")
            return {"success": False, "reason": "already_published"}
    except Exception as e:
        logger.warning(f"[DataQuiz] 기발행 확인 실패 (진행): {e}")

    snap = _latest_snapshot()
    if snap is None:
        logger.warning("[DataQuiz] 정답 소스(스냅샷) 없음 → 미발행 (지침 6)")
        return {"success": False, "reason": "no_snapshot"}

    quiz = build_quiz(snap)
    if quiz is None:
        logger.warning("[DataQuiz] 유효 지표 없음 → 미발행")
        return {"success": False, "reason": "no_metric"}

    try:
        from publishers.x_publisher import publish_tweet
        pub = publish_tweet(quiz["question_text"])
        tweet_id = str(pub.get("tweet_id") or "")
        if not pub.get("success") or not tweet_id:
            return {"success": False, "reason": "publish_failed"}
    except Exception as e:
        logger.warning(f"[DataQuiz] X 발행 실패: {e}")
        return {"success": False, "reason": f"publish_failed: {e}"}

    try:
        from publishers.telegram_publisher import send_message
        send_message(quiz["question_text"].replace(_DISCLAIMER, ""), channel="free")
    except Exception as e:
        logger.warning(f"[DataQuiz] TG 발행 실패 (무시): {e}")

    if _is_dry_run() or tweet_id == "DRY_RUN":
        logger.info("[DataQuiz] DRY_RUN → quiz_rounds 저장 skip")
        return {"success": True, "dry_run": True, "metric": quiz["metric_key"]}

    try:
        _get_client().table("quiz_rounds").insert({
            "quiz_date": today,
            "metric_key": quiz["metric_key"],
            "snapshot_date": quiz["snapshot_date"],
            "question_text": quiz["question_text"],
            "answer": quiz["answer"],
            "answer_value": quiz["answer_value"],
            "answer_label": quiz["answer_label"],
            "tweet_id": tweet_id,
            "status": "open",
        }).execute()
    except Exception as e:
        logger.warning(f"[DataQuiz] quiz_rounds 저장 실패: {e}")
        return {"success": False, "reason": f"db_failed: {e}"}

    # 측정 편승: viral_logs 적재 (tracker 자동 측정 대상 등록)
    try:
        from db.viral_log_store import ViralLog, save_log
        save_log(ViralLog(
            publish_date=today,
            session="data_quiz",
            target_segment="quiz",
            conflict_axis="quiz",
            is_published=True,
            viral_score=0,
            opt_a=quiz["metric_label"],
            opt_b=quiz["answer"],
            tweet_id=tweet_id,
            policy_version=f"data_quiz_v{VERSION}",
        ))
    except Exception as e:
        logger.warning(f"[DataQuiz] viral_logs 적재 실패 (무시): {e}")

    logger.info(f"[DataQuiz] 발행 완료: {quiz['metric_key']} → {tweet_id}")
    return {"success": True, "tweet_id": tweet_id, "metric": quiz["metric_key"]}


# ─────────────────────────────────────────────────────────────────────────
# 2. 정답 공개 — run_quiz_answer (다음 실행 세션)
# ─────────────────────────────────────────────────────────────────────────

def run_quiz_answer(min_age_hours: int = 18) -> dict:
    """open 퀴즈 중 발행 후 N시간 경과분에 정답 reply 발행."""
    logger.info(f"[DataQuiz] v{VERSION} 정답 공개 시작")

    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
        ).isoformat()
        rows = (
            _get_client().table("quiz_rounds")
            .select("id, quiz_date, metric_key, answer, answer_value, "
                    "answer_label, tweet_id, created_at")
            .eq("status", "open")
            .lte("created_at", cutoff)
            .order("created_at")
            .limit(3)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[DataQuiz] open 조회 실패: {e}")
        return {"success": False, "reason": f"query_failed: {e}"}

    if not rows.data:
        return {"success": True, "answered": 0}

    answered = 0
    for row in rows.data:
        header = random.choice(_ANSWER_HEADERS)
        text = (
            f"{header}\n\n"
            f"정답: {row['answer']}) {row['answer_label']}\n"
            f"실제 값: {row['answer_value']}\n\n"
            f"맞히셨나요? 다음 퀴즈에서 또 만나요 🎯{_DISCLAIMER}"
        )
        try:
            from publishers.x_publisher import publish_thread
            pub = publish_thread([text], reply_to=str(row["tweet_id"]))
            ids = pub.get("tweet_ids", [])
            a_id = str(ids[0]) if ids else None
        except Exception as e:
            logger.warning(f"[DataQuiz] 정답 발행 실패 (id={row['id']}): {e}")
            continue

        if a_id and a_id != "DRY_RUN":
            try:
                _get_client().table("quiz_rounds").update({
                    "status": "answered",
                    "answer_tweet_id": a_id,
                    "answered_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", row["id"]).execute()
                answered += 1
                logger.info(f"[DataQuiz] 정답 공개 완료: {row['quiz_date']} → {a_id}")
            except Exception as e:
                logger.warning(f"[DataQuiz] 정답 마킹 실패: {e}")

    return {"success": True, "answered": answered}
