"""gen/hero_shorts/reapproval.py — 승인 요청 자동 재발행 (24h 콜백 소실 창 대응)
================================================================================
왜 필요한가 (실측 근거)
-----------------------
`approval.poll_callbacks` 는 처리한 콜백을 `_ack` 로 확정한다(`offset = update_id + 1`).
Telegram Bot API 는 "Incoming updates are stored on the server until the bot receives
them either way, but they will not be kept longer than 24 hours"
(core.telegram.org/bots/api) 라고 명시한다.

즉 러너3 이 24시간 넘게 실행되지 못하면, 그 사이 마스터가 버튼을 누른 승인 콜백은
서버에서 만료되어 **다음 실행이 폴링해도 없다**. 회차는 무응답으로 고착되고
재요청 외에 복구 수단이 없다. `tiktok.runner catchup` 이 이 상태를
`approval_waiting[].callback_at_risk` 로 감지하고, 이 모듈이 실제 재발행을 수행한다.

재발행이 하는 일
----------------
`approval.request_approval` 을 다시 호출해 **새 메시지 + 새 requested_at** 을 만든다.
`requested_at` 이 갱신되므로 24h 창이 리셋되고, 마스터는 다시 버튼을 누를 수 있다.

원칙 (fail-closed)
------------------
  · 기본 dry-run — `--send` 없이는 텔레그램에 아무것도 보내지 않는다
  · 이미 결정된 회차(`decided_at`)는 재발행하지 않는다 — 중복 승인 유도 금지
  · 이미 발행된 회차(`tiktok.status == published`)는 재발행하지 않는다
  · 재발행 상한(기본 3회)과 쿨다운(기본 24h)을 넘으면 건너뛴다 — 무한 스팸 차단
  · 미디어를 해석하지 못하면 **보내지 않고** 사유를 보고한다(빈 요청 금지)
  · 원 요청은 `approval.original_request` 에 1회만 보존한다(감사 추적)
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import logging
import os
import pathlib
import sys

log = logging.getLogger("hero_shorts.reapproval")

VERSION = "1.0.0"

# Telegram Bot API: updates are not kept longer than 24 hours.
RETENTION_HOURS = 24.0
DEFAULT_MAX_REQUESTS = 3          # 최초 요청 1 + 재발행 3
DEFAULT_COOLDOWN_HOURS = 24.0     # 재발행 최소 간격 — 요청 폭주 방지
PENDING_STATUSES = ("READY_FOR_APPROVAL", "HOLD")
TERMINAL_STATUSES = ("APPROVED", "HOLD_DECIDED", "APPROVAL_FAILED")
VIDEO_MAX_BYTES = 50 * 1024 * 1024

ENV_STATE = "HERO_PUBLISH_STATE"

EXIT_OK = 0        # 재발행 대상 없음 / 전량 전송 성공
EXIT_NEEDED = 3    # dry-run: 재발행 대상 있음
EXIT_UNKNOWN = 4   # 원장 부재
EXIT_SEND_FAIL = 5 # 전송 실패


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _now_iso():
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def _state_path(path=None):
    return pathlib.Path(path or os.environ.get(ENV_STATE) or "gen/data/publish_state.json")


def _entry_ep(key):
    tag = str(key)
    return int(tag[2:]) if tag.startswith("ep") and tag[2:].isdigit() else None


# --------------------------------------------------------------- 감지
def is_pending(entry):
    """승인 결정이 아직 없는 회차인가."""
    ap = entry.get("approval") if isinstance(entry.get("approval"), dict) else None
    if ap is None:
        return False
    if ap.get("decided_at"):
        return False
    return ap.get("status") not in TERMINAL_STATUSES


def requested_at(entry):
    ap = entry.get("approval") if isinstance(entry.get("approval"), dict) else {}
    return _parse_iso(ap.get("requested_at"))


def last_rerequest_at(entry):
    ap = entry.get("approval") if isinstance(entry.get("approval"), dict) else {}
    rows = ap.get("rerequests") or []
    stamps = [_parse_iso(r.get("at")) for r in rows if isinstance(r, dict)]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def rerequest_count(entry):
    ap = entry.get("approval") if isinstance(entry.get("approval"), dict) else {}
    return len([r for r in (ap.get("rerequests") or []) if isinstance(r, dict)])


def find_stale(state, now=None, retention_hours=RETENTION_HOURS, eps=None):
    """24h 창을 넘긴 미결 승인 요청 목록(감지 전용 · 원장 미변경).

    재발행 '가능' 여부는 여기서 판정하지 않는다 — 감지와 판단을 분리한다.
    """
    now = now or _now()
    out = []
    for key, val in state.items():
        if not isinstance(val, dict):
            continue
        ep = _entry_ep(key)
        if ep is None or (eps is not None and ep not in eps):
            continue
        if not is_pending(val):
            continue
        req = requested_at(val)
        if req is None:
            out.append({"ep": ep, "age_hours": None, "requested_at": None,
                        "status": (val.get("approval") or {}).get("status"),
                        "callback_at_risk": False, "reason": "요청 시각 기록 없음"})
            continue
        age = (now - req).total_seconds() / 3600.0
        out.append({
            "ep": ep,
            "age_hours": round(age, 1),
            "requested_at": req.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": (val.get("approval") or {}).get("status"),
            "callback_at_risk": age >= retention_hours,
            "reason": (f"요청 후 {age:.1f}h 경과 — Telegram 보존 {retention_hours:.0f}h 초과"
                       if age >= retention_hours else f"요청 후 {age:.1f}h — 창 내"),
        })
    out.sort(key=lambda r: (r["age_hours"] is None, -(r["age_hours"] or 0)))
    return out


# --------------------------------------------------------------- 미디어
def resolve_media(ep, entry, restorer=None, now=None):
    """재발행용 (video_path, media_url) 해석. 실패하면 (None, None, why).

    순서: 로컬 파일(있으면 sendVideo) → AI Drive 복원 → 원장 media_url.
    `_` 로컬 파일은 size <= 50MB 일 때만 쓴다(초과 시 링크 폴백).
    """
    ap = entry.get("approval") if isinstance(entry.get("approval"), dict) else {}
    comb = entry.get("combined") if isinstance(entry.get("combined"), dict) else {}
    media_url = comb.get("media_url") or ap.get("media_url")
    for cand in (comb.get("video_path"), entry.get("final")):
        if cand and pathlib.Path(str(cand)).exists():
            size = pathlib.Path(str(cand)).stat().st_size
            if size <= VIDEO_MAX_BYTES:
                return str(cand), media_url, f"로컬 영상({size}B) — sendVideo"
    drive = comb.get("aidrive_path")
    if drive:
        dest = pathlib.Path("/tmp/hero_reapproval") / f"ep{ep}_final_voiced.mp4"
        fn = restorer or _restore_from_drive
        try:
            got = fn(drive, dest)
            if got and pathlib.Path(got).exists() \
                    and pathlib.Path(got).stat().st_size <= VIDEO_MAX_BYTES:
                return str(got), media_url, "AI Drive 복원 영상 — sendVideo"
        except Exception as exc:  # noqa: BLE001
            log.warning("AI Drive 복원 실패(ep%s): %s", ep, exc)
    if media_url:
        return None, media_url, "원장 media_url — 링크 전송(sendMessage)"
    return None, None, "미디어 해석 실패 — 로컬/AI Drive/media_url 전부 없음"


def _restore_from_drive(aidrive_path, dest):
    from .mediastore import restore_media
    restore_media(aidrive_path, dest)
    return str(dest)


# --------------------------------------------------------------- 계획
def build_plan(state, now=None, eps=None, retention_hours=RETENTION_HOURS,
               max_requests=DEFAULT_MAX_REQUESTS, cooldown_hours=DEFAULT_COOLDOWN_HOURS,
               restorer=None):
    """재발행 계획. 원장 미변경 · 전송 없음."""
    now = now or _now()
    actions, skipped = [], []
    for row in find_stale(state, now=now, retention_hours=retention_hours, eps=eps):
        ep = row["ep"]
        entry = state.get(f"ep{ep}") or {}
        tk = entry.get("tiktok") if isinstance(entry.get("tiktok"), dict) else {}
        base = {"ep": ep, "age_hours": row["age_hours"], "status": row["status"],
                "callback_at_risk": row["callback_at_risk"]}
        if not row["callback_at_risk"]:
            skipped.append({**base, "skip_reason": "24h 창 내 — 재발행 불필요"})
            continue
        if tk.get("status") == "published":
            skipped.append({**base, "skip_reason": "이미 발행됨(tiktok.status=published)"})
            continue
        count = rerequest_count(entry)
        if count >= max_requests:
            skipped.append({**base, "skip_reason": f"재발행 상한 도달({count}/{max_requests})"})
            continue
        last = last_rerequest_at(entry)
        if last is not None:
            gap = (now - last).total_seconds() / 3600.0
            if gap < cooldown_hours:
                skipped.append({**base, "skip_reason":
                                f"쿨다운 {cooldown_hours:.0f}h 미경과(마지막 {gap:.1f}h 전)"})
                continue
        video_path, media_url, how = resolve_media(ep, entry, restorer=restorer)
        if not media_url and not video_path:
            skipped.append({**base, "skip_reason": how})
            continue
        actions.append({**base, "video_path": video_path, "media_url": media_url,
                        "media_how": how, "rerequest_count": count,
                        "reason": row["reason"]})
    return {
        "version": VERSION,
        "generated_at": _now_iso(),
        "retention_hours": retention_hours,
        "max_requests": max_requests,
        "cooldown_hours": cooldown_hours,
        "actions": actions,
        "skipped": skipped,
        "action_count": len(actions),
    }


# --------------------------------------------------------------- 실행
def re_request(entry, ep, media_url, video_path, sender, reason=None):
    """승인 요청 1건 재발행 ▸ 원장 approval 블록 갱신(깊은 복사).

    `sender` = approval.request_approval 과 같은 시그니처의 콜러블(테스트 주입점).
    """
    ap = entry.setdefault("approval", {})
    title = (entry.get("combined") or {}).get("title") or entry.get("title") or f"Ep{ep}"
    caption = ""
    cap_file = entry.get("caption_file")
    if cap_file and pathlib.Path(str(cap_file)).exists():
        caption = pathlib.Path(str(cap_file)).read_text(encoding="utf-8").strip()
    if "original_request" not in ap:
        ap["original_request"] = {k: ap.get(k) for k in
                                  ("status", "requested_at", "message_id", "media_url",
                                   "chat_id")}
    origin = ap.get("original_request", {}).get("requested_at")
    block = sender(ep, title, caption, media_url, video_path)
    block["status"] = "READY_FOR_APPROVAL"
    block["rerequest_of"] = origin        # 전송 블록에도, 원장에도 남긴다(감사 추적)
    ap["rerequest_of"] = origin
    ap.update({"status": "READY_FOR_APPROVAL", "requested_at": block.get("requested_at"),
               "message_id": block.get("message_id"), "media_url": block.get("media_url"),
               "chat_id": block.get("chat_id"), "decided_at": None,
               "decided_by": None})
    ap.setdefault("rerequests", []).append(
        {"at": block.get("requested_at"), "message_id": block.get("message_id"),
         "reason": reason, "media_how": None})
    ap["rerequest_count"] = rerequest_count(entry)
    return entry


def apply_plan(state, plan, sender, now=None):
    """계획 적용 — 새 원장(깊은 복사)과 결과 요약 반환."""
    new = copy.deepcopy(state)
    sent, failed = [], []
    for act in plan["actions"]:
        ep = act["ep"]
        try:
            entry = new[f"ep{ep}"]
            re_request(entry, ep, act["media_url"], act["video_path"], sender,
                       reason=act["reason"])
            sent.append({"ep": ep, "message_id": entry["approval"].get("message_id"),
                         "media_how": act["media_how"]})
        except Exception as exc:  # noqa: BLE001
            log.error("재발행 실패 ep%s: %s", ep, exc)
            failed.append({"ep": ep, "error": str(exc)[:200]})
    return new, {"sent": sent, "failed": failed}


def format_report(plan, result=None, send=False):
    mode = "전송(--send)" if send else "DRY-RUN"
    lines = [f"[hero-shorts] 승인 재발행 v{VERSION} — {mode}",
             f"재발행 대상 {plan['action_count']}건 · 건너뜀 {len(plan['skipped'])}건 "
             f"(보존 {plan['retention_hours']:.0f}h · 상한 {plan['max_requests']}회 "
             f"· 쿨다운 {plan['cooldown_hours']:.0f}h)"]
    for act in plan["actions"]:
        lines.append(f"· ep{act['ep']} — {act['reason']} / 미디어 {act['media_how']}")
    for sk in plan["skipped"]:
        lines.append(f"  건너뜀 ep{sk['ep']} — {sk['skip_reason']}")
    if result:
        for row in result["sent"]:
            lines.append(f"  전송 ep{row['ep']} message_id={row['message_id']}")
        for row in result["failed"]:
            lines.append(f"  !! 실패 ep{row['ep']} — {row['error']}")
    if not plan["actions"] and not plan["skipped"]:
        lines.append("재발행 대상 없음 — 미결 승인 요청이 없다")
    return "\n".join(lines)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(
        prog="hero-reapproval",
        description=f"승인 요청 자동 재발행 v{VERSION} — 기본 dry-run(전송 없음)")
    ap.add_argument("--state", default=None, help=f"원장 경로(기본 env {ENV_STATE})")
    ap.add_argument("--ep", action="append", default=None, help="대상 회차(반복)")
    ap.add_argument("--now", default=None, help="기준 시각 ISO8601(검증용)")
    ap.add_argument("--retention-hours", type=float, default=RETENTION_HOURS)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    ap.add_argument("--send", action="store_true", help="실제로 텔레그램 재발행 전송")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args(argv)

    path = _state_path(args.state)
    if not path.exists():
        print(json.dumps({"ok": False, "reason": "state_missing", "path": str(path)},
                         ensure_ascii=False))
        return EXIT_UNKNOWN
    state = json.loads(path.read_text(encoding="utf-8"))
    now = _parse_iso(args.now) or _now()
    plan = build_plan(state, now=now, eps=set(args.ep) if args.ep else None,
                      retention_hours=args.retention_hours, max_requests=args.max_requests,
                      cooldown_hours=args.cooldown_hours)
    result = None
    if args.send and plan["actions"]:
        from .approval import request_approval
        new, result = apply_plan(state, plan, request_approval, now=now)
        if result["sent"]:
            from . import statestore
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            try:
                statestore.sync_states([path])
            except Exception as exc:  # noqa: BLE001
                log.error("미러링 실패 — 다음 실행 중복 승인 요청 위험: %s", exc)
                for row in result["sent"]:
                    row["mirror"] = f"실패: {exc}"[:200]
        if result["failed"]:
            if args.as_json:
                print(json.dumps({"plan": plan, "result": result}, ensure_ascii=False, indent=2))
            else:
                print(format_report(plan, result, send=True))
            return EXIT_SEND_FAIL
    if args.as_json:
        print(json.dumps({"plan": plan, "result": result}, ensure_ascii=False, indent=2))
    else:
        print(format_report(plan, result, send=args.send))
    if args.send:
        return EXIT_OK
    return EXIT_NEEDED if plan["action_count"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
