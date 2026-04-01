"""
test_tier2_signals.py — Tier 2 시그널 확장 전수 테스트
=====================================================
목적: Tier 1(145건) + Tier 2(신규) 전체 검증
실행: python test_tier2_signals.py
"""
import sys
import json
import traceback

_total = 0
_passed = 0
_failed = 0
_fail_details = []


def _assert(name, cond, detail=""):
    global _total, _passed, _failed
    _total += 1
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        msg = f"  ❌ {name} — {detail}"
        print(msg)
        _fail_details.append(msg)


def _range(name, val, lo, hi):
    _assert(name, isinstance(val, (int, float)) and lo <= val <= hi,
            f"값={val}, 기대={lo}~{hi}")


# ═══════════════════════════════════════════════════════════════
# Round 1: Tier 2 개별 시그널 단위 테스트
# ═══════════════════════════════════════════════════════════════
def round1():
    print("\n" + "=" * 60)
    print("Round 1: Tier 2 개별 시그널 단위 테스트")
    print("=" * 60)

    from engines.macro_engine import (
        _score_market_breadth, _score_vol_term_structure,
        _score_initial_claims, _score_inflation_expectation,
        _score_em_stress,
        # Tier 1 하위호환 확인용
        _score_fear_greed, _score_crypto_risk,
        _score_equity_momentum, _score_xlf_gld_relative,
        compute_market_score,
    )

    # ─── T2-1: Market Breadth ─────────────────────────────
    print("\n[T2-1] Market Breadth")

    r = _score_market_breadth({"rsp_change": 1.5, "spy_change": 0.8}, {})
    _assert("Broad Rally (spread=0.7)", r["breadth_score"] == 1)
    _assert("Broad Rally state", r["breadth_state"] == "Broad Rally")

    r = _score_market_breadth({"rsp_change": 0.5, "spy_change": 0.4}, {})
    _assert("Neutral (spread=0.1)", r["breadth_score"] == 2)

    r = _score_market_breadth({"rsp_change": -0.3, "spy_change": 0.5}, {})
    _assert("Narrow Rally (spread=-0.8)", r["breadth_score"] == 3)

    # 경계값
    r = _score_market_breadth({"rsp_change": 0.8, "spy_change": 0.5}, {})
    _assert("경계 (spread=0.3=Broad)", r["breadth_score"] == 1)

    r = _score_market_breadth({"rsp_change": 0.0, "spy_change": 0.5}, {})
    _assert("경계 (spread=-0.5=Narrow)", r["breadth_score"] == 3)

    # None / 빈값
    r = _score_market_breadth(None, {})
    _assert("None → 중립", r["breadth_score"] == 2)

    r = _score_market_breadth({}, {})
    _assert("빈 dict → 중립", r["breadth_score"] == 2)

    r = _score_market_breadth({"rsp_change": None, "spy_change": 0.5}, {})
    _assert("RSP None → No Data", r["breadth_score"] == 2)

    _range("breadth 범위", r["breadth_score"], 1, 3)

    # ─── T2-2: Vol Term Structure ─────────────────────────
    print("\n[T2-2] Vol Term Structure")

    r = _score_vol_term_structure({"vix": 15.0}, {"vix3m": 20.0})
    _assert("Contango (0.75)", r["vol_term_score"] == 1)
    _assert("Contango state", r["vol_term_state"] == "Contango")

    r = _score_vol_term_structure({"vix": 18.0}, {"vix3m": 20.0})
    _assert("Flat (0.9)", r["vol_term_score"] == 2)

    r = _score_vol_term_structure({"vix": 35.0}, {"vix3m": 30.0})
    _assert("Backwardation (1.17)", r["vol_term_score"] == 3)
    _assert("Backwardation state", r["vol_term_state"] == "Backwardation")

    # 경계값
    r = _score_vol_term_structure({"vix": 20.0}, {"vix3m": 20.0})
    _assert("경계 (ratio=1.0=Backwardation)", r["vol_term_score"] == 3)

    r = _score_vol_term_structure({"vix": 17.0}, {"vix3m": 20.0})
    _assert("경계 (ratio=0.85=Flat)", r["vol_term_score"] == 2)

    r = _score_vol_term_structure({"vix": 17.1}, {"vix3m": 20.0})
    _assert("경계 (ratio=0.855=Flat)", r["vol_term_score"] == 2)

    # None
    r = _score_vol_term_structure({"vix": None}, {"vix3m": 20.0})
    _assert("VIX None → No Data", r["vol_term_score"] == 2)

    r = _score_vol_term_structure({"vix": 20.0}, None)
    _assert("tier2 None → No Data", r["vol_term_score"] == 2)

    r = _score_vol_term_structure({"vix": 20.0}, {"vix3m": 0})
    _assert("VIX3M 0 → No Data", r["vol_term_score"] == 2)

    _range("vol_term 범위", 3, 1, 3)

    # ─── T2-3: Initial Claims ─────────────────────────────
    print("\n[T2-3] Initial Claims")

    r = _score_initial_claims({"initial_claims": 200.0})
    _assert("Strong Labor (200K)", r["claims_score"] == 1)
    _assert("Strong state", r["claims_state"] == "Strong Labor")

    r = _score_initial_claims({"initial_claims": 260.0})
    _assert("Normal (260K)", r["claims_score"] == 2)

    r = _score_initial_claims({"initial_claims": 350.0})
    _assert("Weak Labor (350K)", r["claims_score"] == 3)
    _assert("Weak state", r["claims_state"] == "Weak Labor")

    # 경계값
    r = _score_initial_claims({"initial_claims": 220.0})
    _assert("경계 (220K=Normal, <220이 Strong)", r["claims_score"] == 2)

    r = _score_initial_claims({"initial_claims": 220.1})
    _assert("경계 (220.1K=Normal)", r["claims_score"] == 2)

    r = _score_initial_claims({"initial_claims": 300.0})
    _assert("경계 (300K=Normal)", r["claims_score"] == 2)

    r = _score_initial_claims({"initial_claims": 300.1})
    _assert("경계 (300.1K=Weak)", r["claims_score"] == 3)

    # None
    r = _score_initial_claims(None)
    _assert("None → 중립", r["claims_score"] == 2)

    r = _score_initial_claims({})
    _assert("빈 dict → 중립", r["claims_score"] == 2)

    _range("claims 범위", r["claims_score"], 1, 3)

    # ─── T2-4: Inflation Expectation ──────────────────────
    print("\n[T2-4] Inflation Expectation")

    r = _score_inflation_expectation({"inflation_exp": 1.5})
    _assert("Disinflation (1.5%)", r["infl_exp_score"] == 1)

    r = _score_inflation_expectation({"inflation_exp": 2.4})
    _assert("Normal (2.4%)", r["infl_exp_score"] == 2)

    r = _score_inflation_expectation({"inflation_exp": 3.2})
    _assert("Inflation Concern (3.2%)", r["infl_exp_score"] == 3)

    # 경계값
    r = _score_inflation_expectation({"inflation_exp": 2.0})
    _assert("경계 (2.0%=Normal, <2.0이 Disinflation)", r["infl_exp_score"] == 2)

    r = _score_inflation_expectation({"inflation_exp": 2.01})
    _assert("경계 (2.01%=Normal)", r["infl_exp_score"] == 2)

    r = _score_inflation_expectation({"inflation_exp": 2.8})
    _assert("경계 (2.8%=Normal)", r["infl_exp_score"] == 2)

    r = _score_inflation_expectation({"inflation_exp": 2.81})
    _assert("경계 (2.81%=Concern)", r["infl_exp_score"] == 3)

    r = _score_inflation_expectation(None)
    _assert("None → 중립", r["infl_exp_score"] == 2)

    _range("infl_exp 범위", r["infl_exp_score"], 1, 3)

    # ─── T2-5: EM Stress ──────────────────────────────────
    print("\n[T2-5] EM Stress")

    r = _score_em_stress({"eem_change": 0.5}, {"dollar_index": 100.0})
    _assert("EM Stable (+0.5%)", r["em_stress_score"] == 1)

    r = _score_em_stress({"eem_change": -1.5}, {"dollar_index": 100.0})
    _assert("EM Mild Weakness (-1.5%)", r["em_stress_score"] == 2)

    r = _score_em_stress({"eem_change": -3.0}, {"dollar_index": 100.0})
    _assert("EM Stress (-3%, DXY약)", r["em_stress_score"] == 3)

    r = _score_em_stress({"eem_change": -3.0}, {"dollar_index": 107.0})
    _assert("EM Crisis Spillover (-3%, DXY강)", r["em_stress_score"] == 4)
    _assert("Spillover state", r["em_stress_state"] == "EM Crisis Spillover")

    # 경계값
    r = _score_em_stress({"eem_change": -2.0}, {"dollar_index": 104.0})
    _assert("경계 (-2%, DXY=104=Spillover)", r["em_stress_score"] == 4)

    r = _score_em_stress({"eem_change": -2.0}, {"dollar_index": 103.9})
    _assert("경계 (-2%, DXY=103.9=Stress)", r["em_stress_score"] == 3)

    r = _score_em_stress({"eem_change": -1.0}, {"dollar_index": 107.0})
    _assert("경계 (-1%=Mild, DXY강해도)", r["em_stress_score"] == 2)

    r = _score_em_stress(None, {"dollar_index": 100.0})
    _assert("None → 안정", r["em_stress_score"] == 1)

    _range("em_stress 범위", 4, 1, 4)

    # ─── Tier 1 하위호환 ──────────────────────────────────
    print("\n[Tier 1 하위호환 확인]")
    r = _score_fear_greed({"value": 50})
    _assert("T1 F&G 정상", r["fear_greed_score"] == 3)
    r = _score_crypto_risk({"btc_change_pct": 0.0})
    _assert("T1 BTC 정상", r["crypto_risk_score"] == 1)
    r = _score_equity_momentum({"sp500": 0.0, "nasdaq": 0.0})
    _assert("T1 Momentum 정상", r["equity_momentum_score"] == 3)
    r = _score_xlf_gld_relative({"XLF": {"change_pct": 0.0}, "GLD": {"change_pct": 0.0}})
    _assert("T1 XLF/GLD 정상", r["xlf_gld_score"] == 2)

    # ─── Market Score 통합 (Tier 1+2) ─────────────────────
    print("\n[Market Score 통합 — Tier 1+2]")

    # Case A: 모든 시그널 정상
    sigs_full = {
        "volatility_score": 2, "rate_score": 2, "commodity_pressure_score": 2,
        "financial_stability_score": 2, "sentiment_score": 2,
        "dollar_tightening_signal": False,
        "fear_greed_score": 3, "crypto_risk_score": 1,
        "equity_momentum_score": 2, "xlf_gld_score": 2,
        "breadth_score": 2, "vol_term_score": 2, "claims_score": 2,
        "infl_exp_score": 2, "em_stress_score": 1,
    }
    ms = compute_market_score(sigs_full)
    for k in ["growth_score", "inflation_score", "liquidity_score",
              "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _range(f"정상 {k}", ms[k], 1, 5)

    # Case B: Tier 2 없이 하위호환
    sigs_legacy = {
        "volatility_score": 2, "rate_score": 2, "commodity_pressure_score": 2,
        "financial_stability_score": 2, "sentiment_score": 2,
        "dollar_tightening_signal": False,
    }
    ms2 = compute_market_score(sigs_legacy)
    for k in ["growth_score", "inflation_score", "liquidity_score",
              "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _range(f"Legacy {k}", ms2[k], 1, 5)

    # Case C: 위기 시나리오
    sigs_crisis = {
        "volatility_score": 5, "rate_score": 4, "commodity_pressure_score": 4,
        "financial_stability_score": 3, "sentiment_score": 3,
        "dollar_tightening_signal": True,
        "fear_greed_score": 1, "crypto_risk_score": 3,
        "equity_momentum_score": 5, "xlf_gld_score": 3,
        "breadth_score": 3, "vol_term_score": 3, "claims_score": 3,
        "infl_exp_score": 3, "em_stress_score": 4,
    }
    ms3 = compute_market_score(sigs_crisis)
    _assert("위기: growth >= 4", ms3["growth_score"] >= 4, f"actual={ms3['growth_score']}")
    _assert("위기: risk >= 3", ms3["risk_score"] >= 3, f"actual={ms3['risk_score']}")
    _assert("위기: liquidity >= 3", ms3["liquidity_score"] >= 3, f"actual={ms3['liquidity_score']}")
    for k in ["growth_score", "inflation_score", "liquidity_score",
              "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _range(f"위기 {k}", ms3[k], 1, 5)

    # Case D: 낙관 시나리오
    sigs_bull = {
        "volatility_score": 1, "rate_score": 1, "commodity_pressure_score": 1,
        "financial_stability_score": 1, "sentiment_score": 1,
        "dollar_tightening_signal": False,
        "fear_greed_score": 5, "crypto_risk_score": 1,
        "equity_momentum_score": 1, "xlf_gld_score": 1,
        "breadth_score": 1, "vol_term_score": 1, "claims_score": 1,
        "infl_exp_score": 1, "em_stress_score": 1,
    }
    ms4 = compute_market_score(sigs_bull)
    _assert("낙관: growth <= 2", ms4["growth_score"] <= 2, f"actual={ms4['growth_score']}")
    for k in ["growth_score", "inflation_score", "liquidity_score",
              "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _range(f"낙관 {k}", ms4[k], 1, 5)


# ═══════════════════════════════════════════════════════════════
# Round 2: 통합 파이프라인 + 정합성
# ═══════════════════════════════════════════════════════════════
def round2():
    print("\n" + "=" * 60)
    print("Round 2: 통합 파이프라인 + 정합성")
    print("=" * 60)

    from engines.macro_engine import run_macro_engine
    from core.validator import validate_data

    # ─── 시나리오 A: 전체 데이터 정상 ─────────────────────
    print("\n[시나리오 A] 전체 데이터 정상")
    snap = {"sp500": 0.5, "nasdaq": 0.8, "vix": 22.0, "us10y": 4.2,
            "oil": 78.0, "dollar_index": 103.0}
    fred = {"credit_stress": "Low", "yield_curve_inverted": False,
            "initial_claims": 225.0, "inflation_exp": 2.35}
    fg = {"value": 45}
    crypto = {"btc_change_pct": -1.2}
    etf = {"XLF": {"change_pct": 0.3}, "GLD": {"change_pct": 0.1}}
    t2 = {"rsp_change": 0.6, "spy_change": 0.4, "vix3m": 24.0, "eem_change": 0.2}

    r = run_macro_engine(snap, fred, "Neutral",
                         fear_greed=fg, crypto=crypto, etf_prices=etf, tier2_data=t2)
    sigs = r["signals"]
    ms = r["market_score"]

    # Tier 2 시그널 존재 확인
    for key in ["breadth_score", "vol_term_score", "claims_score",
                "infl_exp_score", "em_stress_score"]:
        _assert(f"A: {key} 존재", key in sigs)
        _range(f"A: {key} 범위", sigs[key], 1, 5 if "em" in key else 3)

    for k in ["growth_score", "inflation_score", "liquidity_score",
              "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _range(f"A: {k}", ms[k], 1, 5)

    # ─── 시나리오 B: 하위호환 (Tier 2 없음) ───────────────
    print("\n[시나리오 B] 하위호환 — Tier 2 전무")
    r2 = run_macro_engine(snap, {"credit_stress": "Low", "yield_curve_inverted": False},
                          "Bullish")
    _assert("B: breadth 기본값", r2["signals"]["breadth_score"] == 2)
    _assert("B: vol_term 기본값", r2["signals"]["vol_term_score"] == 2)
    _assert("B: claims 기본값", r2["signals"]["claims_score"] == 2)
    _assert("B: infl_exp 기본값", r2["signals"]["infl_exp_score"] == 2)
    _assert("B: em_stress 기본값", r2["signals"]["em_stress_score"] == 1)
    for k in ["growth_score", "inflation_score", "liquidity_score",
              "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _range(f"B: {k}", r2["market_score"][k], 1, 5)

    # ─── 시나리오 C: 위기 전이 시뮬레이션 ─────────────────
    print("\n[시나리오 C] 위기 — EM Spillover + Backwardation")
    snap_c = {"sp500": -4.5, "nasdaq": -5.2, "vix": 45.0, "us10y": 4.8,
              "oil": 95.0, "dollar_index": 107.0}
    fred_c = {"credit_stress": "High", "yield_curve_inverted": True,
              "initial_claims": 380.0, "inflation_exp": 3.5}
    t2_c = {"rsp_change": -5.0, "spy_change": -4.0, "vix3m": 35.0, "eem_change": -4.5}

    r3 = run_macro_engine(snap_c, fred_c, "Bearish",
                          fear_greed={"value": 5}, crypto={"btc_change_pct": -15.0},
                          etf_prices={"XLF": {"change_pct": -4.0}, "GLD": {"change_pct": 3.0}},
                          tier2_data=t2_c)
    s3 = r3["signals"]
    _assert("C: Backwardation", s3["vol_term_state"] == "Backwardation")
    _assert("C: EM Spillover", s3["em_stress_state"] == "EM Crisis Spillover")
    _assert("C: Weak Labor", s3["claims_state"] == "Weak Labor")
    _assert("C: Inflation Concern", s3["infl_exp_state"] == "Inflation Concern")
    _assert("C: Narrow Rally", s3["breadth_state"] == "Narrow Rally")
    _assert("C: risk >= 3", r3["market_score"]["risk_score"] >= 3,
            f"actual={r3['market_score']['risk_score']}")
    _assert("C: liquidity >= 3", r3["market_score"]["liquidity_score"] >= 3,
            f"actual={r3['market_score']['liquidity_score']}")

    # ─── 시나리오 D: 멱등성 ───────────────────────────────
    print("\n[시나리오 D] 멱등성")
    ra = run_macro_engine(snap, fred, "Neutral", fear_greed=fg, crypto=crypto,
                          etf_prices=etf, tier2_data=t2)
    rb = run_macro_engine(snap, fred, "Neutral", fear_greed=fg, crypto=crypto,
                          etf_prices=etf, tier2_data=t2)
    _assert("D: signals 동일", ra["signals"] == rb["signals"])
    _assert("D: market_score 동일", ra["market_score"] == rb["market_score"])

    # ─── 시나리오 E: Validator Tier 2 범위 차단 ───────────
    print("\n[시나리오 E] Validator Tier 2 차단")
    base = {
        "market_snapshot": snap,
        "market_regime": {"market_risk_level": "MEDIUM"},
        "market_score": {"growth_score": 2, "inflation_score": 2, "liquidity_score": 2,
                        "risk_score": 2, "financial_stability_score": 2, "commodity_pressure_score": 2},
        "signals": {"breadth_score": 2, "vol_term_score": 2, "claims_score": 2,
                   "infl_exp_score": 2, "em_stress_score": 1,
                   "fear_greed_score": 3, "crypto_risk_score": 1,
                   "equity_momentum_score": 3, "xlf_gld_score": 2,
                   "volatility_score": 2, "rate_score": 2,
                   "commodity_pressure_score": 2, "sentiment_score": 2},
        "etf_analysis": {}, "etf_strategy": {"stance": {}},
        "etf_allocation": {"allocation": {"QQQM": 25, "XLK": 20, "SPYM": 20,
                                          "XLE": 15, "ITA": 10, "TLT": 10}, "total_weight": 100},
        "portfolio_risk": {}, "trading_signal": {"trading_signal": "HOLD"}, "output_helpers": {},
    }
    vr = validate_data(base)
    _assert("E: 정상 PASS", vr["status"] == "PASS", f"errors={vr.get('errors')}")

    # breadth_score 범위 밖
    bad1 = json.loads(json.dumps(base))
    bad1["signals"]["breadth_score"] = 4
    _assert("E: breadth=4 → FAIL", validate_data(bad1)["status"] == "FAIL")

    # em_stress_score 범위 밖
    bad2 = json.loads(json.dumps(base))
    bad2["signals"]["em_stress_score"] = 5
    _assert("E: em_stress=5 → FAIL", validate_data(bad2)["status"] == "FAIL")

    # vol_term_score 범위 밖
    bad3 = json.loads(json.dumps(base))
    bad3["signals"]["vol_term_score"] = 0
    _assert("E: vol_term=0 → FAIL", validate_data(bad3)["status"] == "FAIL")

    # 정상 경계값
    ok = json.loads(json.dumps(base))
    ok["signals"]["em_stress_score"] = 4
    ok["signals"]["breadth_score"] = 3
    _assert("E: 경계 정상 PASS", validate_data(ok)["status"] == "PASS",
            f"errors={validate_data(ok).get('errors')}")


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🔬 Investment OS — Tier 1+2 Signal 전수 테스트")
    try:
        round1()
        round2()
    except Exception as e:
        print(f"\n💥 예외: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"📊 결과: {_passed}/{_total} PASS | {_failed} FAIL")
    print("=" * 60)
    if _failed:
        print("\n❌ 실패:")
        for d in _fail_details:
            print(d)
        sys.exit(1)
    else:
        print("\n🎉 전체 PASS — 운영 배포 가능")
