"""
tests/test_alert_integration_full.py
======================================
run_alert_engine() 통합 시나리오 전수 테스트

검증 영역:
  1. 단일 알람 시나리오 (16종 각각 단독 발생)
  2. 다중 알람 동시 발생 (정렬 순서 L3→L2→L1)
  3. 중복 방지 로직 (CRISIS ↔ VIX, FED_SHOCK ↔ SPY)
  4. prev_snapshot 의존 (VIX surge, OIL surge, VIX_COUNTDOWN)
  5. 빈 입력 (모든 None) → 빈 리스트
  6. VIX 프리미엄 메타 부착 (vix_premium_level/crossed)
  7. Fed 키워드 감지 정합성 (2건 이상)
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("LOG_LEVEL", "ERROR")
os.environ.setdefault("FRED_API_KEY", "test")
os.environ.setdefault("X_API_KEY", "test")
os.environ.setdefault("X_API_SECRET", "test")
os.environ.setdefault("X_ACCESS_TOKEN", "test")
os.environ.setdefault("X_ACCESS_TOKEN_SECRET", "test")

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.alert_engine import run_alert_engine, VIX_PREMIUM_LEVELS

PASS = 0
FAIL = 0
DETAILS = []

def check(case_id, desc, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  ✅ {case_id}: {desc}")
    else:
        FAIL += 1
        DETAILS.append(f"{case_id}: {desc} — expected={expected} actual={actual}")
        print(f"  ❌ {case_id}: {desc} — expected={expected} actual={actual}")


# ─────────────────────────────────────────────────────────────
# 헬퍼: 빈 입력 베이스
# ─────────────────────────────────────────────────────────────
EMPTY_NEWS = {"source_detail": []}

def _types(alerts):
    return [(a.alert_type, a.level) for a in alerts]


# ═════════════════════════════════════════════════════════════
# Scenario A: 빈 입력 / 정상 시장
# ═════════════════════════════════════════════════════════════
def test_empty_inputs():
    print("\n[Scenario A] 빈 입력 / 정상 시장")

    # 완전 빈 시장
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    check("A-01", "정상 시장 → 0개 alert", len(alerts), 0)

    # snapshot=빈 dict (수집 실패 시뮬레이션)
    alerts = run_alert_engine(
        snapshot={},
        news_result=EMPTY_NEWS,
    )
    check("A-02", "빈 snapshot → 0개 alert", len(alerts), 0)


# ═════════════════════════════════════════════════════════════
# Scenario B: 단일 알람 발생
# ═════════════════════════════════════════════════════════════
def test_single_alerts():
    print("\n[Scenario B] 16종 알람 단일 발생")

    # VIX L2 (prev_snapshot 제공 — VIX_COUNTDOWN 중복 방지)
    alerts = run_alert_engine(
        snapshot={"vix": 40.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        prev_snapshot={"vix": 38.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    check("B-01-VIX-L2", "VIX 40 (prev 38) → VIX/L2 단일",
          _types(alerts), [("VIX", "L2")])

    # VIX L1 (prev_snapshot 제공)
    alerts = run_alert_engine(
        snapshot={"vix": 30.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        prev_snapshot={"vix": 29.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    check("B-02-VIX-L1", "VIX 30 (prev 29) → VIX/L1 단일",
          _types(alerts), [("VIX", "L1")])

    # P2-B (v1.1.2): prev_snapshot=None 시 VIX_COUNTDOWN 미발동 가드 추가
    alerts = run_alert_engine(
        snapshot={"vix": 30.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        prev_snapshot=None,
        news_result=EMPTY_NEWS,
    )
    types = _types(alerts)
    check("B-02b-P2B", "[P2-B 효과] prev=None + VIX 30 → VIX_COUNTDOWN 미발동",
          ("VIX_COUNTDOWN", "L1") not in types and ("VIX", "L1") in types, True)

    # SPY L1 (Fed 미감지)
    alerts = run_alert_engine(
        snapshot={"vix": 20.0, "sp500": -3.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    check("B-03-SPY-L1", "SPY -3% → SPY/L1 단일",
          _types(alerts), [("SPY", "L1")])

    # OIL L2 (가격충격)
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 105.0},
        news_result=EMPTY_NEWS,
    )
    check("B-04-OIL-L2", "Oil $105 → OIL/L2 단일",
          _types(alerts), [("OIL", "L2")])

    # OIL L1 (급등률만)
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 90.0},
        prev_snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    check("B-05-OIL-L1", "Oil 85→90 (+5.8%) → OIL/L1 단일",
          _types(alerts), [("OIL", "L1")])

    # MOVE_SPIKE
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        tier2_data={"move_index": 145.0},
    )
    check("B-06-MOVE", "MOVE 145 → MOVE_SPIKE/L1 단일",
          _types(alerts), [("MOVE_SPIKE", "L1")])

    # STAGFLATION
    alerts = run_alert_engine(
        snapshot={"vix": 22.0, "sp500": -2.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        tier2_data={"tlt_change": -1.5},
    )
    # SPY -2 → SPY 미발동 (-2.5 이상), STAGFLATION 발동
    check("B-07-STAG", "SPY -2 + TLT -1.5 → STAGFLATION/L2 단일",
          _types(alerts), [("STAGFLATION", "L2")])

    # CPI_HOT
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        fred_data={"cpi_yoy": 4.2},
    )
    check("B-08-CPI", "CPI 4.2% → CPI_HOT/L1 단일",
          _types(alerts), [("CPI_HOT", "L1")])

    # SOFR_STRESS
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        fred_data={"sofr_spread": 0.6, "sofr": 5.4},
    )
    check("B-09-SOFR", "SOFR spread 0.6 → SOFR_STRESS/L1 단일",
          _types(alerts), [("SOFR_STRESS", "L1")])

    # YIELD_SPREAD_DEEP
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        fred_data={"spread_2y10y_bp": -60.0, "us2y": 5.0},
    )
    check("B-10-YS", "2Y-10Y -60bp → YIELD_SPREAD_DEEP/L1 단일",
          _types(alerts), [("YIELD_SPREAD_DEEP", "L1")])

    # SMA200_BREAK L2
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        spy_sma_data={"spy_price": 570, "spy_sma50": 575, "spy_sma200": 580},
    )
    check("B-11-SMA-L2", "DC + 이탈 → SMA200_BREAK/L2 단일",
          _types(alerts), [("SMA200_BREAK", "L2")])

    # PCR_EXTREME (signals 통해)
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        signals={"pcr_value": 1.6, "pcr_state": "extreme_fear"},
    )
    check("B-12-PCR", "PCR 1.6 → PCR_EXTREME/L1 단일",
          _types(alerts), [("PCR_EXTREME", "L1")])

    # CRYPTO_BASIS
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        signals={"crypto_basis_spread": -1.5, "crypto_basis_state": "backwardation"},
    )
    check("B-13-CB", "Basis -1.5% → CRYPTO_BASIS/L1 단일",
          _types(alerts), [("CRYPTO_BASIS", "L1")])

    # ETF_RANK
    rc = {"top1_changed": True, "old_top1": "QQQM", "new_top1": "TLT",
          "moved_up": [], "moved_down": []}
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        rank_change=rc,
    )
    check("B-14-ETF", "Top1 변경 → ETF_RANK/L2 단일",
          _types(alerts), [("ETF_RANK", "L2")])

    # REGIME_CHANGE
    rgc = {"old_regime": "Risk-On", "new_regime": "Oil Shock", "direction": "danger",
           "old_risk_level": "LOW", "new_risk_level": "HIGH",
           "regime_changed": True, "risk_changed": True}
    alerts = run_alert_engine(
        snapshot={"vix": 18.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
        regime_change=rgc,
    )
    check("B-15-RGM", "Shock 진입 → REGIME_CHANGE/L2 단일",
          _types(alerts), [("REGIME_CHANGE", "L2")])

    # VIX_COUNTDOWN
    alerts = run_alert_engine(
        snapshot={"vix": 25.5, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        prev_snapshot={"vix": 24.0, "sp500": 0.5, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    # VIX 25.5 < L1(28) so VIX alert 미발동, COUNTDOWN만
    check("B-16-VCD", "VIX 25.5 신규 돌파 → VIX_COUNTDOWN/L1 단일",
          _types(alerts), [("VIX_COUNTDOWN", "L1")])


# ═════════════════════════════════════════════════════════════
# Scenario C: VIX 격상 조건 (prev_snapshot 의존)
# ═════════════════════════════════════════════════════════════
def test_vix_surge():
    print("\n[Scenario C] VIX 급등 격상 조건 (prev_snapshot 의존)")

    # VIX 30 + prev 25 (+20% surge) → L1 → L2 격상
    alerts = run_alert_engine(
        snapshot={"vix": 30.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        prev_snapshot={"vix": 25.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    types = _types(alerts)
    check("C-01", "VIX 30 + surge 20% → L2 격상",
          ("VIX", "L2") in types, True)

    # VIX 30 + prev 28 (+7%) → L1 유지
    alerts = run_alert_engine(
        snapshot={"vix": 30.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        prev_snapshot={"vix": 28.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    types = _types(alerts)
    check("C-02", "VIX 30 + surge 7% → L1 유지",
          ("VIX", "L1") in types, True)


# ═════════════════════════════════════════════════════════════
# Scenario D: CRISIS 발생 → VIX 중복 방지
# ═════════════════════════════════════════════════════════════
def test_crisis_dedup():
    print("\n[Scenario D] CRISIS 발생 시 VIX 중복 방지")

    # 3개 동시 → CRISIS L3, VIX 흡수
    alerts = run_alert_engine(
        snapshot={"vix": 40.0, "sp500": -5.0, "us10y": 5.0, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    types = _types(alerts)
    check("D-01", "CRISIS L3 발생 → VIX 미발행 (중복 방지)",
          ("VIX", "L2") not in types, True)
    check("D-02", "CRISIS L3 포함됨",
          ("CRISIS", "L3") in types, True)
    check("D-03", "SPY L2도 동시 발행 (CRISIS와 별개)",
          ("SPY", "L2") in types, True)


# ═════════════════════════════════════════════════════════════
# Scenario E: FED_SHOCK 발생 → SPY 중복 방지
# ═════════════════════════════════════════════════════════════
def test_fed_dedup():
    print("\n[Scenario E] FED_SHOCK 발생 시 SPY 중복 방지")

    # SPY 급락 + Fed 키워드 2건 → FED_SHOCK
    news = {"source_detail": [
        {"headlines": [
            "Powell signals rate hike", "FOMC raises basis points by 50",
        ]},
    ]}
    alerts = run_alert_engine(
        snapshot={"vix": 22.0, "sp500": -3.0, "us10y": 4.2, "oil": 85.0},
        news_result=news,
    )
    types = _types(alerts)
    check("E-01", "FED_SHOCK 포함됨",
          any(t[0] == "FED_SHOCK" for t in types), True)
    check("E-02", "SPY 단독 미발행 (FED_SHOCK가 흡수)",
          ("SPY", "L1") not in types, True)


# ═════════════════════════════════════════════════════════════
# Scenario F: 등급 내림차순 정렬
# ═════════════════════════════════════════════════════════════
def test_sort_order():
    print("\n[Scenario F] 정렬 순서 L3 → L2 → L1")

    # CRISIS L3 + STAGFLATION L2 + CPI L1 동시
    alerts = run_alert_engine(
        snapshot={"vix": 40.0, "sp500": -5.0, "us10y": 5.0, "oil": 85.0},
        news_result=EMPTY_NEWS,
        tier2_data={"tlt_change": -2.0},
        fred_data={"cpi_yoy": 4.0},
    )
    levels = [a.level for a in alerts]
    print(f"    실제 발행 순서: {_types(alerts)}")
    # L3가 가장 먼저, L1이 마지막
    check("F-01", "정렬: L3가 첫 번째",
          alerts[0].level if alerts else None, "L3")
    check("F-02", "정렬: L1이 마지막",
          alerts[-1].level if alerts else None, "L1")
    # 모든 인접 쌍이 L3<=L2<=L1 순서
    level_order = {"L3": 0, "L2": 1, "L1": 2}
    is_sorted = all(
        level_order[levels[i]] <= level_order[levels[i+1]]
        for i in range(len(levels)-1)
    )
    check("F-03", "전 구간 내림차순 정렬 유지", is_sorted, True)


# ═════════════════════════════════════════════════════════════
# Scenario G: VIX 프리미엄 메타 부착
# ═════════════════════════════════════════════════════════════
def test_vix_premium_meta():
    print(f"\n[Scenario G] VIX 프리미엄 메타 부착 ({VIX_PREMIUM_LEVELS})")

    # VIX 30 + prev 19 → premium_level=30, crossed=True (25 미돌파 시 premium=25)
    alerts = run_alert_engine(
        snapshot={"vix": 30.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        prev_snapshot={"vix": 19.0, "sp500": -1.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    if alerts:
        a = alerts[0]
        check("G-01", "premium_level=30 (vix>=30, vix<35)", a.vix_premium_level, 30)
        check("G-02", "premium_crossed=True (prev=19 < 30)", a.vix_premium_crossed, True)
        check("G-03", "prev_vix=19", a.prev_vix, 19.0)

    # VIX 22 (premium_level=20)
    alerts = run_alert_engine(
        snapshot={"vix": 22.0, "sp500": -3.0, "us10y": 4.2, "oil": 85.0},
        prev_snapshot={"vix": 18.0, "sp500": -3.0, "us10y": 4.2, "oil": 85.0},
        news_result=EMPTY_NEWS,
    )
    if alerts:
        a = alerts[0]
        check("G-04", "premium_level=20 (vix>=20, vix<25)",
              a.vix_premium_level, 20)
        check("G-05", "premium_crossed=True (prev=18 < 20)",
              a.vix_premium_crossed, True)


# ═════════════════════════════════════════════════════════════
# Scenario H: Fed 키워드 감지 (2건 이상)
# ═════════════════════════════════════════════════════════════
def test_fed_keyword():
    print("\n[Scenario H] Fed 키워드 감지 임계 (2건 이상)")

    # 1건만 → 감지 안 됨 → FED_SHOCK 미발동
    news1 = {"source_detail": [{"headlines": ["Powell speech today"]}]}
    alerts = run_alert_engine(
        snapshot={"vix": 22.0, "sp500": -3.0, "us10y": 4.2, "oil": 85.0},
        news_result=news1,
    )
    types = _types(alerts)
    check("H-01", "Fed 키워드 1건만 → FED_SHOCK 미발동",
          any(t[0] == "FED_SHOCK" for t in types), False)
    check("H-02", "SPY 정상 발행 (Fed 미감지)",
          ("SPY", "L1") in types, True)

    # 2건 → 감지 → FED_SHOCK
    news2 = {"source_detail": [{"headlines": [
        "Powell signals rate hike", "FOMC raises basis points"
    ]}]}
    alerts = run_alert_engine(
        snapshot={"vix": 22.0, "sp500": -3.0, "us10y": 4.2, "oil": 85.0},
        news_result=news2,
    )
    types = _types(alerts)
    check("H-03", "Fed 키워드 2건 → FED_SHOCK 발동",
          any(t[0] == "FED_SHOCK" for t in types), True)


# ═════════════════════════════════════════════════════════════
# Scenario I: 다중 알람 동시 (현실적 시나리오)
# ═════════════════════════════════════════════════════════════
def test_multi_alert():
    print("\n[Scenario I] 현실 시나리오 — 복합 위기 상황")

    # 2022-09 스타일: VIX 35, SPY -4, US10Y 4.0 (CRISIS L2), MOVE 145, TLT -2 (STAGFLATION), CPI 6
    alerts = run_alert_engine(
        snapshot={"vix": 36.0, "sp500": -4.5, "us10y": 4.0, "oil": 95.0},
        prev_snapshot={"vix": 30.0, "sp500": -1.0, "us10y": 3.9, "oil": 90.0},
        news_result=EMPTY_NEWS,
        tier2_data={"move_index": 150.0, "tlt_change": -2.0},
        fred_data={"cpi_yoy": 6.0, "sofr_spread": 0.2,
                   "spread_2y10y_bp": -70.0, "us2y": 4.5},
    )
    types = _types(alerts)
    print(f"    발행 alert: {types}")
    check("I-01", "CRISIS L2 발행 (VIX+SPY 2개)",
          ("CRISIS", "L2") in types, True)
    check("I-02", "STAGFLATION L2 발행 (SPY+TLT 동반)",
          ("STAGFLATION", "L2") in types, True)
    check("I-03", "MOVE_SPIKE 발행", ("MOVE_SPIKE", "L1") in types, True)
    check("I-04", "CPI_HOT 발행",   ("CPI_HOT", "L1") in types, True)
    check("I-05", "YIELD_SPREAD_DEEP 발행", ("YIELD_SPREAD_DEEP", "L1") in types, True)
    # SPY는 CRISIS 발생해도 별도 발행 (CRISIS는 VIX만 흡수, SPY는 그대로)
    check("I-06", "SPY L2 발행 (CRISIS와 별개)", ("SPY", "L2") in types, True)
    # VIX는 CRISIS에 흡수
    check("I-07", "VIX 미발행 (CRISIS L2 흡수)",
          not any(t[0] == "VIX" for t in types), True)
    # 정렬: L2 먼저, L1 다음
    level_order = {"L3": 0, "L2": 1, "L1": 2}
    levels = [level_order[a.level] for a in alerts]
    check("I-08", "정렬 유지 (오름차순)",
          levels == sorted(levels), True)


# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 75)
    print("run_alert_engine() 통합 시나리오 전수 테스트")
    print("=" * 75)

    test_empty_inputs()
    test_single_alerts()
    test_vix_surge()
    test_crisis_dedup()
    test_fed_dedup()
    test_sort_order()
    test_vix_premium_meta()
    test_fed_keyword()
    test_multi_alert()

    print("\n" + "=" * 75)
    total = PASS + FAIL
    print(f"결과: {PASS}/{total} PASS ({100*PASS/total:.1f}%)")
    if FAIL:
        print("\n실패 상세:")
        for d in DETAILS:
            print(f"  - {d}")
    print("=" * 75)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
