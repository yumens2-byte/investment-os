"""
core/fb_history.py
==================
Facebook 페이지 게시 이력 — 세션·KST 발행일 단위 중복 게시 차단 (FB-1, 2026-10-05).

배경
  - X 이력(history.json)은 X 발행 성공 시에만 기록된다(run_view Step 7).
    X 실패 후 재실행하면 Facebook 은 같은 글을 다시 올릴 수 있으므로
    Facebook 전용 이력을 분리한다.

판정 규칙
  - 같은 session + 같은 KST 발행일에 status ∈ {"ok", "unknown"} 레코드가 있으면 차단.
  - "unknown" = 쓰기 요청 후 타임아웃 등으로 게시 여부를 확정하지 못한 상태.
    게시됐을 수 있으므로 재게시하지 않는다(보수적 처리). 운영자가 페이지를 확인한 뒤
    필요하면 fb-history 캐시를 비우고 channel=face 로 재실행한다.

저장소
  data/published/fb_history.json — main.yml fb-history 캐시(restore/save)로 run 간 유지.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import FB_HISTORY_FILE, FB_HISTORY_MAX

VERSION = "1.0.0"

logger = logging.getLogger(__name__)

BLOCKING_STATUSES = ("ok", "unknown")
_KST = ZoneInfo("Asia/Seoul")


def publication_date(now: datetime | None = None) -> str:
    """KST 발행일(YYYY-MM-DD). duplicate_checker 의 발행일 기준과 동일(KST)."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(_KST).date().isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def _path(path: Path | None) -> Path:
    return Path(path) if path is not None else Path(FB_HISTORY_FILE)


def load(path: Path | None = None) -> list[dict]:
    p = _path(path)
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[FBHistory] 이력 로드 실패 — 빈 이력으로 진행: {e}")
        return []


def _save(records: list[dict], path: Path | None = None) -> None:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(records[-FB_HISTORY_MAX:], f, ensure_ascii=False, indent=2)


def is_blocked(
    session: str,
    now: datetime | None = None,
    path: Path | None = None,
) -> tuple[bool, str]:
    """(차단 여부, 사유). 같은 session·KST 발행일에 ok/unknown 레코드가 있으면 차단."""
    pub_date = publication_date(now)
    for rec in reversed(load(path)):
        if rec.get("session") != session or rec.get("publication_date") != pub_date:
            continue
        status = rec.get("status")
        if status in BLOCKING_STATUSES:
            return True, f"fb_{status}_exists:{pub_date}:{session}"
    return False, ""


def record(
    session: str,
    status: str,
    text: str,
    post_id: str = "",
    kind: str = "",
    now: datetime | None = None,
    path: Path | None = None,
) -> dict:
    """게시 결과 1건 기록 후 레코드 반환. status: ok | unknown."""
    current = now or datetime.now(timezone.utc)
    rec = {
        "timestamp":        current.astimezone(timezone.utc).isoformat(),
        "publication_date": publication_date(current),
        "session":          session,
        "status":           status,
        "post_id":          post_id or "",
        "kind":             kind or "",
        "content_hash":     content_hash(text),
        "preview":          (text or "")[:80],
    }
    records = load(path)
    records.append(rec)
    _save(records, path)
    logger.info(f"[FBHistory] 기록: session={session} status={status} post_id={post_id or '-'}")
    return rec
