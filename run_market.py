"""
run_market.py (v1.5.0)
=======================
역할: 취합(수집) + 분석
실행: python run_market.py [--session morning|intraday|close]

v1.5.0 변경:
  - Reddit 제거 → 다중 RSS(rss_extended) 단독 감성 사용
  - 감성 통합 로직 단순화

파이프라인:
  수집(yfinance / FRED / 다중RSS)
  → Macro Engine → Regime Engine → ETF Engine → Risk Engine
  → JSON 조립 → Validation → core_data.json 저장
"""
import argparse
import logging
import sys
from datetime import datetime, timezone

from config.settings import LOG_LEVEL, DRY_RUN

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_market")


def _detect_session() -> str:
    try:
        import pytz
        kst = pytz.timezone("Asia/Seoul")
        now_kst = datetime.now(kst)
        hour = now_kst.hour
    except ImportError:
        hour = datetime.utcnow().hour

    if 5 <= hour < 9:
        return "morning"
    elif 22 <= hour or hour < 2:
        return "intraday"
    else:
        return "close"


def run(session: str) -> dict:
    logger.info("=" * 50)
    logger.info(f"[run_market] 시작 — session={session} | DRY_RUN={DRY_RUN}")
    logger.info("=" * 50)

    # ── Step 1: 데이터 수집 ────────────────────────────────────
    logger.info("[Step 1] 시장 데이터 수집")

    from collectors.yahoo_finance import collect_market_snapshot, collect_etf_prices
    from collectors.fred_client import collect_macro_data
    from collectors.news_rss import collect_news_sentiment   # v1.5.0: rss_extended 위임

    snapshot = collect_market_snapshot()
    etf_prices = collect_etf_prices()
    fred_data = collect_macro_data()
    news_result = collect_news_sentiment()

    # v1.5.0: 다중 RSS 단독 감성 사용 (Reddit 제거)
    combined_sentiment = news_result.get("news_sentiment", "Neutral")
    sources_ok = news_result.get("sources_ok", 0)
    sources_fail = news_result.get("sources_fail", 0)

    logger.info(
        f"[Step 1] 완료 — 감성={combined_sentiment} | "
        f"RSS소스 {sources_ok}성공/{sources_fail}실패"
    )

    # ── Step 2: Macro Engine ───────────────────────────────────
    logger.info("[Step 2] Macro Engine 실행")
    from engines.macro_engine import run_macro_engine
    macro_result = run_macro_engine(snapshot, fred_data, combined_sentiment)
    signals = macro_result["signals"]
    market_score = macro_result["market_score"]

    # ── Step 3: Regime Engine ──────────────────────────────────
    logger.info("[Step 3] Regime Engine 실행")
    from engines.regime_engine import run_regime_engine
    regime_result = run_regime_engine(market_score, signals, snapshot)
    market_regime = {
        "market_regime": regime_result["market_regime"],
        "market_risk_level": regime_result["market_risk_level"],
        "regime_reason": regime_result["regime_reason"],
    }
    composite_score = regime_result["composite_risk_score"]

    # ── Step 4: ETF Engine ─────────────────────────────────────
    logger.info("[Step 4] ETF Engine 실행")
    from engines.etf_engine import run_etf_engine
    etf_result = run_etf_engine(
        regime=regime_result["market_regime"],
        risk_level=regime_result["market_risk_level"],
        market_score=market_score,
        etf_prices=etf_prices,
    )

    # ── Step 5: Risk Engine ────────────────────────────────────
    logger.info("[Step 5] Risk Engine 실행")
    from engines.risk_engine import run_risk_engine
    risk_result = run_risk_engine(
        regime=regime_result["market_regime"],
        risk_level=regime_result["market_risk_level"],
        composite_score=composite_score,
        market_score=market_score,
        signals=signals,
        etf_analysis=etf_result["etf_analysis"],
        etf_strategy=etf_result["etf_strategy"],
        etf_allocation=etf_result["etf_allocation"],
        session_type=session,
    )

    # ── Step 6: JSON 조립 ──────────────────────────────────────
    logger.info("[Step 6] JSON Core Data 조립")
    from core.json_builder import assemble_core_data, build_envelope, save_core_data
    data = assemble_core_data(
        snapshot=snapshot,
        market_regime=market_regime,
        market_score=market_score,
        etf_analysis=etf_result["etf_analysis"],
        etf_strategy=etf_result["etf_strategy"],
        etf_allocation=etf_result["etf_allocation"],
        portfolio_risk=risk_result["portfolio_risk"],
        trading_signal=risk_result["trading_signal"],
        output_helpers=risk_result["output_helpers"],
    )
    envelope = build_envelope(f"run market ({session})", data)

    # ── Step 7: Validation ─────────────────────────────────────
    logger.info("[Step 7] Validation 실행")
    from core.validator import validate_data, validate_output
    data_validation = validate_data(data)
    output_validation = validate_output(data)

    # ── Step 8: 저장 ───────────────────────────────────────────
    logger.info("[Step 8] core_data.json 저장")
    save_core_data(envelope, data_validation, output_validation)

    summary = {
        "session": session,
        "regime": market_regime["market_regime"],
        "risk_level": market_regime["market_risk_level"],
        "trading_signal": risk_result["trading_signal"]["trading_signal"],
        "data_validation": data_validation["status"],
        "output_validation": output_validation["status"],
        "publish_eligible": (
            data_validation["status"] == "PASS"
            and output_validation["status"] == "PASS"
        ),
        "sentiment": combined_sentiment,
        "rss_sources_ok": sources_ok,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("=" * 50)
    logger.info(f"[run_market] 완료")
    logger.info(f"  레짐       : {summary['regime']}")
    logger.info(f"  리스크     : {summary['risk_level']}")
    logger.info(f"  시그널     : {summary['trading_signal']}")
    logger.info(f"  감성       : {summary['sentiment']} (RSS {sources_ok}소스)")
    logger.info(f"  validate_data  : {summary['data_validation']}")
    logger.info(f"  validate_output: {summary['output_validation']}")
    logger.info(f"  발행 가능  : {summary['publish_eligible']}")
    logger.info("=" * 50)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Investment OS — Market Analysis v1.5.0")
    parser.add_argument(
        "--session",
        choices=["morning", "intraday", "close", "auto"],
        default="auto",
    )
    args = parser.parse_args()
    session = _detect_session() if args.session == "auto" else args.session
    logger.info(f"[run_market] 세션={session}")

    try:
        result = run(session)
        if not result["publish_eligible"]:
            logger.warning("[run_market] Validation FAIL — run_view.py 실행 차단")
            sys.exit(2)
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[run_market] 예외 발생: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
