"""
run_view.py
===========
역할: 출력(검증 → 중복체크 → X 발행)
실행: python run_view.py [--mode tweet|thread]

파이프라인:
  core_data.json 로드 (run_market.py 결과)
  → validate_data + validate_output (Hard Gate)
  → 중복 검사 (history.json 비교)
  → 트윗 포맷 생성
  → X 발행 (DRY_RUN or 실제)
  → 발행 이력 기록

run_market.py 없이 단독 실행 불가.
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
logger = logging.getLogger("run_view")


def run(mode: str = "tweet", session: str = None) -> dict:
    """
    출력 파이프라인 실행.
    mode:    "tweet" (단일 트윗) or "thread" (X 쓰레드)
    session: 외부에서 강제 지정 시 output_helpers보다 우선 (full 세션용)
    """
    logger.info(f"{'='*50}")
    logger.info(f"[run_view] 시작 — mode={mode} | DRY_RUN={DRY_RUN}")
    logger.info(f"{'='*50}")

    # ── Step 0: DLQ 재처리 (B-17) ────────────────────────────
    try:
        from core.dlq import process_queue, get_queue_size
        q_size = get_queue_size()
        if q_size > 0:
            logger.info(f"[Step 0] DLQ 재처리 시작: {q_size}건")
            dlq_result = process_queue()
            logger.info(f"[Step 0] DLQ 완료: {dlq_result}")
        else:
            logger.info("[Step 0] DLQ 비어있음 — 스킵")
    except Exception as e:
        logger.warning(f"[Step 0] DLQ 처리 실패 (영향 없음): {e}")

    # ── Step 1: core_data.json 로드 ────────────────────────────
    logger.info("[Step 1] core_data.json 로드")
    from core.json_builder import load_core_data
    try:
        envelope = load_core_data()
    except FileNotFoundError as e:
        logger.error(f"[run_view] {e}")
        return {"success": False, "reason": "core_data_not_found"}

    data = envelope.get("data", {})

    # ── Step 2: Validation Hard Gate ──────────────────────────
    logger.info("[Step 2] Validation 실행")
    from core.validator import validate_data, validate_output

    data_result = validate_data(data)
    output_result = validate_output(data)

    if data_result["status"] != "PASS":
        logger.error(f"[run_view] validate_data FAIL: {data_result['errors']}")
        return {
            "success": False,
            "reason": "validate_data_fail",
            "errors": data_result["errors"],
        }

    if output_result["status"] != "PASS":
        logger.error(f"[run_view] validate_output FAIL: {output_result['errors']}")
        return {
            "success": False,
            "reason": "validate_output_fail",
            "errors": output_result["errors"],
        }

    logger.info("[Step 2] Validation PASS")

    # ── Step 3: 트윗 텍스트 생성 ───────────────────────────────
    logger.info("[Step 3] 트윗 포맷 생성")
    from publishers.x_formatter import format_market_snapshot_tweet, format_thread_posts, format_image_tweet, generate_ai_tweet, generate_ai_thread

    # session 인자가 있으면 우선 사용 (full 세션 등 외부 강제 지정)
    _inner_session = data.get("output_helpers", {}).get("session_type", "postmarket")
    session_type = session if session else _inner_session
    session_labels = {
        "morning":   "Morning Brief 🌅",
        "intraday":  "Intraday Update 📡",
        "close":     "Close Summary 🔔",
        "postmarket":"Market Snapshot 📊",
        "full":      "Full Brief 📊",          # v1.8.0 신규
    }
    session_label = session_labels.get(session_type, "Market Snapshot 📊")

    # v1.8.0: session=full 은 mode 무시 — 이미지 트윗 고정
    if session_type == "full":
        primary_text     = format_image_tweet(data, "full")
        image_tweet_text = primary_text
        posts            = [primary_text]
    elif mode == "thread":
        posts = generate_ai_thread(data)  # C-12: AI 스레드
        primary_text = posts[0] if posts else ""
        image_tweet_text = None
    else:
        # C-1: AI 트윗 생성 (Gemini) — 실패 시 기존 하드코딩 fallback
        primary_text = generate_ai_tweet(data, session_label)
        image_tweet_text = format_image_tweet(data, session_type)
        posts = [primary_text]

    logger.info(f"[Step 3] 생성 완료 ({len(primary_text)}자)")

    # ── Step 4: 중복 검사 ──────────────────────────────────────
    logger.info("[Step 4] 중복 검사")
    from core.duplicate_checker import is_duplicate, record_published

    if is_duplicate(primary_text, data):
        logger.warning("[run_view] 중복 감지 — 발행 차단")
        return {
            "success": False,
            "reason": "duplicate_detected",
            "text_preview": primary_text[:80],
        }

    logger.info("[Step 4] 중복 없음 — 발행 진행")

    # ── Step 5: 발행 직전 최종 데이터 검증 로그 ───────────────
    logger.info("[Step 5] 발행 직전 데이터 확인")
    _log_publish_summary(data, primary_text)

    # ── Step 5.5: 이미지 생성 ───────────────────────────────────
    logger.info("[Step 5.5] 대시보드 이미지 생성")
    image_path = None
    try:
        from publishers.image_generator import generate_image
        from datetime import datetime, timezone
        image_path = generate_image(data=data, session=session_type)
        if image_path:
            logger.info(f"[Step 5.5] 이미지 생성 완료: {image_path}")
        else:
            logger.warning("[Step 5.5] 이미지 생성 실패 — 텍스트만 발행")
    except Exception as e:
        logger.warning(f"[Step 5.5] 이미지 생성 예외 — 텍스트만 발행: {e}")

    # ── Step 6: X 발행 ─────────────────────────────────────────
    logger.info(f"[Step 6] X 발행 (mode={mode})")
    from publishers.x_publisher import publish_tweet, publish_tweet_with_image, publish_thread

    if mode == "thread":
        pub_result = publish_thread(posts)
    elif image_path and image_tweet_text:
        pub_result = publish_tweet_with_image(image_tweet_text, image_path)
    else:
        pub_result = publish_tweet(primary_text)

    tweet_id = pub_result.get("tweet_id") or pub_result.get("tweet_ids", [""])[0]

    # ── Step 6-TG: 텔레그램 발행 (전체 세션) ─────────────────────
    logger.info(f"[Step 6-TG] 텔레그램 발행 시작 (session={session_type})")
    try:
        from publishers.telegram_publisher import (
            send_message, send_photo, format_free_signal, send_document
        )

        if session_type == "weekly" or (session_type == "close" and mode == "thread"):
            # weekly (또는 close+thread fallback):
            # 주간 성적표 + AI 성적표 텔레그램 발송 + PDF 유료 채널 발송
            from core.weekly_tracker import get_weekly_summary, get_ai_scorecard
            from publishers.weekly_formatter import (
                format_weekly_telegram,
                format_ai_scorecard_tweet,
                format_ai_scorecard_telegram,
            )
            summary   = get_weekly_summary()
            tg_text   = format_weekly_telegram(summary)
            send_message(tg_text, channel="free")

            # AI 성적표 — X 트윗 + 텔레그램 무료
            try:
                from publishers.x_publisher import publish_tweet
                scorecard = get_ai_scorecard(summary)
                if scorecard.get("total", 0) > 0:
                    sc_tweet = format_ai_scorecard_tweet(scorecard, summary.get("week",""))
                    sc_tg    = format_ai_scorecard_telegram(scorecard, summary.get("week",""))
                    publish_tweet(sc_tweet)
                    send_message(sc_tg, channel="free")
                    logger.info("[Step 6-TG] AI 성적표 발행 완료")
            except Exception as e:
                logger.warning(f"[Step 6-TG] AI 성적표 발행 실패 (영향 없음): {e}")

            # 유료 채널 — 주간 PDF 리포트
            try:
                from publishers.weekly_pdf_builder import build_weekly_pdf
                pdf_path = build_weekly_pdf(summary)
                pdf_caption = f"📄 Investment OS Weekly Report\n{summary.get('week','')}"
                send_document(pdf_path, caption=pdf_caption, channel="paid")
                logger.info(f"[Step 6-TG] 주간 PDF 유료 채널 발송 완료: {pdf_path}")
            except Exception as e:
                logger.warning(f"[Step 6-TG] 주간 PDF 생성/발송 실패 (영향 없음): {e}")
        elif session_type == "full":
            # full: 무료 텍스트 + 유료 이미지 + 유료 상세 리포트
            free_text = format_free_signal(data, session=session_type)
            send_message(free_text, channel="free")
            if image_path:
                send_photo(image_path, caption=free_text, channel="paid")
            else:
                logger.warning("[Step 6-TG] 이미지 없음 — 유료 채널 텍스트만 발행")
                send_message(free_text, channel="paid")
            # 유료 채널 추가 — ETF 상세 전략 + 포지션 사이징 가이드
            from publishers.paid_report_formatter import format_paid_report, generate_ai_etf_rationale
            paid_text = format_paid_report(data)
            send_message(paid_text, channel="paid")

            # C-3: AI ETF 배분 근거 자연어 발행 (유료 채널)
            try:
                ai_rationale = generate_ai_etf_rationale(data)
                if ai_rationale:
                    send_message(f"💡 <b>AI 투자 근거 분석</b>\n\n{ai_rationale}", channel="paid")
                    logger.info("[Step 6-TG] C-3 AI ETF 근거 유료 발행 완료")
            except Exception as e:
                logger.warning(f"[Step 6-TG] C-3 AI ETF 근거 실패 (무시): {e}")

            # C-13: Gemini Vision 차트 분석 (유료 채널)
            if image_path:
                try:
                    from engines.chart_analyzer import analyze_chart
                    chart = analyze_chart(image_path, data)
                    if chart.get("success"):
                        send_message(chart["telegram"], channel="paid")
                        logger.info(
                            f"[Step 6-TG] C-13 차트 분석 발행 완료 | "
                            f"trend={chart['trend']}"
                        )
                    else:
                        logger.info(f"[Step 6-TG] C-13 차트 분석 스킵: {chart.get('reason', '?')}")
                except Exception as ve:
                    logger.warning(f"[Step 6-TG] C-13 차트 분석 실패 (영향 없음): {ve}")

            # B-21B: 카드뉴스 3장 유료 채널 발행
            try:
                from comic.card_news_generator import generate_cards
                card_paths = generate_cards(data)
                if card_paths:
                    from publishers.telegram_publisher import send_photo
                    for cp in card_paths:
                        send_photo(cp, caption="", channel="paid")
                    logger.info(f"[Step 6-TG] B-21B 카드뉴스 {len(card_paths)}장 발행")
            except Exception as ce:
                logger.warning(f"[Step 6-TG] B-21B 카드뉴스 실패 (영향 없음): {ce}")
        elif session_type == "narrative":
            # narrative: Gemini AI 시장 해설 → X + TG 무료/유료 (11:30 KST)
            try:
                from engines.narrative_engine import (
                    generate_narrative, format_narrative_tweet, format_narrative_telegram,
                )
                narr = generate_narrative(data)
                narrative_text = narr.get("narrative", "")
                source = narr.get("source", "fallback")

                if narrative_text:
                    from publishers.x_publisher import publish_tweet as _pub_narr
                    tweet = format_narrative_tweet(narrative_text)
                    _pub_narr(tweet)

                    # B-21C: VS 배틀 카드 생성 + X 이미지 트윗
                    try:
                        from comic.vs_card_generator import generate_vs_card
                        vs_path = generate_vs_card(data)
                        if vs_path:
                            from publishers.x_publisher import publish_tweet_with_image as _pub_vs
                            vs_tweet = format_narrative_tweet(narrative_text)
                            _pub_vs(vs_tweet, vs_path)
                            logger.info("[Step 6-TG] B-21C VS 카드 발행 완료")
                    except Exception as ve:
                        logger.warning(f"[Step 6-TG] B-21C VS 카드 생성 실패 (영향 없음): {ve}")

                    tg_text = format_narrative_telegram(narrative_text, data)
                    send_message(tg_text, channel="free")
                    send_message(tg_text, channel="paid")
                    logger.info(f"[Step 6-TG] AI 내러티브 발행 완료 (source={source})")
                else:
                    logger.warning("[Step 6-TG] AI 내러티브 비어있음 — 스킵")
            except Exception as e:
                logger.warning(f"[Step 6-TG] AI 내러티브 발행 실패 (영향 없음): {e}")

            # C-5: 오늘의 시장 역사 (narrative 세션 후 독립 발행)
            try:
                from engines.history_engine import generate_history_today
                history = generate_history_today()
                if history.get("success"):
                    from publishers.x_publisher import publish_tweet as _pub_hist
                    _pub_hist(history["tweet"])
                    send_message(history["telegram"], channel="free")
                    logger.info(
                        f"[Step 6-TG] C-5 시장 역사 발행 완료 | "
                        f"{history['year']}년 | {history['event'][:20]}..."
                    )
                else:
                    logger.info(f"[Step 6-TG] C-5 시장 역사 스킵: {history.get('reason', '?')}")
            except Exception as he:
                logger.warning(f"[Step 6-TG] C-5 시장 역사 실패 (영향 없음): {he}")
        else:
            # morning / intraday / close: 무료 채널 텍스트
            free_text = format_free_signal(data, session=session_type)
            send_message(free_text, channel="free")

        logger.info("[Step 6-TG] 텔레그램 발행 완료")
    except Exception as e:
        logger.warning(f"[Step 6-TG] 텔레그램 발행 예외 (X 발행 영향 없음): {e}")

    # ── Step 6-ML: 다국어 발행 (C-11) ─────────────────────────
    try:
        from publishers.translator import publish_multilingual, MULTILINGUAL_ENABLED
        if TELEGRAM_CHANNEL_ID
            # 무료 채널 텍스트를 기준으로 번역
            _ml_text = format_free_signal(data, session=session_type) if session_type != "narrative" else ""
            if _ml_text:
                ml_result = publish_multilingual(_ml_text)
                _langs = [l for l, v in ml_result.items() if v]
                if _langs:
                    logger.info(f"[Step 6-ML] 다국어 발행 완료: {', '.join(_langs)}")
    except Exception as me:
        logger.warning(f"[Step 6-ML] 다국어 발행 실패 (영향 없음): {me}")

    # ── Step 7: 이력 기록 ──────────────────────────────────────
    if pub_result.get("success"):
        logger.info("[Step 7] 발행 이력 기록")
        record_published(primary_text, data, tweet_id=str(tweet_id))

    # 결과
    result = {
        "success": pub_result.get("success", False),
        "mode": mode,
        "tweet_id": tweet_id,
        "dry_run": DRY_RUN,
        "regime": data.get("market_regime", {}).get("market_regime", ""),
        "risk_level": data.get("market_regime", {}).get("market_risk_level", ""),
        "text_preview": primary_text[:80],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"{'='*50}")
    logger.info(f"[run_view] 완료 — success={result['success']} | id={tweet_id}")
    logger.info(f"{'='*50}")

    return result


def _log_publish_summary(data: dict, text: str) -> None:
    """발행 직전 핵심 데이터 로그 출력"""
    snap = data.get("market_snapshot", {})
    regime = data.get("market_regime", {})
    alloc = data.get("etf_allocation", {}).get("allocation", {})

    logger.info("─── 발행 직전 데이터 확인 ───")
    logger.info(f"  SPY: {snap.get('sp500', 0):+.2f}% | VIX: {snap.get('vix', 0):.1f} | US10Y: {snap.get('us10y', 0):.2f}%")
    logger.info(f"  Regime: {regime.get('market_regime')} | Risk: {regime.get('market_risk_level')}")
    logger.info(f"  Allocation: {alloc}")
    logger.info(f"  Tweet({len(text)}자):\n{text}")
    logger.info("─────────────────────────────")


def main():
    parser = argparse.ArgumentParser(description="Investment OS — X Publisher")
    parser.add_argument(
        "--mode",
        choices=["tweet", "thread"],
        default="tweet",
        help="발행 모드 (tweet=단일 | thread=쓰레드)",
    )
    args = parser.parse_args()

    try:
        result = run(mode=args.mode)
        if not result.get("success"):
            logger.warning(f"[run_view] 발행 실패/차단: {result.get('reason', 'unknown')}")
            sys.exit(2)
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[run_view] 예외: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
