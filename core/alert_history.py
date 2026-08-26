"""
core/alert_history.py (v1.8.0)
================================
Alert 발송 이력 관리.

발송 규칙:
  1. 등급 상승(악화) 시 → 무조건 발송 (L1→L2, L2→L3)
  2. 등급 하락(완화) 시 → DOWNGRADE_COOLDOWN_HOURS 이내 재발송 금지 (진동 억제)
  3. 등급 유지 시 → COOLDOWN_HOURS 이내 재발송 금지
  4. Alert 해제(조건 미충족) 후 재발생 → 발송

쿨다운:
  - 동일 등급 반복: COOLDOWN_HOURS = 4시간
  - 등급 하락(완화): DOWNGRADE_COOLDOWN_HOURS = 2시간

이력 보관: 파일 최대 200건 / DB append-only

v1.8.0 변경사항 (T-4, 2026-08-27):
  Alert 상태 Supabase 백엔드 연동 (core/alert_state_backend.py).
  - 판정 로직을 순수 함수 _decide_send()로 추출 — 파일/DB 경로 동일 함수 사용
    (판정 등가성 보장, property 테스트 대상)
  - ALERT_STATE_BACKEND 모드:
      file(기본): 기존 파일 판정 — 동작 완전 무변경
      dual:       파일 판정(발송 기준) + DB 병행기록 + DUAL-CHECK 일치 로그
      supabase:   DB 판정 + 파일 병행기록 유지 (DB 예외 시 파일 폴백)
  - record_alert/record_countdown: 파일 기록 항상 유지 + dual/supabase 시 DB 병행
  - DUAL-CHECK 로그는 should_send만 대상 (countdown은 기록 병행만 — S1 게이트 주 판정 대상)

v1.7.0: 등급 진동(L1↔L2 반복) 억제 — 등급 하락(완화) 시 2시간 쿨다운
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

VERSION = "1.8.0"

ALERT_HISTORY_FILE = DATA_DIR / "published" / "alert_history.json"
COOLDOWN_HOURS = 4             # 동일 등급 재발송 금지 시간
DOWNGRADE_COOLDOWN_HOURS = 2   # 등급 하락(완화) 재발송 억제 시간 — v1.7.0


def _load() -> list:
    if not ALERT_HISTORY_FILE.exists():
        return []
    try:
        with open(ALERT_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(history: list) -> None:
    try:
        ALERT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-200:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[AlertHistory] 저장 실패: {e}")


def get_last_level(alert_type: str) -> str:
    """직전 발송된 동일 타입의 Alert 등급 반환 (없으면 '')"""
    history = _load()
    for record in reversed(history):
        if record.get("alert_type") == alert_type:
            return record.get("level", "")
    return ""


# ─────────────────────────────────────────────────────────────
# 판정 순수 함수 (v1.8.0) — 파일/DB 경로 공용, v1.7.0 규칙과 완전 등가
# ─────────────────────────────────────────────────────────────

def _decide_send(
    last_level: str,
    last_time_str: str,
    level: str,
    now: datetime | None = None,
) -> tuple:
    """
    직전 발송 기록 1건 기준 발송 여부 판정 (순수 함수).

    규칙 (v1.7.0과 동일):
      1. 직전 등급보다 높음(악화) → 무조건 발송
      2. 직전 등급보다 낮음(완화) → DOWNGRADE_COOLDOWN_HOURS 이내 차단
      3. 직전 등급과 같고 COOLDOWN_HOURS 이내 → 차단
      4. 직전 등급과 같고 COOLDOWN_HOURS 이후 → 발송
      * 타임스탬프 파싱 오류 → 발송 허용 (기존 보수 정책 유지)
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=COOLDOWN_HOURS)

    # 등급 변화 처리
    if last_level != level:

        # 등급 상승(악화): L1→L2, L2→L3 → 무조건 즉시 발행 (위험 증가 알림)
        if level > last_level:
            return True, f"등급 악화 {last_level}→{level} ↑"

        # 등급 하락(완화): DOWNGRADE_COOLDOWN_HOURS 쿨다운 적용
        try:
            last_time        = datetime.fromisoformat(last_time_str)
            downgrade_cutoff = now - timedelta(hours=DOWNGRADE_COOLDOWN_HOURS)

            if last_time > downgrade_cutoff:
                remaining = int(
                    (last_time + timedelta(hours=DOWNGRADE_COOLDOWN_HOURS) - now).seconds / 60
                )
                return False, (
                    f"등급 완화 {last_level}→{level} ↓ 쿨다운 중 "
                    f"({remaining}분 남음)"
                )

            return True, (
                f"등급 완화 {last_level}→{level} ↓ "
                f"({DOWNGRADE_COOLDOWN_HOURS}시간 경과 — 상황 개선 재알림)"
            )

        except Exception:
            return True, f"등급 완화 {last_level}→{level} ↓ (이력 파싱 오류)"

    # 동일 등급 — 쿨다운 체크
    try:
        last_time = datetime.fromisoformat(last_time_str)
        if last_time > cutoff:
            remaining = int((last_time + timedelta(hours=COOLDOWN_HOURS) - now).seconds / 60)
            return False, f"동일 등급({level}) 쿨다운 중 ({remaining}분 남음)"
        elapsed = int((now - last_time).seconds / 60)
        return True, f"동일 등급({level}) {elapsed}분 경과 — 지속 상황 재알림"
    except Exception:
        return True, "이력 파싱 오류 — 발송"


def _file_last_record(alert_type: str) -> dict | None:
    """파일 이력에서 해당 타입 마지막 발송 기록 반환"""
    for record in reversed(_load()):
        if record.get("alert_type") == alert_type:
            return record
    return None


def _file_decide(alert_type: str, level: str) -> tuple:
    """파일 이력 기반 판정 (v1.7.0 동작과 등가)"""
    last_record = _file_last_record(alert_type)
    if last_record is None:
        return True, "최초 발송"
    return _decide_send(
        last_record.get("level", ""),
        last_record.get("timestamp", ""),
        level,
    )


def _db_decide(alert_type: str, level: str) -> tuple:
    """
    DB 이력 기반 판정.
    Raises: DB 접근 예외 전파 (호출측 파일 폴백)
    """
    from core.alert_state_backend import db_fetch_last, parse_db_timestamp

    row = db_fetch_last(alert_type, channel="main")
    if row is None:
        return True, "최초 발송(DB)"
    send, reason = _decide_send(
        row.get("level", ""),
        parse_db_timestamp(row.get("created_at", "")).isoformat(),
        level,
    )
    return send, f"{reason} (DB)"


def should_send(alert_type: str, level: str) -> tuple:
    """
    발송 여부 판단.

    Returns:
        (send: bool, reason: str)

    v1.8.0 모드 분기:
      supabase → DB 판정 (예외 시 파일 폴백)
      dual     → 파일 판정(발송 기준) + DB 판정 비교 로그(DUAL-CHECK)
      file     → 파일 판정 (기존 동작)
    """
    from core.alert_state_backend import get_mode

    mode = get_mode()

    if mode == "supabase":
        try:
            return _db_decide(alert_type, level)
        except Exception as e:  # noqa: BLE001 — DB 장애 격리, 파일 폴백 설계
            logger.warning(f"[AlertHistory] DB 판정 실패 → 파일 폴백: {e}")
            return _file_decide(alert_type, level)

    file_result = _file_decide(alert_type, level)

    if mode == "dual":
        # S1 Shadow: 파일 vs DB 판정 일치율 대조 (발송은 파일 기준 — 부수효과 없음)
        try:
            db_result = _db_decide(alert_type, level)
            logger.info(
                f"[AlertHistory] DUAL-CHECK type={alert_type}/{level} "
                f"file={file_result[0]} db={db_result[0]} "
                f"match={file_result[0] == db_result[0]}"
            )
        except Exception as e:  # noqa: BLE001 — Shadow 대조는 부수 기능, 격리
            logger.warning(f"[AlertHistory] DUAL-CHECK DB 판정 실패 (파일 기준 유지): {e}")

    return file_result


def record_alert(alert_type: str, level: str, tweet_id: str, preview: str) -> None:
    """
    Alert 발송 이력 기록.
    v1.8.0: 파일 기록 항상 유지 + dual/supabase 모드 시 DB 병행기록.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    history = _load()
    history.append({
        "timestamp":  now_iso,
        "alert_type": alert_type,
        "level":      level,
        "tweet_id":   tweet_id,
        "preview":    preview[:80],
    })
    _save(history)
    logger.info(f"[AlertHistory] 기록: {alert_type}/{level} tweet_id={tweet_id}")

    from core.alert_state_backend import db_record, get_mode
    if get_mode() in ("dual", "supabase"):
        db_record(
            alert_type, level,
            tweet_id=tweet_id, preview=preview,
            channel="main", created_at=now_iso,
        )


def should_send_countdown(vix_level: int) -> tuple:
    """
    VIX 카운트다운 발송 여부 판단 — 레벨별 하루 1회 제한 (KST 기준).

    v1.8.0: supabase 모드 → DB 최신 1건 기준 (append-only 특성상
            "최신 1건이 오늘" ⟺ "오늘 기록 존재" — 파일 판정과 등가).
            DB 예외 시 파일 폴백.
    """
    from core.alert_state_backend import get_mode

    if get_mode() == "supabase":
        try:
            from core.alert_state_backend import (
                db_fetch_last_countdown,
                parse_db_timestamp,
            )
            row = db_fetch_last_countdown(vix_level)
            if row is not None:
                now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
                try:
                    ts_kst = parse_db_timestamp(row.get("created_at", "")) + timedelta(hours=9)
                    if ts_kst.strftime("%Y-%m-%d") == now_kst.strftime("%Y-%m-%d"):
                        return False, (
                            f"VIX {vix_level} 오늘 이미 발행됨 "
                            f"({ts_kst.strftime('%H:%M')} KST) (DB)"
                        )
                except Exception:  # noqa: BLE001, S110 — 파싱 실패 시 허용(원본 보수 정책 동일)
                    pass
            return True, f"VIX {vix_level} 오늘 첫 발행 (DB)"
        except Exception as e:  # noqa: BLE001 — DB 장애 격리, 파일 폴백 설계
            logger.warning(f"[AlertHistory] 카운트다운 DB 판정 실패 → 파일 폴백: {e}")

    # 파일 판정 (file/dual/폴백 — v1.7.0 동작과 등가)
    history = _load()
    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    today   = now_kst.strftime("%Y-%m-%d")

    for record in reversed(history):
        if record.get("alert_type") != "VIX_COUNTDOWN":
            continue
        rec_level = record.get("vix_level", 0)
        if rec_level != vix_level:
            continue
        ts_str = record.get("timestamp", "")
        try:
            ts_kst = datetime.fromisoformat(ts_str) + timedelta(hours=9)
            if ts_kst.strftime("%Y-%m-%d") == today:
                return False, f"VIX {vix_level} 오늘 이미 발행됨 ({ts_kst.strftime('%H:%M')} KST)"
        except Exception:
            pass

    return True, f"VIX {vix_level} 오늘 첫 발행"


def record_countdown(vix_level: int, tweet_id: str) -> None:
    """
    VIX 카운트다운 발송 이력 기록.
    v1.8.0: 파일 기록 항상 유지 + dual/supabase 모드 시 DB 병행기록.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    history = _load()
    history.append({
        "timestamp":  now_iso,
        "alert_type": "VIX_COUNTDOWN",
        "vix_level":  vix_level,
        "level":      "L1",
        "tweet_id":   tweet_id,
        "preview":    f"VIX {vix_level} 카운트다운",
    })
    _save(history)
    logger.info(f"[AlertHistory] VIX 카운트다운 기록: level={vix_level}")

    from core.alert_state_backend import db_record, get_mode
    if get_mode() in ("dual", "supabase"):
        db_record(
            "VIX_COUNTDOWN", "L1",
            tweet_id=tweet_id, preview=f"VIX {vix_level} 카운트다운",
            channel="main", vix_level=vix_level, created_at=now_iso,
        )


# 하위 호환성 유지
def is_cooldown(alert_type: str, level: str) -> bool:
    send, reason = should_send(alert_type, level)
    if not send:
        logger.info(f"[AlertHistory] 차단: {reason}")
    return not send
