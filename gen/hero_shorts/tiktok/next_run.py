"""gen/hero_shorts/tiktok/next_run.py — 다음 실행 시각 자동 주입(B안 후속)
================================================================================
문제(2026-10-15 실측)
    러너3 의 토큰 경보 본문에는 "다음 실행(시각) 판정: PARTIAL — 필요 N회" 줄이
    있어야 하는데, 그 시각을 넣어 주는 `HERO_TIKTOK_NEXT_RUN_AT` 훅은 코드에만
    있고 **어디서도 설정되지 않았다**. 따라서 실전 경보에는 그 줄이 나오지 않았다.

해결
    러너가 실행될 때마다 다음 실행 시각을 스스로 해석해 env 에 주입한다.
    우선순위(위가 이김)

      1. PIN   HERO_TIKTOK_NEXT_RUN_PIN=1 + HERO_TIKTOK_NEXT_RUN_AT=<ISO>
               → 운영자 명시 고정. 어떤 경우에도 덮어쓰지 않는다(테스트/CI 용).
      2. schedule  `gsk schedule list` 에서 skill_name==hero-shorts-tiktok-publish
               이고 is_active 인 항목의 next_run_at. **가장 실제에 가깝다**(스케줄러가
               계산한 발화 예정 시각). 0크레딧 CLI 호출.
      3. cron  HERO_TIKTOK_CRON(기본 "30 10 * * 2,4") 을 HERO_TIKTOK_TZ
               (기본 Asia/Seoul) 기준으로 로컬 계산. gsk 부재/실패 시 폴백이며
               외부 의존이 전혀 없다.
      4. cache gen/data/tiktok_next_run.json 의 마지막 해석값(14일 이내).

    즉 gsk 가 막혀도 cron 계산으로 값이 나오므로 "주입 누락"은 발생하지 않는다.

스케줄러 타임존 근거(실측 — 추측 아님)
    2026-10-15 `gsk schedule list` 실측값과 KST 해석이 정확히 일치한다.
      러너3  "30 10 * * 2,4" → next_run_at 2026-10-20T01:32:33Z = 10-20(화) 10:32:33 KST
      러너1  "0  8 * * 2,4" → next_run_at 2026-10-19T23:00:03Z = 10-20(화) 08:00:03 KST
      주간보고 "0 9 * * 6"   → next_run_at 2026-10-17T00:03:37Z = 10-17(토) 09:03:37 KST
    세 건 모두 KST 로 해석해야 요일·시각이 맞는다 → 크론은 KST 기준이다.
    (스케줄러 값은 발화 지연이 누적돼 +0:03~+2:33 드리프트가 있다. 24h 주기 갱신
     판정에는 무의미한 오차이므로 그대로 쓴다.)

한계(추측 금지)
    `gsk schedule list` 는 스케줄러가 가진 값을 그대로 준다. 크론 정의 자체가
    바뀌면(예: 요일 추가) 이 모듈의 기본 크론 상수는 낡는다 — 다만 1순위 출처가
    스케줄러이므로 실제 주입값은 항상 최신이고, cron 은 폴백일 뿐이다.
    스케줄이 paused/no_credit 이면 is_active 필터로 걸러 cron 폴백을 쓴다
    (그 경우 실제 실행은 없지만, 발행이 수동 실행될 수 있으므로 값을 지어내지
     않고 크론 기준 예정 시각을 쓴다).
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("tiktok.next_run")

VERSION = "1.0.0"

ENV_NEXT_RUN = "HERO_TIKTOK_NEXT_RUN_AT"
ENV_SOURCE = "HERO_TIKTOK_NEXT_RUN_SOURCE"
ENV_PIN = "HERO_TIKTOK_NEXT_RUN_PIN"
ENV_CRON = "HERO_TIKTOK_CRON"
ENV_TZ = "HERO_TIKTOK_TZ"
ENV_CACHE = "HERO_TIKTOK_NEXT_RUN_CACHE"
ENV_SCHEDULE_CMD = "HERO_SCHEDULE_CMD"

SKILL_NAME = "hero-shorts-tiktok-publish"
DEFAULT_CRON = "30 10 * * 2,4"
DEFAULT_TZ = "Asia/Seoul"
DEFAULT_CACHE_FILENAME = "tiktok_next_run.json"

SCHEDULE_CMD_TIMEOUT = 25.0
MAX_DAYS_AHEAD = 400
CACHE_MAX_AGE_HOURS = 336.0  # 14일 — 그보다 오래된 캐시는 신뢰하지 않는다
CACHE_VERSION = 1

DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
MONTH_NAMES = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
KO_DOW = ("월", "화", "수", "목", "금", "토", "일")


# ---------------------------------------------------------------- 경로/시각 유틸
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def cache_path(path=None) -> Path:
    """캐시 경로 — 인자 > HERO_TIKTOK_NEXT_RUN_CACHE > <repo>/gen/data/<파일>."""
    if path:
        return Path(path)
    env = os.getenv(ENV_CACHE)
    if env:
        return Path(env)
    return repo_root() / "gen" / "data" / DEFAULT_CACHE_FILENAME


def now_utc():
    return datetime.now(UTC)


def _parse_iso(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso(dt):
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------- cron 파서
def _int_token(token, lo, hi, names=None):
    token = token.strip()
    if names:
        low = token.lower()
        if low in names:
            return names[low]
    try:
        value = int(token)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"크론 필드 숫자가 아님: {token!r}") from exc
    if value == 7 and hi == 7:  # 일요일 표기 7 → 0 정규화 전 통과
        return value
    if not lo <= value <= hi:
        raise ValueError(f"크론 필드 범위 초과: {token!r} ({lo}-{hi})")
    return value


def _parse_field(spec, lo, hi, names=None):
    spec = "" if spec is None else str(spec).strip()
    if not spec:
        raise ValueError("빈 크론 필드")
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"크론 스텝 숫자가 아님: {step_s!r}") from exc
            if step <= 0:
                raise ValueError("크론 스텝은 1 이상이어야 함")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            start = _int_token(a, lo, hi, names)
            end = _int_token(b, lo, hi, names)
        else:
            start = end = _int_token(part, lo, hi, names)
        if start > end:
            raise ValueError(f"역방향 범위는 지원하지 않음: {part!r}")
        out.update(range(start, end + 1, step))
    if not out:
        raise ValueError("빈 크론 필드")
    return out


def parse_cron(expr) -> dict:
    """5필드 크론 파싱(표준: 분 시 일 월 요일). 역방향 범위는 조용히 틀리느니 실패한다."""
    fields = str(expr or "").split()
    if len(fields) != 5:
        raise ValueError(f"크론은 5필드여야 함: {expr!r}")
    minute = _parse_field(fields[0], 0, 59)
    hour = _parse_field(fields[1], 0, 23)
    dom = _parse_field(fields[2], 1, 31)
    month = _parse_field(fields[3], 1, 12, MONTH_NAMES)
    dow_raw = _parse_field(fields[4], 0, 7, DOW_NAMES)
    dow = {0 if d == 7 else d for d in dow_raw}
    return {
        "minute": minute, "hour": hour, "dom": dom, "month": month, "dow": dow,
        "dom_restricted": fields[2].strip() != "*",
        "dow_restricted": fields[4].strip() != "*",
    }


def _day_matches(day, spec):
    if day.month not in spec["month"]:
        return False
    dom_r, dow_r = spec["dom_restricted"], spec["dow_restricted"]
    if not dom_r and not dow_r:
        return True
    dom_hit = day.day in spec["dom"]
    dow_hit = ((day.weekday() + 1) % 7) in spec["dow"]
    if dom_r and dow_r:  # 표준 크론: 둘 다 제한이면 OR
        return dom_hit or dow_hit
    return dom_hit if dom_r else dow_hit


def cron_next(expr, now=None, tz_name=None):
    """크론의 다음 발화 시각(UTC). 파싱 불가면 ValueError."""
    spec = parse_cron(expr)
    tz = ZoneInfo(tz_name or os.getenv(ENV_TZ) or DEFAULT_TZ)
    base = (now or now_utc()).astimezone(tz)
    start = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    hours = sorted(spec["hour"])
    minutes = sorted(spec["minute"])
    for i in range(MAX_DAYS_AHEAD):
        cand_day = day + timedelta(days=i)
        if not _day_matches(cand_day, spec):
            continue
        for h in hours:
            for m in minutes:
                cand = cand_day.replace(hour=h, minute=m)
                if cand >= start:
                    return cand.astimezone(UTC)
    return None


# ---------------------------------------------------------------- 스케줄러 조회
def fetch_schedule(cmd=None, timeout=SCHEDULE_CMD_TIMEOUT):
    """`gsk schedule list` 원시 JSON. 실패는 예외가 아니라 (None, 사유)."""
    argv = shlex.split(cmd or os.getenv(ENV_SCHEDULE_CMD) or "gsk schedule list")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "gsk 실행 파일 없음"
    except subprocess.TimeoutExpired:
        return None, f"schedule 조회 시간 초과({timeout}s)"
    except OSError as exc:  # noqa: BLE001
        return None, f"schedule 실행 실패: {exc}"
    if proc.returncode != 0:
        return None, f"schedule exit {proc.returncode}"
    try:
        return json.loads(proc.stdout), None
    except (TypeError, ValueError) as exc:
        return None, f"schedule JSON 파싱 실패: {exc}"


def from_schedule(now=None, cmd=None, timeout=SCHEDULE_CMD_TIMEOUT):
    """활성 스케줄에서 우리 스킬의 next_run_at. (UTC dt, evidence, 사유)."""
    payload, err = fetch_schedule(cmd=cmd, timeout=timeout)
    if payload is None:
        return None, {}, err
    items = ((payload or {}).get("data") or {}).get("scheduled_skills") or []
    now_dt = now or now_utc()
    best, evidence, seen = None, {}, []
    for it in items:
        if it.get("skill_name") != SKILL_NAME:
            continue
        seen.append({"name": it.get("name"), "active": bool(it.get("is_active")),
                     "state": it.get("state"), "next_run_at": it.get("next_run_at")})
        if not it.get("is_active"):
            continue
        dt = _parse_iso(it.get("next_run_at"))
        if dt is None:
            continue
        if best is None or dt < best:
            best = dt
            evidence = {"schedule_name": it.get("name"),
                        "scheduled_action_id": it.get("scheduled_action_id"),
                        "cron": ((it.get("schedule_config") or {}).get("value")),
                        "raw_next_run_at": it.get("next_run_at")}
    if best is None:
        reason = "활성 스케줄 없음" if seen else f"스킬({SKILL_NAME}) 스케줄 없음"
        return None, {"schedule_candidates": seen}, reason
    if best <= now_dt:
        evidence["stale_schedule_value"] = _iso(best)
        return None, evidence, "스케줄 값이 과거 — cron 폴백"
    evidence["schedule_candidates"] = seen
    return best, evidence, None


# ------------------------------------------------------------------------ 캐시
def load_cache(path=None):
    p = cache_path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("다음 실행 캐시 읽기 실패(무시): %s", exc)
        return None
    dt = _parse_iso((data or {}).get("next_run_at"))
    if dt is None:
        return None
    age = (now_utc() - (_parse_iso((data or {}).get("resolved_at")) or now_utc())).total_seconds() / 3600.0
    data["age_hours"] = round(age, 2)
    data["stale"] = age > CACHE_MAX_AGE_HOURS
    data["_path"] = str(p)
    return data


def save_cache(res, path=None):
    """원자적(tmp → replace). 실패는 예외를 올리지 않는다(주입 실패가 발행을 죽이면 안 됨)."""
    p = cache_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": CACHE_VERSION, "next_run_at": res.get("next_run_at"),
               "source": res.get("source"), "resolved_at": res.get("resolved_at"),
               "cron": res.get("cron"), "tz": res.get("tz"),
               "evidence": res.get("evidence") or {}}
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return str(p)
    except Exception as exc:  # noqa: BLE001
        log.warning("다음 실행 캐시 저장 실패(무시): %s", exc)
        return None


# ----------------------------------------------------------------------- 해석
def resolve(now=None, query=True, cmd=None, timeout=SCHEDULE_CMD_TIMEOUT, cache=None):
    """다음 실행 시각 해석. 항상 dict 반환(예외 없음)."""
    now_dt = now or now_utc()
    cron = os.getenv(ENV_CRON) or DEFAULT_CRON
    tz = os.getenv(ENV_TZ) or DEFAULT_TZ
    res = {"next_run_at": None, "source": "none", "reason": None, "cron": cron, "tz": tz,
           "resolved_at": _iso(now_dt), "evidence": {}, "attempts": []}

    # 1) 운영자 PIN — 최우선, 절대 덮지 않음
    if os.getenv(ENV_PIN) and os.getenv(ENV_NEXT_RUN):
        dt = _parse_iso(os.getenv(ENV_NEXT_RUN))
        if dt is not None:
            res.update({"next_run_at": _iso(dt), "source": "pin",
                        "reason": f"{ENV_PIN} 설정 — 운영자 고정값"})
            return res
        res["attempts"].append("pin: 값 파싱 실패")

    # 2) 스케줄러(가장 실제에 가까움)
    if query:
        dt, evidence, err = from_schedule(now=now_dt, cmd=cmd, timeout=timeout)
        res["evidence"].update(evidence or {})
        if dt is not None:
            res.update({"next_run_at": _iso(dt), "source": "schedule", "reason": None})
            return res
        res["attempts"].append(f"schedule: {err}")

    # 3) 크론 로컬 계산(외부 의존 0)
    try:
        dt = cron_next(cron, now=now_dt, tz_name=tz)
        if dt is not None:
            res.update({"next_run_at": _iso(dt), "source": "cron",
                        "reason": None if not res["attempts"] else "; ".join(res["attempts"])})
            return res
        res["attempts"].append("cron: 400일 내 발화 없음")
    except Exception as exc:  # noqa: BLE001
        res["attempts"].append(f"cron: {exc}")

    # 4) 캐시(신선할 때만)
    cached = load_cache(cache)
    if cached and not cached.get("stale"):
        res.update({"next_run_at": cached["next_run_at"], "source": "cache",
                    "reason": f"캐시 사용({cached.get('age_hours')}h 경과)",
                    "evidence": {**(res["evidence"] or {}), "cache": cached.get("_path")}})
        return res
    if cached and cached.get("stale"):
        res["attempts"].append(f"cache: 오래됨({cached.get('age_hours')}h)")

    res["reason"] = "; ".join(res["attempts"]) or "해석 실패"
    return res


def apply(now=None, query=True, force=False, cmd=None, timeout=SCHEDULE_CMD_TIMEOUT,
          save=None):
    """다음 실행 시각을 env 에 주입. 이미 값이 있으면 force 전까지 그대로 둔다.

    force=True(러너 경로) — 매 실행 재계산. 그래야 스케줄 변경/연기(드리프트)를 따라간다.
    반환 dict 에 reused=True/False 로 재사용 여부를 남긴다.
    """
    current = os.getenv(ENV_NEXT_RUN)
    if os.getenv(ENV_PIN) and current:
        dt = _parse_iso(current)
        if dt is not None:
            return {"next_run_at": _iso(dt), "source": "pin", "reused": True,
                    "reason": f"{ENV_PIN} 설정 — 재계산 안 함"}
    if current and not force:
        return {"next_run_at": current, "source": os.getenv(ENV_SOURCE) or "env",
                "reused": True}
    res = resolve(now=now, query=query, cmd=cmd, timeout=timeout)
    res["reused"] = False
    value = res.get("next_run_at")
    if value:
        os.environ[ENV_NEXT_RUN] = value
        os.environ[ENV_SOURCE] = str(res.get("source") or "")
        if save is not False:
            res["cache_written"] = save_cache(res, path=save)
    elif current:
        res["next_run_at"] = current
        res["reason"] = f"{res.get('reason')}; 기존 env 값 유지"
    return res


def report_line(res, ko=True):
    """사람이 읽는 한 줄. 시각 해석 불가면 없다고 말한다(지어내지 않음)."""
    value = (res or {}).get("next_run_at")
    if not value:
        return f"다음 실행: 미상({(res or {}).get('reason') or '해석 실패'})"
    dt = _parse_iso(value)
    local = dt.astimezone(ZoneInfo((res or {}).get("tz") or DEFAULT_TZ))
    dow = KO_DOW[local.weekday()] if ko else local.strftime("%a")
    if ko:
        return (f"다음 실행: {value} ({local.strftime('%Y-%m-%d %H:%M')} KST·{dow})"
                f" · 출처 {(res or {}).get('source')}")
    return f"next run: {value} ({local.strftime('%Y-%m-%d %H:%M')} KST {dow}) · src {(res or {}).get('source')}"


def main(argv=None):
    """`python -m gen.hero_shorts.tiktok.next_run [--json] [--no-query] [--apply]`"""
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    do_apply = "--apply" in argv
    query = "--no-query" not in argv
    res = apply(force=True, query=query) if do_apply else resolve(query=query)
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(report_line(res))
        if res.get("reason"):
            print(f"사유: {res['reason']}")
    return 0 if res.get("next_run_at") else 3


if __name__ == "__main__":
    sys.exit(main())
