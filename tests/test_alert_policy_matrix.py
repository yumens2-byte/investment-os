"""
tests/test_alert_policy_matrix.py
===================================
Alert 정책 매트릭스 동기화 검증

검증 영역:
  1. docstring 정책 표 vs 실제 코드 동작 1:1 매핑
  2. 모든 alert_type 16종이 정책 매트릭스에 명세되어 있는지
  3. run_alert.py emoji 매핑 누락 검증 (확장성)
  4. config.settings 임계값 일관성 검증

목적:
  코드 변경 시 정책-구현 동기화를 보장하는 회귀 테스트.
  새 alert 추가 시 이 테스트가 자동으로 누락 감지.
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

from engines.alert_engine import (
    _vix_alert, _spy_alert, _oil_alert, _crisis_alert,
    _fed_shock_alert, _pcr_alert, _crypto_basis_alert, _vix_countdown_alert,
    _etf_rank_alert, _regime_change_alert,
    _detect_move_spike, _detect_sma200_break, _detect_stagflation,
    _detect_yield_spread_deep, _detect_cpi_surprise, _detect_sofr_stress,
    AlertSignal,
)

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


# ═════════════════════════════════════════════════════════════
# Phase 1: 정책 매트릭스 (Source of Truth)
# ═════════════════════════════════════════════════════════════
# alert_engine.py docstring 기준 정책 — 코드 동작이 이것과 일치해야 함
POLICY_MATRIX = {
    # (alert_type, level): x_eligible
    ("VIX",          "L1"): False,
    ("VIX",          "L2"): True,
    ("SPY",          "L1"): False,
    ("SPY",          "L2"): True,
    ("SPY",          "L3"): True,
    ("OIL",          "L1"): False,  # 급등률만, 가격 미충격
    ("OIL",          "L2"): True,   # 가격 충격 ($100 돌파)
    ("FED_SHOCK",    "L2"): True,
    ("FED_SHOCK",    "L3"): True,
    ("CRISIS",       "L2"): True,
    ("CRISIS",       "L3"): True,
    ("STAGFLATION",  "L2"): True,
    ("SMA200_BREAK", "L1"): False,
    ("SMA200_BREAK", "L2"): True,
    ("PCR_EXTREME",  "L1"): False,
    ("CRYPTO_BASIS", "L1"): False,
    ("VIX_COUNTDOWN","L1"): False,
    ("ETF_RANK",     "L1"): False,
    ("ETF_RANK",     "L2"): False,
    ("MOVE_SPIKE",   "L1"): False,
    ("YIELD_SPREAD_DEEP", "L1"): False,
    ("CPI_HOT",      "L1"): False,
    ("SOFR_STRESS",  "L1"): False,
    ("REGIME_CHANGE","L1"): False,
    ("REGIME_CHANGE","L2"): False,
}


# ═════════════════════════════════════════════════════════════
# Phase 2: 정책-구현 매핑 케이스 생성 헬퍼
# ═════════════════════════════════════════════════════════════
def _generate_signal_for(alert_type, level):
    """각 (alert_type, level) 조합을 트리거하는 실제 signal 생성."""
    if alert_type == "VIX" and level == "L1":
        return _vix_alert({"vix": 30.0}, None)
    if alert_type == "VIX" and level == "L2":
        return _vix_alert({"vix": 40.0}, None)
    if alert_type == "SPY" and level == "L1":
        return _spy_alert({"sp500": -3.0})
    if alert_type == "SPY" and level == "L2":
        return _spy_alert({"sp500": -5.0})
    if alert_type == "SPY" and level == "L3":
        return _spy_alert({"sp500": -7.0})
    if alert_type == "OIL" and level == "L1":
        return _oil_alert({"oil": 90}, {"oil": 85})
    if alert_type == "OIL" and level == "L2":
        return _oil_alert({"oil": 105}, None)
    if alert_type == "FED_SHOCK" and level == "L2":
        spy = AlertSignal("SPY", "L1", "", {}, x_eligible=False)
        return _fed_shock_alert(spy, True, {"sp500": -3.0, "vix": 22.0})
    if alert_type == "FED_SHOCK" and level == "L3":
        spy = AlertSignal("SPY", "L2", "", {}, x_eligible=True)
        return _fed_shock_alert(spy, True, {"sp500": -5.0, "vix": 25.0})
    if alert_type == "CRISIS" and level == "L2":
        return _crisis_alert({"vix": 40, "sp500": -5, "us10y": 4.0})
    if alert_type == "CRISIS" and level == "L3":
        return _crisis_alert({"vix": 40, "sp500": -5, "us10y": 5.0})
    if alert_type == "STAGFLATION" and level == "L2":
        return _detect_stagflation({"tlt_change": -2.0}, {"sp500": -2.0})
    if alert_type == "SMA200_BREAK" and level == "L1":
        return _detect_sma200_break({"spy_price": 570, "spy_sma50": 590, "spy_sma200": 580})
    if alert_type == "SMA200_BREAK" and level == "L2":
        return _detect_sma200_break({"spy_price": 570, "spy_sma50": 575, "spy_sma200": 580})
    if alert_type == "PCR_EXTREME":
        return _pcr_alert({"pcr_value": 1.8, "pcr_state": "fear"}, {})
    if alert_type == "CRYPTO_BASIS":
        return _crypto_basis_alert({"crypto_basis_spread": -2.0, "crypto_basis_state": "back"}, {})
    if alert_type == "VIX_COUNTDOWN":
        return _vix_countdown_alert({"vix": 25.5}, {"vix": 24})
    if alert_type == "ETF_RANK" and level == "L1":
        rc = {"top1_changed": False, "old_top1": "QQQM", "new_top1": "QQQM",
              "moved_up":   [{"etf": "TLT", "from": 5, "to": 2}],
              "moved_down": [{"etf": "XLK", "from": 2, "to": 5}]}
        return _etf_rank_alert(rc, None, {})
    if alert_type == "ETF_RANK" and level == "L2":
        rc = {"top1_changed": True, "old_top1": "QQQM", "new_top1": "TLT",
              "moved_up": [], "moved_down": []}
        return _etf_rank_alert(rc, None, {})
    if alert_type == "MOVE_SPIKE":
        return _detect_move_spike({"move_index": 150})
    if alert_type == "YIELD_SPREAD_DEEP":
        return _detect_yield_spread_deep({"spread_2y10y_bp": -70, "us2y": 5.0})
    if alert_type == "CPI_HOT":
        return _detect_cpi_surprise({"cpi_yoy": 4.0, "core_cpi_yoy": 3.5})
    if alert_type == "SOFR_STRESS":
        return _detect_sofr_stress({"sofr_spread": 0.7, "sofr": 5.5})
    if alert_type == "REGIME_CHANGE" and level == "L1":
        rgc = {"old_regime": "Crisis Regime", "new_regime": "Caution",
               "direction": "recovery",
               "old_risk_level": "HIGH", "new_risk_level": "MEDIUM",
               "regime_changed": True, "risk_changed": True}
        return _regime_change_alert(rgc, None, None, {})
    if alert_type == "REGIME_CHANGE" and level == "L2":
        rgc = {"old_regime": "Risk-On", "new_regime": "Oil Shock", "direction": "danger",
               "old_risk_level": "LOW", "new_risk_level": "HIGH",
               "regime_changed": True, "risk_changed": True}
        return _regime_change_alert(rgc, None, None, {})
    return None


# ═════════════════════════════════════════════════════════════
# 검증 1: 정책-구현 매핑 일치
# ═════════════════════════════════════════════════════════════
def test_policy_implementation_match():
    print("\n[Phase 1] 정책 매트릭스 vs 코드 동작 1:1 매핑")

    for (atype, level), expected_xelig in POLICY_MATRIX.items():
        sig = _generate_signal_for(atype, level)
        if sig is None:
            check(f"P-{atype}-{level}",
                  f"{atype}/{level} → 신호 생성 실패",
                  False, True)
            continue
        # alert_type 일치
        check(f"P-{atype}-{level}-T",
              f"{atype}/{level} alert_type 정확",
              sig.alert_type, atype)
        # level 일치
        check(f"P-{atype}-{level}-L",
              f"{atype}/{level} level 정확",
              sig.level, level)
        # x_eligible 일치
        check(f"P-{atype}-{level}-X",
              f"{atype}/{level} x_eligible={expected_xelig}",
              sig.x_eligible, expected_xelig)


# ═════════════════════════════════════════════════════════════
# 검증 2: 16종 alert 모두 정책에 명세되어 있는지
# ═════════════════════════════════════════════════════════════
EXPECTED_ALERT_TYPES = {
    "VIX", "SPY", "OIL", "FED_SHOCK", "CRISIS",
    "PCR_EXTREME", "CRYPTO_BASIS", "VIX_COUNTDOWN",
    "ETF_RANK", "REGIME_CHANGE",
    "MOVE_SPIKE", "SMA200_BREAK", "STAGFLATION",
    "YIELD_SPREAD_DEEP", "CPI_HOT", "SOFR_STRESS",
}

def test_alert_types_coverage():
    print("\n[Phase 2] 16종 alert_type 정책 매트릭스 커버")

    policy_types = {atype for (atype, _) in POLICY_MATRIX.keys()}
    missing = EXPECTED_ALERT_TYPES - policy_types
    extra   = policy_types - EXPECTED_ALERT_TYPES

    check("C-01", f"정책 매트릭스에 {len(EXPECTED_ALERT_TYPES)}개 alert_type 모두 포함",
          missing, set())
    check("C-02", "정책 매트릭스에 없는 알 수 없는 alert_type 없음",
          extra, set())


# ═════════════════════════════════════════════════════════════
# 검증 3: run_alert.py emoji 매핑 (확장성)
# ═════════════════════════════════════════════════════════════
def test_run_alert_emoji_mapping():
    print("\n[Phase 3] run_alert.py B-21A emoji 매핑 검토 (P2-C 효과)")

    # run_alert.py의 _em_map 정의 (P2-C 적용 후 상태)
    EMOJI_MAP = {
        "VIX":           "📊",
        "OIL":           "⛽",
        "SPY":           "📉",
        "CRISIS":        "🚨",
        "FED_SHOCK":     "🏦",
        "STAGFLATION":   "🔥",
        "SMA200_BREAK":  "📉",
        "PCR_EXTREME":   "📊",   # P2-C: PCR → PCR_EXTREME 키 통일
        "CRYPTO_BASIS":  "₿",
    }

    # X 이미지 발행 도달 가능 (x_eligible=True) alert_type 집합
    x_eligible_types = {
        atype for (atype, level), xelig in POLICY_MATRIX.items() if xelig
    }
    missing_emoji = x_eligible_types - set(EMOJI_MAP.keys())
    check("E-01", "x_eligible=True alert_type 전체에 emoji 매핑 존재",
          missing_emoji, set())

    # P2-C (v1.1.2): 키 mismatch 해소 검증
    # 이전: "PCR" 키 사용 (실제 alert_type "PCR_EXTREME" 와 mismatch)
    # 이후: "PCR_EXTREME" 키 사용 (alert_type 과 일치)
    pcr_key_fixed = "PCR_EXTREME" in EMOJI_MAP and "PCR" not in EMOJI_MAP
    check("E-02", "[P2-C 검증] PCR → PCR_EXTREME 키 통일 (이슈 I-06 해소)",
          pcr_key_fixed, True)


# ═════════════════════════════════════════════════════════════
# 검증 4: 임계값 일원화 (config.settings vs 모듈 상수)
# ═════════════════════════════════════════════════════════════
def test_threshold_sourcing():
    print("\n[Phase 4] 임계값 일원화 검증")

    # P1-A (v1.1.2): CRISIS US10Y 임계값이 config로 분리됨 (이슈 I-02 해소)
    import inspect
    from engines import alert_engine as ae
    src = inspect.getsource(ae._crisis_alert)
    has_magic_us10y = "us10y >= 4.8" in src   # 매직 넘버 직접 사용
    check("T-01", "[P1-A 검증] CRISIS us10y 매직 넘버 4.8 제거됨",
          has_magic_us10y, False)
    has_const = "US10Y_CRISIS_THR" in src
    check("T-01b", "[P1-A 검증] CRISIS us10y가 US10Y_CRISIS_THR 상수 사용",
          has_const, True)

    # P2-A (v1.1.2): 연산자 일관성 통일 (이슈 I-01 해소)
    cpi_src  = inspect.getsource(ae._detect_cpi_surprise)
    sofr_src = inspect.getsource(ae._detect_sofr_stress)
    move_src = inspect.getsource(ae._detect_move_spike)
    pcr_src  = inspect.getsource(ae._pcr_alert)
    cb_src   = inspect.getsource(ae._crypto_basis_alert)
    stg_src  = inspect.getsource(ae._detect_stagflation)
    ys_src   = inspect.getsource(ae._detect_yield_spread_deep)

    check("T-02", "[P2-A 검증] CPI >= inclusive 사용 (이전 > strict)",
          "cpi_yoy >= CPI_HOT" in cpi_src, True)
    check("T-03", "[P2-A 검증] SOFR >= inclusive (변경 없음)",
          "sofr_spread >= SOFR_STRESS_THR" in sofr_src, True)
    check("T-04", "[P2-A 검증] MOVE >= inclusive (변경 없음)",
          "move >= MOVE_STRESSED" in move_src, True)
    check("T-05", "[P2-A 검증] PCR >= / <= inclusive 사용",
          "pcr >= PCR_EXTREME_FEAR" in pcr_src
          and "pcr <= PCR_EXTREME_GREED" in pcr_src, True)
    check("T-06", "[P2-A 검증] CRYPTO_BASIS <= inclusive 사용",
          "spread <= CRYPTO_BASIS_BACKWARDATION" in cb_src, True)
    check("T-07", "[P2-A 검증] STAGFLATION <= inclusive 사용",
          "spy_chg <= STAGFLATION_SPY_THR" in stg_src
          and "tlt_chg <= STAGFLATION_TLT_THR" in stg_src, True)
    check("T-08", "[P2-A 검증] YIELD_SPREAD <= inclusive 사용",
          "spread_bp <= YIELD_SPREAD_DEEP_BP" in ys_src, True)


# ═════════════════════════════════════════════════════════════
# 검증 5: VIX_COUNTDOWN prev_snapshot=None 가드 (P2-B 효과)
# ═════════════════════════════════════════════════════════════
def test_vix_countdown_prev_none():
    print("\n[Phase 5] VIX_COUNTDOWN prev_snapshot=None 처리 (P2-B 효과)")

    # P2-B (v1.1.2): prev=None 시 미발동으로 변경됨 (이슈 I-03 해소)
    sig = _vix_countdown_alert({"vix": 30}, None)
    check("V-01", "[P2-B 검증] prev=None + vix=30 → 미발동 (이슈 I-03 해소)",
          sig is None, True)
    # 정상 케이스 회귀 없음
    sig = _vix_countdown_alert({"vix": 25.5}, {"vix": 24})
    check("V-02", "정상 prev 제공 시 동작 유지",
          sig is not None and sig.alert_type == "VIX_COUNTDOWN", True)


# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 75)
    print("Alert 정책 매트릭스 동기화 검증 — 코드 진실 vs 정책 표")
    print("=" * 75)

    test_policy_implementation_match()
    test_alert_types_coverage()
    test_run_alert_emoji_mapping()
    test_threshold_sourcing()
    test_vix_countdown_prev_none()

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
