"""gen/hero_shorts/tiktok/token_history.py — 틱톡 토큰 만료시각 이력 축적·판정
================================================================================
B안(2026-10-15): Zernio 는 토큰 갱신을 **강제하는 API 를 제공하지 않는다**(4계층 전수
조사로 확정 — `gsk zernio` 서브커맨드 3개 / 커넥터 도구 3개 / 공개 OpenAPI 631경로·
966오퍼레이션). 갱신은 Zernio 백그라운드가 수행하며, 만료 후 최대 41분 지연이
관측됐다(2026-10-15: 12:26:47Z 만료 → 13:07:48Z 재발급).

따라서 자동화의 보장은 "갱신이 될 것"이 아니라 **"갱신이 몇 회 연속 성공했는지"를
이력으로 증명하는 것**으로 세워야 한다(미검증은 추정으로 대체하지 않는다).
이 모듈이 그 이력을 축적하고, 목표 실행시각까지 필요한 갱신 횟수를 감당할 수 있는지
판정한다.

동작
    record()        매 실행(preflight)마다 tokenExpiresAt 관측을 적재한다.
                    같은 (expires_at, status) 는 1건으로 병합(runs 카운트·hours_left_min
                    갱신) → 파일 비대화 방지.
    renewals()      만료시각이 바뀐 지점 = 갱신 이벤트. expiry delta / 관측 gap 계산.
    summarize()     갱신 횟수·간격 분포·연속 성공 횟수·최대 관측 간격.
    verify_until()  목표 실행시각까지 필요한 갱신 횟수 vs 연속 성공 횟수 판정.
    backfill()      기존 실행 로그 텍스트에서 만료시각을 긁어 소급 편입.
    should_alert()  동일 상태 경보 중복 억제(차단 1h / 경고 6h 기본).

저장
    gen/data/tiktok_token_history.json — publish_state.json 과 같은 디렉터리(AI Drive 미러 대상).
    구조: {"version":..., "observations":[...], "alerts":[...]}
    경로 우선순위: 인자 > HERO_TOKEN_HISTORY_FILE > <repo>/gen/data/tiktok_token_history.json
    모든 저장은 best-effort 가 아니라 **원자적**(tmp → replace)이다. 쓰기 실패는 예외를
    올리지 않고 로그만 남긴다 — 이력 실패가 발행을 죽이면 안 된다.

한계(추측 금지 — 이 모듈이 모르는 것)
    갱신 트리거 주체는 미확정이다. `gsk schedule list` 에 갱신 실행 항목이 없다.
    백그라운드 스윕인지 외부 호출(대시보드 방문 등)인지 이 모듈로는 알 수 없다.
    이 모듈은 **관측된 사실만** 적재하며, 트리거를 추정해 채우지 않는다.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("tiktok.token_history")

VERSION = "1.0.0"
ENV_PATH = "HERO_TOKEN_HISTORY_FILE"
ENV_NEXT_RUN = "HERO_TIKTOK_NEXT_RUN_AT"
DEFAULT_FILENAME = "tiktok_token_history.json"

# 갱신 간격 실측(2026-10-14 12:26:47Z → 2026-10-15 13:07:48Z = 24h41m).
# 이 허용치를 넘는 관측 gap 은 "지연"이 아니라 "누락"으로 보고 연속 성공을 끊는다.
DEFAULT_INTERVAL_HOURS = 24.0
INTERVAL_TOLERANCE_HOURS = 30.0

# 경보 중복 억제 창(시간). 차단은 짧게(별개 사건일 수 있음), 경고는 길게(같은 회차 중복 호출).
DEFAULT_THROTTLE_BLOCKING_HOURS = 1.0
DEFAULT_THROTTLE_WARN_HOURS = 6.0

BLOCKING_STATUSES = ("expired", "needs_reconnect", "inactive", "missing")

_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
_LINE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_STATUS_RE = re.compile(r"\b(expired|expiring|needs_reconnect|inactive|unavailable|missing|ok)\b")
_HOURS_RE = re.compile(r"남은\s*(-?\d+(?:\.\d+)?)\s*h")
_JSON_STATUS_RE = re.compile(r'"status"\s*:\s*"([a-z_]+)"')
_BARE_EXPIRY_RE = re.compile(r"expires_at\s*[=:]\s*(\d{4}-\d{2}-\d{2}T[0-9:.]+Z?)")
_KO_STATUS = (("만료 임박", "expiring"), ("만료", "expired"))
_KNOWN_STATUS = ("ok", "expiring", "expired", "needs_reconnect", "inactive", "missing",
                 "unavailable")
_EXPIRY_PATTERNS = (
    re.compile(r'"expires_at"\s*:\s*"([^"]+)"'),
    re.compile(r'"tokenExpiresAt"\s*:\s*"([^"]+)"'),
    re.compile(r"tokenExpiresAt\s*[=:]\s*(\d{4}-\d{2}-\d{2}T[0-9:.]+Z?)"),
    re.compile(r"만료시각\s*[=:]\s*(\d{4}-\d{2}-\d{2}T[0-9:.]+Z?)"),
    re.compile(r"만료:\s*(\d{4}-\d{2}-\d{2}T[0-9:.]+Z?)"),
    _BARE_EXPIRY_RE,
)


# ────────────────────────────── 경로·직렬화 ──────────────────────────────

def history_path(explicit=None) -> Path:
    """인자 > HERO_TOKEN_HISTORY_FILE > gen/data/tiktok_token_history.json."""
    if explicit:
        return Path(explicit)
    env = os.getenv(ENV_PATH)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / DEFAULT_FILENAME


def _empty() -> dict:
    return {"version": VERSION, "observations": [], "alerts": []}


def load(path=None) -> dict:
    """이력 로드. 파일 부재/손상 시 빈 구조(예외 전파 안 함)."""
    p = history_path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except Exception as exc:  # noqa: BLE001
        log.warning("토큰 이력 파싱 실패(%s) — 신규 이력으로 시작: %s", p, exc)
        return _empty()
    if isinstance(raw, list):                 # 구버전(관측 리스트) 호환
        return {"version": VERSION, "observations": raw, "alerts": []}
    if not isinstance(raw, dict):
        return _empty()
    raw.setdefault("version", VERSION)
    raw.setdefault("observations", [])
    raw.setdefault("alerts", [])
    return raw


def save(data: dict, path=None) -> bool:
    """원자적 저장(tmp → replace). 실패 시 False(예외 전파 안 함)."""
    p = history_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("토큰 이력 저장 실패(무시): %s — %s", p, exc)
        return False


def mirror(path=None) -> bool:
    """AI Drive(/hero-shorts) 미러링 — best-effort. statestore 재사용."""
    try:
        from ..statestore import sync_states
        return bool(sync_states([history_path(path)]))
    except Exception as exc:  # noqa: BLE001
        log.warning("토큰 이력 미러링 실패(무시): %s", exc)
        return False


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(ts):
    if not ts:
        return None
    text = str(ts).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _hours_between(a, b):
    da, db = _parse(a), _parse(b)
    if da is None or db is None:
        return None
    return round((db - da).total_seconds() / 3600.0, 2)


# ────────────────────────────── 관측 적재 ──────────────────────────────

def observation_from_health(health, source="", now=None) -> dict:
    """TokenHealth(또는 to_dict 결과) → 관측 1건."""
    d = health.to_dict() if hasattr(health, "to_dict") else dict(health)
    ts = _iso(now or datetime.now(UTC))
    hl = d.get("hours_left")
    return {
        "first_seen": ts,
        "last_seen": ts,
        "runs": 1,
        "status": d.get("status"),
        "blocking": bool(d.get("blocking")),
        "expires_at": d.get("expires_at"),
        "hours_left_min": hl,
        "hours_left_last": hl,
        "account_id": d.get("account_id"),
        "username": d.get("username"),
        "profile_id": d.get("profile_id"),
        "sources": [source] if source else [],
    }


def _same_bucket(a: dict, b: dict) -> bool:
    return (a.get("expires_at"), a.get("status")) == (b.get("expires_at"), b.get("status"))


def _merge_into(target: dict, obs: dict) -> None:
    target["last_seen"] = obs["last_seen"]
    target["runs"] = int(target.get("runs") or 1) + int(obs.get("runs") or 1)
    hl = obs.get("hours_left_last")
    if hl is not None:
        m = target.get("hours_left_min")
        target["hours_left_min"] = hl if m is None else min(m, hl)
        target["hours_left_last"] = hl
    for s in obs.get("sources") or []:
        lst = target.setdefault("sources", [])
        if s not in lst:
            lst.append(s)
    for k in ("account_id", "username", "profile_id"):
        if not target.get(k) and obs.get(k):
            target[k] = obs[k]


def record(health, source="", path=None, now=None, do_mirror=False) -> dict:
    """관측 1건 적재. 절대 예외를 올리지 않는다(이력 실패로 발행 차단 금지)."""
    try:
        data = load(path)
        obs = observation_from_health(health, source=source, now=now)
        obs_list = data["observations"]
        appended = True
        if obs_list and _same_bucket(obs_list[-1], obs):
            _merge_into(obs_list[-1], obs)
            appended = False
        else:
            obs_list.append(obs)
        data["version"] = VERSION
        saved = save(data, path)
        rn = renewals(obs_list)
        last_rn = rn[-1] if rn else None
        if do_mirror:
            mirror(path)
        return {
            "appended": appended,
            "saved": saved,
            "observations": len(obs_list),
            "renewal_detected": bool(appended and last_rn
                                     and last_rn.get("to_first_seen") == obs["first_seen"]),
            "last_renewal": last_rn,
            "path": str(history_path(path)),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("토큰 이력 적재 실패(무시 — 판정은 유지): %s", exc)
        return {"appended": False, "saved": False, "error": str(exc),
                "path": str(history_path(path))}


def record_alert(status, channel, sent, path=None, now=None, detail=None) -> None:
    """경보 발송 이력 기록(중복 억제 근거). 실패는 무시."""
    try:
        data = load(path)
        data.setdefault("alerts", []).append({
            "ts": _iso(now or datetime.now(UTC)),
            "status": status,
            "channel": channel,
            "sent": bool(sent),
            "blocking": status in BLOCKING_STATUSES,
            "detail": detail,
        })
        data["alerts"] = data["alerts"][-200:]
        save(data, path)
    except Exception as exc:  # noqa: BLE001
        log.warning("경보 이력 기록 실패(무시): %s", exc)


# ────────────────────────────── 분석 ──────────────────────────────

def renewals(observations) -> list:
    """만료시각이 바뀐 지점 = 갱신 이벤트."""
    out = []
    prev = None
    for o in observations or []:
        cur = o.get("expires_at")
        p = (prev or {}).get("expires_at")
        if cur and p and cur != p:
            delta = _hours_between(p, cur)
            out.append({
                "from_expires_at": p,
                "to_expires_at": cur,
                "from_first_seen": prev.get("first_seen"),
                "to_first_seen": o.get("first_seen"),
                "expiry_delta_hours": delta,
                "observed_gap_hours": _hours_between(prev.get("first_seen"), o.get("first_seen")),
                "regression": bool(delta is not None and delta < 0),
                "status_at_renewal": o.get("status"),
            })
        prev = o
    return out


def summarize(observations) -> dict:
    """갱신 횟수·간격·연속 성공·최대 관측 간격."""
    observations = observations or []
    rn = renewals(observations)
    intervals = [r["expiry_delta_hours"] for r in rn if r.get("expiry_delta_hours") is not None]
    gaps = [r["observed_gap_hours"] for r in rn if r.get("observed_gap_hours") is not None]

    streak, broken_at = 0, None
    for r in reversed(rn):                       # 최근부터 연속 성공 계산
        if r.get("regression"):
            broken_at = r.get("to_first_seen")
            break
        gap = r.get("observed_gap_hours")
        if gap is not None and gap > INTERVAL_TOLERANCE_HOURS:
            broken_at = r.get("to_first_seen")
            break
        streak += 1

    last = observations[-1] if observations else {}
    expired_n = sum(1 for o in observations if o.get("status") == "expired")
    return {
        "history_version": VERSION,
        "observations": len(observations),
        "first_seen": (observations[0].get("first_seen") if observations else None),
        "last_seen": last.get("last_seen"),
        "last_status": last.get("status"),
        "last_expires_at": last.get("expires_at"),
        "last_hours_left": last.get("hours_left_last"),
        "renewals": len(rn),
        "renewal_intervals_hours": intervals,
        "median_interval_hours": (sorted(intervals)[len(intervals) // 2] if intervals else None),
        "max_observed_gap_hours": (max(gaps) if gaps else None),
        "consecutive_renewals": streak,
        "streak_broken_at": broken_at,
        "expired_observations": expired_n,
        "anomalies": [r for r in rn if r.get("regression")],
    }


def verify_until(observations, until, now=None, grace_hours=0.0) -> dict:
    """목표 실행시각까지 필요한 갱신 횟수 vs 관측된 연속 성공 횟수.

    verdict
        ok           목표 시각이 현재 만료 이전 — 추가 갱신 불필요
        proven       필요 횟수 ≤ 연속 성공 횟수(관측 근거 있음)
        partial      이력 부족(추정 구간 포함) 또는 연속 기록이 끊김
        unknown      만료시각/목표시각 파싱 불가
    """
    until_dt = _parse(until)
    s = summarize(observations)
    out = {"until": _iso(until_dt) if until_dt else None, "summary": s}
    last_exp = _parse(s.get("last_expires_at"))
    interval = s.get("median_interval_hours") or DEFAULT_INTERVAL_HOURS
    if until_dt is None or last_exp is None:
        out.update({"verdict": "unknown", "renewals_needed": None,
                    "consecutive_renewals": s["consecutive_renewals"],
                    "assumed_interval_hours": interval,
                    "reason": "만료시각 또는 목표시각 파싱 불가 — 판정 불가"})
        return out
    span = (until_dt - last_exp).total_seconds() / 3600.0 - float(grace_hours or 0.0)
    needed = max(0, math.ceil(span / interval - 1e-9)) if span > 0 else 0
    out.update({"renewals_needed": needed,
                "consecutive_renewals": s["consecutive_renewals"],
                "assumed_interval_hours": interval,
                "hours_from_last_expiry_to_target": round(span, 2)})
    if needed == 0:
        out.update({"verdict": "ok",
                    "reason": "목표 시각이 현재 토큰 만료 이전 — 추가 갱신 불필요"})
    elif s.get("streak_broken_at"):
        out.update({"verdict": "partial",
                    "reason": f"연속 성공 기록이 {s['streak_broken_at']} 에서 끊겼다"})
    elif s["consecutive_renewals"] >= needed:
        out.update({"verdict": "proven",
                    "reason": f"필요 {needed}회 ≤ 관측 연속 성공 {s['consecutive_renewals']}회"})
    else:
        out.update({"verdict": "partial",
                    "reason": f"필요 {needed}회 > 관측 연속 성공 {s['consecutive_renewals']}회"
                              " — 이력 부족(성공 확률은 추정하지 않는다)"})
    return out


def format_report(summary: dict, verify: dict | None = None) -> str:
    """사람이 읽는 리포트 텍스트."""
    iv = summary.get("renewal_intervals_hours") or []
    lines = [
        f"[hero-shorts] 틱톡 토큰 이력 — 관측 {summary.get('observations')}건 "
        f"({summary.get('first_seen') or '-'} ~ {summary.get('last_seen') or '-'})",
        f"현재: {str(summary.get('last_status')).upper()} · 만료 {summary.get('last_expires_at') or '-'}"
        f" (남은 {summary.get('last_hours_left')}h)",
        f"갱신: {summary.get('renewals')}회 · 연속 성공 {summary.get('consecutive_renewals')}회"
        + (f" · 기록 끊김({summary.get('streak_broken_at')})" if summary.get("streak_broken_at") else ""),
        f"간격: {iv if iv else '관측 없음'}"
        + (f" · 중앙값 {summary.get('median_interval_hours')}h" if iv else ""),
        f"만료 상태 관측: {summary.get('expired_observations')}회",
    ]
    if verify:
        lines.append(f"목표({verify.get('until')}) 판정: {str(verify.get('verdict')).upper()} — "
                     f"필요 {verify.get('renewals_needed')}회 / 연속 {verify.get('consecutive_renewals')}회"
                     f" · {verify.get('reason')}")
    if summary.get("anomalies"):
        lines.append(f"이상: 만료시각 역행 {len(summary['anomalies'])}건")
    return "\n".join(lines)


# ────────────────────────────── 경보 중복 억제 ──────────────────────────────

def _throttle_hours(blocking: bool) -> float:
    for name in ("HERO_TIKTOK_ALERT_THROTTLE_HOURS",
                 "HERO_TIKTOK_ALERT_THROTTLE_BLOCKING_HOURS" if blocking
                 else "HERO_TIKTOK_ALERT_THROTTLE_WARN_HOURS"):
        env = os.getenv(name)
        if env:
            try:
                return float(env)
            except ValueError:
                log.warning("%s 파싱 실패(%r) — 기본값 사용", name, env)
    return DEFAULT_THROTTLE_BLOCKING_HOURS if blocking else DEFAULT_THROTTLE_WARN_HOURS


def last_alert(status, path=None):
    data = load(path)
    for a in reversed(data.get("alerts") or []):
        if a.get("status") == status:
            return a
    return None


def should_alert(status, blocking=False, path=None, now=None):
    """(발송 여부, 사유). 동일 상태 경보가 억제 창 안이면 False."""
    if status == "ok":
        return False, "ok 상태는 경보 대상 아님"
    la = last_alert(status, path=path)
    win = _throttle_hours(blocking)
    if not la:
        return True, "동일 상태 경보 이력 없음"
    age = _hours_between(la.get("ts"), _iso(now or datetime.now(UTC)))
    if age is None:
        return True, "이전 경보 시각 파싱 불가 — 발송"
    if age >= win:
        return True, f"이전 경보 {age}h 경과(≥ {win}h)"
    return False, f"이전 경보 {age}h 전(< {win}h) — 중복 억제"


# ────────────────────────────── 소급(backfill) ──────────────────────────────

def _expiry_in_line(line):
    for pat in _EXPIRY_PATTERNS:
        m = pat.search(line)
        if m:
            return m.group(1).strip().strip('"')
    return None


def _status_in_line(line, hours_raw=None):
    """로그 한 줄에서 상태 추정. JSON status → 한국어 → 영문 → 잔여시간 부호 순."""
    m = _JSON_STATUS_RE.search(line)
    if m and m.group(1) in _KNOWN_STATUS:
        return m.group(1)
    for ko, st in _KO_STATUS:
        if ko in line:
            return st
    m2 = _STATUS_RE.search(line)
    if m2:
        return m2.group(1)
    if hours_raw is not None:
        try:
            return "expired" if float(hours_raw) <= 0 else "ok"
        except ValueError:
            return None
    return None


def backfill(entries, path=None) -> dict:
    """기존 실행 로그에서 만료시각을 긁어 이력에 편입.

    entries: [{"name":..,"text":..}] | [(name, text)] | [text]
    관측 시각(first_seen)은 로그 줄 선두 타임스탬프가 있을 때만 채운다 —
    없으면 None 으로 두고 간격 계산에서 제외한다(추정으로 채우지 않는다).
    """
    data = load(path)
    obs_list = data["observations"]
    added = 0
    for name, text in _norm_entries(entries):
        for line in str(text).splitlines():
            exp = _expiry_in_line(line)
            if not exp:
                continue
            m_ts = _LINE_TS_RE.match(line.strip())
            stamp = None
            if m_ts:
                parsed = _parse(m_ts.group(1).replace(" ", "T"))
                stamp = _iso(parsed) if parsed else None
            m_h = _HOURS_RE.search(line)
            st = _status_in_line(line, m_h.group(1) if m_h else None)
            obs = {
                "first_seen": stamp,
                "last_seen": stamp,
                "runs": 1,
                "status": st or "unknown",
                "blocking": bool(st in BLOCKING_STATUSES),
                "expires_at": exp,
                "hours_left_min": float(m_h.group(1)) if m_h else None,
                "hours_left_last": float(m_h.group(1)) if m_h else None,
                "account_id": None, "username": None, "profile_id": None,
                "sources": [f"backfill:{name}"],
                "backfilled": True,
            }
            if obs_list and _same_bucket(obs_list[-1], obs):
                _merge_into(obs_list[-1], obs)
            else:
                obs_list.append(obs)
                added += 1
    data["observations"] = _sort_observations(obs_list)
    data["observations"] = _dedupe(data["observations"])
    save(data, path)
    return {"added": added, "observations": len(data["observations"]),
            "path": str(history_path(path))}


def _norm_entries(entries):
    out = []
    for e in entries or []:
        if isinstance(e, dict):
            out.append((e.get("name") or "log", e.get("text") or ""))
        elif isinstance(e, (tuple, list)) and len(e) == 2:
            out.append((str(e[0]), str(e[1])))
        else:
            out.append((str(e), str(e)))
    return out


def _sort_observations(obs_list):
    return sorted(obs_list, key=lambda o: (o.get("first_seen") is None, o.get("first_seen") or ""))


def _dedupe(obs_list):
    out = []
    for o in obs_list:
        if out and _same_bucket(out[-1], o):
            _merge_into(out[-1], o)
        else:
            out.append(o)
    return out


def prune(path=None, keep=500) -> dict:
    data = load(path)
    obs = _dedupe(_sort_observations(data["observations"]))
    data["observations"] = obs[-int(keep):]
    data["alerts"] = (data.get("alerts") or [])[-200:]
    save(data, path)
    return {"observations": len(data["observations"]), "alerts": len(data["alerts"]),
            "path": str(history_path(path))}


# ────────────────────────────── CLI ──────────────────────────────

def _cmd_record(args) -> int:
    from . import account as tk_account
    health = tk_account.check_token_health(account_id=args.account_id,
                                           warn_hours=args.warn_hours)
    res = record(health, source=args.source, path=args.path, do_mirror=args.mirror)
    payload = {"health": health.to_dict(), "history": res,
               "summary": summarize(load(args.path)["observations"])}
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if not health.blocking else 70


def _cmd_report(args) -> int:
    obs = load(args.path)["observations"]
    s = summarize(obs)
    v = verify_until(obs, args.until) if args.until else None
    if args.json:
        print(json.dumps({"summary": s, "verify": v}, ensure_ascii=False, indent=1))
    else:
        print(format_report(s, v))
    return 0


def _cmd_backfill(args) -> int:
    entries = []
    for f in args.files:
        fp = Path(f)
        try:
            entries.append({"name": fp.name, "text": fp.read_text(encoding="utf-8", errors="replace")})
        except Exception as exc:  # noqa: BLE001
            print(f"경고: {f} 읽기 실패 — {exc}", file=sys.stderr)
    res = backfill(entries, path=args.path)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="token_history", description="틱톡 토큰 만료시각 이력")
    ap.add_argument("action", choices=["record", "report", "backfill", "prune"], nargs="?",
                    default="report")
    ap.add_argument("--path", default=None)
    ap.add_argument("--account-id", default=None)
    ap.add_argument("--warn-hours", type=float, default=None)
    ap.add_argument("--source", default="cli.token_history")
    ap.add_argument("--mirror", action="store_true", help="record 후 AI Drive 미러링")
    ap.add_argument("--until", default=None, help="report: 목표 실행시각 ISO8601")
    ap.add_argument("--files", nargs="*", default=[], help="backfill: 로그 파일 경로")
    ap.add_argument("--keep", type=int, default=500, help="prune: 보존 관측 수")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    if args.action == "record":
        return _cmd_record(args)
    if args.action == "report":
        return _cmd_report(args)
    if args.action == "backfill":
        return _cmd_backfill(args)
    print(json.dumps(prune(args.path, args.keep), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
