"""
publishers/x_publisher.py
tweepy v4 기반 X(Twitter) 발행 모듈.
DRY_RUN=true 시 실제 발행 없이 로그만 출력.
"""
import logging
import time
from typing import Optional
try:
    import tweepy
    _TWEEPY_AVAILABLE = True
except ImportError:
    tweepy = None
    _TWEEPY_AVAILABLE = False

from config.settings import (
    X_API_KEY, X_API_SECRET,
    X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET,
    DRY_RUN,
)

logger = logging.getLogger(__name__)

# Retry 설정
MAX_RETRIES = 3
RETRY_WAIT_SEC = 30


def _get_client():
    """tweepy v2 Client 생성"""
    if not _TWEEPY_AVAILABLE:
        logger.error("[XPublisher] tweepy 미설치. pip install tweepy 실행 필요.")
        return None
    if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET]):
        logger.error("[XPublisher] X API 키 미설정. 발행 불가.")
        return None
    try:
        client = tweepy.Client(
            consumer_key=X_API_KEY,
            consumer_secret=X_API_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_TOKEN_SECRET,
        )
        return client
    except Exception as e:
        logger.error(f"[XPublisher] 클라이언트 생성 실패: {e}")
        return None


def _publish_single(client, text: str, reply_to_id: Optional[str] = None) -> Optional[str]:
    """
    단일 트윗 발행.
    reply_to_id: 쓰레드 연결 시 직전 트윗 ID.
    Returns: 트윗 ID or None (실패)
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            kwargs = {"text": text}
            if reply_to_id:
                kwargs["in_reply_to_tweet_id"] = reply_to_id

            response = client.create_tweet(**kwargs)
            tweet_id = str(response.data["id"])
            logger.info(f"[XPublisher] 발행 성공: tweet_id={tweet_id}")
            return tweet_id
        except Exception as e:
            err_name = type(e).__name__
            if "TooManyRequests" in err_name or "429" in str(e):
                logger.warning(f"[XPublisher] Rate limit (시도 {attempt}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES:
                    logger.info(f"[XPublisher] {RETRY_WAIT_SEC}초 대기 후 재시도...")
                    time.sleep(RETRY_WAIT_SEC)
                else:
                    logger.error("[XPublisher] Rate limit 초과 — 발행 포기")
                    return None
            elif "Forbidden" in err_name or "403" in str(e):
                logger.error(f"[XPublisher] 권한 없음 (X API Basic 플랜 필요): {e}")
                return None
            else:
                logger.error(f"[XPublisher] 발행 실패 (시도 {attempt}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(10)
                else:
                    return None


def publish_tweet(tweet_text: str) -> dict:
    """
    단일 트윗 발행.

    DRY_RUN=true → 실제 발행 없이 로그 출력
    DRY_RUN=false → 실제 X 발행

    Returns:
        {
            "success": bool,
            "tweet_id": str,
            "dry_run": bool,
            "text_preview": str,
        }
    """
    logger.info(
        f"[XPublisher] {'[DRY RUN] ' if DRY_RUN else ''}발행 시작 "
        f"({len(tweet_text)}자)"
    )
    logger.info(f"[XPublisher] 내용 미리보기:\n{tweet_text[:100]}...")

    if DRY_RUN:
        logger.info("[XPublisher] [DRY RUN] 실제 발행 건너뜀")
        return {
            "success": True,
            "tweet_id": "DRY_RUN",
            "dry_run": True,
            "text_preview": tweet_text[:80],
        }

    client = _get_client()
    if client is None:
        return {
            "success": False,
            "tweet_id": None,
            "dry_run": False,
            "text_preview": tweet_text[:80],
        }

    tweet_id = _publish_single(client, tweet_text)
    return {
        "success": tweet_id is not None,
        "tweet_id": tweet_id,
        "dry_run": False,
        "text_preview": tweet_text[:80],
    }


def publish_thread(posts: list) -> dict:
    """
    X 쓰레드 발행 (순차적으로 reply 연결).

    Returns:
        {
            "success": bool,
            "published_count": int,
            "tweet_ids": list[str],
            "dry_run": bool,
        }
    """
    logger.info(
        f"[XPublisher] {'[DRY RUN] ' if DRY_RUN else ''}쓰레드 발행 시작 "
        f"({len(posts)}개 포스트)"
    )

    if DRY_RUN:
        for i, post in enumerate(posts, 1):
            logger.info(f"[XPublisher] [DRY RUN] [{i}/{len(posts)}]\n{post[:80]}...")
        return {
            "success": True,
            "published_count": len(posts),
            "tweet_ids": ["DRY_RUN"] * len(posts),
            "dry_run": True,
        }

    client = _get_client()
    if client is None:
        return {"success": False, "published_count": 0, "tweet_ids": [], "dry_run": False}

    tweet_ids = []
    reply_to = None

    for i, post in enumerate(posts, 1):
        tweet_id = _publish_single(client, post, reply_to_id=reply_to)
        if tweet_id is None:
            logger.error(f"[XPublisher] 쓰레드 {i}번 발행 실패 — 중단")
            break
        tweet_ids.append(tweet_id)
        reply_to = tweet_id
        # 쓰레드 간 1.5초 대기 (Rate Limit 예방)
        if i < len(posts):
            time.sleep(1.5)

    return {
        "success": len(tweet_ids) == len(posts),
        "published_count": len(tweet_ids),
        "tweet_ids": tweet_ids,
        "dry_run": False,
    }
