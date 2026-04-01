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
    # ── Tier 1 확장 시그널 임계값 (2026-04-01 추가) ──
    FEAR_GREED_FEAR_THRESHOLD, FEAR_GREED_GREED_THRESHOLD,
    BTC_RISK_DROP_THRESHOLD, BTC_RISK_SURGE_THRESHOLD,
    EQUITY_STRONG_MOVE_THRESHOLD, EQUITY_CRASH_THRESHOLD,
    XLF_GLD_RISK_ON_THRESHOLD, XLF_GLD_RISK_OFF_THRESHOLD,
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
    elif oil < 100:
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
# 1-B. Tier 1 확장 시그널 (2026-04-01 추가)
#     기존에 수집 중이지만 분석 엔진에 미연결이었던 데이터를
#     시그널로 변환하여 Market Score 정밀도를 높인다.
# ──────────────────────────────────────────────────────────────

def _score_fear_greed(fear_greed: dict) -> dict:
    """
    [T1-1] Fear & Greed Index → 심리 보강 시그널
    ─────────────────────────────────────────────
    목적: RSS 뉴스 감성만으로는 시장 심리를 정밀하게 측정하기 어려움.
          CNN/alternative.me의 Fear & Greed 지수를 보조 지표로 활용하여
          Risk Score 산출 시 sentiment_score를 보정한다.

    입력: fear_greed dict (collect_fear_greed() 결과)
          - value: 0~100 (0=극도 공포, 100=극도 탐욕)
          - label: "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"

    출력: fear_greed_score (1~5, 높을수록 위험/과열)
          - 1: Extreme Fear (역발상 매수 구간)
          - 2: Fear
          - 3: Neutral
          - 4: Greed
          - 5: Extreme Greed (과열 경고)
    """
    if fear_greed is None:
        # 수집 실패 시 중립 반환 — 기존 로직에 영향 없음
        return {"fear_greed_score": 3, "fear_greed_state": "Unknown"}

    value = fear_greed.get("value", 50)

    if value <= 20:
        state, score = "Extreme Fear", 1
    elif value <= FEAR_GREED_FEAR_THRESHOLD:
        state, score = "Fear", 2
    elif value <= FEAR_GREED_GREED_THRESHOLD:
        state, score = "Neutral", 3
    elif value <= 85:
        state, score = "Greed", 4
    else:
        state, score = "Extreme Greed", 5

    return {"fear_greed_score": score, "fear_greed_state": state}


def _score_crypto_risk(crypto: dict) -> dict:
    """
    [T1-2] BTC 24h 등락률 → 위험선호도 보조 시그널
    ─────────────────────────────────────────────────
    목적: BTC는 위험자산 선행지표로 작동하는 경우가 많다.
          BTC 급락 시 전통 자산 리스크오프 전이 가능성,
          BTC 급등 시 과열/투기 심리 경고로 활용한다.

    입력: crypto dict (collect_crypto_prices() 결과)
          - btc_change_pct: 24h 등락률 (%)

    출력: crypto_risk_score (1~4)
          - 1: BTC 안정 (|변화| < 3%)
          - 2: BTC 소폭 변동 (3~5%)
          - 3: BTC 급락 (<-5%) → 리스크오프 전이 경고
          - 4: BTC 급등 (>8%) → 과열/투기 경고
    """
    if not crypto:
        return {"crypto_risk_score": 1, "crypto_risk_state": "Unknown"}

    btc_chg = crypto.get("btc_change_pct", 0.0)

    if btc_chg <= BTC_RISK_DROP_THRESHOLD:
        # BTC 급락 → 위험자산 전반 리스크오프 전이 가능성
        state, score = "BTC Crash", 3
    elif btc_chg >= BTC_RISK_SURGE_THRESHOLD:
        # BTC 급등 → 투기 과열, 변동성 확대 경고
        state, score = "BTC Surge", 4
    elif abs(btc_chg) >= 3.0:
        # BTC 보통 변동
        state, score = "BTC Volatile", 2
    else:
        # BTC 안정
        state, score = "BTC Stable", 1

    return {"crypto_risk_score": score, "crypto_risk_state": state}


def _score_equity_momentum(snapshot: dict) -> dict:
    """
    [T1-3] S&P500 / NASDAQ 일간 등락률 → 모멘텀 시그널
    ──────────────────────────────────────────────────────
    목적: 현재 Growth Score는 VIX + 금리만으로 산출되어
          실제 시장 방향(상승/하락)이 반영되지 않는다.
          S&P500/NASDAQ 등락률을 직접 반영하여 Growth Score를 보강.

    입력: snapshot dict (collect_market_snapshot() 결과)
          - sp500: 일간 등락률 (%)
          - nasdaq: 일간 등락률 (%)

    출력: equity_momentum_score (1~5)
          - 1: 강한 상승 (>1.5%) → 성장 우호
          - 2: 소폭 상승 (0~1.5%)
          - 3: 보합 (-0.5~0%)
          - 4: 소폭 하락 (-2~-0.5%)
          - 5: 급락 (<-2%) → 성장 악화
    """
    sp500_chg = snapshot.get("sp500", 0.0) or 0.0
    nasdaq_chg = snapshot.get("nasdaq", 0.0) or 0.0

    # 두 지수 평균으로 전체 시장 방향 판단
    avg_chg = (sp500_chg + nasdaq_chg) / 2

    if avg_chg >= EQUITY_STRONG_MOVE_THRESHOLD:
        state, score = "Strong Rally", 1
    elif avg_chg >= 0.3:
        state, score = "Mild Rally", 2
    elif avg_chg >= -0.3:
        state, score = "Flat", 3
    elif avg_chg >= EQUITY_CRASH_THRESHOLD:
        state, score = "Mild Decline", 4
    else:
        state, score = "Sharp Decline", 5

    return {
        "equity_momentum_score": score,
        "equity_momentum_state": state,
        "equity_avg_change": round(avg_chg, 2),
    }


def _score_xlf_gld_relative(etf_prices: dict) -> dict:
    """
    [T1-4] XLF(금융) vs GLD(금) 상대강도 → 금융 안정 보조 시그널
    ─────────────────────────────────────────────────────────────
    목적: 현재 Financial Stability Score는 FRED HY스프레드 하나에만
          의존한다. XLF(금융 섹터)와 GLD(금) 상대강도를 추가하면
          시장 참가자의 실시간 안전자산 vs 위험자산 선호를 반영 가능.

    로직: XLF 등락률 - GLD 등락률
          - 양수 → 금융 강세, 위험자산 선호 → 안정
          - 음수 → 금 강세, 안전자산 선호 → 불안정

    입력: etf_prices dict (collect_etf_prices() 결과)
          - XLF: {"price": ..., "change_pct": ...}
          - GLD: {"price": ..., "change_pct": ...}

    출력: xlf_gld_score (1~3)
          - 1: XLF >> GLD → 금융 안정, Risk-On
          - 2: XLF ≈ GLD → 중립
          - 3: GLD >> XLF → 안전자산 선호, 불안정
    """
    if not etf_prices:
        return {"xlf_gld_score": 2, "xlf_gld_state": "Unknown"}

    xlf_data = etf_prices.get("XLF", {})
    gld_data = etf_prices.get("GLD", {})

    xlf_chg = xlf_data.get("change_pct", 0.0) or 0.0
    gld_chg = gld_data.get("change_pct", 0.0) or 0.0

    # XLF - GLD: 양수면 금융 강세, 음수면 금 강세
    spread = xlf_chg - gld_chg

    if spread >= XLF_GLD_RISK_ON_THRESHOLD:
        state, score = "Financial Risk-On", 1
    elif spread <= XLF_GLD_RISK_OFF_THRESHOLD:
        state, score = "Safe Haven Bid", 3
    else:
        state, score = "Neutral", 2

    return {
        "xlf_gld_score": score,
        "xlf_gld_state": state,
        "xlf_gld_spread": round(spread, 2),
    }


# ──────────────────────────────────────────────────────────────
# 2. Market Score  (6개 축)
# ──────────────────────────────────────────────────────────────

def compute_market_score(signals: dict) -> dict:
    """
    6개 축 Market Score 산출 — Tier 1 확장 반영 (2026-04-01)
    ──────────────────────────────────────────────────────────
    출력: 각 축 1~5 (5=위험/부담, 1=안정/우호)

    변경 이력:
      v1.0: VIX + 금리 + Oil + 감성 기반 단순 평균
      v1.1 (Tier 1): Fear&Greed, BTC, 주가모멘텀, XLF/GLD 반영
           - growth_score: 주가 모멘텀 직접 반영 (기존 VIX+금리만 → +모멘텀)
           - risk_score: Fear&Greed + BTC 변동 보조 반영
           - financial_stability: XLF/GLD 상대강도 보조 반영
    """
    # ── 기존 시그널 ──
    vix_score = signals.get("volatility_score", 2)
    rate_score = signals.get("rate_score", 2)
    oil_score = signals.get("commodity_pressure_score", 2)
    stability_score = signals.get("financial_stability_score", 2)
    sentiment_score = signals.get("sentiment_score", 2)

    # ── Tier 1 확장 시그널 (없으면 중립값 사용 → 기존 로직 보존) ──
    fear_greed_score = signals.get("fear_greed_score", 3)    # T1-1: 1~5
    crypto_risk_score = signals.get("crypto_risk_score", 1)  # T1-2: 1~4
    momentum_score = signals.get("equity_momentum_score", 3) # T1-3: 1~5
    xlf_gld_score = signals.get("xlf_gld_score", 2)          # T1-4: 1~3

    # ── growth_score: 성장 환경 ──
    # 기존: (VIX + 금리) / 2
    # 개선: 주가 모멘텀 30% 가중 반영
    #   - VIX 낮고 + 금리 낮고 + 실제 주가 상승 중 = 진짜 성장 우호
    #   - VIX 낮아도 주가 하락 중이면 성장 약화 반영
    growth_raw = (vix_score * 0.30 + rate_score * 0.30 + momentum_score * 0.40)
    growth_score = max(1, min(5, round(growth_raw)))

    # ── inflation_score: 원자재 + 금리 (기존 유지) ──
    inflation_score = max(1, min(5, round((oil_score + rate_score) / 2)))

    # ── liquidity_score: 신용 + 달러 (기존 유지) ──
    dollar_score = 2 if not signals.get("dollar_tightening_signal") else 3
    liquidity_score = max(1, min(5, round((stability_score + dollar_score) / 2)))

    # ── risk_score: 종합 위험도 ──
    # 기존: (VIX + 감성) / 2
    # 개선: Fear&Greed + BTC 변동 반영
    #   - Fear&Greed가 Extreme Fear이면 리스크 높음
    #   - BTC 급락이면 위험자산 전반 리스크오프 전이
    #   - 가중치: VIX 30% + 감성 25% + Fear&Greed 25% + BTC 20%
    #   주의: fear_greed_score는 높을수록 탐욕(과열)이므로
    #         공포(1) = 시장 위험, 탐욕(5) = 과열 위험 — 둘 다 risk 가중
    fg_risk = fear_greed_score if fear_greed_score <= 2 else (
        5 - fear_greed_score + 1 if fear_greed_score >= 4 else 2
    )
    # fg_risk 변환: Extreme Fear(1)→4, Fear(2)→3, Neutral(3)→2,
    #               Greed(4)→3, Extreme Greed(5)→4 — U자형 리스크 곡선
    fg_risk_map = {1: 4, 2: 3, 3: 2, 4: 3, 5: 4}
    fg_risk = fg_risk_map.get(fear_greed_score, 2)

    risk_raw = (
        vix_score * 0.30 +
        sentiment_score * 0.25 +
        fg_risk * 0.25 +
        crypto_risk_score * 0.20
    )
    risk_score = max(1, min(5, round(risk_raw)))

    # ── financial_stability_score: 금융 안정 ──
    # 기존: HY 스프레드 단독
    # 개선: XLF/GLD 상대강도 30% 반영
    #   - HY 스프레드 낮더라도 GLD가 급등(안전자산 선호)이면 불안정
    stability_raw = stability_score * 0.70 + xlf_gld_score * 0.30
    financial_stability = max(1, min(5, round(stability_raw)))

    score = {
        "growth_score": growth_score,
        "inflation_score": inflation_score,
        "liquidity_score": liquidity_score,
        "risk_score": risk_score,
        "financial_stability_score": financial_stability,
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
    fear_greed: dict = None,
    crypto: dict = None,
    etf_prices: dict = None,
) -> dict:
    """
    시장 스냅샷 + FRED + 뉴스 감성 + 확장 데이터 → 신호 + Market Score 반환.
    ─────────────────────────────────────────────────────────────────────────
    Tier 1 확장 (2026-04-01):
      - fear_greed: Fear & Greed Index → sentiment 보강 (T1-1)
      - crypto: BTC/ETH 가격 → risk appetite 보조 (T1-2)
      - snapshot.sp500/nasdaq: 이미 수집된 등락률 → 모멘텀 (T1-3)
      - etf_prices: XLF/GLD 상대강도 → 금융안정 보강 (T1-4)

    하위호환:
      fear_greed, crypto, etf_prices가 None이어도 기존 로직 정상 동작.
      각 Tier 1 시그널 함수는 None 입력 시 중립값을 반환하도록 설계.

    Args:
        snapshot: collect_market_snapshot() 결과
        fred_data: collect_macro_data() 결과
        news_sentiment: "Bullish" / "Neutral" / "Bearish"
        fear_greed: collect_fear_greed() 결과 (옵션)
        crypto: collect_crypto_prices() 결과 (옵션)
        etf_prices: collect_etf_prices() 결과 (옵션)

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

    # ── 기존 7개 시그널 산출 ──
    signals = {}
    signals.update(_score_vix(vix))
    signals.update(_score_us10y(us10y))
    signals.update(_score_oil(oil))
    signals.update(_score_dxy(dxy))
    signals.update(_score_credit(credit_stress))
    signals.update(_score_yield_curve(yield_curve_inverted))
    signals.update(_score_news_sentiment(news_sentiment))

    # ── Tier 1 확장 4개 시그널 산출 (2026-04-01 추가) ──
    # 각 함수는 데이터가 None이어도 안전하게 중립값 반환
    signals.update(_score_fear_greed(fear_greed))          # T1-1
    signals.update(_score_crypto_risk(crypto))             # T1-2
    signals.update(_score_equity_momentum(snapshot))        # T1-3
    signals.update(_score_xlf_gld_relative(etf_prices))    # T1-4

    # Market Score (Tier 1 확장 시그널 포함)
    market_score = compute_market_score(signals)

    # ── 로그 출력 (기존 + Tier 1 확장) ──
    logger.info(
        f"[MacroEngine] VIX={vix:.1f}({signals['vix_state']}) | "
        f"Rate={signals['rate_environment']} | "
        f"Oil={signals['oil_state']} | "
        f"Dollar={signals['dollar_state']}"
    )
    logger.info(
        f"[MacroEngine] Tier1: F&G={signals.get('fear_greed_state','N/A')} | "
        f"BTC={signals.get('crypto_risk_state','N/A')} | "
        f"Momentum={signals.get('equity_momentum_state','N/A')} | "
        f"XLF/GLD={signals.get('xlf_gld_state','N/A')}"
    )

    return {
        "signals": signals,
        "market_score": market_score,
    }
