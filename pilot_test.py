"""
pilot_test.py (v1.12.0)
=======================
파일럿 테스트 — 외부 API 없이 픽스처로 전체 파이프라인 검증.
v1.5.0 변경: Reddit 제거 → 다중 RSS 감성 엔진 테스트 추가

실행: python pilot_test.py --round 1
     python pilot_test.py --round 2
     python pilot_test.py --round all

Round 1: Risk-Off / HIGH Risk — RSS 감성 Bearish 시나리오
Round 2: Risk-On  / LOW Risk  — RSS 감성 Bullish + 중복 차단
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("FRED_API_KEY", "TEST_SKIP")
os.environ.setdefault("X_API_KEY", "TEST_SKIP")
os.environ.setdefault("X_API_SECRET", "TEST_SKIP")
os.environ.setdefault("X_ACCESS_TOKEN", "TEST_SKIP")
os.environ.setdefault("X_ACCESS_TOKEN_SECRET", "TEST_SKIP")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pilot_test")

# ─── 픽스처 ───────────────────────────────────────────────────

FIXTURE_RISK_OFF = {
    "snapshot": {
        "sp500": -1.51, "nasdaq": -2.01,
        "vix": 28.5, "us10y": 4.62,
        "oil": 95.4, "dollar_index": 104.8,
    },
    "fred": {
        "fed_funds_rate": 5.25, "hy_spread": 4.8,
        "yield_curve": -0.3, "credit_stress": "Moderate",
        "yield_curve_inverted": True,
    },
    "news_sentiment": "Bearish",
    "session": "morning",
}

FIXTURE_RISK_ON = {
    "snapshot": {
        "sp500": 0.95, "nasdaq": 1.42,
        "vix": 14.2, "us10y": 3.8,
        "oil": 68.5, "dollar_index": 100.2,
    },
    "fred": {
        "fed_funds_rate": 4.25, "hy_spread": 2.8,
        "yield_curve": 0.5, "credit_stress": "Low",
        "yield_curve_inverted": False,
    },
    "news_sentiment": "Bullish",
    "session": "close",
}

# RSS 감성 테스트용 픽스처 헤드라인
RSS_BEARISH_HEADLINES = [
    "S&P 500 drops as recession fears mount",
    "Fed signals more rate hikes ahead",
    "Wall Street selloff deepens amid inflation concerns",
    "Stock market falls sharply on weak economic data",
    "Investors panic as VIX surges to 30",
    "Oil shock threatens global growth outlook",
    "Credit markets signal deepening financial stress",
    "Nasdaq plunges as tech stocks face massive selloff",
]

RSS_BULLISH_HEADLINES = [
    "S&P 500 rallies to record high on strong earnings",
    "Fed signals rate cuts ahead boosting markets",
    "Wall Street surges as inflation cools sharply",
    "Strong jobs report beats all expectations",
    "Tech stocks soar on AI growth optimism",
    "Risk appetite returns as VIX drops below 15",
    "ETF inflows hit record as investors embrace risk",
    "Markets gain on positive economic outlook",
]


# ─── 공통 유틸 ────────────────────────────────────────────────

class PilotTestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed: list = []
        self.failed: list = []

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        icon = "✅" if condition else "❌"
        status = "PASS" if condition else "FAIL"
        msg = f"  {icon} [{status}] {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        (self.passed if condition else self.failed).append(label)
        return condition

    def summary(self) -> dict:
        total = len(self.passed) + len(self.failed)
        return {
            "name": self.name,
            "total": total,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "success": len(self.failed) == 0,
        }


def run_engine_pipeline(fixture: dict) -> tuple:
    from engines.macro_engine import run_macro_engine
    from engines.regime_engine import run_regime_engine
    from engines.etf_engine import run_etf_engine
    from engines.risk_engine import run_risk_engine
    from core.json_builder import assemble_core_data

    snapshot = fixture["snapshot"]
    fred = fixture["fred"]
    news = fixture["news_sentiment"]
    session = fixture["session"]

    etf_prices = {
        "QQQM": {"price": 180.0, "change_pct": snapshot["nasdaq"] * 0.9},
        "XLK":  {"price": 210.0, "change_pct": snapshot["nasdaq"] * 0.85},
        "SPYM": {"price": 40.0,  "change_pct": snapshot["sp500"] * 0.3},
        "XLE":  {"price": 90.0,  "change_pct": 0.5},
        "ITA":  {"price": 125.0, "change_pct": 0.3},
        "TLT":  {"price": 92.0,  "change_pct": -0.2},
    }

    macro = run_macro_engine(snapshot, fred, news)
    regime_result = run_regime_engine(macro["market_score"], macro["signals"], snapshot)
    market_regime = {k: regime_result[k] for k in
                     ["market_regime", "market_risk_level", "regime_reason"]}

    etf_result = run_etf_engine(
        regime=regime_result["market_regime"],
        risk_level=regime_result["market_risk_level"],
        market_score=macro["market_score"],
        etf_prices=etf_prices,
    )
    risk_result = run_risk_engine(
        regime=regime_result["market_regime"],
        risk_level=regime_result["market_risk_level"],
        composite_score=regime_result["composite_risk_score"],
        market_score=macro["market_score"],
        signals=macro["signals"],
        etf_analysis=etf_result["etf_analysis"],
        etf_strategy=etf_result["etf_strategy"],
        etf_allocation=etf_result["etf_allocation"],
        session_type=session,
    )
    data = assemble_core_data(
        snapshot=snapshot,
        market_regime=market_regime,
        market_score=macro["market_score"],
        etf_analysis=etf_result["etf_analysis"],
        etf_strategy=etf_result["etf_strategy"],
        etf_allocation=etf_result["etf_allocation"],
        portfolio_risk=risk_result["portfolio_risk"],
        trading_signal=risk_result["trading_signal"],
        output_helpers=risk_result["output_helpers"],
    )
    return data, regime_result


# ─── RSS 감성 엔진 단위 테스트 ──────────────────────────────

def test_rss_sentiment_engine(t: PilotTestResult):
    """v1.5.0 신규: rss_extended 감성 엔진 단위 검증"""
    print("\n[RSS 감성 엔진 단위 테스트]")
    from collectors.rss_extended import _score_headline, _dedup_headlines

    # 부정어 처리 검증
    score_plain = _score_headline("markets rally strongly")
    score_negated = _score_headline("no rally despite rate cuts")
    t.check("부정어 처리: 'no rally' < 'rally'",
            score_negated < score_plain,
            f"plain={score_plain:.1f} negated={score_negated:.1f}")

    # 강세 헤드라인 점수 양수
    bull_score = _score_headline("S&P 500 surges to record high on strong earnings")
    t.check("강세 헤드라인 → 양수 점수", bull_score > 0, f"score={bull_score:.1f}")

    # 약세 헤드라인 점수 음수
    bear_score = _score_headline("market crash deepens recession fears")
    t.check("약세 헤드라인 → 음수 점수", bear_score < 0, f"score={bear_score:.1f}")

    # 중립 헤드라인 0에 가까움
    neutral_score = _score_headline("Fed holds rates steady at meeting")
    t.check("중립 헤드라인 → 낮은 절대값", abs(neutral_score) <= 1.0,
            f"score={neutral_score:.1f}")

    # dedup 기능 검증
    duped = [
        "S&P 500 rallies on Fed news",
        "S&P 500 rallies on Fed news",   # 완전 동일
        "markets surge higher",
    ]
    unique = _dedup_headlines(duped)
    t.check("dedup: 동일 헤드라인 제거", len(unique) == 2, f"dedup결과={len(unique)}건")

    # 픽스처 기반 Bearish 감성 집계 검증
    from collectors.rss_extended import _aggregate_sentiment
    mock_results = [{
        "name": "TestSource",
        "headlines": RSS_BEARISH_HEADLINES,
        "count": len(RSS_BEARISH_HEADLINES),
        "success": True,
    }]
    mock_configs = [{"name": "TestSource", "weight": 1.0}]
    bear_result = _aggregate_sentiment(mock_results, mock_configs)
    t.check("Bearish 헤드라인 → Bearish 감성",
            bear_result["news_sentiment"] == "Bearish",
            f"sentiment={bear_result['news_sentiment']} score={bear_result['net_weighted_score']:.2f}")

    # Bullish 집계 검증
    mock_results[0]["headlines"] = RSS_BULLISH_HEADLINES
    bull_result = _aggregate_sentiment(mock_results, mock_configs)
    t.check("Bullish 헤드라인 → Bullish 감성",
            bull_result["news_sentiment"] == "Bullish",
            f"sentiment={bull_result['news_sentiment']} score={bull_result['net_weighted_score']:.2f}")

    # 소스 전체 실패 시 Neutral 반환
    empty_results = [{"name": "Dead", "headlines": [], "count": 0, "success": False}]
    empty_agg = _aggregate_sentiment(empty_results, [{"name": "Dead", "weight": 1.0}])
    t.check("전체 소스 실패 → Neutral",
            empty_agg["news_sentiment"] == "Neutral",
            f"sentiment={empty_agg['news_sentiment']}")

    # Reddit 비활성화 stub 검증
    from collectors.reddit_client import collect_reddit_sentiment
    reddit_stub = collect_reddit_sentiment()
    t.check("Reddit stub: available=False", reddit_stub["available"] is False)
    t.check("Reddit stub: reason 필드 존재", "reason" in reddit_stub)


# ─── Round 1 ─────────────────────────────────────────────────

def run_round1():
    print("\n" + "=" * 60)
    print("  파일럿 테스트 Round 1 — Risk-Off + Bearish RSS 시나리오")
    print("=" * 60)
    t = PilotTestResult("Round 1")

    # 이력 초기화
    from config.settings import HISTORY_FILE
    if HISTORY_FILE.exists():
        HISTORY_FILE.unlink()
    print("  [초기화] history.json 리셋")

    # ── RSS 감성 엔진 단위 테스트 ──────────────────────────────
    test_rss_sentiment_engine(t)

    # ── 엔진 파이프라인 ───────────────────────────────────────
    print("\n[엔진 파이프라인]")
    try:
        data, regime_result = run_engine_pipeline(FIXTURE_RISK_OFF)
        t.check("엔진 파이프라인 정상 실행", True)
    except Exception as e:
        t.check("엔진 파이프라인 정상 실행", False, str(e))
        return t.summary()

    regime = data["market_regime"]["market_regime"]
    risk_level = data["market_regime"]["market_risk_level"]
    t.check("Regime 결정 완료", bool(regime), f"regime={regime}")
    t.check("Risk MEDIUM 이상",
            risk_level in ("MEDIUM", "HIGH"),
            f"VIX=28.5 → {risk_level}")

    # ── Allocation 검증 ───────────────────────────────────────
    alloc = data["etf_allocation"]["allocation"]
    total_w = data["etf_allocation"]["total_weight"]
    t.check("total_weight == 100", total_w == 100)
    t.check("모든 Core ETF 포함",
            all(e in alloc for e in ["QQQM","XLK","SPYM","XLE","ITA","TLT"]))
    t.check("Risk-Off: QQQM 비중 ≤ 10%",
            alloc.get("QQQM", 99) <= 10,
            f"QQQM={alloc.get('QQQM')}%")

    # ── Validation ─────────────────────────────────────────────
    from core.validator import validate_data, validate_output
    dr = validate_data(data)
    or_ = validate_output(data)
    t.check("validate_data PASS", dr["status"] == "PASS", str(dr["errors"]))
    t.check("validate_output PASS", or_["status"] == "PASS", str(or_["errors"]))

    # ── JSON 저장 ──────────────────────────────────────────────
    from core.json_builder import build_envelope, save_core_data
    envelope = build_envelope("pilot_test_r1", data)
    save_core_data(envelope, dr, or_)
    t.check("core_data.json 저장", True)

    # ── 트윗 포맷 ──────────────────────────────────────────────
    from publishers.x_formatter import format_market_snapshot_tweet, format_thread_posts
    tweet = format_market_snapshot_tweet(data, "Morning Brief 🌅")
    t.check("트윗 생성 성공", bool(tweet))
    t.check("트윗 280자 이내", len(tweet) <= 280, f"{len(tweet)}자")
    t.check("해시태그 포함", "#ETF" in tweet)

    posts = format_thread_posts(data)
    t.check("쓰레드 포스트 ≥ 4개", len(posts) >= 4, f"{len(posts)}개")
    t.check("모든 포스트 280자 이내",
            all(len(p) <= 280 for p in posts),
            f"max={max(len(p) for p in posts)}자")

    # ── 중복 검사 최초 ─────────────────────────────────────────
    from core.duplicate_checker import is_duplicate, record_published
    t.check("최초 실행: 중복 없음", not is_duplicate(tweet, data))
    record_published(tweet, data, "PILOT_R1")
    t.check("발행 이력 기록 성공", True)

    # ── DRY_RUN 발행 ───────────────────────────────────────────
    from publishers.x_publisher import publish_tweet
    result = publish_tweet(tweet)
    t.check("DRY_RUN 발행 성공", result["success"])
    t.check("DRY_RUN 모드 확인", result["dry_run"] is True)

    # ── 미리보기 ───────────────────────────────────────────────
    print("\n[발행 예정 트윗 미리보기]")
    print("─" * 50)
    print(tweet)
    print("─" * 50)
    print(f"  Allocation: {alloc}")

    return t.summary()


# ─── Round 2 ─────────────────────────────────────────────────

def run_round2():
    print("\n" + "=" * 60)
    print("  파일럿 테스트 Round 2 — Risk-On + Bullish RSS + 중복 차단")
    print("=" * 60)
    t = PilotTestResult("Round 2")

    # ── 엔진 파이프라인 ───────────────────────────────────────
    print("\n[엔진 파이프라인 — Risk-On]")
    try:
        data_on, _ = run_engine_pipeline(FIXTURE_RISK_ON)
        t.check("Risk-On 파이프라인 성공", True)
    except Exception as e:
        t.check("Risk-On 파이프라인 성공", False, str(e))
        return t.summary()

    regime_on = data_on["market_regime"]["market_regime"]
    risk_on = data_on["market_regime"]["market_risk_level"]
    alloc_on = data_on["etf_allocation"]["allocation"]

    t.check("Risk-On Regime 감지", risk_on in ("LOW", "MEDIUM"), f"{risk_on}")
    t.check("Risk-On: QQQM 비중 ≥ ITA 비중",
            alloc_on.get("QQQM", 0) >= alloc_on.get("ITA", 0),
            f"QQQM={alloc_on.get('QQQM')}% ITA={alloc_on.get('ITA')}%")

    # ── Validation ─────────────────────────────────────────────
    from core.validator import validate_data, validate_output
    dr = validate_data(data_on)
    or_ = validate_output(data_on)
    t.check("validate_data PASS", dr["status"] == "PASS", str(dr["errors"]))
    t.check("validate_output PASS", or_["status"] == "PASS", str(or_["errors"]))

    # ── 트윗 ───────────────────────────────────────────────────
    from publishers.x_formatter import format_market_snapshot_tweet
    tweet_on = format_market_snapshot_tweet(data_on, "Close Summary 🔔")
    t.check("트윗 생성", bool(tweet_on))
    t.check("280자 이내", len(tweet_on) <= 280, f"{len(tweet_on)}자")

    # ── 중복 검사 — 다른 레짐 → 허용 ─────────────────────────
    print("\n[중복 검사]")
    from core.duplicate_checker import is_duplicate, record_published
    t.check("다른 레짐: 중복 아님", not is_duplicate(tweet_on, data_on),
            f"Round1(Risk-Off) ≠ Round2({regime_on})")
    record_published(tweet_on, data_on, "PILOT_R2")

    # 동일 재실행 → 차단
    t.check("동일 레짐 재실행 → 중복 차단",
            is_duplicate(tweet_on, data_on),
            "이력 등록 후 재실행 → 차단되어야 함")

    # ── core_data 저장/로드 왕복 ───────────────────────────────
    from core.json_builder import build_envelope, save_core_data, load_core_data
    env_on = build_envelope("pilot_test_r2", data_on)
    save_core_data(env_on, dr, or_)
    loaded = load_core_data()
    t.check("core_data.json 로드 성공", True)
    t.check("로드 레짐 일치",
            loaded["data"]["market_regime"]["market_regime"] == regime_on,
            f"regime={regime_on}")

    # ── publish_payload 확인 ───────────────────────────────────
    from config.settings import PUBLISH_PAYLOAD_FILE
    with open(PUBLISH_PAYLOAD_FILE) as f:
        payload = json.load(f)
    t.check("publish_ready=True", payload.get("publish_ready") is True)

    # ── settings 버전 확인 ─────────────────────────────────────
    print("\n[설정 검증]")
    from config.settings import SYSTEM_VERSION, RSS_SOURCES
    t.check("시스템 버전 v1.19.0", SYSTEM_VERSION == "v1.19.0")
    t.check("RSS 소스 5개 이상", len(RSS_SOURCES) >= 5, f"{len(RSS_SOURCES)}개")

    # ── 미리보기 ───────────────────────────────────────────────
    print("\n[발행 예정 트윗 미리보기]")
    print("─" * 50)
    print(tweet_on)
    print("─" * 50)
    print(f"  Allocation: {alloc_on}")

    return t.summary()


# ─── 최종 리포트 ─────────────────────────────────────────────

def print_final_report(r1: dict, r2: dict):
    print("\n" + "=" * 60)
    print("  파일럿 테스트 최종 결과 (v1.19.0 — AI 성적표 주간 결산)")
    print("=" * 60)
    for r in [r1, r2]:
        icon = "✅" if r["success"] else "❌"
        label = "성공" if r["success"] else "실패"
        print(f"  {icon} {r['name']}: {r['passed']}/{r['total']} PASS ({label})")

    all_ok = r1["success"] and r2["success"]
    print()
    if all_ok:
        print("  🎉 파일럿 테스트 2회 완료 — 특이사항 없음")
        print("  → v1.19.0 변경사항: AI 성적표 포맷 + weekly session 통합")
        print("  → Git 업로드 진행 가능")
    else:
        for r in [r1, r2]:
            if not r["success"]:
                print(f"  ⚠️  [{r['name']}] 실패 항목 재확인 필요")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Investment OS Pilot Test v1.12.0")
    parser.add_argument("--round", choices=["1", "2", "all"], default="all")
    args = parser.parse_args()

    r1 = {"name": "Round 1", "success": True, "passed": 0, "total": 0}
    r2 = {"name": "Round 2", "success": True, "passed": 0, "total": 0}

    if args.round in ("1", "all"):
        r1 = run_round1()
    if args.round in ("2", "all"):
        r2 = run_round2()

    print_final_report(r1, r2)
    sys.exit(0 if (r1["success"] and r2["success"]) else 1)


if __name__ == "__main__":
    main()
