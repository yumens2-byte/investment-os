"""
gen/hero_shorts/publish_targets/threads/verify.py — 쓰레드 발행 검증
====================================================================
설계서 §7 · T13. post code 를 회수해 원장 기록과 대조한다.

## 미확인 (설계서 §13-1) — 추정 금지
쓰레드 공개 URL 형식은 1차 출처에서 확인하지 못했다.
`threads.net/@user/post/<code>` 로 **가정**해 정규식을 두되, `confirmed=False` 로
표시하고 실측 1건 확보 후 확정한다. 가정이 틀리면 `extract_post_code` 는 None 을
돌려주고 verify 는 "추출 실패"를 문제로 남긴다 — 성공으로 채우지 않는다.
"""
import logging
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger("hero_shorts.publish_targets.threads.verify")

VERSION = "1.0.0"

# 가정(미확인) — 실측 확보 시 확정. threads.net / threads.com 양쪽 허용.
THREADS_HOSTS = ("threads.net", "www.threads.net", "threads.com", "www.threads.com")
POST_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")
_POST_MARKER = "post"

URL_FORMAT_CONFIRMED = False   # §13-1 미확인 — True 로 바꾸려면 실측 1건 필요


def extract_post_code(url: Optional[str]) -> Optional[str]:
    """쓰레드 공개 URL → post code. 형식이 아니면 None."""
    if not url:
        return None
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in THREADS_HOSTS:
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if _POST_MARKER not in parts:
        return None
    idx = parts.index(_POST_MARKER)
    if idx + 1 >= len(parts):
        return None
    code = parts[idx + 1]
    return code if POST_CODE_RE.match(code or "") else None


def verify_url(record: Dict[str, Any], result) -> Dict[str, Any]:
    recorded_code = ((record.get("targets") or {}).get("threads") or {}).get("platform_post_id")
    url = getattr(result, "platform_post_url", None) or (
        result.get("platform_post_url") if isinstance(result, dict) else None)

    code = extract_post_code(url)
    problems: List[str] = []
    if url and not code:
        problems.append(
            f"URL 에서 post code 추출 실패: {url} (URL 형식 미확인 — §13-1)")
    if recorded_code and code and recorded_code != code:
        problems.append(f"원장 code {recorded_code} != 응답 code {code}")

    return {
        "matched_by": "url_post_code" if code else None,
        "format_confirmed": URL_FORMAT_CONFIRMED,
        "platform_post_id": code,
        "drift_min": None,
        "dupes": [],
        "problems": problems,
        "api_checked": False,
    }


def verify(record: Dict[str, Any], result: Any,
           lister: Optional[Callable[[str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    out = verify_url(record, result)
    post_id = getattr(result, "post_id", None) or (
        result.get("post_id") if isinstance(result, dict) else None)
    if not lister or not post_id:
        return out
    try:
        body = lister(post_id) or {}
    except Exception as exc:
        out["problems"].append(f"list_posts 조회 실패: {type(exc).__name__}: {exc}")
        return out
    out["api_checked"] = True
    post = _find_post(body)
    if not post:
        out["problems"].append("list_posts 응답에서 게시물 없음")
        return out
    entry = _find_entry(post, "threads")
    if not entry:
        out["problems"].append("list_posts 응답에 threads 엔트리 없음")
        return out
    code = extract_post_code(entry.get("platformPostUrl") or entry.get("url"))
    if code:
        out["matched_by"] = "list_posts_post_code"
        out["platform_post_id"] = code
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
