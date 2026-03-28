"""
engines/risk_engine.py
05_portfolio_risk_engine.md 기준 구현.
ETF 결과 + Market Regime → Portfolio Risk + Trading Signal.
"""
import logging
from typing import Dict
from config.settings import ETF_CORE

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 1. Position Sizing
# ──────────────────────────────────────────────────────────────

def _compute_position_sizing_multiplier(risk_level: str, composite_score: int) -> float:
    """
    Risk Level → Position Sizing Multiplier.
    1.0 = 풀 포지션, 0.5 = 절반 축소
    """
    if risk_level == "LOW":
        return 1.0
    elif risk_level == "MEDIUM":
        return 0.9 if composite_score < 55 else 0.75
    else:  # HIGH
        return 0.6 if composite_score < 80 else 0.5


# ──────────────────────────────────────────────────────────────
# 2. Crash Alert
# ──────────────────────────────────────────────────────────────

def _compute_crash_alert(
    risk_level: str,
    vix_state: str,
    credit_stress: str,
) -> str:
    """
    Crash Alert Level: LOW / MEDIUM / HIGH / CRITICAL
    """
    if risk_level == "HIGH" and vix_state in ("High", "Extreme") and credit_stress == "High":
        return "HIGH"
    elif risk_level == "HIGH" or vix_state in ("High", "Extreme"):
        return "MEDIUM"
    elif risk_level == "MEDIUM":
        return "LOW"
    else:
        return "LOW"


# ──────────────────────────────────────────────────────────────
# 3. Hedge Intensity
# ──────────────────────────────────────────────────────────────

def _compute_hedge_intensity(risk_level: str, crash_alert: str) -> str:
    """
    Hedge Intensity: None / Low / Medium / High
    """
    if crash_alert == "HIGH" or risk_level == "HIGH":
        return "High"
    elif risk_level == "MEDIUM":
        return "Medium"
    else:
        return "Low"


# ──────────────────────────────────────────────────────────────
# 4. Trading Signal
# ──────────────────────────────────────────────────────────────

def _determine_trading_signal(
    risk_level: str,
    stance: Dict[str, str],
    timing_signal: Dict[str, str],
) -> dict:
    """
    포트폴리오 전체 Trading Signal 결정.
    BUY / ADD / HOLD / REDUCE / HEDGE / SELL
    """
    buy_watch = [
        etf for etf in ETF_CORE
        if timing_signal.get(etf) in ("BUY", "ADD ON PULLBACK")
    ]
    hold = [
        etf for etf in ETF_CORE
        if timing_signal.get(etf) == "HOLD"
    ]
    reduce = [
        etf for etf in ETF_CORE
        if timing_signal.get(etf) in ("REDUCE", "SELL")
    ]

    # 전체 시그널
    if risk_level == "HIGH":
        signal = "HEDGE"
        reason = "High risk environment — defensive posture required"
    elif risk_level == "MEDIUM":
        if len(buy_watch) >= 3:
            signal = "ADD"
            reason = "Moderate risk with selective opportunities"
        else:
            signal = "HOLD"
            reason = "Moderate risk — maintain current exposure"
    else:  # LOW
        if len(buy_watch) >= 3:
            signal = "BUY"
            reason = "Low risk, positive momentum across multiple ETFs"
        else:
            signal = "HOLD"
            reason = "Low risk but limited broad momentum"

    return {
        "trading_signal": signal,
        "signal_reason": reason,
        "signal_matrix": {
            "buy_watch": buy_watch,
            "hold": hold,
            "reduce": reduce,
        },
    }


# ──────────────────────────────────────────────────────────────
# 5. One-Line Summary
# ──────────────────────────────────────────────────────────────

def _build_one_line_summary(
    regime: str,
    risk_level: str,
    trading_signal: str,
    top_etfs: list,
) -> str:
    top = ", ".join(top_etfs[:2])
    if risk_level == "HIGH":
        return f"{regime} — high risk environment favors {top} defensively."
    elif risk_level == "LOW":
        return f"{regime} — risk-on conditions favor growth via {top}."
    else:
        return f"{regime} — mixed signals; selective exposure to {top}."


# ──────────────────────────────────────────────────────────────
# 6. 통합 진입점
# ──────────────────────────────────────────────────────────────

def run_risk_engine(
    regime: str,
    risk_level: str,
    composite_score: int,
    market_score: dict,
    signals: dict,
    etf_analysis: dict,
    etf_strategy: dict,
    etf_allocation: dict,
    session_type: str = "postmarket",
) -> dict:
    """
    ETF 결과 + Regime → Portfolio Risk + Trading Signal + Output Helpers 반환
    """
    logger.info(f"[RiskEngine] 분석 시작: {regime} / {risk_level}")

    vix_state = signals.get("vix_state", "Normal")
    credit_stress = signals.get("credit_stress_signal", "Low")

    sizing = _compute_position_sizing_multiplier(risk_level, composite_score)
    crash_alert = _compute_crash_alert(risk_level, vix_state, credit_stress)
    hedge_intensity = _compute_hedge_intensity(risk_level, crash_alert)

    stance = etf_strategy.get("stance", {})
    timing = etf_analysis.get("timing_signal", {})

    trading_signal = _determine_trading_signal(risk_level, stance, timing)

    # Top ETF (rank 기준)
    rank = etf_analysis.get("etf_rank", {})
    top_etfs = [k for k, _ in sorted(rank.items(), key=lambda x: x[1])][:3]

    summary = _build_one_line_summary(
        regime, risk_level, trading_signal["trading_signal"], top_etfs
    )

    # 포트폴리오 리턴 특성
    overweights = [e for e, s in stance.items() if s == "Overweight"]
    portfolio_bias = "Defensive" if risk_level in ("MEDIUM", "HIGH") else "Growth"

    portfolio_risk = {
        "portfolio_return_impact": portfolio_bias,
        "portfolio_risk_impact": "High" if risk_level == "HIGH" else "Moderate",
        "diversification_score": max(40, 100 - composite_score),
        "drawdown_risk": "Elevated" if risk_level == "HIGH" else "Contained",
        "market_risk_level": risk_level,
        "crash_alert_level": crash_alert,
        "position_exposure": "Reduced beta" if sizing < 0.9 else "Full beta",
        "position_sizing_multiplier": sizing,
        "hedge_intensity": hedge_intensity,
    }

    output_helpers = {
        "one_line_summary": summary,
        "session_type": session_type,
        "language_policy": "ko",
    }

    logger.info(
        f"[RiskEngine] 완료: Signal={trading_signal['trading_signal']} | "
        f"Sizing={sizing:.1f}x | Crash={crash_alert}"
    )

    return {
        "portfolio_risk": portfolio_risk,
        "trading_signal": trading_signal,
        "output_helpers": output_helpers,
    }
