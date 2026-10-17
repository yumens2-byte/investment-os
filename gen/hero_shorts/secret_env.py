"""
gen/hero_shorts/secret_env.py — 발행 식별값 단일 해석 지점 (보안 S1~S9)
================================================================================
2026-10-18 마스터 지시: 소스 하드코딩을 전량 삭제하고 시크릿/환경변수로만 주입한다.

원칙
    S1 단일 해석 지점 — 발행 식별값은 이 모듈 밖에서 기본값을 갖지 않는다.
    S2 우선순위 — CLI 인자 > 환경변수(GitHub Secrets). 소스 기본값 없음.
    S3 fail-closed — 미설정/형식위반은 즉시 예외. 외부 호출 전에 차단한다.
    S4 누출 방지 — 값은 로그·예외 메시지에 나가지 않는다(scrub/redact). 오류에는 변수명만.
    S5 형식 검증 — 공백·개행·제어문자·플레이스홀더·과길이 거부. 선택적 엄격모드(24 hex).

이 모듈은 어떤 값도 절대 출력하지 않는다. 값의 존재·길이·형식만 보고한다.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

__all__ = [
    "SecretMissingError",
    "SecretInvalidError",
    "ACCOUNT_ENV_BY_PLATFORM",
    "GENERIC_ACCOUNT_ENV",
    "EMAIL_ENVS",
    "resolve_account_id",
    "resolve_email",
    "resolve_required",
    "validate_secret_value",
    "redact",
    "scrub",
    "register_secret",
    "preflight_report",
]

# ── 예외 ────────────────────────────────────────────────────────────
class SecretMissingError(RuntimeError):
    """시크릿/환경변수 미설정 — fail-closed(S3)."""


class SecretInvalidError(ValueError):
    """시크릿 값이 형식 검증을 통과하지 못함(S5)."""


# ── 이름 정의 (S1) ──────────────────────────────────────────────────
ACCOUNT_ENV_BY_PLATFORM = {
    "youtube": "HERO_YOUTUBE_ACCOUNT_ID",
    "threads": "HERO_THREADS_ACCOUNT_ID",
    "instagram": "HERO_ZERNIO_ACCOUNT_ID",
    "tiktok": "HERO_TIKTOK_ACCOUNT_ID",
}
GENERIC_ACCOUNT_ENV = "HERO_ZERNIO_ACCOUNT_ID"
EMAIL_ENVS = ("HERO_ALERT_EMAIL", "HERO_FROM_ACCOUNT")

# ── 형식 검증기 (S5) ────────────────────────────────────────────────
_STRICT_HEX24_RE = re.compile(r"^[0-9a-f]{24}$")
_LOOSE_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.:@-]{4,128}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(changeme|change_me|placeholder|todo|tbd|none|null|nil|example|dummy|sample|"
    r"xxx+|x{4,}|\*+|redacted|secret|password|your[-_].*|test[-_]?value|<.*>)$"
)
DEFAULT_MAX_LEN = 256

# S4 — 해석된 값은 전역 등록되어 scrub() 이 예외 메시지에서 제거한다.
_KNOWN_VALUES: set = set()


def register_secret(value: Optional[str]):
    """해석된 시크릿 값을 누출 방지 목록에 등록한다."""
    if isinstance(value, str) and len(value) >= 4:
        _KNOWN_VALUES.add(value)
    return value


def redact(value: Optional[str]) -> str:
    """로그용 마스킹 — 값 대신 설정 여부·길이만 노출."""
    if not value:
        return "<미설정>"
    return f"<설정됨:{len(str(value))}자>"


def scrub(text: Any) -> str:
    """문자열에서 등록된 시크릿 값을 제거한다(예외 메시지·로그용)."""
    out = "" if text is None else str(text)
    for value in sorted(_KNOWN_VALUES, key=len, reverse=True):
        if value and value in out:
            out = out.replace(value, "***")
    return out


def validate_secret_value(name: str, value: Any, strict: bool = False,
                          pattern: Optional[re.Pattern] = None,
                          max_len: int = DEFAULT_MAX_LEN) -> str:
    """S5 형식 검증. 실패 시 값이 아닌 변수명만 오류에 담는다."""
    if value is None:
        raise SecretMissingError(f"{name} 미설정")
    raw = str(value)
    if not raw.strip():
        raise SecretMissingError(f"{name} 빈 값")
    if raw != raw.strip():
        raise SecretInvalidError(f"{name} 값 앞뒤 공백/개행 불가")
    if len(raw) > max_len:
        raise SecretInvalidError(f"{name} 값 길이 초과(max {max_len}자)")
    if _CONTROL_RE.search(raw):
        raise SecretInvalidError(f"{name} 값에 제어문자 포함")
    if _PLACEHOLDER_RE.match(raw):
        raise SecretInvalidError(f"{name} 플레이스홀더 값 거부")
    effective = (pattern or _STRICT_HEX24_RE) if strict else None
    if effective is not None and not effective.match(raw):
        raise SecretInvalidError(f"{name} 형식 불일치")
    return raw


def resolve_required(name: str, cli_value: Optional[str] = None, strict: bool = False,
                     pattern: Optional[re.Pattern] = None, required: bool = True,
                     max_len: int = DEFAULT_MAX_LEN) -> Optional[str]:
    """CLI 인자 > 환경변수(S2). 소스 기본값 없음(S1)."""
    value = cli_value if cli_value not in (None, "") else os.getenv(name)
    if value in (None, ""):
        if required:
            raise SecretMissingError(
                f"{name} 미설정 — 소스 기본값은 2026-10-18 제거되었습니다. "
                "시크릿(GitHub Secrets) 또는 환경변수로 주입하세요"
            )
        return None
    return register_secret(validate_secret_value(name, value, strict=strict,
                                                 pattern=pattern, max_len=max_len))


def resolve_account_id(platform: str, cli_value: Optional[str] = None,
                       required: bool = False, strict: Optional[bool] = None) -> Optional[str]:
    """플랫폼별 계정 ID 해석. 엄격모드는 HERO_SECRET_STRICT=1 로 켠다(기본 off, S5)."""
    key = str(platform or "").strip().lower()
    name = ACCOUNT_ENV_BY_PLATFORM.get(key)
    if not name:
        raise SecretInvalidError(
            f"미지원 플랫폼 {platform!r} — 지원: {sorted(ACCOUNT_ENV_BY_PLATFORM)}"
        )
    if strict is None:
        strict = os.getenv("HERO_SECRET_STRICT") == "1"
    return resolve_required(name, cli_value=cli_value, strict=strict,
                            pattern=_LOOSE_ACCOUNT_RE, required=required)


def resolve_email(name: str, cli_value: Optional[str] = None,
                  required: bool = False) -> Optional[str]:
    """알림 주소 해석 — 소스에 개인 메일 하드코딩 금지(공개 저장소 PII)."""
    return resolve_required(name, cli_value=cli_value, strict=True,
                            pattern=_EMAIL_RE, required=required)


def preflight_report(platform: str = "youtube") -> Dict[str, Any]:
    """설정 점검 리포트 — 값은 절대 담지 않는다(S4). 워크플로 로그용."""
    key = str(platform or "").strip().lower()
    name = ACCOUNT_ENV_BY_PLATFORM.get(key)
    raw = os.getenv(name) if name else None
    ok, reason = False, "미설정"
    if raw:
        try:
            validate_secret_value(name, raw)
            ok, reason = True, "형식 OK"
        except (SecretMissingError, SecretInvalidError) as exc:
            ok, reason = False, type(exc).__name__
    return {
        "platform": key,
        "env": name,
        "configured": bool(raw),
        "valid": ok,
        "length": len(raw) if raw else 0,
        "reason": reason,
        "value_logged": False,
    }


if __name__ == "__main__":  # 로컬 점검 — 값 미출력
    import json as _json
    import sys as _sys
    _platform = _sys.argv[1] if len(_sys.argv) > 1 else "youtube"
    print(_json.dumps(preflight_report(_platform), ensure_ascii=False))
