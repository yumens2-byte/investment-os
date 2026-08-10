"""
engines/alert_followup.py
================================
D안: Alert 사후 리포트 — "그래서 어떻게 됐나".

L2급 이상 Alert 발생 22~30h 후, 당시값(daily_snapshots) vs 현재값(run_alert
snapshot 재사용)을 비교하는 트윗을 원본 Alert 트윗의 reply로 발행한다.

VERSION = "1.0.0"

v1.0.0 (2026-08-10):
  - 신규. 호출: run_alert.run() 말미 독립 try/except 1블록.
  - 당시값: daily_snapshots(alert_date) — trigger_value(TEXT) 파싱 배제.
  - VIX/OIL: 절대값 비교. SPY: spy_change 서술형(절대가 미보유).
  - 데이터 부재 → followup_tweet_id='SKIP_NO_DATA' 마킹 (재시도 안 함).
  - 실 트윗 ID가 아닌 값(EMOTION/FAIL/SKIP_X 등) 원본은 대상 제외.
  - 1회 실행당 최대 2건 (X 발행량 캡).

DB: daily_alerts.followup_tweet_id (마이그레이션 add_alert_followup_column).
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

KST = timezone(timedelta(hours=9))

# 대상 시간 윈도우 (Alert created_at 기준 경과 시간)
WINDOW_MIN_HOURS = 22
WINDOW_MAX_HOURS = 30

# 대상 레벨 (L2 이상)
TARGET_LEVELS = ("L2", "L3", "CRISIS")

# 1회 실행당 처리 캡
MAX_PER_RUN = 2

# 원본 tweet_id 무효값 (실 ID 아님 — followup 대상 제외)
INVALID_TWEET_IDS = ("", "FAIL", "SKIP", "SKIP_X", "EMOTION", "DRY_RUN", "X_FAIL")

# 안티봇: 문구 풀 (진정/악화/횡보)
_CALM_TEMPLATES = [
    ("🕐 24시간 전 {label} 경보, 그 후...\n\n{then_line}\n{now_line}\n\n"
     "시장은 진정 국면입니다. {delta_line}"),
    ("📉→😌 어제의 {label} 급변 후속\n\n{then_line}\n{now_line}\n\n"
     "{delta_line} 일단 한숨 돌렸습니다."),
    ("✅ {label} 경보 사후 점검\n\n{then_line}\n{now_line}\n\n"
     "{delta_line} 공포는 하루를 못 넘겼네요."),
]
_WORSE_TEMPLATES = [
    ("🚨 24시간 전 {label} 경보, 아직 안 끝났습니다\n\n{then_line}\n{now_line}\n\n"
     "{delta_line} 경계 유지가 필요합니다."),
    ("🕐 어제 {label} 경보 후속 — 상황 지속\n\n{then_line}\n{now_line}\n\n"
     "{delta_line} 아직 안전지대가 아닙니다."),
    ("📊 {label} 사후 점검: 압력 지속\n\n{then_line}\n{now_line}\n\n"
     "{delta_line} 방어적 접근 유효."),
]
_FLAT_TEMPLATES = [
    ("🕐 24시간 전 {label} 경보 후속\n\n{then_line}\n{now_line}\n\n"
     "{delta_line} 큰 변화 없이 관망세입니다."),
    ("📊 어제 {label} 경보, 지금은?\n\n{then_line}\n{now_line}\n\n"
     "{delta_line} 시장은 숨 고르기 중."),
]
_DISCLAIMER = "\n\n⚠️ 투자 참고 정보, 투자 권유 아님"


def _get_client():
    """Supabase 클라이언트 (마스터 패턴 — 지연 import)."""
    from db.supabase_client import get_client
    return get_client()


def _is_valid_tweet_id(tweet_id: Any) -> bool:
    return bool(tweet_id) and str(tweet_id) not in INVALID_TWEET_IDS


# ─────────────────────────────────────────────────────────────────────────
# 1. 대상 조회
# ─────────────────────────────────────────────────────────────────────────

def find_followup_targets(now_utc: datetime | None = None) -> list[dict[str, Any]]:
    """22~30h 경과, L2+, followup 미발행, 실 tweet_id 보유 Alert 조회."""
    now = now_utc or datetime.now(timezone.utc)
    upper = (now - timedelta(hours=WINDOW_MIN_HOURS)).isoformat()
    lower = (now - timedelta(hours=WINDOW_MAX_HOURS)).isoformat()

    try:
        rows = (
            _get_client().table("daily_alerts")
            .select("id, alert_date, alert_type, alert_level, trigger_value, "
                    "tweet_id, created_at")
            .in_("alert_level", list(TARGET_LEVELS))
            .is_("followup_tweet_id", "null")
            .gte("created_at", lower)
            .lte("created_at", upper)
            .order("created_at")
            .limit(10)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[AlertFollowup] 대상 조회 실패: {e}")
        return []

    targets = []
    for r in (rows.data or []):
        if _is_valid_tweet_id(r.get("tweet_id")):
            targets.append(r)
    return targets[:MAX_PER_RUN]


def _fetch_then_snapshot(alert_date: str) -> dict[str, Any] | None:
    """당시값: daily_snapshots(alert_date). 없으면 None."""
    try:
        rows = (
            _get_client().table("daily_snapshots")
            .select("snapshot_date, vix, oil_wti, spy_change, us10y")
            .eq("snapshot_date", alert_date)
            .limit(1)
            .execute()
        )
        if rows.data:
            return rows.data[0]
    except Exception as e:
        logger.warning(f"[AlertFollowup] 당시 스냅샷 조회 실패({alert_date}): {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────
# 2. 비교/포맷
# ─────────────────────────────────────────────────────────────────────────

def build_followup_text(
    alert: dict[str, Any],
    then_snap: dict[str, Any],
    now_snap: dict[str, Any],
) -> str | None:
    """
    비교 트윗 텍스트. 필수 데이터 부재 시 None (미발행 — 지침 6).

    alert_type: VIX/OIL/SPY 계열별 분기. 그 외 타입은 VIX 기준 대표 지표 사용.
    """
    atype = str(alert.get("alert_type", "")).upper()

    if "OIL" in atype:
        label = "유가(WTI)"
        then_v = then_snap.get("oil_wti")
        now_v = now_snap.get("oil")
        unit = "$"
        higher_is_worse = True
    elif "SPY" in atype:
        # SPY는 절대가 미보유 → 등락률 서술형
        then_c = then_snap.get("spy_change")
        now_c = now_snap.get("sp500")
        if then_c is None or now_c is None:
            return None
        then_line = f"당시: SPY 일간 {float(then_c):+.1f}%"
        now_line = f"오늘: SPY {float(now_c):+.1f}%"
        if float(now_c) >= 0.3:
            tmpl = random.choice(_CALM_TEMPLATES)
            delta_line = "낙폭 이후 반등 흐름."
        elif float(now_c) <= -0.3:
            tmpl = random.choice(_WORSE_TEMPLATES)
            delta_line = "하락 압력이 이어지는 모습."
        else:
            tmpl = random.choice(_FLAT_TEMPLATES)
            delta_line = "보합권 등락."
        text = tmpl.format(
            label="SPY 급락", then_line=then_line,
            now_line=now_line, delta_line=delta_line,
        )
        return text + _DISCLAIMER
    else:
        # VIX 및 기타(FED/CRISIS 등) → VIX 대표 지표
        label = "VIX" if "VIX" in atype else f"{atype}(VIX 기준)"
        then_v = then_snap.get("vix")
        now_v = now_snap.get("vix")
        unit = ""
        higher_is_worse = True

    if then_v is None or now_v is None:
        return None

    then_f, now_f = float(then_v), float(now_v)
    if then_f == 0:
        return None
    pct = (now_f - then_f) / abs(then_f) * 100.0

    then_line = f"당시: {label} {unit}{then_f:,.1f}"
    now_line = f"현재: {label} {unit}{now_f:,.1f}"
    delta_line = f"24시간 변화 {pct:+.1f}%."

    worse = pct > 2.0 if higher_is_worse else pct < -2.0
    calm = pct < -2.0 if higher_is_worse else pct > 2.0
    if calm:
        tmpl = random.choice(_CALM_TEMPLATES)
    elif worse:
        tmpl = random.choice(_WORSE_TEMPLATES)
    else:
        tmpl = random.choice(_FLAT_TEMPLATES)

    text = tmpl.format(
        label=label, then_line=then_line,
        now_line=now_line, delta_line=delta_line,
    )
    return text + _DISCLAIMER


def _mark(alert_id: Any, followup_tweet_id: str) -> None:
    try:
        _get_client().table("daily_alerts").update(
            {"followup_tweet_id": followup_tweet_id}
        ).eq("id", alert_id).execute()
    except Exception as e:
        logger.warning(f"[AlertFollowup] 마킹 실패 (id={alert_id}): {e}")


# ─────────────────────────────────────────────────────────────────────────
# 3. 메인 — run_followup
# ─────────────────────────────────────────────────────────────────────────

def run_followup(snapshot: dict[str, Any]) -> dict:
    """
    D안 메인. run_alert.run() 말미에서 현재 snapshot을 전달받아 실행.
    추가 데이터 수집 0회.
    """
    logger.info(f"[AlertFollowup] v{VERSION} 시작")

    if not isinstance(snapshot, dict) or snapshot.get("vix") is None:
        logger.info("[AlertFollowup] 현재 스냅샷 불충분 → skip")
        return {"success": True, "processed": 0, "reason": "no_snapshot"}

    targets = find_followup_targets()
    if not targets:
        return {"success": True, "processed": 0}

    processed = 0
    for alert in targets:
        then_snap = _fetch_then_snapshot(str(alert.get("alert_date", "")))
        if then_snap is None:
            _mark(alert["id"], "SKIP_NO_DATA")
            logger.info(
                f"[AlertFollowup] 당시 데이터 없음 → SKIP_NO_DATA "
                f"(id={alert['id']}, date={alert.get('alert_date')})"
            )
            continue

        text = build_followup_text(alert, then_snap, snapshot)
        if text is None:
            _mark(alert["id"], "SKIP_NO_DATA")
            logger.info(f"[AlertFollowup] 비교값 부재 → SKIP_NO_DATA (id={alert['id']})")
            continue

        try:
            from publishers.x_publisher import publish_thread
            pub = publish_thread([text], reply_to=str(alert["tweet_id"]))
            ids = pub.get("tweet_ids", [])
            f_id = str(ids[0]) if ids else None
        except Exception as e:
            logger.warning(f"[AlertFollowup] 발행 실패 (id={alert['id']}): {e}")
            f_id = None

        if f_id and f_id != "DRY_RUN":
            _mark(alert["id"], f_id)
            processed += 1
            logger.info(
                f"[AlertFollowup] 발행 완료: {alert.get('alert_type')}/"
                f"{alert.get('alert_level')} → {f_id}"
            )
        elif f_id == "DRY_RUN":
            logger.info(f"[AlertFollowup] DRY_RUN → 마킹 skip (id={alert['id']})")
        # 발행 실패는 마킹하지 않음 → 다음 실행(윈도우 내) 재시도

    return {"success": True, "processed": processed, "targets": len(targets)}
