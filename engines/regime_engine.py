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
    Market Score 6개 축 + 원시 신호(VIX/Oil) 직접 반영 → 0~100 Risk Score.

    버그 수정 (v1.5.3):
      - 기존 정규화 공식이 너무 낮은 점수를 반환해 VIX=31도 LOW로 판정
      - VIX/Oil 임계치 직접 체크로 하한 보정 추가
    """
    ms = market_score

    # 1. 가중합 (최소 100, 최대 500)
    total = (
        ms.get("risk_score", 2) * 25 +
        ms.get("liquidity_score", 2) * 20 +
        ms.get("inflation_score", 2) * 20 +
        ms.get("financial_stability_score", 2) * 20 +
        ms.get("commodity_pressure_score", 2) * 15
    )
    # 0~100 정규화
    normalized = int((total - 100) / 4)

    # 2. VIX 직접 하한 보정 — 점수가 낮아도 VIX가 높으면 강제 상향
    vix_state = signals.get("vix_state", "Normal")
    vix_floor = {
        "Low":      0,
        "Normal":   0,
        "Elevated": 40,   # VIX 20~30 → 최소 MEDIUM(40)
        "High":     70,   # VIX 30~40 → HIGH(70) ← 시장 통념: VIX 30 = 공포 진입선
        "Extreme":  80,   # VIX 40+   → HIGH 상단(80)
    }
    normalized = max(normalized, vix_floor.get(vix_state, 0))

    # 3. Oil Shock 직접 하한 보정
    if signals.get("oil_shock_signal", False):
        normalized = max(normalized, 50)   # Oil Shock → 최소 MEDIUM

    # 4. 수익률 곡선 역전 보정
    if signals.get("yield_curve_inverted", False):
        normalized = max(normalized, 40)   # 역전 → 최소 MEDIUM 진입

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
        return "Oil Shock", "Commodity shock — oil surge"

    # Liquidity Crisis: 신용 위기 + VIX 극단
    if credit_stress == "High" and vix_state in ("High", "Extreme"):
        return "Liquidity Crisis", "Credit spread surge + extreme volatility"

    # Recession Risk: 장단기 역전 + 성장 약화
    if recession and market_score.get("growth_score", 1) >= 4:
        return "Recession Risk", "Yield curve inversion + growth slowdown"

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
        return "Stagflation Risk", "High inflation + weakening growth"

    # Risk-Off: 전반적 위험 회피
    if risk >= 4 and sentiment == "Bearish":
        return "Risk-Off", "Fear spreading + risk asset avoidance"

    # Risk-On: 성장 우호적
    if growth <= 2 and liquidity <= 2 and risk <= 2:
        return "Risk-On", "Growth + liquidity + low risk"

    # AI Bubble: 성장 과열 (낮은 점수 = 과열)
    if growth == 1 and risk == 1 and stability == 1:
        return "AI Bubble", "Overheated growth + low vol + excess optimism"

    # 중간 상태
    if risk >= 3 or liquidity >= 3:
        return "Risk-Off", "Liquidity tightening or risk-off tendency"

    return "Transition", "Mixed signals — direction unclear"


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
