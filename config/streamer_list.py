"""
config/streamer_list.py (C-16)
===================================
미장 유튜버 채널 리스트 — GitHub Secret에서 로드.

GitHub Secret: YOUTUBE_CHANNELS
형식: ""
예시: ""

VERSION = "1.1.0"
"""
import os
import logging

logger = logging.getLogger(__name__)

# RSS URL 자동 생성용
RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="


def get_youtubers() -> list:
    """
    GitHub Secret YOUTUBE_CHANNELS에서 유튜버 리스트 로드.
    형식: "이름1:채널ID1,이름2:채널ID2,..."
    """
    raw = os.environ.get("YOUTUBE_CHANNELS", "")
    if not raw:
        logger.warning("[StreamerList] YOUTUBE_CHANNELS 미설정")
        return []

    youtubers = []
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        name, channel_id = entry.split(":", 1)
        youtubers.append({
            "name": name.strip(),
            "channel_id": channel_id.strip(),
        })

    logger.info(f"[StreamerList] 유튜버 {len(youtubers)}명 로드")
    return youtubers
