"""
gen/hero_shorts/publish_targets/caption_plan.py — 캡션 3투영 생성
=================================================================
설계서 §5. 회차 레코드 → {common, youtube, threads} 투영.
원칙: 문구 창작 금지 · 결정론적(같은 입력 → 같은 출력).

    YouTube : title(100자) / content(5,000자) / tags(합계 500자)
    Threads : content(500자) / topic_tag(1~50자, '.'·'&' 금지)

Threads 500자 초과 시 훅부터 제거한다. 제거로도 해결되지 않으면
**차단하지 않고 over_limit=True 로 반환**한다 — 판정은 TH-G2 게이트가 한다
(캡션 생성기는 판정하지 않는다: 책임 분리).
"""
import logging
import re

log = logging.getLogger("hero_shorts.publish_targets.caption_plan")

VERSION = "1.0.0"

DISCLAIMER = "⚠️ 투자 참고 정보, 투자 권유 아님"
HASHTAG_LIST = ("EDT", "JCUniverse", "시장만화", "주식", "매크로", "숏폼")
WORLD_TAG = "JCUniverse"
DEFAULT_TOPIC_TAG = "JCUniverse"

YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_DESC_LIMIT = 5000
YOUTUBE_TAG_TOTAL_LIMIT = 500
THREADS_TEXT_LIMIT = 500
TOPIC_TAG_LIMIT = 50

_HASHTAG_RE = re.compile(r"#[0-9A-Za-z가-힣_]+")


def _hashtag_line(tags=HASHTAG_LIST):
    return " ".join("#" + t for t in tags)


def _youtube_tags(tags=HASHTAG_LIST):
    """YouTube tags 배열 — '#' 제거, 태그당 100자, 합계 500자까지만 담는다."""
    out, total = [], 0
    for t in tags:
        t = str(t).lstrip("#").strip()
        if not t or len(t) > YOUTUBE_TITLE_LIMIT:
            continue
        if total + len(t) > YOUTUBE_TAG_TOTAL_LIMIT:
            break
        out.append(t)
        total += len(t)
    return out


def youtube_title(ep_label, title, limit=YOUTUBE_TITLE_LIMIT):
    """YouTube 제목 — 회차 라벨 + 회차 제목. 초과 시 회차 제목을 우선 보존한다.

    라벨이 제목 단독이 되는 것을 막는 것이 Y-G3 의 목적이다(설계서 §4.3).
    """
    title = str(title or "").strip()
    label = str(ep_label or "").strip()
    if not label:
        return title[:limit]
    room = limit - len(label) - 1
    if room <= 0:
        return label[:limit]
    return f"{label} {title[:room]}".strip()


def threads_content(title, hook=None, tags=HASHTAG_LIST,
                    limit=THREADS_TEXT_LIMIT):
    """Threads 본문 — 500자 상한. 초과 시 훅 제거 → 해시태그 축소 순.

    반환: (text, compressions:list[str], over_limit:bool)
    """
    title = str(title or "").strip()
    hook_t = str(hook or "").strip()
    compressions = []

    def _build(use_hook, tag_list):
        parts = [title]
        if use_hook and hook_t:
            parts.append(hook_t)
        parts.append(DISCLAIMER)
        if tag_list:
            parts.append(_hashtag_line(tag_list))
        return "\n\n".join([p for p in parts if p]).strip() + "\n"

    text = _build(True, tags)
    if len(text) <= limit:
        return text, compressions, False

    # 1단계 — 훅 제거
    if hook_t:
        text = _build(False, tags)
        compressions.append("hook_removed")
        if len(text) <= limit:
            return text, compressions, False

    # 2단계 — 해시태그 축소(세계관 태그 우선 보존)
    kept = [t for t in tags if t == WORLD_TAG]
    text = _build(False, kept)
    compressions.append("hashtags_trimmed")
    if len(text) <= limit:
        return text, compressions, False

    # 3단계 — 해시태그 전면 제거
    text = _build(False, ())
    compressions.append("hashtags_removed")
    return text, compressions, len(text) > limit


def sanitize_topic_tag(value, fallback=DEFAULT_TOPIC_TAG):
    """topic_tag — '.'·'&' 를 제거하고 50자로 절단. 쓰레드 규격(§4.4 TH-G3)."""
    t = str(value or fallback)
    for ch in (".", "&"):
        t = t.replace(ch, "")
    t = t.strip()
    if not t:
        t = fallback
    return t[:TOPIC_TAG_LIMIT]


def build_projections(record, ep_label=None, hook=None,
                      topic_tag=DEFAULT_TOPIC_TAG, tags=HASHTAG_LIST):
    """회차 레코드 → 3투영. 결정론적.

    Args:
        record: 원장 회차 레코드(status/title/approval/targets 등)
        ep_label: 회차 라벨(예: "Ep89"). 없으면 제목만 사용.
        hook: 세계관 훅(마스터 입력). 없으면 미사용.
        topic_tag: Threads topic_tag.
        tags: 해시태그 목록.

    Returns:
        {"common": {...}, "youtube": {...}, "threads": {...}}
    """
    title = str(record.get("title") or "").strip()
    if not title:
        raise ValueError("캡션 생성 불가 — 회차 제목 없음(추측 생성 금지)")

    yt_title = youtube_title(ep_label, title)
    yt_parts = [yt_title]
    if hook and str(hook).strip():
        yt_parts.append(str(hook).strip())
    yt_parts += [DISCLAIMER, _hashtag_line(tags)]
    yt_content = "\n\n".join([p for p in yt_parts if p]).strip() + "\n"

    th_text, compressions, over_limit = threads_content(title, hook=hook, tags=tags)

    projections = {
        "common": {
            "disclaimer": DISCLAIMER,
            "hashtags": ["#" + t for t in tags],
            "ep_label": ep_label or "",
            "world_tag": WORLD_TAG,
        },
        "youtube": {
            "title": yt_title,
            "content": yt_content,
            "tags": _youtube_tags(tags),
            "over_limit": len(yt_content) > YOUTUBE_DESC_LIMIT,
        },
        "threads": {
            "content": th_text,
            "topic_tag": sanitize_topic_tag(topic_tag),
            "first_comment": None,
            "compressions": compressions,
            "over_limit": over_limit,
        },
    }
    log.info("캡션 3투영 생성: 라벨=%s · yt제목=%d자 · th본문=%d자(압축=%s)",
             ep_label, len(yt_title), len(th_text), compressions or "없음")
    return projections
