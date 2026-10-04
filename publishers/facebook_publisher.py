"""
publishers/facebook_publisher.py
================================
Facebook 페이지 게시 모듈 (Graph API, 페이지 액세스 토큰) — FB-1, 2026-10-05.

공식 문서 기준
  - 텍스트 : POST {graph}/{page_id}/feed    message        → 응답 id
             (Pages API 'Posts' 가이드)
  - 이미지 : POST {graph}/{page_id}/photos  source(multipart) + caption → 응답 id, post_id
             (Graph API Page Photos 레퍼런스: message 는 "Deprecated. Please use the caption param")
  - 이미지 제한 : "Files can not exceed 10MB" → 초과 시 텍스트(/feed)로 대체
  - 80001 : 페이지 호출 과다(rate limit)

운영 원칙
  - DRY_RUN=true → HTTP 요청·파일 open 없이 미리보기 결과만 반환.
  - 쓰기 요청은 재시도하지 않는다(중복 게시 방지). DLQ 도 사용하지 않는다.
  - 결과 status
      ok       : 게시 확정 (post_id 수신)
      failed   : 게시되지 않음이 확정 (4xx 응답, 연결 수립 실패, 설정 누락)
      unknown  : 요청 전송 후 응답을 확정하지 못함 (읽기 타임아웃, 연결 끊김, 5xx)
                 → 호출부가 fb_history 에 unknown 으로 남겨 당일 재게시를 막는다.
  - 토큰은 로그·결과에 남기지 않는다(_redact).
  - 이 모듈은 예외를 밖으로 던지지 않는다 — X·Telegram 경로에 영향 없음.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from config.settings import (
    DRY_RUN,
    FACE_GRAPH_BASE,
    FACE_HTTP_TIMEOUT_SEC,
    FACE_PAGE_ID,
    FACE_PAGE_TOKEN,
    FACE_PHOTO_MAX_BYTES,
)

VERSION = "1.0.0"

logger = logging.getLogger(__name__)

_MASK = "***"
_TOKEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(access_token|input_token)=[^&\s'\"<>]+"), rf"\1={_MASK}"),
    (re.compile(r"(?i)(\"?access_token\"?\s*:\s*\")[^\"]+"), rf"\1{_MASK}"),
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), f"EAA{_MASK}"),
)


def _redact(text: Any) -> str:
    s = str(text)
    if FACE_PAGE_TOKEN and len(FACE_PAGE_TOKEN) >= 8:
        s = s.replace(FACE_PAGE_TOKEN, _MASK)
    for pattern, repl in _TOKEN_PATTERNS:
        s = pattern.sub(repl, s)
    return s


def _result(
    status: str,
    kind: str = "",
    post_id: str = "",
    reason: str = "",
    error_code: int | None = None,
    dry_run: bool = False,
) -> dict:
    return {
        "success":    status in ("ok", "dry_run"),
        "status":     status,
        "kind":       kind,
        "post_id":    post_id,
        "reason":     reason,
        "error_code": error_code,
        "dry_run":    dry_run,
    }


def _classify_error(code: int | None, http_status: int) -> str:
    if code == 190 or http_status == 401:
        return "auth_error"
    if code == 200:
        return "permission_error"
    if code == 80001:
        return "rate_limited"
    return "api_error"


def _resolve_kind(image_path: str | None) -> tuple[str, str]:
    """(kind, 사유). kind: photo | feed."""
    if not image_path:
        return "feed", "no_image"
    if not os.path.isfile(image_path):
        return "feed", "image_missing"
    size = os.path.getsize(image_path)
    if size > FACE_PHOTO_MAX_BYTES:
        return "feed", f"image_too_large:{size}"
    return "photo", ""


def _parse_error(resp: requests.Response) -> tuple[int | None, str]:
    try:
        err = resp.json().get("error", {}) or {}
        return err.get("code"), str(err.get("message", ""))[:200]
    except ValueError:
        return None, (resp.text or "")[:200]


def publish_page_post(text: str, image_path: str | None = None, session: str = "") -> dict:
    """
    Facebook 페이지에 게시한다.

    image_path 가 유효하고 10MB 이하면 /photos(이미지+캡션), 그 외는 /feed(텍스트).
    반환 dict: success, status(ok|failed|unknown|dry_run), kind, post_id, reason, error_code, dry_run
    """
    if not text or not text.strip():
        return _result("failed", reason="empty_text")

    kind, kind_reason = _resolve_kind(image_path)
    if kind_reason and kind_reason != "no_image":
        logger.warning(f"[FBPublisher] 이미지 사용 불가({kind_reason}) — 텍스트로 게시")

    logger.info(
        f"[FBPublisher] v{VERSION} {'[DRY RUN] ' if DRY_RUN else ''}게시 시작 "
        f"(session={session or '-'}, kind={kind}, {len(text)}자)"
    )

    if DRY_RUN:
        logger.info(f"[FBPublisher][DRY_RUN] 게시 생략 — 미리보기:\n{text}")
        return _result("dry_run", kind=kind, dry_run=True)

    if not FACE_PAGE_ID or not FACE_PAGE_TOKEN:
        logger.warning("[FBPublisher] FACE_PAGE_ID / FACE_PAGE_TOKEN 미설정 — 게시 건너뜀")
        return _result("failed", kind=kind, reason="not_configured")

    url = f"{FACE_GRAPH_BASE}/{FACE_PAGE_ID}/{'photos' if kind == 'photo' else 'feed'}"

    try:
        if kind == "photo":
            with open(image_path, "rb") as fh:
                resp = requests.post(
                    url,
                    data={"caption": text, "access_token": FACE_PAGE_TOKEN},
                    files={"source": fh},
                    timeout=FACE_HTTP_TIMEOUT_SEC,
                )
        else:
            resp = requests.post(
                url,
                data={"message": text, "access_token": FACE_PAGE_TOKEN},
                timeout=FACE_HTTP_TIMEOUT_SEC,
            )
    except requests.exceptions.ConnectTimeout as e:
        # 연결 수립 전 실패 → 요청이 전송되지 않았음이 확정
        logger.error(f"[FBPublisher] 연결 실패(미전송): {_redact(e)}")
        return _result("failed", kind=kind, reason="connect_timeout")
    except requests.exceptions.RequestException as e:
        # 전송 후 응답 미확정(읽기 타임아웃·연결 끊김 등) → 게시됐을 수 있음
        logger.error(f"[FBPublisher] 응답 미확정 — 게시 여부 확인 필요: {_redact(e)}")
        return _result("unknown", kind=kind, reason=f"request_error:{type(e).__name__}")
    except OSError as e:
        logger.error(f"[FBPublisher] 이미지 파일 읽기 실패: {_redact(e)}")
        return _result("failed", kind=kind, reason="image_read_error")

    if resp.status_code == 200:
        try:
            body = resp.json()
        except ValueError:
            logger.error("[FBPublisher] 200 응답 JSON 파싱 실패 — 게시 여부 확인 필요")
            return _result("unknown", kind=kind, reason="invalid_json")
        post_id = str(body.get("post_id") or body.get("id") or "")
        if not post_id:
            logger.error(f"[FBPublisher] 200 응답에 id 없음 — 확인 필요: {_redact(body)}")
            return _result("unknown", kind=kind, reason="no_post_id")
        logger.info(f"[FBPublisher] 게시 완료: kind={kind} post_id={post_id}")
        return _result("ok", kind=kind, post_id=post_id)

    code, message = _parse_error(resp)
    if resp.status_code >= 500:
        logger.error(
            f"[FBPublisher] 서버 오류 {resp.status_code} (code={code}) — 게시 여부 확인 필요: "
            f"{_redact(message)}"
        )
        return _result("unknown", kind=kind, reason=f"http_{resp.status_code}", error_code=code)

    reason = _classify_error(code, resp.status_code)
    logger.error(
        f"[FBPublisher] 게시 실패 {resp.status_code} {reason} (code={code}): {_redact(message)}"
    )
    return _result("failed", kind=kind, reason=reason, error_code=code)
