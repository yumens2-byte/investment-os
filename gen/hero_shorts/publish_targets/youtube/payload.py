"""
gen/hero_shorts/publish_targets/youtube/payload.py — 유튜브 요청 페이로드
========================================================================
설계서 §4.3 · §6.1 · F5. `platformSpecificData` 를 조립한다.

    containsSyntheticMedia : 합성미디어 고지 (Y-G2 · 필수 true)
    visibility             : public | private | unlisted (Y-G4 · 기본 public)
    madeForKids            : 기본 false (E11)
    categoryId             : 기본 27 Education (E11)
    title / description / tags

**미확인**: Zernio REST 의 봉투 키·필드 대소문자는 1차 스펙 미열람이다(설계서 §13-2).
여기 쓰인 필드명은 Zernio 플랫폼 문서에 기재된 이름 그대로이며, 확정 시
수정 지점을 이 파일 1곳으로 제한한다.
"""
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("hero_shorts.publish_targets.youtube.payload")

VERSION = "1.0.0"

CATEGORY_EDUCATION = "27"       # E11 승인값
DEFAULT_VISIBILITY = "public"   # E11 승인값
DEFAULT_MADE_FOR_KIDS = False   # E11 승인값
DEFAULT_CONTAINS_SYNTHETIC_MEDIA = True   # Y-G2 필수


def platform_specific_data(projection: Dict[str, Any], *,
                           visibility: str = DEFAULT_VISIBILITY,
                           category_id: str = CATEGORY_EDUCATION,
                           made_for_kids: bool = DEFAULT_MADE_FOR_KIDS,
                           contains_synthetic_media: bool = DEFAULT_CONTAINS_SYNTHETIC_MEDIA,
                           ) -> Dict[str, Any]:
    """유튜브 전용 데이터. projection 은 `caption_plan.build_projections()["youtube"]`."""
    title = str(projection.get("title") or "").strip()
    if not title:
        raise ValueError("유튜브 제목 없음 — 캡션 3투영을 먼저 생성하라")
    return {
        "title": title,
        "description": str(projection.get("content") or ""),
        "tags": [str(t) for t in (projection.get("tags") or [])],
        "visibility": str(visibility or DEFAULT_VISIBILITY),
        "categoryId": str(category_id),
        "madeForKids": bool(made_for_kids),
        "containsSyntheticMedia": bool(contains_synthetic_media),
    }


def platform_entry(account_id: str, projection: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """`platforms[]` 배열의 유튜브 엔트리 1건."""
    if not account_id:
        raise ValueError("유튜브 계정 ID 없음 — 시크릿 주입 선행 필요")
    return {
        "platform": "youtube",
        "accountId": str(account_id),
        "platformSpecificData": platform_specific_data(projection, **kwargs),
    }


def title_from_projection(projection: Dict[str, Any]) -> str:
    """유튜브가 content 첫 줄을 제목으로 쓰므로(F6), 첫 줄 == title 을 보증해야 한다."""
    return str(projection.get("title") or "").strip()


def first_line(text: str) -> str:
    lines = str(text or "").splitlines()
    return lines[0].strip() if lines else ""


def first_line_matches_title(projection: Dict[str, Any]) -> bool:
    """content 첫 줄이 title 과 일치하는가 — 불일치면 유튜브 제목이 오염된다."""
    return first_line(projection.get("content")) == title_from_projection(projection)
