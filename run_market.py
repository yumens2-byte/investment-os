"""
run_market.py (v1.7.0)
=======================
역할: 취합(수집) + 분석
실행: python run_market.py [--session morning|intraday|close]

v1.5.1 (2026-04-07) 🐛 BUGFIX: assemble_core_data() Phase 1A 파라미터 누락 수정
  - Step 5.5/5.6에서 수집한 crypto_basis_result, btc_sentiment_result가
    Step 6의 assemble_core_data() 호출 시 전달되지 않아
    json_builder가 디폴트 dict ({"state": "Unknown"})를 사용하던 버그 수정
  - 결과: core_data.json과 daily_snapshots에 항상 NULL/Unknown 저장되던 문제 해결
  - 동일 패턴이 과거 FRED, FX 적재 시에도 발생한 적 있음 — 신규 데이터 소스
    추가 시 반드시 assemble_core_data() 파라미터 전달 경로 전체 확인 필요

v1.5.0 변경:
  - Reddit 제거 → 다중 RSS(rss_extended) 단독 감성 사용
  - 감성 통합 로직 단순화


run_market.py (v1.6.0)
=======================
v1.6.0 (2026-04-11) — Priority A 6개 핵심 지표 추가
  - Gold(GC=F), IWM, TLT, MOVE, US2Y(DGS2), SPY SMA50/200 수집
  - macro_engine에 spy_sma_data 전달
  - assemble_core_data()에 spy_sma_data 전달
  - detect_alerts()에 spy_sma_data, fred_data 전달

파이프라인:
  수집(yfinance / FRED / 다중RSS / Crypto.com / LunarCrush)
  → Macro Engine → Regime Engine → ETF Engine → Risk Engine
  → Phase 1A (T4-1 Basis, T4-4 Sentiment)
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

def _validate_core_inputs(
    snapshot: dict,
    fred_data: dict,
    crypto_basis_result,
    btc_sentiment_result,
) -> None:
    """
    assemble_core_data() 직전 필수 필드 None 체크 게이트 (v1.7.0).

    반복 발생 고위험 패턴 방지:
      - Tier1 필수 필드 4개 전체 None → ValueError (파이프라인 중단)
      - Tier1 부분 None / Tier2 None → WARNING (엔진에서 중립 처리, 계속)
      - 파라미터 전달 체인 누락 → WARNING (v1.5.1 BUGFIX 패턴 재발 방지)

    Args:
        snapshot: collect_market_snapshot() 결과
        fred_data: collect_macro_data() 결과
        crypto_basis_result: get_btc_basis() 결과 (None 가능)
        btc_sentiment_result: get_btc_sentiment() 결과 (None 가능)

    Raises:
        ValueError: Tier1 필수 필드 4개 전체 None인 경우
    """
    # ── Tier1 필수 필드 체크 ─────────────────────────────────
    tier1_missing = []
    for key in ("vix", "sp500", "us10y", "oil"):
        if snapshot.get(key) is None:
            tier1_missing.append(f"snapshot.{key}")

    if tier1_missing:
        all_none = len(tier1_missing) == 4
        if all_none:
            logger.error(
                f"[Gate] Tier1 필수 필드 전체 None: {tier1_missing} "
                f"— 시장 데이터 수집 전면 실패. 파이프라인 중단."
            )
            raise ValueError(
                f"assemble_core_data() 게이트: Tier1 필수 필드 전체 누락 {tier1_missing}"
            )
        else:
            logger.warning(
                f"[Gate] Tier1 필드 부분 누락: {tier1_missing} "
                f"— 엔진에서 중립 처리 진행"
            )

    # ── Tier2 경고 체크 ──────────────────────────────────────
    for key in ("fed_funds_rate", "spread_2y10y_bp"):
        if fred_data.get(key) is None:
            logger.warning(
                f"[Gate] fred_data.{key} = None "
                f"— 엔진 중립 처리 (파이프라인 계속)"
            )

    # ── 파라미터 전달 체인 체크 (v1.5.1 BUGFIX 패턴 재발 방지) ──
    if crypto_basis_result is None:
        logger.warning(
            "[Gate] crypto_basis_result = None "
            "— assemble_core_data()에 None 전달됨 (Unknown 처리됨)"
        )
    if btc_sentiment_result is None:
        logger.warning(
            "[Gate] btc_sentiment_result = None "
            "— assemble_core_data()에 None 전달됨 (Unknown 처리됨)"
        )


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

    # ── Step 1-YT: 유튜버 영상 요약 (C-16) ────────────────────
    streamer_result = None
    try:
        from collectors.youtube_rss import collect_youtube_summaries
        from engines.streamer_analyzer import analyze_streamer_content
        yt_data = collect_youtube_summaries()
        if yt_data.get("success") and yt_data.get("videos"):
            streamer_result = analyze_streamer_content(yt_data["videos"])
            if streamer_result.get("success"):
                logger.info(
                    f"[Step 1-YT] 유튜버 요약 완료 | "
                    f"방향={streamer_result.get('direction', '?')} | "
                    f"영상 {streamer_result.get('video_count', 0)}건"
                )
            else:
                logger.info("[Step 1-YT] 유튜버 요약 스킵")
        else:
            logger.info("[Step 1-YT] 최근 영상 없음 → 스킵")
    except Exception as e:
        logger.warning(f"[Step 1-YT] 유튜버 요약 실패 (영향 없음): {e}")
      
    # ── Step 2-PrevSig: signal_diff용 이전 signals 사전 로드 ──
    # run_regime_engine()이 regime_tracker의 last_signals를 갱신하기 전에
    # 이전 값을 먼저 읽어두어야 Step 8-R에서 signal_diff 계산 가능
    _prev_signals = {}
    try:
        from core.regime_tracker import _load as _rt_load
        _prev_signals = _rt_load().get("last_signals", {})
        if _prev_signals:
            logger.info(f"[Step 2-PrevSig] 이전 signals 로드 완료: {len(_prev_signals)}개")
        else:
            logger.info("[Step 2-PrevSig] 이전 signals 없음 (첫 실행 또는 초기화)")
    except Exception as e:
        logger.warning(f"[Step 2-PrevSig] 이전 signals 로드 실패 (영향 없음): {e}")
      
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


    # ── Step 2-SMA: Priority A — SPY SMA50/200 수집 (v1.6.0) ──
    spy_sma_data = {}
    try:
        from collectors.yahoo_finance import collect_spy_sma
        spy_sma_data = collect_spy_sma()
        logger.info(
            f"[Step 2-SMA] SPY SMA 수집 완료: "
            f"Price=${spy_sma_data.get('spy_price')} | "
            f"SMA50=${spy_sma_data.get('spy_sma50')} | "
            f"SMA200=${spy_sma_data.get('spy_sma200')}"
        )
    except Exception as e:
        logger.warning(f"[Step 2-SMA] SPY SMA 수집 실패 (영향 없음): {e}")


  # ── Step 2-Sector: Priority B — 섹터 ETF + 구리 수집 ─────
    sector_data = {}
    try:
        from collectors.yahoo_finance import collect_sector_etfs
        sector_data = collect_sector_etfs()
        logger.info(
            f"[Step 2-Sector] 섹터 수집 완료 | "
            f"방어(XLV/XLU/XLP) avg={round((sector_data.get('xlv_change',0) or 0 + sector_data.get('xlu_change',0) or 0 + sector_data.get('xlp_change',0) or 0)/3,2)} | "
            f"Cu={sector_data.get('copper_change')}%"
        )
    except Exception as e:
        logger.warning(f"[Step 2-Sector] 섹터 수집 실패 (영향 없음): {e}")
  

    # D-2: Put/Call Ratio 수집
    pcr_data = {}
    try:
        from collectors.yahoo_finance import collect_put_call_ratio
        pcr_data = collect_put_call_ratio()
        logger.info(f"[Step 2-PCR] PCR={pcr_data.get('pcr',0)} ({pcr_data.get('pcr_state','?')})")
    except Exception as e:
        logger.warning(f"[Step 2-PCR] PCR 수집 실패 (VIX만 적용): {e}")

    from engines.macro_engine import run_macro_engine
    macro_result = run_macro_engine(
        snapshot,
        fred_data,
        combined_sentiment,
        fear_greed=fear_greed,       # T1-1: Fear & Greed → sentiment 보강
        crypto=crypto,               # T1-2: BTC → risk appetite 보조
        etf_prices=etf_prices,       # T1-4: XLF/GLD → 금융안정 보강
        tier2_data=tier2_data,       # T2-1,2,5: Breadth/VolTerm/EM
        pcr_data=pcr_data,           # T1-5: Put/Call Ratio (D-2)
        spy_sma_data=spy_sma_data,    # ← 신규 추가
        # T1-3: snapshot 내 sp500/nasdaq 등락률은 이미 snapshot에 포함
        # T2-3,4: fred_data 내 initial_claims/inflation_exp는 이미 fred_data에 포함
        sector_data=sector_data,      # ← 추가
    )
    signals = macro_result["signals"]
    market_score = macro_result["market_score"]

    # ── Step 3: Regime Engine ──────────────────────────────────
    logger.info("[Step 3] Regime Engine 실행")
    from engines.regime_engine import run_regime_engine
    # E-5: news_analysis 전달하여 Gemini bearish 가드 적용
    regime_result = run_regime_engine(market_score, signals, snapshot, news_analysis=news_analysis)
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
            confidence = cross_check.get("confidence", 0.0)
            suggested  = cross_check.get("suggested_regime", "")
            reason_txt = cross_check.get("reason", "")
            old_risk   = market_regime["market_risk_level"]

            logger.warning(
                f"[Step 3.5] ⚠️ Gemini 레짐 불일치: "
                f"rule={regime_result['market_regime']} → "
                f"제안={suggested} | confidence={confidence:.1f} | {reason_txt}"
            )

            # ── 보완 1: confidence >= 0.7 시 Risk Level 보수적 상향 ──
            # ── 보완 2: confidence >= 0.8 시 Risk Level 보수적 상향 ──
            if confidence >= 0.8:
                RISK_UP = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "HIGH"}
                new_risk = RISK_UP.get(old_risk, old_risk)

                if new_risk != old_risk:
                    market_regime["market_risk_level"] = new_risk
                    regime_result["market_risk_level"] = new_risk   # ← 이 줄 추가
                    logger.warning(
                        f"[Step 3.5] 🔺 Risk Level 상향: {old_risk} → {new_risk} "
                        f"(Gemini confidence={confidence:.1f})"
                    )

                # ── 보완 2: regime_reason에 AI 검증 경고 태그 추가 ──
                market_regime["regime_reason"] = (
                    f"[AI검증⚠️] {market_regime['regime_reason']} "
                    f"| Gemini 제안={suggested}"
                )

                # ── 보완 3: TG 알림 (Risk Level 변경 포함) ──
                try:
                    from publishers.telegram_publisher import send_message
                    risk_msg = f"\n🔺 Risk Level {old_risk} → {new_risk} 상향 적용" if new_risk != old_risk else ""
                    send_message(
                        f"⚠️ [레짐 크로스체크 불일치]\n"
                        f"Rule: {regime_result['market_regime']} ({old_risk})\n"
                        f"Gemini 제안: {suggested} | confidence: {confidence:.1f}\n"
                        f"사유: {reason_txt}"
                        f"{risk_msg}"
                    )
                except Exception:
                    pass
            else:
                # confidence < 0.7 → 경고만 (낮은 확신 무시)
                logger.info(
                    f"[Step 3.5] confidence={confidence:.1f} < 0.7 "
                    f"— 경고만, Risk Level 유지"
                )
                try:
                    from publishers.telegram_publisher import send_message
                    send_message(
                        f"ℹ️ [레짐 크로스체크 참고]\n"
                        f"Rule: {regime_result['market_regime']} | "
                        f"Gemini 제안: {suggested}\n"
                        f"confidence={confidence:.1f} (낮음) — 레짐 유지\n"
                        f"사유: {reason_txt}"
                    )
                except Exception:
                    pass

    except Exception as e:
        logger.warning(f"[Step 3.5] Gemini 크로스체크 실패 (무시): {e}")

    # ── Step 4: ETF Engine ─────────────────────────────────────
    logger.info("[Step 4] ETF Engine 실행")

    # E-1: SMA5/SMA20 수집 (중기 트렌드 보정)
    sma_data = {}
    try:
        from collectors.yahoo_finance import collect_etf_sma
        sma_data = collect_etf_sma()
        logger.info(f"[Step 4-SMA] ETF SMA 수집 완료: {len(sma_data)}개")
    except Exception as e:
        logger.warning(f"[Step 4-SMA] SMA 수집 실패 (당일 변동률만 적용): {e}")

    # D-4: ETF 거래량 트렌드 수집 (자금 흐름 보정)
    volume_data = {}
    try:
        from collectors.yahoo_finance import collect_etf_volume_trend
        volume_data = collect_etf_volume_trend()
        logger.info(f"[Step 4-VOL] ETF 거래량 수집 완료: {len(volume_data)}개")
    except Exception as e:
        logger.warning(f"[Step 4-VOL] 거래량 수집 실패 (가격만 적용): {e}")

    from engines.etf_engine import run_etf_engine
    etf_result = run_etf_engine(
        regime=regime_result["market_regime"],
        risk_level=regime_result["market_risk_level"],
        market_score=market_score,
        etf_prices=etf_prices,
        sma_data=sma_data,
        volume_data=volume_data,
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

  
    # ── STEP 5.5: T4-1 Crypto Basis Spread (2026-04-07 Phase 1A) ──
    logger.info("[Step 5.5] T4-1 Crypto Basis Spread 수집")
    crypto_basis_result = None
    try:
        from collectors.crypto_com_client import get_btc_basis
        crypto_basis_result = get_btc_basis()
        if crypto_basis_result.get("success"):
            logger.info(
                f"[Step 5.5] 완료: basis={crypto_basis_result['basis_spread']:+.4f}% "
                f"state={crypto_basis_result['state']}"
            )
        else:
            logger.warning(
                f"[Step 5.5] 실패 (영향 없음): {crypto_basis_result.get('error')}"
            )
    except Exception as e:
        logger.warning(f"[Step 5.5] 예외 발생 (영향 없음): {e}")
        crypto_basis_result = {
            "success": False,
            "basis_spread": None,
            "state": "Unknown",
            "score": 2,
            "mark": None,
            "index": None,
        }

    # ── STEP 5.6: T4-4 BTC Social Sentiment (2026-04-07 Phase 1A) ──
    logger.info("[Step 5.6] T4-4 BTC Social Sentiment 수집")
    btc_sentiment_result = None
    try:
        from collectors.lunarcrush_client import get_btc_sentiment
        btc_sentiment_result = get_btc_sentiment()
        if btc_sentiment_result.get("success"):
            logger.info(
                f"[Step 5.6] 완료: sentiment={btc_sentiment_result['sentiment']}% "
                f"state={btc_sentiment_result['state']}"
            )
        else:
            logger.warning(
                f"[Step 5.6] 실패 (영향 없음): {btc_sentiment_result.get('error')}"
            )
    except Exception as e:
        logger.warning(f"[Step 5.6] 예외 발생 (영향 없음): {e}")
        btc_sentiment_result = {
            "success": False,
            "sentiment": None,
            "state": "Unknown",
            "score": 2,
            "themes_supportive": "",
            "themes_critical": "",
        }


    # ── Step 6: JSON 조립 ──────────────────────────────────────
    logger.info("[Step 6] JSON Core Data 조립")

    # ── Step 6 게이트: assemble_core_data() 파라미터 Null 검증 ──
    # v1.7.0: 반복 발생 고위험 패턴 방지 (v1.5.1 BUGFIX 동일 사고 재발 차단)
    _validate_core_inputs(
        snapshot=snapshot,
        fred_data=fred_data,
        crypto_basis_result=crypto_basis_result,
        btc_sentiment_result=btc_sentiment_result,
    )
  
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
        # ── Phase 1A (v1.5.1 BUGFIX): 누락되어 있던 파라미터 추가 ──
        crypto_basis=crypto_basis_result,
        btc_sentiment=btc_sentiment_result,
        spy_sma_data=spy_sma_data,     # ← 신규 추가
    )
    envelope = build_envelope(f"run market ({session})", data)

    # top_headlines 주입 — output_helpers에 RSS 상위 헤드라인 추가
    try:
        headlines = news_result.get("headlines", [])
        if headlines:
            data["output_helpers"]["top_headlines"] = headlines[:3]
    except Exception:
        pass

    # C-16: 유튜버 요약 결과 주입
    if streamer_result and streamer_result.get("success"):
        data["streamer_consensus"] = {
            "direction": streamer_result.get("direction", "neutral"),
            "summary_points": streamer_result.get("summary_points", []),
            "tweet": streamer_result.get("tweet", ""),
            "video_count": streamer_result.get("video_count", 0),
        }

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
    # v1.6.0 재설계: format_rank_change(paid) DEPRECATED 제거
    #   → format_etf_rank_premium() + signal_diff로 교체
    #   → B-5(run_alert.py) 프리미엄 발송 역할 흡수
    #   → rank_history 정상 저장 후 alert 재감지 불가 구조 수정
    try:
        from core.rank_tracker import detect_rank_change
        etf_rank = envelope.get("data", {}).get("etf_analysis", {}).get("etf_rank", {})
        if etf_rank:
            # 수정
            change = detect_rank_change(
                etf_rank,
                dt_utc=datetime.now(timezone.utc),
                dry_run=DRY_RUN,
            )
            if change:
                from publishers.telegram_publisher import send_message, format_rank_change

                # ── 무료 채널: 핵심 변화 요약 (기존 유지) ─────────────
                send_message(format_rank_change(change, channel="free"), channel="free")
                logger.info("[Step 8-R] 무료 채널 ETF 랭킹 변화 발송 완료")

                # ── 유료 채널: 💎 프리미엄 랭킹 전환 리포트 ──────────
                # Top1 교체 시에만 발송 — signal_diff 원인 분석 포함
                if change.get("top1_changed"):
                    try:
                        from publishers.premium_alert_formatter import format_etf_rank_premium
                        from core.signal_diff import compute_signal_diff

                        # signal_diff: Step 2-PrevSig에서 사전 로드한 이전 signals 활용
                        _signal_diff = None
                        if _prev_signals:
                            _signal_diff = compute_signal_diff(_prev_signals, signals)
                            logger.info(
                                f"[Step 8-R] signal_diff 계산 완료: "
                                f"{_signal_diff.get('summary', 'N/A')}"
                            )
                        else:
                            logger.info("[Step 8-R] signal_diff 계산 불가 (prev_signals 없음) — 원인 분석 생략")

                        _regime = market_regime.get("market_regime", "—")
                        _risk   = market_regime.get("market_risk_level", "—")
                        _sig    = risk_result["trading_signal"]["trading_signal"]

                        pm_text = format_etf_rank_premium(
                            change,
                            signal_diff_result=_signal_diff,
                            regime=_regime,
                            risk_level=_risk,
                            trading_signal=_sig,
                        )
                        send_message(pm_text, channel="paid")
                        logger.info(
                            f"[Step 8-R] 💎 프리미엄 ETF 랭킹 리포트 발송 완료 "
                            f"({change.get('old_top1')}→{change.get('new_top1')})"
                        )
                    except Exception as pm_e:
                        logger.warning(f"[Step 8-R] 💎 프리미엄 발송 실패 (영향 없음): {pm_e}")

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
    except ValueError as e:
        # v1.7.0: Tier1 필수 필드 전체 누락 (게이트 차단)
        logger.critical(f"[run_market] 데이터 게이트 차단: {e}")
        sys.exit(3)
    except Exception as e:
        logger.critical(f"[run_market] 예외 발생: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
