"""
notifier/telegram.py
Investment Comic v2.0 — 파이프라인 실패 알림

사용: python -m notifier.telegram --message "내용"
"""

import os
import logging
import argparse
import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_alert(message: str) -> bool:
    """
    Telegram 알림 전송
    Returns: True = 성공, False = 실패 (알림 실패 시 파이프라인 중단 없음)
    """
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_FREE_CHANNEL_ID", "")

    if not token or not chat_id:
        logger.warning("[Telegram] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_FREE_CHANNEL_ID 미설정, 알림 생략")
        return False

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": message},
            timeout=10
        )
        resp.raise_for_status()
        logger.info(f"[Telegram] 알림 전송 완료: {message[:50]}")
        return True
    except Exception as e:
        # 알림 실패는 파이프라인을 멈추지 않음
        logger.warning(f"[Telegram] 알림 전송 실패: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    send_alert(args.message)
