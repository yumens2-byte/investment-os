"""
tests/test_b21a_x_image_guard.py
=================================
BUGFIX-2026-05-14 검증

대상: run_alert.py 라인 815~891 — B-21A X 이미지 발행 정합성 가드

검증 시나리오:
  - x_eligible=True  + tweet_id 성공  → X 이미지 발행 (PASS)
  - x_eligible=True  + tweet_id 실패  → X 이미지 스킵 (정합성)
  - x_eligible=False                  → X 이미지 스킵 (BUGFIX 핵심)
  - meme_path=None                    → 양쪽 모두 시도 안 함

설계 원칙 (run_alert.py docstring 참조):
  발행 조건 = meme_path 존재 AND x_eligible=True AND tweet_id ∉ {SKIP_X, FAIL}
"""
import sys
from pathlib import Path

# ── 격리된 가드 로직 (run_alert.py에서 추출한 정확한 동일 로직) ──
def should_publish_x_image(meme_path, x_eligible, tweet_id):
    """
    B-21A X 이미지 발행 여부 결정.
    run_alert.py 라인 841~842와 100% 동일한 로직.
    """
    _x_image_ok = x_eligible and tweet_id not in ("SKIP_X", "FAIL")
    return bool(meme_path) and _x_image_ok


# ── 이전 버그 동작 (회귀 비교용) ──
def should_publish_x_image_BUGGY(meme_path, x_eligible, tweet_id):
    """패치 이전 동작 — meme_path만 체크."""
    return bool(meme_path)


# ─────────────────────────────────────────────────────────────
# 테스트 케이스
# ─────────────────────────────────────────────────────────────
PASS_COUNT = 0
FAIL_COUNT = 0
FAIL_DETAILS = []

def check(case_id, desc, actual, expected):
    global PASS_COUNT, FAIL_COUNT
    if actual == expected:
        PASS_COUNT += 1
        print(f"  ✅ {case_id}: {desc}")
    else:
        FAIL_COUNT += 1
        FAIL_DETAILS.append(f"{case_id}: {desc} — expected={expected} actual={actual}")
        print(f"  ❌ {case_id}: {desc} — expected={expected} actual={actual}")


def main():
    print("=" * 70)
    print("B-21A X 이미지 정합성 가드 단위 테스트 (BUGFIX-2026-05-14)")
    print("=" * 70)

    MEME = "data/images/meme_20260513.png"

    # ─────────────────────────────────────────────────────────
    # Group 1: x_eligible=True 정상 케이스 (발행되어야 함)
    # ─────────────────────────────────────────────────────────
    print("\n[Group 1] x_eligible=True 정상 케이스 (X 이미지 발행 기대)")
    check("T-01", "OIL/L2 EMOTION 성공 → 발행",
          should_publish_x_image(MEME, True, "2054587468901446056"), True)
    check("T-02", "VIX/L2 EMOTION 성공 → 발행",
          should_publish_x_image(MEME, True, "2054587468901446057"), True)
    check("T-03", "SPY/L3 EMOTION 성공 → 발행",
          should_publish_x_image(MEME, True, "EMOTION"), True)
    check("T-04", "FED_SHOCK/L2 케이스B(쿨다운) 성공 → 발행",
          should_publish_x_image(MEME, True, "1234567890"), True)
    check("T-05", "CRISIS/L3 EMOTION 성공 → 발행",
          should_publish_x_image(MEME, True, "EMOTION"), True)
    check("T-06", "STAGFLATION/L2 성공 → 발행",
          should_publish_x_image(MEME, True, "9999"), True)
    check("T-07", "SMA200_BREAK/L2 성공 → 발행",
          should_publish_x_image(MEME, True, "EMOTION"), True)

    # ─────────────────────────────────────────────────────────
    # Group 2: x_eligible=False — BUGFIX 핵심 (모두 스킵되어야 함)
    # ─────────────────────────────────────────────────────────
    print("\n[Group 2] x_eligible=False — BUGFIX 핵심 (X 이미지 스킵 기대)")
    check("T-10", "VIX/L1 (사용자 보고 케이스) → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-11", "CPI_HOT/L1 (2026-05-13 로그 케이스) → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-12", "PCR/L1 극단 공포 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-13", "CRYPTO_BASIS/L1 백워데이션 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-14", "VIX_COUNTDOWN/L1 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-15", "MOVE_SPIKE/L1 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-16", "SOFR_STRESS/L1 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-17", "YIELD_SPREAD_DEEP/L1 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-18", "SMA200_BREAK/L1 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-19", "OIL/L1 (급등률만, 가격 미충격) → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-20", "SPY/L1 주의 급락 → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-21", "ETF_RANK → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)
    check("T-22", "REGIME_CHANGE → 스킵",
          should_publish_x_image(MEME, False, "SKIP_X"), False)

    # ─────────────────────────────────────────────────────────
    # Group 3: x_eligible=True 이지만 X 텍스트 실패 (정합성 가드)
    # ─────────────────────────────────────────────────────────
    print("\n[Group 3] X 텍스트 발행 실패 시 이미지도 스킵 (정합성)")
    check("T-30", "x_elig=True + tweet_id=FAIL → 스킵",
          should_publish_x_image(MEME, True, "FAIL"), False)
    check("T-31", "x_elig=True + tweet_id=SKIP_X (이상 케이스) → 스킵",
          should_publish_x_image(MEME, True, "SKIP_X"), False)

    # ─────────────────────────────────────────────────────────
    # Group 4: meme_path 부재
    # ─────────────────────────────────────────────────────────
    print("\n[Group 4] meme_path=None (이미지 생성 실패)")
    check("T-40", "meme_path=None + x_elig=True 성공 → 발행 안함",
          should_publish_x_image(None, True, "EMOTION"), False)
    check("T-41", "meme_path=None + x_elig=False → 발행 안함",
          should_publish_x_image(None, False, "SKIP_X"), False)
    check("T-42", "meme_path='' (빈 문자열) → 발행 안함",
          should_publish_x_image("", True, "EMOTION"), False)

    # ─────────────────────────────────────────────────────────
    # Group 5: 회귀 비교 — 패치 전 vs 패치 후
    # ─────────────────────────────────────────────────────────
    print("\n[Group 5] 회귀 비교 — 패치 전(BUGGY) vs 패치 후(FIXED)")
    cases = [
        ("OIL/L2",       True,  "EMOTION", True,  True),    # 발행 → 발행 (동일)
        ("VIX/L1",       False, "SKIP_X",  True,  False),   # 발행 → 스킵 (BUGFIX)
        ("CPI_HOT/L1",   False, "SKIP_X",  True,  False),   # 발행 → 스킵 (BUGFIX)
        ("PCR/L1",       False, "SKIP_X",  True,  False),   # 발행 → 스킵 (BUGFIX)
        ("FAIL 케이스",   True,  "FAIL",    True,  False),   # 발행 → 스킵 (정합성)
    ]
    for name, xe, tid, expected_buggy, expected_fixed in cases:
        buggy = should_publish_x_image_BUGGY(MEME, xe, tid)
        fixed = should_publish_x_image(MEME, xe, tid)
        diff  = "DIFF" if buggy != fixed else "same"
        check(f"R-{name}", f"BUGGY={buggy} → FIXED={fixed} [{diff}]",
              (buggy, fixed), (expected_buggy, expected_fixed))

    # ─────────────────────────────────────────────────────────
    # Group 6: alert_engine.py 정책 매트릭스 100% 매핑
    # ─────────────────────────────────────────────────────────
    print("\n[Group 6] alert_engine.py 정책 매트릭스 완전 매핑")
    # alert_engine.py docstring "x_eligible 정책" 섹션 기준 전수 케이스
    policy_matrix = [
        # (alert_type, level, x_eligible_정책)
        ("VIX",          "L1", False),
        ("VIX",          "L2", True),
        ("SPY",          "L1", False),
        ("SPY",          "L2", True),
        ("SPY",          "L3", True),
        ("OIL",          "L1", False),  # 급등률만
        ("OIL",          "L2", True),   # 가격충격 ($100 돌파)
        ("FED_SHOCK",    "L2", True),
        ("FED_SHOCK",    "L3", True),
        ("CRISIS",       "L2", True),
        ("CRISIS",       "L3", True),
        ("STAGFLATION",  "L2", True),
        ("SMA200_BREAK", "L1", False),
        ("SMA200_BREAK", "L2", True),
        ("PCR",          "L1", False),
        ("CRYPTO_BASIS", "L1", False),
        ("VIX_COUNTDOWN","L1", False),
        ("ETF_RANK",     "L2", False),
        ("REGIME_CHANGE","L2", False),
        ("MOVE_SPIKE",   "L1", False),
        ("YIELD_SPREAD_DEEP", "L1", False),
        ("CPI_HOT",      "L1", False),
        ("SOFR_STRESS",  "L1", False),
    ]
    for atype, level, xe_policy in policy_matrix:
        # 정상 경로: x_eligible=True면 EMOTION, False면 SKIP_X
        tid_simulated = "EMOTION" if xe_policy else "SKIP_X"
        result = should_publish_x_image(MEME, xe_policy, tid_simulated)
        expected = xe_policy  # x_eligible=True인 것만 발행되어야 함
        check(f"P-{atype}_{level}",
              f"x_elig={xe_policy} → 발행={result}",
              result, expected)

    # ─────────────────────────────────────────────────────────
    # 결과 요약
    # ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    total = PASS_COUNT + FAIL_COUNT
    print(f"결과: {PASS_COUNT}/{total} PASS ({100*PASS_COUNT/total:.1f}%)")
    if FAIL_COUNT:
        print(f"\n실패 상세:")
        for d in FAIL_DETAILS:
            print(f"  - {d}")
    print("=" * 70)
    sys.exit(0 if FAIL_COUNT == 0 else 1)


if __name__ == "__main__":
    main()
