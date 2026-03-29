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
