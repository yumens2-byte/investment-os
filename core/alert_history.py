"""
core/alert_history.py (v1.6.1)
================================
Alert 발송 이력 관리.

발송 규칙:
  1. 등급 변화 시 → 무조건 발송 (L1→L2, L2→L1 모두)
  2. 등급 유지 시 → COOLDOWN_HOURS 이내 재발송 금지
  3. Alert 해제 (조건 미충족) 후 재발생 → 발송

쿨다운: 기본 4시간 (동일 등급 반복 발송 방지)
이력 보관: 최대 200건
"""
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

ALERT_HISTORY_FILE = DATA_DIR / "published" / "alert_history.json"
COOLDOWN_HOURS = 4   # 동일 등급 재발송 금지 시간


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

    규칙:
      1. 직전 등급과 다름 → 발송 (등급 변화)
      2. 직전 등급과 같고 COOLDOWN_HOURS 이내 → 차단
      3. 직전 등급과 같고 COOLDOWN_HOURS 이후 → 발송 (상황 지속 알림)
    """
    history = _load()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=COOLDOWN_HOURS)

    # 이 타입의 마지막 발송 기록 찾기
    last_record = None
    for record in reversed(history):
        if record.get("alert_type") == alert_type:
            last_record = record
            break

    # 최초 발송
    if last_record is None:
        return True, "최초 발송"

    last_level = last_record.get("level", "")
    last_time_str = last_record.get("timestamp", "")

    # 등급 변화 → 무조건 발송
    if last_level != level:
        direction = "↑ 상승" if level > last_level else "↓ 하락"
        return True, f"등급 변화 {last_level}→{level} ({direction})"

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "alert_type": alert_type,
        "level": level,
        "tweet_id": tweet_id,
        "preview": preview[:80],
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
    type_key = f"VIX_COUNTDOWN_{vix_level}"

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
