"""gen/hero_shorts/tiktok/recover.py — 발행 결과 회수(Zernio 실조회)
====================================================================
S7 `record` 경로가 플랫폼 정본 값을 받지 못하면 원장에
`post_id=None` · `published_at=로컬 now` 가 박힌다(2026-10-15 ep88 실측:
원장 02:24Z vs 플랫폼 01:38Z — 46분 불일치).

여기서는 Zernio `list_posts` 를 **읽기만** 하여 플랫폼 정본을 회수한다.
발행·크레딧 0. 매칭 실패 시 추측하지 않고 None 을 반환한다(fail-closed).
"""
import json
import logging
import os
import subprocess

log = logging.getLogger("tiktok.recover")

PLATFORM = "tiktok"
DEFAULT_LIMIT = 20


def _run_json(args, timeout=120):
    proc = subprocess.run(["gsk", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gsk {' '.join(args[:3])} 실패 rc={proc.returncode} — {proc.stderr.strip()[:200]}"
        )
    out = proc.stdout
    idx = out.find("{")
    if idx < 0:
        raise RuntimeError("list_posts 응답에 JSON 없음 — 파싱 거부")
    return json.loads(out[idx:])


def _iter_posts(node):
    """응답 구조 변화에 견디는 재귀 수집 — platform==tiktok 인 게시물만."""
    if isinstance(node, dict):
        if node.get("platform") == PLATFORM and ("platformPostUrl" in node or "status" in node):
            yield node
        for value in node.values():
            yield from _iter_posts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_posts(value)


def tiktok_post_id_from_url(url):
    """플랫폼 URL → 숫자 게시물 ID. 미매칭은 None(추측 금지)."""
    if not url:
        return None
    tail = str(url).rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else None


def list_tiktok_posts(account_id=None, limit=DEFAULT_LIMIT):
    aid = account_id or os.environ.get("HERO_TIKTOK_ACCOUNT_ID")
    args = ["zernio", "list_posts", "--platform", PLATFORM, "-n", str(limit)]
    if aid:
        args += ["--account_id", aid]
    payload = _run_json(args)
    seen, out = set(), []
    for post in _iter_posts(payload):
        pid = post.get("_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(post)
    return out


def resolve_published_post(post_url=None, post_id=None, account_id=None,
                           limit=DEFAULT_LIMIT, allow_latest=False):
    """Zernio 실조회 → 플랫폼 정본 필드. 매칭 실패 시 None.

    `allow_latest=True` 는 post_url·post_id 없이 '가장 최근 발행건'을 쓰는
    명시적 예외 경로다(감사 기록 전용). 기본은 fail-closed.
    """
    posts = list_tiktok_posts(account_id=account_id, limit=limit)
    plat_id = tiktok_post_id_from_url(post_url)
    matched, how = None, None
    for post in posts:
        if post_id and post.get("_id") == post_id:
            matched, how = post, "post_id"
            break
        if plat_id and tiktok_post_id_from_url(post.get("platformPostUrl")) == plat_id:
            matched, how = post, "post_url"
            break
    if matched is None and allow_latest and posts:
        matched, how = posts[0], "latest"
    if matched is None:
        log.warning("발행 결과 회수 실패 — 매칭 없음(post_url=%s post_id=%s)", post_url, post_id)
        return None
    purl = matched.get("platformPostUrl")
    return {
        "post_id": matched.get("_id"),
        "platform_post_id": tiktok_post_id_from_url(purl),
        "platform_post_url": purl,
        "published_at": matched.get("publishedAt") or matched.get("scheduledFor"),
        "status": matched.get("status"),
        "matched_by": how,
    }
