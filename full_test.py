"""
전수 테스트 스크립트 (v1.7.0)
"""
import os, sys, warnings, subprocess
warnings.filterwarnings("ignore")

# 환경변수 설정
for k, v in [("DRY_RUN","true"),("LOG_LEVEL","WARNING"),
             ("FRED_API_KEY","x"),("X_API_KEY","x"),("X_API_SECRET","x"),
             ("X_ACCESS_TOKEN","x"),("X_ACCESS_TOKEN_SECRET","x")]:
    os.environ.setdefault(k, v)

PASS = 0
FAIL = 0

def check(label, ok, detail=""):
    global PASS, FAIL
    icon = "✅" if ok else "❌"
    suffix = f" ({detail})" if detail and not ok else ""
    print(f"  {icon} {label}{suffix}")
    if ok: PASS += 1
    else:  FAIL += 1


print("\n── [1] 파일럿 테스트 (42케이스) ──────────────────────────────")
result = subprocess.run(
    [sys.executable, "main.py", "test", "--round", "all"],
    capture_output=True, text=True, cwd="."
)
output = result.stdout
print("  " + "\n  ".join(output.strip().split("\n")[-5:]))
check("파일럿 테스트 28/28 PASS", "28/28 PASS" in output)
check("파일럿 테스트 14/14 PASS", "14/14 PASS" in output)

print("\n── [2] SYSTEM_VERSION + CODENAME ─────────────────────────────")
import importlib
import config.settings as _cs_mod
importlib.reload(_cs_mod)
SYSTEM_VERSION = _cs_mod.SYSTEM_VERSION
CODENAME = _cs_mod.CODENAME
check("SYSTEM_VERSION = v1.11.0", SYSTEM_VERSION == "v1.11.0", SYSTEM_VERSION)
check("CODENAME = EDT Investment", CODENAME == "EDT Investment", CODENAME)

print("\n── [3] fx_rates 수집 흐름 ─────────────────────────────────────")
import inspect
from run_market import run as rm_run
src = inspect.getsource(rm_run)
check("collect_fx_rates() 호출", "fx_rates = collect_fx_rates()" in src)
check("assemble_core_data(fx_rates=fx_rates)", "fx_rates=fx_rates" in src)

print("\n── [4] json_builder fx_rates 파라미터 ────────────────────────")
from core.json_builder import assemble_core_data
params = inspect.signature(assemble_core_data).parameters
check("fx_rates 파라미터 존재", "fx_rates" in params)
default_ok = params.get("fx_rates") and params["fx_rates"].default is None
check("fx_rates default=None", default_ok)

print("\n── [5] x_publisher 메소드 분리 ───────────────────────────────")
from publishers.x_publisher import publish_tweet, publish_thread, publish_tweet_with_image, upload_media
r1 = publish_tweet("test")
r2 = publish_tweet_with_image("test", "/nonexist.png")
check("publish_tweet (기존 유지)", r1["success"] and r1["dry_run"])
check("publish_tweet_with_image (신규)", r2["success"] and r2.get("has_image", False))
check("publish_thread import OK", callable(publish_thread))
check("upload_media import OK", callable(upload_media))

print("\n── [6] format_image_tweet 4세션 ──────────────────────────────")
from publishers.x_formatter import format_image_tweet
data_fmt = {
    "market_snapshot": {"sp500":-1.67,"nasdaq":-2.15,"vix":31.05,"us10y":4.44,"oil":99.64,"dollar_index":100.15},
    "market_regime": {"market_regime":"Risk-Off","market_risk_level":"MEDIUM"},
    "trading_signal": {"trading_signal":"HOLD","signal_matrix":{}},
    "output_helpers": {"one_line_summary":"Risk-Off — defensive conditions."}
}
for s in ["morning","intraday","close","weekly"]:
    t = format_image_tweet(data_fmt, s)
    check(f"format_image_tweet [{s}] ≤280자", len(t) <= 280, f"{len(t)}자")

print("\n── [7] 대시보드 이미지 4세션 생성 ────────────────────────────")
from publishers.dashboard_builder import build_dashboard
from pathlib import Path
from datetime import datetime, timezone
data_dash = {
    "market_snapshot": {"sp500":-1.67,"nasdaq":-2.15,"vix":31.05,"us10y":4.44,"oil":99.64,"dollar_index":100.15},
    "market_regime": {"market_regime":"Risk-Off","market_risk_level":"MEDIUM","regime_reason":"Fear spreading + risk asset avoidance"},
    "market_score": {"growth_score":3,"inflation_score":2,"liquidity_score":2,"risk_score":4,"financial_stability_score":2,"commodity_pressure_score":3},
    "etf_strategy": {"stance":{"QQQM":"Underweight","XLK":"Underweight","SPYM":"Neutral","XLE":"Overweight","ITA":"Overweight","TLT":"Overweight"}},
    "etf_allocation": {"allocation":{"QQQM":5,"XLK":5,"SPYM":20,"XLE":25,"ITA":25,"TLT":20}},
    "trading_signal": {"trading_signal":"HOLD","signal_matrix":{"buy_watch":["XLE","TLT"],"hold":["SPYM","ITA"],"reduce":["QQQM","XLK"]}},
    "output_helpers": {"one_line_summary":"Risk-Off — defensive conditions. Focus on XLE, TLT."},
    "fx_rates": {"usdkrw":1452.30,"eurusd":1.0812,"usdjpy":149.52}
}
dt = datetime(2026,3,29,21,30,tzinfo=timezone.utc)
for s in ["morning","intraday","close","weekly"]:
    p = build_dashboard(data_dash, session=s, dt_utc=dt, output_dir=Path(f"/tmp/ftest_{s}"))
    ok = bool(p and os.path.exists(p))
    kb = os.path.getsize(p)//1024 if ok else 0
    check(f"대시보드 [{s}] 생성 ({kb} KB)", ok)

print("\n── [8] 파일 정리 확인 ─────────────────────────────────────────")
check("dashboard_builder.py 존재", os.path.exists("publishers/dashboard_builder.py"))
check("image_generator.py 존재",   os.path.exists("publishers/image_generator.py"))
check("dashboard_builder_v2.py 제거", not os.path.exists("publishers/dashboard_builder_v2.py"))

print("\n── [9] dashboard_html_builder import + session=full 라우팅 ─────")
try:
    from publishers.dashboard_html_builder import build_html_dashboard
    check("dashboard_html_builder import OK", True)
except Exception as e:
    check("dashboard_html_builder import OK", False, str(e))

try:
    import inspect
    from publishers.image_generator import generate_image
    src = inspect.getsource(generate_image)
    has_full   = 'session == "full"' in src
    has_html   = 'build_html_dashboard' in src
    has_mpl    = 'build_dashboard' in src
    check("image_generator full 분기", has_full)
    check("image_generator HTML 라우팅", has_html)
    check("image_generator matplotlib 유지", has_mpl)
except Exception as e:
    check("image_generator full 분기 검증", False, str(e))

try:
    import inspect
    import run_view
    src = inspect.getsource(run_view.run)
    has_full_branch = 'session_type == "full"' in src
    check("run_view full 세션 분기", has_full_branch)
    has_full_label = '"full"' in src and '"Full Brief' in src
    check("run_view full 레이블 정의", has_full_label)
except Exception as e:
    check("run_view full 분기 검증", False, str(e))

try:
    from config.settings import SCHEDULE_FULL
    check("SCHEDULE_FULL = 18:30", SCHEDULE_FULL == "18:30", SCHEDULE_FULL)
except Exception as e:
    check("SCHEDULE_FULL 상수", False, str(e))

print("\n── [10] 텔레그램 publisher import + 구조 검증 ────────────")
try:
    from publishers.telegram_publisher import (
        send_message, send_photo, format_free_signal
    )
    check("telegram_publisher import OK", True)
except Exception as e:
    check("telegram_publisher import OK", False, str(e))

try:
    import inspect
    from publishers.telegram_publisher import format_free_signal
    # 샘플 data로 텍스트 생성 검증
    sample = {
        "market_regime": {"market_regime": "Risk-Off", "market_risk_level": "MEDIUM"},
        "trading_signal": {"trading_signal": "HOLD", "signal_reason": "Moderate risk",
                           "signal_matrix": {"buy_watch": ["XLE"], "hold": ["SPYM"], "reduce": ["QQQM"]}},
        "market_snapshot": {"vix": 31.05, "sp500": -1.67},
        "output_helpers": {"one_line_summary": "Defensive conditions."},
    }
    text = format_free_signal(sample)
    check("format_free_signal 생성 (>50자)", len(text) > 50, f"{len(text)}자")
    check("format_free_signal HTML 태그 포함", "<b>" in text)
except Exception as e:
    check("format_free_signal 검증", False, str(e))

try:
    import inspect, run_view
    src = inspect.getsource(run_view.run)
    check("run_view Step 6-TG 분기 존재", "Step 6-TG" in src)
    check("run_view send_message 호출", "send_message" in src)
    check("run_view send_photo 호출", "send_photo" in src)
except Exception as e:
    check("run_view TG 분기 검증", False, str(e))

try:
    from config.settings import SYSTEM_VERSION, TELEGRAM_FREE_CHANNEL, TELEGRAM_PAID_CHANNEL
    check("SYSTEM_VERSION = v1.11.0", SYSTEM_VERSION == "v1.11.0", SYSTEM_VERSION)
    check("TELEGRAM_FREE_CHANNEL 상수", TELEGRAM_FREE_CHANNEL == "free")
    check("TELEGRAM_PAID_CHANNEL 상수", TELEGRAM_PAID_CHANNEL == "paid")
except Exception as e:
    check("settings v1.9.0 검증", False, str(e))

print("\n── [11] 주간 성적표 모듈 검증 ────────────────────────────")
try:
    from core.weekly_tracker import record_daily, get_weekly_summary
    check("weekly_tracker import OK", True)
except Exception as e:
    check("weekly_tracker import OK", False, str(e))

try:
    from publishers.weekly_formatter import format_weekly_thread, format_weekly_telegram
    check("weekly_formatter import OK", True)
except Exception as e:
    check("weekly_formatter import OK", False, str(e))

try:
    from core.weekly_tracker import record_daily, get_weekly_summary
    from publishers.weekly_formatter import format_weekly_thread, format_weekly_telegram
    import tempfile, os
    from pathlib import Path
    from datetime import datetime, timezone

    # 임시 파일로 기록 테스트
    with tempfile.TemporaryDirectory() as tmpdir:
        import core.weekly_tracker as wt
        orig = wt.WEEKLY_LOG_PATH
        wt.WEEKLY_LOG_PATH = Path(tmpdir) / "weekly_log.json"

        sample_data = {
            "market_regime": {"market_regime": "Risk-Off", "market_risk_level": "MEDIUM"},
            "trading_signal": {"trading_signal": "HOLD", "signal_reason": "Moderate risk",
                               "signal_matrix": {"buy_watch": ["XLE","TLT"], "hold": ["SPYM"], "reduce": ["QQQM"]}},
        }
        record_daily(sample_data, dt_utc=datetime(2026,3,28,tzinfo=timezone.utc))
        record_daily(sample_data, dt_utc=datetime(2026,3,27,tzinfo=timezone.utc))

        summary = get_weekly_summary()
        check("weekly_tracker 기록 정상", summary.get("days", 0) >= 1, f"{summary.get('days')}일")

        thread = format_weekly_thread(summary)
        check("weekly_thread 포스트 생성", len(thread) >= 2, f"{len(thread)}개")
        check("weekly_thread 280자 이내", all(len(p) <= 280 for p in thread),
              f"max={max(len(p) for p in thread)}자")

        tg_text = format_weekly_telegram(summary)
        check("weekly_telegram 텍스트 생성", len(tg_text) > 50, f"{len(tg_text)}자")
        check("weekly_telegram HTML 태그 포함", "<b>" in tg_text)

        wt.WEEKLY_LOG_PATH = orig
except Exception as e:
    check("주간 성적표 통합 검증", False, str(e))

try:
    import inspect, run_view
    src = inspect.getsource(run_view.run)
    check('run_view weekly 분기 존재', 'session_type == "weekly"' in src)
    check("run_view weekly_formatter 호출", "weekly_formatter" in src)
except Exception as e:
    check("run_view weekly 분기 검증", False, str(e))

try:
    import inspect, run_market
    src = inspect.getsource(run_market.run)
    check("run_market Step 8-W 존재", "Step 8-W" in src)
    check("run_market record_daily 호출", "record_daily" in src)
except Exception as e:
    check("run_market weekly_tracker 검증", False, str(e))

print(f"\n{'='*60}")
print(f"  전수 테스트 결과: {PASS}개 PASS  {FAIL}개 FAIL")
if FAIL == 0:
    print(f"  ✅ 전체 PASS")
else:
    print(f"  ❌ 실패 항목 있음")
print(f"{'='*60}")
sys.exit(FAIL)
