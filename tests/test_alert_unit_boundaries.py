"""
tests/test_alert_unit_boundaries.py
=====================================
Alert 16종 단위 임계값 / 경계값 / 분기 전수 테스트

검증 원칙:
  - 각 alert 함수의 임계값 미만 / 정확히 / 초과 동작 검증
  - level / x_eligible / alert_type 매핑 검증
  - None / 누락 입력 안전성 검증
  - 격상 조건 (VIX surge, OIL price+surge) 검증

정책 출처: engines/alert_engine.py docstring + 코드 진실
의존성: 외부 모킹 없음 — 순수 함수 단위 테스트
"""
import os
import sys
from pathlib import Path

# 환경변수 mock
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("LOG_LEVEL", "ERROR")
os.environ.setdefault("FRED_API_KEY", "test")
os.environ.setdefault("X_API_KEY", "test")
os.environ.setdefault("X_API_SECRET", "test")
os.environ.setdefault("X_ACCESS_TOKEN", "test")
os.environ.setdefault("X_ACCESS_TOKEN_SECRET", "test")

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.alert_engine import (
    _vix_alert, _spy_alert, _oil_alert,
    _fed_shock_alert, _crisis_alert,
    _pcr_alert, _crypto_basis_alert, _vix_countdown_alert,
    _etf_rank_alert, _regime_change_alert,
    _detect_move_spike, _detect_sma200_break, _detect_stagflation,
    _detect_yield_spread_deep, _detect_cpi_surprise, _detect_sofr_stress,
    AlertSignal,
    VIX_L1, VIX_L2, VIX_SURGE_PCT,
    SPY_L1, SPY_L2, SPY_L3,
    OIL_SHOCK_PRICE, OIL_SURGE_PCT,
    PCR_EXTREME_FEAR, PCR_EXTREME_GREED,
    CRYPTO_BASIS_BACKWARDATION,
    VIX_COUNTDOWN_LEVELS,
)
from config.settings import (
    MOVE_STRESSED,
    STAGFLATION_SPY_THR, STAGFLATION_TLT_THR,
    YIELD_SPREAD_DEEP_BP,
    CPI_HOT,
    SOFR_STRESS_THR,
)

# ─────────────────────────────────────────────────────────────
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

def assert_signal(case_id, desc, sig, expected_type=None, expected_level=None,
                  expected_xelig=None, expected_none=False):
    """AlertSignal 객체 또는 None 검증."""
    if expected_none:
        check(case_id, desc, sig is None, True)
        return
    if sig is None:
        check(case_id, f"{desc} (sig=None, expected signal)", False, True)
        return
    ok = True
    parts = []
    if expected_type is not None:
        parts.append(f"type={sig.alert_type}")
        if sig.alert_type != expected_type: ok = False
    if expected_level is not None:
        parts.append(f"level={sig.level}")
        if sig.level != expected_level: ok = False
    if expected_xelig is not None:
        parts.append(f"x_elig={sig.x_eligible}")
        if sig.x_eligible != expected_xelig: ok = False
    check(case_id, f"{desc} → {' '.join(parts)}", ok, True)


# ═════════════════════════════════════════════════════════════
# 1. VIX Alert (_vix_alert)
# ═════════════════════════════════════════════════════════════
def test_vix():
    print(f"\n[VIX] 임계값: L1={VIX_L1}, L2={VIX_L2}, surge={VIX_SURGE_PCT}%")
    # 경계값
    assert_signal("VIX-01", "vix=0 (수집 실패)", _vix_alert({"vix": 0}, None), expected_none=True)
    assert_signal("VIX-02", "vix=-1 (이상값)", _vix_alert({"vix": -1}, None), expected_none=True)
    assert_signal("VIX-03", "vix=27.9 (L1 미만)", _vix_alert({"vix": 27.9}, None), expected_none=True)
    assert_signal("VIX-04", "vix=28.0 (L1 경계 inclusive)",
                  _vix_alert({"vix": 28.0}, None), "VIX", "L1", False)
    assert_signal("VIX-05", "vix=30.0 (L1 중간)",
                  _vix_alert({"vix": 30.0}, None), "VIX", "L1", False)
    assert_signal("VIX-06", "vix=34.9 (L1 상단)",
                  _vix_alert({"vix": 34.9}, None), "VIX", "L1", False)
    assert_signal("VIX-07", "vix=35.0 (L2 경계 inclusive)",
                  _vix_alert({"vix": 35.0}, None), "VIX", "L2", True)
    assert_signal("VIX-08", "vix=50.0 (L2 극단)",
                  _vix_alert({"vix": 50.0}, None), "VIX", "L2", True)

    # 격상 조건: L1 + surge 15%+ → L2
    assert_signal("VIX-10", "vix=29 + surge 16% → L2 격상",
                  _vix_alert({"vix": 29.0}, {"vix": 25.0}), "VIX", "L2", True)
    assert_signal("VIX-11", "vix=29 + surge 14% → L1 유지",
                  _vix_alert({"vix": 29.0}, {"vix": 25.5}), "VIX", "L1", False)
    assert_signal("VIX-12", "vix=29 + surge 정확히 15% → L2 격상",
                  _vix_alert({"vix": 28.75}, {"vix": 25.0}), "VIX", "L2", True)
    assert_signal("VIX-13", "vix=40 + surge 20% → L2 (격상 무관)",
                  _vix_alert({"vix": 40.0}, {"vix": 33.0}), "VIX", "L2", True)
    assert_signal("VIX-14", "vix=27 + surge 20% (L1 미만) → None",
                  _vix_alert({"vix": 27.0}, {"vix": 22.5}), expected_none=True)
    assert_signal("VIX-15", "prev_snapshot=None → 격상 체크 안 함",
                  _vix_alert({"vix": 30.0}, None), "VIX", "L1", False)
    assert_signal("VIX-16", "prev_vix=0 → 격상 체크 안 함",
                  _vix_alert({"vix": 30.0}, {"vix": 0}), "VIX", "L1", False)


# ═════════════════════════════════════════════════════════════
# 2. SPY Alert (_spy_alert)
# ═════════════════════════════════════════════════════════════
def test_spy():
    print(f"\n[SPY] 임계값: L1={SPY_L1}, L2={SPY_L2}, L3={SPY_L3}")
    assert_signal("SPY-01", "sp500=0 (변동 없음)", _spy_alert({"sp500": 0}), expected_none=True)
    assert_signal("SPY-02", "sp500=+0.5% (상승)", _spy_alert({"sp500": 0.5}), expected_none=True)
    assert_signal("SPY-03", "sp500=-2.4% (L1 미만)", _spy_alert({"sp500": -2.4}), expected_none=True)
    assert_signal("SPY-04", "sp500=-2.5% (L1 경계 inclusive)",
                  _spy_alert({"sp500": -2.5}), "SPY", "L1", False)
    assert_signal("SPY-05", "sp500=-3.0% (L1 중간)",
                  _spy_alert({"sp500": -3.0}), "SPY", "L1", False)
    assert_signal("SPY-06", "sp500=-3.99% (L1 상단)",
                  _spy_alert({"sp500": -3.99}), "SPY", "L1", False)
    assert_signal("SPY-07", "sp500=-4.0% (L2 경계 inclusive)",
                  _spy_alert({"sp500": -4.0}), "SPY", "L2", True)
    assert_signal("SPY-08", "sp500=-5.5% (L2 중간)",
                  _spy_alert({"sp500": -5.5}), "SPY", "L2", True)
    assert_signal("SPY-09", "sp500=-5.99% (L2 상단)",
                  _spy_alert({"sp500": -5.99}), "SPY", "L2", True)
    assert_signal("SPY-10", "sp500=-6.0% (L3 경계 inclusive)",
                  _spy_alert({"sp500": -6.0}), "SPY", "L3", True)
    assert_signal("SPY-11", "sp500=-10% (L3 극단)",
                  _spy_alert({"sp500": -10.0}), "SPY", "L3", True)


# ═════════════════════════════════════════════════════════════
# 3. OIL Alert (_oil_alert)
# ═════════════════════════════════════════════════════════════
def test_oil():
    print(f"\n[OIL] 임계값: 가격={OIL_SHOCK_PRICE}, surge={OIL_SURGE_PCT}%")
    assert_signal("OIL-01", "oil=0", _oil_alert({"oil": 0}, None), expected_none=True)
    assert_signal("OIL-02", "oil=80 + prev=78 (둘 다 미충족)",
                  _oil_alert({"oil": 80}, {"oil": 78}), expected_none=True)
    assert_signal("OIL-03", "oil=99.9 (가격 미충족) + surge 없음",
                  _oil_alert({"oil": 99.9}, None), expected_none=True)
    assert_signal("OIL-04", "oil=100.0 (가격충격 경계 inclusive)",
                  _oil_alert({"oil": 100.0}, None), "OIL", "L2", True)
    assert_signal("OIL-05", "oil=120 (가격충격)",
                  _oil_alert({"oil": 120.0}, None), "OIL", "L2", True)
    # surge only (L1)
    assert_signal("OIL-06", "oil=83 + prev=80 (+3.75% < 4%)",
                  _oil_alert({"oil": 83}, {"oil": 80}), expected_none=True)
    assert_signal("OIL-07", "oil=83.2 + prev=80 (+4.0% 경계 inclusive)",
                  _oil_alert({"oil": 83.2}, {"oil": 80}), "OIL", "L1", False)
    assert_signal("OIL-08", "oil=90 + prev=80 (+12.5% surge)",
                  _oil_alert({"oil": 90}, {"oil": 80}), "OIL", "L1", False)
    # 가격+surge 동시
    assert_signal("OIL-09", "oil=110 + prev=100 (+10% surge + 가격충격)",
                  _oil_alert({"oil": 110}, {"oil": 100}), "OIL", "L2", True)
    # prev_snapshot=None → surge 체크 안 함
    assert_signal("OIL-10", "oil=85 + prev=None",
                  _oil_alert({"oil": 85}, None), expected_none=True)
    assert_signal("OIL-11", "oil=85 + prev.oil=0",
                  _oil_alert({"oil": 85}, {"oil": 0}), expected_none=True)


# ═════════════════════════════════════════════════════════════
# 4. FED_SHOCK Alert
# ═════════════════════════════════════════════════════════════
def test_fed_shock():
    print(f"\n[FED_SHOCK] SPY 급락 + Fed키워드 AND 조건")
    # SPY alert 없거나 Fed 미감지 시 None
    assert_signal("FED-01", "spy_sig=None",
                  _fed_shock_alert(None, True, {"sp500": -3, "vix": 30}), expected_none=True)
    assert_signal("FED-02", "fed_detected=False",
                  _fed_shock_alert(AlertSignal("SPY","L1","",{}, x_eligible=False),
                                   False, {"sp500": -3, "vix": 30}), expected_none=True)
    # SPY L2 이상 + Fed → L3
    spy_l2 = AlertSignal("SPY", "L2", "", {}, x_eligible=True)
    assert_signal("FED-03", "SPY -4.5% + Fed → L3",
                  _fed_shock_alert(spy_l2, True, {"sp500": -4.5, "vix": 20}),
                  "FED_SHOCK", "L3", True)
    # SPY L1 + VIX>=L1 + Fed → L3
    spy_l1 = AlertSignal("SPY", "L1", "", {}, x_eligible=False)
    assert_signal("FED-04", "SPY -3% + VIX 28 + Fed → L3 (복합)",
                  _fed_shock_alert(spy_l1, True, {"sp500": -3.0, "vix": 28.0}),
                  "FED_SHOCK", "L3", True)
    # SPY L1 + VIX<L1 + Fed → L2
    assert_signal("FED-05", "SPY -3% + VIX 25 + Fed → L2",
                  _fed_shock_alert(spy_l1, True, {"sp500": -3.0, "vix": 25.0}),
                  "FED_SHOCK", "L2", True)


# ═════════════════════════════════════════════════════════════
# 5. CRISIS Alert (_crisis_alert)
# ═════════════════════════════════════════════════════════════
def test_crisis():
    print(f"\n[CRISIS] VIX>=35, SPY<=-4, US10Y>=4.8 카운트")
    assert_signal("CRS-01", "3개 모두 미충족",
                  _crisis_alert({"vix": 20, "sp500": -1, "us10y": 4.0}), expected_none=True)
    assert_signal("CRS-02", "1개만 (VIX) → None",
                  _crisis_alert({"vix": 36, "sp500": -1, "us10y": 4.0}), expected_none=True)
    # 2개 → L2
    assert_signal("CRS-03", "VIX+SPY 2개 → L2",
                  _crisis_alert({"vix": 36, "sp500": -4.5, "us10y": 4.0}),
                  "CRISIS", "L2", True)
    assert_signal("CRS-04", "VIX+US10Y 2개 → L2",
                  _crisis_alert({"vix": 36, "sp500": -1, "us10y": 4.8}),
                  "CRISIS", "L2", True)
    assert_signal("CRS-05", "SPY+US10Y 2개 → L2",
                  _crisis_alert({"vix": 20, "sp500": -4.5, "us10y": 4.85}),
                  "CRISIS", "L2", True)
    # 3개 → L3
    assert_signal("CRS-06", "3개 동시 → L3",
                  _crisis_alert({"vix": 40, "sp500": -5, "us10y": 5.0}),
                  "CRISIS", "L3", True)
    # 경계값
    assert_signal("CRS-07", "VIX=35 정확히 + SPY=-4 정확히 → L2 (둘 다 카운트)",
                  _crisis_alert({"vix": 35.0, "sp500": -4.0, "us10y": 4.0}),
                  "CRISIS", "L2", True)
    assert_signal("CRS-08", "US10Y=4.8 정확히 + 1개 더 → L2",
                  _crisis_alert({"vix": 35, "sp500": -1, "us10y": 4.8}),
                  "CRISIS", "L2", True)


# ═════════════════════════════════════════════════════════════
# 6. PCR Alert (_pcr_alert)
# ═════════════════════════════════════════════════════════════
def test_pcr():
    print(f"\n[PCR] 임계값: extreme_fear>{PCR_EXTREME_FEAR}, extreme_greed<{PCR_EXTREME_GREED}")
    assert_signal("PCR-01", "pcr=0 (수집 실패)",
                  _pcr_alert({"pcr_value": 0}, {}), expected_none=True)
    assert_signal("PCR-02", "pcr=None",
                  _pcr_alert({"pcr_value": None}, {}), expected_none=True)
    assert_signal("PCR-03", "pcr=0.8 (정상 범위)",
                  _pcr_alert({"pcr_value": 0.8}, {}), expected_none=True)
    # 극단 공포
    # P2-A (v1.1.2): strict > → inclusive >= 변경. 경계값 1.5 정확 시 발동
    assert_signal("PCR-04", "pcr=1.5 (경계 inclusive, v1.1.2+) → L1 발동",
                  _pcr_alert({"pcr_value": 1.5}, {}), "PCR_EXTREME", "L1", False)
    assert_signal("PCR-05", "pcr=1.51 → 극단 공포 L1",
                  _pcr_alert({"pcr_value": 1.51}, {}), "PCR_EXTREME", "L1", False)
    assert_signal("PCR-06", "pcr=2.0 → 극단 공포 L1",
                  _pcr_alert({"pcr_value": 2.0}, {}), "PCR_EXTREME", "L1", False)
    # 극단 탐욕
    # P2-A: < → <= 변경. 경계값 0.5 정확 시 발동
    assert_signal("PCR-07", "pcr=0.5 (경계 inclusive, v1.1.2+) → L1 발동",
                  _pcr_alert({"pcr_value": 0.5}, {}), "PCR_EXTREME", "L1", False)
    assert_signal("PCR-08", "pcr=0.49 → 극단 탐욕 L1",
                  _pcr_alert({"pcr_value": 0.49}, {}), "PCR_EXTREME", "L1", False)
    assert_signal("PCR-09", "pcr=0.3 → 극단 탐욕 L1",
                  _pcr_alert({"pcr_value": 0.3}, {}), "PCR_EXTREME", "L1", False)


# ═════════════════════════════════════════════════════════════
# 7. CRYPTO_BASIS Alert
# ═════════════════════════════════════════════════════════════
def test_crypto_basis():
    print(f"\n[CRYPTO_BASIS] 임계값: backwardation<{CRYPTO_BASIS_BACKWARDATION}%")
    assert_signal("CB-01", "spread=None",
                  _crypto_basis_alert({}, {}), expected_none=True)
    assert_signal("CB-02", "spread=+0.5% (정상)",
                  _crypto_basis_alert({"crypto_basis_spread": 0.5}, {}), expected_none=True)
    assert_signal("CB-03", "spread=-0.5% (정상 범위)",
                  _crypto_basis_alert({"crypto_basis_spread": -0.5}, {}), expected_none=True)
    # P2-A (v1.1.2): strict < → inclusive <= 변경
    assert_signal("CB-04", "spread=-1.0 (경계 inclusive, v1.1.2+) → L1 발동",
                  _crypto_basis_alert({"crypto_basis_spread": -1.0}, {}),
                  "CRYPTO_BASIS", "L1", False)
    assert_signal("CB-05", "spread=-1.01 → L1",
                  _crypto_basis_alert({"crypto_basis_spread": -1.01}, {}),
                  "CRYPTO_BASIS", "L1", False)
    assert_signal("CB-06", "spread=-3.0 (극단)",
                  _crypto_basis_alert({"crypto_basis_spread": -3.0}, {}),
                  "CRYPTO_BASIS", "L1", False)


# ═════════════════════════════════════════════════════════════
# 8. VIX_COUNTDOWN
# ═════════════════════════════════════════════════════════════
def test_vix_countdown():
    print(f"\n[VIX_COUNTDOWN] 레벨: {VIX_COUNTDOWN_LEVELS}")
    assert_signal("VCD-01", "vix=24 (어떤 레벨도 미달)",
                  _vix_countdown_alert({"vix": 24}, {"vix": 20}), expected_none=True)
    assert_signal("VCD-02", "vix=25 정확히 + prev=24 (신규 돌파)",
                  _vix_countdown_alert({"vix": 25}, {"vix": 24}),
                  "VIX_COUNTDOWN", "L1", False)
    assert_signal("VCD-03", "vix=26 + prev=25 (이미 25 돌파 상태)",
                  _vix_countdown_alert({"vix": 26}, {"vix": 25}), expected_none=True)
    assert_signal("VCD-04", "vix=27 + prev=26 (신규 27 돌파)",
                  _vix_countdown_alert({"vix": 27}, {"vix": 26}),
                  "VIX_COUNTDOWN", "L1", False)
    assert_signal("VCD-05", "vix=29.5 + prev=27 (신규 29 돌파)",
                  _vix_countdown_alert({"vix": 29.5}, {"vix": 27}),
                  "VIX_COUNTDOWN", "L1", False)
    assert_signal("VCD-06", "vix=0",
                  _vix_countdown_alert({"vix": 0}, None), expected_none=True)
    # P2-B (v1.1.2): prev_snapshot=None 안전 가드 추가 — 이전엔 발동, 이후 미발동
    assert_signal("VCD-07", "vix=26 + prev=None → 미발동 (P2-B 가드, v1.1.2+)",
                  _vix_countdown_alert({"vix": 26}, None), expected_none=True)


# ═════════════════════════════════════════════════════════════
# 9. ETF_RANK
# ═════════════════════════════════════════════════════════════
def test_etf_rank():
    print(f"\n[ETF_RANK]")
    assert_signal("ETF-01", "rank_change=None",
                  _etf_rank_alert(None, None, {}), expected_none=True)
    # Top1 변경 → L2
    rc = {"top1_changed": True, "old_top1": "QQQM", "new_top1": "TLT",
          "moved_up": [], "moved_down": []}
    assert_signal("ETF-02", "Top1 변경 → L2",
                  _etf_rank_alert(rc, None, {}), "ETF_RANK", "L2", False)
    # Top3 내 교체 → L1
    rc2 = {"top1_changed": False, "old_top1": "QQQM", "new_top1": "QQQM",
           "moved_up":   [{"etf": "TLT", "from": 5, "to": 2}],
           "moved_down": [{"etf": "XLK", "from": 2, "to": 5}]}
    assert_signal("ETF-03", "Top3 변동 → L1",
                  _etf_rank_alert(rc2, None, {}), "ETF_RANK", "L1", False)
    # 하위만 변동 → None
    rc3 = {"top1_changed": False, "old_top1": "QQQM", "new_top1": "QQQM",
           "moved_up":   [{"etf": "ITA",  "from": 6, "to": 5}],
           "moved_down": [{"etf": "XLE",  "from": 5, "to": 6}]}
    assert_signal("ETF-04", "하위만 변동 → None",
                  _etf_rank_alert(rc3, None, {}), expected_none=True)


# ═════════════════════════════════════════════════════════════
# 10. REGIME_CHANGE
# ═════════════════════════════════════════════════════════════
def test_regime():
    print(f"\n[REGIME_CHANGE]")
    assert_signal("RGM-01", "regime_change=None",
                  _regime_change_alert(None, None, None, {}), expected_none=True)
    # Shock 진입
    rc = {"old_regime": "Risk-On", "new_regime": "Oil Shock", "direction": "danger",
          "old_risk_level": "LOW", "new_risk_level": "HIGH",
          "regime_changed": True, "risk_changed": True}
    assert_signal("RGM-02", "Shock 진입 → L2",
                  _regime_change_alert(rc, None, None, {}),
                  "REGIME_CHANGE", "L2", False)
    # danger 방향 (Shock 아닌)
    rc2 = {"old_regime": "Risk-On", "new_regime": "Caution", "direction": "danger",
           "old_risk_level": "LOW", "new_risk_level": "MEDIUM",
           "regime_changed": True, "risk_changed": True}
    assert_signal("RGM-03", "danger 방향 → L2",
                  _regime_change_alert(rc2, None, None, {}),
                  "REGIME_CHANGE", "L2", False)
    # recovery 방향
    rc3 = {"old_regime": "Crisis Regime", "new_regime": "Caution", "direction": "recovery",
           "old_risk_level": "HIGH", "new_risk_level": "MEDIUM",
           "regime_changed": True, "risk_changed": True}
    assert_signal("RGM-04", "recovery 방향 → L1",
                  _regime_change_alert(rc3, None, None, {}),
                  "REGIME_CHANGE", "L1", False)
    # Risk만 변경
    rc4 = {"old_regime": "Caution", "new_regime": "Caution", "direction": "danger",
           "old_risk_level": "MEDIUM", "new_risk_level": "HIGH",
           "regime_changed": False, "risk_changed": True}
    assert_signal("RGM-05", "Risk만 변경 → L1",
                  _regime_change_alert(rc4, None, None, {}),
                  "REGIME_CHANGE", "L1", False)


# ═════════════════════════════════════════════════════════════
# 11. MOVE_SPIKE
# ═════════════════════════════════════════════════════════════
def test_move():
    print(f"\n[MOVE_SPIKE] 임계값: {MOVE_STRESSED}")
    assert_signal("MV-01", "tier2={}", _detect_move_spike({}), expected_none=True)
    assert_signal("MV-02", "move=None", _detect_move_spike({"move_index": None}), expected_none=True)
    assert_signal("MV-03", "move=120", _detect_move_spike({"move_index": 120}), expected_none=True)
    assert_signal("MV-04", f"move={MOVE_STRESSED} 경계 inclusive",
                  _detect_move_spike({"move_index": MOVE_STRESSED}),
                  "MOVE_SPIKE", "L1", False)
    assert_signal("MV-05", "move=160",
                  _detect_move_spike({"move_index": 160}),
                  "MOVE_SPIKE", "L1", False)


# ═════════════════════════════════════════════════════════════
# 12. SMA200_BREAK
# ═════════════════════════════════════════════════════════════
def test_sma200():
    print(f"\n[SMA200_BREAK]")
    assert_signal("SMA-01", "데이터 None",
                  _detect_sma200_break(None), expected_none=True)
    assert_signal("SMA-02", "price 누락",
                  _detect_sma200_break({"spy_sma50": 600, "spy_sma200": 580}), expected_none=True)
    # 정상 (200선 상회)
    assert_signal("SMA-03", "price>sma200 → None",
                  _detect_sma200_break({"spy_price": 600, "spy_sma50": 590, "spy_sma200": 580}),
                  expected_none=True)
    # 200선 이탈 단독 (sma50 > sma200) → L1
    assert_signal("SMA-04", "200선 이탈만 (sma50>sma200) → L1",
                  _detect_sma200_break({"spy_price": 570, "spy_sma50": 590, "spy_sma200": 580}),
                  "SMA200_BREAK", "L1", False)
    # 데스크로스 + 200선 이탈 → L2
    assert_signal("SMA-05", "DC + 이탈 → L2",
                  _detect_sma200_break({"spy_price": 570, "spy_sma50": 575, "spy_sma200": 580}),
                  "SMA200_BREAK", "L2", True)
    # 데스크로스만 (200선 상회) → None
    assert_signal("SMA-06", "DC만 (price>sma200) → None",
                  _detect_sma200_break({"spy_price": 590, "spy_sma50": 575, "spy_sma200": 580}),
                  expected_none=True)


# ═════════════════════════════════════════════════════════════
# 13. STAGFLATION
# ═════════════════════════════════════════════════════════════
def test_stagflation():
    print(f"\n[STAGFLATION] 임계값: SPY<{STAGFLATION_SPY_THR} AND TLT<{STAGFLATION_TLT_THR}")
    assert_signal("STG-01", "tlt=None",
                  _detect_stagflation({}, {"sp500": -2.0}), expected_none=True)
    assert_signal("STG-02", "spy=None",
                  _detect_stagflation({"tlt_change": -2.0}, {}), expected_none=True)
    assert_signal("STG-03", "둘 다 정상",
                  _detect_stagflation({"tlt_change": 0.5}, {"sp500": 0.5}), expected_none=True)
    # P2-A (v1.1.2): strict < → inclusive <= 변경
    assert_signal("STG-04", "spy=-1.0 정확히 + tlt=-2 (경계 inclusive, v1.1.2+) → L2",
                  _detect_stagflation({"tlt_change": -2.0}, {"sp500": -1.0}),
                  "STAGFLATION", "L2", True)
    assert_signal("STG-05", "tlt=-1.0 정확히 + spy=-2 (경계 inclusive, v1.1.2+) → L2",
                  _detect_stagflation({"tlt_change": -1.0}, {"sp500": -2.0}),
                  "STAGFLATION", "L2", True)
    assert_signal("STG-06", "spy=-1.5 + tlt=-1.5 → L2",
                  _detect_stagflation({"tlt_change": -1.5}, {"sp500": -1.5}),
                  "STAGFLATION", "L2", True)
    assert_signal("STG-07", "spy=-3 + tlt=-2 (극단)",
                  _detect_stagflation({"tlt_change": -2.0}, {"sp500": -3.0}),
                  "STAGFLATION", "L2", True)
    # 한쪽만 미충족
    assert_signal("STG-08", "spy=-3 + tlt=+0.5 (TLT 상승) → None",
                  _detect_stagflation({"tlt_change": 0.5}, {"sp500": -3.0}), expected_none=True)


# ═════════════════════════════════════════════════════════════
# 14. YIELD_SPREAD_DEEP
# ═════════════════════════════════════════════════════════════
def test_yield_spread():
    print(f"\n[YIELD_SPREAD_DEEP] 임계값: <{YIELD_SPREAD_DEEP_BP}bp")
    assert_signal("YS-01", "spread=None",
                  _detect_yield_spread_deep({}), expected_none=True)
    assert_signal("YS-02", "spread=+50bp (정상)",
                  _detect_yield_spread_deep({"spread_2y10y_bp": 50}), expected_none=True)
    # P2-A (v1.1.2): strict < → inclusive <= 변경
    assert_signal("YS-03", f"spread={YIELD_SPREAD_DEEP_BP}bp 경계 inclusive (v1.1.2+) → L1",
                  _detect_yield_spread_deep({"spread_2y10y_bp": YIELD_SPREAD_DEEP_BP, "us2y": 5.0}),
                  "YIELD_SPREAD_DEEP", "L1", False)
    assert_signal("YS-04", "spread=-50.1bp → L1",
                  _detect_yield_spread_deep({"spread_2y10y_bp": -50.1}),
                  "YIELD_SPREAD_DEEP", "L1", False)
    assert_signal("YS-05", "spread=-100bp (심화) → L1",
                  _detect_yield_spread_deep({"spread_2y10y_bp": -100, "us2y": 5.2}),
                  "YIELD_SPREAD_DEEP", "L1", False)


# ═════════════════════════════════════════════════════════════
# 15. CPI_HOT
# ═════════════════════════════════════════════════════════════
def test_cpi():
    print(f"\n[CPI_HOT] 임계값: >{CPI_HOT}% (strict)")
    assert_signal("CPI-01", "cpi=None",
                  _detect_cpi_surprise({}), expected_none=True)
    assert_signal("CPI-02", "cpi=2.0% (정상)",
                  _detect_cpi_surprise({"cpi_yoy": 2.0}), expected_none=True)
    # P2-A (v1.1.2): strict > → inclusive >= 변경
    assert_signal("CPI-03", f"cpi={CPI_HOT}% 경계 inclusive (v1.1.2+) → L1",
                  _detect_cpi_surprise({"cpi_yoy": CPI_HOT, "core_cpi_yoy": 3.0}),
                  "CPI_HOT", "L1", False)
    assert_signal("CPI-04", "cpi=3.51% → L1",
                  _detect_cpi_surprise({"cpi_yoy": 3.51, "core_cpi_yoy": 3.0}),
                  "CPI_HOT", "L1", False)
    assert_signal("CPI-05", "cpi=5.0% (극단)",
                  _detect_cpi_surprise({"cpi_yoy": 5.0}),
                  "CPI_HOT", "L1", False)


# ═════════════════════════════════════════════════════════════
# 16. SOFR_STRESS
# ═════════════════════════════════════════════════════════════
def test_sofr():
    print(f"\n[SOFR_STRESS] 임계값: >={SOFR_STRESS_THR}%p")
    assert_signal("SOFR-01", "spread=None",
                  _detect_sofr_stress({}), expected_none=True)
    assert_signal("SOFR-02", "spread=0.1 (정상)",
                  _detect_sofr_stress({"sofr_spread": 0.1}), expected_none=True)
    assert_signal("SOFR-03", f"spread={SOFR_STRESS_THR} 경계 inclusive",
                  _detect_sofr_stress({"sofr_spread": SOFR_STRESS_THR, "sofr": 5.3}),
                  "SOFR_STRESS", "L1", False)
    assert_signal("SOFR-04", "spread=1.0 (극단)",
                  _detect_sofr_stress({"sofr_spread": 1.0, "sofr": 5.5}),
                  "SOFR_STRESS", "L1", False)


# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 75)
    print("Alert 16종 단위 임계값 / 경계값 / 분기 전수 테스트")
    print("=" * 75)

    test_vix()
    test_spy()
    test_oil()
    test_fed_shock()
    test_crisis()
    test_pcr()
    test_crypto_basis()
    test_vix_countdown()
    test_etf_rank()
    test_regime()
    test_move()
    test_sma200()
    test_stagflation()
    test_yield_spread()
    test_cpi()
    test_sofr()

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
