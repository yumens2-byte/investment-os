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

        # ── C-4: 주간 리뷰 실패해도 격주 교육은 독립 실행 ──
        try:
            from weekend.education_series import is_education_week, generate_education_content
            if is_education_week():
                logger.info("[run_weekend] C-4 격주 교육 콘텐츠 생성 시작")
                edu = generate_education_content()
                if edu.get("success"):
                    try:
                        from publishers.x_publisher import publish_tweet as _pub_edu
                        _pub_edu(edu["tweet"])
                        logger.info(f"[run_weekend] C-4 X 발행: #{edu['episode']} {edu['topic']}")
                    except Exception as xe:
                        logger.warning(f"[run_weekend] C-4 X 발행 실패: {xe}")
                    try:
                        from publishers.telegram_publisher import send_message as _send_edu
                        _send_edu(edu["telegram"], channel="free")
                        logger.info(f"[run_weekend] C-4 TG 발행 완료: #{edu['episode']}")
                    except Exception as te:
                        logger.warning(f"[run_weekend] C-4 TG 발행 실패: {te}")
                else:
                    logger.info(f"[run_weekend] C-4 교육 스킵: {edu.get('reason', '?')}")
            else:
                logger.info("[run_weekend] C-4 격주 아님 — 교육 콘텐츠 스킵")
        except Exception as ee:
            logger.warning(f"[run_weekend] C-4 교육 실패 (영향 없음): {ee}")

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

    # ── C-4: 격주 투자 교육 시리즈 ──
    try:
        from weekend.education_series import is_education_week, generate_education_content
        if is_education_week():
            logger.info("[run_weekend] C-4 격주 교육 콘텐츠 생성 시작")
            edu = generate_education_content()
            if edu.get("success"):
                try:
                    from publishers.x_publisher import publish_tweet as _pub_edu
                    _pub_edu(edu["tweet"])
                    logger.info(f"[run_weekend] C-4 X 발행: #{edu['episode']} {edu['topic']}")
                except Exception as xe:
                    logger.warning(f"[run_weekend] C-4 X 발행 실패: {xe}")
                try:
                    from publishers.telegram_publisher import send_message as _send_edu
                    _send_edu(edu["telegram"], channel="free")
                    logger.info(f"[run_weekend] C-4 TG 발행 완료: #{edu['episode']}")
                except Exception as te:
                    logger.warning(f"[run_weekend] C-4 TG 발행 실패: {te}")
            else:
                logger.info(f"[run_weekend] C-4 교육 스킵: {edu.get('reason', '?')}")
        else:
            logger.info("[run_weekend] C-4 격주 아님 — 교육 콘텐츠 스킵")
    except Exception as ee:
        logger.warning(f"[run_weekend] C-4 교육 실패 (영향 없음): {ee}")

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

        # ── C-4B: 프리뷰 실패해도 금융 상식은 독립 실행 ──
        _run_finance_basics()

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

    # ── C-4B: 매주 일요일 금융 기본 상식 ──
    _run_finance_basics()

    return {
        "success": True,
        "day": "sun",
        "type": "next_week_preview",
        "tweet_id": tweet_id,
        "image_path": image_path,
        "events_count": len(result.get("events", [])),
    }


def _run_finance_basics():
    """C-4B: 매주 일요일 금융 기본 상식 발행"""
    try:
        from weekend.finance_basics import generate_finance_basics
        logger.info("[run_weekend] C-4B 금융 상식 콘텐츠 생성 시작")
        fb = generate_finance_basics()
        if fb.get("success"):
            try:
                from publishers.x_publisher import publish_tweet as _pub_fb
                _pub_fb(fb["tweet"])
                logger.info(f"[run_weekend] C-4B X 발행: #{fb['episode']} {fb['topic']}")
            except Exception as xe:
                logger.warning(f"[run_weekend] C-4B X 발행 실패: {xe}")
            try:
                from publishers.telegram_publisher import send_message as _send_fb
                _send_fb(fb["telegram"], channel="free")
                logger.info(f"[run_weekend] C-4B TG 발행 완료: #{fb['episode']}")
            except Exception as te:
                logger.warning(f"[run_weekend] C-4B TG 발행 실패: {te}")
        else:
            logger.info(f"[run_weekend] C-4B 금융 상식 스킵: {fb.get('reason', '?')}")
    except Exception as fe:
        logger.warning(f"[run_weekend] C-4B 금융 상식 실패 (영향 없음): {fe}")


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
