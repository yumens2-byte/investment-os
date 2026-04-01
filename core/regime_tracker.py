"""
core/regime_tracker.py (B-6)
==============================
레짐 이력 저장 + 전환 감지

저장 파일: data/published/regime_history.json
구조:
{
  "last_regime": "Risk-On",
  "last_risk_level": "LOW",
  "last_market_score": {"growth_score": 2, ...},
  "last_signals": {"volatility_score": 2, ...},
  "last_updated": "2026-04-01",
  "changes": [
    {"date": "2026-04-01", "from_regime": "Risk-On", "to_regime": "Risk-Off", ...}
  ]
}

레짐 위험도 서열 (숫자 높을수록 위험):
  Risk-On(1) < AI Bubble(2) < Transition(3) < Risk-Off(4)
  < Stagflation(5) < Recession(6) = Oil Shock(6)
  < Liquidity Crisis(7) < Crisis Regime(8)

전환 방향:
  숫자 상승 = 위험 증가 방향 → L2
  숫자 하락 = 회복 방향 → L1
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REGIME_HISTORY_PATH = Path(
    os.getenv("REGIME_HISTORY_PATH", "data/published/regime_history.json")
)

# ── 레짐 위험도 서열 ──────────────────────────────────────
# 숫자가 높을수록 위험한 레짐
_REGIME_RISK_ORDER = {
    "Risk-On": 1,
    "AI Bubble": 2,
    "Transition": 3,
    "Risk-Off": 4,
    "Stagflation Risk": 5,
    "Recession Risk": 6,
    "Oil Shock": 6,
    "Liquidity Crisis": 7,
    "Crisis Regime": 8,
}


def _load() -> dict:
    """레짐 이력 JSON 로드"""
    if REGIME_HISTORY_PATH.exists():
        try:
            return json.loads(REGIME_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_regime": "",
        "last_risk_level": "",
        "last_market_score": {},
        "last_signals": {},
        "last_updated": "",
        "changes": [],
    }


def _save(data: dict) -> None:
    """레짐 이력 JSON 저장"""
    REGIME_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGIME_HISTORY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def detect_regime_change(
    new_regime: str,
    new_risk_level: str,
    new_market_score: dict,
    new_signals: dict,
    dt_utc: Optional[datetime] = None,
) -> Optional[dict]:
    """
    레짐 전환 감지

    Args:
        new_regime:       현재 레짐 (예: "Risk-Off")
        new_risk_level:   현재 Risk Level (예: "HIGH")
        new_market_score: 현재 Market Score dict
        new_signals:      현재 signals dict (19개 시그널 포함)
        dt_utc:           기준 시각

    Returns:
        전환 있으면 dict, 없으면 None
        {
          "regime_changed": bool,
          "risk_changed": bool,
          "old_regime": str,
          "new_regime": str,
          "old_risk_level": str,
          "new_risk_level": str,
          "direction": "danger" | "recovery",  # 위험 증가 vs 회복
          "old_market_score": dict,
          "new_market_score": dict,
          "old_signals": dict,
          "new_signals": dict,
        }
    """
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)
    kst = dt_utc + timedelta(hours=9)
    today = kst.strftime("%Y-%m-%d")

    history = _load()
    old_regime = history.get("last_regime", "")
    old_risk_level = history.get("last_risk_level", "")
    old_market_score = history.get("last_market_score", {})
    old_signals = history.get("last_signals", {})

    # ── 첫 실행: 기준 저장만 하고 변화 없음 반환 ──
    if not old_regime:
        history["last_regime"] = new_regime
        history["last_risk_level"] = new_risk_level
        history["last_market_score"] = new_market_score
        history["last_signals"] = new_signals
        history["last_updated"] = today
        _save(history)
        logger.info("[RegimeTracker] 첫 실행 — 기준 레짐 저장")
        return None

    # ── 레짐/리스크 변화 판단 ──
    regime_changed = (old_regime != new_regime)
    risk_changed = (old_risk_level != new_risk_level)

    if not regime_changed and not risk_changed:
        # 변화 없음 — 이력만 갱신
        history["last_regime"] = new_regime
        history["last_risk_level"] = new_risk_level
        history["last_market_score"] = new_market_score
        history["last_signals"] = new_signals
        history["last_updated"] = today
        _save(history)
        logger.info("[RegimeTracker] 레짐 변화 없음")
        return None

    # ── 전환 방향 판단 ──
    # 레짐 위험도 서열 비교
    old_order = _REGIME_RISK_ORDER.get(old_regime, 3)
    new_order = _REGIME_RISK_ORDER.get(new_regime, 3)

    if new_order > old_order:
        direction = "danger"     # 위험 증가 방향
    elif new_order < old_order:
        direction = "recovery"   # 회복 방향
    else:
        # 레짐 서열 동일 — Risk Level로 판단
        risk_order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        old_r = risk_order.get(old_risk_level, 2)
        new_r = risk_order.get(new_risk_level, 2)
        direction = "danger" if new_r > old_r else "recovery"

    change = {
        "regime_changed": regime_changed,
        "risk_changed": risk_changed,
        "old_regime": old_regime,
        "new_regime": new_regime,
        "old_risk_level": old_risk_level,
        "new_risk_level": new_risk_level,
        "direction": direction,
        "old_market_score": old_market_score,
        "new_market_score": new_market_score,
        "old_signals": old_signals,
        "new_signals": new_signals,
    }

    # ── 이력 저장 ──
    history["last_regime"] = new_regime
    history["last_risk_level"] = new_risk_level
    history["last_market_score"] = new_market_score
    history["last_signals"] = new_signals
    history["last_updated"] = today
    changes = history.get("changes", [])
    changes.append({
        "date": today,
        "from_regime": old_regime,
        "to_regime": new_regime,
        "from_risk": old_risk_level,
        "to_risk": new_risk_level,
        "direction": direction,
    })
    history["changes"] = changes[-30:]  # 최근 30일만 유지
    _save(history)

    logger.info(
        f"[RegimeTracker] 전환 감지 — "
        f"{old_regime}({old_risk_level}) → {new_regime}({new_risk_level}) "
        f"[{direction}]"
    )
    return change
