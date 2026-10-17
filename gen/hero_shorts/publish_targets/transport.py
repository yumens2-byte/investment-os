"""
gen/hero_shorts/publish_targets/transport.py — 전송 어댑터 (경로 A)
====================================================================
설계서 §2.3 · §6.1 · §6.2 · W7.

전송 수단은 **이 파일 하나에만** 존재한다. 타깃 계층은 transport 를 주입받아
쓰기만 하고, 경로 A/B 교체는 여기서만 일어난다(설계서 §2.1-3).

구현 2종:
    ZernioCliTransport   — 현행 `gsk zernio create_post` 래퍼.
                           F1 실측: Facebook·Instagram·TikTok 만 지원 →
                           유튜브·쓰레드는 **명시적으로 거부**한다(검증 전용).
    ZernioRestTransport  — 경로 A. `POST {base}/posts` (Zernio REST).
                           유튜브·쓰레드를 단일 호출로 함께 발송한다.

## 최우선 안전 항목 (설계서 §6.2 · 리스크 "높음")
Zernio 는 `publishNow: true` 거부를 **HTTP 207** 로 돌려주고, 이때
`fetch().ok` 가 `true` 다. returncode/HTTP 상태만 보면 **실패를 성공으로 기록**한다.
따라서 `decompose_response()` 는 다음을 반드시 지킨다.

    1. HTTP 207 은 그 자체로 부분실패 신호다.
    2. 어떤 타깃이든 **명시적 성공 엔트리**(platforms[] 안의 status ∈ TERMINAL_OK)
       가 없으면 published 로 판정하지 않는다.
    3. 유튜브는 URL 호스트까지 확인한다(youtube.com/youtu.be) — 다른 플랫폼의
       성공을 유튜브 성공으로 승계하지 않는다.

## 미확인 (설계서 §13-2)
Zernio REST 인증 헤더·정확한 엔드포인트 경로는 1차 스펙 미열람 상태다.
`ZERNIO_API_BASE` 환경변수로 교체 가능하게 두고, 값은 실측 1건 확보 후 확정한다.
추정으로 확정하지 않는다 — 본 모듈의 외부 호출은 fail-closed 로만 동작한다.
"""
from __future__ import annotations

import json
import logging
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .base import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PUBLISHED,
)

log = logging.getLogger("hero_shorts.publish_targets.transport")

VERSION = "1.0.0"

# F1 — `gsk zernio create_post` 가 받는 플랫폼. 이 밖의 값은 CLI 경로에서 거부한다.
CLI_SUPPORTED_PLATFORMS = ("facebook", "instagram", "tiktok")

# Zernio 응답의 종결 상태값(publish_runtime 관례 계승).
TERMINAL_OK = ("published", "completed", "success")
TERMINAL_FAIL = ("failed", "error", "rejected", "cancelled")

# 유튜브 성공 판정에 허용하는 호스트(설계서 §4.3 · publish_runtime 규칙 계승).
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")

DEFAULT_API_BASE = "https://zernio.com/api/v1"
DEFAULT_TIMEOUT_SEC = 120
MAX_RETRY = 3


class TransportError(RuntimeError):
    """전송 계층 오류 — 호출부가 재시도 여부를 판단한다."""


class UnsupportedPlatformError(TransportError):
    """해당 transport 가 지원하지 않는 플랫폼(F1)."""


class PartialFailureError(TransportError):
    """HTTP 207 또는 전건 실패 — 부분실패를 성공으로 승격하지 않는다."""

    def __init__(self, message: str, response: "TransportResponse") -> None:
        super().__init__(message)
        self.response = response


# --------------------------------------------------------------------------- #
# 응답 봉투
# --------------------------------------------------------------------------- #
@dataclass
class TransportResponse:
    """전송 1회의 결과 봉투. 판정은 decompose_response 가 담당한다."""

    transport: str
    http_status: int
    body: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    request: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok_envelope(self) -> bool:
        """HTTP 수준 성공 여부. **이 값만으로 발행 성공을 판정하지 않는다.**"""
        return 200 <= int(self.http_status) < 300

    @property
    def partial_failure(self) -> bool:
        """설계서 §6.2 함정 — 207 은 2xx 이지만 실패를 포함한다."""
        return int(self.http_status) == 207


# --------------------------------------------------------------------------- #
# 응답 파싱 (플랫폼별 결과 분해)
# --------------------------------------------------------------------------- #
def extract_post(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """응답 본문에서 게시물 객체를 꺼낸다. 없으면 None(성공 판정 불가)."""
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("post"), dict):
            return data["post"]
        if isinstance(data.get("posts"), list) and data["posts"]:
            return data["posts"][0]
    if isinstance(body.get("post"), dict):
        return body["post"]
    return None


def platform_entry(post: Dict[str, Any], platform: str) -> Optional[Dict[str, Any]]:
    for item in (post or {}).get("platforms") or []:
        if str(item.get("platform", "")).lower() == platform:
            return item
    return None


def entry_url(entry: Optional[Dict[str, Any]]) -> Optional[str]:
    if not entry:
        return None
    return entry.get("platformPostUrl") or entry.get("postUrl") or entry.get("url")


def entry_error(entry: Optional[Dict[str, Any]]) -> Optional[str]:
    if not entry:
        return None
    err = entry.get("error") or entry.get("errorMessage")
    return str(err) if err else None


def _host_of(url: Optional[str]) -> str:
    from urllib.parse import urlparse

    return (urlparse(url or "").hostname or "").lower()


def _judge_entry(platform: str, entry: Dict[str, Any]) -> str:
    """platforms[] 엔트리 1건 → STATUS_* 판정."""
    status = str(entry.get("status", "")).lower()
    url = entry_url(entry)
    if status in TERMINAL_FAIL:
        return STATUS_FAILED
    if status in TERMINAL_OK:
        if platform == "youtube":
            # 유튜브는 URL 호스트까지 확인한다 — 다른 플랫폼 성공을 승계하지 않는다.
            return STATUS_PUBLISHED if _host_of(url) in YOUTUBE_HOSTS else STATUS_PENDING
        return STATUS_PUBLISHED
    return STATUS_PENDING


def decompose_response(response: TransportResponse,
                       platforms: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """전송 응답 → {platform: {"outcome","url","error","post_id","explicit"}}.

    **성공은 명시적 엔트리로만 인정한다**(설계서 §6.2 · 리스크 "높음").
    HTTP 207 · 전건 실패 · 엔트리 부재는 published 로 승격되지 않는다.
    """
    post = extract_post(response.body)
    aggregate = str((post or {}).get("status", "")).lower()
    http = int(response.http_status)
    hard_fail = (http >= 400) or response.partial_failure or (aggregate in TERMINAL_FAIL)

    out: Dict[str, Dict[str, Any]] = {}
    for platform in platforms:
        entry = platform_entry(post, platform) if post else None
        if entry is not None:
            outcome = _judge_entry(platform, entry)
            out[platform] = {
                "outcome": outcome,
                "url": entry_url(entry),
                "error": entry_error(entry),
                "post_id": (post or {}).get("_id") or (post or {}).get("id"),
                "explicit": True,
            }
            continue
        # 엔트리 부재 — 절대 published 로 승격하지 않는다.
        out[platform] = {
            "outcome": STATUS_FAILED if hard_fail else STATUS_PENDING,
            "url": None,
            "error": (f"HTTP {http}" + (" — 부분실패(207)" if response.partial_failure else "")
                      if hard_fail else None),
            "post_id": (post or {}).get("_id") or (post or {}).get("id"),
            "explicit": False,
        }
    if response.partial_failure:
        log.warning("부분실패(HTTP 207) 감지 — 성공 엔트리만 통과시킨다: %s",
                    {k: v["outcome"] for k, v in out.items()})
    return out


def collect_errors(response: TransportResponse) -> List[str]:
    post = extract_post(response.body) or {}
    errors: List[str] = []
    for entry in post.get("platforms") or []:
        err = entry_error(entry)
        if err:
            errors.append(f"{entry.get('platform', 'unknown')}: {err}")
        st = str(entry.get("status", "")).lower()
        if st in TERMINAL_FAIL:
            errors.append(f"{entry.get('platform', 'unknown')}: status={st}")
    aggregate = str(post.get("status", "")).lower()
    if aggregate in TERMINAL_FAIL:
        errors.append(f"aggregate: status={aggregate}")
    if int(response.http_status) >= 400:
        errors.append(f"HTTP {response.http_status}: {response.raw[:200]}")
    return errors


# --------------------------------------------------------------------------- #
# 경로 A — Zernio REST
# --------------------------------------------------------------------------- #
class ZernioRestTransport:
    """경로 A. `POST {base}/posts` 로 유튜브·쓰레드를 함께 발송한다.

    설계서 §2.3: API 키는 `ZERNIO_API_KEY` 시크릿 1개를 요구한다.
    키 값은 어떤 경우에도 로그·원장·커밋에 남기지 않는다(루트 규칙 §1-3).
    """

    name = "zernio_rest"

    def __init__(self, api_key: Optional[str] = None,
                 base: Optional[str] = None,
                 timeout_sec: int = DEFAULT_TIMEOUT_SEC,
                 opener=None) -> None:
        self.base = (base or _env("ZERNIO_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self.timeout_sec = timeout_sec
        self._api_key = api_key
        self._opener = opener or urllib.request.urlopen

    # --- 시크릿은 호출 시점에 해석한다(테스트는 open() 직전까지 키를 요구하지 않음) ---
    @property
    def api_key(self) -> str:
        if self._api_key:
            return self._api_key
        from .. import secret_env as secrets

        return secrets.resolve_required("ZERNIO_API_KEY")

    def send(self, request_body: Dict[str, Any],
             platforms: Sequence[str] = ()) -> TransportResponse:
        """단일 호출로 전송한다. 실패는 예외가 아니라 응답 봉투로 돌려준다.

        네트워크/프로토콜 오류만 TransportError 로 올린다 — 호출부가 재시도한다.
        """
        url = f"{self.base}/posts"
        payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = int(getattr(resp, "status", 0) or 0)
        except urllib.error.HTTPError as exc:              # 4xx/5xx — 본문을 살린다
            raw = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
            status = int(getattr(exc, "code", 0) or 0)
        except Exception as exc:                            # 네트워크 계열
            raise TransportError(f"Zernio REST 호출 실패: {type(exc).__name__}: {exc}") from exc

        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"_unparsed": raw[:1000]}
        log.info("Zernio REST 응답: HTTP %s · 플랫폼 %s", status, list(platforms) or "-")
        return TransportResponse(transport=self.name, http_status=status,
                                 body=body, raw=raw, request=request_body)


# --------------------------------------------------------------------------- #
# 현행 — gsk zernio CLI (유튜브·쓰레드 미지원: F1)
# --------------------------------------------------------------------------- #
class ZernioCliTransport:
    """현행 자산 래퍼. **유튜브·쓰레드는 보내지 못한다** — 검증 전용으로 남긴다."""

    name = "zernio_cli"

    def __init__(self, runner=None, args_dir: str = "/tmp/opencode") -> None:
        self._runner = runner or _default_runner
        self.args_dir = args_dir

    def send(self, request_body: Dict[str, Any],
             platforms: Sequence[str] = ()) -> TransportResponse:
        unsupported = [p for p in platforms if p not in CLI_SUPPORTED_PLATFORMS]
        if unsupported:
            raise UnsupportedPlatformError(
                "gsk zernio create_post 는 유튜브·쓰레드를 지원하지 않는다(F1): "
                f"{unsupported} — 경로 A(ZernioRestTransport)를 사용하라")

        d = Path(self.args_dir)
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(d),
                                         prefix="zernio_create_post_",
                                         suffix=".json", delete=False) as tmp:
            args_file = Path(tmp.name)
            json.dump(request_body, tmp, ensure_ascii=False, indent=2)
        try:
            cp = self._runner(
                ["gsk", "zernio", "create_post", "자동발행", "--args-file", str(args_file), "-y"])
        finally:
            if args_file.exists():
                args_file.unlink()

        rc = getattr(cp, "returncode", 1)
        raw = (getattr(cp, "stdout", "") or "").strip()
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"_unparsed": raw[:1000]}
        status = 200 if rc == 0 else 400
        return TransportResponse(transport=self.name, http_status=status,
                                 body=body, raw=raw, request=request_body)


def _default_runner(args: List[str]) -> Any:
    return subprocess.run(args, capture_output=True, text=True)


def _env(key: str) -> Optional[str]:
    import os

    return os.environ.get(key) or None


# --------------------------------------------------------------------------- #
# 팩토리 · 헬퍼
# --------------------------------------------------------------------------- #
def make_transport(kind: str = "rest", **kwargs) -> Any:
    """`kind="rest"`(경로 A) | `"cli"`(현행)."""
    k = str(kind or "").strip().lower()
    if k in ("rest", "zernio_rest", "a"):
        return ZernioRestTransport(**kwargs)
    if k in ("cli", "zernio_cli"):
        return ZernioCliTransport(**kwargs)
    raise ValueError(f"알 수 없는 전송 방식: {kind!r} (rest|cli)")


def build_request(media_url: str, entries: Sequence[Dict[str, Any]],
                  *, publish_now: bool = True,
                  scheduled_at: Optional[str] = None) -> Dict[str, Any]:
    """REST 요청 본문 조립 — `platforms` 배열 + 플랫폼별 `platformSpecificData`.

    **미확인**: 필드명 대소문자·봉투 키는 Zernio REST 스펙 1차 열람 후 확정 대상이다.
    구조를 한 곳에 모아 두어 확정 시 수정 지점을 1곳으로 제한한다.
    """
    if not media_url:
        raise TransportError("media_url 없음 — 업로드 단계 선행 필요")
    if not entries:
        raise TransportError("platforms 비어 있음 — 전송할 타깃이 없다")
    body: Dict[str, Any] = {
        "mediaUrls": [media_url],
        "platforms": [dict(e) for e in entries],
        "publishNow": bool(publish_now),
    }
    if scheduled_at:
        body["scheduledAt"] = scheduled_at
        body["publishNow"] = False
    return body


def platform_count(body: Dict[str, Any]) -> int:
    return len(body.get("platforms") or [])


def retry_budget(existing_attempts: Iterable[Any], max_attempts: int = MAX_RETRY) -> int:
    """남은 재시도 횟수. 상한 3회(설계서 §6.3 기존 상수 계승)."""
    return max(0, int(max_attempts) - len(list(existing_attempts or [])))


def quoted(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)
