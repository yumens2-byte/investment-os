"""
config/streamer_list.py (C-16)
===================================
미장 유튜버 채널 리스트 — GitHub Secret에서 로드.

GitHub Secret: YOUTUBE_CHANNELS
형식: "이름1:채널ID1,이름2:채널ID2,..."
  - 항목 구분은 반각 쉼표(,), 이름과 채널 ID 구분은 첫 번째 반각 콜론(:)
  - 이름 안의 공백은 유지(앞뒤 공백은 제거), 끝 쉼표·「:」 없는 항목은 무시
  - 채널 ID 는 「UC」로 시작하는 24자. 값 전체를 따옴표로 감싸지 않는다
예시: "채널A:UCxxxxxxxxxxxxxxxxxxxxxx,채널B:UCyyyyyyyyyyyyyyyyyyyyyy"

v1.1.1 (2026-11-06, DN2026_0101) — 설명문에 형식·예시 기입(로직 불변)

VERSION = "1.1.1"
"""
import os
import logging

VERSION = "1.1.1"

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
