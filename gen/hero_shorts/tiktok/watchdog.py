"""gen/hero_shorts/tiktok/watchdog.py — Hero Shorts 무발동 감지
================================================================================
공백(2026-10-15 실측)
    기존 `notify_watchdog.yml` 은 `workflow_run`(완료 이벤트)에만 반응한다.
    **이벤트가 아예 안 오면(드롭·지연) 아무 경보도 없다** — 그 워크플로우 주석이
    스스로 한계로 명시한 지점이다. 실측 근거: hero gen 의 크론은 `0 1 * * 2,4`
    (KST 화·목 10:00)인데 2026-10-15 실제 발화는 `05:42:36Z` — **4시간 42분 지연**.
    지연은 조용히 지나가고, 드롭은 영원히 안 온다.

해결
    "발화했다"를 **기대 시각 대비 관측 증거**로 판정한다. 이벤트를 기다리는 게
    아니라, 기대 시각이 지난 뒤 **증거가 있는지**를 능동적으로 조회한다.

    신호(각각 독립, 없으면 `unavailable` 로 기록 — '활동 없음'과 구분한다)
      actions        GitHub Actions 실행 이력(Actions 스케줄 감시)
      schedule       `gsk schedule list` — 활성 스킬의 `next_run_at`(스킬 발화 감시)
      token_history  토큰 이력 `last_seen`(러너3 preflight 실행 증거)
      ledger         원장 `tiktok` 기록의 최신 시각(러너3 발행 활동 증거)

    판정
      ok        기대 시각이 지났지만 아직 유예(grace) 안 — 판단 보류
      fresh     기대 시각 이후 증거가 있음
      missed    기대 시각 + 유예를 넘겼는데 증거가 없음
      unknown   판정에 필요한 신호를 하나도 못 읽음 — 정상도 비정상도 주장하지 않는다

    **`unavailable` 을 '활동 없음'으로 뭉개면 없는 실패를 만든다.** 그래서 신호가
    전부 못 읽히면 `missed` 가 아니라 `unknown` 이다. 침묵은 증거가 아니다.

한계(추측 금지)
    GitHub 스케줄은 고부하 시 수 시간 지연된다(실측 4h42m). 그래서 Actions 유예는
    기본 6h 다 — 짧게 잡으면 지연을 '드롭'으로 오판해 경보 피로를 만든다.
    타임존: Actions 크론은 UTC, gsk 스킬 크론은 KST 로 해석한다(실측 근거는 상수 주석).
    `--expect actions` 는 Actions 워크플로우만 감시한다. 러너3 는 **gsk 스케줄 스킬**
    이므로 Actions API 로는 보이지 않는다 — 스킬 감시는 gsk 가 있는 실행 환경에서
    `--expect skill` 로 돌려야 한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

log = logging.getLogger("tiktok.watchdog")

VERSION = "1.0.0"

# ⚠️ 타임존이 다르다 — 같은 문자열이라도 해석 기준이 다르다(2026-10-15 실측).
#   GitHub Actions 크론은 **UTC 고정**이다(`0 1` = 01:00 UTC = KST 10:00).
#   gsk 스케줄 스킬 크론은 **KST** 로 해석된다(`30 10` = KST 10:30).
#   한쪽 기준으로 통일하면 다른 쪽 기대 시각이 하루 어긋난다 — 실측으로 확인됐다.
ACTIONS_CRON = "0 1 * * 2,4"
ACTIONS_CRON_TZ = "UTC"
SKILL_CRON = "30 10 * * 2,4"
HERO_WORKFLOW_NAME = "🎬 Hero Shorts Gen (v2.8.2 — gen 폴더)"
SKILL_NAME = "hero-shorts-tiktok-publish"
KO_DOW = ("월", "화", "수", "목", "금", "토", "일")

OK, FRESH, MISSED, UNKNOWN = "ok", "fresh", "missed", "unknown"
EXIT_CODES = {OK: 0, FRESH: 0, MISSED: 3, UNKNOWN: 4}

DEFAULT_GRACE_ACTIONS = 6.0
DEFAULT_GRACE_SKILL = 2.0
UNKNOWN_CHANNEL = "tiktok_token_history.json"


def _now():
    return datetime.now(UTC)


def _parse_iso(value):
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _iso(dt):
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def cron_previous(expr, now=None, tz_name=None):
    """크론의 **직전** 발화 시각(now 이하). 없으면 None."""
    from . import next_run as nx
    now_dt = now or _now()
    try:
        cur = nx.cron_next(expr, now=now_dt - timedelta(days=40), tz_name=tz_name)
    except Exception as exc:  # noqa: BLE001
        log.warning("크론 파싱 실패(%s): %s", expr, exc)
        return None
    prev, guard = None, 0
    while cur is not None and cur <= now_dt and guard < 200:
        prev, guard = cur, guard + 1
        cur = nx.cron_next(expr, now=cur, tz_name=tz_name)
    return prev


# --------------------------------------------------------------------- 신호
def _unavailable(name, reason):
    return {"name": name, "available": False, "reason": reason, "last_at": None}


def signal_actions(now, repo=None, token=None, limit=40, opener=None):
    """GitHub Actions 실행 이력 — hero 워크플로우의 최근 실행 시각."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not repo:
        return _unavailable("actions", "GITHUB_REPOSITORY 미설정")

    def _fetch(url):
        if opener is not None:
            return opener(url, token)
        import urllib.request
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    try:
        payload = _fetch(
            f"https://api.github.com/repos/{repo}/actions/runs?per_page={int(limit)}"
        )
    except Exception as exc:  # noqa: BLE001
        return _unavailable("actions", f"Actions API 실패: {exc}")
    runs = []
    for run in (payload or {}).get("workflow_runs") or []:
        if run.get("name") != HERO_WORKFLOW_NAME:
            continue
        runs.append({"created_at": run.get("created_at"), "event": run.get("event"),
                     "conclusion": run.get("conclusion"), "status": run.get("status"),
                     "url": run.get("html_url")})
    if not runs:
        return {"name": "actions", "available": True, "reason": "hero 워크플로우 실행 이력 없음",
                "last_at": None, "runs": []}
    newest = max((r["created_at"] for r in runs if r.get("created_at")), default=None)
    return {"name": "actions", "available": True, "reason": None,
            "last_at": newest, "runs": runs[:5]}


def signal_schedule(now, lister=None):
    """gsk 스케줄러 — 활성 스킬의 next_run_at(과거면 미발화)."""
    from . import next_run as nx
    try:
        payload = lister() if lister is not None else nx.fetch_schedule()[0]
    except Exception as exc:  # noqa: BLE001
        return _unavailable("schedule", f"스케줄 조회 실패: {exc}")
    if not payload:
        return _unavailable("schedule", "스케줄러 조회 불가(gsk 부재 등)")
    items = ((payload or {}).get("data") or {}).get("scheduled_skills") or []
    mine = [it for it in items if it.get("skill_name") == SKILL_NAME]
    if not mine:
        return {"name": "schedule", "available": True, "reason": "해당 스킬 스케줄 없음",
                "last_at": None, "active": False, "overdue": None}
    it = mine[0]
    dt = _parse_iso(it.get("next_run_at"))
    active = bool(it.get("is_active"))
    overdue = bool(active and dt and dt < now)
    return {"name": "schedule", "available": True, "reason": None, "last_at": None,
            "active": active, "state": it.get("state"), "next_run_at": it.get("next_run_at"),
            "overdue": overdue}


def signal_token_history(now, path=None, loader=None):
    """토큰 이력 — 마지막 관측 시각(러너3 preflight 실행 증거)."""
    try:
        if loader is not None:
            doc = loader()
        else:
            from . import token_history as th
            doc = th.load(path)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("token_history", f"이력 읽기 실패: {exc}")
    obs = (doc or {}).get("observations") or []
    if not obs:
        return {"name": "token_history", "available": True, "reason": "관측 없음",
                "last_at": None, "observations": 0}
    stamps = [t for t in (o.get("last_seen") or o.get("first_seen") for o in obs) if t]
    return {"name": "token_history", "available": True, "reason": None,
            "last_at": max(stamps) if stamps else None, "observations": len(obs)}


def signal_ledger(now, path=None, loader=None):
    """발행 원장 — tiktok 기록의 최신 시각(러너3 발행 활동 증거)."""
    try:
        state = loader() if loader is not None else _load_ledger(path)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("ledger", f"원장 읽기 실패: {exc}")
    if not state:
        return _unavailable("ledger", "원장 없음")
    stamps = []
    for key, rec in state.items():
        if not isinstance(rec, dict):
            continue
        tk = rec.get("tiktok") or {}
        for field in ("published_at", "recorded_at", "updated_at"):
            if tk.get(field):
                stamps.append(tk[field])
        ver = tk.get("verification") or {}
        if ver.get("published_at"):
            stamps.append(ver["published_at"])
    return {"name": "ledger", "available": True, "reason": None,
            "last_at": max(stamps) if stamps else None, "entries": len(state)}


def _load_ledger(path=None):
    p = path or os.environ.get("HERO_WATCHDOG_LEDGER")
    if not p:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = os.path.join(root, "gen", "data", "publish_state.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


# --------------------------------------------------------------------- 판정
def decide(now, expect="both", signals=None, grace_hours=None):
    """기대 시각 대비 증거로 판정. 항상 dict(예외 없음)."""
    signals = signals or {}
    out = {"expect": expect, "checked_at": _iso(now), "signals": signals,
           "problems": [], "verdict": UNKNOWN, "partial": False}
    if expect == "actions":
        cron, cron_tz, observables = ACTIONS_CRON, ACTIONS_CRON_TZ, ["actions"]
    elif expect == "skill":
        cron, cron_tz, observables = SKILL_CRON, None, ["token_history", "ledger", "schedule"]
    else:
        cron, cron_tz, observables = ACTIONS_CRON, ACTIONS_CRON_TZ, \
            ["actions", "token_history", "ledger"]
    grace = float(grace_hours if grace_hours is not None
                  else (DEFAULT_GRACE_ACTIONS if expect == "actions" else DEFAULT_GRACE_SKILL))
    out.update({"cron": cron, "cron_tz": cron_tz or "Asia/Seoul", "grace_hours": grace})
    due = cron_previous(cron, now=now, tz_name=cron_tz)
    out["due_at"] = _iso(due)
    if due is None:
        out.update({"verdict": UNKNOWN, "reason": f"기대 시각 계산 불가(크론 {cron})"})
        return out

    readable = [n for n in observables if (signals.get(n) or {}).get("available")]
    missing = [n for n in observables if n not in readable]
    out.update({"readable_signals": readable, "unavailable_signals": missing})
    if not readable:
        out.update({"verdict": UNKNOWN, "partial": True,
                    "reason": "기대 시각을 판정할 신호를 하나도 읽지 못함"
                              f"(불가: {', '.join(missing)}) — 침묵은 증거가 아니다"})
        return out
    if missing:
        out["partial"] = True
        out["problems"].append(f"신호 일부 불가: {', '.join(missing)}")

    stamps = [(n, _parse_iso((signals.get(n) or {}).get("last_at"))) for n in readable]
    stamps = [(n, t) for n, t in stamps if t]
    newest = max((t for _, t in stamps), default=None)
    # 스케줄 신호는 next_run_at 이 과거면 그 자체가 미발화 증거다.
    overdue = (signals.get("schedule") or {}).get("overdue")

    if newest is not None and newest >= due:
        out.update({"verdict": FRESH, "last_activity_at": _iso(newest),
                    "last_activity_source": max(stamps, key=lambda x: x[1])[0]})
        return out
    if now < due + timedelta(hours=grace):
        remain = (due + timedelta(hours=grace) - now).total_seconds() / 3600.0
        out.update({"verdict": OK, "last_activity_at": _iso(newest),
                    "reason": f"기대 시각 + 유예까지 {remain:.1f}h 남음 — 판단 보류"})
        return out
    if overdue is True:
        out["problems"].append("스케줄러 next_run_at 이 과거 — 미발화 상태로 판단")
    out.update({"verdict": MISSED, "last_activity_at": _iso(newest),
                "reason": f"기대 {_iso(due)} + 유예 {grace}h 경과 · 읽은 신호 중 증거 없음"
                          f"({', '.join(readable)})"})
    if overdue is None:
        out["problems"].append("스케줄러 신호 없음 — 실제 발화 여부는 미확정")
    return out


def format_report(res):
    lines = [f"[hero-shorts] 무발동 감시 — {str(res.get('verdict')).upper()}"
             f" ({res.get('expect')}, v{VERSION})",
             f"기대 직전 발화: {res.get('due_at') or '-'} · 크론 {res.get('cron')}"
             f" · 유예 {res.get('grace_hours')}h",
             f"최근 활동: {res.get('last_activity_at') or '없음'}"
             + (f" ({res.get('last_activity_source')})" if res.get("last_activity_source") else "")]
    sig = res.get("signals") or {}
    parts = []
    for name, s in sig.items():
        if not s.get("available"):
            parts.append(f"{name}=불가({s.get('reason')})")
        else:
            parts.append(f"{name}={s.get('last_at') or '증거없음'}")
    if parts:
        lines.append("신호: " + " · ".join(parts))
    if res.get("partial"):
        lines.append("주의: 신호 일부 불가 — 부분 감시(coverage 축소)")
    if res.get("reason"):
        lines.append(f"사유: {res['reason']}")
    for p in res.get("problems") or []:
        lines.append(f"문제: {p}")
    return "\n".join(lines)


def alert(res):
    """missed 일 때만 경보(ok/fresh 는 침묵). 실패해도 흐름을 죽이지 않는다."""
    if res.get("verdict") != MISSED:
        return "(경보 없음)"
    try:
        from . import account as tk_account
        if tk_account.send_telegram(format_report(res)):
            return "telegram"
    except Exception as exc:  # noqa: BLE001
        log.warning("감시 경보 발송 실패: %s", exc)
    return "none"


def collect(now=None, expect="both", repo=None, token=None, history_path=None,
            ledger_path=None, fetch_actions=True):
    now = now or _now()
    signals = {}
    if expect in ("actions", "both"):
        signals["actions"] = (signal_actions(now, repo=repo, token=token)
                              if fetch_actions else _unavailable("actions", "조회 생략"))
    if expect == "skill":
        signals["schedule"] = signal_schedule(now)
    signals["token_history"] = (signal_token_history(now, path=history_path)
                               if expect != "actions"
                               else _unavailable("token_history", "Actions 환경에서 미조회"))
    signals["ledger"] = (signal_ledger(now, path=ledger_path) if expect != "actions"
                         else _unavailable("ledger", "Actions 환경에서 미조회"))
    return decide(now, expect=expect, signals=signals)


def main(argv=None):
    ap = argparse.ArgumentParser(description=f"hero-shorts 무발동 감시 v{VERSION}")
    ap.add_argument("--expect", choices=["actions", "skill", "both"], default="both")
    ap.add_argument("--grace-hours", type=float, default=None)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--history", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true", help="missed 시 텔레그램 경보")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    res = collect(expect=args.expect, repo=args.repo, history_path=args.history,
                  ledger_path=args.ledger)
    if args.grace_hours is not None:
        res = decide(_now(), expect=args.expect, signals=res.get("signals"),
                     grace_hours=args.grace_hours)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(format_report(res))
    if args.notify:
        print(f"경보: {alert(res)}")
    return EXIT_CODES.get(res.get("verdict"), 4)


if __name__ == "__main__":
    sys.exit(main())
