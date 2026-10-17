"""
gen/hero_shorts/publish_targets/threads/payload.py — 쓰레드 요청 페이로드
========================================================================
설계서 §4.4 · §5.2 · F7·F8. **2026-10-18 docs.zernio.com 1차 출처로 배치 확정.**

쓰레드로 가는 필드는 두 계층이다(스펙 확인):

    엔트리 customContent    본문(≤500자) — 스펙: "Send a shorter Threads version in
                            customContent" (교차 발행 시 플랫폼별 본문 오버라이드)
    platformSpecificData    topic_tag   — 1~50자, '.'·'&' 금지
                            firstComment — ≤500자 · 발행 직후 댓글로 게시되며
                                            250/24h 쿼터에 포함된다

`threadItems`(스레드 시퀀스)는 사용하지 않는다 — 단일 포스트 발행이 설계다.
"""
import logging
from typing import Any, Dict, Optional

log = logging.getLogger("hero_shorts.publish_targets.threads.payload")

VERSION = "1.1.0"

PLATFORM = "threads"

TEXT_LIMIT = 500                # 스펙
FIRST_COMMENT_LIMIT = 500       # 스펙
TOPIC_TAG_MIN = 1               # 스펙
TOPIC_TAG_MAX = 50              # 스펙
FORBIDDEN_TOPIC_CHARS = (".", "&")


def content_text(projection: Dict[str, Any]) -> str:
    """엔트리 `customContent` — 쓰레드 본문. 500자 초과는 여기서 차단한다."""
    text = str(projection.get("content") or "")
    if not text.strip():
        raise ValueError("쓰레드 본문 없음 — 캡션 3투영을 먼저 생성하라")
    if len(text) > TEXT_LIMIT:
        raise ValueError(f"쓰레드 본문 {len(text)}자 > {TEXT_LIMIT}자 (TH-G2)")
    return text


def platform_specific_data(projection: Dict[str, Any], *,
                           topic_tag: Optional[str] = None,
                           first_comment: Optional[str] = None) -> Dict[str, Any]:
    """쓰레드 전용 데이터 — 스펙 표의 필드만 넣는다(`text` 아님: customContent 다)."""
    tag = topic_tag if topic_tag is not None else projection.get("topic_tag")
    out: Dict[str, Any] = {}
    if tag:
        t = str(tag)
        bad = [c for c in FORBIDDEN_TOPIC_CHARS if c in t]
        if bad or not (TOPIC_TAG_MIN <= len(t) <= TOPIC_TAG_MAX):
            raise ValueError(f"topic_tag 부적합({len(t)}자, 금지문자 {bad}): {t!r}")
        out["topic_tag"] = t
    fc = first_comment if first_comment is not None else projection.get("first_comment")
    if fc:
        s = str(fc)
        if len(s) > FIRST_COMMENT_LIMIT:
            raise ValueError(f"firstComment {len(s)}자 > {FIRST_COMMENT_LIMIT}자")
        out["firstComment"] = s
    return out


def platform_entry(account_id: str, projection: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """`platforms[]` 배열의 쓰레드 엔트리 1건."""
    if not account_id:
        raise ValueError("쓰레드 계정 ID 없음 — 시크릿 주입 선행 필요")
    entry: Dict[str, Any] = {
        "platform": PLATFORM,
        "accountId": str(account_id),
        "customContent": content_text(projection),
    }
    psd = platform_specific_data(projection, **kwargs)
    if psd:
        entry["platformSpecificData"] = psd
    return entry
