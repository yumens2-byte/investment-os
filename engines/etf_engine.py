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
    sma_data: Dict[str, dict] = None,
) -> Dict[str, int]:
    """
    ETF 점수 산출.
    Base score (regime) + 당일 momentum 보정 + SMA 트렌드 보정 (E-1).
    """
    base = _get_base_score(regime)
    scored = {}
    if sma_data is None:
        sma_data = {}

    for etf in ETF_CORE:
        score = base.get(etf, 3)
        change_pct = etf_prices.get(etf, {}).get("change_pct", 0.0)

        # 당일 변동률 보정: ±1 점 조정
        if change_pct > 1.5:
            score = min(5, score + 1)
        elif change_pct < -1.5:
            score = max(1, score - 1)

        # E-1: SMA5/SMA20 트렌드 보정 — 중기 추세 반영
        sma_trend = sma_data.get(etf, {}).get("trend", "flat")
        if sma_trend == "golden_cross":
            score = min(5, score + 1)   # 상승 추세 보너스
        elif sma_trend == "dead_cross":
            score = max(1, score - 1)   # 하락 추세 페널티

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
            strategy_reason[etf] = "상위 랭크 + 긍정적 타이밍 시그널"
        elif rank >= 5:
            if risk_level == "HIGH":
                stance[etf] = "Underweight"
                strategy_reason[etf] = "고위험 환경에서 하위 랭크"
            else:
                stance[etf] = "Underweight"
                strategy_reason[etf] = "상대적으로 약한 포지셔닝"
        elif etf == "TLT" and risk_level in ("MEDIUM", "HIGH"):
            stance[etf] = "Hedge"
            strategy_reason[etf] = "리스크 상승 구간 듀레이션 헤지"
        else:
            stance[etf] = "Neutral"
            strategy_reason[etf] = "뚜렷한 시그널 없음 — 중립 유지"

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

    # 총합 100% 정규화 (v1.16.0 수정 — 분산 투입)
    # 기존: 남는 비중을 최대 ETF 1개에 전부 투입 → 과도한 집중
    # 수정: Overweight ETF에 우선 분산 → 부족하면 Neutral에도 분산
    total = sum(allocation.values())
    if total != 100:
        diff = 100 - total  # 양수: 부족, 음수: 초과

        if diff > 0:
            # 부족분 → Overweight ETF에 우선 분산 투입
            ow_etfs = [e for e in ETF_CORE if stance.get(e) == "Overweight"]
            if not ow_etfs:
                # Overweight 없으면 Neutral에 분산
                ow_etfs = [e for e in ETF_CORE if stance.get(e) != "Underweight"]
            if ow_etfs:
                per_etf = diff // len(ow_etfs)
                remainder = diff % len(ow_etfs)
                for i, etf in enumerate(ow_etfs):
                    allocation[etf] += per_etf + (1 if i < remainder else 0)
            else:
                # fallback: 최대 비중 ETF에 흡수
                max_etf = max(allocation, key=lambda k: allocation[k])
                allocation[max_etf] += diff
        elif diff < 0:
            # 초과분 → 가장 큰 비중 ETF에서 차감
            max_etf = max(allocation, key=lambda k: allocation[k])
            allocation[max_etf] += diff  # diff는 음수

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
    sma_data: Dict[str, dict] = None,
) -> dict:
    """ETF 분석 전 과정 실행 후 etf_analysis / etf_strategy / etf_allocation 반환"""
    logger.info(f"[ETFEngine] 분석 시작: Regime={regime}, Risk={risk_level}")

    etf_score = compute_etf_score(regime, etf_prices, market_score, sma_data=sma_data)
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


# ──────────────────────────────────────────────────────────────
# 6. ETF 상세 전략 근거 자동 생성 (B-7, 2026-04-01 추가)
# ──────────────────────────────────────────────────────────────

# ETF별 영향 시그널 매핑
# key: ETF 티커
# value: list of (signal_key, state_key, direction)
#   direction: "bullish" = 점수 높을수록 해당 ETF에 유리
#              "bearish" = 점수 높을수록 해당 ETF에 불리
_ETF_SIGNAL_MAP = {
    "QQQM": [
        ("equity_momentum_score", "equity_momentum_state", "bearish"),   # 낮을수록 유리
        ("ai_momentum_score",     "ai_momentum_state",     "bearish"),   # AI Leadership(1)이 유리
        ("nasdaq_rel_score",      "nasdaq_rel_state",      "bearish"),   # Growth주도(1)가 유리
        ("breadth_score",         "breadth_state",         "bearish"),   # Broad(1)이 유리
        ("vol_term_score",        "vol_term_state",        "bearish"),   # Contango(1)가 유리
    ],
    "XLK": [
        ("equity_momentum_score", "equity_momentum_state", "bearish"),
        ("ai_momentum_score",     "ai_momentum_state",     "bearish"),
        ("nasdaq_rel_score",      "nasdaq_rel_state",      "bearish"),
        ("breadth_score",         "breadth_state",         "bearish"),
    ],
    "SPYM": [
        ("volatility_score",          "vix_state",               "bearish"),
        ("financial_stability_score", "credit_stress_signal",    "bearish"),
        ("claims_score",              "claims_state",            "bearish"),
        ("breadth_score",             "breadth_state",           "bearish"),
    ],
    "XLE": [
        ("commodity_pressure_score", "oil_state",       "bullish"),    # 유가 높을수록 유리
        ("infl_exp_score",           "infl_exp_state",  "bullish"),    # 인플레 높을수록 유리
        ("em_stress_score",          "em_stress_state",  "bearish"),   # EM 안정이 유리
    ],
    "ITA": [
        ("volatility_score",          "vix_state",              "bullish"),   # 공포 높으면 방산 수요
        ("financial_stability_score", "credit_stress_signal",   "bullish"),   # 불안정하면 방산
        ("em_stress_score",           "em_stress_state",        "bullish"),   # EM 스트레스 → 지정학
        ("banking_stress_score",      "banking_stress_state",   "neutral"),
    ],
    "TLT": [
        ("volatility_score",  "vix_state",        "bullish"),    # VIX 높으면 채권 수요
        ("vol_term_score",    "vol_term_state",    "bullish"),    # 백워데이션 → 안전자산
        ("rate_score",        "rate_environment",  "bearish"),    # 금리 낮을수록 유리
        ("fear_greed_score",  "fear_greed_state",  "special"),    # Extreme Fear → 채권
        ("claims_score",      "claims_state",      "bullish"),    # 노동시장 악화 → 금리인하 기대
    ],
}

# ETF별 정형화된 리스크 텍스트
_ETF_RISK_TEXT = {
    "QQQM": {
        "Overweight": "금리 상승 + 대형주 계절성 약화 가능",
        "Underweight": "테크 섹터 회복 지연 시 기회비용",
    },
    "XLK": {
        "Overweight": "기술주 순환 + 규제 리스크",
        "Underweight": "섹터 과대평가 지속 가능",
    },
    "SPYM": {
        "Overweight": "공격적 환경 전환 시 기회비용",
        "Underweight": "시장 추가 하락 가능",
    },
    "XLE": {
        "Overweight": "유가 급락 + 대체에너지 전환",
        "Underweight": "지정학 리스크로 유가 반등 가능",
    },
    "ITA": {
        "Overweight": "방산 예산 삭감 가능성",
        "Underweight": "전통 방산 수요 감소",
    },
    "TLT": {
        "Overweight": "금리 반등 시 가격 하락",
        "Underweight": "신용리스크 확대 시 기회 상실",
    },
}

# 시그널 한국어 라벨 (signal_diff.py와 동일)
_SIGNAL_LABEL = {
    "volatility_score":          "VIX",
    "rate_score":                "금리",
    "commodity_pressure_score":  "유가",
    "financial_stability_score": "금융안정",
    "sentiment_score":           "시장심리",
    "fear_greed_score":          "공포탐욕",
    "crypto_risk_score":         "BTC",
    "equity_momentum_score":     "주가모멘텀",
    "xlf_gld_score":             "금융/금",
    "breadth_score":             "시장참여도",
    "vol_term_score":            "변동성구조",
    "claims_score":              "실업수당",
    "infl_exp_score":            "기대인플레",
    "em_stress_score":           "신흥국",
    "ai_momentum_score":         "AI모멘텀",
    "nasdaq_rel_score":          "나스닥상대",
    "banking_stress_score":      "은행스트레스",
}


def generate_etf_rationale(
    etf: str,
    stance: str,
    signals: dict,
    regime: str,
) -> dict:
    """
    [B-7] ETF별 시그널 기반 매수/매도 근거 자동 생성

    19개 시그널 중 해당 ETF에 영향을 주는 시그널만 선별하고,
    현재 Stance(Overweight/Underweight/Neutral/Hedge)와 일치하는
    방향의 시그널을 근거로 추출한다.

    Args:
        etf:      ETF 티커 (예: "TLT")
        stance:   현재 Stance (예: "Overweight")
        signals:  19개 시그널 dict (macro_engine 출력)
        regime:   현재 레짐 (예: "Risk-Off")

    Returns:
        {
          "rationale": "VIX Extreme + Vol 백워데이션 + 금리 하락 기대",
          "risk": "금리 반등 시 가격 하락",
          "signals_used": ["volatility_score", "vol_term_score", "rate_score"],
        }
    """
    if not signals:
        return {
            "rationale": f"Regime {regime} 기반 전략",
            "risk": "시그널 데이터 없음",
            "signals_used": [],
        }

    signal_map = _ETF_SIGNAL_MAP.get(etf, [])
    if not signal_map:
        return {
            "rationale": f"Regime {regime} 기반 전략",
            "risk": "—",
            "signals_used": [],
        }

    # ── 해당 ETF에 유리/불리한 시그널 선별 ──
    favorable = []   # Overweight 근거가 되는 시그널
    unfavorable = []  # Underweight 근거가 되는 시그널

    for sig_key, state_key, direction in signal_map:
        score = signals.get(sig_key)
        state = signals.get(state_key, "")
        if score is None:
            continue

        label = _SIGNAL_LABEL.get(sig_key, sig_key)

        if direction == "bullish":
            # 점수 높을수록 해당 ETF에 유리
            if score >= 3:
                favorable.append(f"{label} {state}")
            elif score <= 1:
                unfavorable.append(f"{label} {state}")
        elif direction == "bearish":
            # 점수 낮을수록 해당 ETF에 유리
            if score <= 1:
                favorable.append(f"{label} {state}")
            elif score >= 3:
                unfavorable.append(f"{label} {state}")
        elif direction == "special":
            # Fear & Greed: Extreme Fear(1)→TLT 유리, Extreme Greed(5)→TLT 불리
            if score <= 2:
                favorable.append(f"{label} {state}")
            elif score >= 4:
                unfavorable.append(f"{label} {state}")

    # ── Stance에 따라 근거 선택 ──
    if stance in ("Overweight", "Hedge"):
        rationale_list = favorable[:4] if favorable else [f"Regime {regime} 기반"]
    elif stance == "Underweight":
        rationale_list = unfavorable[:4] if unfavorable else [f"Regime {regime} 기반"]
    else:  # Neutral
        rationale_list = [f"뚜렷한 방향성 없음 — Regime {regime}"]

    rationale = " + ".join(rationale_list)

    # ── 리스크 텍스트 ──
    risk_map = _ETF_RISK_TEXT.get(etf, {})
    risk = risk_map.get(stance, "포지션 모니터링 필요")

    # 사용된 시그널 키 목록
    used = [sk for sk, _, _ in signal_map if signals.get(sk) is not None]

    return {
        "rationale": rationale,
        "risk": risk,
        "signals_used": used,
    }


def generate_all_etf_rationales(
    stance_dict: dict,
    signals: dict,
    regime: str,
) -> Dict[str, dict]:
    """
    [B-7] 전체 ETF 6종의 근거 일괄 생성

    Args:
        stance_dict: {"QQQM": "Overweight", "TLT": "Hedge", ...}
        signals:     19개 시그널 dict
        regime:      현재 레짐

    Returns:
        {"QQQM": {"rationale": "...", "risk": "...", "signals_used": [...]}, ...}
    """
    result = {}
    for etf in ETF_CORE:
        stance = stance_dict.get(etf, "Neutral")
        result[etf] = generate_etf_rationale(etf, stance, signals, regime)
    return result
