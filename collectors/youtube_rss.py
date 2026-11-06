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

v1.2.0 (2026-11-06, DN2026_0103) — 실패 채널 재호출(최대 3회)
  배경: 11-06 정기 morning(01:49 UTC) 15채널 전부 http_error(404×14·500×1, text/html),
        같은 러너 21:19 UTC 실행은 전부 200 — 시간대에 따라 RSS 가 오류 페이지를 반환.
  - collect_youtube_summaries(max_attempts=1): 기본 1회(기존 동작). run_market 은 morning 만 3
  - 실패 사유(empty_feed·http_error·parse_error·fetch_error·entry_error) 채널만 재호출,
    ok·stale 은 재호출하지 않음. 대기 RETRY_DELAYS_SEC = (30, 60)
  - stats 에 attempts(실제 시도 횟수)·recovered(재시도로 회복한 채널 수) 추가
  - 채널별 최종 로그에 attempt=N, 영상 순서는 채널 등록 순서 유지

v1.2.1 (2026-11-06, DN2026_0105) — 재호출 대기 매회 60초로 변경
  - RETRY_DELAYS_SEC = (60,) — 모든 재호출 전 60초 대기(마지막 값 재사용 규칙 그대로)
  - 근거: 11-06 03:35·03:52 UTC 30→60초 3회 모두 404, 장애가 분 단위 이상 지속

VERSION = "1.2.1"
"""
import logging
import time
import urllib.error
from datetime import datetime, timedelta, timezone

VERSION = "1.2.1"

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

# DN2026_0103: 재호출 대기(초). 회차별 값, 목록보다 회차가 많으면 마지막 값 재사용
# DN2026_0105: 매회 60초
RETRY_DELAYS_SEC = (60,)
_sleep = time.sleep  # 테스트에서 교체


def _retry_delay(attempt: int) -> int:
    """attempt(2부터) 직전 대기 초."""
    idx = min(attempt - 2, len(RETRY_DELAYS_SEC) - 1)
    return RETRY_DELAYS_SEC[idx]


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


def _fetch_channel(feedparser, yt: dict, rss_base: str, cutoff) -> tuple:
    """채널 1개 1회 호출 → (사유 코드, 상세, videos 목록, 항목 수). 예외는 사유 코드로 가둔다."""
    name = yt["name"]
    try:
        feed = feedparser.parse(f"{rss_base}{yt['channel_id']}")
    except Exception as e:  # noqa: BLE001 — 채널 1개 실패가 전체를 막지 않도록
        return REASON_FETCH, type(e).__name__, [], 0

    reason, detail = _classify_feed(feed)
    if reason is not None:
        return reason, detail, [], 0

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
    except Exception as e:  # noqa: BLE001 — R6-02: 채널 단위로 실패를 가둔다
        return REASON_ENTRY, f"{type(e).__name__} {detail}", [], len(entries)
    if picked:
        return REASON_OK, detail, picked, len(entries)
    return REASON_STALE, detail, [], len(entries)


def collect_youtube_summaries(max_attempts: int = 1) -> dict:
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
                    "http_error": 0, "parse_error": 0, "fetch_error": 0, "entry_error": 0,
                    "attempts": 1, "recovered": 0},
        }

    Args:
        max_attempts: 실패 채널 최대 호출 횟수(DN2026_0103). 기본 1 = 재호출 없음.
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

    try:
        max_attempts = max(1, int(max_attempts))
    except (TypeError, ValueError):
        max_attempts = 1

    results = {}            # 채널 index → 최종 (reason, picked)
    pending = list(range(len(youtubers)))
    last_fail = {}          # 채널 index → 마지막 실패 (reason, detail)
    attempts_used = 0
    recovered = 0

    for attempt in range(1, max_attempts + 1):
        if not pending:
            break
        if attempt > 1:
            delay = _retry_delay(attempt)
            logger.info(
                f"[YouTubeRSS] 재시도 {attempt}/{max_attempts} — 대상 {len(pending)}채널 ({delay}s 대기)"
            )
            _sleep(delay)
        attempts_used = attempt
        still_failed = []
        for i in pending:
            reason, detail, picked, n_entries = _fetch_channel(
                feedparser, youtubers[i], RSS_BASE, cutoff
            )
            name = youtubers[i]["name"]
            tag = f" attempt={attempt}" if max_attempts > 1 else ""
            if reason in FAIL_REASONS:
                last_fail[i] = (reason, detail)
                still_failed.append(i)
                logger.warning(f"[YouTubeRSS] {name}: {reason} {detail}{tag}")
                continue
            results[i] = (reason, picked)
            if attempt > 1:
                recovered += 1
            if reason == REASON_OK:
                logger.info(f"[YouTubeRSS] {name}: {REASON_OK} n={len(picked)}{tag}")
            else:
                logger.info(f"[YouTubeRSS] {name}: {REASON_STALE} entries={n_entries} {detail}{tag}")
        pending = still_failed

    for i in pending:  # 최대 횟수 후에도 실패한 채널
        results[i] = (last_fail[i][0], [])

    for i in range(len(youtubers)):  # 채널 등록 순서 유지
        reason, picked = results[i]
        stats[reason] += 1
        all_videos.extend(picked)
    stats["attempts"] = attempts_used
    stats["recovered"] = recovered

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
