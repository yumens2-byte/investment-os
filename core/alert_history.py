"""
core/alert_history.py (v1.7.0)
================================
Alert 발송 이력 관리.

발송 규칙:
  1. 등급 상승(악화) 시 → 무조건 발송 (L1→L2, L2→L3)
  2. 등급 하락(완화) 시 → DOWNGRADE_COOLDOWN_HOURS 이내 재발송 금지 (진동 억제)
  3. 등급 유지 시 → COOLDOWN_HOURS 이내 재발송 금지
  4. Alert 해제(조건 미충족) 후 재발생 → 발송

쿨다운:
  - 동일 등급 반복: COOLDOWN_HOURS = 4시간
  - 등급 하락(완화): DOWNGRADE_COOLDOWN_HOURS = 2시간  ← v1.7.0 신규

이력 보관: 최대 200건

v1.7.0 변경사항:
  등급 진동(L1↔L2 반복) 억제 — 등급 하락(완화) 시 2시간 쿨다운 적용
  등급 상승(악화)는 기존과 동일하게 무조건 즉시 발행
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

ALERT_HISTORY_FILE = DATA_DIR / "published" / "alert_history.json"
COOLDOWN_HOURS = 4             # 동일 등급 재발송 금지 시간
DOWNGRADE_COOLDOWN_HOURS = 2   # 등급 하락(완화) 재발송 억제 시간 — v1.7.0 신규


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


def should_send(alert_type: str, level: str) -> tuple:
    """
    발송 여부 판단.

    Returns:
        (send: bool, reason: str)

    규칙 (v1.7.0):
      1. 직전 등급보다 높음(악화) → 무조건 발송 (L1→L2, L2→L3)
      2. 직전 등급보다 낮음(완화) → DOWNGRADE_COOLDOWN_HOURS 이내 차단 (진동 억제)
      3. 직전 등급과 같고 COOLDOWN_HOURS 이내 → 차단
      4. 직전 등급과 같고 COOLDOWN_HOURS 이후 → 발송 (지속 상황 재알림)
    """
    history = _load()
    now     = datetime.now(timezone.utc)
    cutoff  = now - timedelta(hours=COOLDOWN_HOURS)

    # 이 타입의 마지막 발송 기록 찾기
    last_record = None
    for record in reversed(history):
        if record.get("alert_type") == alert_type:
            last_record = record
            break

    # 최초 발송
    if last_record is None:
        return True, "최초 발송"

    last_level    = last_record.get("level", "")
    last_time_str = last_record.get("timestamp", "")

    # 등급 변화 처리
    if last_level != level:

        # 등급 상승(악화): L1→L2, L2→L3 → 무조건 즉시 발행 (위험 증가 알림)
        if level > last_level:
            return True, f"등급 악화 {last_level}→{level} ↑"

        # 등급 하락(완화): L2→L1, L3→L2 → DOWNGRADE_COOLDOWN_HOURS 쿨다운 적용
        # 목적: VIX 28~35 사이 진동 시 10분마다 L1↔L2 반복 발행 방지
        try:
            last_time         = datetime.fromisoformat(last_time_str)
            downgrade_cutoff  = now - timedelta(hours=DOWNGRADE_COOLDOWN_HOURS)

            if last_time > downgrade_cutoff:
                # 쿨다운 중 — 발행 차단
                remaining = int(
                    (last_time + timedelta(hours=DOWNGRADE_COOLDOWN_HOURS) - now).seconds / 60
                )
                return False, (
                    f"등급 완화 {last_level}→{level} ↓ 쿨다운 중 "
                    f"({remaining}분 남음)"
                )

            # 쿨다운 경과 — 발행 허용 (상황 개선 재알림)
            elapsed = int((now - last_time).seconds / 60)
            return True, (
                f"등급 완화 {last_level}→{level} ↓ "
                f"({DOWNGRADE_COOLDOWN_HOURS}시간 경과 — 상황 개선 재알림)"
            )

        except Exception:
            # 이력 파싱 오류 → 안전하게 발행 허용
            return True, f"등급 완화 {last_level}→{level} ↓ (이력 파싱 오류)"

    # 동일 등급 — 쿨다운 체크
    try:
        last_time = datetime.fromisoformat(last_time_str)
        if last_time > cutoff:
            remaining = int((last_time + timedelta(hours=COOLDOWN_HOURS) - now).seconds / 60)
            return False, f"동일 등급({level}) 쿨다운 중 ({remaining}분 남음)"
        else:
            elapsed = int((now - last_time).seconds / 60)
            return True, f"동일 등급({level}) {elapsed}분 경과 — 지속 상황 재알림"
    except Exception:
        return True, "이력 파싱 오류 — 발송"


def record_alert(alert_type: str, level: str, tweet_id: str, preview: str) -> None:
    """Alert 발송 이력 기록"""
    history = _load()
    history.append({
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "alert_type": alert_type,
        "level":      level,
        "tweet_id":   tweet_id,
        "preview":    preview[:80],
    })
    _save(history)
    logger.info(f"[AlertHistory] 기록: {alert_type}/{level} tweet_id={tweet_id}")


def should_send_countdown(vix_level: int) -> tuple:
    """
    VIX 카운트다운 발송 여부 판단 — 레벨별 하루 1회 제한

    규칙:
      - 동일 레벨(25/27/29)은 오늘(KST 기준) 이미 발행됐으면 차단
      - 상위 레벨(25→27→29)은 신규 발행 허용
    """
    history = _load()
    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    today   = now_kst.strftime("%Y-%m-%d")

    for record in reversed(history):
        if record.get("alert_type") != "VIX_COUNTDOWN":
            continue
        rec_level = record.get("vix_level", 0)
        if rec_level != vix_level:
            continue
        # 오늘(KST) 이미 이 레벨 발행됨
        ts_str = record.get("timestamp", "")
        try:
            ts_kst = datetime.fromisoformat(ts_str) + timedelta(hours=9)
            if ts_kst.strftime("%Y-%m-%d") == today:
                return False, f"VIX {vix_level} 오늘 이미 발행됨 ({ts_kst.strftime('%H:%M')} KST)"
        except Exception:
            pass

    return True, f"VIX {vix_level} 오늘 첫 발행"


def record_countdown(vix_level: int, tweet_id: str) -> None:
    """VIX 카운트다운 발송 이력 기록"""
    history = _load()
    history.append({
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "alert_type": "VIX_COUNTDOWN",
        "vix_level":  vix_level,
        "level":      "L1",
        "tweet_id":   tweet_id,
        "preview":    f"VIX {vix_level} 카운트다운",
    })
    _save(history)
    logger.info(f"[AlertHistory] VIX 카운트다운 기록: level={vix_level}")


# 하위 호환성 유지
def is_cooldown(alert_type: str, level: str) -> bool:
    send, reason = should_send(alert_type, level)
    if not send:
        logger.info(f"[AlertHistory] 차단: {reason}")
    return not send
