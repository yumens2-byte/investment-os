"""
engines/etf_engine.py
04_etf_strategy_engine.md 기준 구현.
ETF Score → Ranking → Timing → Strategy → Allocation 전 과정.
"""
import logging
from typing import Dict, List
from config.settings import ETF_CORE

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 1. ETF Score Engine
# ──────────────────────────────────────────────────────────────

# Regime별 ETF 우선순위 가중치 테이블
# (regime) → {etf: base_score}
_REGIME_SCORE_TABLE: Dict[str, Dict[str, int]] = {
    "Risk-On": {
        "QQQM": 5, "XLK": 4, "SPYM": 3,
        "XLE": 2, "ITA": 2, "TLT": 1,
    },
    "Risk-Off": {
        "QQQM": 1, "XLK": 1, "SPYM": 4,
        "XLE": 3, "ITA": 4, "TLT": 4,
    },
    "Oil Shock": {
        "QQQM": 1, "XLK": 1, "SPYM": 3,
        "XLE": 5, "ITA": 4, "TLT": 3,
    },
    "Liquidity Crisis": {
        "QQQM": 1, "XLK": 1, "SPYM": 3,
        "XLE": 2, "ITA": 3, "TLT": 5,
    },
    "Recession Risk": {
        "QQQM": 2, "XLK": 2, "SPYM": 4,
        "XLE": 2, "ITA": 3, "TLT": 5,
    },
    "Stagflation Risk": {
        "QQQM": 1, "XLK": 1, "SPYM": 3,
        "XLE": 5, "ITA": 4, "TLT": 2,
    },
    "AI Bubble": {
        "QQQM": 5, "XLK": 5, "SPYM": 2,
        "XLE": 2, "ITA": 2, "TLT": 1,
    },
    "Transition": {
        "QQQM": 3, "XLK": 3, "SPYM": 3,
        "XLE": 3, "ITA": 3, "TLT": 3,
    },
}


def _get_base_score(regime: str) -> Dict[str, int]:
    """Regime 기반 기초 점수 조회 (Unknown → Transition 사용)"""
    for key in _REGIME_SCORE_TABLE:
        if key in regime:
            return _REGIME_SCORE_TABLE[key]
    return _REGIME_SCORE_TABLE["Transition"]


def compute_etf_score(
    regime: str,
    etf_prices: Dict[str, dict],
    market_score: dict,
) -> Dict[str, int]:
    """
    ETF 점수 산출.
    Base score (regime) + momentum 보정.
    """
    base = _get_base_score(regime)
    scored = {}

    for etf in ETF_CORE:
        score = base.get(etf, 3)
        change_pct = etf_prices.get(etf, {}).get("change_pct", 0.0)

        # 당일 변동률 보정: ±0.5 점 조정
        if change_pct > 1.5:
            score = min(5, score + 1)
        elif change_pct < -1.5:
            score = max(1, score - 1)

        scored[etf] = score

    logger.debug(f"[ETFEngine] Score: {scored}")
    return scored


# ──────────────────────────────────────────────────────────────
# 2. ETF Ranking Engine
# ──────────────────────────────────────────────────────────────

def compute_etf_rank(etf_score: Dict[str, int]) -> Dict[str, int]:
    """
    ETF 상대 랭킹 산출.
    같은 점수 시 ETF_CORE 순서 기준 정렬.
    Returns: {etf: rank(1=최고)}
    """
    sorted_etfs = sorted(etf_score.items(), key=lambda x: -x[1])
    rank = {etf: idx + 1 for idx, (etf, _) in enumerate(sorted_etfs)}
    logger.debug(f"[ETFEngine] Rank: {rank}")
    return rank


def get_timing_signal(
    etf_rank: Dict[str, int],
    etf_prices: Dict[str, dict],
    risk_level: str,
) -> Dict[str, str]:
    """
    ETF Timing Signal 산출.
    Rank + 당일 변동률 → BUY / ADD ON PULLBACK / HOLD / REDUCE / SELL
    """
    signals = {}
    for etf, rank in etf_rank.items():
        change = etf_prices.get(etf, {}).get("change_pct", 0.0)

        if rank <= 2:
            if change > 0:
                signals[etf] = "BUY"
            else:
                signals[etf] = "ADD ON PULLBACK"
        elif rank <= 4:
            signals[etf] = "HOLD"
        else:  # rank 5~6
            if risk_level == "HIGH":
                signals[etf] = "SELL" if change < -1.5 else "REDUCE"
            else:
                signals[etf] = "REDUCE"

    return signals


# ──────────────────────────────────────────────────────────────
# 3. ETF Strategy (Stance)
# ──────────────────────────────────────────────────────────────

def compute_etf_strategy(
    etf_rank: Dict[str, int],
    timing_signal: Dict[str, str],
    risk_level: str,
) -> Dict[str, str]:
    """
    ETF Stance: Overweight / Neutral / Underweight / Hedge / Exclude
    """
    stance = {}
    strategy_reason = {}

    for etf in ETF_CORE:
        rank = etf_rank.get(etf, 3)
        timing = timing_signal.get(etf, "HOLD")

        if rank <= 2 and timing in ("BUY", "ADD ON PULLBACK"):
            stance[etf] = "Overweight"
            strategy_reason[etf] = "Top ranked ETF with positive timing signal"
        elif rank >= 5:
            if risk_level == "HIGH":
                stance[etf] = "Underweight"
                strategy_reason[etf] = "Low rank in high risk environment"
            else:
                stance[etf] = "Underweight"
                strategy_reason[etf] = "Relatively weak positioning"
        elif etf == "TLT" and risk_level in ("MEDIUM", "HIGH"):
            stance[etf] = "Hedge"
            strategy_reason[etf] = "Duration hedge in elevated risk environment"
        else:
            stance[etf] = "Neutral"
            strategy_reason[etf] = "Neutral positioning — no strong signal"

    return {"stance": stance, "strategy_reason": strategy_reason}


# ──────────────────────────────────────────────────────────────
# 4. ETF Allocation Engine
# ──────────────────────────────────────────────────────────────

# Regime별 배분 가이드라인 (Governance Rule: 총합 100% 필수)
_ALLOCATION_TEMPLATE: Dict[str, Dict[str, int]] = {
    "Risk-On":          {"QQQM": 35, "XLK": 25, "SPYM": 20, "XLE": 10, "ITA": 5,  "TLT": 5},
    "Risk-Off":         {"QQQM": 5,  "XLK": 5,  "SPYM": 20, "XLE": 25, "ITA": 25, "TLT": 20},
    "Oil Shock":        {"QQQM": 5,  "XLK": 5,  "SPYM": 20, "XLE": 30, "ITA": 25, "TLT": 15},
    "Liquidity Crisis": {"QQQM": 5,  "XLK": 5,  "SPYM": 15, "XLE": 15, "ITA": 20, "TLT": 40},
    "Recession Risk":   {"QQQM": 10, "XLK": 10, "SPYM": 25, "XLE": 10, "ITA": 15, "TLT": 30},
    "Stagflation Risk": {"QQQM": 5,  "XLK": 5,  "SPYM": 15, "XLE": 35, "ITA": 30, "TLT": 10},
    "AI Bubble":        {"QQQM": 35, "XLK": 35, "SPYM": 15, "XLE": 5,  "ITA": 5,  "TLT": 5},
    "Transition":       {"QQQM": 20, "XLK": 15, "SPYM": 20, "XLE": 15, "ITA": 15, "TLT": 15},
}


def compute_etf_allocation(
    regime: str,
    stance: Dict[str, str],
) -> dict:
    """
    ETF Allocation 산출.
    Governance Rule: 총합 100% 강제.
    """
    # Regime 기반 베이스 배분 선택
    template = _ALLOCATION_TEMPLATE.get("Transition")
    for key in _ALLOCATION_TEMPLATE:
        if key in regime:
            template = _ALLOCATION_TEMPLATE[key]
            break

    allocation = dict(template)

    # Stance 불일치 보정
    # Underweight이면 비중을 5%로 제한
    for etf, s in stance.items():
        if s == "Underweight" and allocation.get(etf, 0) > 10:
            allocation[etf] = 5
        elif s == "Overweight" and allocation.get(etf, 0) < 15:
            allocation[etf] = 20

    # 총합 100% 정규화
    total = sum(allocation.values())
    if total != 100:
        diff = 100 - total
        # 가장 큰 비중 ETF에 차이 흡수
        max_etf = max(allocation, key=lambda k: allocation[k])
        allocation[max_etf] += diff

    allocation_reason = {
        etf: f"{stance.get(etf, 'Neutral')} — regime={regime}" for etf in ETF_CORE
    }

    return {
        "allocation": allocation,
        "allocation_reason": allocation_reason,
        "total_weight": sum(allocation.values()),
    }


# ──────────────────────────────────────────────────────────────
# 5. 통합 진입점
# ──────────────────────────────────────────────────────────────

def run_etf_engine(
    regime: str,
    risk_level: str,
    market_score: dict,
    etf_prices: Dict[str, dict],
) -> dict:
    """ETF 분석 전 과정 실행 후 etf_analysis / etf_strategy / etf_allocation 반환"""
    logger.info(f"[ETFEngine] 분석 시작: Regime={regime}, Risk={risk_level}")

    etf_score = compute_etf_score(regime, etf_prices, market_score)
    etf_rank = compute_etf_rank(etf_score)
    timing_signal = get_timing_signal(etf_rank, etf_prices, risk_level)
    strategy = compute_etf_strategy(etf_rank, timing_signal, risk_level)
    allocation = compute_etf_allocation(regime, strategy["stance"])

    logger.info(
        f"[ETFEngine] 완료 | Top3: "
        f"{[k for k,v in sorted(etf_rank.items(), key=lambda x: x[1])[:3]]}"
    )

    return {
        "etf_analysis": {
            "etf_score": etf_score,
            "etf_rank": etf_rank,
            "timing_signal": timing_signal,
        },
        "etf_strategy": strategy,
        "etf_allocation": allocation,
    }
