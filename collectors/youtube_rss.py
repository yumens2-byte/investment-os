"""
collectors/youtube_rss.py (C-16)
===================================
유튜버 채널 RSS 수집 — 최근 24시간 영상 필터링.

YouTube RSS 피드에서 영상 제목 + 설명(description) 추출.
비용: $0 (공개 RSS 피드, API 키 불필요)

VERSION = "1.0.0"
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def collect_youtube_summaries() -> dict:
    """
    등록된 유튜버 채널의 최근 영상 제목/설명을 수집.

    Returns:
        {
          "success": True,
          "videos": [
            {"channel": "소수몽키", "title": "...", "description": "...", "published": "..."},
            ...
          ],
          "total": 5,
        }
    """
    try:
        import feedparser
    except ImportError:
        logger.warning("[YouTubeRSS] feedparser 미설치 → pip install feedparser")
        return {"success": False, "videos": [], "total": 0, "error": "feedparser 미설치"}

    from config.streamer_list import get_youtubers, RSS_BASE

    # 최근 48시간 내 영상만 (여유 확보)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    all_videos = []

    youtubers = get_youtubers()
    if not youtubers:
        return {"success": False, "videos": [], "total": 0, "error": "YOUTUBE_CHANNELS 미설정"}

    for yt in youtubers:
        rss_url = f"{RSS_BASE}{yt['channel_id']}"
        try:
            feed = feedparser.parse(rss_url)
            if not feed.entries:
                logger.info(f"[YouTubeRSS] {yt['name']}: 영상 없음")
                continue

            # 최신 3개만 확인 (RSS는 최신순)
            for entry in feed.entries[:3]:
                # 발행 시간 파싱
                published = entry.get("published_parsed")
                if published:
                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue

                title = entry.get("title", "").strip()
                # YouTube RSS의 description은 media_group에 있음
                description = ""
                if hasattr(entry, "media_group"):
                    for mg in entry.media_group:
                        if hasattr(mg, "media_description"):
                            for md in mg.media_description:
                                description = md.get("content", "")
                                break

                # media_group이 없으면 summary 사용
                if not description:
                    description = entry.get("summary", "")

                # 설명이 너무 길면 앞부분만
                if len(description) > 500:
                    description = description[:500] + "..."

                if title:
                    all_videos.append({
                        "channel": yt["name"],
                        "title": title,
                        "description": description,
                        "published": entry.get("published", ""),
                    })

            logger.info(f"[YouTubeRSS] {yt['name']}: {len([v for v in all_videos if v['channel'] == yt['name']])}건 수집")

        except Exception as e:
            logger.warning(f"[YouTubeRSS] {yt['name']} RSS 수집 실패: {e}")
            continue

    logger.info(f"[YouTubeRSS] 총 {len(all_videos)}건 수집 완료")
    return {
        "success": len(all_videos) > 0,
        "videos": all_videos,
        "total": len(all_videos),
    }
