"""
gen/hero_shorts/approval.py — 인스타 발행 전 텔레그램 승인 게이트 (v2.7.0)
================================================================
2페이즈 스케줄의 승인 계층. 상세설계: docs/APPROVAL_GATE_DESIGN.md
  페이즈1(생성 러너): QC 통과 산출물(영상+캡션)을 텔레그램으로 전송해 [승인]/[보류] 인라인 버튼 요청
  페이즈2(발행 러너): getUpdates 폴링으로 마스터 결정을 원장(approval 블록)에 반영 — 승인건만 발행 허용

보안 원칙(fail-closed):
  • TELEGRAM_BOT_TOKEN / HERO_TELEGRAM_CHAT_ID 미설정이면 승인 절차가 즉시 실패한다(조용한 통과 없음).
  • 콜백은 승인 요청을 보낸 채팅(HERO_TELEGRAM_CHAT_ID, 쉼표 복수 허용)과 일치할 때만 유효하다.
  • 승인 없이는 zernio_publish 가 발행하지 않는다(HERO_APPROVAL_REQUIRED=1 또는 승인 요청 이력 존재 시).
  • 결정 콜백은 1회 소비 — answerCallbackQuery + 메시지 편집으로 더블탭·리플레이 차단, 원장 decided_at 이 멱등 키.
"""
import datetime
import json
import logging
import os
import uuid
import urllib.request
from pathlib import Path

log = logging.getLogger("hero_shorts.approval")

API_BASE = "https://api.telegram.org/bot"
CB_PREFIX = "hs"                       # callback_data 형식: "hs:approve:ep87"
VIDEO_MAX_BYTES = 50 * 1024 * 1024     # Telegram 봇 API sendVideo 상한(50MB)
CAPTION_MAX = 900                      # sendVideo 캡션 안전 자르기 길이(공식 1024)


class ApprovalError(RuntimeError):
    pass


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _config():
    """시크릿/채팅 설정 해석 — 미설정이면 fail-closed로 즉시 실패."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("HERO_TELEGRAM_CHAT_ID")
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token), ("HERO_TELEGRAM_CHAT_ID", chat)) if not v]
    if missing:
        raise ApprovalError(f"텔레그램 승인 설정 누락({', '.join(missing)}) — fail-closed: 승인 절차 중단")
    return token, chat


def _call_api(token, method, params=None, file_field=None, file_path=None, timeout=60):
    """봇 API 호출 — 파일 없으면 JSON POST, 있으면 multipart 업로드. result 반환."""
    url = f"{API_BASE}{token}/{method}"
    if file_path is None:
        data = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    else:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (params or {}).items():
            if v is None:
                continue
            parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode("utf-8"))
        size = Path(file_path).stat().st_size
        if size > VIDEO_MAX_BYTES:
            raise ApprovalError(f"영상이 텔레그램 업로드 상한 초과({size}B > {VIDEO_MAX_BYTES}B) — 링크 전송으로 폴백 필요")
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                      f"filename=\"{Path(file_path).name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode("utf-8"))
        parts.append(Path(file_path).read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        req = urllib.request.Request(url, data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("ok"):
        raise ApprovalError(f"텔레그램 API {method} 실패: {str(payload)[:200]}")
    return payload.get("result")


def request_approval(ep, title, caption, media_url, video_path=None):
    """페이즈1 — 영상(있으면 sendVideo, 없거나 실패 시 링크 sendMessage) + 승인 버튼 전송.
    반환: 원장 기록용 approval 블록(dict)."""
    token, chat = _config()
    kb = {"inline_keyboard": [[
        {"text": "✅ 승인", "callback_data": f"{CB_PREFIX}:approve:ep{ep}"},
        {"text": "⏸ 보류", "callback_data": f"{CB_PREFIX}:hold:ep{ep}"},
    ]]}
    text = (f"🎬 Hero Shorts Ep{ep} 발행 승인 요청\n제목: {title}\n\n"
            f"{(caption or '').strip()[:CAPTION_MAX]}\n\n미리보기: {media_url}")
    sent = None
    if video_path and Path(video_path).exists():
        try:
            sent = _call_api(token, "sendVideo",
                             params={"chat_id": chat, "caption": text[:CAPTION_MAX],
                                     "reply_markup": json.dumps(kb)},
                             file_field="video", file_path=str(video_path))
        except Exception as exc:
            log.warning("sendVideo 실패 — 링크 메시지로 폴백: %s", exc)
    if sent is None:
        sent = _call_api(token, "sendMessage",
                         params={"chat_id": chat, "text": text, "reply_markup": json.dumps(kb)})
    block = {
        "status": "READY_FOR_APPROVAL",
        "requested_at": _now_iso(),
        "chat_id": chat,
        "message_id": sent.get("message_id"),
        "media_url": media_url,
        "video_sent": sent is not None and sent is not True,
    }
    log.info("승인 요청 전송 완료: ep%s → chat=%s message_id=%s", ep, chat, block["message_id"])
    return block


def _ack(token, update_id):
    try:
        _call_api(token, "getUpdates", params={"offset": update_id + 1, "timeout": 0})
    except Exception as exc:
        log.warning("콜백 ack 실패(무시): %s", exc)


def poll_callbacks():
    """getUpdates 폴링 — 유효 콜백(승인 채팅·형식 일치)만 해석. 반환: 결정 리스트."""
    token, chat = _config()
    allowed = {c.strip() for c in chat.split(",") if c.strip()}
    updates = _call_api(token, "getUpdates",
                        params={"timeout": 0, "allowed_updates": ["callback_query"]}) or []
    out = []
    for u in updates:
        uid = u.get("update_id")
        cq = u.get("callback_query") or {}
        data = cq.get("data") or ""
        parts = data.split(":")
        chat_id = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
        if len(parts) != 3 or parts[0] != CB_PREFIX or parts[1] not in ("approve", "hold") \
                or not parts[2].startswith("ep"):
            _ack(token, uid)
            continue
        if chat_id not in allowed:
            log.warning("허용되지 않은 채팅의 콜백 무시: chat=%s", chat_id)
            _ack(token, uid)
            continue
        try:
            ep = int(parts[2][2:])
        except ValueError:
            _ack(token, uid)
            continue
        out.append({
            "ep": ep,
            "decision": "APPROVED" if parts[1] == "approve" else "HOLD",
            "callback_id": cq.get("id"),
            "chat_id": chat_id,
            "message_id": (cq.get("message") or {}).get("message_id"),
            "decided_by": (cq.get("from") or {}).get("username"),
            "decided_at": _now_iso(),
            "update_id": uid,
        })
    return out


def answer_callback(callback_id, text):
    token, _ = _config()
    _call_api(token, "answerCallbackQuery",
              params={"callback_query_id": callback_id, "text": text, "show_alert": False})


def edit_message(message_id, text):
    token, chat = _config()
    _call_api(token, "editMessageText",
              params={"chat_id": chat, "message_id": message_id, "text": text})


def resolve_pending(state, lookback_hours=36):
    """페이즈2 — 승인 대기 회차(approval.status == READY_FOR_APPROVAL|HOLD, 미결정)를
    폴링 결과와 대조해 원장에 반영. 결정 콜백은 1회 소비(응답+메시지 편집). 요약 dict 반환."""
    pending = {k: v for k, v in state.items()
               if str(k).startswith("ep") and isinstance(v, dict)
               and isinstance(v.get("approval"), dict)
               and v["approval"].get("status") in ("READY_FOR_APPROVAL", "HOLD")
               and not v["approval"].get("decided_at")}
    if not pending:
        return {"pending": 0, "decisions": []}
    applied = []
    for d in poll_callbacks():
        key = f"ep{d['ep']}"
        entry = pending.get(key)
        if not entry:                       # 대기 회차가 아닌 콜백 — ack 완료, 무시
            continue
        ap = entry["approval"]
        ap.update({"status": d["decision"], "decided_at": d["decided_at"],
                   "decided_by": d.get("decided_by"), "callback_id": d.get("callback_id"),
                   "message_id": d.get("message_id")})
        approved = d["decision"] == "APPROVED"
        note = f"Ep{d['ep']} " + ("✅ 승인 — 발행 진행" if approved else "⏸ 보류 — 미발행 유지")
        try:
            answer_callback(d["callback_id"], note)
        except Exception as exc:
            log.warning("callback 응답 실패(무시): %s", exc)
        try:
            if d.get("message_id"):
                edit_message(d["message_id"], note)
        except Exception as exc:
            log.warning("메시지 갱신 실패(무시): %s", exc)
        applied.append({"ep": d["ep"], "decision": d["decision"]})
        log.info("승인 결정 반영: ep%s → %s", d["ep"], d["decision"])
    return {"pending": len(pending), "decisions": applied}
