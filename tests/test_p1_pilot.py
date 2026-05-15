"""
tests/test_p1_pilot.py
========================
P1 그룹 파일럿 테스트 (마스터 승인 대체용 무중단 검증)

검증 대상:
  P1-A: I-02 — CRISIS US10Y 임계값 config 분리
  P1-B: 개선5 — Alert 회귀 테스트 CI workflow 신설

원칙:
  - 운영 동작이 패치 전과 100% 동일함을 증명 (회귀 없음)
  - config 변경 가능성만 추가됨을 증명 (확장성 확보)
  - ENV 변수 override 작동 검증
  - YAML 신택스 정합
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
# P1-A: CRISIS US10Y_CRISIS_THR config 분리 검증
# ═════════════════════════════════════════════════════════════
def test_p1a_config_separation():
    print("\n[P1-A] CRISIS US10Y 임계값 config 분리 — I-02 해소")

    # 1. config.settings에 상수 노출 확인
    from config.settings import US10Y_CRISIS_THR
    check("P1A-01", "config.settings.US10Y_CRISIS_THR 노출됨",
          hasattr(__import__('config.settings', fromlist=['US10Y_CRISIS_THR']),
                  'US10Y_CRISIS_THR'), True)

    # 2. 기본값이 기존 매직 넘버 4.8과 동일 (회귀 방지)
    check("P1A-02", "기본값 4.8 (기존 매직 넘버와 동일 — 회귀 없음)",
          US10Y_CRISIS_THR, 4.8)

    # 3. alert_engine.py에서 import되는지
    from engines import alert_engine as ae
    check("P1A-03", "alert_engine 모듈에서 US10Y_CRISIS_THR 참조 가능",
          hasattr(ae, "US10Y_CRISIS_THR"), True)

    # 4. _crisis_alert 소스에 매직 넘버 4.8 제거 확인
    import inspect
    src = inspect.getsource(ae._crisis_alert)
    has_magic = "us10y >= 4.8" in src
    check("P1A-04", "[코드 클린] _crisis_alert에서 매직 넘버 4.8 제거",
          has_magic, False)
    has_const = "US10Y_CRISIS_THR" in src
    check("P1A-05", "[코드 클린] _crisis_alert에서 US10Y_CRISIS_THR 상수 사용",
          has_const, True)


# ═════════════════════════════════════════════════════════════
# P1-A: 패치 전 동작 100% 재현 (회귀 0)
# ═════════════════════════════════════════════════════════════
def test_p1a_no_regression():
    print("\n[P1-A] 회귀 없음 검증 — 기본값으로 패치 전과 동일")
    from engines.alert_engine import _crisis_alert

    # 회귀 케이스: 정확히 임계값 4.8 → 카운트 +1
    sig = _crisis_alert({"vix": 36, "sp500": -1, "us10y": 4.8})
    check("P1A-R1", "us10y=4.8 정확히 + VIX 카운트 → CRISIS L2 (회귀 없음)",
          sig is not None and sig.level == "L2", True)

    # 회귀 케이스: 4.79 → 카운트 안 됨
    sig = _crisis_alert({"vix": 36, "sp500": -1, "us10y": 4.79})
    check("P1A-R2", "us10y=4.79 (임계 미만) + VIX 1개만 → None (회귀 없음)",
          sig is None, True)

    # 회귀 케이스: 3개 동시
    sig = _crisis_alert({"vix": 40, "sp500": -5, "us10y": 5.0})
    check("P1A-R3", "3개 동시 → CRISIS L3 (회귀 없음)",
          sig is not None and sig.level == "L3", True)

    # 회귀 케이스: 2개 (VIX+SPY, us10y 정상)
    sig = _crisis_alert({"vix": 40, "sp500": -5, "us10y": 4.0})
    check("P1A-R4", "VIX+SPY 2개, us10y 정상 → CRISIS L2 (회귀 없음)",
          sig is not None and sig.level == "L2", True)


# ═════════════════════════════════════════════════════════════
# P1-A: ENV 변수 override 작동 검증
# ═════════════════════════════════════════════════════════════
def test_p1a_env_override():
    print("\n[P1-A] ENV 변수 운영 중 튜닝 가능성 검증")

    # ENV 변수가 5.0 일 때 4.9는 카운트 안 됨을 검증 — 모듈 리로드 필요
    import importlib
    import sys as _sys

    # 1차: 기본값 4.8
    if 'config.settings' in _sys.modules:
        del _sys.modules['config.settings']
    if 'engines.alert_engine' in _sys.modules:
        del _sys.modules['engines.alert_engine']

    os.environ["US10Y_CRISIS_THR"] = "5.0"
    import config.settings as cs
    import engines.alert_engine as ae

    check("P1A-E1", "ENV US10Y_CRISIS_THR=5.0 적용",
          cs.US10Y_CRISIS_THR, 5.0)
    check("P1A-E2", "alert_engine도 동일하게 5.0 반영",
          ae.US10Y_CRISIS_THR, 5.0)

    # 4.9 (기존엔 카운트, ENV 5.0 후엔 카운트 안 됨)
    sig = ae._crisis_alert({"vix": 36, "sp500": -1, "us10y": 4.9})
    check("P1A-E3", "ENV=5.0 + us10y=4.9 → 카운트 안 됨 (1개만, None)",
          sig is None, True)

    # 5.0 정확히
    sig = ae._crisis_alert({"vix": 36, "sp500": -1, "us10y": 5.0})
    check("P1A-E4", "ENV=5.0 + us10y=5.0 정확히 → CRISIS L2",
          sig is not None and sig.level == "L2", True)

    # 정리 — 다른 테스트에 영향 없도록 기본값 복귀
    del os.environ["US10Y_CRISIS_THR"]
    del _sys.modules['config.settings']
    del _sys.modules['engines.alert_engine']
    import config.settings as cs2
    check("P1A-E5", "ENV 제거 후 기본값 4.8 복귀",
          cs2.US10Y_CRISIS_THR, 4.8)


# ═════════════════════════════════════════════════════════════
# P1-B: GitHub Actions workflow YAML 검증
# ═════════════════════════════════════════════════════════════
def test_p1b_workflow_yaml():
    print("\n[P1-B] CI Workflow YAML 정합성 검증")

    workflow_path = Path(__file__).parent.parent / ".github" / "workflows" / "ci_alert_tests.yml"
    check("P1B-01", f"workflow 파일 존재",
          workflow_path.exists(), True)

    # YAML 신택스 파싱
    try:
        import yaml
        with open(workflow_path) as f:
            wf = yaml.safe_load(f)
        yaml_ok = True
    except Exception as e:
        yaml_ok = False
        print(f"    YAML 파싱 예외: {e}")
    check("P1B-02", "YAML 신택스 정상 파싱", yaml_ok, True)

    if not yaml_ok:
        return

    # 필수 키 검증
    check("P1B-03", "name 정의됨",
          wf.get("name"), "Alert Regression Tests")
    # PyYAML이 `on` 을 True (boolean)로 파싱하는 경우 있음 — 두 키 모두 체크
    on_section = wf.get("on") or wf.get(True)
    check("P1B-04", "on 트리거 정의됨",
          on_section is not None and "workflow_dispatch" in on_section, True)
    check("P1B-05", "pull_request 트리거 포함",
          "pull_request" in on_section, True)
    check("P1B-06", "push 트리거 포함",
          "push" in on_section, True)

    # jobs 구조
    jobs = wf.get("jobs", {})
    check("P1B-07", "alert-regression job 정의됨",
          "alert-regression" in jobs, True)
    job = jobs.get("alert-regression", {})
    check("P1B-08", "runs-on=ubuntu-latest",
          job.get("runs-on"), "ubuntu-latest")
    check("P1B-09", "timeout-minutes 설정됨 (장시간 hang 방지)",
          isinstance(job.get("timeout-minutes"), int), True)

    # 5개 테스트 모두 step에 포함되는지
    steps = job.get("steps", [])
    step_runs = " ".join(s.get("run", "") for s in steps if "run" in s)
    tests = [
        "test_alert_unit_boundaries.py",
        "test_alert_integration_full.py",
        "test_alert_policy_matrix.py",
        "test_b21a_x_image_guard.py",
        "test_b21a_integration_sim.py",
    ]
    for t in tests:
        check(f"P1B-T-{t}", f"{t} step 포함됨",
              t in step_runs, True)

    # 환경변수 더미값 설정 검증 (실제 API 호출 방지)
    env = job.get("env", {})
    check("P1B-10", "DRY_RUN=true 강제",
          env.get("DRY_RUN"), "true")
    check("P1B-11", "X API 더미 환경변수 설정",
          all(env.get(k) == "test" for k in [
              "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"
          ]), True)


# ═════════════════════════════════════════════════════════════
# P1-B: workflow trigger paths 정합성
# ═════════════════════════════════════════════════════════════
def test_p1b_trigger_paths():
    print("\n[P1-B] workflow 트리거 paths 정합성")
    import yaml
    workflow_path = Path(__file__).parent.parent / ".github" / "workflows" / "ci_alert_tests.yml"
    with open(workflow_path) as f:
        wf = yaml.safe_load(f)

    on = wf.get("on") or wf.get(True)
    pr_paths   = on.get("pull_request", {}).get("paths", [])
    push_paths = on.get("push", {}).get("paths", [])

    # alert 관련 핵심 파일 변경 시 trigger 되어야 함
    required = ['engines/alert_engine.py', 'run_alert.py', 'config/settings.py']
    for r in required:
        check(f"P1B-P-{r}", f"PR paths에 {r} 포함",
              r in pr_paths, True)
        check(f"P1B-P-PUSH-{r}", f"push paths에 {r} 포함",
              r in push_paths, True)

    # 테스트 파일도 trigger
    test_globs = ['tests/test_alert_**.py', 'tests/test_b21a_**.py']
    for g in test_globs:
        check(f"P1B-P-TG-{g}", f"PR paths에 {g} 포함",
              g in pr_paths, True)


# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 75)
    print("P1 그룹 파일럿 테스트 (마스터 승인 대체)")
    print("  P1-A: CRISIS US10Y 임계값 config 분리 (I-02)")
    print("  P1-B: Alert 회귀 테스트 CI workflow (개선5)")
    print("=" * 75)

    test_p1a_config_separation()
    test_p1a_no_regression()
    test_p1a_env_override()
    test_p1b_workflow_yaml()
    test_p1b_trigger_paths()

    print("\n" + "=" * 75)
    total = PASS + FAIL
    rate = 100*PASS/total if total else 0
    print(f"P1 파일럿 결과: {PASS}/{total} PASS ({rate:.1f}%)")
    if FAIL:
        print("\n실패 상세:")
        for d in DETAILS:
            print(f"  - {d}")
    print("=" * 75)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
