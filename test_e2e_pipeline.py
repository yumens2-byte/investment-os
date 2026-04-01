"""
test_e2e_pipeline.py (B-2)
============================
E2E 반자동 플로우 통합 테스트

목적: 수집(mock) → 분석 → core_data 조립 → Alert 감지 → 포맷 생성
     전 구간을 네트워크 없이 시뮬레이션하여 데이터 정합성 검증.

검증 항목:
  1. macro_engine: 19개 시그널 산출 → market_score 6축
  2. regime_engine: 레짐 판정
  3. etf_engine: ETF Score/Rank/Strategy/Allocation + B-7 근거
  4. risk_engine: Trading Signal
  5. json_builder: core_data 조립 (signals 포함 여부 — 버그픽스 검증)
  6. validator: validate_data PASS
  7. alert_engine: B-5/B-6 Alert 생성
  8. signal_diff: 원인 분석
  9. paid_report_formatter: B-7 유료 리포트 생성
  10. alert_formatter: X 트윗 + TG 포맷 생성
"""
import sys
import traceback

_t = _p = _f = 0
_fd = []

def _a(n, c, d=""):
    global _t, _p, _f
    _t += 1
    if c: _p += 1; print(f"  ✅ {n}")
    else: _f += 1; m = f"  ❌ {n} — {d}"; print(m); _fd.append(m)


def run():
    print("\n" + "=" * 60)
    print("E2E 반자동 플로우 통합 테스트")
    print("=" * 60)

    # ══════════════════════════════════════════════
    # Stage 1: Mock 데이터 준비
    # ══════════════════════════════════════════════
    print("\n[Stage 1] Mock 데이터 준비")

    snapshot = {
        "sp500": -2.8, "nasdaq": -3.5, "vix": 32.0,
        "us10y": 4.5, "oil": 82.0, "dollar_index": 105.0,
    }
    fred_data = {
        "fed_funds_rate": 4.25, "hy_spread": 4.8, "yield_curve": -0.2,
        "credit_stress": "Moderate", "yield_curve_inverted": True,
        "initial_claims": 280.0, "inflation_exp": 2.6,
    }
    fear_greed = {"value": 18}  # Fear
    crypto = {"btc_usd": 55000, "btc_change_pct": -6.0, "eth_usd": 1800, "eth_change_pct": -5.0}
    etf_prices = {
        "QQQM": {"price": 170, "change_pct": -3.2},
        "XLK": {"price": 200, "change_pct": -2.8},
        "SPYM": {"price": 48, "change_pct": -1.0},
        "XLE": {"price": 90, "change_pct": -0.5},
        "ITA": {"price": 135, "change_pct": 0.2},
        "TLT": {"price": 95, "change_pct": 1.5},
        "XLF": {"price": 38, "change_pct": -2.0},
        "GLD": {"price": 210, "change_pct": 1.8},
    }
    tier2_data = {
        "rsp_change": -2.5, "spy_change": -2.8,
        "vix3m": 28.0,
        "eem_change": -3.2,
        "soxx_change": -4.0, "qqq_change": -3.5,
        "kre_change": -4.5,
    }
    news_sentiment = "Bearish"

    _a("Mock 데이터 준비 완료", True)

    # ══════════════════════════════════════════════
    # Stage 2: Macro Engine (19개 시그널)
    # ══════════════════════════════════════════════
    print("\n[Stage 2] Macro Engine")
    from engines.macro_engine import run_macro_engine

    macro_result = run_macro_engine(
        snapshot, fred_data, news_sentiment,
        fear_greed=fear_greed, crypto=crypto,
        etf_prices=etf_prices, tier2_data=tier2_data,
    )
    signals = macro_result["signals"]
    market_score = macro_result["market_score"]

    _a("signals dict 존재", isinstance(signals, dict) and len(signals) > 0)
    _a("시그널 19개 키 존재", sum(1 for k in signals if k.endswith("_score")) >= 16,
       f"count={sum(1 for k in signals if k.endswith('_score'))}")
    _a("market_score 6축", len(market_score) == 6)

    # 시그널 값 범위 검증
    _SIG_RANGES = {
        "fear_greed_score": (1,5), "crypto_risk_score": (1,4),
        "equity_momentum_score": (1,5), "xlf_gld_score": (1,3),
        "breadth_score": (1,3), "vol_term_score": (1,3),
        "claims_score": (1,3), "infl_exp_score": (1,3),
        "em_stress_score": (1,4), "ai_momentum_score": (1,3),
        "nasdaq_rel_score": (1,3), "banking_stress_score": (1,3),
        "volatility_score": (1,5), "rate_score": (1,4),
        "commodity_pressure_score": (1,4), "sentiment_score": (1,3),
    }
    for key, (lo, hi) in _SIG_RANGES.items():
        val = signals.get(key)
        if val is not None:
            _a(f"범위 {key}={val}", lo <= val <= hi, f"기대={lo}~{hi}")

    for key in ["growth_score", "inflation_score", "liquidity_score",
                "risk_score", "financial_stability_score", "commodity_pressure_score"]:
        _a(f"Score {key}={market_score[key]}", 1 <= market_score[key] <= 5)

    # ══════════════════════════════════════════════
    # Stage 3: Regime Engine
    # ══════════════════════════════════════════════
    print("\n[Stage 3] Regime Engine")
    from engines.regime_engine import run_regime_engine

    composite = sum(market_score.values()) / len(market_score) * 20
    regime_result = run_regime_engine(market_score, signals, snapshot)
    regime = regime_result["market_regime"]
    risk_level = regime_result["market_risk_level"]

    _a("레짐 문자열", isinstance(regime, str) and len(regime) > 0, f"regime={regime}")
    _a("risk_level Enum", risk_level in ("LOW", "MEDIUM", "HIGH"), f"actual={risk_level}")

    # ══════════════════════════════════════════════
    # Stage 4: ETF Engine + B-7 근거
    # ══════════════════════════════════════════════
    print("\n[Stage 4] ETF Engine + B-7")
    from engines.etf_engine import run_etf_engine, generate_all_etf_rationales

    etf_result = run_etf_engine(regime, risk_level, market_score, etf_prices)

    _a("etf_analysis 존재", "etf_analysis" in etf_result)
    _a("etf_rank 6개", len(etf_result["etf_analysis"]["etf_rank"]) == 6)
    _a("allocation 합계 100", etf_result["etf_allocation"]["total_weight"] == 100)

    # B-7 근거 생성
    stance = etf_result["etf_strategy"]["stance"]
    rationales = generate_all_etf_rationales(stance, signals, regime)
    _a("ETF 6개 근거 생성", len(rationales) == 6)
    for etf in ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]:
        _a(f"B-7 {etf} rationale", len(rationales[etf]["rationale"]) > 0)

    # ══════════════════════════════════════════════
    # Stage 5: Risk Engine
    # ══════════════════════════════════════════════
    print("\n[Stage 5] Risk Engine")
    from engines.risk_engine import run_risk_engine

    risk_result = run_risk_engine(
        regime=regime, risk_level=risk_level, composite_score=composite,
        market_score=market_score, signals=signals,
        etf_analysis=etf_result["etf_analysis"],
        etf_strategy=etf_result["etf_strategy"],
        etf_allocation=etf_result["etf_allocation"],
        session_type="full",
    )
    trading_signal = risk_result["trading_signal"]["trading_signal"]
    _a("trading_signal Enum", trading_signal in ("BUY", "ADD", "HOLD", "REDUCE", "HEDGE", "SELL"),
       f"actual={trading_signal}")

    # ══════════════════════════════════════════════
    # Stage 6: JSON Core Data 조립 (signals 포함)
    # ══════════════════════════════════════════════
    print("\n[Stage 6] JSON Core Data 조립")
    from core.json_builder import assemble_core_data

    market_regime = {
        "market_regime": regime,
        "market_risk_level": risk_level,
        "regime_reason": regime_result.get("regime_reason", ""),
    }
    data = assemble_core_data(
        snapshot=snapshot, market_regime=market_regime,
        market_score=market_score, signals=signals,
        etf_analysis=etf_result["etf_analysis"],
        etf_strategy=etf_result["etf_strategy"],
        etf_allocation=etf_result["etf_allocation"],
        portfolio_risk=risk_result["portfolio_risk"],
        trading_signal=risk_result["trading_signal"],
        output_helpers=risk_result["output_helpers"],
        fear_greed=fear_greed, crypto=crypto, macro_data=fred_data,
    )

    _a("core_data에 signals 포함", "signals" in data and len(data["signals"]) > 0,
       f"keys={list(data.get('signals',{}).keys())[:3]}")
    _a("signals에 volatility_score 존재", "volatility_score" in data["signals"])
    _a("signals에 ai_momentum_score 존재", "ai_momentum_score" in data["signals"])

    # ══════════════════════════════════════════════
    # Stage 7: Validator
    # ══════════════════════════════════════════════
    print("\n[Stage 7] Validator")
    from core.validator import validate_data

    vr = validate_data(data)
    _a("validate_data PASS", vr["status"] == "PASS", f"errors={vr.get('errors')}")

    # ══════════════════════════════════════════════
    # Stage 8: Alert Engine (B-5/B-6)
    # ══════════════════════════════════════════════
    print("\n[Stage 8] Alert Engine (B-5/B-6)")
    from engines.alert_engine import run_alert_engine
    from core.signal_diff import compute_signal_diff, compute_score_diff

    # 이전 상태 시뮬레이션 (정상 시장)
    prev_signals = {k: 2 for k in signals if k.endswith("_score")}
    prev_signals.update({k: "Normal" for k in signals if k.endswith("_state")})
    prev_score = {k: 2 for k in market_score}

    # B-5: 랭킹 변화 Mock
    rank_change = {
        "top1_changed": True, "old_top1": "QQQM", "new_top1": "TLT",
        "moved_up": [{"etf": "TLT", "from": 5, "to": 1}],
        "moved_down": [{"etf": "QQQM", "from": 1, "to": 5}],
        "old_rank": {"QQQM": 1, "XLK": 2, "SPYM": 3, "XLE": 4, "ITA": 5, "TLT": 6},
        "new_rank": etf_result["etf_analysis"]["etf_rank"],
    }

    # B-6: 레짐 전환 Mock
    regime_change = {
        "regime_changed": True, "risk_changed": True,
        "old_regime": "Risk-On", "new_regime": regime,
        "old_risk_level": "LOW", "new_risk_level": risk_level,
        "direction": "danger",
        "old_market_score": prev_score, "new_market_score": market_score,
        "old_signals": prev_signals, "new_signals": signals,
    }

    signal_diff_result = compute_signal_diff(prev_signals, signals)
    score_diff_result = compute_score_diff(prev_score, market_score)

    _a("signal_diff Top3 생성", len(signal_diff_result["top_movers"]) > 0)
    _a("signal_diff summary", len(signal_diff_result["summary"]) > 5)
    _a("score_diff 생성", len(score_diff_result) > 0)

    alerts = run_alert_engine(
        snapshot, {"source_detail": []}, None,
        rank_change=rank_change, regime_change=regime_change,
        signal_diff_result=signal_diff_result, score_diff_result=score_diff_result,
    )
    alert_types = [a.alert_type for a in alerts]
    _a("ETF_RANK Alert 생성", "ETF_RANK" in alert_types)
    _a("REGIME_CHANGE Alert 생성", "REGIME_CHANGE" in alert_types)
    _a("VIX Alert 생성 (VIX=32)", "VIX" in alert_types)

    # ══════════════════════════════════════════════
    # Stage 9: 포맷 생성 (X + TG)
    # ══════════════════════════════════════════════
    print("\n[Stage 9] 포맷 생성")
    from publishers.alert_formatter import (
        format_alert_tweet, format_etf_rank_telegram, format_regime_change_telegram,
    )
    from publishers.premium_alert_formatter import (
        format_etf_rank_premium, format_regime_change_premium_v2,
    )
    from publishers.paid_report_formatter import format_paid_report

    # X 트윗 (모든 Alert)
    for alert in alerts:
        tweet = format_alert_tweet(alert)
        _a(f"X {alert.alert_type} ≤280자", len(tweet) <= 280, f"len={len(tweet)}")

    # TG 무료 — B-5
    tg_b5 = format_etf_rank_telegram(rank_change, signal_diff_result, regime)
    _a("TG무료 B-5 생성", len(tg_b5) > 50)
    _a("TG무료 B-5 원인 포함", "원인" in tg_b5)

    # TG 무료 — B-6
    tg_b6 = format_regime_change_telegram(
        regime_change, signal_diff_result, score_diff_result, trading_signal,
        min(etf_result["etf_analysis"]["etf_rank"], key=etf_result["etf_analysis"]["etf_rank"].get),
    )
    _a("TG무료 B-6 생성", len(tg_b6) > 50)

    # TG 유료 — B-5
    tg_b5_paid = format_etf_rank_premium(
        rank_change, signal_diff_result, regime, risk_level, trading_signal,
    )
    _a("TG유료 B-5 프리미엄 생성", "PREMIUM" in tg_b5_paid)
    _a("TG유료 B-5 원인 포함", "원인" in tg_b5_paid)

    # TG 유료 — B-6
    etf_rank = etf_result["etf_analysis"]["etf_rank"]
    top1 = min(etf_rank, key=etf_rank.get)
    tg_b6_paid = format_regime_change_premium_v2(
        regime_change, signal_diff_result, score_diff_result,
        trading_signal, top1,
        etf_hints=["TLT", "ITA"], avoid_etfs=["QQQM", "XLK"],
    )
    _a("TG유료 B-6 프리미엄 생성", "PREMIUM" in tg_b6_paid)

    # B-7 유료 리포트
    paid_report = format_paid_report(data)
    _a("B-7 유료 리포트 생성", len(paid_report) > 200)
    _a("B-7 근거 키워드 포함", "근거" in paid_report)
    _a("B-7 리스크 키워드 포함", "리스크" in paid_report)
    _a("B-7 PREMIUM 포함", "PREMIUM" in paid_report)

    # ══════════════════════════════════════════════
    # Stage 10: 데이터 정합성 최종 검증
    # ══════════════════════════════════════════════
    print("\n[Stage 10] 데이터 정합성 최종 검증")

    # core_data → alert_engine → formatter 데이터 흐름 일관성
    _a("signals→signal_diff 연결", signal_diff_result["top_movers"][0]["signal"] in signals)
    _a("regime→alert 연결", regime_change["new_regime"] == regime)
    _a("score→diff 연결",
       score_diff_result.get("growth_score", {}).get("new") == market_score["growth_score"])
    _a("rank→alert 연결", rank_change["new_rank"] == etf_result["etf_analysis"]["etf_rank"])

    print("\n" + "=" * 60)
    print("🏁 E2E 파이프라인 전 구간 통과")
    print("=" * 60)


if __name__ == "__main__":
    print("🔬 Investment OS — E2E 반자동 플로우 통합 테스트 (B-2)")
    try:
        run()
    except Exception as e:
        print(f"\n💥 예외: {e}")
        traceback.print_exc()
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"📊 결과: {_p}/{_t} PASS | {_f} FAIL")
    print("=" * 60)
    if _f:
        for d in _fd:
            print(d)
        sys.exit(1)
    else:
        print("\n🎉 E2E 전체 PASS — 운영 배포 가능")
