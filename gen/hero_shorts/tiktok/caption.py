"""
gen/hero_shorts/tiktok/caption.py — 틱톡 편별 캡션 생성
========================================================
설계서 v1.0 §2.2 준수. 문구 창작 없음 — 제목·시리즈 마커·고정 템플릿만 결합한다.

    편1: 세계관 훅(마스터 입력) + 독립 이해 가능 문구
    편2·3: "1편에서 이어집니다" 연결 유도
    전 편: 면책문 + 세계관 태그 포함 해시태그
"""
import logging

from ..caption import DISCLAIMER

log = logging.getLogger("tiktok.caption")

VERSION = "1.0.0"

HASHTAGS = "#EDT #JCUniverse #시장만화 #주식 #매크로 #숏폼"
OPENING_TEMPLATE = "{title} {marker}"
CONTINUE_TEMPLATE = "{title} {marker} — 1편에서 이어집니다"


def build_segment_caption(segment, title, hook=None):
    """분할 세그먼트 1편분 캡션. 결정론적(같은 입력 → 같은 캡션)."""
    no = segment["segment_no"]
    marker = segment["series_marker"]
    if no == 1:
        head = hook.strip() if hook else OPENING_TEMPLATE.format(title=title, marker=marker)
    else:
        head = CONTINUE_TEMPLATE.format(title=title, marker=marker)
    return f"{head}\n\n{DISCLAIMER}\n\n{HASHTAGS}".strip() + "\n"

def build_combined_caption(title, hook=None):
    """합본(단일 편) 캡션 — 시리즈 마커 없음. 문구 창작 없음(결정론적)."""
    parts = [title.strip()]
    if hook and hook.strip():
        parts.append(hook.strip())
    head = "\n".join(parts)
    return f"{head}\n\n{DISCLAIMER}\n\n{HASHTAGS}".strip() + "\n"
