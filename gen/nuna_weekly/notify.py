"""
gen/nuna_weekly/notify.py — 텔레그램 알림·보류 버튼 (설계서 8장, 보안 15장 S-04)
================================================================================
gen/hero_shorts/approval.py 의 Bot API 사용법(sendVideo multipart · sendMessage ·
getUpdates allowed_updates=["callback_query"] · answerCallbackQuery · inline_keyboard)을 이식했다.

S-04 — 보류 콜백은 NUNA_TELEGRAM_ADMIN_IDS(쉼표 구분 정수)에 든 사용자 id 만 수락한다.
       관리자 ID 미설정이면 보류 버튼 자체를 붙이지 않는다(누구나 누르는 버튼 금지).
       거부 시도는 사용자 id 만 기록(이름·username 미기록).
토큰은 URL(/bot<token>/)에 들어가므로 예외·로그는 반드시 secret_env.redact 를 거친다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, secret_env

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.notify")

API_BASE = "https://api.telegram.org/bot"
CB_PREFIX = "nuna:hold:"
CAPTION_MAX = 1024           # Telegram 캡션 상한(hero approval.py 주석: 공식 1024)
POLL_TIMEOUT_SEC = 2


class NotifyError(RuntimeError):
    """텔레그램 호출 실패 (메시지는 마스킹됨)."""


def parse_admin_ids(raw: str | None) -> set[int]:
    out: set[int] = set()
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            log.warning("[NunaWeekly:notify] NUNA_TELEGRAM_ADMIN_IDS 에 정수 아닌 항목 무시")
    return out


def hold_callback(episode_id: str) -> str:
    return f"{CB_PREFIX}{episode_id}"


def _kst_text(dt: datetime) -> str:
    d = dt if dt.tzinfo else dt.replace(tzinfo=config.KST)
    return d.astimezone(config.KST).strftime("%Y-%m-%d %H:%M KST")


def build_generated(episode_id: str, scheduled_at: datetime, admin_ids: set[int]) -> dict[str, Any]:
    """알림 캡션·키보드 구성 (네트워크 없음)."""
    caption = f"[NUNA 주간 브리핑] {episode_id} 생성 완료 · 발행 예정 {_kst_text(scheduled_at)}"
    markup = None
    if admin_ids:
        markup = {"inline_keyboard": [[{"text": "보류", "callback_data": hold_callback(episode_id)}]]}
    else:
        caption += "\n보류 버튼 비활성: 관리자 ID 미설정"
    return {"caption": caption[:CAPTION_MAX], "reply_markup": markup}


class TelegramClient:
    def __init__(self, session=None, timeout: int = 60) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.s = session
        self.timeout = timeout
        self._token = secret_env.require("TELEGRAM_BOT_TOKEN")
        self.chat_id = secret_env.require("TELEGRAM_PAID_CHANNEL_ID")
        self.admin_ids = parse_admin_ids(secret_env.optional("NUNA_TELEGRAM_ADMIN_IDS"))
        log.info("[NunaWeekly:notify] v%s 시작 — 관리자 %d명", VERSION, len(self.admin_ids))

    def _call(self, method: str, params: dict[str, Any] | None = None,
              file_field: str | None = None, file_path: Path | None = None, timeout: int | None = None) -> Any:
        url = f"{API_BASE}{self._token}/{method}"
        try:
            if file_path is None:
                r = self.s.post(url, json=params or {}, timeout=timeout or self.timeout)
            else:
                data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else str(v))
                        for k, v in (params or {}).items() if v is not None}
                with Path(file_path).open("rb") as fh:
                    r = self.s.post(url, data=data, files={file_field: (Path(file_path).name, fh, "video/mp4")},
                                    timeout=timeout or self.timeout)
        except Exception as e:
            raise NotifyError(secret_env.redact(f"텔레그램 {method} 호출 실패: {type(e).__name__}: {e}", 300)) from None
        try:
            payload = r.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise NotifyError(f"텔레그램 {method} 실패 HTTP {getattr(r, 'status_code', '?')}: "
                              f"{secret_env.redact(str(payload.get('description') if isinstance(payload, dict) else ''), 200)}")
        return payload.get("result")

    # ── 생성 완료 알림 ──
    def notify_generated(self, episode_id: str, video_path: str | Path | None, scheduled_at: datetime,
                         dry: bool) -> dict[str, Any]:
        plan = build_generated(episode_id, scheduled_at, self.admin_ids)
        p = Path(video_path) if video_path else None
        use_video = bool(p and p.is_file() and p.stat().st_size <= config.TELEGRAM_VIDEO_MAX_BYTES)
        method = "sendVideo" if use_video else "sendMessage"
        if p and p.is_file() and not use_video:
            log.warning("[NunaWeekly:notify] 영상 %dB > 텔레그램 상한 — 텍스트만 전송", p.stat().st_size)
        result: dict[str, Any] = {"method": method, "caption": plan["caption"],
                                  "reply_markup": plan["reply_markup"], "dry": dry, "message_id": None}
        if dry:
            log.info("[NunaWeekly:notify] DRY_RUN — %s 계획만 (전송 없음)", method)
            return result
        params: dict[str, Any] = {"chat_id": self.chat_id}
        if plan["reply_markup"]:
            params["reply_markup"] = plan["reply_markup"]
        if use_video:
            params["caption"] = plan["caption"]
            sent = self._call("sendVideo", params, file_field="video", file_path=p, timeout=max(self.timeout, 300))
        else:
            params["text"] = plan["caption"]
            sent = self._call("sendMessage", params)
        result["message_id"] = (sent or {}).get("message_id") if isinstance(sent, dict) else None
        log.info("[NunaWeekly:notify] %s 전송 완료 — %s", method, episode_id)
        return result

    # ── 보류 콜백 폴링 ──
    def poll_hold(self, episode_id: str) -> dict[str, Any] | None:
        # offset 확정(ack)을 하지 않는다 — 같은 봇의 hero_shorts 승인 콜백(hs:*)까지 소비하기 때문.
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT_SEC, "allowed_updates": ["callback_query"]}
        updates = self._call("getUpdates", params, timeout=POLL_TIMEOUT_SEC + 30) or []
        want = hold_callback(episode_id)
        accepted: dict[str, Any] | None = None
        for u in updates:
            cq = u.get("callback_query") or {}
            if cq.get("data") != want:
                continue
            from_id = (cq.get("from") or {}).get("id")
            ok = isinstance(from_id, int) and from_id in self.admin_ids
            try:
                self._call("answerCallbackQuery", {"callback_query_id": cq.get("id"),
                                                   "text": "보류 접수" if ok else "권한 없음",
                                                   "show_alert": False})
            except NotifyError as e:
                log.warning("[NunaWeekly:notify] answerCallbackQuery 실패(무시): %s", e)
            if not ok:
                log.warning("[NunaWeekly:notify] 보류 거부 — 권한 없는 사용자 id=%s", from_id)
                continue
            if accepted is None:
                accepted = {"by": str(from_id), "at": datetime.now(config.KST).isoformat(timespec="seconds")}
                log.info("[NunaWeekly:notify] 보류 접수 — %s by id=%s", episode_id, from_id)
        return accepted

    # ── 리포트 ──
    def send_report(self, text: str, dry: bool) -> bool:
        if dry:
            log.info("[NunaWeekly:notify] DRY_RUN — 리포트 전송 생략 (%d자)", len(text or ""))
            return True
        try:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": secret_env.redact(text)[:4096]})
        except NotifyError as e:
            log.error("[NunaWeekly:notify] 리포트 전송 실패: %s", e)
            return False
        return True
