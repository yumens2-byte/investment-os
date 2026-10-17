"""
gen/hero_shorts/publish_targets/youtube/verify.py — 유튜브 발행 검증
====================================================================
설계서 §7 · T13. URL 에서 videoId 를 회수하고 원장 기록과 대조한다.

회수 우선순위(설계서 §7):
    1. 발행 응답의 platform_post_url → videoId 추출
    2. `list_posts` 조회로 보강(주입식 lister — 오프라인 테스트 가능)
    3. 불일치/부재는 **실패로 기록**한다(추정 채움 금지)

드리프트: 플랫폼이 보고한 게시시각과 우리 기록시각의 차이(분). 0 이 정상.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("hero_shorts.publish_targets.youtube.verify")

VERSION = "1.0.0"

# 확정 형식 — youtu.be/<id> · youtube.com/watch?v=<id> · /shorts/<id> · /embed/<id>
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_PATH_PREFIXES = ("shorts", "embed", "live", "v")


def extract_video_id(url: Optional[str]) -> Optional[str]:
    """유튜브 공개 URL → videoId. 형식이 아니면 None(추정 금지)."""
    if not url:
        return None
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None

    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if VIDEO_ID_RE.match(candidate or "") else None

    qs = parse_qs(parsed.query or "")
    if qs.get("v"):
        candidate = qs["v"][0]
        if VIDEO_ID_RE.match(candidate or ""):
            return candidate

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in _PATH_PREFIXES:
        candidate = parts[1]
        if VIDEO_ID_RE.match(candidate or ""):
            return candidate
    return None


def _parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def drift_minutes(published_at, recorded_at) -> Optional[int]:
    a, b = _parse_iso(published_at), _parse_iso(recorded_at)
    if not a or not b:
        return None
    return int(round(abs((b - a).total_seconds()) / 60.0))


def verify_url(record: Dict[str, Any], result: Any) -> Dict[str, Any]:
    """URL 기반 대조 — 외부 호출 없이 수행 가능한 부분."""
    recorded_id = ((record.get("targets") or {}).get("youtube") or {}).get("platform_post_id")
    url = getattr(result, "platform_post_url", None) or (
        result.get("platform_post_url") if isinstance(result, dict) else None)
    published_at = getattr(result, "published_at", None) or (
        result.get("published_at") if isinstance(result, dict) else None)
    recorded_at = getattr(result, "recorded_at", None) or (
        result.get("recorded_at") if isinstance(result, dict) else None)

    video_id = extract_video_id(url)
    problems: List[str] = []
    if url and not video_id:
        problems.append(f"URL 에서 videoId 추출 실패: {url}")
    if recorded_id and video_id and recorded_id != video_id:
        problems.append(f"원장 videoId {recorded_id} != 응답 videoId {video_id}")

    return {
        "matched_by": "url_video_id" if video_id else None,
        "platform_post_id": video_id,
        "drift_min": drift_minutes(published_at, recorded_at),
        "dupes": [],
        "problems": problems,
        "api_checked": False,
    }


def verify(record: Dict[str, Any], result: Any,
           lister: Optional[Callable[[str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """URL 대조 + (선택) `list_posts` 대조.

    lister 가 주어지면 post_id 로 조회해 videoId·게시시각을 회수한다.
    lister 가 없으면 URL 대조 결과만 돌려주고 `api_checked=False` 로 남긴다 —
    **추정으로 채우지 않는다.**
    """
    out = verify_url(record, result)
    post_id = getattr(result, "post_id", None) or (
        result.get("post_id") if isinstance(result, dict) else None)
    if not lister or not post_id:
        return out

    try:
        body = lister(post_id) or {}
    except Exception as exc:                      # 조회 실패는 검증 미완으로 남긴다
        out["problems"].append(f"list_posts 조회 실패: {type(exc).__name__}: {exc}")
        return out

    out["api_checked"] = True
    post = _find_post(body)
    if not post:
        out["problems"].append("list_posts 응답에서 게시물 없음")
        return out

    entry = _find_entry(post, "youtube")
    if not entry:
        out["problems"].append("list_posts 응답에 youtube 엔트리 없음")
        return out
    api_video_id = extract_video_id(entry.get("platformPostUrl") or entry.get("url"))
    if api_video_id:
        out["matched_by"] = "list_posts_video_id"
        if out["platform_post_id"] and api_video_id != out["platform_post_id"]:
            out["dupes"].append(api_video_id)
            out["problems"].append(
                f"응답 videoId {out['platform_post_id']} != 조회 videoId {api_video_id}")
        out["platform_post_id"] = api_video_id
    return out


def _find_post(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = body.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("post"), dict):
            return data["post"]
        if isinstance(data.get("posts"), list) and data["posts"]:
            return data["posts"][0]
    if isinstance(body.get("post"), dict):
        return body["post"]
    return None


def _find_entry(post: Dict[str, Any], platform: str) -> Optional[Dict[str, Any]]:
    for item in post.get("platforms") or []:
        if str(item.get("platform", "")).lower() == platform:
            return item
    return None
