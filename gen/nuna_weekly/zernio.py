"""
gen/nuna_weekly/zernio.py — Zernio REST 클라이언트 (설계서 8장 외부연계, 15장 보안)
===================================================================================
gen/hero_shorts/publish_targets/transport.py(2026-10-18 docs.zernio.com 실측 스펙)의
로직을 이식했다. hero_shorts 는 import 하지 않는다.

    엔드포인트   base = https://zernio.com/api/v1  (ZERNIO_API_BASE 로 교체 가능)
    인증         Authorization: Bearer <key>  — key_alias main=ZERNIO_API_KEY, sub=ZERNIO_API_SUB_KEY
    업로드       POST /media/presign {filename,contentType,size} → {uploadUrl,publicUrl,key,expiresIn}
                 PUT uploadUrl (Content-Type 동일 · Authorization 없음)
    검증         POST /tools/validate/media {"url"} → {valid,size,contentType,platformLimits}
    발행         POST /posts (Idempotency-Key ≤255) — 207 = 부분실패(2xx 이지만 실패)
    조회         GET /posts/{id} · GET /accounts/health  (hero monitor/zernio_publish_watch.py)

보안:
    uploadUrl 은 1시간짜리 쓰기 자격증명 — 반환·로그 금지. 키·계정 ID·query string 로그 금지.
    미디어는 sha256[:12] 로만 식별해 로그에 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import config, secret_env
from .models import TargetResult

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.zernio")

DEFAULT_API_BASE = "https://zernio.com/api/v1"
DEFAULT_TIMEOUT_SEC = 120
MAX_IDEMPOTENCY_KEY_LEN = 255
RETRY_AFTER_CAP_SEC = 60

# hero transport.py 종결 상태값 계승
TERMINAL_OK = ("published", "completed", "success")
TERMINAL_FAIL = ("failed", "error", "rejected", "cancelled", "partial", "idempotency_conflict")
SCHEDULED_STATES = ("scheduled", "pending")
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")

# hero STAGING_CONTENT_TYPES 중 이 파이프라인이 쓰는 영상·이미지 MIME 만 이식
STAGING_CONTENT_TYPES: dict[str, str] = {
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime", ".webm": "video/webm",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}
STAGING_MAX_BYTES = 5 * 1024 * 1024 * 1024


class ZernioError(RuntimeError):
    """Zernio 호출 실패 (메시지는 마스킹됨)."""


def _sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def guess_content_type(filename: str) -> str:
    mime = STAGING_CONTENT_TYPES.get(Path(filename).suffix.lower())
    if not mime:
        raise ZernioError(f"스테이징: 확장자를 스펙 MIME 로 해석 불가 — fail-closed: {Path(filename).suffix}")
    return mime


def _parse(text: str) -> dict[str, Any]:
    try:
        v = json.loads(text) if (text or "").strip() else {}
    except json.JSONDecodeError:
        return {"_unparsed": secret_env.redact(text, 500)}
    return v if isinstance(v, dict) else {"_list": v}


def error_code(body: dict[str, Any]) -> str:
    """응답 오류 코드 — 최상위 code 또는 error.code (스펙 미확정: 방어적 파싱)."""
    if not isinstance(body, dict):
        return ""
    code = body.get("code")
    err = body.get("error")
    if not code and isinstance(err, dict):
        code = err.get("code")
    return str(code or "").lower()


# --------------------------------------------------------------------------- #
# 응답 분해 (hero decompose_response 의미 계승)
# --------------------------------------------------------------------------- #
def extract_post(body: dict[str, Any]) -> dict[str, Any] | None:
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


def _platform_entry(post: dict[str, Any] | None, platform: str) -> dict[str, Any] | None:
    for item in (post or {}).get("platforms") or []:
        if isinstance(item, dict) and str(item.get("platform", "")).lower() == platform:
            return item
    return None


def _entry_url(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    return entry.get("platformPostUrl") or entry.get("postUrl") or entry.get("url")


def _entry_error(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    err = entry.get("error") or entry.get("errorMessage")
    return str(err) if err else None


def _host(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").lower()


def decompose(platform_list: Sequence[str], http_status: int, body: dict[str, Any],
              scheduled: bool | None = None) -> dict[str, TargetResult]:
    """응답 → {platform: TargetResult}. 성공은 플랫폼별 명시 엔트리로만 인정한다.

    · HTTP 207 = 부분실패. 4xx/5xx·집계 상태 실패와 함께 hard_fail.
    · 엔트리 status ∈ TERMINAL_OK → published (youtube 는 URL 호스트까지 확인).
    · 예약 발행(scheduled) 이면 실패 아닌 엔트리 status ∈ scheduled|pending → scheduled.
    · 엔트리 부재는 published/scheduled 로 승격하지 않는다.
    """
    post = extract_post(body)
    if scheduled is None:
        scheduled = bool((post or {}).get("scheduledFor"))
    aggregate = str((post or {}).get("status", "")).lower()
    http = int(http_status)
    partial = http == 207
    hard_fail = http >= 400 or partial or aggregate in TERMINAL_FAIL
    post_id = (post or {}).get("_id") or (post or {}).get("id")
    post_id = str(post_id) if post_id else None

    out: dict[str, TargetResult] = {}
    for platform in platform_list:
        entry = _platform_entry(post, platform)
        if entry is not None:
            st = str(entry.get("status", "")).lower()
            url = _entry_url(entry)
            err = _entry_error(entry)
            if st in TERMINAL_FAIL:
                status = "failed"
                err = err or f"status={st}"
            elif st in TERMINAL_OK:
                if platform == "youtube" and _host(url) not in YOUTUBE_HOSTS:
                    status = "pending"
                else:
                    status = "published"
            elif scheduled and st in SCHEDULED_STATES:
                status = "scheduled"
            else:
                status = "pending"
            out[platform] = TargetResult(platform, status, post_id,
                                         url if status == "published" else None,
                                         secret_env.redact(err, 500) if err else None)
            continue
        if hard_fail:
            detail = f"HTTP {http}"
            code = error_code(body)
            if code:
                detail += f" code={code}"
            existing = (body or {}).get("existingPostId") if isinstance(body, dict) else None
            if existing:
                detail += f" existingPostId={existing}"
            if partial:
                detail += " — 부분실패(207)"
            out[platform] = TargetResult(platform, "failed", post_id, None, secret_env.redact(detail, 500))
        else:
            out[platform] = TargetResult(platform, "pending", post_id, None, None)
    if partial:
        log.warning("[NunaWeekly:zernio] 부분실패(HTTP 207) — 명시 성공 엔트리만 통과: %s",
                    {k: v.status for k, v in out.items()})
    return out


# --------------------------------------------------------------------------- #
# REST 클라이언트
# --------------------------------------------------------------------------- #
class ZernioClient:
    def __init__(self, key_alias: str, session=None, base: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT_SEC,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if key_alias not in config.ZERNIO_KEY_ENV:
            raise ZernioError(f"알 수 없는 key_alias: {key_alias}")
        if session is None:
            import requests

            session = requests.Session()
        self.key_alias = key_alias
        self.s = session
        self.base = (base or os.environ.get("ZERNIO_API_BASE") or DEFAULT_API_BASE).strip().rstrip("/")
        self.timeout = timeout
        self._sleep = sleep
        self._key: str | None = None
        self.calls: list[tuple[str, str]] = []
        log.info("[NunaWeekly:zernio] v%s 시작 — key_alias=%s", VERSION, key_alias)

    # 키는 첫 호출 시점에 해석한다 (S3 fail-closed)
    def _api_key(self) -> str:
        if self._key is None:
            self._key = secret_env.require(config.ZERNIO_KEY_ENV[self.key_alias])
        return self._key

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._api_key()}", "Accept": "application/json",
             "Content-Type": "application/json"}
        if extra:
            h.update(extra)
        return h

    def _request(self, method: str, path: str, body: Any = None,
                 extra_headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any], Any]:
        url = f"{self.base}{path}"
        headers = self._headers(extra_headers)   # 키 미설정은 SecretMissingError 로 그대로 올린다
        self.calls.append((method, path))
        try:
            r = self.s.request(method, url,
                               data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
                               headers=headers, timeout=self.timeout)
        except Exception as e:
            raise ZernioError(secret_env.redact(f"{method} {path} 호출 실패: {type(e).__name__}: {e}", 300)) from None
        status = int(getattr(r, "status_code", 0) or 0)
        parsed = _parse(getattr(r, "text", "") or "")
        if status >= 400:
            log.warning("[NunaWeekly:zernio] %s %s HTTP %s 본문: %s", method, path, status,
                        secret_env.redact(getattr(r, "text", ""), 500))
        return status, parsed, getattr(r, "headers", None) or {}

    # ── 업로드 ──
    def presign(self, filename: str, content_type: str, size: int) -> dict[str, Any]:
        """→ {upload_url, public_url, key, expires_in}. upload_url 은 호출부 밖으로 내보내지 않는다."""
        if not filename:
            raise ZernioError("presign: filename 필요")
        if content_type not in STAGING_CONTENT_TYPES.values():
            raise ZernioError(f"presign: contentType 미허용: {content_type}")
        size = int(size)
        if size <= 0 or size > STAGING_MAX_BYTES:
            raise ZernioError(f"presign: size 범위 오류: {size}")
        status, parsed, _ = self._request("POST", "/media/presign",
                                          {"filename": filename, "contentType": content_type, "size": size})
        if status >= 400:
            raise ZernioError(f"presign 거부 — HTTP {status} code={error_code(parsed) or '?'}")
        upload = parsed.get("uploadUrl") or parsed.get("upload_url")
        public = parsed.get("publicUrl") or parsed.get("public_url")
        if not upload or not public:
            raise ZernioError(f"presign 응답에 uploadUrl/publicUrl 없음 — keys={sorted(parsed)[:8]}")
        return {"upload_url": upload, "public_url": public, "key": parsed.get("key"),
                "expires_in": parsed.get("expiresIn") or parsed.get("expires_in")}

    def put_file(self, upload_url: str, path: str | Path, content_type: str) -> int:
        """서명 URL 로 PUT — Authorization 헤더를 붙이지 않는다."""
        p = Path(path)
        self.calls.append(("PUT", "<upload_url>"))
        try:
            with p.open("rb") as fh:
                r = self.s.request("PUT", upload_url, data=fh,
                                   headers={"Content-Type": content_type, "Accept": "*/*"},
                                   timeout=self.timeout)
        except Exception as e:
            raise ZernioError(secret_env.redact(f"PUT 업로드 실패: {type(e).__name__}: {e}", 300)) from None
        status = int(getattr(r, "status_code", 0) or 0)
        if not 200 <= status < 300:
            raise ZernioError(f"PUT 업로드 거부 — HTTP {status}")
        return status

    def stage_file(self, path: str | Path) -> dict[str, Any]:
        """존재·크기·MIME 검증 → presign → PUT. upload_url 은 반환하지 않는다."""
        p = Path(path)
        if not p.is_file():
            raise ZernioError(f"스테이징 대상 파일 없음: {p.name}")
        size = p.stat().st_size
        if size <= 0 or size > STAGING_MAX_BYTES:
            raise ZernioError(f"스테이징 크기 오류: {size}B")
        ctype = guess_content_type(p.name)
        digest = _sha256_of(p)
        pre = self.presign(p.name, ctype, size)
        put_status = self.put_file(pre["upload_url"], p, ctype)
        log.info("[NunaWeekly:zernio] 스테이징 완료: sha256=%s %dB PUT HTTP %s", digest[:12], size, put_status)
        return {"public_url": pre["public_url"], "sha256": digest, "size": size, "key": pre["key"]}

    def validate_media(self, url: str) -> dict[str, Any]:
        status, parsed, _ = self._request("POST", "/tools/validate/media", {"url": str(url)})
        return {"http_status": status, "valid": parsed.get("valid") is True, "body": parsed}

    # ── 발행 ──
    def create_post(self, body: dict[str, Any], idempotency_key: str) -> tuple[int, dict[str, Any]]:
        if not idempotency_key or not str(idempotency_key).strip():
            raise ZernioError("create_post: Idempotency-Key 필수")
        key = str(idempotency_key)
        if len(key) > MAX_IDEMPOTENCY_KEY_LEN:
            raise ZernioError(f"Idempotency-Key {len(key)}자 > {MAX_IDEMPOTENCY_KEY_LEN}자")
        if body.get("publishNow"):
            raise ZernioError("publishNow 금지 — scheduledFor 예약 발행만 허용")
        hdr = {"Idempotency-Key": key}
        status, parsed, headers = self._request("POST", "/posts", body, hdr)
        if status == 409 and error_code(parsed) == "idempotency_conflict":
            wait = _retry_after(headers)
            log.warning("[NunaWeekly:zernio] 409 idempotency_conflict — %ss 후 1회 재시도", wait)
            self._sleep(wait)
            status, parsed, _ = self._request("POST", "/posts", body, hdr)
        log.info("[NunaWeekly:zernio] POST /posts HTTP %s", status)
        return status, parsed

    def get_post(self, post_id: str) -> dict[str, Any]:
        status, parsed, _ = self._request("GET", f"/posts/{post_id}")
        if status >= 400:
            raise ZernioError(f"GET /posts/{{id}} HTTP {status}")
        return parsed

    def accounts_health(self) -> dict[str, Any]:
        status, parsed, _ = self._request("GET", "/accounts/health")
        if status >= 400:
            raise ZernioError(f"GET /accounts/health HTTP {status}")
        return parsed


def _retry_after(headers: Any) -> float:
    raw = None
    try:
        raw = headers.get("Retry-After") if headers else None
    except Exception:
        raw = None
    try:
        sec = float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        sec = 1.0
    return max(0.0, min(sec, float(RETRY_AFTER_CAP_SEC)))


# --------------------------------------------------------------------------- #
# 더미 — 테스트·DRY_RUN. 네트워크 없음, 결정적.
# --------------------------------------------------------------------------- #
class DummyZernioClient:
    def __init__(self, key_alias: str = "main", post_status: str = "scheduled",
                 get_status: str = "published", http_status: int = 200,
                 health: dict[str, Any] | None = None) -> None:
        self.key_alias = key_alias
        self.post_status = post_status
        self.get_status = get_status
        self.http_status = http_status
        self._health = health
        self.calls: list[tuple[str, Any]] = []
        self._posts: dict[str, list[str]] = {}

    def presign(self, filename, content_type, size):
        self.calls.append(("presign", filename))
        return {"upload_url": f"https://upload.invalid/{filename}?sig=x",
                "public_url": f"https://media.invalid/{self.key_alias}/{filename}", "key": filename,
                "expires_in": 3600}

    def put_file(self, upload_url, path, content_type):
        self.calls.append(("put_file", Path(path).name))
        return 200

    def stage_file(self, path):
        p = Path(path)
        self.calls.append(("stage_file", p.name))
        digest = _sha256_of(p) if p.is_file() else hashlib.sha256(p.name.encode()).hexdigest()
        return {"public_url": f"https://media.invalid/{self.key_alias}/{p.name}", "sha256": digest,
                "size": p.stat().st_size if p.is_file() else 0, "key": p.name}

    def validate_media(self, url):
        self.calls.append(("validate_media", url))
        return {"http_status": 200, "valid": True, "body": {"valid": True}}

    def create_post(self, body, idempotency_key):
        if not idempotency_key:
            raise ZernioError("create_post: Idempotency-Key 필수")
        self.calls.append(("create_post", idempotency_key, body))
        pid = "dummy_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
        plats = [e["platform"] for e in body.get("platforms", [])]
        self._posts[pid] = plats
        if self.http_status >= 400:
            return self.http_status, {"error": {"code": "dummy_error", "message": "dummy"}}
        return self.http_status, {"post": {"_id": pid, "status": self.post_status,
                                           "scheduledFor": body.get("scheduledFor"),
                                           "platforms": [{"platform": p, "status": self.post_status}
                                                         for p in plats]}}

    def get_post(self, post_id):
        self.calls.append(("get_post", post_id))
        plats = self._posts.get(post_id, list(config.PLATFORMS))
        entries = []
        for p in plats:
            e: dict[str, Any] = {"platform": p, "status": self.get_status}
            if self.get_status in TERMINAL_OK:
                e["platformPostUrl"] = (f"https://youtube.com/shorts/{post_id}" if p == "youtube"
                                        else f"https://{p}.invalid/{post_id}")
            entries.append(e)
        return {"post": {"_id": post_id, "status": self.get_status, "platforms": entries}}

    def accounts_health(self):
        self.calls.append(("accounts_health", None))
        if self._health is not None:
            return self._health
        return {"accounts": [{"platform": p, "status": "healthy", "canPost": True, "needsReconnect": False}
                             for p in config.PLATFORMS]}
