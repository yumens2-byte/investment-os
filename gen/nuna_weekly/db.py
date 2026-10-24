"""
gen/nuna_weekly/db.py — 원장 저장소 (Supabase PostgREST 직접 호출)
====================================================================
supabase-py 를 쓰지 않는다: supabase==2.10.0 과 google-genai>=2.25 가 의존성 충돌한다
(2026-10-19 pip 해석 실패 실측). PostgREST REST 를 requests 로 직접 호출한다.

보안 (설계서 15장):
    S-05 — 서버용 DB 키는 SUPABASE_SERVICE_ROLE_KEY Secret을 사용 — 이 모듈만 읽는다.
    S-06 — 테이블 허용 목록 밖 접근은 호출 전에 예외.

테스트·DRY_RUN 용 MemoryStore 는 RestStore 와 동일한 의미(상한·조건부 전이)를 구현한다.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from . import secret_env

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.db")

WRITE_TABLES = frozenset({"nuna_episodes", "nuna_publish_targets", "nuna_cost_ledger", "nuna_step_runs"})
READ_TABLES = WRITE_TABLES | frozenset({"daily_snapshots", "daily_news", "daily_analysis"})
RPC_ALLOWED = frozenset({"nuna_reserve_cost"})
SUPABASE_KEY_ENV = "SUPABASE_SERVICE_ROLE_KEY"


class StoreError(RuntimeError):
    """DB 호출 실패 (메시지는 마스킹됨)."""


class BudgetExceeded(StoreError):
    """nuna_reserve_cost 가 상한 초과로 거부."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Store(Protocol):
    def get_episode(self, episode_id: str) -> dict[str, Any] | None: ...
    def create_episode(self, row: dict[str, Any]) -> dict[str, Any]: ...
    def transition(self, episode_id: str, expect: Iterable[str], new_status: str,
                   fields: dict[str, Any] | None = None) -> bool: ...
    def update_episode(self, episode_id: str, fields: dict[str, Any]) -> None: ...
    def reserve_cost(self, episode_id: str, step: str, provider: str, model: str,
                     units: float, unit: str, usd: float, fx_rate: float,
                     episode_cap_krw: int, month_cap_krw: int,
                     request_id: str | None = None) -> int: ...
    def settle_cost(self, cost_id: int, state: str, usd: float | None = None,
                    units: float | None = None, fx_rate: float | None = None,
                    request_id: str | None = None) -> None: ...
    def month_spent_krw(self) -> int: ...
    def upsert_target(self, row: dict[str, Any]) -> None: ...
    def update_target(self, episode_id: str, platform: str, fields: dict[str, Any]) -> None: ...
    def list_targets(self, episode_id: str) -> list[dict[str, Any]]: ...
    def step_run(self, row: dict[str, Any]) -> None: ...
    def select_by_dates(self, table: str, date_col: str, dates: list[str]) -> list[dict[str, Any]]: ...
    def latest_row(self, table: str, date_col: str, on_or_before: str) -> dict[str, Any] | None: ...


# --------------------------------------------------------------------------- #
# PostgREST 구현
# --------------------------------------------------------------------------- #
class RestStore:
    def __init__(self, url: str | None = None, key: str | None = None,
                 session=None, timeout: int = 20) -> None:
        import requests

        self.base = (url or secret_env.require("SUPABASE_URL")).rstrip("/") + "/rest/v1"
        self._key = key or secret_env.require(SUPABASE_KEY_ENV)
        secret_env.register(self._key)
        self.s = session or requests.Session()
        self.timeout = timeout

    # ── 공통 ──
    def _key_type(self) -> str:
        if self._key.startswith("sb_secret_"):
            return "secret"
        if self._key.startswith("sb_publishable_"):
            return "publishable"
        return "legacy_jwt" if self._key.count(".") == 2 else "unknown"

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        h = {"apikey": self._key, "Content-Type": "application/json", "Accept": "application/json"}
        # 신형 API 키는 JWT가 아니다. 기존 JWT 키만 Bearer 헤더에 전달한다.
        if self._key_type() not in ("secret", "publishable"):
            h["Authorization"] = f"Bearer {self._key}"
        if prefer:
            h["Prefer"] = prefer
        return h

    def check_access(self) -> dict[str, Any]:
        """현재 Actions 키로 실제 읽기 권한 검사. 0행 조회만 하며 원장·비용을 쓰지 않는다."""
        checked = []
        for table in sorted(READ_TABLES):
            self._call("GET", table, params={"select": "*", "limit": "0"})
            checked.append(table)
        return {"key_source": SUPABASE_KEY_ENV, "key_type": self._key_type(),
                "db_host": urlsplit(self.base).hostname, "readable_tables": checked,
                "write_permission": "not_tested"}

    @staticmethod
    def _check_table(table: str, write: bool) -> None:
        allowed = WRITE_TABLES if write else READ_TABLES
        if table not in allowed:
            raise StoreError(f"허용되지 않은 테이블 접근 차단(S-06): {table} write={write}")

    def _call(self, method: str, path: str, *, params: dict[str, str] | None = None,
              body: Any = None, prefer: str | None = None) -> Any:
        try:
            r = self.s.request(method, f"{self.base}/{path}", params=params,
                               data=None if body is None else json.dumps(body, ensure_ascii=False, default=str),
                               headers=self._headers(prefer), timeout=self.timeout)
        except Exception as e:
            raise StoreError(secret_env.redact(f"DB 호출 실패 {method} {path}: {type(e).__name__}: {e}", 300)) from None
        if r.status_code >= 400:
            text = secret_env.redact(r.text, 500)
            if "NUNA_BUDGET_EXCEEDED" in r.text:
                raise BudgetExceeded(text)
            try:
                body = r.json()
            except Exception:
                body = {}
            if isinstance(body, dict) and body.get("code") == "42501":
                raise StoreError(
                    f"DB_PERMISSION_DENIED: HTTP {r.status_code} {method} {path}; "
                    f"key_source={SUPABASE_KEY_ENV}, key_type={self._key_type()}, "
                    f"db_host={urlsplit(self.base).hostname}. "
                    f"YAML과 코드의 키 이름은 {SUPABASE_KEY_ENV}입니다. Secret 값의 프로젝트·서버 권한을 확인하세요. "
                    "키 이름 변경만으로 DB 권한은 변경되지 않습니다.")
            raise StoreError(f"DB HTTP {r.status_code} {method} {path}: {text}")
        if not r.text.strip():
            return None
        return r.json()

    # ── 회차 ──
    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        rows = self._call("GET", "nuna_episodes", params={"episode_id": f"eq.{episode_id}", "select": "*"})
        return rows[0] if rows else None

    def create_episode(self, row: dict[str, Any]) -> dict[str, Any]:
        self._check_table("nuna_episodes", True)
        rows = self._call("POST", "nuna_episodes", params={"on_conflict": "episode_id"}, body=row,
                          prefer="resolution=ignore-duplicates,return=representation")
        return (rows or [None])[0] or self.get_episode(row["episode_id"]) or {}

    def transition(self, episode_id: str, expect: Iterable[str], new_status: str,
                   fields: dict[str, Any] | None = None) -> bool:
        exp = ",".join(quote(s) for s in expect)
        body = dict(fields or {})
        body["status"] = new_status
        rows = self._call("PATCH", "nuna_episodes",
                          params={"episode_id": f"eq.{episode_id}", "status": f"in.({exp})"},
                          body=body, prefer="return=representation")
        return bool(rows)

    def update_episode(self, episode_id: str, fields: dict[str, Any]) -> None:
        if "status" in fields:
            raise StoreError("status 변경은 transition() 으로만 — 조건부 전이 원칙")
        self._call("PATCH", "nuna_episodes", params={"episode_id": f"eq.{episode_id}"},
                   body=fields, prefer="return=minimal")

    # ── 비용 ──
    def reserve_cost(self, episode_id, step, provider, model, units, unit, usd, fx_rate,
                     episode_cap_krw, month_cap_krw, request_id=None) -> int:
        if "nuna_reserve_cost" not in RPC_ALLOWED:  # pragma: no cover
            raise StoreError("RPC 차단")
        res = self._call("POST", "rpc/nuna_reserve_cost", body={
            "p_episode_id": episode_id, "p_step": step, "p_provider": provider, "p_model": model,
            "p_units": units, "p_unit": unit, "p_usd": round(float(usd), 4), "p_fx_rate": round(float(fx_rate), 2),
            "p_episode_cap_krw": episode_cap_krw, "p_month_cap_krw": month_cap_krw,
            "p_request_id": request_id})
        return int(res)

    def settle_cost(self, cost_id, state, usd=None, units=None, fx_rate=None, request_id=None) -> None:
        if state not in ("charged", "released"):
            raise StoreError(f"정산 상태 오류: {state}")
        body: dict[str, Any] = {"state": state}
        if usd is not None and fx_rate is not None:
            body["usd"] = round(float(usd), 4)
            body["krw"] = round(float(usd) * float(fx_rate))
        if units is not None:
            body["units"] = units
        if request_id:
            body["request_id"] = request_id
        self._call("PATCH", "nuna_cost_ledger", params={"id": f"eq.{int(cost_id)}"},
                   body=body, prefer="return=minimal")

    def month_spent_krw(self) -> int:
        from .config import KST

        now = datetime.now(KST)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        rows = self._call("GET", "nuna_cost_ledger",
                          params={"select": "krw", "created_at": f"gte.{start}",
                                  "state": "in.(reserved,charged)"})
        return int(sum(float(r["krw"]) for r in rows or []))

    # ── 채널 ──
    def upsert_target(self, row: dict[str, Any]) -> None:
        self._call("POST", "nuna_publish_targets", params={"on_conflict": "episode_id,platform"},
                   body=row, prefer="resolution=merge-duplicates,return=minimal")

    def update_target(self, episode_id: str, platform: str, fields: dict[str, Any]) -> None:
        self._call("PATCH", "nuna_publish_targets",
                   params={"episode_id": f"eq.{episode_id}", "platform": f"eq.{platform}"},
                   body=fields, prefer="return=minimal")

    def list_targets(self, episode_id: str) -> list[dict[str, Any]]:
        return self._call("GET", "nuna_publish_targets",
                          params={"episode_id": f"eq.{episode_id}", "select": "*", "order": "platform"}) or []

    # ── 실행 로그 ──
    def step_run(self, row: dict[str, Any]) -> None:
        self._call("POST", "nuna_step_runs", body=row, prefer="return=minimal")

    # ── 읽기 전용 (daily_*) ──
    def select_by_dates(self, table: str, date_col: str, dates: list[str]) -> list[dict[str, Any]]:
        self._check_table(table, False)
        if not dates:
            return []
        return self._call("GET", table, params={date_col: f"in.({','.join(dates)})", "select": "*"}) or []

    def latest_row(self, table: str, date_col: str, on_or_before: str) -> dict[str, Any] | None:
        self._check_table(table, False)
        rows = self._call("GET", table, params={date_col: f"lte.{on_or_before}", "select": "*",
                                                "order": f"{date_col}.desc", "limit": "1"})
        return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# 메모리 구현 — 테스트·DRY_RUN. DB 함수·트리거와 같은 규칙을 강제한다.
# --------------------------------------------------------------------------- #
class MemoryStore:
    def __init__(self, daily: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.episodes: dict[str, dict[str, Any]] = {}
        self.targets: dict[tuple, dict[str, Any]] = {}
        self.costs: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.daily = daily or {}
        self._lock = threading.Lock()

    def get_episode(self, episode_id):
        e = self.episodes.get(episode_id)
        return dict(e) if e else None

    def create_episode(self, row):
        with self._lock:
            if row["episode_id"] not in self.episodes:
                r = {"status": "PLANNED", "created_at": _now_iso(), **row}
                self.episodes[row["episode_id"]] = r
            return dict(self.episodes[row["episode_id"]])

    def transition(self, episode_id, expect, new_status, fields=None):
        with self._lock:
            e = self.episodes.get(episode_id)
            if not e or e.get("status") not in set(expect):
                return False
            e.update(fields or {})
            e["status"] = new_status
            e["updated_at"] = _now_iso()
            return True

    def update_episode(self, episode_id, fields):
        if "status" in fields:
            raise StoreError("status 변경은 transition() 으로만 — 조건부 전이 원칙")
        with self._lock:
            self.episodes[episode_id].update(fields)

    def _sum(self, pred) -> float:
        return sum(c["krw"] for c in self.costs if c["state"] in ("reserved", "charged") and pred(c))

    def reserve_cost(self, episode_id, step, provider, model, units, unit, usd, fx_rate,
                     episode_cap_krw, month_cap_krw, request_id=None):
        with self._lock:
            krw = round(float(usd) * float(fx_rate))
            ep = self._sum(lambda c: c["episode_id"] == episode_id)
            mo = self._sum(lambda c: True)
            if ep + krw > episode_cap_krw:
                raise BudgetExceeded(f"NUNA_BUDGET_EXCEEDED: episode {episode_id} used {ep} + need {krw} > cap {episode_cap_krw}")
            if mo + krw > month_cap_krw:
                raise BudgetExceeded(f"NUNA_BUDGET_EXCEEDED: month used {mo} + need {krw} > cap {month_cap_krw}")
            if request_id and any(c.get("request_id") == request_id for c in self.costs):
                raise StoreError("request_id 중복")
            cid = len(self.costs) + 1
            self.costs.append({"id": cid, "episode_id": episode_id, "step": step, "provider": provider,
                               "model": model, "units": units, "unit": unit, "usd": float(usd),
                               "fx_rate": float(fx_rate), "krw": krw, "state": "reserved",
                               "request_id": request_id})
            return cid

    def settle_cost(self, cost_id, state, usd=None, units=None, fx_rate=None, request_id=None):
        with self._lock:
            c = next(x for x in self.costs if x["id"] == cost_id)
            if c["state"] != "reserved":
                raise StoreError(f"NUNA_COST_LEDGER_SETTLED: row {cost_id} already {c['state']}")
            if state not in ("charged", "released"):
                raise StoreError(f"정산 상태 오류: {state}")
            c["state"] = state
            if usd is not None and fx_rate is not None:
                c["usd"] = float(usd)
                c["krw"] = round(float(usd) * float(fx_rate))
            if units is not None:
                c["units"] = units
            if request_id:
                c["request_id"] = request_id

    def month_spent_krw(self):
        return int(self._sum(lambda c: True))

    def upsert_target(self, row):
        with self._lock:
            key = (row["episode_id"], row["platform"])
            if any(t["idempotency_key"] == row["idempotency_key"] and k != key
                   for k, t in self.targets.items()):
                raise StoreError("idempotency_key 중복")
            self.targets.setdefault(key, {"status": "pending", "attempts": 0}).update(row)

    def update_target(self, episode_id, platform, fields):
        with self._lock:
            self.targets[(episode_id, platform)].update(fields)

    def list_targets(self, episode_id):
        return [dict(v) for (e, _), v in sorted(self.targets.items()) if e == episode_id]

    def step_run(self, row):
        self.runs.append(dict(row))

    def select_by_dates(self, table, date_col, dates):
        if table not in READ_TABLES:
            raise StoreError(f"허용되지 않은 테이블 접근 차단(S-06): {table}")
        return [dict(r) for r in self.daily.get(table, []) if str(r.get(date_col)) in set(dates)]

    def latest_row(self, table, date_col, on_or_before):
        if table not in READ_TABLES:
            raise StoreError(f"허용되지 않은 테이블 접근 차단(S-06): {table}")
        rows = [r for r in self.daily.get(table, []) if str(r.get(date_col)) <= on_or_before]
        return dict(max(rows, key=lambda r: str(r[date_col]))) if rows else None


class DryRunStore(MemoryStore):
    """DRY_RUN — 쓰기는 메모리(운영 원장 오염 방지), daily_* 읽기만 실 DB."""

    def __init__(self, reader: RestStore | None) -> None:
        super().__init__()
        self._reader = reader

    def select_by_dates(self, table, date_col, dates):
        if self._reader is None:
            return super().select_by_dates(table, date_col, dates)
        return self._reader.select_by_dates(table, date_col, dates)

    def latest_row(self, table, date_col, on_or_before):
        if self._reader is None:
            return super().latest_row(table, date_col, on_or_before)
        return self._reader.latest_row(table, date_col, on_or_before)


def make_store(dry: bool, memory_only: bool = False) -> Any:
    """dry=True → DryRunStore(쓰기 메모리). memory_only=True → 외부 접속 없음(테스트)."""
    if memory_only:
        return MemoryStore()
    if dry:
        reader = None
        if secret_env.optional("SUPABASE_URL") and secret_env.optional(SUPABASE_KEY_ENV):
            reader = RestStore()
        return DryRunStore(reader)
    return RestStore()
