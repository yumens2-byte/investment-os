"""
publishers/fb_formatter.py
==========================
Telegram(HTML parse_mode) 텍스트 → Facebook 페이지 게시용 평문 변환.

배경 (FB-1, 2026-10-05)
  - Facebook 은 본문 HTML 을 렌더링하지 않는다. TG 텍스트의 <b>/<i> 태그가
    그대로 노출되므로 제거한다.
  - TG free 텍스트에는 텔레그램 전용 안내 줄이 있다.
      telegram_publisher.format_free_signal (full 세션):
        "💎 <i>풀버전 대시보드 → 유료 채널</i>"
    Facebook 에는 의미가 없으므로 해당 줄을 제거한다.
"""
from __future__ import annotations

import html
import re

VERSION = "1.0.0"

# Facebook 게시 시 제거할 TG 전용 줄 식별 문구 (줄 단위 포함 검사)
TG_ONLY_LINE_MARKERS: tuple[str, ...] = ("유료 채널",)

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def to_facebook_text(tg_html: str) -> str:
    """TG HTML 텍스트를 Facebook 평문으로 변환한다. 결과가 비면 "" 반환."""
    if not tg_html:
        return ""

    text = _TAG_RE.sub("", tg_html)
    text = html.unescape(text)

    lines = [
        line.rstrip()
        for line in text.splitlines()
        if not any(marker in line for marker in TG_ONLY_LINE_MARKERS)
    ]
    text = "\n".join(lines)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()
