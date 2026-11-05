"""
collectors/youtube_rss.py (C-16)
===================================
유튜버 채널 RSS 수집 — 최근 48시간 영상 필터링 (채널별 최신 3개만 확인).

YouTube RSS 피드에서 영상 제목 + 설명(description) 추출.
비용: $0 (공개 RSS 피드, API 키 불필요)

v1.1.0 (2026-11-06, DN2026_0101) — 수집 관측 강화
  배경: 11-04·11-05 morning 에서 전 채널이 「영상 없음」으로 기록됐으나
        RSS 응답 실패인지 실제 영상 부재인지 로그로 구분할 수 없었다(STD-PROC-15).
  - 채널별 결과를 사유 코드로 분리 기록:
      ok / stale / empty_feed / http_error / parse_error / fetch_error / entry_error
  - 반환값에 stats(사유별 채널 수) 추가 — 기존 키(success·videos·total·error) 유지
  - 전 채널이 수집 단계에서 실패하면 ERROR 로그 + Actions 경고 1줄(STD-OBS-07)
  - 요청 방식(feedparser.parse(url))·필터 기준(48시간·최신 3개)·반환 videos 형식은 불변
  - 설명문 정정: 24시간 → 48시간(실제 코드 값)

VERSION = "1.1.0"
"""
import logging
import urllib.error
from datetime import datetime, timedelta, timezone

VERSION = "1.1.0"

logger = logging.getLogger(__name__)

# 채널별 결과 사유 코드 (DN2026_0101)
REASON_OK = "ok"                # 48시간 내 영상 1건 이상 수집
REASON_STALE = "stale"          # 응답 정상·영상 있음, 48시간 내 0건
REASON_EMPTY = "empty_feed"     # 응답은 받았으나 영상 항목 0개
REASON_HTTP = "http_error"      # HTTP 상태코드 4xx·5xx
REASON_PARSE = "parse_error"    # 피드 파싱 오류(bozo)이고 영상 항목 0개
REASON_FETCH = "fetch_error"    # 네트워크 실패(feedparser 가 bozo 로 담는 URLError·OSError 포함) 또는 호출 중 예외
REASON_ENTRY = "entry_error"    # 응답은 정상, 항목 처리 중 예외
FAIL_REASONS = (REASON_EMPTY, REASON_HTTP, REASON_PARSE, REASON_FETCH, REASON_ENTRY)

ERROR_ALL_FAILED = "all_channels_failed"
ERROR_NO_RECENT = "no_recent_videos"
ERROR_NOT_SET = "YOUTUBE_CHANNELS 미설정"

LOOKBACK_HOURS = 48
ENTRIES_PER_CHANNEL = 3


def _classify_feed(feed) -> tuple:
    """feedparser 결과 → (사유 코드 또는 None, 상세 문자열). None 이면 항목 검사로 진행."""
    status = feed.get("status")
    bozo_exc = feed.get("bozo_exception") if feed.get("bozo") else None
    ctype = (feed.get("headers") or {}).get("content-type", "")
    detail = f"status={status} ctype={ctype or '-'}"
    if bozo_exc is not None:
        detail += f" bozo={type(bozo_exc).__name__}"
    # feedparser 는 리다이렉트 후 정상 수신 시에도 status=301/302 를 남기므로 4xx·5xx 만 실패로 본다
    if isinstance(status, int) and status >= 400:
        return REASON_HTTP, detail
    # R6-01: feedparser 6.0.11 은 연결 실패(URLError)를 예외로 올리지 않고 bozo 로 담아 돌려준다
    if not feed.entries and isinstance(bozo_exc, (urllib.error.URLError, OSError)):
        return REASON_FETCH, detail
    if not feed.entries:
        if bozo_exc is not None:
            return REASON_PARSE, detail
        return REASON_EMPTY, detail
    return None, detail


def _extract_description(entry) -> str:
    # YouTube RSS의 description은 media_group에 있음
    description = ""
    if hasattr(entry, "media_group"):
        for mg in entry.media_group:
            if hasattr(mg, "media_description"):
                for md in mg.media_description:
                    description = md.get("content", "")
                    break
    # media_group이 없으면 summary 사용 (feedparser 6.0.11 실측: 이 경로로 들어옴)
    if not description:
        description = entry.get("summary", "")
    # 설명이 너무 길면 앞부분만
    if len(description) > 500:
        description = description[:500] + "..."
    return description


def collect_youtube_summaries() -> dict:
    """
    등록된 유튜버 채널의 최근 영상 제목/설명을 수집.

    Returns:
        {
          "success": True,
          "videos": [
            {"channel": "유튜버", "title": "...", "description": "...", "published": "..."},
            ...
          ],
          "total": 5,
          "error": None | "all_channels_failed" | "no_recent_videos" | "YOUTUBE_CHANNELS 미설정" | ...,
          "stats": {"channels": 15, "ok": 14, "stale": 1, "empty_feed": 0,
                    "http_error": 0, "parse_error": 0, "fetch_error": 0},
        }
    """
    logger.info(f"[YouTubeRSS] v{VERSION} 시작")
    try:
        import feedparser
    except ImportError:
        logger.warning("[YouTubeRSS] feedparser 미설치 → pip install feedparser")
        return {"success": False, "videos": [], "total": 0, "error": "feedparser 미설치"}

    from config.streamer_list import RSS_BASE, get_youtubers

    # 최근 48시간 내 영상만 (여유 확보)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    all_videos = []

    youtubers = get_youtubers()
    if not youtubers:
        return {"success": False, "videos": [], "total": 0, "error": ERROR_NOT_SET}

    stats = {"channels": len(youtubers), REASON_OK: 0, REASON_STALE: 0}
    for r in FAIL_REASONS:
        stats[r] = 0

    for yt in youtubers:
        name = yt["name"]
        rss_url = f"{RSS_BASE}{yt['channel_id']}"
        try:
            feed = feedparser.parse(rss_url)
        except Exception as e:  # noqa: BLE001 — 채널 1개 실패가 전체를 막지 않도록
            stats[REASON_FETCH] += 1
            logger.warning(f"[YouTubeRSS] {name}: {REASON_FETCH} {type(e).__name__}")
            continue

        reason, detail = _classify_feed(feed)
        if reason is not None:
            stats[reason] += 1
            logger.warning(f"[YouTubeRSS] {name}: {reason} {detail}")
            continue

        entries = feed.entries
        picked = []
        try:
            # 최신 3개만 확인 (RSS는 최신순)
            for entry in entries[:ENTRIES_PER_CHANNEL]:
                # 발행 시간 파싱
                published = entry.get("published_parsed")
                if published:
                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue

                title = entry.get("title", "").strip()
                if title:
                    picked.append({
                        "channel": name,
                        "title": title,
                        "description": _extract_description(entry),
                        "published": entry.get("published", ""),
                    })
        except Exception as e:  # noqa: BLE001 — R6-02: v1.0.0 과 같이 채널 단위로 실패를 가둔다
            stats[REASON_ENTRY] += 1
            logger.warning(f"[YouTubeRSS] {name}: {REASON_ENTRY} {type(e).__name__} {detail}")
            continue
        all_videos.extend(picked)
        count = len(picked)

        if count:
            stats[REASON_OK] += 1
            logger.info(f"[YouTubeRSS] {name}: {REASON_OK} n={count}")
        else:
            stats[REASON_STALE] += 1
            logger.info(f"[YouTubeRSS] {name}: {REASON_STALE} entries={len(entries)} {detail}")

    failed = sum(stats[r] for r in FAIL_REASONS)
    summary = " ".join(f"{k}={v}" for k, v in stats.items())
    logger.info(f"[YouTubeRSS] 총 {len(all_videos)}건 수집 완료 | {summary}")

    error = None
    if not all_videos:
        if failed == stats["channels"]:
            error = ERROR_ALL_FAILED
            logger.error(f"[YouTubeRSS] 전 채널 수집 실패 — {summary}")
            # STD-OBS-07: 공개 화면에는 사유 코드 집계만 올린다(원문·URL 없음)
            print(f"::warning title=YouTubeRSS::{ERROR_ALL_FAILED} {summary}", flush=True)
        else:
            error = ERROR_NO_RECENT

    return {
        "success": len(all_videos) > 0,
        "videos": all_videos,
        "total": len(all_videos),
        "error": error,
        "stats": stats,
    }
