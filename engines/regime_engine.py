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

    # 3-B. Oil High ($90+) 하한 보정 (v1.16.0 추가)
    oil_state = signals.get("oil_state", "Moderate")
    if oil_state == "High":
        normalized = max(normalized, 35)   # Oil $90~100 → MEDIUM 근접

    # 4. 수익률 곡선 역전 보정
    if signals.get("yield_curve_inverted", False):
        normalized = max(normalized, 40)   # 역전 → 최소 MEDIUM 진입

    # 5. F&G Extreme Fear 하한 보정 (v1.16.0 추가)
    #    F&G ≤ 1 (Extreme Fear) → 최소 MEDIUM (40)
    #    시장 전체가 극단적 공포인데 Risk=LOW는 비상식적
    fear_greed = signals.get("fear_greed_score", 3)
    if fear_greed <= 1:
        normalized = max(normalized, 45)   # Extreme Fear → 최소 MEDIUM 중반

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
        return "Oil Shock", "유가 급등 충격 — 원자재 쇼크"

    # Oil High + 인플레이션 복합 (v1.16.0 추가)
    # $90~100 범위에서도 인플레이션 기대가 높으면 Stagflation 방향
    oil_state = signals.get("oil_state", "Moderate")
    infl_exp = signals.get("infl_exp_score", 2)
    if oil_state == "High" and infl_exp >= 3:
        return "Stagflation Risk", "유가 $90+ + 기대인플레 상승 — 스태그플레이션 경고"

    # Liquidity Crisis: 신용 위기 + VIX 극단
    if credit_stress == "High" and vix_state in ("High", "Extreme"):
        return "Liquidity Crisis", "신용 스프레드 급등 + 극단적 변동성"

    # Recession Risk: 장단기 역전 + 성장 약화
    if recession and market_score.get("growth_score", 1) >= 4:
        return "Recession Risk", "수익률 곡선 역전 + 성장 둔화"

    return "", ""


# ──────────────────────────────────────────────────────────────
# 3. 기본 Regime 결정
# ──────────────────────────────────────────────────────────────

def _determine_base_regime(market_score: dict, signals: dict) -> Tuple[str, str]:
    """
    Market Score 기반 기본 Regime 결정.
    Returns: (regime, reason)

    v1.16.0 (2026-04-01):
      - F&G Extreme Fear 가드: F&G ≤ 1 이면 Risk-On 차단
      - VIX Elevated 가드: VIX 25+ 이면 Risk-On 차단
      - Oil 직접 가드: WTI $95+ 이면 Risk-On 차단
      → 단일 일간 반등(SPY +3%)으로 Risk-On 오판 방지
    """
    growth = market_score.get("growth_score", 2)
    inflation = market_score.get("inflation_score", 2)
    liquidity = market_score.get("liquidity_score", 2)
    risk = market_score.get("risk_score", 2)
    stability = market_score.get("financial_stability_score", 2)

    sentiment = signals.get("sentiment_state", "Neutral")
    sp500 = signals.get("sp500_change", 0.0)

    # ── Risk-On 차단 가드 (v1.16.0 추가) ────────────────────
    # 아래 조건 중 하나라도 해당하면 Risk-On 판정을 차단합니다.
    # Market Score만으로는 잡지 못하는 극단 상황을 원시 시그널로 직접 체크.
    fear_greed = signals.get("fear_greed_score", 3)
    vix_state = signals.get("vix_state", "Normal")
    oil_state = signals.get("oil_state", "Moderate")
    vol_term_state = signals.get("vol_term_state", "")

    risk_on_blocked = False
    block_reasons = []

    # (1) F&G Extreme Fear (≤ 1): 시장 전체가 극단적 공포 상태
    #     단일 일간 반등(데드캣바운스)으로 growth 양호해도 Risk-On 불가
    if fear_greed <= 1:
        risk_on_blocked = True
        block_reasons.append("Extreme Fear")

    # (2) VIX Elevated 이상 (25+): 변동성 경계 구간
    #     시장 통념: VIX 20 이상은 불안정, 25 이상은 위험 경계
    if vix_state in ("Elevated", "High", "Extreme"):
        risk_on_blocked = True
        block_reasons.append(f"VIX {vix_state}")

    # (3) Oil $95+ (High/Oil Shock): 에너지 비용 부담
    if oil_state in ("High", "Oil Shock"):
        risk_on_blocked = True
        block_reasons.append(f"Oil {oil_state}")

    # (4) Vol Term Backwardation: 구조적 위기 의심
    if vol_term_state == "Backwardation":
        risk_on_blocked = True
        block_reasons.append("Vol Backwardation")

    # ── 레짐 판정 ──────────────────────────────────────────

    # Stagflation: 인플레이션 높고 성장 약한 경우
    if inflation >= 4 and growth >= 4:
        return "Stagflation Risk", "고인플레이션 + 성장 약화"

    # Risk-Off: 전반적 위험 회피
    if risk >= 4 and sentiment == "Bearish":
        return "Risk-Off", "공포 확산 + 위험자산 회피"

    # Risk-Off: F&G Extreme Fear 단독으로도 Risk-Off 판정
    if fear_greed <= 1 and risk >= 3:
        return "Risk-Off", f"Extreme Fear (F&G) + Risk Score {risk} — 위험회피 국면"

    # Risk-On: 성장 우호적 — 단, 가드 조건 통과해야 함
    if growth <= 2 and liquidity <= 2 and risk <= 2:
        if sentiment == "Bearish":
            return "Risk-Off", "성장 지표 양호하나 Bearish 심리 — 방어 우선"
        if risk_on_blocked:
            # 가드에 걸림 → Risk-On 대신 Transition 판정
            reason = " + ".join(block_reasons)
            return "Transition", f"Score 양호하나 {reason} — Risk-On 차단"
        return "Risk-On", "성장 + 유동성 + 저위험"

    # AI Bubble: 성장 과열 (낮은 점수 = 과열)
    if growth == 1 and risk == 1 and stability == 1:
        if risk_on_blocked:
            return "Transition", f"과열 시그널이나 {' + '.join(block_reasons)} — AI Bubble 차단"
        return "AI Bubble", "과열 성장 + 저변동성 + 과도한 낙관"

    # 중간 상태
    if risk >= 3 or liquidity >= 3:
        return "Risk-Off", "유동성 긴축 또는 위험회피 경향"

    return "Transition", "혼재 시그널 — 방향 불명확"


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
