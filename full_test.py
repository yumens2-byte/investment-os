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
from config.settings import SYSTEM_VERSION, CODENAME
check("SYSTEM_VERSION = v1.7.0", SYSTEM_VERSION == "v1.7.0", SYSTEM_VERSION)
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

print(f"\n{'='*60}")
print(f"  전수 테스트 결과: {PASS}개 PASS  {FAIL}개 FAIL")
if FAIL == 0:
    print(f"  ✅ 전체 PASS")
else:
    print(f"  ❌ 실패 항목 있음")
print(f"{'='*60}")
sys.exit(FAIL)
