"""
gen/hero_shorts/publish_targets/youtube/payload.py — 유튜브 요청 페이로드
========================================================================
설계서 §4.3 · §6.1 · F5. **2026-10-18 docs.zernio.com 1차 출처로 필드 배치 확정.**

유튜브로 가는 필드는 **두 계층으로 나뉜다**(스펙 확인):

    최상위(플랫폼 공통)     tags[]          — "tags sits at the top level of the request,
                                              alongside content" (태그당 ≤100자 · 합계 ≤500자)
    엔트리 customContent   본문(설명)       — 유튜브 설명 = content 전체
    platformSpecificData  title            — 미지정 시 content 첫 줄이 제목이 된다
                          visibility / madeForKids / containsSyntheticMedia
                          categoryId / playlistId / firstComment(≤10,000자)

스펙 근거(실측):
  · "A plain post becomes a public video whose title is the first line of content and
     whose description is the whole of content."
  · "tags sits at the top level of the request, alongside content, and reaches YouTube
     as snippet.tags"
  · 커스텀 썸네일은 **Shorts 에서 지원되지 않는다** — 썸네일 필드를 보내지 않는다.
"""
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("hero_shorts.publish_targets.youtube.payload")

VERSION = "1.1.0"

CATEGORY_EDUCATION = "27"       # E11 승인값
DEFAULT_VISIBILITY = "public"   # E11 승인값
DEFAULT_MADE_FOR_KIDS = False   # E11 승인값
DEFAULT_CONTAINS_SYNTHETIC_MEDIA = True   # Y-G2 필수

TITLE_LIMIT = 100               # 스펙
DESC_LIMIT = 5000               # 스펙
TAG_ITEM_LIMIT = 100            # 스펙
TAG_TOTAL_LIMIT = 500           # 스펙
FIRST_COMMENT_LIMIT = 10000     # 스펙(유튜브는 쓰레드 500자와 다르다)


def title_from_projection(projection: Dict[str, Any]) -> str:
    return str(projection.get("title") or "").strip()


def first_line(text: str) -> str:
    lines = str(text or "").splitlines()
    return lines[0].strip() if lines else ""


def first_line_matches_title(projection: Dict[str, Any]) -> bool:
    """content 첫 줄이 title 과 일치하는가 — 불일치면 유튜브 제목이 오염된다."""
    return first_line(projection.get("content")) == title_from_projection(projection)


def content_text(projection: Dict[str, Any]) -> str:
    """최상위/엔트리 `content` — 유튜브 설명이자 제목 파생원.

    스펙: 제목은 content 첫 줄, 설명은 content 전체다. 따라서 첫 줄을 제목으로
    고정하지 않으면 공개 제목이 오염된다(F6). 첫 줄이 title 과 다르면 앞에 붙인다.
    """
    title = title_from_projection(projection)
    if not title:
        raise ValueError("유튜브 제목 없음 — 캡션 3투영을 먼저 생성하라")
    if "\n" in title or "\r" in title:
        raise ValueError("유튜브 제목에 줄바꿈 불가 — 첫 줄이 제목이 된다")
    if len(title) > TITLE_LIMIT:
        raise ValueError(f"유튜브 제목 {len(title)}자 > {TITLE_LIMIT}자")
    body = str(projection.get("content") or "").strip()
    text = body if first_line(body) == title else f"{title}\n\n{body}".strip()
    if len(text) > DESC_LIMIT:
        raise ValueError(f"유튜브 설명 {len(text)}자 > {DESC_LIMIT}자")
    return text


def tags_of(projection: Dict[str, Any]) -> List[str]:
    """최상위 `tags` 배열. 스펙 상한(태그당 100자·합계 500자)을 넘는 항목은 자른다."""
    out: List[str] = []
    total = 0
    for t in (projection.get("tags") or []):
        s = str(t)
        if len(s) > TAG_ITEM_LIMIT:
            continue                    # 스펙: 100자 초과 태그는 건너뛴다
        if total + len(s) > TAG_TOTAL_LIMIT:
            break                       # 스펙: 합계 500자까지만 유지
        out.append(s)
        total += len(s)
    return out


def platform_specific_data(projection: Dict[str, Any], *,
                           visibility: str = DEFAULT_VISIBILITY,
                           category_id: str = CATEGORY_EDUCATION,
                           made_for_kids: bool = DEFAULT_MADE_FOR_KIDS,
                           contains_synthetic_media: bool = DEFAULT_CONTAINS_SYNTHETIC_MEDIA,
                           playlist_id: Optional[str] = None,
                           first_comment: Optional[str] = None,
                           ) -> Dict[str, Any]:
    """유튜브 전용 데이터 — 스펙 표의 필드만 넣는다.

    `description`·`tags` 는 여기 넣지 않는다(스펙상 최상위다).
    """
    title = title_from_projection(projection)
    if not title:
        raise ValueError("유튜브 제목 없음 — 캡션 3투영을 먼저 생성하라")
    out: Dict[str, Any] = {
        "title": title,
        "visibility": str(visibility or DEFAULT_VISIBILITY),
        "categoryId": str(category_id),
        "madeForKids": bool(made_for_kids),
        "containsSyntheticMedia": bool(contains_synthetic_media),
    }
    if playlist_id:
        out["playlistId"] = str(playlist_id)
    if first_comment:
        fc = str(first_comment)
        if len(fc) > FIRST_COMMENT_LIMIT:
            raise ValueError(f"firstComment {len(fc)}자 > {FIRST_COMMENT_LIMIT}자")
        out["firstComment"] = fc
    return out


def platform_entry(account_id: str, projection: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """`platforms[]` 배열의 유튜브 엔트리 1건 (customContent 포함)."""
    if not account_id:
        raise ValueError("유튜브 계정 ID 없음 — 시크릿 주입 선행 필요")
    return {
        "platform": "youtube",
        "accountId": str(account_id),
        "customContent": content_text(projection),
        "platformSpecificData": platform_specific_data(projection, **kwargs),
    }
