"""Zernio publish watch — 읽기 전용 폴러 + 상태 차이 경보.

실행 주체: .github/workflows/zernio_publish_watch.yml (30분 cron + 수동)
감시 대상: gen/hero_shorts/monitor/zernio_watch_targets.json (post id 목록)
상태 파일: gen/hero_shorts/monitor/zernio_watch_state.json (이전 스냅샷, 워크플로우가 커밋으로 갱신)
출력: alerts.txt — 텔레그램으로 보낼 메시지 1행씩(변화 없으면 빈 파일)

호출 정책: Zernio API는 GET 만 사용한다(게시·수정·삭제 없음).
경보 규칙:
  - post 상태 변화(threads entry 기준) — published면 URL 포함 축하, failed/cancelled면 경고
  - 계정 헬스 이상(status != healthy 또는 canPost 아님)
  - 폴링 실패 — 1회째, 이후 4회째마다(≈2시간) 알림(연속 실패 스팸 방지)
  - 감시 목록에 새 post — 첫 관측값을 기준선으로 조용히 등록(알림 없음)
종료 코드: 경보 유무와 무관하게 0(상태 전이는 실패가 아님). 설정/코드 결함만 비정상 종료
(워크플로우 실패 → notify_watchdog 경보로 연결).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

BASE = "https://zernio.com/api/v1"
HERE = Path(__file__).resolve().parent
TARGETS_PATH = HERE / "zernio_watch_targets.json"
STATE_PATH = HERE / "zernio_watch_state.json"
ALERTS_PATH = Path("alerts.txt")


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get_json(path: str, api_key: str) -> tuple[Dict[str, Any], int]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — 고정 호스트
        return json.loads(resp.read().decode("utf-8", "replace")), int(resp.status)


def poll_post(post_id: str, api_key: str) -> tuple[str, str, str | None, str | None]:
    """(entry_status, post_status, platformPostUrl, raw_error) — 실패 시 status='__poll_error__'."""
    try:
        body, _ = get_json(f"/posts/{post_id}", api_key)
    except urllib.error.HTTPError as exc:
        return "__poll_error__", f"HTTP {exc.code}", None, str(exc)[:200]
    except Exception as exc:  # 네트워크 계열
        return "__poll_error__", f"{type(exc).__name__}", None, str(exc)[:200]
    post = body.get("post") or {}
    entry = (post.get("platforms") or [{}])[0]
    return (
        str(entry.get("status") or "unknown"),
        str(post.get("status") or "unknown"),
        entry.get("platformPostUrl"),
        entry.get("errorMessage"),
    )


def poll_health(api_key: str) -> Dict[str, Any]:
    try:
        body, _ = get_json("/accounts/health", api_key)
    except Exception as exc:
        return {"status": "__poll_error__", "can_post": None, "detail": str(exc)[:200]}
    acct = next((a for a in body.get("accounts", []) if a.get("platform") == "threads"), {})
    return {
        "status": acct.get("status") or "unknown",
        "can_post": acct.get("canPost"),
        "token_valid": acct.get("tokenValid"),
        "needs_reconnect": acct.get("needsReconnect"),
        "issues": acct.get("issues") or [],
    }


def main() -> int:
    api_key = os.environ.get("ZERNIO_API_KEY") or ""
    if not api_key:
        raise SystemExit("ZERNIO_API_KEY 미주입 — 워크플로우 시크릿 확인 필요")

    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    state: Dict[str, Any] = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    posts_state: Dict[str, Any] = state.get("posts") or {}
    alerts: List[str] = []
    poll_ok = True

    for tgt in targets.get("posts", []):
        pid, label = tgt["post_id"], tgt.get("label", pid)
        entry_status, post_status, url, raw_err = poll_post(pid, api_key)
        prev = posts_state.get(pid)
        if entry_status == "__poll_error__":
            poll_ok = False
            posts_state.setdefault(pid, {"label": label, "entry_status": "unknown"})
            continue
        if prev is None:                       # 신규 감시 대상 — 기준선 등록만
            posts_state[pid] = {"label": label, "entry_status": entry_status,
                                "post_status": post_status, "url": url,
                                "last_checked": now_utc()}
            continue
        old = prev.get("entry_status")
        if old != entry_status:
            if entry_status == "published":
                alerts.append(f"🎉 [{label}] Threads 발행 완료! url={url or 'URL 미지정'}")
            elif entry_status == "failed":
                alerts.append(
                    f"⚠️ [{label}] Threads 발행 실패 확정 (post={post_status}) — retry 레버 유효, "
                    f"error={(raw_err or '')[-160:]}")
            elif entry_status == "cancelled":
                alerts.append(f"ℹ️ [{label}] 게시 취소 상태로 전이 ({old} → cancelled)")
            else:
                alerts.append(f"ℹ️ [{label}] 상태 변화 {old} → {entry_status} (post={post_status})")
        posts_state[pid] = {"label": label, "entry_status": entry_status,
                            "post_status": post_status, "url": url,
                            "last_checked": now_utc()}

    health = poll_health(api_key)
    if health.get("status") == "__poll_error__":
        poll_ok = False
    else:
        prev_h = state.get("health") or {}
        bad = health.get("status") != "healthy" or health.get("can_post") is not True
        prev_bad = prev_h.get("status") != "healthy" or prev_h.get("can_post") is not True
        if bad and not prev_bad:
            alerts.append(f"🚨 Zernio threads 계정 헬스 이상: {json.dumps(health, ensure_ascii=False)}")
        state["health"] = {**health, "checked_at": now_utc()}

    if poll_ok:
        state["consecutive_poll_failures"] = 0
    else:
        n = int(state.get("consecutive_poll_failures") or 0) + 1
        state["consecutive_poll_failures"] = n
        if n == 1 or (n >= 4 and (n - 4) % 4 == 0):
            alerts.append(f"🚨 Zernio 폴링 실패(연속 {n}회) — API/네트워크 점검 필요")

    state["posts"] = posts_state
    state["last_run"] = now_utc()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    ALERTS_PATH.write_text("\n".join(alerts) + ("\n" if alerts else ""), encoding="utf-8")
    print(f"POLL_OK={poll_ok} ALERTS={len(alerts)}")
    for line in alerts:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
