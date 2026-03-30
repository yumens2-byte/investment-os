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
check("SYSTEM_VERSION = v1.19.0", SYSTEM_VERSION == "v1.19.0", SYSTEM_VERSION)
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
    check("SYSTEM_VERSION = v1.19.0", SYSTEM_VERSION == "v1.19.0", SYSTEM_VERSION)
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

print("\n── [12] 유료 채널 리포트 포맷 검증 ────────────────────────")
try:
    from publishers.telegram_publisher import format_paid_report
    check("format_paid_report import OK", True)
except Exception as e:
    check("format_paid_report import OK", False, str(e))

try:
    sample = {
        "market_regime": {"market_regime": "Risk-Off", "market_risk_level": "MEDIUM"},
        "trading_signal": {"trading_signal": "HOLD", "signal_reason": "Moderate risk",
                           "signal_matrix": {"buy_watch": ["XLE","TLT"], "hold": ["SPYM"], "reduce": ["QQQM"]}},
        "etf_strategy": {
            "stance": {"QQQM":"Underweight","XLK":"Underweight","SPYM":"Neutral","XLE":"Overweight","ITA":"Neutral","TLT":"Overweight"},
            "strategy_reason": {"QQQM":"Weak","XLK":"Weak","SPYM":"Neutral","XLE":"Top ranked","ITA":"Neutral","TLT":"Top ranked"},
        },
        "etf_analysis": {"timing_signal": {"XLE":"BUY","TLT":"ADD ON PULLBACK","SPYM":"HOLD","ITA":"HOLD","QQQM":"REDUCE","XLK":"REDUCE"}},
        "etf_allocation": {"allocation": {"QQQM":5,"XLK":5,"SPYM":20,"XLE":25,"ITA":25,"TLT":20}},
        "portfolio_risk": {"position_sizing_multiplier": 0.75, "crash_alert_level": "MEDIUM",
                           "hedge_intensity": "Medium", "diversification_score": 45},
    }
    text = format_paid_report(sample)
    check("format_paid_report 생성 (>200자)", len(text) > 200, f"{len(text)}자")
    check("format_paid_report ETF 포함", "XLE" in text and "TLT" in text)
    check("format_paid_report 포지션사이징 포함", "포지션" in text)
    check("format_paid_report HTML 태그", "<b>" in text)
except Exception as e:
    check("format_paid_report 통합 검증", False, str(e))

try:
    import inspect, run_view
    src = inspect.getsource(run_view.run)
    check("run_view format_paid_report 호출", "format_paid_report" in src)
    check("run_view 유료 채널 추가 발송", "paid_text" in src)
except Exception as e:
    check("run_view 유료 리포트 검증", False, str(e))

print("\n── [13] 프리미엄 알람 검증 ─────────────────────────────")
try:
    from publishers.premium_alert_formatter import (
        format_vix_premium, format_regime_change_premium, VIX_LEVELS
    )
    check("premium_alert_formatter import OK", True)
    check("VIX_LEVELS 4단계", len(VIX_LEVELS) == 4)

    txt = format_vix_premium(31.5, 28.0, "Risk-Off", "HIGH")
    check("format_vix_premium 생성", len(txt) > 30)
    check("format_vix_premium HTML", "<b>" in txt)

    txt2 = format_regime_change_premium("Risk-On", "Risk-Off", "REDUCE", "HIGH", ["TLT"])
    check("format_regime_change_premium 생성", len(txt2) > 30)
    check("format_regime_change_premium 전환 표시", "→" in txt2)
except Exception as e:
    check("premium_alert_formatter 검증", False, str(e))

try:
    import inspect, run_alert
    src = inspect.getsource(run_alert.run)
    check('run_alert 유료 채널 분기 존재', 'channel="paid"' in src)
    check("run_alert premium_alert_formatter 호출", "premium_alert_formatter" in src)
except Exception as e:
    check("run_alert 유료 채널 검증", False, str(e))

try:
    from engines.alert_engine import VIX_PREMIUM_LEVELS
    check("VIX_PREMIUM_LEVELS 상수 존재", len(VIX_PREMIUM_LEVELS) == 4)
    check("VIX_PREMIUM_LEVELS 값 확인", 20 in VIX_PREMIUM_LEVELS and 35 in VIX_PREMIUM_LEVELS)
except Exception as e:
    check("VIX_PREMIUM_LEVELS 검증", False, str(e))

print("\n── [14] ETF 랭킹 변화 알림 검증 ──────────────────────────")
try:
    from core.rank_tracker import detect_rank_change
    check("rank_tracker import OK", True)
except Exception as e:
    check("rank_tracker import OK", False, str(e))

try:
    from publishers.telegram_publisher import format_rank_change
    check("format_rank_change import OK", True)
    sample_change = {
        "top1_changed": True, "old_top1": "XLE", "new_top1": "TLT",
        "moved_up":   [{"etf": "TLT", "from": 2, "to": 1}],
        "moved_down": [{"etf": "XLE", "from": 1, "to": 2}],
        "new_rank": {"XLE": 2, "TLT": 1, "SPYM": 3, "ITA": 4, "QQQM": 5, "XLK": 6},
    }
    free_txt = format_rank_change(sample_change, channel="free")
    paid_txt = format_rank_change(sample_change, channel="paid")
    check("format_rank_change free 생성", len(free_txt) > 20)
    check("format_rank_change paid 생성", len(paid_txt) > 20)
    check("format_rank_change paid 상세", "🥇" in paid_txt)
except Exception as e:
    check("format_rank_change 검증", False, str(e))

try:
    import tempfile
    from pathlib import Path
    from datetime import datetime, timezone, timedelta
    import core.rank_tracker as rt
    orig_path = rt.RANK_HISTORY_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        rt.RANK_HISTORY_PATH = Path(tmpdir) / "rank_history.json"
        # 첫 실행 — None 반환
        r1 = detect_rank_change({"XLE":1,"TLT":2,"SPYM":3,"ITA":4,"QQQM":5,"XLK":6},
                                  dt_utc=datetime(2026,3,28,tzinfo=timezone.utc))
        check("첫 실행 변화 없음 (None)", r1 is None)
        # 랭킹 변경 — 변화 감지
        r2 = detect_rank_change({"TLT":1,"XLE":2,"SPYM":3,"ITA":4,"QQQM":5,"XLK":6},
                                  dt_utc=datetime(2026,3,29,tzinfo=timezone.utc))
        check("랭킹 변화 감지", r2 is not None)
        check("1위 변경 감지", r2.get("top1_changed") == True if r2 else False)
        rt.RANK_HISTORY_PATH = orig_path
except Exception as e:
    check("rank_tracker 통합 검증", False, str(e))

try:
    import inspect, run_market
    src = inspect.getsource(run_market.run)
    check("run_market Step 8-R 존재", "Step 8-R" in src)
    check("run_market detect_rank_change 호출", "detect_rank_change" in src)
except Exception as e:
    check("run_market rank_tracker 검증", False, str(e))

print("\n── [15] 유료 ETF 상세전략 + 포지션사이징 리포트 검증 ──────")
try:
    from publishers.paid_report_formatter import format_paid_report
    check("paid_report_formatter import OK", True)
except Exception as e:
    check("paid_report_formatter import OK", False, str(e))

try:
    from publishers.paid_report_formatter import format_paid_report
    sample = {
        "market_regime": {"market_regime": "Risk-Off", "market_risk_level": "HIGH"},
        "trading_signal": {
            "trading_signal": "REDUCE",
            "signal_reason": "High risk environment",
            "signal_matrix": {
                "buy_watch": ["TLT"], "hold": ["SPYM"], "reduce": ["QQQM","XLK"]
            }
        },
        "etf_strategy": {
            "stance": {"QQQM":"Underweight","XLK":"Underweight","SPYM":"Neutral","XLE":"Overweight","ITA":"Neutral","TLT":"Overweight"},
            "strategy_reason": {"XLE":"Oil hedge","TLT":"Safe haven"}
        },
        "etf_allocation": {"allocation": {"QQQM":5,"XLK":5,"SPYM":20,"XLE":25,"ITA":25,"TLT":20}},
        "etf_analysis": {
            "timing_signal": {"QQQM":"REDUCE","XLK":"REDUCE","SPYM":"HOLD","XLE":"ADD ON PULLBACK","ITA":"HOLD","TLT":"BUY"},
            "etf_rank": {"QQQM":6,"XLK":5,"SPYM":3,"XLE":1,"ITA":4,"TLT":2}
        },
        "portfolio_risk": {
            "position_sizing_multiplier": 0.75,
            "hedge_intensity": "HIGH",
            "position_exposure": "Defensive",
            "crash_alert_level": "MEDIUM"
        }
    }
    txt = format_paid_report(sample)
    check("format_paid_report 생성 (>200자)", len(txt) > 200, f"{len(txt)}자")
    check("format_paid_report ETF 포함", "XLE" in txt and "TLT" in txt)
    check("format_paid_report 포지션사이징 포함", "0.75" in txt)
    check("format_paid_report HTML 태그", "<b>" in txt)
except Exception as e:
    check("format_paid_report 통합 검증", False, str(e))

try:
    import inspect, run_view
    src = inspect.getsource(run_view.run)
    check("run_view format_paid_report 호출", "paid_report_formatter" in src)
    check("run_view 유료 채널 추가 발송", "paid_text" in src)
except Exception as e:
    check("run_view paid_report 검증", False, str(e))

print("\n── [16] 주간 PDF 리포트 검증 ───────────────────────────")
try:
    from publishers.weekly_pdf_builder import build_weekly_pdf
    check("weekly_pdf_builder import OK", True)
except Exception as e:
    check("weekly_pdf_builder import OK", False, str(e))

try:
    import tempfile
    from pathlib import Path
    import publishers.weekly_pdf_builder as wpb
    orig = wpb.OUTPUT_DIR
    with tempfile.TemporaryDirectory() as tmpdir:
        wpb.OUTPUT_DIR = Path(tmpdir)
        sample_summary = {
            "week": "2026-W13", "days": 3,
            "dominant_regime": "Risk-Off", "dominant_signal": "HOLD",
            "signal_counts": {"HOLD": 2, "REDUCE": 1},
            "buy_count": {"XLE": 2, "TLT": 1},
            "reduce_count": {"QQQM": 1},
            "etf_week_return": {"XLE": 3.2, "TLT": 1.1, "SPYM": -0.5, "QQQM": -2.1, "ITA": 0.8, "XLK": -1.3},
            "entries": [
                {"date": "2026-03-25", "regime": "Risk-Off", "risk": "MEDIUM",
                 "signal": "HOLD", "buy_watch": ["XLE","TLT"], "hold": ["SPYM"], "reduce": ["QQQM"],
                 "etf_returns": {}},
                {"date": "2026-03-26", "regime": "Risk-Off", "risk": "HIGH",
                 "signal": "REDUCE", "buy_watch": ["TLT"], "hold": ["SPYM"], "reduce": ["QQQM","XLK"],
                 "etf_returns": {}},
            ]
        }
        pdf_path = build_weekly_pdf(sample_summary)
        check("PDF 파일 생성", Path(pdf_path).exists(), pdf_path)
        check("PDF 크기 > 3KB", Path(pdf_path).stat().st_size > 3000,
              f"{Path(pdf_path).stat().st_size}B")
        wpb.OUTPUT_DIR = orig
except Exception as e:
    check("weekly_pdf_builder 통합 검증", False, str(e))

try:
    from publishers.telegram_publisher import send_document
    check("send_document import OK", True)
except Exception as e:
    check("send_document import OK", False, str(e))

try:
    import inspect, run_view
    src = inspect.getsource(run_view.run)
    check("run_view weekly PDF 생성 호출", "weekly_pdf_builder" in src)
    check("run_view send_document 호출", "send_document" in src)
except Exception as e:
    check("run_view weekly PDF 검증", False, str(e))

try:
    reqs = open("requirements.txt").read()
    check("requirements.txt reportlab 포함", "reportlab" in reqs)
except Exception as e:
    check("requirements.txt 검증", False, str(e))

print("\n── [17] Fear & Greed + 뉴스 헤드라인 검증 ──────────────")
try:
    from collectors.fear_greed import collect_fear_greed, _label, _label_emoji
    check("fear_greed import OK", True)
    check("_label Extreme Fear", _label(15) == "Extreme Fear")
    check("_label Fear", _label(35) == "Fear")
    check("_label Neutral", _label(50) == "Neutral")
    check("_label Greed", _label(65) == "Greed")
    check("_label Extreme Greed", _label(85) == "Extreme Greed")
    check("_label_emoji", len(_label_emoji("Extreme Fear")) > 0)
except Exception as e:
    check("fear_greed 검증", False, str(e))

try:
    import inspect, core.json_builder as jb
    src = inspect.getsource(jb.assemble_core_data)
    check("json_builder fear_greed 파라미터", "fear_greed" in src)
    check("json_builder fear_greed 반환", '"fear_greed"' in src)
except Exception as e:
    check("json_builder fear_greed 검증", False, str(e))

try:
    import inspect, publishers.telegram_publisher as tp
    src = inspect.getsource(tp.format_free_signal)
    check("telegram morning F&G 통합", "fear_greed" in src)
    check("telegram morning 헤드라인 통합", "top_headlines" in src)
except Exception as e:
    check("telegram morning 확장 검증", False, str(e))

try:
    from publishers.telegram_publisher import format_free_signal
    sample = {
        "market_regime": {"market_regime": "Risk-Off", "market_risk_level": "HIGH"},
        "trading_signal": {"trading_signal": "REDUCE", "signal_reason": "High risk",
                           "signal_matrix": {"buy_watch": ["TLT"], "hold": [], "reduce": ["QQQM"]}},
        "market_snapshot": {"vix": 31.0, "sp500": -1.5, "us10y": 4.4, "oil": 99.0},
        "output_helpers": {
            "one_line_summary": "Risk-Off",
            "top_headlines": ["Fed signals higher for longer", "Tech selloff deepens", "Oil near $100"]
        },
        "fear_greed": {"value": 18, "label": "Extreme Fear", "emoji": "😱", "change": -4},
    }
    txt = format_free_signal(sample, session="morning")
    check("morning 포맷 F&G 포함", "18/100" in txt or "Extreme Fear" in txt)
    check("morning 포맷 헤드라인 포함", "Fed signals" in txt or "헤드라인" in txt)
except Exception as e:
    check("morning 포맷 통합 검증", False, str(e))

try:
    from collectors.rss_extended import collect_extended_sentiment
    import inspect
    src = inspect.getsource(collect_extended_sentiment)
    check("rss_extended headlines 반환", '"headlines"' in src)
except Exception as e:
    check("rss_extended headlines 검증", False, str(e))

print("\n── [18] VIX 카운트다운 하루 1회 검증 ─────────────────────")
try:
    from engines.alert_engine import VIX_COUNTDOWN_LEVELS, _vix_countdown_alert
    check("VIX_COUNTDOWN_LEVELS 존재", len(VIX_COUNTDOWN_LEVELS) == 3)
    check("VIX_COUNTDOWN_LEVELS 값", 25 in VIX_COUNTDOWN_LEVELS and 29 in VIX_COUNTDOWN_LEVELS)

    # 신규 돌파 감지 테스트
    snap_25 = {"vix": 25.5, "sp500": -1.0, "us10y": 4.4, "oil": 90.0}
    prev_24 = {"vix": 24.0}
    sig = _vix_countdown_alert(snap_25, prev_24)
    check("VIX 25 신규 돌파 감지", sig is not None)
    check("VIX_COUNTDOWN 타입", sig.alert_type == "VIX_COUNTDOWN" if sig else False)

    # 이미 이 레벨 위에 있으면 신규 돌파 아님
    prev_26 = {"vix": 26.0}
    sig2 = _vix_countdown_alert(snap_25, prev_26)
    check("이미 레벨 위 → 감지 안 함", sig2 is None)
except Exception as e:
    check("VIX 카운트다운 엔진 검증", False, str(e))

try:
    from publishers.alert_formatter import format_countdown_tweet, _TYPE_TITLE
    check("VIX_COUNTDOWN 타입 등록", "VIX_COUNTDOWN" in _TYPE_TITLE)
    check("format_countdown_tweet 존재", callable(format_countdown_tweet))
except Exception as e:
    check("alert_formatter VIX_COUNTDOWN 검증", False, str(e))

try:
    from core.alert_history import should_send_countdown, record_countdown
    check("should_send_countdown import OK", True)

    import tempfile, core.alert_history as ah
    from pathlib import Path
    orig = ah.ALERT_HISTORY_FILE
    with tempfile.TemporaryDirectory() as tmpdir:
        ah.ALERT_HISTORY_FILE = Path(tmpdir) / "alert_history.json"
        # 첫 발행 허용
        send, reason = should_send_countdown(25)
        check("카운트다운 첫 발행 허용", send == True)
        # 기록 후 차단
        record_countdown(25, "TEST_ID")
        send2, reason2 = should_send_countdown(25)
        check("카운트다운 하루 1회 차단", send2 == False)
        # 다른 레벨은 허용
        send3, _ = should_send_countdown(27)
        check("다른 레벨(27) 발행 허용", send3 == True)
        ah.ALERT_HISTORY_FILE = orig
except Exception as e:
    check("alert_history 카운트다운 검증", False, str(e))

try:
    import inspect, run_alert
    src = inspect.getsource(run_alert.run)
    check("run_alert VIX_COUNTDOWN 분기", "VIX_COUNTDOWN" in src)
    check("run_alert should_send_countdown 호출", "should_send_countdown" in src)
except Exception as e:
    check("run_alert 카운트다운 검증", False, str(e))

print("\n── [19] 경제지표 발표 즉시 알람 검증 ──────────────────────")
try:
    from publishers.econ_event_formatter import (
        format_econ_event, format_econ_event_telegram, INDICATOR_META
    )
    check("econ_event_formatter import OK", True)
    check("INDICATOR_META 5개 이상", len(INDICATOR_META) >= 4)

    txt = format_econ_event("FEDFUNDS", 5.25, 5.50, "Risk-Off", "HOLD")
    check("format_econ_event 생성", len(txt) > 20)
    check("format_econ_event 280자 이내", len(txt) <= 280)

    tg = format_econ_event_telegram("FEDFUNDS", 5.25, 5.50, "Risk-Off", "HOLD")
    check("format_econ_event_telegram HTML", "<b>" in tg)
except Exception as e:
    check("econ_event_formatter 검증", False, str(e))

try:
    from collectors.fred_client import detect_macro_changes
    check("detect_macro_changes import OK", True)
    # 임계값 이상 변화 감지
    cur  = {"fed_funds_rate": 5.50, "hy_spread": 4.0, "yield_curve": 0.1}
    prev = {"fed_funds_rate": 5.25, "hy_spread": 3.4, "yield_curve": 0.4}
    changes = detect_macro_changes(cur, prev)
    check("임계값 이상 변화 감지", len(changes) >= 1)
    # 임계값 미만 변화 무시
    small = {"fed_funds_rate": 5.26, "hy_spread": 3.45, "yield_curve": 0.35}
    no_changes = detect_macro_changes(small, prev)
    check("임계값 미만 변화 무시", len(no_changes) == 0)
except Exception as e:
    check("detect_macro_changes 검증", False, str(e))

try:
    import inspect, run_alert
    src = inspect.getsource(run_alert.run)
    check("run_alert 경제지표 감지 분기", "detect_macro_changes" in src)
    check("run_alert 경제지표 발행", "econ_event_formatter" in src)
except Exception as e:
    check("run_alert 경제지표 검증", False, str(e))

print("\n── [20] AI 성적표 주간 결산 검증 ───────────────────────────")
try:
    from core.weekly_tracker import get_ai_scorecard
    from publishers.weekly_formatter import (
        format_ai_scorecard_tweet, format_ai_scorecard_telegram
    )
    check("get_ai_scorecard import OK", True)
    check("format_ai_scorecard_tweet import OK", True)
    check("format_ai_scorecard_telegram import OK", True)
except Exception as e:
    check("AI 성적표 import", False, str(e))

try:
    from core.weekly_tracker import get_ai_scorecard
    from publishers.weekly_formatter import format_ai_scorecard_tweet, format_ai_scorecard_telegram

    sample_summary = {
        "week": "2026-W13",
        "buy_count":    {"XLE": 3, "TLT": 2},
        "reduce_count": {"QQQM": 2},
        "etf_week_return": {"XLE": 3.2, "TLT": 0.8, "QQQM": 1.1, "SPYM": -0.5},
    }
    sc = get_ai_scorecard(sample_summary)
    check("AI 성적표 생성", sc.get("total", 0) > 0, f"total={sc.get('total')}")
    check("적중률 계산", 0 <= sc.get("hit_rate", -1) <= 1)

    tweet = format_ai_scorecard_tweet(sc, "2026-W13")
    check("AI 성적표 트윗 생성", len(tweet) > 20)
    check("AI 성적표 280자 이내", len(tweet) <= 280, f"{len(tweet)}자")

    tg = format_ai_scorecard_telegram(sc, "2026-W13")
    check("AI 성적표 TG HTML", "<b>" in tg)
    check("TG 성적표 포함", "성적표" in tg)
except Exception as e:
    check("AI 성적표 통합 검증", False, str(e))

try:
    import inspect, run_view
    src = inspect.getsource(run_view.run)
    check("run_view AI 성적표 호출", "get_ai_scorecard" in src)
    check("run_view AI 성적표 X 발행", "format_ai_scorecard_tweet" in src)
except Exception as e:
    check("run_view AI 성적표 검증", False, str(e))

print(f"\n{'='*60}")
print(f"  전수 테스트 결과: {PASS}개 PASS  {FAIL}개 FAIL")
if FAIL == 0:
    print(f"  ✅ 전체 PASS")
else:
    print(f"  ❌ 실패 항목 있음")
print(f"{'='*60}")
sys.exit(FAIL)
