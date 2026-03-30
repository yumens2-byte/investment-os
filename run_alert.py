"""
run_alert.py
=============
역할: Alert 감지 + 발송
실행: python run_alert.py
     python main.py alert

파이프라인:
  시장 데이터 수집 (yfinance)
  RSS 뉴스 수집 (rss_extended)
  → Alert 엔진 (조건 판정)
  → 쿨다운 체크 (1시간)
  → Alert 포맷 생성
  → X 발행 (DRY_RUN or 실제)
  → 이력 기록

이상 없으면 조용히 종료 (Alert 없음 = 정상)
"""
import json
import logging
import sys
from datetime import datetime, timezone

from config.settings import LOG_LEVEL, DRY_RUN

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_alert")


def _load_prev_snapshot() -> dict:
    """직전 core_data.json에서 이전 스냅샷 로드 (급변 감지용)"""
    from config.settings import CORE_DATA_FILE
    try:
        if CORE_DATA_FILE.exists():
            with open(CORE_DATA_FILE, encoding="utf-8") as f:
                envelope = json.load(f)
            return envelope.get("data", {}).get("market_snapshot", {})
    except Exception:
        pass
    return {}


def run() -> dict:
    logger.info("=" * 50)
    logger.info(f"[run_alert] 시작 | DRY_RUN={DRY_RUN}")
    logger.info("=" * 50)

    # ── Step 1: 데이터 수집 ────────────────────────────────
    logger.info("[Step 1] 시장 데이터 + RSS 수집")
    from collectors.yahoo_finance import collect_market_snapshot
    from collectors.news_rss import collect_news_sentiment

    prev_snapshot = _load_prev_snapshot()
    snapshot      = collect_market_snapshot()
    news_result   = collect_news_sentiment()

    # ── Step 1-M: FRED 경제지표 변화 감지 ────────────────
    try:
        from collectors.fred_client import collect_macro_data, detect_macro_changes
        from core.alert_history import should_send as _should_send, record_alert as _rec
        from publishers.econ_event_formatter import format_econ_event, format_econ_event_telegram
        from publishers.telegram_publisher import send_message as tg_send
        from publishers.x_publisher import publish_tweet as x_pub

        # 이전 FRED 데이터 로드 (core_data.json 활용)
        prev_macro = {}
        try:
            from core.json_builder import load_core_data
            prev_data = load_core_data()
            prev_macro = prev_data.get("macro_data", {})
        except Exception:
            pass

        cur_macro = collect_macro_data()
        macro_changes = detect_macro_changes(cur_macro, prev_macro)

        # 레짐/시그널 정보
        try:
            from core.json_builder import load_core_data as _lcd
            _cd = _lcd()
            _regime = _cd.get("market_regime", {}).get("market_regime", "—")
            _signal = _cd.get("trading_signal", {}).get("trading_signal", "HOLD")
        except Exception:
            _regime, _signal = "—", "HOLD"

        for mc in macro_changes:
            iid = mc["indicator_id"]
            # 하루 1회 중복 방지 (alert_history 활용)
            _send, _reason = _should_send(f"ECON_{iid}", "L1")
            if not _send:
                logger.info(f"[Step 1-M] 경제지표 차단: {iid} — {_reason}")
                continue

            logger.info(f"[Step 1-M] 경제지표 변화: {iid} {mc['prev']} → {mc['new']}")

            tweet = format_econ_event(iid, mc["prev"], mc["new"], _regime, _signal)
            tg_txt = format_econ_event_telegram(iid, mc["prev"], mc["new"], _regime, _signal)

            res = x_pub(tweet)
            _rec(f"ECON_{iid}", "L1", str(res.get("tweet_id","FAIL")), tweet)
            tg_send(tg_txt, channel="free")
            logger.info(f"[Step 1-M] 경제지표 발행 완료: {iid}")
    except Exception as e:
        logger.warning(f"[Step 1-M] 경제지표 감지 실패 (영향 없음): {e}")

    # ── Step 2: Alert 엔진 실행 ───────────────────────────
    logger.info("[Step 2] Alert 엔진 실행")
    from engines.alert_engine import run_alert_engine
    alerts = run_alert_engine(snapshot, news_result, prev_snapshot or None)

    if not alerts:
        logger.info("[run_alert] Alert 없음 — 정상 종료")
        return {"alerts_detected": 0, "alerts_sent": 0}

    # ── Step 3: 쿨다운 체크 + 발송 ───────────────────────
    from core.alert_history import should_send, record_alert
    from publishers.alert_formatter import format_alert_tweet
    from publishers.x_publisher import publish_tweet

    sent_count = 0
    results = []

    for signal in alerts:
        # ── VIX 카운트다운 — 하루 1회 전용 처리 ─────────────────
        if signal.alert_type == "VIX_COUNTDOWN":
            from core.alert_history import should_send_countdown, record_countdown
            from publishers.alert_formatter import format_countdown_tweet
            vix_now = signal.snapshot.get("vix", 0)
            # 가장 가까운 카운트다운 레벨 찾기
            from engines.alert_engine import VIX_COUNTDOWN_LEVELS
            triggered = max((l for l in VIX_COUNTDOWN_LEVELS if vix_now >= l), default=None)
            if triggered is None:
                continue
            send, reason = should_send_countdown(triggered)
            if not send:
                logger.info(f"[run_alert] VIX 카운트다운 차단: {reason}")
                continue
            logger.info(f"[run_alert] VIX 카운트다운 발행: VIX {triggered} — {reason}")
            tweet = format_countdown_tweet(signal)
            result = publish_tweet(tweet)
            tweet_id = result.get("tweet_id", "FAIL")
            if result.get("success"):
                record_countdown(triggered, str(tweet_id))
                sent_count += 1
            # 텔레그램 무료 채널
            try:
                from publishers.telegram_publisher import send_message
                send_message(f"⚠️ <b>VIX 카운트다운</b>\n\n{tweet}", channel="free")
            except Exception as e:
                logger.warning(f"[run_alert] TG 카운트다운 발송 실패: {e}")
            results.append({"type": "VIX_COUNTDOWN", "level": triggered, "tweet_id": tweet_id})
            continue

        # 발송 여부 판단 (등급 변화 + 쿨다운)
        send, reason = should_send(signal.alert_type, signal.level)
        if not send:
            logger.info(f"[run_alert] 발송 차단: {signal.alert_type}/{signal.level} — {reason}")
            continue
        logger.info(f"[run_alert] 발송 결정: {signal.alert_type}/{signal.level} — {reason}")

        # 트윗 포맷 생성
        tweet = format_alert_tweet(signal)

        # 발행 직전 로그
        logger.info(
            f"[run_alert] 발행 예정 [{signal.alert_type}/{signal.level}] "
            f"({len(tweet)}자)\n{tweet}"
        )

        # X 발행
        result = publish_tweet(tweet)

        tweet_id = result.get("tweet_id", "FAIL")
        if result.get("success"):
            record_alert(signal.alert_type, signal.level, str(tweet_id), tweet)
            sent_count += 1
            logger.info(f"[run_alert] 발행 완료: {signal.alert_type}/{signal.level} → {tweet_id}")
        else:
            logger.error(f"[run_alert] 발행 실패: {signal.alert_type}/{signal.level}")

        # 텔레그램 무료 채널 — Alert 즉시 발송 (DRY_RUN 무관)
        try:
            from publishers.telegram_publisher import send_message
            tg_text = f"🚨 <b>Investment OS Alert</b>\n\n{tweet}"
            send_message(tg_text, channel="free")
            logger.info(f"[run_alert] 텔레그램 무료 채널 발송 완료: {signal.alert_type}/{signal.level}")
        except Exception as e:
            logger.warning(f"[run_alert] 텔레그램 발송 예외 (X 발행 영향 없음): {e}")

        # 텔레그램 유료 채널 — VIX 레벨 돌파 또는 레짐 전환 시 프리미엄 알람
        try:
            vix_crossed = getattr(signal, "vix_premium_crossed", False)
            vix_level   = getattr(signal, "vix_premium_level", None)
            prev_vix    = getattr(signal, "prev_vix", 0)

            snap   = data.get("market_snapshot", {})
            regime = data.get("market_regime", {}).get("market_regime", "—")
            risk   = data.get("market_regime", {}).get("market_risk_level", "—")
            matrix = data.get("trading_signal", {}).get("signal_matrix", {})
            buy    = matrix.get("buy_watch", [])
            sig_val = data.get("trading_signal", {}).get("trading_signal", "HOLD")

            from publishers.telegram_publisher import send_message as tg_send
            from publishers.premium_alert_formatter import (
                format_vix_premium, format_regime_change_premium
            )

            # VIX 프리미엄 레벨 돌파 알람
            if vix_crossed and vix_level:
                vix_now = snap.get("vix", 0)
                pm_text = format_vix_premium(vix_now, prev_vix, regime, risk)
                tg_send(pm_text, channel="paid")
                logger.info(f"[run_alert] 유료 채널 VIX {vix_level} 알람 발송")

            # 레짐 전환 알람 (alert_type이 CRISIS 또는 FED_SHOCK 시)
            if signal.alert_type in ("CRISIS", "FED_SHOCK"):
                pm_text = format_regime_change_premium(
                    "—", regime, sig_val, risk, buy
                )
                tg_send(pm_text, channel="paid")
                logger.info(f"[run_alert] 유료 채널 레짐 전환 알람 발송")

        except Exception as e:
            logger.warning(f"[run_alert] 유료 채널 알람 예외 (영향 없음): {e}")

        results.append({
            "type": signal.alert_type,
            "level": signal.level,
            "tweet_id": tweet_id,
            "success": result.get("success"),
        })

    summary = {
        "alerts_detected": len(alerts),
        "alerts_sent": sent_count,
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("=" * 50)
    logger.info(f"[run_alert] 완료 — 감지:{len(alerts)}개 발송:{sent_count}개")
    logger.info("=" * 50)

    return summary


def main():
    try:
        result = run()
        # Alert 발송 성공이든 없든 정상 종료
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[run_alert] 예외: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
