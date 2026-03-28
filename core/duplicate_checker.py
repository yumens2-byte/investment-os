"""
core/duplicate_checker.py
X 발행 직전 중복 검사.
history.json에 최근 발행 이력을 저장하고, 동일 내용 재발행을 차단한다.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from config.settings import HISTORY_FILE, DUPLICATE_CHECK_COUNT

logger = logging.getLogger(__name__)


def _load_history() -> list:
    """발행 이력 로드"""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[DupChecker] 이력 로드 실패: {e}")
        return []


def _save_history(history: list) -> None:
    """발행 이력 저장"""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[DupChecker] 이력 저장 실패: {e}")


def _compute_content_hash(content: str) -> str:
    """발행 콘텐츠 해시값 산출 (SHA256 앞 16자)"""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]


def _compute_regime_hash(data: dict) -> str:
    """
    regime + risk_level + top_etf 조합 해시.
    콘텐츠가 달라도 동일 레짐이면 중복으로 간주.
    """
    regime = data.get("market_regime", {}).get("market_regime", "")
    risk = data.get("market_regime", {}).get("market_risk_level", "")
    rank = data.get("etf_analysis", {}).get("etf_rank", {})
    top_etfs = sorted(rank.items(), key=lambda x: x[1])[:3]
    top_str = ",".join([e for e, _ in top_etfs])
    combined = f"{regime}|{risk}|{top_str}"
    return hashlib.sha256(combined.encode()).hexdigest()[:12]


def is_duplicate(tweet_text: str, data: dict) -> bool:
    """
    중복 발행 여부 판단.
    True = 중복 → 발행 차단.
    False = 신규 → 발행 허용.

    비교 기준:
      1. 트윗 본문 해시 (완전 동일)
      2. 레짐+리스크+Top ETF 해시 (동일 시장 상태 재발행 방지)
    """
    history = _load_history()
    recent = history[-DUPLICATE_CHECK_COUNT:]

    content_hash = _compute_content_hash(tweet_text)
    regime_hash = _compute_regime_hash(data)

    for record in recent:
        if record.get("content_hash") == content_hash:
            logger.warning(f"[DupChecker] 콘텐츠 중복 감지: hash={content_hash}")
            return True
        if record.get("regime_hash") == regime_hash:
            logger.warning(f"[DupChecker] 레짐 중복 감지: hash={regime_hash}")
            return True

    return False


def record_published(tweet_text: str, data: dict, tweet_id: str = "DRY_RUN") -> None:
    """
    발행 성공 후 이력 기록.
    tweet_id: 실제 X 트윗 ID 또는 DRY_RUN.
    """
    history = _load_history()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tweet_id": tweet_id,
        "content_hash": _compute_content_hash(tweet_text),
        "regime_hash": _compute_regime_hash(data),
        "regime": data.get("market_regime", {}).get("market_regime", ""),
        "risk_level": data.get("market_regime", {}).get("market_risk_level", ""),
        "preview": tweet_text[:80],
    }
    history.append(record)

    # 이력은 최대 200건 유지
    if len(history) > 200:
        history = history[-200:]

    _save_history(history)
    logger.info(f"[DupChecker] 발행 이력 기록: tweet_id={tweet_id}")
