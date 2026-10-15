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

A안(2026-10-15) — 경보는 "관측"이 아니라 "전달"까지가 임무다
    기존 구현은 WARN_ONLY(expiring·unavailable)만 경보하고, **BLOCKING(expired·
    needs_reconnect·inactive·missing)은 경보 없이 예외로 죽었다**. 즉 발행이 막힌
    바로 그 상황에서 사람이 알림을 못 받는 역전이 있었다(2026-10-15 실측: 21:44
    preflight expired → exit 70, 경보 0건). 이제 차단 상태에서도 경보를 먼저 보내고
    예외를 올린다. 또한 모든 preflight 관측을 token_history 에 적재한다(B안).

    갱신 강제는 불가하다: Zernio 는 토큰 갱신 API 를 제공하지 않는다(4계층 전수 조사).
    유일한 수동 복구는 Zernio 대시보드 재연결이며, 따라서 경보 문구는 그 경로를 명시한다.
"""
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime

log = logging.getLogger("tiktok.account")

VERSION = "1.3.0"
PLATFORM = "tiktok"

# 24h 주기 토큰은 만료 직후 첫 호출에서 자동 갱신된다(2026-10-15 실측).
# 12h 로 두면 하루의 절반이 상시 경보 → 경보 피로. 실제 실패는 expired(차단)가 잡는다.
DEFAULT_WARN_HOURS = 2.0
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

RECONNECT_URL = "https://zernio.com/dashboard/connections"
HISTORY_SOURCE_DEFAULT = "runner3.preflight"


def _profile_id_of(account):
    """list_accounts 항목의 profileId(문자열 또는 dict) → 문자열. 실패 시 None."""
    if not isinstance(account, dict):
        return None
    raw = account.get("profileId")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for k in ("_id", "id", "profileId"):
            if raw.get(k):
                return str(raw[k])
    return None


def _history():
    """token_history 지연 임포트 — 이력 모듈 부재/실패가 preflight 를 죽이지 않게."""
    try:
        from . import token_history
        return token_history
    except Exception as exc:  # noqa: BLE001
        log.warning("토큰 이력 모듈 로드 실패(이력 없이 계속): %s", exc)
        return None


def _next_run():
    """next_run 지연 임포트 — 모듈 부재/실패가 preflight 를 죽이지 않게."""
    try:
        from . import next_run
        return next_run
    except Exception as exc:  # noqa: BLE001
        log.warning("다음 실행 해석 모듈 로드 실패(주입 생략): %s", exc)
        return None


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
    warn_hours: float | None = None
    profile_id: str | None = None

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
            "warn_hours": self.warn_hours,
            "profile_id": self.profile_id,
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

    pid_holder = {"pid": None}

    def verdict(ok, status, account_id=None, username=None, expires_at=None,
                hours_left=None, problems=None):
        return TokenHealth(ok, status, account_id, username, expires_at, hours_left,
                           problems or [], hours_threshold, profile_id=pid_holder["pid"])

    try:
        pool = fetch_accounts() if accounts is None else list(accounts)
    except Exception as exc:  # noqa: BLE001
        # 조회 자체가 불가한 환경(예: Actions 러너에 gsk 부재)에서는 판정을 보류하고
        # 발행을 막지 않는다. 실제 무효 상태(만료·재연결·비활성)만 차단한다.
        return verdict(True, UNAVAILABLE, aid,
                       problems=[f"계정 조회 불가 — 판정 보류(차단 안 함): {exc}"])

    if not aid:
        return verdict(False, MISSING, problems=[f"{ACCOUNT_ENV} 미설정 — 계정 특정 불가"])

    account = find_account(pool, aid)
    if account is None:
        return verdict(False, MISSING, aid, problems=[f"계정 {aid} 미발견 — 연결 해제 의심"])

    pid_holder["pid"] = _profile_id_of(account)

    username = account.get("username")
    expires_raw = account.get("tokenExpiresAt")
    expires = _parse_iso(expires_raw)
    hours_left = None
    if expires is not None:
        hours_left = round((expires - current).total_seconds() / 3600.0, 2)

    # 우선순위 1 — 재연결 필요(만료시각과 무관하게 최우선)
    if account.get("needsReconnection") is True:
        return verdict(False, NEEDS_RECONNECT, aid, username, expires_raw, hours_left,
                       ["needsReconnection=True — Zernio 대시보드에서 재연결 필요"])
    # 우선순위 2 — 비활성
    if account.get("isActive") is False:
        return verdict(False, INACTIVE, aid, username, expires_raw, hours_left,
                       ["isActive=False — 계정 비활성"])
    # 우선순위 3 — 만료시각 부재는 유효성 미확인 → fail-closed
    if expires is None:
        return verdict(False, EXPIRED, aid, username, None, None,
                       ["tokenExpiresAt 부재 — 유효성 확인 불가(fail-closed)"])
    # 우선순위 4 — 만료
    if hours_left <= 0:
        return verdict(False, EXPIRED, aid, username, expires_raw, hours_left,
                       [f"토큰 만료(남은 {hours_left}h)"])
    # 우선순위 5 — 만료 임박(경고 전용, 차단 아님)
    if hours_left <= hours_threshold:
        return verdict(False, EXPIRING, aid, username, expires_raw, hours_left,
                       [f"토큰 만료 임박(남은 {hours_left}h ≤ 임계 {hours_threshold}h)"])
    return verdict(True, OK, aid, username, expires_raw, hours_left, [])


def format_alert(health):
    if health.status == OK:
        action = "조치 불필요"
    elif health.status in (EXPIRED, NEEDS_RECONNECT, MISSING, INACTIVE):
        action = (f"즉시 재연결 — {RECONNECT_URL} "
                  f"(프로필 {health.profile_id or '-'} / 계정 {health.account_id or '-'}"
                  f" / @{health.username or '-'})")
    elif health.status == EXPIRING:
        action = "만료 임박 — 자동 갱신이 만료 30~45분 후 발화한 실측이 있으나, "
        action += "실행 시각과 겹치면 차단된다. 재연결 링크를 대기시켜 둘 것"
    else:
        action = "계정 조회 불가 — gsk 가 있는 환경에서 재점검"
    lines = [
        f"[hero-shorts] 틱톡 토큰 preflight — {health.status.upper()}",
        f"계정: {health.username or '-'} ({health.account_id or '-'})"
        + (f" · 프로필 {health.profile_id}" if health.profile_id else ""),
        f"만료: {health.expires_at or '-'} (남은 {health.hours_left}h)",
        f"조치: {action}",
    ]
    lines.extend(_history_lines())
    if health.problems:
        lines.append("문제: " + " / ".join(health.problems))
    return "\n".join(lines)


def _history_lines():
    """이력 요약 2줄(가능할 때만) — 이력 부재/실패는 조용히 생략."""
    th = _history()
    if th is None:
        return []
    try:
        obs = th.load()["observations"]
        if not obs:
            return ["이력: 관측 없음(이번 실행부터 축적)"]
        s = th.summarize(obs)
        out = [f"이력: 관측 {s['observations']}건 · 갱신 {s['renewals']}회"
               f" · 연속 성공 {s['consecutive_renewals']}회"
               + (f" · 끊김({s['streak_broken_at']})" if s.get("streak_broken_at") else "")]
        nx = _next_run()
        if nx is not None:
            nx.apply()  # 값이 비었을 때만 채운다(스케줄러 조회는 러너가 force 로 수행)
        nxt = os.getenv("HERO_TIKTOK_NEXT_RUN_AT")
        if nxt:
            v = th.verify_until(obs, nxt)
            out.append(f"다음 실행({nxt}) 판정: {str(v['verdict']).upper()}"
                       f" — 필요 {v['renewals_needed']}회 / 연속 {v['consecutive_renewals']}회")
            out.append(f"주입 출처: {os.getenv('HERO_TIKTOK_NEXT_RUN_SOURCE') or 'env'}")
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("이력 요약 생성 실패(생략): %s", exc)
        return []


def record_observation(health, source=HISTORY_SOURCE_DEFAULT, do_mirror=False):
    """preflight 관측을 이력에 적재(B안). 실패해도 판정은 유지된다."""
    th = _history()
    if th is None:
        return None
    return th.record(health, source=source, do_mirror=do_mirror)


def send_alert(health, force=False, now=None):
    """경보 발송 — 실패해도 preflight 판정은 유지(경보 실패로 발행이 죽지 않게).

    채널 우선순위: 텔레그램(저장소 시크릿 TELEGRAM_BOT_TOKEN·TELEGRAM_PAID_CHANNEL_ID
    실재) → Gmail(publish_runtime.send_email_alert) 폴백.
    중복 억제: 동일 상태 경보가 창(차단 1h / 경고 6h) 안이면 기본 생략. force=True 면 무시.
    """
    th = _history()
    if not force and th is not None:
        ok, reason = th.should_alert(health.status, blocking=health.blocking, now=now)
        if not ok:
            log.info("preflight 경보 생략(%s): %s", health.status, reason)
            return f"(경보 생략 — {reason})"
    body = format_alert(health)
    subject = f"[hero-shorts] 틱톡 토큰 {health.status}"
    sent, channel = False, "none"
    if send_telegram(f"{subject}\n{body}"):
        sent, channel = True, "telegram"
        log.info("preflight 경보 발송 완료(텔레그램)")
    else:
        try:
            from .. import publish_runtime as pr
            pr.send_email_alert(subject, f"<pre>{body}</pre>")
            sent, channel = True, "email"
        except Exception as exc:  # noqa: BLE001
            log.warning("preflight 경보 발송 실패(판정은 유지): %s", exc)
    if th is not None:
        th.record_alert(health.status, channel, sent, now=now,
                        detail="; ".join(health.problems) or None)
    return body


def send_telegram(text):
    """텔레그램 발송. 자격증명 부재/실패 시 False(예외 전파 안 함)."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_PAID_CHANNEL_ID") or "").strip()
    if not token or not chat:
        return False
    try:
        import urllib.request
        payload = json.dumps({
            "chat_id": chat, "text": text, "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001
        log.warning("텔레그램 경보 발송 실패: %s", exc)
        return False


def assert_token_ready(account_id=None, warn_hours=None, now=None, accounts=None,
                       source=HISTORY_SOURCE_DEFAULT, notify=True):
    """차단 상태면 RuntimeError. 만료 임박은 경고+경보만.

    A안(2026-10-15): 차단 상태에서도 **경보를 먼저** 보낸 뒤 예외를 올린다 —
    발행이 막힌 상황이 조용히 지나가면 사람이 재연결 시점을 알 수 없다.
    B안: 모든 관측을 token_history 에 적재한다(이력 실패는 판정에 영향 없음).
    """
    nx = _next_run()
    if nx is not None:
        try:
            nx.apply(force=True)  # 매 실행 재계산 — 경보에 "다음 실행 판정"을 싣기 위해
        except Exception as exc:  # noqa: BLE001
            log.warning("다음 실행 시각 주입 실패(계속): %s", exc)
    health = check_token_health(account_id=account_id, warn_hours=warn_hours,
                                now=now, accounts=accounts)
    record_observation(health, source=source)
    if health.blocking:
        log.error("틱톡 preflight 차단(%s): %s", health.status, "; ".join(health.problems))
        if notify:
            send_alert(health, now=now)
        raise RuntimeError(
            f"틱톡 preflight 차단({health.status}) — {'; '.join(health.problems)}"
        )
    if health.status in WARN_ONLY:
        log.warning("틱톡 preflight 경고(%s): %s", health.status, "; ".join(health.problems))
        if notify:
            send_alert(health, now=now)
    return health
