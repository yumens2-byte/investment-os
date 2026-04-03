"""
run_weekend.py (B-20)
========================
주말 콘텐츠 발행 — 토/일 자동 판별

토요일: "이번 주 전투 결과" (weekly_review)
일요일: "다음 주 전투 예고" (next_week_preview)

사용:
  python run_weekend.py              ← 오늘 요일에 맞게 자동 판별
  python run_weekend.py --day sat    ← 토요일 콘텐츠 강제
  python run_weekend.py --day sun    ← 일요일 콘텐츠 강제
"""
import argparse
import json
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1")


def run(day: str = "auto") -> dict:
    """
    주말 콘텐츠 발행

    Args:
        day: "sat" | "sun" | "auto"

    Returns:
        {"success": bool, "day": str, "type": str, ...}
    """
    _setup_logging()

    logger.info("=" * 50)
    logger.info(f"[run_weekend] 시작 | day={day} | DRY_RUN={DRY_RUN}")
    logger.info("=" * 50)

    # ── 요일 판별 ──
    if day == "auto":
        kst = datetime.now(timezone.utc) + timedelta(hours=9)
        weekday = kst.weekday()  # 0=월 ... 5=토, 6=일
        if weekday == 5:
            day = "sat"
        elif weekday == 6:
            day = "sun"
        else:
            logger.info(f"[run_weekend] 평일({weekday}) — 주말 콘텐츠 스킵")
            return {"success": True, "day": "weekday", "type": "skip"}

    if day == "sat":
        result = _run_saturday()
    elif day == "sun":
        result = _run_sunday()
    else:
        logger.error(f"[run_weekend] 알 수 없는 day: {day}")
        return {"success": False, "error": f"알 수 없는 day: {day}"}

    logger.info("=" * 50)
    logger.info(f"[run_weekend] 완료 | day={day} | success={result.get('success')}")
    logger.info("=" * 50)

    return result


def _run_saturday() -> dict:
    """토요일: 주간 리뷰"""
    logger.info("[run_weekend] 토요일 — 주간 리뷰 생성")

    from weekend.weekly_review import generate_weekly_review
    result = generate_weekly_review()

    if not result.get("success"):
        logger.warning(f"[run_weekend] 주간 리뷰 생성 실패: {result.get('error')}")
        return {"success": False, "day": "sat", "type": "weekly_review", "error": result.get("error")}

    # ── X 발행 ──
    tweet_text = result["tweet_text"]
    image_path = result.get("image_path")

    try:
        from publishers.x_publisher import publish_tweet, publish_image_tweet
        if image_path:
            x_result = publish_image_tweet(tweet_text, image_path)
        else:
            x_result = publish_tweet(tweet_text)
        tweet_id = x_result.get("tweet_id", "FAIL")
        logger.info(f"[run_weekend] X 발행: {tweet_id}")
    except Exception as e:
        logger.warning(f"[run_weekend] X 발행 실패: {e}")
        tweet_id = "X_FAIL"

    # ── TG 발행 ──
    try:
        from publishers.telegram_publisher import send_message, send_image
        send_message(result["tg_text"])
        if image_path:
            send_image(image_path)
        logger.info("[run_weekend] TG 발행 완료")
    except Exception as e:
        logger.warning(f"[run_weekend] TG 발행 실패: {e}")

    return {
        "success": True,
        "day": "sat",
        "type": "weekly_review",
        "tweet_id": tweet_id,
        "image_path": image_path,
    }


def _run_sunday() -> dict:
    """일요일: 다음 주 프리뷰"""
    logger.info("[run_weekend] 일요일 — 다음 주 프리뷰 생성")

    from weekend.next_week_preview import generate_next_week_preview
    result = generate_next_week_preview()

    if not result.get("success"):
        logger.warning(f"[run_weekend] 프리뷰 생성 실패: {result.get('error')}")
        return {"success": False, "day": "sun", "type": "next_week_preview", "error": result.get("error")}

    # ── X 발행 ──
    tweet_text = result["tweet_text"]
    image_path = result.get("image_path")

    try:
        from publishers.x_publisher import publish_tweet, publish_image_tweet
        if image_path:
            x_result = publish_image_tweet(tweet_text, image_path)
        else:
            x_result = publish_tweet(tweet_text)
        tweet_id = x_result.get("tweet_id", "FAIL")
        logger.info(f"[run_weekend] X 발행: {tweet_id}")
    except Exception as e:
        logger.warning(f"[run_weekend] X 발행 실패: {e}")
        tweet_id = "X_FAIL"

    # ── TG 발행 ──
    try:
        from publishers.telegram_publisher import send_message, send_image
        send_message(result["tg_text"])
        if image_path:
            send_image(image_path)
        logger.info("[run_weekend] TG 발행 완료")
    except Exception as e:
        logger.warning(f"[run_weekend] TG 발행 실패: {e}")

    return {
        "success": True,
        "day": "sun",
        "type": "next_week_preview",
        "tweet_id": tweet_id,
        "image_path": image_path,
        "events_count": len(result.get("events", [])),
    }


def _setup_logging():
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="주말 콘텐츠 발행")
    parser.add_argument("--day", default="auto", choices=["auto", "sat", "sun"])
    args = parser.parse_args()

    result = run(day=args.day)
    print(json.dumps(result, ensure_ascii=False, indent=2))
