"""
tests/test_p2_pilot.py
========================
P2 그룹 파일럿 테스트 (마스터 승인 대체용)

검증 대상:
  P2-A: I-01 — 연산자 일관성 통일 (PCR, CRYPTO_BASIS, STAGFLATION, YIELD_SPREAD, CPI)
  P2-B: I-03 — VIX_COUNTDOWN prev=None 안전 가드
  P2-C: I-06 — run_alert.py emoji_map 키 통일
  P2-D: 개선6 — 운영 메트릭 요약 로그

원칙:
  - 경계값 정확 일치 시 새로운 정책(inclusive)으로 발동되는지 검증
  - 기존 명백한 케이스는 회귀 없음 검증
  - prev=None 시 VIX_COUNTDOWN 미발동 검증
  - emoji_map 키 PCR_EXTREME 매핑 검증
  - METRICS 로그 포맷 검증
"""
import os
import sys
import io
import logging
from pathlib import Path

os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("LOG_LEVEL", "INFO")  # METRICS 로그 캡처용
os.environ.setdefault("FRED_API_KEY", "test")
os.environ.setdefault("X_API_KEY", "test")
os.environ.setdefault("X_API_SECRET", "test")
os.environ.setdefault("X_ACCESS_TOKEN", "test")
os.environ.setdefault("X_ACCESS_TOKEN_SECRET", "test")

sys.path.insert(0, str(Path(__file__).parent.parent))

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
# P2-A: 연산자 일관성 통일 (5종 alert)
# ═════════════════════════════════════════════════════════════
def test_p2a_operator_consistency():
    print("\n[P2-A] 연산자 일관성 통일 — I-01 해소")
    from engines.alert_engine import (
        _pcr_alert, _crypto_basis_alert,
        _detect_stagflation, _detect_yield_spread_deep, _detect_cpi_surprise,
        PCR_EXTREME_FEAR, PCR_EXTREME_GREED, CRYPTO_BASIS_BACKWARDATION,
    )
    from config.settings import (
        STAGFLATION_SPY_THR, STAGFLATION_TLT_THR,
        YIELD_SPREAD_DEEP_BP, CPI_HOT,
    )

    # ── PCR (이전: > strict, 이후: >= inclusive) ──
    # 경계값 1.5 정확 일치 → 이제 발동
    sig = _pcr_alert({"pcr_value": PCR_EXTREME_FEAR, "pcr_state": "fear"}, {})
    check("P2A-PCR-1", f"pcr=1.5 (경계 inclusive, 신규 정책) → 발동",
          sig is not None and sig.alert_type == "PCR_EXTREME", True)
    # 경계값 0.5 정확 일치 → 이제 발동
    sig = _pcr_alert({"pcr_value": PCR_EXTREME_GREED, "pcr_state": "greed"}, {})
    check("P2A-PCR-2", f"pcr=0.5 (경계 inclusive) → 발동",
          sig is not None and sig.alert_type == "PCR_EXTREME", True)
    # 기존 발동 케이스 회귀 없음
    sig = _pcr_alert({"pcr_value": 1.8, "pcr_state": "fear"}, {})
    check("P2A-PCR-3", "pcr=1.8 (명백한 발동) → 회귀 없음",
          sig is not None, True)
    # 미발동 케이스
    sig = _pcr_alert({"pcr_value": 1.4, "pcr_state": "normal"}, {})
    check("P2A-PCR-4", "pcr=1.4 (임계 미만) → 미발동",
          sig is None, True)

    # ── CRYPTO_BASIS (이전: < strict, 이후: <= inclusive) ──
    sig = _crypto_basis_alert({"crypto_basis_spread": CRYPTO_BASIS_BACKWARDATION,
                                "crypto_basis_state": "back"}, {})
    check("P2A-CB-1", "basis=-1.0 (경계 inclusive) → 발동",
          sig is not None, True)
    sig = _crypto_basis_alert({"crypto_basis_spread": -0.99,
                                "crypto_basis_state": "normal"}, {})
    check("P2A-CB-2", "basis=-0.99 (임계 미달) → 미발동",
          sig is None, True)

    # ── STAGFLATION (이전: < strict, 이후: <= inclusive) ──
    sig = _detect_stagflation(
        {"tlt_change": STAGFLATION_TLT_THR},
        {"sp500": STAGFLATION_SPY_THR}
    )
    check("P2A-STG-1", "spy=-1.0 + tlt=-1.0 (둘 다 경계 inclusive) → 발동",
          sig is not None, True)
    sig = _detect_stagflation(
        {"tlt_change": -0.99},
        {"sp500": -1.0}
    )
    check("P2A-STG-2", "spy=-1.0 + tlt=-0.99 (TLT 미충족) → 미발동",
          sig is None, True)

    # ── YIELD_SPREAD_DEEP (이전: < strict, 이후: <= inclusive) ──
    sig = _detect_yield_spread_deep({"spread_2y10y_bp": YIELD_SPREAD_DEEP_BP, "us2y": 5.0})
    check("P2A-YS-1", "spread=-50bp (경계 inclusive) → 발동",
          sig is not None, True)
    sig = _detect_yield_spread_deep({"spread_2y10y_bp": -49.9})
    check("P2A-YS-2", "spread=-49.9bp (임계 미달) → 미발동",
          sig is None, True)

    # ── CPI_HOT (이전: > strict, 이후: >= inclusive) ──
    sig = _detect_cpi_surprise({"cpi_yoy": CPI_HOT, "core_cpi_yoy": 3.0})
    check("P2A-CPI-1", "cpi=3.5% (경계 inclusive, BLS 발표값 가능성) → 발동",
          sig is not None, True)
    sig = _detect_cpi_surprise({"cpi_yoy": 3.49})
    check("P2A-CPI-2", "cpi=3.49% (임계 미만) → 미발동",
          sig is None, True)


# ═════════════════════════════════════════════════════════════
# P2-A: 운영 영향 측정 — 명백 케이스 회귀 없음
# ═════════════════════════════════════════════════════════════
def test_p2a_no_regression():
    print("\n[P2-A] 명백한 케이스에서 회귀 없음 확인")
    from engines.alert_engine import (
        _pcr_alert, _crypto_basis_alert,
        _detect_stagflation, _detect_yield_spread_deep, _detect_cpi_surprise,
    )

    # 명백한 발동 케이스들 — 모두 동일 동작 유지
    cases = [
        ("PCR 2.0",  lambda: _pcr_alert({"pcr_value": 2.0, "pcr_state": "extreme_fear"}, {}), True),
        ("PCR 0.3",  lambda: _pcr_alert({"pcr_value": 0.3, "pcr_state": "extreme_greed"}, {}), True),
        ("Basis -3", lambda: _crypto_basis_alert({"crypto_basis_spread": -3.0, "crypto_basis_state": "back"}, {}), True),
        ("STAG -3/-2", lambda: _detect_stagflation({"tlt_change": -2.0}, {"sp500": -3.0}), True),
        ("YS -100bp", lambda: _detect_yield_spread_deep({"spread_2y10y_bp": -100, "us2y": 5.2}), True),
        ("CPI 6%",   lambda: _detect_cpi_surprise({"cpi_yoy": 6.0}), True),
    ]
    for desc, fn, should_fire in cases:
        sig = fn()
        fired = sig is not None
        check(f"P2A-NR-{desc}", f"{desc} → fired={fired} (회귀 없음)",
              fired, should_fire)

    # 명백한 미발동 케이스
    not_cases = [
        ("PCR 1.0 정상", lambda: _pcr_alert({"pcr_value": 1.0, "pcr_state": "normal"}, {})),
        ("Basis 0",      lambda: _crypto_basis_alert({"crypto_basis_spread": 0.0, "crypto_basis_state": "normal"}, {})),
        ("STAG 정상",    lambda: _detect_stagflation({"tlt_change": 0.5}, {"sp500": 0.3})),
        ("YS +50bp",    lambda: _detect_yield_spread_deep({"spread_2y10y_bp": 50, "us2y": 4.5})),
        ("CPI 2%",       lambda: _detect_cpi_surprise({"cpi_yoy": 2.0})),
    ]
    for desc, fn in not_cases:
        sig = fn()
        check(f"P2A-NF-{desc}", f"{desc} → None (회귀 없음)",
              sig is None, True)


# ═════════════════════════════════════════════════════════════
# P2-B: VIX_COUNTDOWN prev=None 가드
# ═════════════════════════════════════════════════════════════
def test_p2b_vix_countdown_guard():
    print("\n[P2-B] VIX_COUNTDOWN prev_snapshot=None 안전 가드 — I-03 해소")
    from engines.alert_engine import _vix_countdown_alert

    # ── 패치 핵심: prev=None 시 미발동 ──
    sig = _vix_countdown_alert({"vix": 25.5}, None)
    check("P2B-01", "[BUGFIX 핵심] prev=None + vix=25.5 → 미발동 (이전: 발동)",
          sig is None, True)
    sig = _vix_countdown_alert({"vix": 30}, None)
    check("P2B-02", "prev=None + vix=30 → 미발동",
          sig is None, True)

    # ── prev.vix=0 시도 미발동 (수집 실패 케이스) ──
    sig = _vix_countdown_alert({"vix": 27}, {"vix": 0})
    check("P2B-03", "prev.vix=0 (수집 실패) → 미발동",
          sig is None, True)
    sig = _vix_countdown_alert({"vix": 27}, {})  # 빈 dict
    check("P2B-04", "prev=빈dict → 미발동",
          sig is None, True)
    sig = _vix_countdown_alert({"vix": 27}, {"vix": None})
    check("P2B-05", "prev.vix=None → 미발동",
          sig is None, True)

    # ── 정상 prev 제공 시 동작 유지 (회귀 없음) ──
    sig = _vix_countdown_alert({"vix": 25.5}, {"vix": 24})
    check("P2B-06", "prev=24 + vix=25.5 → 신규 25 돌파 (회귀 없음)",
          sig is not None and sig.alert_type == "VIX_COUNTDOWN", True)
    sig = _vix_countdown_alert({"vix": 27}, {"vix": 26})
    check("P2B-07", "prev=26 + vix=27 → 신규 27 돌파 (회귀 없음)",
          sig is not None, True)
    sig = _vix_countdown_alert({"vix": 26}, {"vix": 25})
    check("P2B-08", "prev=25 + vix=26 → 이미 25 돌파 상태 → 미발동",
          sig is None, True)


# ═════════════════════════════════════════════════════════════
# P2-C: run_alert.py emoji_map 키 통일
# ═════════════════════════════════════════════════════════════
def test_p2c_emoji_map_key():
    print("\n[P2-C] run_alert.py emoji_map 키 통일 — I-06 해소")
    # run_alert.py 소스 정적 분석
    run_alert_path = Path(__file__).parent.parent / "run_alert.py"
    src = run_alert_path.read_text(encoding="utf-8")

    check("P2C-01", "[BUGFIX] emoji_map 키 'PCR_EXTREME' 추가됨",
          '"PCR_EXTREME":' in src, True)
    # 이전 mismatch 키 제거 확인
    # 단순히 "PCR":  로 시작하는 매핑이 emoji_map 안에 있는지 확인
    # (다른 곳에서 PCR 단어가 나올 수 있으니 컨텍스트 좁힘)
    check("P2C-02", "[BUGFIX] 이전 키 '\"PCR\":         \"📊\"' 제거됨",
          '"PCR":         "📊"' not in src, True)

    # 모든 x_eligible=True alert_type 에 emoji 매핑 존재 확인
    x_elig_types = {
        "VIX", "SPY", "OIL", "FED_SHOCK", "CRISIS",
        "STAGFLATION", "SMA200_BREAK",
    }
    for atype in x_elig_types:
        check(f"P2C-EXIST-{atype}", f"emoji_map에 {atype} 키 존재",
              f'"{atype}":' in src, True)


# ═════════════════════════════════════════════════════════════
# P2-D: 운영 메트릭 요약 로그
# ═════════════════════════════════════════════════════════════
def test_p2d_metrics_log_format():
    print("\n[P2-D] 운영 메트릭 요약 로그 포맷 검증")
    run_alert_path = Path(__file__).parent.parent / "run_alert.py"
    src = run_alert_path.read_text(encoding="utf-8")

    # METRICS 로그 라인 존재
    check("P2D-01", "[BUGFIX] METRICS 로그 라인 추가됨",
          "[run_alert] METRICS detected=" in src, True)
    check("P2D-02", "by_level / by_type 포함",
          "by_level=" in src and "by_type=" in src, True)
    check("P2D-03", "sent_x / tg_only 포함",
          "sent_x=" in src and "tg_only=" in src, True)

    # summary dict 에 metrics 키 포함
    check("P2D-04", "summary 반환값에 metrics 키 포함 (호출자 활용 가능)",
          '"metrics":' in src, True)


# ═════════════════════════════════════════════════════════════
# P2-D: 메트릭 계산 로직 격리 검증 (라이브 시뮬레이션)
# ═════════════════════════════════════════════════════════════
def test_p2d_metrics_calculation():
    print("\n[P2-D] METRICS 계산 로직 격리 시뮬레이션")

    # run_alert.py P2-D 코드 블록과 동일한 계산 로직
    def compute_metrics(results):
        m = {"by_type": {}, "by_level": {"L1": 0, "L2": 0, "L3": 0},
             "x_sent": 0, "tg_only": 0}
        for r in results:
            t = r.get("type", "?")
            l = r.get("level", "?")
            m["by_type"][t] = m["by_type"].get(t, 0) + 1
            if l in m["by_level"]:
                m["by_level"][l] += 1
            if r.get("success"):
                m["x_sent"] += 1
            else:
                m["tg_only"] += 1
        return m

    # 케이스 1: 사용자 보고 시나리오 (2026-05-13 로그)
    results = [
        {"type": "OIL", "level": "L2", "tweet_id": "EMOTION",
         "x_eligible": True, "success": True},
        {"type": "CPI_HOT", "level": "L1", "tweet_id": "SKIP_X",
         "x_eligible": False, "success": False},
    ]
    m = compute_metrics(results)
    check("P2D-S1-1", "x_sent=1 (OIL/L2)", m["x_sent"], 1)
    check("P2D-S1-2", "tg_only=1 (CPI_HOT/L1)", m["tg_only"], 1)
    check("P2D-S1-3", "by_level=L1:1, L2:1, L3:0",
          m["by_level"], {"L1": 1, "L2": 1, "L3": 0})
    check("P2D-S1-4", "by_type={'OIL':1, 'CPI_HOT':1}",
          m["by_type"], {"OIL": 1, "CPI_HOT": 1})

    # 케이스 2: 복합 위기
    results = [
        {"type": "CRISIS",      "level": "L3", "success": True},
        {"type": "SPY",         "level": "L2", "success": True},
        {"type": "STAGFLATION", "level": "L2", "success": True},
        {"type": "MOVE_SPIKE",  "level": "L1", "success": False},
        {"type": "CPI_HOT",     "level": "L1", "success": False},
    ]
    m = compute_metrics(results)
    check("P2D-S2-1", "x_sent=3 (L2/L3)", m["x_sent"], 3)
    check("P2D-S2-2", "tg_only=2 (L1)", m["tg_only"], 2)
    check("P2D-S2-3", "by_level=L1:2, L2:2, L3:1",
          m["by_level"], {"L1": 2, "L2": 2, "L3": 1})

    # 케이스 3: 빈 results
    m = compute_metrics([])
    check("P2D-S3-1", "빈 results → x_sent=0", m["x_sent"], 0)
    check("P2D-S3-2", "빈 results → tg_only=0", m["tg_only"], 0)
    check("P2D-S3-3", "빈 results → by_type={}", m["by_type"], {})


# ═════════════════════════════════════════════════════════════
# P2 통합: 모든 패치 적용된 상태에서 alert_engine 정상 동작
# ═════════════════════════════════════════════════════════════
def test_p2_integration():
    print("\n[P2 통합] 모든 P2 패치 적용 상태에서 run_alert_engine 동작")
    from engines.alert_engine import run_alert_engine

    # P2-A 효과: CPI=3.5 정확히 → 발동
    alerts = run_alert_engine(
        snapshot={"vix": 18, "sp500": 0.5, "us10y": 4.2, "oil": 85},
        prev_snapshot={"vix": 18, "sp500": 0.5, "us10y": 4.2, "oil": 85},
        news_result={"source_detail": []},
        fred_data={"cpi_yoy": 3.5},
    )
    types = [(a.alert_type, a.level) for a in alerts]
    check("P2I-01", "[P2-A 효과] CPI=3.5 정확히 → CPI_HOT/L1 발동",
          ("CPI_HOT", "L1") in types, True)

    # P2-B 효과: prev=None + vix=26 → VIX_COUNTDOWN 미발동
    alerts = run_alert_engine(
        snapshot={"vix": 26, "sp500": -1, "us10y": 4.2, "oil": 85},
        prev_snapshot=None,
        news_result={"source_detail": []},
    )
    types = [(a.alert_type, a.level) for a in alerts]
    check("P2I-02", "[P2-B 효과] prev=None + vix=26 → VIX_COUNTDOWN 미발동",
          ("VIX_COUNTDOWN", "L1") not in types, True)

    # P2-A 효과: STAGFLATION 경계값 -1.0 정확히 → 발동
    alerts = run_alert_engine(
        snapshot={"vix": 18, "sp500": -1.0, "us10y": 4.2, "oil": 85},
        prev_snapshot={"vix": 18, "sp500": 0, "us10y": 4.2, "oil": 85},
        news_result={"source_detail": []},
        tier2_data={"tlt_change": -1.0},
    )
    types = [(a.alert_type, a.level) for a in alerts]
    check("P2I-03", "[P2-A 효과] SPY=-1.0 + TLT=-1.0 정확히 → STAGFLATION/L2",
          ("STAGFLATION", "L2") in types, True)


# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 75)
    print("P2 그룹 파일럿 테스트 (마스터 승인 대체)")
    print("  P2-A: 연산자 일관성 (I-01)")
    print("  P2-B: VIX_COUNTDOWN prev=None 가드 (I-03)")
    print("  P2-C: emoji_map 키 통일 (I-06)")
    print("  P2-D: 운영 메트릭 로그 (개선6)")
    print("=" * 75)

    test_p2a_operator_consistency()
    test_p2a_no_regression()
    test_p2b_vix_countdown_guard()
    test_p2c_emoji_map_key()
    test_p2d_metrics_log_format()
    test_p2d_metrics_calculation()
    test_p2_integration()

    print("\n" + "=" * 75)
    total = PASS + FAIL
    rate = 100*PASS/total if total else 0
    print(f"P2 파일럿 결과: {PASS}/{total} PASS ({rate:.1f}%)")
    if FAIL:
        print("\n실패 상세:")
        for d in DETAILS:
            print(f"  - {d}")
    print("=" * 75)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
