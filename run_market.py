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

    from collectors.yahoo_finance import collect_market_snapshot, collect_etf_prices, collect_fx_rates
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

    # FX 환율 수집 (USD/KRW, EUR/USD, USD/JPY)
    fx_rates = collect_fx_rates()
    logger.info(f"[Step 1-FX] FX 환율 수집 완료: {fx_rates}")

    # Fear & Greed Index 수집
    fear_greed = None
    try:
        from collectors.fear_greed import collect_fear_greed
        fear_greed = collect_fear_greed()
    except Exception as e:
        logger.warning(f"[Step 1-FG] Fear & Greed 수집 실패 (영향 없음): {e}")

    # BTC/ETH 가격 수집
    crypto = {}
    try:
        from collectors.yahoo_finance import collect_crypto_prices
        crypto = collect_crypto_prices()
        logger.info(f"[Step 1-CR] 크립토 수집 완료: {crypto}")
    except Exception as e:
        logger.warning(f"[Step 1-CR] 크립토 수집 실패 (영향 없음): {e}")

    # 뉴스 헤드라인 3줄 요약 (Claude API, 미설정 시 스킵)
    news_summary = None
    try:
        from collectors.news_summarizer import summarize_headlines
        headlines = news_result.get("headlines", [])
        if headlines:
            news_summary = summarize_headlines(headlines)
            if news_summary:
                logger.info(f"[Step 1-NS] 뉴스 요약 완료: {len(news_summary.get('summary', []))}줄")
    except Exception as e:
        logger.warning(f"[Step 1-NS] 뉴스 요약 실패 (영향 없음): {e}")

    logger.info(
        f"[Step 1] 완료 — 감성={combined_sentiment} | "
        f"RSS소스 {sources_ok}성공/{sources_fail}실패"
    )

    # ── Step 1-G: Gemini 뉴스 심층 분석 (B-16) ───────────────
    news_analysis = None
    try:
        from collectors.news_analyzer import analyze_headlines
        headlines = news_result.get("headlines", [])
        if headlines:
            news_analysis = analyze_headlines(headlines)
            if news_analysis and news_analysis.get("success"):
                logger.info(
                    f"[Step 1-G] Gemini 뉴스 분석 완료 | "
                    f"감성={news_analysis.get('overall_sentiment', '?')} | "
                    f"이슈 {len(news_analysis.get('top_issues', []))}건"
                )
            else:
                logger.info("[Step 1-G] Gemini 뉴스 분석 스킵 (fallback)")
    except Exception as e:
        logger.warning(f"[Step 1-G] Gemini 뉴스 분석 실패 (영향 없음): {e}")

    # ── Step 2: Macro Engine ───────────────────────────────────
    # Tier 1+2 확장 (2026-04-01): fear_greed, crypto, etf_prices,
    # tier2_data를 macro_engine에 전달하여 확장 시그널 산출에 활용
    logger.info("[Step 2] Macro Engine 실행 (Tier 1+2 확장 시그널 포함)")

    # Tier 2 추가 시장 데이터 수집 (RSP, VIX3M, EEM)
    tier2_data = {}
    try:
        from collectors.yahoo_finance import collect_tier2_market_data
        tier2_data = collect_tier2_market_data()
        logger.info(f"[Step 2-T2] Tier 2 데이터 수집 완료: {tier2_data}")
    except Exception as e:
        logger.warning(f"[Step 2-T2] Tier 2 데이터 수집 실패 (영향 없음): {e}")

    from engines.macro_engine import run_macro_engine
    macro_result = run_macro_engine(
        snapshot,
        fred_data,
        combined_sentiment,
        fear_greed=fear_greed,       # T1-1: Fear & Greed → sentiment 보강
        crypto=crypto,               # T1-2: BTC → risk appetite 보조
        etf_prices=etf_prices,       # T1-4: XLF/GLD → 금융안정 보강
        tier2_data=tier2_data,       # T2-1,2,5: Breadth/VolTerm/EM
        # T1-3: snapshot 내 sp500/nasdaq 등락률은 이미 snapshot에 포함
        # T2-3,4: fred_data 내 initial_claims/inflation_exp는 이미 fred_data에 포함
    )
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

    # ── Step 3.5: Gemini 레짐 크로스체크 (C-8) ─────────────────
    try:
        from engines.regime_engine import gemini_cross_check
        cross_check = gemini_cross_check(
            regime_result=regime_result,
            market_score=market_score,
            signals=signals,
            news_analysis=news_analysis,
        )
        if cross_check.get("checked") and not cross_check.get("agree"):
            logger.warning(
                f"[Step 3.5] ⚠️ Gemini 레짐 불일치: "
                f"rule={regime_result['market_regime']} → "
                f"제안={cross_check.get('suggested_regime')} | "
                f"{cross_check.get('reason')}"
            )
            # TG 알림 (불일치 시)
            try:
                from publishers.telegram_publisher import send_message
                send_message(
                    f"⚠️ [레짐 크로스체크 불일치]\n"
                    f"Rule: {regime_result['market_regime']}\n"
                    f"Gemini 제안: {cross_check.get('suggested_regime')}\n"
                    f"사유: {cross_check.get('reason')}\n"
                    f"confidence: {cross_check.get('confidence', 0):.1f}"
                )
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[Step 3.5] Gemini 크로스체크 실패 (무시): {e}")

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
        fx_rates=fx_rates,
        fear_greed=fear_greed,
        crypto=crypto,
        news_summary=news_summary,
        news_analysis=news_analysis,
        macro_data=fred_data,
        snapshot=snapshot,
        market_regime=market_regime,
        market_score=market_score,
        signals=signals,
        etf_analysis=etf_result["etf_analysis"],
        etf_strategy=etf_result["etf_strategy"],
        etf_allocation=etf_result["etf_allocation"],
        portfolio_risk=risk_result["portfolio_risk"],
        trading_signal=risk_result["trading_signal"],
        output_helpers=risk_result["output_helpers"],
    )
    envelope = build_envelope(f"run market ({session})", data)

    # top_headlines 주입 — output_helpers에 RSS 상위 헤드라인 추가
    try:
        headlines = news_result.get("headlines", [])
        if headlines:
            data["output_helpers"]["top_headlines"] = headlines[:3]
    except Exception:
        pass

    # ── Step 7: Validation ─────────────────────────────────────
    logger.info("[Step 7] Validation 실행")
    from core.validator import validate_data, validate_output
    data_validation = validate_data(data)
    output_validation = validate_output(data)

    # ── Step 8: 저장 ───────────────────────────────────────────
    logger.info("[Step 8] core_data.json 저장")
    save_core_data(envelope, data_validation, output_validation)

    # ── Step 8-W: 주간 성적표 누적 기록 ────────────────────────
    try:
        from core.weekly_tracker import record_daily
        record_daily(envelope.get("data", {}), dt_utc=datetime.now(timezone.utc))
    except Exception as e:
        logger.warning(f"[Step 8-W] 주간 기록 실패 (영향 없음): {e}")

    # ── Step 8-DB: Supabase 일별 데이터 적재 ──────────────────
    try:
        from db.daily_store import store_all_daily_data
        db_result = store_all_daily_data(
            data=data,
            regime_score=composite_score,
            rss_result=news_result,
        )
        logger.info(f"[Step 8-DB] Supabase 적재: {db_result}")
    except Exception as e:
        logger.warning(f"[Step 8-DB] Supabase 적재 실패 (영향 없음): {e}")

    # ── Step 8-R: ETF 랭킹 변화 감지 + 텔레그램 알림 ───────────
    try:
        from core.rank_tracker import detect_rank_change
        etf_rank = envelope.get("data", {}).get("etf_analysis", {}).get("etf_rank", {})
        if etf_rank:
            change = detect_rank_change(etf_rank, dt_utc=datetime.now(timezone.utc))
            if change:
                from publishers.telegram_publisher import send_message, format_rank_change
                # 무료 채널 — 핵심 변화
                send_message(format_rank_change(change, channel="free"), channel="free")
                # 유료 채널 — 상세 (1위 교체 시에만)
                if change.get("top1_changed"):
                    send_message(format_rank_change(change, channel="paid"), channel="paid")
                logger.info("[Step 8-R] ETF 랭킹 변화 알림 발송 완료")
    except Exception as e:
        logger.warning(f"[Step 8-R] 랭킹 변화 감지 실패 (영향 없음): {e}")

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
        choices=["morning", "intraday", "close", "full", "weekly", "narrative", "auto"],
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
