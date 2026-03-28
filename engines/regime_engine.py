"""
engines/regime_engine.py
03_market_regime_engine.md 기준 구현.
Market Score + Signals → Market Regime + Risk Level 결정.
"""
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 1. Risk Level 계산 (0~100 점수 → LOW/MEDIUM/HIGH)
# ──────────────────────────────────────────────────────────────

def _compute_composite_risk_score(market_score: dict, signals: dict) -> int:
    """
    Market Score 6개 축을 종합하여 0~100 Risk Score 산출.
    (5점 체계이므로 최대 합산 30, 최소 6)
    """
    ms = market_score
    total = (
        ms.get("risk_score", 2) * 25 +          # 위험 심리 (가중 25%)
        ms.get("liquidity_score", 2) * 20 +      # 유동성 (20%)
        ms.get("inflation_score", 2) * 20 +      # 인플레이션 (20%)
        ms.get("financial_stability_score", 2) * 20 +  # 금융 안정 (20%)
        ms.get("commodity_pressure_score", 2) * 15     # 원자재 (15%)
    )
    # 최소 100, 최대 500 범위를 0~100으로 정규화
    normalized = int((total - 100) / 4)
    return max(0, min(100, normalized))


def _map_risk_level(score: int) -> str:
    """03_market_regime_engine.md 8. Risk Level Mapping"""
    if score < 40:
        return "LOW"
    elif score < 70:
        return "MEDIUM"
    else:
        return "HIGH"


# ──────────────────────────────────────────────────────────────
# 2. Shock Override 규칙
# ──────────────────────────────────────────────────────────────

def _detect_shock_regime(signals: dict, market_score: dict) -> Tuple[str, str]:
    """
    특수 충격 이벤트 감지 → Regime Override.
    Returns: (regime_type, shock_reason) or ("", "")
    """
    oil_shock = signals.get("oil_shock_signal", False)
    recession = signals.get("recession_signal", False)
    credit_stress = signals.get("credit_stress_signal", "Low")
    vix_state = signals.get("vix_state", "Normal")

    # Oil Shock: 유가 충격 신호
    if oil_shock and market_score.get("commodity_pressure_score", 1) >= 4:
        return "Oil Shock", "원자재 충격 — 유가 급등 지속"

    # Liquidity Crisis: 신용 위기 + VIX 극단
    if credit_stress == "High" and vix_state in ("High", "Extreme"):
        return "Liquidity Crisis", "신용 스프레드 급등 + 극단적 변동성"

    # Recession Risk: 장단기 역전 + 성장 약화
    if recession and market_score.get("growth_score", 1) >= 4:
        return "Recession Risk", "장단기 금리 역전 + 성장 둔화"

    return "", ""


# ──────────────────────────────────────────────────────────────
# 3. 기본 Regime 결정
# ──────────────────────────────────────────────────────────────

def _determine_base_regime(market_score: dict, signals: dict) -> Tuple[str, str]:
    """
    Market Score 기반 기본 Regime 결정.
    Returns: (regime, reason)
    """
    growth = market_score.get("growth_score", 2)
    inflation = market_score.get("inflation_score", 2)
    liquidity = market_score.get("liquidity_score", 2)
    risk = market_score.get("risk_score", 2)
    stability = market_score.get("financial_stability_score", 2)

    sentiment = signals.get("sentiment_state", "Neutral")
    sp500 = signals.get("sp500_change", 0.0)  # 직접 전달된 경우

    # Stagflation: 인플레이션 높고 성장 약한 경우
    if inflation >= 4 and growth >= 4:
        return "Stagflation Risk", "고인플레이션 + 성장 약화 동시 발생"

    # Risk-Off: 전반적 위험 회피
    if risk >= 4 and sentiment == "Bearish":
        return "Risk-Off", "공포 심리 확산 + 위험 자산 회피"

    # Risk-On: 성장 우호적
    if growth <= 2 and liquidity <= 2 and risk <= 2:
        return "Risk-On", "성장 + 유동성 + 낮은 위험"

    # AI Bubble: 성장 과열 (낮은 점수 = 과열)
    if growth == 1 and risk == 1 and stability == 1:
        return "AI Bubble", "성장 과열 + 낮은 변동성 + 과도한 낙관"

    # 중간 상태
    if risk >= 3 or liquidity >= 3:
        return "Risk-Off", "유동성 축소 또는 위험 회피 경향"

    return "Transition", "복합 신호 — 방향성 불분명"


# ──────────────────────────────────────────────────────────────
# 4. 통합 진입점
# ──────────────────────────────────────────────────────────────

def run_regime_engine(market_score: dict, signals: dict, snapshot: dict) -> dict:
    """
    Market Score + Signals → Market Regime 결정.

    Returns:
        market_regime dict (market_regime, market_risk_level, regime_reason)
    """
    logger.info("[RegimeEngine] 레짐 판단 시작")

    # signals에 스냅샷 정보 보완
    signals = {**signals, "sp500_change": snapshot.get("sp500", 0.0)}

    # Risk Score 계산
    composite_score = _compute_composite_risk_score(market_score, signals)
    risk_level = _map_risk_level(composite_score)

    # Shock Override 먼저 확인
    shock_regime, shock_reason = _detect_shock_regime(signals, market_score)
    if shock_regime:
        regime = shock_regime
        reason = shock_reason
        # Shock 감지 시 리스크 레벨 최소 HIGH
        if risk_level == "LOW":
            risk_level = "MEDIUM"
        logger.info(f"[RegimeEngine] Shock Override: {regime}")
    else:
        regime, reason = _determine_base_regime(market_score, signals)

    result = {
        "market_regime": regime,
        "market_risk_level": risk_level,
        "regime_reason": reason,
        "composite_risk_score": composite_score,
    }

    logger.info(
        f"[RegimeEngine] 결과: {regime} | {risk_level} | 점수={composite_score}"
    )
    return result
