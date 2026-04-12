"""
core/signal_diff.py (B-5/B-6 공통)
====================================
이전 vs 현재 signals/market_score 비교 → 변화 원인 Top3 추출

목적:
  "ETF 랭킹이 바뀌었다" / "레짐이 전환되었다" 만으로는 부족.
  "왜 바뀌었는지"를 19개 시그널 중 변화량 상위 3개로 자동 설명.

사용처:
  - B-5 ETF 랭킹 변화 알림: 어떤 시그널이 ETF 순위를 뒤집었는지
  - B-6 레짐 전환 알림: 어떤 Score/시그널이 레짐 전환을 일으켰는지

출력 예시:
  {
    "top_movers": [
      {"signal": "volatility_score", "old": 2, "new": 5, "change": +3,
       "state": "Extreme"},
      {"signal": "em_stress_score", "old": 1, "new": 4, "change": +3,
       "state": "EM Crisis Spillover"},
      ...
    ],
    "score_diff": {
      "growth_score": {"old": 2, "new": 4, "change": +2},
      ...
    },
    "summary": "VIX Extreme + EM Crisis Spillover + Weak Labor"
  }
"""
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── 비교 대상 시그널 키 목록 ──────────────────────────────
# _score 로 끝나는 수치형 시그널만 비교 (state 문자열은 제외)
_SIGNAL_KEYS = [
    # 기존 7개
    "volatility_score",
    "rate_score",
    "commodity_pressure_score",
    "financial_stability_score",
    "sentiment_score",
    # Tier 1 (4개)
    "fear_greed_score",
    "crypto_risk_score",
    "equity_momentum_score",
    "xlf_gld_score",
    # Tier 2 (5개)
    "breadth_score",
    "vol_term_score",
    "claims_score",
    "infl_exp_score",
    "em_stress_score",
    # Tier 3 (3개)
    "ai_momentum_score",
    "nasdaq_rel_score",
    "banking_stress_score",
]

# ── 시그널 → state 키 매핑 ────────────────────────────────
# 각 시그널의 한국어/영어 상태 라벨을 가져오기 위한 매핑
# signals dict에 "{signal_key}" 와 함께 "{base}_state" 가 존재
_SIGNAL_STATE_MAP = {
    "volatility_score":          "vix_state",
    "rate_score":                "rate_environment",
    "commodity_pressure_score":  "oil_state",
    "financial_stability_score": "credit_stress_signal",
    "sentiment_score":           "sentiment_state",
    "fear_greed_score":          "fear_greed_state",
    "crypto_risk_score":         "crypto_risk_state",
    "equity_momentum_score":     "equity_momentum_state",
    "xlf_gld_score":             "xlf_gld_state",
    "breadth_score":             "breadth_state",
    "vol_term_score":            "vol_term_state",
    "claims_score":              "claims_state",
    "infl_exp_score":            "infl_exp_state",
    "em_stress_score":           "em_stress_state",
    "ai_momentum_score":         "ai_momentum_state",
    "nasdaq_rel_score":          "nasdaq_rel_state",
    "banking_stress_score":      "banking_stress_state",
    # ── Priority A (2026-04-11) ───────────────────────────────
    "gold_score":            "gold_state",
    "small_cap_score":       "small_cap_state",
    "move_score":            "move_state",
    "market_quadrant_score": "market_quadrant",
    "spread_score":          "spread_state",
    "trend_score":           "trend_state",
    # ── Priority B (2026-04-11) ───────────────────────────────
    "cpi_score":          "cpi_state",
    "labor_score":        "labor_state",
    "sector_score":       "sector_state",
    "copper_gold_score":  "copper_gold_state",
    "fed_bs_score":       "fed_bs_state",
    "sofr_score":         "sofr_state",
}

# ── 시그널 한국어 라벨 ────────────────────────────────────
# 알림 메시지에서 사용할 시그널 한국어 이름
_SIGNAL_LABEL_KR = {
    "volatility_score":          "VIX",
    "rate_score":                "금리",
    "commodity_pressure_score":  "원자재",
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
    # ── Priority A (2026-04-11) ───────────────────────────────
    "gold_score":            "금현물",
    "small_cap_score":       "소형주",
    "move_score":            "채권변동성",
    "market_quadrant_score": "시장국면",
    "spread_score":          "금리스프레드",
    "trend_score":           "기술추세",
    # ── Priority B (2026-04-11) ──────────────────────────────
    "cpi_score":         "CPI인플레",
    "labor_score":       "고용시장",
    "sector_score":      "섹터로테이션",
    "copper_gold_score": "구리/금비율",
    "fed_bs_score":      "연준자산",
    "sofr_score":        "단기자금",
}

# ── Market Score 키 ───────────────────────────────────────
_SCORE_KEYS = [
    "growth_score", "inflation_score", "liquidity_score",
    "risk_score", "financial_stability_score", "commodity_pressure_score",
]


def compute_signal_diff(
    old_signals: dict,
    new_signals: dict,
    top_n: int = 3,
) -> dict:
    """
    이전 vs 현재 시그널 비교 → 변화량 Top N 추출

    Args:
        old_signals: 이전 실행의 signals dict
        new_signals: 현재 signals dict
        top_n:       상위 몇 개를 추출할지 (기본 3)

    Returns:
        {
          "top_movers": [
            {"signal": str, "old": int, "new": int, "change": int,
             "state": str, "label_kr": str},
            ...
          ],
          "summary": str  # 한국어 요약 (예: "VIX Extreme + 신흥국 급락 + 실업수당 증가")
        }
    """
    if not old_signals or not new_signals:
        return {"top_movers": [], "summary": "이전 데이터 없음"}

    # ── 각 시그널의 변화량 계산 ──
    diffs = []
    for key in _SIGNAL_KEYS:
        old_val = old_signals.get(key)
        new_val = new_signals.get(key)

        # 둘 다 숫자인 경우만 비교
        if not isinstance(old_val, (int, float)) or not isinstance(new_val, (int, float)):
            continue

        change = new_val - old_val
        if change == 0:
            continue  # 변화 없으면 스킵

        # 현재 시그널의 state 라벨 가져오기
        state_key = _SIGNAL_STATE_MAP.get(key, "")
        state = new_signals.get(state_key, "")

        diffs.append({
            "signal": key,
            "old": old_val,
            "new": new_val,
            "change": change,
            "abs_change": abs(change),  # 정렬용
            "state": state,
            "label_kr": _SIGNAL_LABEL_KR.get(key, key),
        })

    # ── 변화량 절대값 내림차순 정렬 → Top N 추출 ──
    diffs.sort(key=lambda x: x["abs_change"], reverse=True)
    top_movers = diffs[:top_n]

    # abs_change 필드 제거 (내부용)
    for m in top_movers:
        del m["abs_change"]

    # ── 한국어 요약 생성 ──
    # 형식: "VIX Extreme + 신흥국 EM Crisis Spillover + 실업수당 Weak Labor"
    summary_parts = []
    for m in top_movers:
        state_str = f" {m['state']}" if m["state"] else ""
        direction = "↑" if m["change"] > 0 else "↓"
        summary_parts.append(f"{m['label_kr']}{state_str} {direction}")

    summary = " + ".join(summary_parts) if summary_parts else "변화 미미"

    return {
        "top_movers": top_movers,
        "summary": summary,
    }


def compute_score_diff(
    old_score: dict,
    new_score: dict,
) -> dict:
    """
    이전 vs 현재 Market Score 비교

    Returns:
        {
          "growth_score": {"old": 2, "new": 4, "change": +2},
          "risk_score": {"old": 2, "new": 4, "change": +2},
          ...
          "biggest_change": "growth_score"  # 가장 큰 변화 Score
        }
    """
    if not old_score or not new_score:
        return {}

    result = {}
    max_change = 0
    biggest = ""

    for key in _SCORE_KEYS:
        old_val = old_score.get(key, 0)
        new_val = new_score.get(key, 0)
        change = new_val - old_val

        result[key] = {"old": old_val, "new": new_val, "change": change}

        if abs(change) > max_change:
            max_change = abs(change)
            biggest = key

    result["biggest_change"] = biggest
    return result
