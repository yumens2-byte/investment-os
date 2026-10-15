"""gen/hero_shorts/tiktok/catchup.py — 러너3 실행 누락 회수 계획
=================================================================
읽기 전용 · 0크레딧 · 발행 없음. 원장과 크론만으로 "무엇을 얼마나 회수해야
하는가"를 계산한다.

배경(실측 근거)
--------------
1) 러너3 는 화·목 10:30 KST 발화인데 스케줄이 드롭되면 그대로 사라진다.
   실측: hero gen 크론 0 1 * * 2,4(UTC) 대비 실제 발화 4시간 42분 지연
   (2026-10-15T05:42:36Z). 24h 를 넘겨 드롭되면 슬롯이 조용히 소실된다.
2) S2 `resolve_approvals` 는 결정을 반영한 뒤 `_ack` 로 Telegram offset 을
   전진시킨다(approval.py `offset = update_id + 1`). 그래서 미처리 콜백은
   **미실행 기간 동안에만** 남아 있고, Telegram Bot API 는
   "Incoming updates ... will not be kept longer than 24 hours"
   (core.telegram.org/bots/api) 라고 명시한다 — 즉 24시간을 넘겨 실행하면
   승인 콜백이 **영구 소실**되어 회차가 무응답으로 고착된다. 재요청만이 복구다.
3) 스킬 S5 는 `items[0]`(= 가장 오래된 회차) 1편만 발행한다. 생성이 발행을
   앞지르면 미발행 잔량이 쌓이고 회당 1편씩만 줄어든다 — 회수 계획이 필요하다.

원칙
----
  · 판정 불가는 추측하지 않는다 — 기준 없으면 unknown(exit 4)
  · 발행은 하지 않는다. 계획과 근거만 낸다(발행은 승인 게이트를 지나는 별개 행위)
  · 잔량 계산은 러너3 `ready` 와 **같은 함수**(collect_ready)를 쓴다 — 판정 이원화 금지
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import math
import sys

log = logging.getLogger("tiktok.catchup")

VERSION = "1.0.0"
DEFAULT_CRON = "30 10 * * 2,4"
DEFAULT_TZ = "Asia/Seoul"

# Telegram Bot API 공식 문서(core.telegram.org/bots/api):
# "Incoming updates are stored on the server until the bot receives them either way,
#  but they will not be kept longer than 24 hours."
APPROVAL_RETENTION_HOURS = 24.0
RETAINED_SLOTS_SHOWN = 12

EXIT_OK = 0        # 회수 불필요
EXIT_CATCHUP = 3   # 회수 필요(잔량 또는 미실행 슬롯)
EXIT_UNKNOWN = 4   # 기준 없음(발행 이력 부재)

VERDICT_UP_TO_DATE = "up_to_date"
VERDICT_BACKLOG = "catchup_needed"
VERDICT_MISSED_RUN = "missed_run"
VERDICT_GENERATION_GAP = "generation_gap"
VERDICT_NO_BASELINE = "no_baseline"


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def _iso(dt):
    if dt is None:
        return None
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------- 크론 슬롯
def slots_between(expr, after, until, tz_name=None, guard=400):
    """(after, until] 구간의 크론 발화 시각. 크론 해석 기준은 호출자가 정한다.

    ⚠️ 러너3 는 gsk 스케줄 스킬이라 **KST** 다(Actions 크론과 다르다).
    """
    from . import next_run as nx
    if after is None or until is None or after >= until:
        return []
    out, cur, n = [], nx.cron_next(expr, now=after, tz_name=tz_name), 0
    while cur is not None and cur <= until and n < guard:
        out.append(cur)
        n += 1
        cur = nx.cron_next(expr, now=cur, tz_name=tz_name)
    return out


# --------------------------------------------------------------- 발행 이력
def publish_events(state):
    """원장 ▸ 확정 발행 시각 목록(플랫폼 값 우선). 다중 세그먼트 전부 포함."""
    out = []
    for key, val in state.items():
        tag = str(key)
        if not (tag.startswith("ep") and tag[2:].isdigit()) or not isinstance(val, dict):
            continue
        tk = val.get("tiktok") if isinstance(val.get("tiktok"), dict) else {}
        if tk.get("status") != "published":
            continue
        stamps = [_parse_iso(tk.get("published_at"))]
        for source in (tk.get("segments") or [], (tk.get("split_history") or {}).get("segments") or []):
            for seg in source:
                if isinstance(seg, dict):
                    stamps.append(_parse_iso(seg.get("published_at")))
        stamps = [s for s in stamps if s is not None]
        if not stamps:
            out.append({"ep": int(tag[2:]), "at": None, "source": "none"})
            continue
        out.append({"ep": int(tag[2:]), "at": max(stamps),
                    "source": "platform" if tk.get("published_at") else "segments"})
    out.sort(key=lambda e: (e["at"] is None, e["at"] or _dt.datetime.min.replace(
        tzinfo=_dt.timezone.utc)))
    return out


def last_publish(state):
    """가장 최근 확정 발행(시각 있는 것만). 없으면 None."""
    dated = [e for e in publish_events(state) if e["at"] is not None]
    return dated[-1] if dated else None


# --------------------------------------------------------------- 승인 압력
def approval_pressure(state, now=None, retention_hours=APPROVAL_RETENTION_HOURS):
    """승인 미결 회차 ▸ 요청 후 경과. 24h 초과는 콜백 소실 위험(재요청 필요)."""
    now = now or _now()
    out = []
    for key, val in state.items():
        tag = str(key)
        if not (tag.startswith("ep") and tag[2:].isdigit()) or not isinstance(val, dict):
            continue
        tk = val.get("tiktok") if isinstance(val.get("tiktok"), dict) else {}
        if tk.get("status") == "published":
            continue
        ap = val.get("approval") if isinstance(val.get("approval"), dict) else None
        if ap is not None and ap.get("status") == "APPROVED":
            continue
        requested = _parse_iso(ap.get("requested_at")) if ap else None
        age = ((now - requested).total_seconds() / 3600.0) if requested else None
        out.append({
            "ep": int(tag[2:]),
            "status": (ap or {}).get("status") or "no_approval",
            "requested_at": _iso(requested),
            "age_hours": round(age, 1) if age is not None else None,
            "callback_at_risk": bool(age is not None and age >= retention_hours),
        })
    out.sort(key=lambda r: (r["age_hours"] is None, -(r["age_hours"] or 0)))
    return out


# --------------------------------------------------------------- 계획
def build_plan(state, now=None, cron=None, tz_name=None, max_publish=1,
               retention_hours=APPROVAL_RETENTION_HOURS):
    """원장 ▸ 회수 계획. 발행 없음, 원장 미변경."""
    from . import runner
    now = now or _now()
    cron = cron or DEFAULT_CRON
    tz_name = tz_name or DEFAULT_TZ
    max_publish = max(1, int(max_publish or 1))

    items, stats = runner.collect_ready(state)          # 러너3 와 동일 판정
    publishable = [i for i in items if not i["missing"]]
    incomplete = [i for i in items if i["missing"]]

    last = last_publish(state)
    gaps = slots_between(cron, last["at"], now, tz_name=tz_name) if last else []
    missed = len(gaps)

    deficit = min(missed, len(publishable))
    if deficit:
        recommended = min(deficit, max_publish)
    else:
        recommended = min(1, len(publishable))
    runs_to_clear = int(math.ceil(len(publishable) / max_publish)) if publishable else 0

    stale = [r for r in approval_pressure(state, now=now, retention_hours=retention_hours)
             if r["callback_at_risk"]]

    if last is None:
        verdict = VERDICT_NO_BASELINE
    elif publishable:
        verdict = VERDICT_BACKLOG
    elif incomplete:
        verdict = VERDICT_GENERATION_GAP
    elif missed:
        verdict = VERDICT_MISSED_RUN
    else:
        verdict = VERDICT_UP_TO_DATE

    remedies = []
    if publishable:
        remedies.append(
            f"발행 잔량 {len(publishable)}편 — 이번 실행 권장 {recommended}편, "
            f"최고 오래된 ep{publishable[0]['ep']}")
        if runs_to_clear > 1:
            remedies.append(
                f"전량 소진까지 {runs_to_clear}회 실행 필요(회당 {max_publish}편 한도)")
    if incomplete:
        remedies.append(
            "생성 결손 " + ", ".join(f"ep{i['ep']}({'+'.join(i['missing'])})"
                                      for i in incomplete)
            + " — 러너1 재생성 필요")
    if stale:
        remedies.append(
            "승인 콜백 소실 위험 " + ", ".join(
                f"ep{r['ep']}(요청 후 {r['age_hours']}h, {r['status']})" for r in stale)
            + " — Telegram 보존 24h 초과, 재요청 필요")
    if missed and not publishable and not incomplete:
        remedies.append(f"미발행 슬롯 {missed}개 · 발행할 회차 없음 — 생성/승인 대기")

    return {
        "version": VERSION,
        "generated_at": _iso(now),
        "cron": cron,
        "cron_tz": tz_name,
        "verdict": verdict,
        "last_publish": {"ep": last["ep"], "at": _iso(last["at"]),
                         "source": last["source"]} if last else None,
        "missed_slots": missed,
        "missed_slot_at": [_iso(g) for g in gaps[-RETAINED_SLOTS_SHOWN:]],
        "backlog_count": len(items),
        "publishable_count": len(publishable),
        "incomplete_count": len(incomplete),
        "deficit": deficit,
        "recommended_publish": recommended,
        "runs_to_clear": runs_to_clear,
        "max_publish": max_publish,
        "publishable": [{"ep": i["ep"], "title": i["title"],
                         "duration_sec": i["duration_sec"], "media_url": i["media_url"]}
                        for i in publishable],
        "incomplete": [{"ep": i["ep"], "missing": i["missing"]} for i in incomplete],
        "approval_waiting": approval_pressure(state, now=now, retention_hours=retention_hours),
        "stale_approvals": stale,
        "stats": stats,
        "remedies": remedies,
    }


def format_report(plan):
    lines = [
        f"[hero-shorts] 러너3 회수 계획 v{VERSION} — {plan['verdict']}",
        f"기준 {plan['cron']} ({plan['cron_tz']}) · 기준선 "
        + (f"ep{plan['last_publish']['ep']} 발행 {plan['last_publish']['at']}"
           if plan["last_publish"] else "없음(발행 이력 부재)"),
        f"미발행 슬롯 {plan['missed_slots']} · 잔량 {plan['backlog_count']}"
        f"(발행가능 {plan['publishable_count']} / 결손 {plan['incomplete_count']})"
        f" · 부채 {plan['deficit']} · 권장 발행 {plan['recommended_publish']}편",
    ]
    if plan["missed_slot_at"]:
        lines.append("지난 미발행 슬롯: " + ", ".join(plan["missed_slot_at"][-6:]))
    for row in plan["publishable"]:
        lines.append(f"  · ep{row['ep']} {row['duration_sec']}s {row['title']}")
    for row in plan["incomplete"]:
        lines.append(f"  !! ep{row['ep']} 결손 {'+'.join(row['missing'])}")
    for row in plan["approval_waiting"]:
        risk = " ⚠콜백소실위험" if row["callback_at_risk"] else ""
        age = f"{row['age_hours']}h" if row["age_hours"] is not None else "기록없음"
        lines.append(f"  대기 ep{row['ep']} {row['status']} ({age}){risk}")
    for r in plan["remedies"]:
        lines.append("조치: " + r)
    if not plan["remedies"]:
        lines.append("조치 불필요 — 회수 대상 없음")
    return "\n".join(lines)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(
        prog="tiktok-catchup", description=f"러너3 실행 누락 회수 계획 v{VERSION}(읽기 전용)")
    ap.add_argument("--state", default="gen/data/publish_state.json", help="원장 경로")
    ap.add_argument("--cron", default=DEFAULT_CRON)
    ap.add_argument("--tz", default=DEFAULT_TZ)
    ap.add_argument("--now", default=None, help="기준 시각 ISO8601(검증/시뮬레이션용)")
    ap.add_argument("--max-publish", type=int, default=1, help="실행 1회 발행 한도")
    ap.add_argument("--retention-hours", type=float, default=APPROVAL_RETENTION_HOURS)
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args(argv)

    from . import runner
    now = _parse_iso(args.now) or _now()
    try:
        state = runner.load_publish_state(args.state)
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "reason": "state_missing", "detail": str(exc)},
                         ensure_ascii=False))
        return EXIT_UNKNOWN

    plan = build_plan(state, now=now, cron=args.cron, tz_name=args.tz,
                      max_publish=args.max_publish, retention_hours=args.retention_hours)
    print(json.dumps(plan, ensure_ascii=False, indent=2) if args.as_json
          else format_report(plan))

    if plan["verdict"] == VERDICT_NO_BASELINE and not plan["backlog_count"]:
        return EXIT_UNKNOWN
    if plan["verdict"] == VERDICT_UP_TO_DATE:
        return EXIT_OK
    return EXIT_CATCHUP


if __name__ == "__main__":
    sys.exit(main())
