"""
core/ai_output_validator.py
============================
AI 트윗 출력 검증 + 어색함 휴리스틱 단일 진입점 (x_formatter v1.5.0~).

목적:
  - x_formatter._detect_non_publishable_chars 이전 (DRY 위반 해소)
  - 7종 검증 통합: length / non_korean / hashtag_count / emoji_count /
                   forbidden_phrase / prompt_leak / awkwardness
  - 어색함 점수화 (0.0~1.0) — D-7 활성화 후 0.6 이상 retry 트리거

설계 원칙:
  - 휴리스틱 기반 (ML 미사용) — 운영 안정성 우선
  - false positive 최소화 (한자/영어/숫자/이모지 통과)
  - 실패 시 첫 사유만 반환 (retry 프롬프트 단순화)

변경이력:
  v1.0.0 (2026-05-06) 신설.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from core.tone_policy import ToneSpec

VERSION = "1.0.0"

logger = logging.getLogger(__name__)
logger.info(f"[OutputValidator] v{VERSION} 로드")


# ─────────────────────────────────────────────────────────────
# 비한국어 패턴 (x_formatter._NON_PUBLISHABLE_PATTERN 이전)
# ─────────────────────────────────────────────────────────────
# 명확한 비한국어 스크립트 — 한국어 콘텐츠에 등장 불가.
# 한자(CJK)는 false positive 방지 위해 통과시킴.
_NON_PUBLISHABLE_PATTERN = re.compile(
    "["
    "\u0900-\u097F"   # Devanagari (힌디/산스크리트)
    "\u0980-\u09FF"   # Bengali
    "\u0A00-\u0A7F"   # Gurmukhi
    "\u0A80-\u0AFF"   # Gujarati
    "\u0B00-\u0B7F"   # Oriya
    "\u0B80-\u0BFF"   # Tamil
    "\u0C00-\u0C7F"   # Telugu
    "\u0C80-\u0CFF"   # Kannada
    "\u0D00-\u0D7F"   # Malayalam
    "\u0E00-\u0E7F"   # Thai
    "\u0E80-\u0EFF"   # Lao
    "\u1000-\u109F"   # Myanmar
    "\u0600-\u06FF"   # Arabic
    "\u0750-\u077F"   # Arabic Supplement
    "\u08A0-\u08FF"   # Arabic Extended-A
    "\u0590-\u05FF"   # Hebrew
    "\u0400-\u04FF"   # Cyrillic
    "\u0500-\u052F"   # Cyrillic Supplement
    "\u3040-\u309F"   # Hiragana
    "\u30A0-\u30FF"   # Katakana
    "\u0370-\u03FF"   # Greek
    "]"
)


# ─────────────────────────────────────────────────────────────
# 이모지 패턴 (개수 카운트용)
# ─────────────────────────────────────────────────────────────
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # 기호 & 그림문자
    "\U0001F600-\U0001F64F"  # 이모티콘
    "\U0001F680-\U0001F6FF"  # 교통/지도
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"  # 기타 기호 (☀️/⚠️ 등)
    "\U00002700-\U000027BF"  # Dingbats
    "]"
)


# ─────────────────────────────────────────────────────────────
# 메타 라벨 / 프롬프트 누설 패턴
# ─────────────────────────────────────────────────────────────
_PROMPT_LEAK_HEAD_PATTERNS = (
    re.compile(r"^\s*\*{0,2}\[?\s*톤\s*[:：]?", re.IGNORECASE),
    re.compile(r"^\s*\*{0,2}\[?\s*(조건|출력|본문|내용|결론|요약|답변|트윗)\s*[:：]"),
    re.compile(r"^\s*#{1,6}\s+"),                  # 마크다운 헤더
    re.compile(r"^\s*\*{2}[^*]+\*{2}\s*$"),        # 굵은체 한 줄
    re.compile(r"^\s*\[.+톤.+\]\s*$"),             # [긴급 톤], [낙관적 톤]
)

# ─────────────────────────────────────────────────────────────
# 어색함 휴리스틱 보조 패턴
# ─────────────────────────────────────────────────────────────
# 미완결 종결: 마지막 어절이 조사/접속어로 끝남
_INCOMPLETE_TRAILING_PATTERN = re.compile(
    r"(은|는|이|가|을|를|에|에서|와|과|도|만|의|로|으로|및|또는|그리고|하지만|그런데|즉)$"
)

# 마크다운 잔존
_MARKDOWN_RESIDUAL_PATTERNS = (
    re.compile(r"\*{2}[^*]+\*{2}"),     # **bold**
    re.compile(r"`[^`]+`"),             # `code`
    re.compile(r"^#{1,6}\s+", re.MULTILINE),
)

# 톤-내용 미스매치 — HIGH 리스크에서 절대 안 나올 표현
_HIGH_RISK_INAPPROPRIATE = ("ㅋ", "ㅎ", ":)", "(:", "ㅠ ㅎ", "헤헤")


# ─────────────────────────────────────────────────────────────
# 데이터 구조
# ─────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """검증 결과 — ai_quality_store에 그대로 적재."""
    passed: bool
    failure_reason: Optional[str]
    checks: dict = field(default_factory=dict)
    awkwardness_score: float = 0.0
    awkwardness_flags: list[str] = field(default_factory=list)

    def to_flags_jsonb(self) -> dict:
        """ai_quality_log.flags JSONB 컬럼 적재용."""
        return {
            "checks":            self.checks,
            "awkwardness_flags": self.awkwardness_flags,
        }


# ─────────────────────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────────────────────

def detect_non_publishable_chars(text: str) -> Optional[str]:
    """
    비한국어 스크립트 감지. 첫 매칭 주변 컨텍스트 반환 (디버깅용).
    매칭 없으면 None.

    x_formatter v1.4.0 _detect_non_publishable_chars 이전.
    """
    if not text:
        return None
    m = _NON_PUBLISHABLE_PATTERN.search(text)
    if not m:
        return None
    start = max(0, m.start() - 5)
    end   = min(len(text), m.end() + 10)
    return text[start:end]


def validate(
    text: str,
    spec: ToneSpec,
    *,
    is_thread_post: bool = False,
) -> ValidationResult:
    """
    7종 통합 검증. 첫 실패 사유만 failure_reason으로 반환.

    Args:
        text: AI 출력 텍스트 (clean 처리 후)
        spec: 1차에 결정된 ToneSpec
        is_thread_post: 스레드 포스트는 해시태그 체크 완화

    Returns:
        ValidationResult
    """
    checks: dict = {}

    # 1. length
    text_len = len(text)
    length_min, length_max = spec.length_target
    # ±20% 허용오차 (스레드 포스트는 더 관대)
    tolerance = 0.30 if is_thread_post else 0.15
    soft_min  = int(length_min * (1 - tolerance))
    soft_max  = int(length_max * (1 + tolerance))
    if text_len < soft_min:
        checks["length"] = {"value": text_len, "ok": False, "reason": "too_short"}
        return _fail("length_too_short", checks, text, spec)
    if text_len > soft_max:
        checks["length"] = {"value": text_len, "ok": False, "reason": "too_long"}
        return _fail("length_too_long", checks, text, spec)
    checks["length"] = {"value": text_len, "ok": True}

    # 2. non_korean
    non_kr = detect_non_publishable_chars(text)
    if non_kr is not None:
        checks["non_korean"] = {"detected": non_kr, "ok": False}
        return _fail("non_korean", checks, text, spec)
    checks["non_korean"] = {"ok": True}

    # 3. hashtag_count
    hashtags = re.findall(r"#\S+", text)
    hashtag_count = len(hashtags)
    if not is_thread_post:
        ht_min, ht_max = spec.hashtag_count_target
        if hashtag_count < ht_min - 1 or hashtag_count > ht_max + 2:
            # off-by-one 1개, 상한 2개 허용 (Gemini 변동 흡수)
            checks["hashtag_count"] = {"value": hashtag_count, "ok": False}
            return _fail("hashtag_count", checks, text, spec)
    checks["hashtag_count"] = {"value": hashtag_count, "ok": True}

    # 4. emoji_count
    emoji_count = len(_EMOJI_PATTERN.findall(text))
    if emoji_count == 0 or emoji_count > 6:
        checks["emoji_count"] = {"value": emoji_count, "ok": False}
        return _fail("emoji_count", checks, text, spec)
    checks["emoji_count"] = {"value": emoji_count, "ok": True}

    # 5. forbidden_phrase
    found = _find_forbidden(text, spec.forbidden)
    if found:
        checks["forbidden_phrase"] = {"matched": found, "ok": False}
        return _fail("forbidden_phrase", checks, text, spec)
    checks["forbidden_phrase"] = {"ok": True}

    # 6. prompt_leak
    leak = _find_prompt_leak(text)
    if leak:
        checks["prompt_leak"] = {"matched": leak, "ok": False}
        return _fail("prompt_leak", checks, text, spec)
    checks["prompt_leak"] = {"ok": True}

    # 7. awkwardness (점수만 산출, 차단은 D-7부터)
    awk_score, awk_flags = _score_awkwardness(text, spec)
    checks["awkwardness"] = {"score": awk_score, "flags": awk_flags, "ok": True}

    # D-7까지는 awkwardness로 차단하지 않음 (shadow only).
    # D-7 활성화 시 아래 주석 해제:
    # if awk_score >= 0.6:
    #     return ValidationResult(
    #         passed=False, failure_reason="awkward",
    #         checks=checks, awkwardness_score=awk_score, awkwardness_flags=awk_flags,
    #     )

    return ValidationResult(
        passed=True,
        failure_reason=None,
        checks=checks,
        awkwardness_score=awk_score,
        awkwardness_flags=awk_flags,
    )


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

def _fail(reason: str, checks: dict, text: str, spec: ToneSpec) -> ValidationResult:
    """검증 실패 시 awkwardness도 같이 산출 (적재용 일관성)."""
    awk_score, awk_flags = _score_awkwardness(text, spec)
    return ValidationResult(
        passed=False,
        failure_reason=reason,
        checks=checks,
        awkwardness_score=awk_score,
        awkwardness_flags=awk_flags,
    )


def _find_forbidden(text: str, forbidden: tuple[str, ...]) -> Optional[str]:
    """대소문자 무시 단순 substring 체크. 첫 매칭 반환."""
    text_lower = text.lower()
    for phrase in forbidden:
        if phrase.lower() in text_lower:
            return phrase
    return None


def _find_prompt_leak(text: str) -> Optional[str]:
    """첫 줄에 메타 라벨/마크다운 헤더 등이 있는지."""
    if not text.strip():
        return None
    first_line = text.strip().split("\n", 1)[0]
    for pat in _PROMPT_LEAK_HEAD_PATTERNS:
        if pat.search(first_line):
            return first_line[:50]
    return None


def _score_awkwardness(text: str, spec: ToneSpec) -> tuple[float, list[str]]:
    """
    어색함 휴리스틱 점수화. 0.0~1.0 클립.

    6개 룰의 가중치 합산:
      - 동일 어절 3회 이상           : 0.20
      - 미완결 종결                   : 0.15
      - 톤 라벨 누설                  : 0.20
      - 마크다운 잔존                 : 0.10
      - 프롬프트 표현 누설             : 0.15
      - 톤-내용 미스매치 (HIGH)        : 0.20
    """
    score = 0.0
    flags: list[str] = []

    if not text:
        return 0.0, flags

    # Rule 1: 동일 어절 3회 이상 반복
    words = re.findall(r"\b[\w가-힣]{2,}\b", text)
    if words:
        from collections import Counter
        word_counts = Counter(words)
        repeated = [w for w, c in word_counts.items() if c >= 3]
        if repeated:
            score += 0.20
            flags.append(f"repeated_word:{repeated[0]}")

    # Rule 2: 미완결 종결 (마지막 어절이 조사/접속어로 끝남)
    last_token = text.strip().split()[-1] if text.strip().split() else ""
    # 끝 구두점/이모지/해시태그 제외
    last_clean = re.sub(r"[.!?…」』#\s]+$", "", last_token)
    last_clean = _EMOJI_PATTERN.sub("", last_clean)
    if last_clean and _INCOMPLETE_TRAILING_PATTERN.search(last_clean):
        score += 0.15
        flags.append("incomplete_trailing")

    # Rule 3: 톤 라벨이 본문에 등장
    if spec.tone_name and spec.tone_name in text:
        score += 0.20
        flags.append("tone_name_leak")

    # Rule 4: 마크다운 잔존
    for pat in _MARKDOWN_RESIDUAL_PATTERNS:
        if pat.search(text):
            score += 0.10
            flags.append("markdown_residual")
            break

    # Rule 5: 프롬프트 표현 누설 (본문 중간/끝에)
    middle_leak_phrases = (
        "위 데이터", "다음과 같이", "위와 같이", "조건에 맞게",
        "트윗 본문", "지시한 대로",
    )
    for phr in middle_leak_phrases:
        if phr in text:
            score += 0.15
            flags.append(f"prompt_phrase_leak:{phr}")
            break

    # Rule 6: 톤-내용 미스매치 (HIGH 리스크에서 캐주얼/유머)
    if spec.risk_level == "HIGH":
        for token in _HIGH_RISK_INAPPROPRIATE:
            if token in text:
                score += 0.20
                flags.append(f"tone_mismatch:{token}")
                break

    return min(score, 1.0), flags
