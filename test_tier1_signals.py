"""
test_tier1_signals.py — Tier 1 시그널 확장 전수 테스트
=====================================================
목적: macro_engine Tier 1 확장 시그널 4개 + Market Score 보강 로직 검증
실행: python test_tier1_signals.py
결과: 전체 PASS 시만 운영 배포 가능

테스트 항목:
  Round 1: 개별 시그널 함수 단위 테스트 (정상/경계/이상값)
  Round 2: 통합 파이프라인 테스트 (end-to-end + 정합성)
"""
import sys
import json
import traceback

# ─── 테스트 카운터 ──────────────────────────────────────────
_total = 0
_passed = 0
_failed = 0
_fail_details = []


def _assert(test_name: str, condition: bool, detail: str = ""):
    global _total, _passed, _failed
    _total += 1
    if condition:
        _passed += 1
        print(f"  ✅ {test_name}")
    else:
        _failed += 1
        msg = f"  ❌ {test_name} — {detail}"
        print(msg)
        _fail_details.append(msg)


def _assert_range(test_name: str, value, lo, hi):
    """값이 lo~hi 범위 내에 있는지 확인"""
    _assert(
        test_name,
        isinstance(value, (int, float)) and lo <= value <= hi,
        f"값={value}, 기대 범위={lo}~{hi}"
    )


# ═══════════════════════════════════════════════════════════════
# Round 1: 개별 시그널 함수 단위 테스트
# ═══════════════════════════════════════════════════════════════

def round1_unit_tests():
    print("\n" + "=" * 60)
    print("Round 1: 개별 시그널 함수 단위 테스트")
    print("=" * 60)

    from engines.macro_engine import (
        _score_fear_greed,
        _score_crypto_risk,
        _score_equity_momentum,
        _score_xlf_gld_relative,
        _score_vix,
        _score_us10y,
        _score_oil,
        _score_dxy,
        _score_credit,
        _score_yield_curve,
        _score_news_sentiment,
        compute_market_score,
    )

    # ──────────────────────────────────────────────────────
    # T1-1: Fear & Greed Signal
    # ──────────────────────────────────────────────────────
    print("\n[T1-1] Fear & Greed Signal")

    # 정상 케이스
    r = _score_fear_greed({"value": 10, "label": "Extreme Fear"})
    _assert("F&G Extreme Fear (10)", r["fear_greed_score"] == 1)
    _assert("F&G Extreme Fear state", r["fear_greed_state"] == "Extreme Fear")

    r = _score_fear_greed({"value": 25})
    _assert("F&G Fear (25)", r["fear_greed_score"] == 2)

    r = _score_fear_greed({"value": 50})
    _assert("F&G Neutral (50)", r["fear_greed_score"] == 3)

    r = _score_fear_greed({"value": 75})
    _assert("F&G Greed (75)", r["fear_greed_score"] == 4)

    r = _score_fear_greed({"value": 90})
    _assert("F&G Extreme Greed (90)", r["fear_greed_score"] == 5)

    # 경계값
    r = _score_fear_greed({"value": 20})
    _assert("F&G 경계 (20=Extreme Fear)", r["fear_greed_score"] == 1)

    r = _score_fear_greed({"value": 21})
    _assert("F&G 경계 (21=Fear)", r["fear_greed_score"] == 2)

    r = _score_fear_greed({"value": 30})
    _assert("F&G 경계 (30=Fear)", r["fear_greed_score"] == 2)

    r = _score_fear_greed({"value": 31})
    _assert("F&G 경계 (31=Neutral)", r["fear_greed_score"] == 3)

    r = _score_fear_greed({"value": 70})
    _assert("F&G 경계 (70=Neutral)", r["fear_greed_score"] == 3)

    r = _score_fear_greed({"value": 71})
    _assert("F&G 경계 (71=Greed)", r["fear_greed_score"] == 4)

    r = _score_fear_greed({"value": 85})
    _assert("F&G 경계 (85=Greed)", r["fear_greed_score"] == 4)

    r = _score_fear_greed({"value": 86})
    _assert("F&G 경계 (86=Extreme Greed)", r["fear_greed_score"] == 5)

    # 이상값 / None 입력
    r = _score_fear_greed(None)
    _assert("F&G None 입력 → 중립", r["fear_greed_score"] == 3)
    _assert("F&G None state = Unknown", r["fear_greed_state"] == "Unknown")

    r = _score_fear_greed({})
    _assert("F&G 빈 dict → 중립 (value=50 default)", r["fear_greed_score"] == 3)

    r = _score_fear_greed({"value": 0})
    _assert("F&G 최소값 (0)", r["fear_greed_score"] == 1)
    _assert_range("F&G score 범위 검증 (0)", r["fear_greed_score"], 1, 5)

    r = _score_fear_greed({"value": 100})
    _assert("F&G 최대값 (100)", r["fear_greed_score"] == 5)
    _assert_range("F&G score 범위 검증 (100)", r["fear_greed_score"], 1, 5)

    # ──────────────────────────────────────────────────────
    # T1-2: Crypto Risk Signal
    # ──────────────────────────────────────────────────────
    print("\n[T1-2] Crypto Risk Signal")

    r = _score_crypto_risk({"btc_change_pct": 0.5})
    _assert("BTC Stable (0.5%)", r["crypto_risk_score"] == 1)

    r = _score_crypto_risk({"btc_change_pct": -3.5})
    _assert("BTC Volatile (-3.5%)", r["crypto_risk_score"] == 2)

    r = _score_crypto_risk({"btc_change_pct": 4.0})
    _assert("BTC Volatile (+4%)", r["crypto_risk_score"] == 2)

    r = _score_crypto_risk({"btc_change_pct": -7.0})
    _assert("BTC Crash (-7%)", r["crypto_risk_score"] == 3)

    r = _score_crypto_risk({"btc_change_pct": -5.0})
    _assert("BTC 경계 (-5% = Crash)", r["crypto_risk_score"] == 3)

    r = _score_crypto_risk({"btc_change_pct": 10.0})
    _assert("BTC Surge (+10%)", r["crypto_risk_score"] == 4)

    r = _score_crypto_risk({"btc_change_pct": 8.0})
    _assert("BTC 경계 (+8% = Surge)", r["crypto_risk_score"] == 4)

    # 이상값
    r = _score_crypto_risk(None)
    _assert("BTC None → 안정", r["crypto_risk_score"] == 1)

    r = _score_crypto_risk({})
    _assert("BTC 빈 dict → 안정", r["crypto_risk_score"] == 1)

    r = _score_crypto_risk({"btc_change_pct": -50.0})
    _assert("BTC 극단 급락 (-50%)", r["crypto_risk_score"] == 3)
    _assert_range("BTC score 범위 검증 (-50%)", r["crypto_risk_score"], 1, 4)

    r = _score_crypto_risk({"btc_change_pct": 100.0})
    _assert("BTC 극단 급등 (100%)", r["crypto_risk_score"] == 4)
    _assert_range("BTC score 범위 검증 (100%)", r["crypto_risk_score"], 1, 4)

    # ──────────────────────────────────────────────────────
    # T1-3: Equity Momentum Signal
    # ──────────────────────────────────────────────────────
    print("\n[T1-3] Equity Momentum Signal")

    r = _score_equity_momentum({"sp500": 2.0, "nasdaq": 2.5})
    _assert("강한 상승 (avg=2.25%)", r["equity_momentum_score"] == 1)
    _assert("강한 상승 state", r["equity_momentum_state"] == "Strong Rally")

    r = _score_equity_momentum({"sp500": 0.5, "nasdaq": 0.8})
    _assert("소폭 상승 (avg=0.65%)", r["equity_momentum_score"] == 2)

    r = _score_equity_momentum({"sp500": 0.1, "nasdaq": -0.1})
    _assert("보합 (avg=0.0%)", r["equity_momentum_score"] == 3)

    r = _score_equity_momentum({"sp500": -1.0, "nasdaq": -0.8})
    _assert("소폭 하락 (avg=-0.9%)", r["equity_momentum_score"] == 4)

    r = _score_equity_momentum({"sp500": -3.0, "nasdaq": -4.0})
    _assert("급락 (avg=-3.5%)", r["equity_momentum_score"] == 5)
    _assert("급락 state", r["equity_momentum_state"] == "Sharp Decline")

    # None 처리
    r = _score_equity_momentum({"sp500": None, "nasdaq": None})
    _assert("sp500/nasdaq None → 보합", r["equity_momentum_score"] == 3)

    r = _score_equity_momentum({})
    _assert("빈 snapshot → 보합", r["equity_momentum_score"] == 3)

    # 극단값 범위 확인
    r = _score_equity_momentum({"sp500": -10.0, "nasdaq": -12.0})
    _assert_range("극단 급락 score 범위", r["equity_momentum_score"], 1, 5)

    r = _score_equity_momentum({"sp500": 10.0, "nasdaq": 15.0})
    _assert_range("극단 급등 score 범위", r["equity_momentum_score"], 1, 5)

    # ──────────────────────────────────────────────────────
    # T1-4: XLF/GLD Relative Signal
    # ──────────────────────────────────────────────────────
    print("\n[T1-4] XLF/GLD Relative Signal")

    etf_risk_on = {"XLF": {"change_pct": 2.0}, "GLD": {"change_pct": 0.5}}
    r = _score_xlf_gld_relative(etf_risk_on)
    _assert("XLF>>GLD Risk-On (spread=1.5)", r["xlf_gld_score"] == 1)
    _assert("XLF>>GLD state", r["xlf_gld_state"] == "Financial Risk-On")

    etf_neutral = {"XLF": {"change_pct": 0.5}, "GLD": {"change_pct": 0.3}}
    r = _score_xlf_gld_relative(etf_neutral)
    _assert("XLF≈GLD Neutral (spread=0.2)", r["xlf_gld_score"] == 2)

    etf_risk_off = {"XLF": {"change_pct": -1.0}, "GLD": {"change_pct": 1.5}}
    r = _score_xlf_gld_relative(etf_risk_off)
    _assert("GLD>>XLF Safe Haven (spread=-2.5)", r["xlf_gld_score"] == 3)

    # 경계값
    etf_boundary = {"XLF": {"change_pct": 1.0}, "GLD": {"change_pct": 0.0}}
    r = _score_xlf_gld_relative(etf_boundary)
    _assert("경계 (spread=1.0 = Risk-On)", r["xlf_gld_score"] == 1)

    etf_boundary2 = {"XLF": {"change_pct": 0.0}, "GLD": {"change_pct": 1.0}}
    r = _score_xlf_gld_relative(etf_boundary2)
    _assert("경계 (spread=-1.0 = Safe Haven)", r["xlf_gld_score"] == 3)

    # None / 빈 dict
    r = _score_xlf_gld_relative(None)
    _assert("None → 중립", r["xlf_gld_score"] == 2)

    r = _score_xlf_gld_relative({})
    _assert("빈 dict → 중립", r["xlf_gld_score"] == 2)

    r = _score_xlf_gld_relative({"XLF": {}, "GLD": {}})
    _assert("change_pct 없는 dict → 중립", r["xlf_gld_score"] == 2)

    _assert_range("XLF/GLD score 범위", r["xlf_gld_score"], 1, 3)

    # ──────────────────────────────────────────────────────
    # 기존 시그널 하위호환 확인
    # ──────────────────────────────────────────────────────
    print("\n[기존 시그널 하위호환]")

    r = _score_vix(25.0)
    _assert("기존 VIX 정상", r["volatility_score"] == 3)

    r = _score_us10y(4.2)
    _assert("기존 US10Y 정상", r["rate_score"] == 2)

    r = _score_oil(85.0)
    _assert("기존 OIL 정상", r["commodity_pressure_score"] == 2)

    r = _score_dxy(105.0)
    _assert("기존 DXY 정상", r["dollar_tightening_signal"] == True)

    r = _score_credit("High")
    _assert("기존 Credit 정상", r["financial_stability_score"] == 3)

    r = _score_yield_curve(True)
    _assert("기존 YieldCurve 정상", r["recession_signal"] == True)

    r = _score_news_sentiment("Bearish")
    _assert("기존 Sentiment 정상", r["sentiment_score"] == 3)

    # ──────────────────────────────────────────────────────
    # Market Score 통합 산출 테스트
    # ──────────────────────────────────────────────────────
    print("\n[Market Score 통합 산출]")

    # Case A: 모든 Tier 1 데이터 있는 경우
    signals_full = {
        "volatility_score": 3,
        "rate_score": 2,
        "commodity_pressure_score": 2,
        "financial_stability_score": 2,
        "sentiment_score": 2,
        "dollar_tightening_signal": False,
        "fear_greed_score": 3,      # Neutral
        "crypto_risk_score": 1,     # Stable
        "equity_momentum_score": 2, # Mild Rally
        "xlf_gld_score": 2,         # Neutral
    }
    ms = compute_market_score(signals_full)
    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"Score 범위 ({key})", ms[key], 1, 5)

    # Case B: Tier 1 데이터 전혀 없는 경우 (하위호환)
    signals_legacy = {
        "volatility_score": 2,
        "rate_score": 2,
        "commodity_pressure_score": 2,
        "financial_stability_score": 2,
        "sentiment_score": 2,
        "dollar_tightening_signal": False,
    }
    ms_legacy = compute_market_score(signals_legacy)
    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"Legacy 범위 ({key})", ms_legacy[key], 1, 5)

    # Case C: 극단적 위험 시나리오
    signals_crisis = {
        "volatility_score": 5,      # VIX 극단
        "rate_score": 4,            # 고금리
        "commodity_pressure_score": 4,
        "financial_stability_score": 3,
        "sentiment_score": 3,       # Bearish
        "dollar_tightening_signal": True,
        "fear_greed_score": 1,      # Extreme Fear
        "crypto_risk_score": 3,     # BTC Crash
        "equity_momentum_score": 5, # Sharp Decline
        "xlf_gld_score": 3,         # Safe Haven
    }
    ms_crisis = compute_market_score(signals_crisis)
    _assert("위기 시 growth_score 높음 (4~5)", ms_crisis["growth_score"] >= 4,
            f"actual={ms_crisis['growth_score']}")
    _assert("위기 시 risk_score 높음 (3~5)", ms_crisis["risk_score"] >= 3,
            f"actual={ms_crisis['risk_score']}")
    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"위기 범위 ({key})", ms_crisis[key], 1, 5)

    # Case D: 극단적 낙관 시나리오
    signals_bull = {
        "volatility_score": 1,
        "rate_score": 1,
        "commodity_pressure_score": 1,
        "financial_stability_score": 1,
        "sentiment_score": 1,
        "dollar_tightening_signal": False,
        "fear_greed_score": 5,      # Extreme Greed
        "crypto_risk_score": 4,     # BTC Surge
        "equity_momentum_score": 1, # Strong Rally
        "xlf_gld_score": 1,         # Financial Risk-On
    }
    ms_bull = compute_market_score(signals_bull)
    _assert("낙관 시 growth_score 낮음 (1~2)", ms_bull["growth_score"] <= 2,
            f"actual={ms_bull['growth_score']}")
    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"낙관 범위 ({key})", ms_bull[key], 1, 5)


# ═══════════════════════════════════════════════════════════════
# Round 2: 통합 파이프라인 + 정합성 테스트
# ═══════════════════════════════════════════════════════════════

def round2_integration_tests():
    print("\n" + "=" * 60)
    print("Round 2: 통합 파이프라인 + 정합성 테스트")
    print("=" * 60)

    from engines.macro_engine import run_macro_engine

    # ──────────────────────────────────────────────────────
    # 시나리오 A: 정상 운영 데이터 (Tier 1 포함)
    # ──────────────────────────────────────────────────────
    print("\n[시나리오 A] 정상 운영 — 모든 데이터 정상 수집")

    snapshot_a = {"sp500": 0.5, "nasdaq": 0.8, "vix": 22.0, "us10y": 4.2,
                  "oil": 78.0, "dollar_index": 103.0}
    fred_a = {"credit_stress": "Low", "yield_curve_inverted": False,
              "fed_funds_rate": 5.25, "hy_spread": 3.2, "yield_curve": 0.5}
    fg_a = {"value": 45, "label": "Fear"}
    crypto_a = {"btc_change_pct": -1.2, "btc_usd": 84000}
    etf_a = {"XLF": {"price": 45.0, "change_pct": 0.3},
             "GLD": {"price": 220.0, "change_pct": 0.1},
             "QQQM": {"price": 180.0, "change_pct": 0.5}}

    result_a = run_macro_engine(snapshot_a, fred_a, "Neutral",
                                fear_greed=fg_a, crypto=crypto_a, etf_prices=etf_a)

    _assert("통합A: signals dict 존재", "signals" in result_a)
    _assert("통합A: market_score dict 존재", "market_score" in result_a)

    sigs_a = result_a["signals"]
    ms_a = result_a["market_score"]

    # Tier 1 시그널 생성 확인
    _assert("통합A: fear_greed_score 존재", "fear_greed_score" in sigs_a)
    _assert("통합A: crypto_risk_score 존재", "crypto_risk_score" in sigs_a)
    _assert("통합A: equity_momentum_score 존재", "equity_momentum_score" in sigs_a)
    _assert("통합A: xlf_gld_score 존재", "xlf_gld_score" in sigs_a)

    # 범위 검증
    _assert_range("통합A: fear_greed_score", sigs_a["fear_greed_score"], 1, 5)
    _assert_range("통합A: crypto_risk_score", sigs_a["crypto_risk_score"], 1, 4)
    _assert_range("통합A: equity_momentum_score", sigs_a["equity_momentum_score"], 1, 5)
    _assert_range("통합A: xlf_gld_score", sigs_a["xlf_gld_score"], 1, 3)

    # Market Score 전체 범위 검증
    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"통합A: {key}", ms_a[key], 1, 5)

    # ──────────────────────────────────────────────────────
    # 시나리오 B: 하위호환 — Tier 1 데이터 없음
    # ──────────────────────────────────────────────────────
    print("\n[시나리오 B] 하위호환 — Tier 1 데이터 전무")

    result_b = run_macro_engine(snapshot_a, fred_a, "Bullish")

    sigs_b = result_b["signals"]
    ms_b = result_b["market_score"]

    # Tier 1 시그널이 기본값으로 존재해야 함
    _assert("하위호환: fear_greed_score 중립(3)", sigs_b["fear_greed_score"] == 3)
    _assert("하위호환: crypto_risk_score 안정(1)", sigs_b["crypto_risk_score"] == 1)
    _assert("하위호환: xlf_gld_score 중립(2)", sigs_b["xlf_gld_score"] == 2)

    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"하위호환: {key}", ms_b[key], 1, 5)

    # ──────────────────────────────────────────────────────
    # 시나리오 C: 위기 시뮬레이션
    # ──────────────────────────────────────────────────────
    print("\n[시나리오 C] 위기 시뮬레이션 — VIX=45, BTC -12%, F&G=8")

    snapshot_c = {"sp500": -4.5, "nasdaq": -5.2, "vix": 45.0, "us10y": 4.8,
                  "oil": 95.0, "dollar_index": 107.0}
    fred_c = {"credit_stress": "High", "yield_curve_inverted": True,
              "fed_funds_rate": 5.5, "hy_spread": 6.0, "yield_curve": -0.3}
    fg_c = {"value": 8, "label": "Extreme Fear"}
    crypto_c = {"btc_change_pct": -12.0, "btc_usd": 62000}
    etf_c = {"XLF": {"price": 38.0, "change_pct": -3.5},
             "GLD": {"price": 240.0, "change_pct": 2.8}}

    result_c = run_macro_engine(snapshot_c, fred_c, "Bearish",
                                fear_greed=fg_c, crypto=crypto_c, etf_prices=etf_c)
    sigs_c = result_c["signals"]
    ms_c = result_c["market_score"]

    _assert("위기: growth_score 높음 (위험)", ms_c["growth_score"] >= 3,
            f"actual={ms_c['growth_score']}")
    _assert("위기: risk_score 높음", ms_c["risk_score"] >= 3,
            f"actual={ms_c['risk_score']}")
    _assert("위기: fear_greed Extreme Fear", sigs_c["fear_greed_state"] == "Extreme Fear")
    _assert("위기: BTC Crash 감지", sigs_c["crypto_risk_state"] == "BTC Crash")
    _assert("위기: Sharp Decline 감지", sigs_c["equity_momentum_state"] == "Sharp Decline")
    _assert("위기: Safe Haven Bid 감지", sigs_c["xlf_gld_state"] == "Safe Haven Bid")

    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"위기: {key} 범위", ms_c[key], 1, 5)

    # ──────────────────────────────────────────────────────
    # 시나리오 D: 낙관 시뮬레이션
    # ──────────────────────────────────────────────────────
    print("\n[시나리오 D] 낙관 시뮬레이션 — VIX=12, BTC +5%, F&G=92")

    snapshot_d = {"sp500": 1.8, "nasdaq": 2.3, "vix": 12.0, "us10y": 3.2,
                  "oil": 65.0, "dollar_index": 97.0}
    fred_d = {"credit_stress": "Low", "yield_curve_inverted": False,
              "fed_funds_rate": 4.5, "hy_spread": 2.8, "yield_curve": 1.2}
    fg_d = {"value": 92, "label": "Extreme Greed"}
    crypto_d = {"btc_change_pct": 5.0, "btc_usd": 95000}
    etf_d = {"XLF": {"price": 52.0, "change_pct": 1.8},
             "GLD": {"price": 205.0, "change_pct": -0.3}}

    result_d = run_macro_engine(snapshot_d, fred_d, "Bullish",
                                fear_greed=fg_d, crypto=crypto_d, etf_prices=etf_d)
    sigs_d = result_d["signals"]
    ms_d = result_d["market_score"]

    _assert("낙관: growth_score 낮음 (우호)", ms_d["growth_score"] <= 2,
            f"actual={ms_d['growth_score']}")
    _assert("낙관: Extreme Greed 감지", sigs_d["fear_greed_state"] == "Extreme Greed")
    _assert("낙관: Strong Rally 감지", sigs_d["equity_momentum_state"] == "Strong Rally")
    _assert("낙관: Financial Risk-On 감지", sigs_d["xlf_gld_state"] == "Financial Risk-On")

    # 과열 경고: F&G Extreme Greed는 risk 가중해야 함
    _assert("낙관: risk_score 과열 반영 (>=2)", ms_d["risk_score"] >= 2,
            f"actual={ms_d['risk_score']}")

    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _assert_range(f"낙관: {key} 범위", ms_d[key], 1, 5)

    # ──────────────────────────────────────────────────────
    # 시나리오 E: 데이터 정합성 — 같은 입력 → 같은 출력 (멱등성)
    # ──────────────────────────────────────────────────────
    print("\n[시나리오 E] 멱등성 확인 — 동일 입력 2회 실행")

    result_e1 = run_macro_engine(snapshot_a, fred_a, "Neutral",
                                  fear_greed=fg_a, crypto=crypto_a, etf_prices=etf_a)
    result_e2 = run_macro_engine(snapshot_a, fred_a, "Neutral",
                                  fear_greed=fg_a, crypto=crypto_a, etf_prices=etf_a)

    _assert("멱등성: signals 동일",
            result_e1["signals"] == result_e2["signals"],
            f"diff detected")
    _assert("멱등성: market_score 동일",
            result_e1["market_score"] == result_e2["market_score"],
            f"diff detected")

    # ──────────────────────────────────────────────────────
    # 시나리오 F: Validator 정합성 검증
    # ──────────────────────────────────────────────────────
    print("\n[시나리오 F] Validator — 비상식적 수치 차단 검증")

    from core.validator import validate_data

    # F-1: 정상 core_data 구조 (최소 필수 구조)
    valid_core = {
        "market_snapshot": {"sp500": 0.5, "nasdaq": 0.8, "vix": 22.0,
                           "us10y": 4.2, "oil": 78.0, "dollar_index": 103.0},
        "market_regime": {"market_risk_level": "MEDIUM"},
        "market_score": {"growth_score": 2, "inflation_score": 2, "liquidity_score": 2,
                        "risk_score": 3, "financial_stability_score": 2, "commodity_pressure_score": 2},
        "signals": {"fear_greed_score": 3, "crypto_risk_score": 1,
                   "equity_momentum_score": 2, "xlf_gld_score": 2,
                   "volatility_score": 3, "rate_score": 2,
                   "commodity_pressure_score": 2, "sentiment_score": 2},
        "etf_analysis": {},
        "etf_strategy": {"stance": {}},
        "etf_allocation": {"allocation": {"QQQM": 25, "XLK": 20, "SPYM": 20,
                                          "XLE": 15, "ITA": 10, "TLT": 10},
                          "total_weight": 100},
        "portfolio_risk": {},
        "trading_signal": {"trading_signal": "HOLD"},
        "output_helpers": {},
    }
    vr = validate_data(valid_core)
    _assert("Validator: 정상 데이터 PASS", vr["status"] == "PASS",
            f"errors={vr.get('errors', [])}")

    # F-2: Market Score 범위 벗어남 → 차단
    invalid_score = json.loads(json.dumps(valid_core))
    invalid_score["market_score"]["growth_score"] = 7  # 비상식적
    vr2 = validate_data(invalid_score)
    _assert("Validator: growth_score=7 → FAIL", vr2["status"] == "FAIL",
            f"should fail but got {vr2['status']}")

    # F-3: 시그널 범위 벗어남 → 차단
    invalid_sig = json.loads(json.dumps(valid_core))
    invalid_sig["signals"]["fear_greed_score"] = 0  # 범위 밖
    vr3 = validate_data(invalid_sig)
    _assert("Validator: fear_greed_score=0 → FAIL", vr3["status"] == "FAIL",
            f"should fail but got {vr3['status']}")

    # F-4: crypto_risk_score 범위 벗어남 → 차단
    invalid_crypto = json.loads(json.dumps(valid_core))
    invalid_crypto["signals"]["crypto_risk_score"] = 5  # 최대 4인데 5
    vr4 = validate_data(invalid_crypto)
    _assert("Validator: crypto_risk_score=5 → FAIL", vr4["status"] == "FAIL",
            f"should fail but got {vr4['status']}")

    # F-5: VIX 음수 → 차단
    invalid_vix = json.loads(json.dumps(valid_core))
    invalid_vix["market_snapshot"]["vix"] = -5.0
    vr5 = validate_data(invalid_vix)
    _assert("Validator: VIX=-5 → FAIL", vr5["status"] == "FAIL")

    # F-6: 정상 범위 시그널은 PASS
    normal_sigs = json.loads(json.dumps(valid_core))
    normal_sigs["signals"]["fear_greed_score"] = 5   # 범위 내
    normal_sigs["signals"]["crypto_risk_score"] = 4  # 범위 내
    vr6 = validate_data(normal_sigs)
    _assert("Validator: 경계값 정상 → PASS", vr6["status"] == "PASS",
            f"errors={vr6.get('errors', [])}")


# ═══════════════════════════════════════════════════════════════
# 실행
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🔬 Investment OS — Tier 1 Signal 전수 테스트")
    print(f"   테스트 대상: engines/macro_engine.py + core/validator.py")
    print(f"   날짜: 2026-04-01")

    try:
        round1_unit_tests()
        round2_integration_tests()
    except Exception as e:
        print(f"\n💥 테스트 실행 중 예외 발생: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── 결과 요약 ──
    print("\n" + "=" * 60)
    print(f"📊 테스트 결과: {_passed}/{_total} PASS | {_failed} FAIL")
    print("=" * 60)

    if _failed > 0:
        print("\n❌ 실패 항목:")
        for d in _fail_details:
            print(d)
        print(f"\n🚫 {_failed}건 실패 — 운영 배포 불가")
        sys.exit(1)
    else:
        print("\n🎉 전체 PASS — 운영 배포 가능")
        sys.exit(0)
