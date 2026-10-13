"""gen/hero_shorts/tiktok/account.py — 틱톡 계정 토큰 preflight
================================================================
발행 **전에** 토큰 유효성을 관측하고, 무효 상태면 발행을 차단한다(fail-closed).

상세설계: tiktok_token_preflight_design_v1.md v1.0

상태값
    ok              정상
    expiring        만료 임박(경고만 — 운영 차단 아님)
    expired         만료
    needs_reconnect 재연결 필요(needsReconnection=True)
    inactive        계정 비활성(isActive=False)
    missing         계정 미발견 또는 계정 ID 미설정
    unavailable     계정 조회 자체 불가(CI 러너에 gsk 부재 등) — 경고만, 차단 안 함

설계 대비 의도적 수정(1건)
    설계 §5는 "ok 아니면 차단"이었다. 그러나 24시간 주기 토큰에 warn_hours=12를
    적용하면 하루의 절반이 상시 차단되어 발행이 사실상 불가능해진다.
    따라서 `expiring` 은 **경고 + 경보**만 하고 차단하지 않는다(실제 무효 상태만 차단).
    BLOCKING 집합 참조.

금지
    `lastSuccessfulPublishAt` 은 판정에 쓰지 않는다. 오늘 6편 발행 성공 후에도
    None 으로 관측됐다(반영 지연) — 신선도 지표로 부적합하다.
"""
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime

log = logging.getLogger("tiktok.account")

VERSION = "1.1.0"
PLATFORM = "tiktok"

DEFAULT_WARN_HOURS = 12.0
ACCOUNT_ENV = "HERO_TIKTOK_ACCOUNT_ID"
WARN_HOURS_ENV = "HERO_TIKTOK_PREFLIGHT_WARN_HOURS"
PREFLIGHT_ENV = "HERO_TIKTOK_PREFLIGHT"

OK = "ok"
EXPIRING = "expiring"
EXPIRED = "expired"
NEEDS_RECONNECT = "needs_reconnect"
INACTIVE = "inactive"
MISSING = "missing"
UNAVAILABLE = "unavailable"

# 실제 발행을 막는 상태. expiring 은 경고만(설계 대비 수정 — 모듈 docstring 참조).
BLOCKING = frozenset({EXPIRED, NEEDS_RECONNECT, INACTIVE, MISSING})
WARN_ONLY = frozenset({EXPIRING, UNAVAILABLE})


@dataclass
class TokenHealth:
    """preflight 판정 결과."""
    ok: bool
    status: str
    account_id: str | None = None
    username: str | None = None
    expires_at: str | None = None
    hours_left: float | None = None
    problems: list = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.status in BLOCKING

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "blocking": self.blocking,
            "account_id": self.account_id,
            "username": self.username,
            "expires_at": self.expires_at,
            "hours_left": self.hours_left,
            "warn_hours": self.hours_left,
            "problems": list(self.problems),
            "version": VERSION,
        }


def preflight_enabled() -> bool:
    """HERO_TIKTOK_PREFLIGHT=0/false/no/off 이면 비활성(비상 우회)."""
    raw = (os.environ.get(PREFLIGHT_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def resolve_warn_hours(warn_hours=None) -> float:
    if warn_hours is not None:
        return float(warn_hours)
    env = os.environ.get(WARN_HOURS_ENV)
    if env:
        try:
            return float(env)
        except ValueError:
            log.warning("%s 파싱 실패(%r) — 기본값 사용", WARN_HOURS_ENV, env)
    return DEFAULT_WARN_HOURS


def _parse_iso(ts):
    if not ts:
        return None
    text = str(ts).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def fetch_accounts(platform=PLATFORM):
    """`gsk zernio list_accounts` → 해당 플랫폼 계정 목록."""
    proc = subprocess.run(
        ["gsk", "zernio", "list_accounts"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"list_accounts 실패 — rc={proc.returncode} {proc.stderr.strip()[:200]}"
        )
    idx = proc.stdout.find("{")
    if idx < 0:
        raise RuntimeError("list_accounts 응답에 JSON 없음 — 파싱 거부")
    payload = json.loads(proc.stdout[idx:])
    data = payload.get("data") or {}
    accounts = data.get("accounts") or []
    if platform:
        accounts = [a for a in accounts if a.get("platform") == platform]
    return accounts


def find_account(accounts, account_id):
    return next((a for a in accounts if a.get("_id") == account_id), None)


def check_token_health(account_id=None, warn_hours=None, now=None, accounts=None):
    """토큰 상태 판정. `now`·`accounts` 주입 → 시각·네트워크 비의존(테스트 결정론)."""
    aid = account_id or os.environ.get(ACCOUNT_ENV)
    hours_threshold = resolve_warn_hours(warn_hours)
    current = now or datetime.now(UTC)
    try:
        pool = fetch_accounts() if accounts is None else list(accounts)
    except Exception as exc:  # noqa: BLE001
        # 조회 자체가 불가한 환경(예: Actions 러너에 gsk 부재)에서는 판정을 보류하고
        # 발행을 막지 않는다. 실제 무효 상태(만료·재연결·비활성)만 차단한다.
        return TokenHealth(True, UNAVAILABLE, aid, None, None, None,
                           [f"계정 조회 불가 — 판정 보류(차단 안 함): {exc}"])

    if not aid:
        return TokenHealth(False, MISSING, None,
                           problems=[f"{ACCOUNT_ENV} 미설정 — 계정 특정 불가"])

    account = find_account(pool, aid)
    if account is None:
        return TokenHealth(False, MISSING, aid,
                           problems=[f"계정 {aid} 미발견 — 연결 해제 의심"])

    username = account.get("username")
    expires_raw = account.get("tokenExpiresAt")
    expires = _parse_iso(expires_raw)
    hours_left = None
    if expires is not None:
        hours_left = round((expires - current).total_seconds() / 3600.0, 2)

    # 우선순위 1 — 재연결 필요(만료시각과 무관하게 최우선)
    if account.get("needsReconnection") is True:
        return TokenHealth(False, NEEDS_RECONNECT, aid, username, expires_raw, hours_left,
                           ["needsReconnection=True — Zernio 대시보드에서 재연결 필요"])
    # 우선순위 2 — 비활성
    if account.get("isActive") is False:
        return TokenHealth(False, INACTIVE, aid, username, expires_raw, hours_left,
                           ["isActive=False — 계정 비활성"])
    # 우선순위 3 — 만료시각 부재는 유효성 미확인 → fail-closed
    if expires is None:
        return TokenHealth(False, EXPIRED, aid, username, None, None,
                           ["tokenExpiresAt 부재 — 유효성 확인 불가(fail-closed)"])
    # 우선순위 4 — 만료
    if hours_left <= 0:
        return TokenHealth(False, EXPIRED, aid, username, expires_raw, hours_left,
                           [f"토큰 만료(남은 {hours_left}h)"])
    # 우선순위 5 — 만료 임박(경고 전용, 차단 아님)
    if hours_left <= hours_threshold:
        return TokenHealth(False, EXPIRING, aid, username, expires_raw, hours_left,
                           [f"토큰 만료 임박(남은 {hours_left}h ≤ 임계 {hours_threshold}h)"])
    return TokenHealth(True, OK, aid, username, expires_raw, hours_left, [])


def format_alert(health):
    lines = [
        f"[hero-shorts] 틱톡 토큰 preflight — {health.status.upper()}",
        f"계정: {health.username or '-'} ({health.account_id or '-'})",
        f"만료: {health.expires_at or '-'} (남은 {health.hours_left}h)",
        "조치: " + (
            "Zernio 대시보드(https://zernio.com/dashboard/connections)에서 재연결"
            if health.status in (EXPIRED, NEEDS_RECONNECT)
            else ("계정 조회 불가 — gsk가 있는 환경에서 재점검"
                  if health.status == UNAVAILABLE
                  else "만료 전 재연결 권장(자동 갱신 작동 미검증)")
        ),
    ]
    if health.problems:
        lines.append("문제: " + " / ".join(health.problems))
    return "\n".join(lines)


def send_alert(health):
    """경보 발송 — 실패해도 preflight 판정은 유지(경보 실패로 발행이 죽지 않게)."""
    body = format_alert(health)
    try:
        from .. import publish_runtime as pr
        pr.send_email_alert(f"[hero-shorts] 틱톡 토큰 {health.status}", f"<pre>{body}</pre>")
    except Exception as exc:  # noqa: BLE001
        log.warning("preflight 경보 발송 실패(판정은 유지): %s", exc)
    return body


def assert_token_ready(account_id=None, warn_hours=None, now=None, accounts=None):
    """차단 상태면 RuntimeError. 만료 임박은 경고+경보만."""
    health = check_token_health(account_id=account_id, warn_hours=warn_hours,
                                now=now, accounts=accounts)
    if health.blocking:
        raise RuntimeError(
            f"틱톡 preflight 차단({health.status}) — {'; '.join(health.problems)}"
        )
    if health.status in WARN_ONLY:
        log.warning("틱톡 preflight 경고(%s): %s", health.status, "; ".join(health.problems))
        send_alert(health)
    return health
