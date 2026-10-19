"""
gen/nuna_weekly/policy.py — 콘텐츠 정책 단일 출처 (설계서 9장 Q4·Q5·Q6, 15장 S-07·S-11)
=====================================================================================
script(생성 시 검증)와 qc(발행 전 검증)가 같은 규칙을 쓰도록 여기 한 곳에 둔다.

    find_forbidden(text)        — 투자 권유·확정 전망 표현 (단어 경계·구 단위, hero G1 부분문자열 오탐 개선)
    find_links(text)            — URL·@멘션 (프롬프트 인젝션 산출물 차단)
    has_disclaimer(text)        — 면책 문구 포함 여부
    extract_numbers(text)       — 숫자 토큰 추출 (부호·소수·천단위·%)
    unsupported_numbers(text, allowed, date_numbers) — 근거 없는 숫자 목록
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence

from .config import DISCLAIMER

VERSION = "1.0.0"

# 권유·확정 표현 — 한국어는 어미 포함 구, 영어는 단어 경계 구
FORBIDDEN_PATTERNS: Sequence[re.Pattern] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"매수\s*하세요", r"매도\s*하세요", r"사세요", r"파세요", r"사야\s*합니다", r"팔아야\s*합니다",
    r"매수\s*추천", r"매도\s*추천", r"무조건", r"반드시\s*오릅", r"반드시\s*내립", r"확실히\s*오릅",
    r"확실히\s*내립", r"수익\s*보장", r"원금\s*보장", r"보장합니다", r"100%\s*(상승|하락|수익)",
    r"\bbuy\s+now\b", r"\bsell\s+now\b", r"\bguaranteed\b", r"\bmust\s+buy\b", r"\bstrong\s+buy\b",
    r"\bto\s+the\s+moon\b",
))
_LINK_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|net|io|kr|co)\b|(?<![\w])@[A-Za-z0-9_]{2,})", re.IGNORECASE)
_NUM_RE = re.compile(r"(?<![\w.])[-+−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])[-+−]?\d+(?:\.\d+)?")
_DATE_CTX_RE = re.compile(r"^\s*(월|일|요일|주|번째|거래일|째)")


def find_forbidden(text: str) -> list[str]:
    return [m.group(0) for p in FORBIDDEN_PATTERNS for m in p.finditer(text or "")]


def find_links(text: str) -> list[str]:
    return [m.group(0) for m in _LINK_RE.finditer(text or "")]


def has_disclaimer(text: str) -> bool:
    return DISCLAIMER.replace(" ", "") in (text or "").replace(" ", "")


def _to_float(tok: str) -> float:
    return float(tok.replace(",", "").replace("−", "-"))


def extract_numbers(text: str) -> list[tuple]:
    """[(token, value, following_text)]"""
    out = []
    for m in _NUM_RE.finditer(text or ""):
        out.append((m.group(0), _to_float(m.group(0)), (text or "")[m.end():m.end() + 4]))
    return out


def allowed_values(values: Iterable[float]) -> set[float]:
    """facts 수치 → 허용 집합 (원값·절댓값·소수 0/1/2자리 반올림)."""
    out: set[float] = set()
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        fv = float(v)
        for x in (fv, abs(fv)):
            for nd in (0, 1, 2):
                out.add(round(x, nd))
    return out


def _matches(value: float, allowed: set[float]) -> bool:
    tol = 0.5 if abs(value) >= 100 else 0.051
    return any(abs(abs(value) - abs(a)) <= tol for a in allowed)


def unsupported_numbers(text: str, allowed: set[float], date_numbers: set[int]) -> list[str]:
    """근거 없는 숫자 토큰 목록. 날짜·순번 문맥(월/일/요일/주/째)의 정수는 date_numbers 로 검사."""
    bad: list[str] = []
    for tok, val, follow in extract_numbers(text):
        if _DATE_CTX_RE.match(follow):
            if not (val.is_integer() and int(val) in date_numbers):
                bad.append(tok)
            continue
        if not _matches(val, allowed):
            bad.append(tok)
    return bad
