"""
gen/nuna_weekly/notify.py — 텔레그램 알림·보류 버튼 (설계서 8장, 보안 15장 S-04)
================================================================================
gen/hero_shorts/approval.py 의 Bot API 사용법(sendVideo multipart · sendMessage ·
getUpdates allowed_updates=["callback_query"] · answerCallbackQuery · inline_keyboard)을 이식했다.

S-04 — TELEGRAM_PAID_CHANNEL_ID로 지정한 채널의 실제 관리자만 보류할 수 있다.
       getChat/getChatAdministrators로 확인하며 조회 실패 시 중단한다.
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
        caption += "\n보류 버튼 비활성: 채널 관리자 미확인"
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
        self.admin_ids: set[int] = set()
        self._chat: dict[str, Any] | None = None
        log.info("[NunaWeekly:notify] v%s 시작 — 채널 기준 관리자 조회", VERSION)

    def _resolve_admin_ids(self, refresh: bool = False) -> set[int]:
        if self._chat is None:
            chat = self._call("getChat", {"chat_id": self.chat_id})
            if (not isinstance(chat, dict) or type(chat.get("id")) is not int
                    or chat.get("type") not in ("private", "group", "supergroup", "channel")):
                raise NotifyError("Telegram 대상 채팅 확인 실패 — 보류 권한 확인 불가")
            self._chat = chat
        if self.admin_ids and not refresh:
            return self.admin_ids
        if self._chat["type"] == "private":
            # 개인 대화는 chat ID가 상대 사용자 ID와 같다. 그 사용자만 허용한다.
            ids = {self._chat["id"]} if self._chat["id"] > 0 else set()
        else:
            rows = self._call("getChatAdministrators", {"chat_id": self._chat["id"]})
            if not isinstance(rows, list):
                raise NotifyError("Telegram 채널 관리자 응답 형식 오류")
            ids = set()
            for row in rows:
                if not isinstance(row, dict) or row.get("status") not in ("creator", "administrator"):
                    continue
                user = row.get("user") or {}
                if not isinstance(user, dict):
                    continue
                uid = user.get("id")
                if type(uid) is int and uid > 0 and user.get("is_bot") is False:
                    ids.add(uid)
        if not ids:
            raise NotifyError("Telegram 보류 권한 사용자 없음 — 채널 관리자·봇 권한 확인 필요")
        self.admin_ids = ids
        return ids

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
        admins = self._resolve_admin_ids() if not dry else set()
        plan = build_generated(episode_id, scheduled_at, admins)
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
        admins = self._resolve_admin_ids(refresh=True)
        # offset 확정(ack)을 하지 않는다 — 같은 봇의 hero_shorts 승인 콜백(hs:*)까지 소비하기 때문.
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT_SEC, "allowed_updates": ["callback_query"]}
        updates = self._call("getUpdates", params, timeout=POLL_TIMEOUT_SEC + 30) or []
        if not isinstance(updates, list):
            raise NotifyError("Telegram callback 응답 형식 오류")
        want = hold_callback(episode_id)
        accepted: dict[str, Any] | None = None
        for u in updates:
            if not isinstance(u, dict):
                continue
            cq = u.get("callback_query") or {}
            if not isinstance(cq, dict):
                continue
            if cq.get("data") != want:
                continue
            from_id = (cq.get("from") or {}).get("id")
            message = cq.get("message") or {}
            chat = message.get("chat") or {}
            ok = (type(from_id) is int and from_id in admins
                  and (cq.get("from") or {}).get("is_bot") is not True
                  and type(chat.get("id")) is int and chat["id"] == self._chat["id"])
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
