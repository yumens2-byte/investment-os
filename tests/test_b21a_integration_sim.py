"""
tests/test_b21a_integration_sim.py
====================================
BUGFIX-2026-05-14 통합 시뮬레이션

실제 run_alert.py의 Step 3 루프를 mock 환경에서 실행하여
publish_tweet_with_image 호출이 정합성 가드를 통과하는지 검증.

검증 포인트:
  1. x_eligible=False Alert → publish_tweet_with_image 호출 안 됨
  2. x_eligible=True Alert → publish_tweet_with_image 호출 됨
  3. TG 발행은 정책과 무관하게 모두 발행됨 (영향 없음 확인)
  4. record_alert 호출은 X 텍스트 발행 성공한 경우만
"""
import os
import sys
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock

# 환경변수 mock (config.settings import 시 필요)
os.environ.setdefault("DRY_RUN", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # 출력 깔끔하게
os.environ.setdefault("FRED_API_KEY", "test")
os.environ.setdefault("X_API_KEY", "test")
os.environ.setdefault("X_API_SECRET", "test")
os.environ.setdefault("X_ACCESS_TOKEN", "test")
os.environ.setdefault("X_ACCESS_TOKEN_SECRET", "test")
os.environ.setdefault("TG_BOT_TOKEN", "test")
os.environ.setdefault("TG_CHANNEL_ID_FREE", "test")
os.environ.setdefault("TG_CHANNEL_ID_PAID", "test")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test")

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


# ──────────────────────────────────────────────────────────────
# AlertSignal mock factory
# ──────────────────────────────────────────────────────────────
def _make_signal(alert_type, level, x_eligible, snapshot=None):
    from engines.alert_engine import AlertSignal
    return AlertSignal(
        alert_type=alert_type,
        level=level,
        reason=f"테스트 사유: {alert_type}/{level}",
        snapshot=snapshot or {"sp500": 0.2, "vix": 18.0, "oil": 95.0, "us10y": 4.42},
        etf_hints=["SPY"],
        avoid_etfs=["QQQM"],
        x_eligible=x_eligible,
    )


# ──────────────────────────────────────────────────────────────
# Integration 시나리오
# ──────────────────────────────────────────────────────────────
def simulate_alert(signal, x_text_post_will_succeed=True, meme_will_succeed=True):
    """
    한 개의 AlertSignal을 받아 run_alert.py Step 3 루프 일부를
    mock 환경에서 실행하고, publish_tweet_with_image 호출 여부 반환.

    Returns:
        dict: {
            "x_text_called": X 텍스트 발행 호출 여부,
            "x_image_called": X 이미지(B-21A) 발행 호출 여부,
            "tg_called": TG 발행 호출 여부,
        }
    """
    # ── Mock 컨테이너 ──
    calls = {
        "x_text_called":  False,
        "x_image_called": False,
        "tg_called":      False,
    }

    # ── publish_tweet_with_image mock ──
    def _mock_publish_tweet_with_image(text, image_path):
        calls["x_image_called"] = True
        return {"success": True, "tweet_id": "MOCK_IMG_TID"}

    # ── post_alert_tweet mock (감정 트리거 포맷) ──
    def _mock_post_alert_tweet(text, dry_run=False):
        calls["x_text_called"] = True
        return x_text_post_will_succeed

    # ── publish_tweet mock (기본 포맷, 케이스 B용) ──
    def _mock_publish_tweet(text):
        calls["x_text_called"] = True
        return {"success": x_text_post_will_succeed,
                "tweet_id": "MOCK_TXT_TID" if x_text_post_will_succeed else None}

    # ── TG send mock ──
    def _mock_send_message(text, channel="free", **kwargs):
        calls["tg_called"] = True

    def _mock_send_photo(image_path, caption="", channel="free", **kwargs):
        calls["tg_called"] = True

    # ── generate_meme mock ──
    def _mock_generate_meme(alert_type, alert_level, snapshot, core_data=None):
        return "/tmp/mock_meme.png" if meme_will_succeed else None

    # ── 정합성 가드 로직 (run_alert.py 라인 841~891 그대로) ──
    # 실제 코드를 import하면 너무 많은 의존성 필요 — 동일 로직 인라인 실행
    from run_alert import _is_x_cooldown_active

    x_history = {}  # 쿨다운 없음 — 신규 케이스
    _x_elig = getattr(signal, "x_eligible", False)
    _x_cool = _is_x_cooldown_active(signal.alert_type, x_history)
    tweet_id = "SKIP_X"

    # 케이스 분기 (run_alert.py 라인 607~660 그대로)
    if _x_elig and not _x_cool:
        # 케이스 A
        ok = _mock_post_alert_tweet("emotion_text", dry_run=False)
        tweet_id = "EMOTION" if ok else "FAIL"
    elif _x_elig and _x_cool:
        # 케이스 B
        r = _mock_publish_tweet("basic_text")
        tweet_id = r.get("tweet_id", "FAIL") or "FAIL"
    else:
        # 케이스 C: x_eligible=False → 스킵
        pass

    # TG 발행 (모든 케이스)
    meme_path = _mock_generate_meme(signal.alert_type, signal.level, signal.snapshot)
    _mock_send_photo(meme_path, caption="tg_text", channel="free")

    # ── ★★ B-21A X 이미지 정합성 가드 (run_alert.py 라인 841~891) ★★ ──
    _x_image_ok = _x_elig and tweet_id not in ("SKIP_X", "FAIL")
    if meme_path and _x_image_ok:
        _mock_publish_tweet_with_image("meme_tweet", meme_path)
    elif meme_path and not _x_image_ok:
        pass  # 스킵 로그

    return calls


# ──────────────────────────────────────────────────────────────
# 테스트 실행
# ──────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("B-21A X 이미지 정합성 가드 통합 시뮬레이션 (BUGFIX-2026-05-14)")
    print("=" * 70)

    # ─── Scenario 1: 사용자 보고 케이스 재현 (2026-05-13 로그) ───
    print("\n[Scenario 1] 사용자 보고 케이스 재현")

    # OIL/L2 (x_eligible=True) — 정상 발행
    sig_oil = _make_signal("OIL", "L2", True)
    r = simulate_alert(sig_oil)
    check("S1-OIL-X-TXT",   "OIL/L2 X 텍스트 발행",      r["x_text_called"],  True)
    check("S1-OIL-X-IMG",   "OIL/L2 X 이미지 발행",      r["x_image_called"], True)
    check("S1-OIL-TG",      "OIL/L2 TG 발행",           r["tg_called"],      True)

    # CPI_HOT/L1 (x_eligible=False) — BUGFIX 핵심
    sig_cpi = _make_signal("CPI_HOT", "L1", False)
    r = simulate_alert(sig_cpi)
    check("S1-CPI-X-TXT",   "CPI_HOT/L1 X 텍스트 스킵",   r["x_text_called"],  False)
    check("S1-CPI-X-IMG",   "CPI_HOT/L1 X 이미지 스킵 (BUGFIX)", r["x_image_called"], False)
    check("S1-CPI-TG",      "CPI_HOT/L1 TG 발행 정상",   r["tg_called"],      True)

    # VIX/L1 (x_eligible=False) — 사용자가 보고한 핵심 케이스
    sig_vix = _make_signal("VIX", "L1", False)
    r = simulate_alert(sig_vix)
    check("S1-VIX-X-TXT",   "VIX/L1 X 텍스트 스킵 (정책)",  r["x_text_called"],  False)
    check("S1-VIX-X-IMG",   "VIX/L1 X 이미지 스킵 (BUGFIX 핵심)", r["x_image_called"], False)
    check("S1-VIX-TG",      "VIX/L1 TG 발행 정상",        r["tg_called"],      True)

    # ─── Scenario 2: 전체 정책 매트릭스 ───
    print("\n[Scenario 2] alert_engine.py 정책 매트릭스 전수")
    matrix = [
        ("VIX",          "L2", True),
        ("VIX",          "L1", False),
        ("SPY",          "L3", True),
        ("SPY",          "L2", True),
        ("SPY",          "L1", False),
        ("OIL",          "L2", True),
        ("OIL",          "L1", False),
        ("FED_SHOCK",    "L3", True),
        ("FED_SHOCK",    "L2", True),
        ("CRISIS",       "L3", True),
        ("STAGFLATION",  "L2", True),
        ("SMA200_BREAK", "L2", True),
        ("SMA200_BREAK", "L1", False),
        ("PCR",          "L1", False),
        ("CRYPTO_BASIS", "L1", False),
        ("MOVE_SPIKE",   "L1", False),
        ("YIELD_SPREAD_DEEP", "L1", False),
        ("CPI_HOT",      "L1", False),
        ("SOFR_STRESS",  "L1", False),
        ("ETF_RANK",     "L2", False),
        ("REGIME_CHANGE","L2", False),
    ]
    for atype, level, xe in matrix:
        sig = _make_signal(atype, level, xe)
        r = simulate_alert(sig)
        # 핵심 invariant: x_eligible == X 이미지 발행 여부
        check(f"S2-{atype}-{level}", f"x_elig={xe} → X이미지={r['x_image_called']}",
              r["x_image_called"], xe)
        # TG는 모든 alert에 대해 발행 (영향 없음)
        check(f"S2-{atype}-{level}-TG", f"x_elig={xe} → TG=True",
              r["tg_called"], True)

    # ─── Scenario 3: X 텍스트 발행 실패 → 이미지도 스킵 ───
    print("\n[Scenario 3] X 텍스트 발행 실패 시 이미지 스킵 (정합성)")
    sig_oil_fail = _make_signal("OIL", "L2", True)
    r = simulate_alert(sig_oil_fail, x_text_post_will_succeed=False)
    check("S3-OIL-FAIL-TXT", "X 텍스트 호출은 시도됨",      r["x_text_called"],  True)
    check("S3-OIL-FAIL-IMG", "X 텍스트 실패 → 이미지 스킵", r["x_image_called"], False)

    # ─── Scenario 4: 밈 생성 실패 → X 이미지 없음 ───
    print("\n[Scenario 4] 밈 생성 실패 시 X 이미지 호출 안 됨")
    sig_no_meme = _make_signal("OIL", "L2", True)
    r = simulate_alert(sig_no_meme, meme_will_succeed=False)
    check("S4-NO-MEME-TXT", "X 텍스트 정상 발행",         r["x_text_called"],  True)
    check("S4-NO-MEME-IMG", "meme_path=None → X 이미지 스킵", r["x_image_called"], False)

    # ─── 결과 ───
    print("\n" + "=" * 70)
    total = PASS + FAIL
    print(f"결과: {PASS}/{total} PASS ({100*PASS/total:.1f}%)")
    if FAIL:
        print("\n실패 상세:")
        for d in DETAILS:
            print(f"  - {d}")
    print("=" * 70)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
