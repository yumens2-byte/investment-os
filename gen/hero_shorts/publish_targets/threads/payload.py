"""
gen/hero_shorts/publish_targets/threads/payload.py — 쓰레드 요청 페이로드
=========================================================================
설계서 §4.4 · §5.2 · F7·F8.

    text         : 본문(≤500자 · TH-G2)
    topic_tag    : 1~50자, '.'·'&' 금지 (TH-G3 / F8 문서 표기 그대로)
    firstComment : 기본 미사용(TH-G5)

**미확인**: 필드명은 F8 에 기재된 표기를 그대로 쓴다(`topic_tag` · `firstComment`).
Zernio REST 스펙 1차 열람 후 확정 대상이며, 수정 지점을 이 파일 1곳으로 제한한다.
"""
import logging
from typing import Any, Dict, Optional

log = logging.getLogger("hero_shorts.publish_targets.threads.payload")

VERSION = "1.0.0"

PLATFORM = "threads"


def platform_specific_data(projection: Dict[str, Any], *,
                           topic_tag: Optional[str] = None,
                           first_comment: Optional[str] = None) -> Dict[str, Any]:
    """쓰레드 전용 데이터. projection 은 `caption_plan.build_projections()["threads"]`."""
    text = str(projection.get("content") or "")
    if not text.strip():
        raise ValueError("쓰레드 본문 없음 — 캡션 3투영을 먼저 생성하라")
    tag = topic_tag if topic_tag is not None else projection.get("topic_tag")
    out: Dict[str, Any] = {"text": text}
    if tag:
        out["topic_tag"] = str(tag)
    fc = first_comment if first_comment is not None else projection.get("first_comment")
    if fc:
        out["firstComment"] = str(fc)
    return out


def platform_entry(account_id: str, projection: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """`platforms[]` 배열의 쓰레드 엔트리 1건."""
    if not account_id:
        raise ValueError("쓰레드 계정 ID 없음 — 시크릿 주입 선행 필요")
    return {
        "platform": PLATFORM,
        "accountId": str(account_id),
        "platformSpecificData": platform_specific_data(projection, **kwargs),
    }
