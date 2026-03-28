"""
engines/macro_engine.py
02_macro_liquidity_engine.md 기준 구현.
Raw 시장 데이터 → Standard Signal → Market Score 산출.
"""
import logging
from typing import Optional
from config.settings import (
    VIX_LOW_THRESHOLD, VIX_HIGH_THRESHOLD,
    US10Y_LOW_THRESHOLD, US10Y_HIGH_THRESHOLD,
    OIL_LOW_THRESHOLD, OIL_HIGH_THRESHOLD,
    DXY_HIGH_THRESHOLD,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 1. Signal Layer  (출력: 1~5 점수, 낮을수록 Risk-Off 우호)
# ──────────────────────────────────────────────────────────────

def _score_vix(vix: float) -> dict:
    """VIX → 변동성 압박 점수 (5=극단 공포, 1=안정)"""
    if vix < 15:
        level, score = "Low", 1
    elif vix < VIX_LOW_THRESHOLD:
        level, score = "Normal", 2
    elif vix < VIX_HIGH_THRESHOLD:
        level, score = "Elevated", 3
    elif vix < 40:
        level, score = "High", 4
    else:
        level, score = "Extreme", 5
    return {"vix_state": level, "volatility_score": score}


def _score_us10y(us10y: float) -> dict:
    """US10Y → 금리 환경 점수 (4=고금리 압박)"""
    if us10y < US10Y_LOW_THRESHOLD:
        env, score = "Low Rate", 1
    elif us10y < US10Y_HIGH_THRESHOLD:
        env, score = "Moderate Rate", 2
    elif us10y < 5.0:
        env, score = "High Rate", 3
    else:
        env, score = "Very High Rate", 4
    return {"rate_environment": env, "rate_score": score}


def _score_oil(oil: float) -> dict:
    """WTI → 원자재/인플레이션 압박 점수"""
    if oil < OIL_LOW_THRESHOLD:
        state, score = "Low", 1
    elif oil < OIL_HIGH_THRESHOLD:
        state, score = "Moderate", 2
    elif oil < 110:
        state, score = "High", 3
    else:
        state, score = "Oil Shock", 4
    return {
        "oil_state": state,
        "commodity_pressure_score": score,
        "oil_shock_signal": score >= 4,
    }


def _score_dxy(dxy: float) -> dict:
    """Dollar Index → 유동성 축소 신호"""
    if dxy < 98:
        state, score = "Weak", 1
    elif dxy < DXY_HIGH_THRESHOLD:
        state, score = "Moderate", 2
    else:
        state, score = "Strong", 3
    return {
        "dollar_state": state,
        "dollar_tightening_signal": score >= 3,
    }


def _score_credit(credit_stress: str) -> dict:
    """신용 스트레스 → 금융 안정 점수"""
    mapping = {"Low": 1, "Moderate": 2, "High": 3, "Unknown": 2}
    score = mapping.get(credit_stress, 2)
    return {
        "credit_stress_signal": credit_stress,
        "financial_stability_score": score,
    }


def _score_yield_curve(inverted: bool) -> dict:
    """장단기 역전 여부 → 경기침체 신호"""
    return {
        "yield_curve_inverted": inverted,
        "recession_signal": inverted,
    }


def _score_news_sentiment(news_sentiment: str) -> dict:
    """뉴스/Reddit 감성 → 심리 점수"""
    mapping = {"Bullish": 1, "Neutral": 2, "Bearish": 3, "Unknown": 2}
    score = mapping.get(news_sentiment, 2)
    return {"sentiment_score": score, "sentiment_state": news_sentiment}


# ──────────────────────────────────────────────────────────────
# 2. Market Score  (6개 축)
# ──────────────────────────────────────────────────────────────

def compute_market_score(signals: dict) -> dict:
    """
    6개 축 Market Score 산출.
    출력: 각 축 1~5 (5=위험/부담, 1=안정/우호)
    """
    vix_score = signals.get("volatility_score", 2)
    rate_score = signals.get("rate_score", 2)
    oil_score = signals.get("commodity_pressure_score", 2)
    stability_score = signals.get("financial_stability_score", 2)
    sentiment_score = signals.get("sentiment_score", 2)

    # growth_score: 성장 환경 (낮은 금리 + 낮은 VIX = 성장 우호)
    growth_score = max(1, min(5, round((vix_score + rate_score) / 2)))

    # inflation_score: 원자재 + 금리
    inflation_score = max(1, min(5, round((oil_score + rate_score) / 2)))

    # liquidity_score: 신용 + 달러 (dollar_score 없으면 stability 사용)
    dollar_score = 2 if not signals.get("dollar_tightening_signal") else 3
    liquidity_score = max(1, min(5, round((stability_score + dollar_score) / 2)))

    # risk_score: VIX + 감성
    risk_score = max(1, min(5, round((vix_score + sentiment_score) / 2)))

    score = {
        "growth_score": growth_score,
        "inflation_score": inflation_score,
        "liquidity_score": liquidity_score,
        "risk_score": risk_score,
        "financial_stability_score": stability_score,
        "commodity_pressure_score": oil_score,
    }
    logger.debug(f"[Macro] Market Score: {score}")
    return score


# ──────────────────────────────────────────────────────────────
# 3. 통합 진입점
# ──────────────────────────────────────────────────────────────

def run_macro_engine(
    snapshot: dict,
    fred_data: dict,
    news_sentiment: str,
) -> dict:
    """
    시장 스냅샷 + FRED + 뉴스 감성 → 신호 + Market Score 반환.

    Args:
        snapshot: collect_market_snapshot() 결과
        fred_data: collect_macro_data() 결과
        news_sentiment: "Bullish" / "Neutral" / "Bearish"

    Returns:
        macro_result dict (signals + market_score)
    """
    logger.info("[MacroEngine] 분석 시작")

    vix = snapshot.get("vix", 20.0)
    us10y = snapshot.get("us10y", 4.0)
    oil = snapshot.get("oil", 75.0)
    dxy = snapshot.get("dollar_index", 100.0)
    credit_stress = fred_data.get("credit_stress", "Unknown")
    yield_curve_inverted = fred_data.get("yield_curve_inverted", False)

    # 개별 신호
    signals = {}
    signals.update(_score_vix(vix))
    signals.update(_score_us10y(us10y))
    signals.update(_score_oil(oil))
    signals.update(_score_dxy(dxy))
    signals.update(_score_credit(credit_stress))
    signals.update(_score_yield_curve(yield_curve_inverted))
    signals.update(_score_news_sentiment(news_sentiment))

    # Market Score
    market_score = compute_market_score(signals)

    logger.info(
        f"[MacroEngine] VIX={vix:.1f}({signals['vix_state']}) | "
        f"Rate={signals['rate_environment']} | "
        f"Oil={signals['oil_state']} | "
        f"Dollar={signals['dollar_state']}"
    )

    return {
        "signals": signals,
        "market_score": market_score,
    }
