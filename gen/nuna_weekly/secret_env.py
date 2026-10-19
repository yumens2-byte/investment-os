"""
gen/nuna_weekly/secret_env.py — 비밀값 단일 해석·누출 방지 (보안 15장 S-01·S-03)
==================================================================================
gen/hero_shorts/secret_env.py(S1~S5)의 원칙을 이식하고, 15.3 마스킹 규칙을 추가했다.

    S1 단일 해석 지점 — 비밀값은 이 모듈에서만 환경변수로 읽는다. 소스 기본값 없음.
    S3 fail-closed  — 미설정·형식위반은 외부 호출 전에 예외.
    S4 누출 방지     — 값은 로그·예외에 나가지 않는다. 오류에는 변수명만.
    15.3            — Bearer/Key/sk_/AIza 패턴과 URL query string 을 *** 로 치환.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from typing import Any

VERSION = "1.0.0"


class SecretMissingError(RuntimeError):
    """비밀값 미설정 — fail-closed."""


class SecretInvalidError(ValueError):
    """비밀값 형식 위반."""


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(changeme|change_me|placeholder|todo|tbd|none|null|nil|example|dummy|sample|"
    r"xxx+|x{4,}|\*+|redacted|secret|password|your[-_].*|test[-_]?value|<.*>)$"
)
_MAX_LEN = 512

_KNOWN: set = set()

# 15.3 — 값 등록 여부와 무관하게 항상 지우는 패턴
_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)(\bkey\s+)[^\s\"']+"),
    re.compile(r"(sk_)[0-9A-Za-z]{16,}"),
    re.compile(r"(AIza)[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(https?://[^\s\"'?#]+)\?[^\s\"'#]*"),
    re.compile(r"(?i)(bot)\d{6,}:[0-9A-Za-z_\-]{20,}"),
)


def register(value: str | None) -> str | None:
    if isinstance(value, str) and len(value) >= 4:
        _KNOWN.add(value)
    return value


def redact(text: Any, limit: int | None = None) -> str:
    """로그·DB 저장 전 마스킹. limit 지정 시 마스킹 후 자른다."""
    out = "" if text is None else str(text)
    for v in sorted(_KNOWN, key=len, reverse=True):
        if v and v in out:
            out = out.replace(v, "***")
    for p in _PATTERNS:
        out = p.sub(lambda m: f"{m.group(1)}***", out)
    if limit is not None and len(out) > limit:
        out = out[:limit] + "…"
    return out


def describe(value: str | None) -> str:
    return "<미설정>" if not value else f"<설정됨:{len(value)}자>"


def _validate(name: str, raw: str) -> str:
    if raw != raw.strip():
        raise SecretInvalidError(f"{name} 앞뒤 공백·개행 불가")
    if len(raw) > _MAX_LEN:
        raise SecretInvalidError(f"{name} 길이 초과")
    if _CONTROL_RE.search(raw):
        raise SecretInvalidError(f"{name} 제어문자 포함")
    if _PLACEHOLDER_RE.match(raw):
        raise SecretInvalidError(f"{name} 플레이스홀더 값 거부")
    return raw


def require(name: str) -> str:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        raise SecretMissingError(f"{name} 미설정")
    return register(_validate(name, raw))  # type: ignore[return-value]


def optional(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return register(_validate(name, raw))


def require_all(names: Iterable[str]) -> None:
    """일괄 존재 확인 — 누락 이름만 모아 한 번에 보고한다."""
    missing = [n for n in names if not (os.environ.get(n) or "").strip()]
    if missing:
        raise SecretMissingError("미설정: " + ", ".join(missing))


class RedactingFilter(logging.Filter):
    """모든 로그 레코드의 메시지를 마스킹한다."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - 포맷 오류는 원문 유지
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


_NOISY_LOGGERS = ("httpx", "httpcore", "hpack", "urllib3", "anthropic",
                  "google_genai", "google.genai", "requests")


def setup_logging(level: int = logging.INFO) -> None:
    """루트 로거에 마스킹 필터 장착 + SDK 로거 WARNING 고정 (15.1 S-01)."""
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.setLevel(level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
