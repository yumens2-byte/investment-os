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
    # ── Tier 2 확장 시그널 임계값 (2026-04-01 추가) ──
    BREADTH_HEALTHY_THRESHOLD, BREADTH_NARROW_THRESHOLD,
    VOL_TERM_BACKWARDATION, VOL_TERM_NORMAL,
    ICSA_LOW_THRESHOLD, ICSA_HIGH_THRESHOLD,
    INFLATION_EXP_LOW, INFLATION_EXP_HIGH,
    EM_STRESS_THRESHOLD,
    # ── Tier 3 확장 시그널 임계값 (2026-04-01 추가) ──
    AI_MOM_STRONG_THRESHOLD, AI_MOM_WEAK_THRESHOLD,
    NASDAQ_REL_GROWTH_THRESHOLD, NASDAQ_REL_VALUE_THRESHOLD,
    BANK_STRESS_THRESHOLD,
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
# 1-C. Tier 2 확장 시그널 (2026-04-01 추가)
#     새로운 데이터 소스를 추가 수집하여 분석 엔진에 연결.
#     yfinance: RSP, VIX3M, EEM / FRED: ICSA, T5YIFR
# ──────────────────────────────────────────────────────────────



# ──────────────────────────────────────────────────────────────
# 1-C. Tier 2 확장 시그널 (2026-04-01 추가)
#     새로운 데이터 소스를 추가 수집하여 분석 엔진에 연결.
#     yfinance: RSP, VIX3M, EEM / FRED: ICSA, T5YIFR
# ──────────────────────────────────────────────────────────────

def _score_market_breadth(tier2_data: dict, snapshot: dict) -> dict:
    """
    [T2-1] Market Breadth Signal — RSP/SPY 상대강도
    ─────────────────────────────────────────────────
    목적: S&P500 지수가 상승해도 소수 메가캡(AAPL, NVDA 등)이
          끌고 가는 것인지, 전체 시장이 참여하는 것인지를 구분.
          RSP(균등가중 ETF)와 SPY(시총가중 ETF) 등락률 차이로 판단.

    로직: breadth_spread = RSP등락률 - SPY등락률
          - 양수(RSP > SPY) → 중소형주도 참여, 건강한 랠리
          - 음수(RSP < SPY) → 대형주만 상승, 취약한 랠리
          - SP500 자체가 하락 중이면 breadth 의미 약화 → 보정

    입력: tier2_data (collect_tier2_market_data 결과)
          - rsp_change: RSP 일간 등락률 (%)
          - spy_change: SPY 일간 등락률 (%)

    출력: breadth_score (1~3)
          - 1: 건강한 참여 (Broad Rally)
          - 2: 중립
          - 3: 소수 종목 집중 (Narrow Rally) → Growth 약화 신호
    """
    if not tier2_data:
        return {"breadth_score": 2, "breadth_state": "Unknown", "breadth_spread": 0.0}

    rsp_chg = tier2_data.get("rsp_change")
    spy_chg = tier2_data.get("spy_change")

    # 둘 다 None이면 판단 불가
    if rsp_chg is None or spy_chg is None:
        return {"breadth_score": 2, "breadth_state": "No Data", "breadth_spread": 0.0}

    spread = rsp_chg - spy_chg

    if spread >= BREADTH_HEALTHY_THRESHOLD:
        state, score = "Broad Rally", 1
    elif spread <= BREADTH_NARROW_THRESHOLD:
        state, score = "Narrow Rally", 3
    else:
        state, score = "Neutral", 2

    return {
        "breadth_score": score,
        "breadth_state": state,
        "breadth_spread": round(spread, 2),
    }


def _score_vol_term_structure(snapshot: dict, tier2_data: dict) -> dict:
    """
    [T2-2] Volatility Term Structure Signal — VIX/VIX3M 비율
    ─────────────────────────────────────────────────────────
    목적: 현재 VIX만 사용하면 "공포가 일시적인지 구조적인지" 구분 불가.
          VIX(1개월) vs VIX3M(3개월) 비율을 보면:
            - VIX > VIX3M (백워데이션): 단기 패닉, 시장 급변
            - VIX < VIX3M (컨탱고): 정상 상태, 안정

    로직: ratio = VIX / VIX3M
          - ratio >= 1.0 → 백워데이션 = 구조적 위기 의심
          - ratio < 0.85 → 정상 컨탱고 = 안정
          - 그 사이 → 경계

    입력: snapshot (VIX 가격), tier2_data (VIX3M 가격)

    출력: vol_term_score (1~3)
          - 1: 정상 (컨탱고)
          - 2: 경계
          - 3: 백워데이션 (구조적 위기) → Risk 대폭 가중
    """
    vix = snapshot.get("vix")
    vix3m = tier2_data.get("vix3m") if tier2_data else None

    if vix is None or vix3m is None or vix3m == 0:
        return {"vol_term_score": 2, "vol_term_state": "No Data", "vol_term_ratio": None}

    ratio = vix / vix3m

    if ratio >= VOL_TERM_BACKWARDATION:
        # 백워데이션: 단기 VIX가 장기보다 높음 → 급격한 공포
        state, score = "Backwardation", 3
    elif ratio < VOL_TERM_NORMAL:
        # 정상 컨탱고: 시장 안정
        state, score = "Contango", 1
    else:
        state, score = "Flat", 2

    return {
        "vol_term_score": score,
        "vol_term_state": state,
        "vol_term_ratio": round(ratio, 3),
    }


def _score_initial_claims(fred_data: dict) -> dict:
    """
    [T2-3] Initial Jobless Claims Signal — 실업수당 청구
    ────────────────────────────────────────────────────
    목적: 실물경제 선행지표 중 가장 실시간성이 높은 데이터.
          주간 업데이트되므로 월간 지표(CPI, GDP)보다 빠르게
          노동시장 악화를 감지 가능.

    로직: ICSA (천 명 단위)
          - < 220K → 노동시장 매우 강함 → 성장 우호
          - 220~300K → 정상 범위
          - > 300K → 노동시장 악화 → Recession 경고

    입력: fred_data (collect_macro_data 결과)
          - initial_claims: 천 명 단위 (예: 225.0 = 225,000명)

    출력: claims_score (1~3)
          - 1: 노동시장 강함
          - 2: 정상
          - 3: 노동시장 악화 → Growth 약화 + Recession 가중
    """
    claims = fred_data.get("initial_claims") if fred_data else None

    if claims is None:
        return {"claims_score": 2, "claims_state": "No Data", "claims_value": None}

    if claims < ICSA_LOW_THRESHOLD:
        state, score = "Strong Labor", 1
    elif claims > ICSA_HIGH_THRESHOLD:
        state, score = "Weak Labor", 3
    else:
        state, score = "Normal", 2

    return {
        "claims_score": score,
        "claims_state": state,
        "claims_value": round(claims, 1),
    }


def _score_inflation_expectation(fred_data: dict) -> dict:
    """
    [T2-4] Inflation Expectation Signal — 5Y Breakeven
    ───────────────────────────────────────────────────
    목적: 현재 Inflation Score는 유가 + 금리만 반영.
          시장 참가자의 실제 인플레이션 기대(5년 Breakeven)를
          추가하면 인플레이션 국면 판단이 정밀해짐.

    로직: T5YIFR (%)
          - < 2.0% → 디스인플레이션, 성장 둔화 가능성
          - 2.0~2.8% → Fed 목표 부근, 정상
          - > 2.8% → 인플레이션 우려 → Inflation Score 가중

    입력: fred_data (collect_macro_data 결과)
          - inflation_exp: % (예: 2.35)

    출력: infl_exp_score (1~3)
          - 1: 디스인플레이션 (2% 미만)
          - 2: 정상 (2~2.8%)
          - 3: 인플레이션 우려 (2.8% 초과)
    """
    infl = fred_data.get("inflation_exp") if fred_data else None

    if infl is None:
        return {"infl_exp_score": 2, "infl_exp_state": "No Data", "infl_exp_value": None}

    if infl < INFLATION_EXP_LOW:
        state, score = "Disinflation", 1
    elif infl > INFLATION_EXP_HIGH:
        state, score = "Inflation Concern", 3
    else:
        state, score = "Normal", 2

    return {
        "infl_exp_score": score,
        "infl_exp_state": state,
        "infl_exp_value": round(infl, 2),
    }


def _score_em_stress(tier2_data: dict, snapshot: dict) -> dict:
    """
    [T2-5] Emerging Market Stress Signal — EEM 등락률
    ──────────────────────────────────────────────────
    목적: 신흥국 시장 급락 + 달러 강세가 동시 발생하면
          글로벌 리스크오프가 선진국으로 전이되는 패턴이 반복됨.
          1997 아시아 위기, 2013 Taper Tantrum, 2018 터키 리라 등.

    로직: EEM 등락률
          - EEM < -2% → 신흥국 스트레스
          - EEM < -2% AND DXY 강세 → 위기 전이 위험 (score 상향)
          - EEM 정상 → 글로벌 리스크 안정

    입력: tier2_data (EEM 등락률), snapshot (DXY 가격)

    출력: em_stress_score (1~4)
          - 1: 안정
          - 2: 소폭 약세
          - 3: 스트레스 (EEM 급락)
          - 4: 위기 전이 위험 (EEM 급락 + 달러 강세)
    """
    eem_chg = tier2_data.get("eem_change") if tier2_data else None

    if eem_chg is None:
        return {"em_stress_score": 1, "em_stress_state": "No Data"}

    dxy = snapshot.get("dollar_index", 100.0)
    # DXY 104 이상 = 달러 강세 (settings의 DXY_HIGH_THRESHOLD 참조)
    dollar_strong = dxy is not None and dxy >= 104.0

    if eem_chg <= EM_STRESS_THRESHOLD:
        if dollar_strong:
            # 신흥국 급락 + 달러 강세 동시 → 위기 전이 위험
            state, score = "EM Crisis Spillover", 4
        else:
            state, score = "EM Stress", 3
    elif eem_chg <= -1.0:
        state, score = "EM Mild Weakness", 2
    else:
        state, score = "EM Stable", 1

    return {
        "em_stress_score": score,
        "em_stress_state": state,
    }


# ──────────────────────────────────────────────────────────────
# 1-D. Tier 3 확장 시그널 (2026-04-01 추가)
#     노션 설계서(v2.1)에 정의되었으나 미구현이었던 시그널 보완.
#     ai_momentum / nasdaq_relative / banking_stress
# ──────────────────────────────────────────────────────────────

def _score_ai_momentum(tier2_data: dict) -> dict:
    """
    [T3-1] AI Momentum Signal — SOXX vs QQQ 상대강도
    ─────────────────────────────────────────────────
    목적: 설계서 ai_momentum_signal 구현.
          AI/반도체 섹터가 Tech 전체(QQQ) 대비 강한지 약한지로
          AI 테마 리더십 지속 여부를 판단.
          SOXX가 QQQ보다 강하면 AI 투자 모멘텀 건재,
          약하면 AI 테마 둔화 또는 로테이션 발생.

    로직: spread = SOXX등락률 - QQQ등락률
          - spread > 0.5% → AI 리더십 강함 (Growth 강화)
          - spread < -1.0% → AI 둔화 (Growth 약화 경고)
          - 그 사이 → 중립

    입력: tier2_data (collect_tier2_market_data 결과)
          - soxx_change: SOXX 일간 등락률 (%)
          - qqq_change: QQQ 일간 등락률 (%)

    출력: ai_momentum_score (1~3)
          - 1: AI 리더십 강함 → Growth 보강
          - 2: 중립
          - 3: AI 둔화 → Growth 약화 신호
    """
    if not tier2_data:
        return {"ai_momentum_score": 2, "ai_momentum_state": "No Data", "ai_momentum_spread": 0.0}

    soxx_chg = tier2_data.get("soxx_change")
    qqq_chg = tier2_data.get("qqq_change")

    if soxx_chg is None or qqq_chg is None:
        return {"ai_momentum_score": 2, "ai_momentum_state": "No Data", "ai_momentum_spread": 0.0}

    spread = soxx_chg - qqq_chg

    if spread >= AI_MOM_STRONG_THRESHOLD:
        state, score = "AI Leadership", 1
    elif spread <= AI_MOM_WEAK_THRESHOLD:
        state, score = "AI Slowdown", 3
    else:
        state, score = "Neutral", 2

    return {
        "ai_momentum_score": score,
        "ai_momentum_state": state,
        "ai_momentum_spread": round(spread, 2),
    }


def _score_nasdaq_relative(snapshot: dict) -> dict:
    """
    [T3-2] Nasdaq Relative Signal — NASDAQ vs SP500 상대수익
    ────────────────────────────────────────────────────────
    목적: 설계서 nasdaq_relative_signal 구현.
          NASDAQ이 SP500보다 강하면 Growth/Tech 주도 시장,
          약하면 방어주/가치주 선호(Rotation) 시장.
          Regime 판단의 보조 지표로 활용.

    로직: spread = NASDAQ등락률 - SP500등락률
          - spread > 0.5% → Growth 주도 (Tech Outperform)
          - spread < -0.5% → Value 선호 (Defensive Rotation)
          - 그 사이 → 균형

    입력: snapshot (collect_market_snapshot 결과)
          - nasdaq: NASDAQ 일간 등락률 (%)
          - sp500: SP500 일간 등락률 (%)

    출력: nasdaq_rel_score (1~3)
          - 1: Growth/Tech 주도
          - 2: 균형
          - 3: Value/방어주 선호 → Growth 약화
    """
    sp500_chg = snapshot.get("sp500", 0.0) or 0.0
    nasdaq_chg = snapshot.get("nasdaq", 0.0) or 0.0

    spread = nasdaq_chg - sp500_chg

    if spread >= NASDAQ_REL_GROWTH_THRESHOLD:
        state, score = "Growth Leadership", 1
    elif spread <= NASDAQ_REL_VALUE_THRESHOLD:
        state, score = "Value Rotation", 3
    else:
        state, score = "Balanced", 2

    return {
        "nasdaq_rel_score": score,
        "nasdaq_rel_state": state,
        "nasdaq_rel_spread": round(spread, 2),
    }


def _score_banking_stress(tier2_data: dict, etf_prices: dict) -> dict:
    """
    [T3-3] Banking Stress Signal — KRE vs XLF 상대강도
    ───────────────────────────────────────────────────
    목적: 설계서 banking_stress_signal 구현.
          2023 SVB/시그니처뱅크 사태에서 KRE(지역은행)가
          XLF(금융 전체) 대비 대폭 하락하며 금융 시스템 스트레스를
          선행 감지한 패턴을 자동화.

    로직: spread = KRE등락률 - XLF등락률
          - spread < -1.5% → 지역은행 스트레스 (SVB 패턴)
          - 그 외 → 안정

    입력:
          - tier2_data.kre_change: KRE 일간 등락률 (%)
          - etf_prices.XLF.change_pct: XLF 일간 등락률 (%)

    출력: banking_stress_score (1~3)
          - 1: 안정
          - 2: 소폭 약세
          - 3: 은행 스트레스 → Financial Stability 약화
    """
    kre_chg = tier2_data.get("kre_change") if tier2_data else None

    # XLF는 etf_prices에서 가져옴 (이미 수집 중)
    xlf_chg = None
    if etf_prices:
        xlf_data = etf_prices.get("XLF", {})
        xlf_chg = xlf_data.get("change_pct", 0.0)

    if kre_chg is None or xlf_chg is None:
        return {"banking_stress_score": 1, "banking_stress_state": "No Data", "banking_stress_spread": 0.0}

    spread = kre_chg - xlf_chg

    if spread <= BANK_STRESS_THRESHOLD:
        # KRE가 XLF 대비 대폭 하락 → SVB 패턴, 은행 시스템 스트레스
        state, score = "Bank Stress", 3
    elif spread <= -0.5:
        state, score = "Bank Mild Weakness", 2
    else:
        state, score = "Bank Stable", 1

    return {
        "banking_stress_score": score,
        "banking_stress_state": state,
        "banking_stress_spread": round(spread, 2),
    }


# ──────────────────────────────────────────────────────────────
# 2. Market Score  (6개 축)
# ──────────────────────────────────────────────────────────────

def compute_market_score(signals: dict) -> dict:
    """
    6개 축 Market Score 산출 — Tier 1+2+3 확장 반영 (2026-04-01)
    ──────────────────────────────────────────────────────────
    출력: 각 축 1~5 (5=위험/부담, 1=안정/우호)

    변경 이력:
      v1.0: VIX + 금리 + Oil + 감성 기반 단순 평균
      v1.1 (Tier 1): Fear&Greed, BTC, 주가모멘텀, XLF/GLD 반영
      v1.2 (Tier 2): Market Breadth, Vol Term, 실업수당, 기대인플레, EM Stress
      v1.3 (Tier 3): AI Momentum, Nasdaq Relative, Banking Stress
           - growth_score: + AI모멘텀 + Nasdaq상대강도
           - financial_stability: + Banking Stress(KRE/XLF)
    """
    # ── 기존 시그널 ──
    vix_score = signals.get("volatility_score", 2)
    rate_score = signals.get("rate_score", 2)
    oil_score = signals.get("commodity_pressure_score", 2)
    stability_score = signals.get("financial_stability_score", 2)
    sentiment_score = signals.get("sentiment_score", 2)

    # ── Tier 1 확장 시그널 ──
    fear_greed_score = signals.get("fear_greed_score", 3)
    crypto_risk_score = signals.get("crypto_risk_score", 1)
    momentum_score = signals.get("equity_momentum_score", 3)
    xlf_gld_score = signals.get("xlf_gld_score", 2)

    # ── Tier 2 확장 시그널 (없으면 중립값 → 기존 로직 보존) ──
    breadth_score = signals.get("breadth_score", 2)        # T2-1: 1~3
    vol_term_score = signals.get("vol_term_score", 2)      # T2-2: 1~3
    claims_score = signals.get("claims_score", 2)          # T2-3: 1~3
    infl_exp_score = signals.get("infl_exp_score", 2)      # T2-4: 1~3
    em_stress_score = signals.get("em_stress_score", 1)    # T2-5: 1~4

    # ── Tier 3 확장 시그널 (없으면 중립값 → 기존 로직 보존) ──
    ai_momentum_score = signals.get("ai_momentum_score", 2)    # T3-1: 1~3
    nasdaq_rel_score = signals.get("nasdaq_rel_score", 2)      # T3-2: 1~3
    banking_stress_score = signals.get("banking_stress_score", 1) # T3-3: 1~3

    # ═══════════════════════════════════════════════════════
    # growth_score: 성장 환경
    # ═══════════════════════════════════════════════════════
    # v1.2: VIX 20% + 금리 20% + 모멘텀 30% + Breadth 15% + 실업수당 15%
    # v1.3: VIX 15% + 금리 15% + 모멘텀 20% + Breadth 10% + 실업수당 10%
    #       + AI모멘텀 15% + Nasdaq상대 15%
    #   - AI모멘텀: SOXX/QQQ 상대강도 (AI 테마 리더십 지속 여부)
    #   - Nasdaq상대: Growth vs Value 로테이션 방향
    growth_raw = (
        vix_score * 0.15 +
        rate_score * 0.15 +
        momentum_score * 0.20 +
        breadth_score * 0.10 +
        claims_score * 0.10 +
        ai_momentum_score * 0.15 +
        nasdaq_rel_score * 0.15
    )
    growth_score = max(1, min(5, round(growth_raw)))

    # ═══════════════════════════════════════════════════════
    # inflation_score: 인플레이션 환경
    # ═══════════════════════════════════════════════════════
    # v1.1: (Oil + 금리) / 2
    # v1.2: Oil 35% + 금리 30% + 기대인플레 35%
    #   - 기대인플레: 시장 참가자의 실제 인플레이션 전망 반영
    inflation_raw = (
        oil_score * 0.35 +
        rate_score * 0.30 +
        infl_exp_score * 0.35
    )
    inflation_score = max(1, min(5, round(inflation_raw)))

    # ═══════════════════════════════════════════════════════
    # liquidity_score: 유동성 환경
    # ═══════════════════════════════════════════════════════
    # v1.1: (stability + dollar) / 2
    # v1.2: stability 30% + dollar 30% + EM Stress 40%
    #   - EM Stress: 신흥국 급락+달러강세 → 글로벌 유동성 경색 반영
    dollar_score = 2 if not signals.get("dollar_tightening_signal") else 3
    liquidity_raw = (
        stability_score * 0.30 +
        dollar_score * 0.30 +
        em_stress_score * 0.40
    )
    liquidity_score = max(1, min(5, round(liquidity_raw)))

    # ═══════════════════════════════════════════════════════
    # risk_score: 종합 위험도
    # ═══════════════════════════════════════════════════════
    # v1.1: VIX 30% + 감성 25% + F&G 25% + BTC 20%
    # v1.2: VIX 20% + 감성 15% + F&G 20% + BTC 15% + Vol Term 30%
    #   - Vol Term: 백워데이션이면 구조적 위기 → Risk 대폭 가중
    fg_risk_map = {1: 4, 2: 3, 3: 2, 4: 3, 5: 4}
    fg_risk = fg_risk_map.get(fear_greed_score, 2)

    risk_raw = (
        vix_score * 0.20 +
        sentiment_score * 0.15 +
        fg_risk * 0.20 +
        crypto_risk_score * 0.15 +
        vol_term_score * 0.30
    )
    risk_score = max(1, min(5, round(risk_raw)))

    # ═══════════════════════════════════════════════════════
    # financial_stability_score: 금융 안정
    # ═══════════════════════════════════════════════════════
    # v1.1: HY 70% + XLF/GLD 30%
    # v1.3: HY 50% + XLF/GLD 20% + Banking Stress 30%
    #   - Banking Stress: KRE/XLF 상대강도 (SVB 패턴 선행 감지)
    stability_raw = (
        stability_score * 0.50 +
        xlf_gld_score * 0.20 +
        banking_stress_score * 0.30
    )
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
    tier2_data: dict = None,
) -> dict:
    """
    시장 스냅샷 + FRED + 뉴스 감성 + 확장 데이터 → 신호 + Market Score 반환.
    ─────────────────────────────────────────────────────────────────────────
    Tier 1 확장 (2026-04-01):
      - fear_greed: Fear & Greed Index → sentiment 보강 (T1-1)
      - crypto: BTC/ETH 가격 → risk appetite 보조 (T1-2)
      - snapshot.sp500/nasdaq: 이미 수집된 등락률 → 모멘텀 (T1-3)
      - etf_prices: XLF/GLD 상대강도 → 금융안정 보강 (T1-4)

    Tier 2 확장 (2026-04-01):
      - tier2_data.rsp/spy: Market Breadth → Growth 참여도 (T2-1)
      - tier2_data.vix3m: Vol Term Structure → Risk 위기구조 (T2-2)
      - fred_data.initial_claims: 실업수당 → Growth 실물경제 (T2-3)
      - fred_data.inflation_exp: 기대인플레 → Inflation 정밀화 (T2-4)
      - tier2_data.eem: EM Stress → Liquidity 글로벌 전이 (T2-5)

    하위호환:
      모든 확장 파라미터가 None이어도 기존 로직 정상 동작.

    Args:
        snapshot: collect_market_snapshot() 결과
        fred_data: collect_macro_data() 결과
        news_sentiment: "Bullish" / "Neutral" / "Bearish"
        fear_greed: collect_fear_greed() 결과 (옵션)
        crypto: collect_crypto_prices() 결과 (옵션)
        etf_prices: collect_etf_prices() 결과 (옵션)
        tier2_data: collect_tier2_market_data() 결과 (옵션)

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
    signals.update(_score_fear_greed(fear_greed))          # T1-1
    signals.update(_score_crypto_risk(crypto))             # T1-2
    signals.update(_score_equity_momentum(snapshot))        # T1-3
    signals.update(_score_xlf_gld_relative(etf_prices))    # T1-4

    # ── Tier 2 확장 5개 시그널 산출 (2026-04-01 추가) ──
    # 각 함수는 데이터가 None이어도 안전하게 중립값 반환
    signals.update(_score_market_breadth(tier2_data, snapshot))        # T2-1
    signals.update(_score_vol_term_structure(snapshot, tier2_data))    # T2-2
    signals.update(_score_initial_claims(fred_data))                   # T2-3
    signals.update(_score_inflation_expectation(fred_data))            # T2-4
    signals.update(_score_em_stress(tier2_data, snapshot))             # T2-5

    # ── Tier 3 확장 3개 시그널 산출 (2026-04-01 추가) ──
    # 설계서 v2.1에 정의되었으나 미구현이었던 시그널 보완
    signals.update(_score_ai_momentum(tier2_data))                     # T3-1
    signals.update(_score_nasdaq_relative(snapshot))                   # T3-2
    signals.update(_score_banking_stress(tier2_data, etf_prices))      # T3-3

    # Market Score (Tier 1 + 2 확장 시그널 포함)
    market_score = compute_market_score(signals)

    # ── 로그 출력 ──
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
    logger.info(
        f"[MacroEngine] Tier2: Breadth={signals.get('breadth_state','N/A')} | "
        f"VolTerm={signals.get('vol_term_state','N/A')} | "
        f"Claims={signals.get('claims_state','N/A')} | "
        f"InflExp={signals.get('infl_exp_state','N/A')} | "
        f"EM={signals.get('em_stress_state','N/A')}"
    )
    logger.info(
        f"[MacroEngine] Tier3: AI={signals.get('ai_momentum_state','N/A')} | "
        f"NasRel={signals.get('nasdaq_rel_state','N/A')} | "
        f"Bank={signals.get('banking_stress_state','N/A')}"
    )

    return {
        "signals": signals,
        "market_score": market_score,
    }
