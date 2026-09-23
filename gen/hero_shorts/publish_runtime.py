"""
gen/hero_shorts/publish_runtime.py — Zernio 즉시/예약 발행 + 게시상태 모니터 + 알림
=======================================================================
원칙:
    - QC/G5 통과 산출물만 제르니오 업로드 요청
    - create_post 결과의 post_id 로 list_posts 폴링
    - published 면 공개 URL 회수
    - failed / error / rejected / cancelled 면 실패 처리
    - 통상 시간(max_wait_sec) 초과 시 timeout 처리 및 선택적 이메일 알림
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("hero_shorts.publish_runtime")

DEFAULT_FROM_ACCOUNT = os.getenv("HERO_FROM_ACCOUNT")
DEFAULT_ALERT_TO = os.getenv("HERO_ALERT_EMAIL")
TERMINAL_FAIL = {"failed", "error", "rejected", "cancelled"}
TERMINAL_OK = {"published", "completed", "success"}


def _run_cmd(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _run_json(args: List[str]) -> Dict[str, Any]:
    cp = _run_cmd(args)
    if cp.returncode != 0:
        raise RuntimeError(
            f"명령 실패: {' '.join(shlex.quote(a) for a in args)} | "
            f"exit={cp.returncode} | stderr={cp.stderr.strip()}"
        )
    stdout = cp.stdout.strip()
    if not stdout:
        raise RuntimeError(f"명령 결과 비어 있음: {' '.join(shlex.quote(a) for a in args)}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON 파싱 실패: {' '.join(shlex.quote(a) for a in args)} | stdout={stdout[:500]}"
        ) from exc


def _run_ok(args: List[str]) -> None:
    cp = _run_cmd(args)
    if cp.returncode != 0:
        raise RuntimeError(
            f"명령 실패: {' '.join(shlex.quote(a) for a in args)} | "
            f"exit={cp.returncode} | stderr={cp.stderr.strip()}"
        )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_post(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("post"), dict):
            return data["post"]
        if isinstance(data.get("posts"), list) and data["posts"]:
            return data["posts"][0]
    raise RuntimeError(f"게시물 데이터를 찾지 못함: keys={list(payload.keys())}")


def _platform_entry(post: Dict[str, Any], platform: str) -> Optional[Dict[str, Any]]:
    for item in post.get("platforms", []):
        if item.get("platform") == platform:
            return item
    return None


def _post_url(entry: Optional[Dict[str, Any]]) -> Optional[str]:
    if not entry:
        return None
    return entry.get("platformPostUrl") or entry.get("postUrl") or entry.get("url")


def _normalize_status(post: Dict[str, Any], platform: str) -> Tuple[str, Optional[str]]:
    entry = _platform_entry(post, platform)
    url = _post_url(entry)
    if entry:
        p_status = str(entry.get("status", "")).lower()
        if p_status in TERMINAL_OK:
            return "published", url
        if p_status in TERMINAL_FAIL:
            return "failed", url
        if url and p_status not in TERMINAL_FAIL:
            return "published", url
    aggregate = str(post.get("status", "unknown")).lower()
    if aggregate in TERMINAL_OK:
        return "published", url
    if aggregate in TERMINAL_FAIL:
        return "failed", url
    return "pending", url


def _collect_errors(post: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for entry in post.get("platforms", []):
        platform = entry.get("platform", "unknown")
        status = str(entry.get("status", "")).lower()
        if entry.get("error"):
            errors.append(f"{platform}: {entry.get('error')}")
        if entry.get("errorMessage"):
            errors.append(f"{platform}: {entry.get('errorMessage')}")
        if status in TERMINAL_FAIL:
            errors.append(f"{platform}: status={status}")
    aggregate = str(post.get("status", "")).lower()
    if aggregate in TERMINAL_FAIL:
        errors.append(f"aggregate: status={aggregate}")
    return errors


def _format_alert_html(post_id: str, state: str, message: str, post_url: Optional[str], snapshot: Dict[str, Any]) -> str:
    url_html = (
        f'<p><b>공개 링크</b>: <a href="{post_url}">{post_url}</a></p>'
        if post_url else "<p><b>공개 링크</b>: 아직 없음</p>"
    )
    snapshot_html = json.dumps(snapshot, ensure_ascii=False, indent=2)
    return (
        "<h2>Zernio 게시 상태 알림</h2>"
        f"<p><b>post_id</b>: {post_id}</p>"
        f"<p><b>상태</b>: {state}</p>"
        f"<p><b>시각(UTC)</b>: {_iso_now()}</p>"
        f"<p><b>메시지</b>: {message}</p>"
        f"{url_html}"
        f"<pre>{snapshot_html}</pre>"
    )


def send_email_alert(subject: str, html_body: str,
                     from_account: Optional[str] = DEFAULT_FROM_ACCOUNT,
                     to: Optional[str] = DEFAULT_ALERT_TO) -> None:
    if not from_account or not to:
        raise RuntimeError("이메일 알림 설정 누락: HERO_FROM_ACCOUNT 및 HERO_ALERT_EMAIL 필요")
    _run_ok([
        "gsk", "gmail", "send",
        "--from_account", from_account,
        "--to", to,
        "--subject", subject,
        "--content_type", "text/html",
        "--body", html_body,
        "-y",
    ])


def create_post_args(text: str, media_url: str, account_id: str,
                     schedule_at: Optional[str] = None,
                     ai_generated: bool = True) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "text": text,
        "media_urls": [media_url],
        "account_ids": [account_id],
        "ai_generated": ai_generated,
    }
    if schedule_at:
        payload["schedule_at"] = schedule_at
    return payload


def create_post(text: str, media_url: str, account_id: str,
                schedule_at: Optional[str] = None,
                ai_generated: bool = True) -> Dict[str, Any]:
    args_file = Path("/tmp/opencode/zernio_create_post_args.json")
    args_file.parent.mkdir(parents=True, exist_ok=True)
    args_file.write_text(json.dumps(
        create_post_args(text=text, media_url=media_url, account_id=account_id,
                         schedule_at=schedule_at, ai_generated=ai_generated),
        ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    payload = _run_json(["gsk", "zernio", "create_post", "자동발행", "--args-file", str(args_file), "-y"])
    data = payload.get("data") or {}
    post = data.get("post") or {}
    post_id = post.get("_id")
    if not post_id:
        raise RuntimeError(f"create_post 결과에 post_id 없음: keys={list(data.keys())}")
    log.info("제르니오 발행 요청 완료: post_id=%s status=%s", post_id, post.get("status"))
    return payload


def poll_post(post_id: str, platform: str = "instagram",
              max_wait_sec: int = 900, poll_interval_sec: int = 30) -> Dict[str, Any]:
    start = time.time()
    last_post: Optional[Dict[str, Any]] = None
    while True:
        payload = _run_json(["gsk", "zernio", "list_posts", "--post_id", post_id])
        post = _extract_post(payload)
        last_post = post
        status, post_url = _normalize_status(post, platform)
        if status == "published":
            return {
                "outcome": "published",
                "post_id": post_id,
                "post_url": post_url,
                "snapshot": post,
                "elapsed_sec": round(time.time() - start, 1),
            }
        errors = _collect_errors(post)
        if status == "failed" or errors:
            return {
                "outcome": "failed",
                "post_id": post_id,
                "post_url": post_url,
                "errors": errors,
                "snapshot": post,
                "elapsed_sec": round(time.time() - start, 1),
            }
        if time.time() - start >= max_wait_sec:
            _, post_url = _normalize_status(last_post or {}, platform)
            return {
                "outcome": "timeout",
                "post_id": post_id,
                "post_url": post_url,
                "snapshot": last_post or {},
                "elapsed_sec": round(time.time() - start, 1),
            }
        time.sleep(poll_interval_sec)


def maybe_alert(result: Dict[str, Any],
                from_account: str = DEFAULT_FROM_ACCOUNT,
                alert_to: str = DEFAULT_ALERT_TO,
                max_wait_sec: int = 900,
                alert_on_timeout: bool = True,
                alert_on_error: bool = True) -> None:
    outcome = result.get("outcome")
    post_id = result.get("post_id", "unknown")
    if outcome == "failed" and alert_on_error:
        message = "플랫폼 처리 중 실패 또는 에러 상태 감지"
        if result.get("errors"):
            message += " | " + " ; ".join(result["errors"])
        send_email_alert(
            f"[Zernio 알림] 게시 실패: {post_id}",
            _format_alert_html(post_id, "failed", message, result.get("post_url"), result.get("snapshot", {})),
            from_account=from_account,
            to=alert_to,
        )
    elif outcome == "timeout" and alert_on_timeout:
        send_email_alert(
            f"[Zernio 알림] 게시 지연: {post_id}",
            _format_alert_html(
                post_id,
                "timeout",
                f"통상 대기시간 초과: {max_wait_sec}초 안에 게시 완료 미확인",
                result.get("post_url"),
                result.get("snapshot", {}),
            ),
            from_account=from_account,
            to=alert_to,
        )


def create_and_monitor_post(text: str, media_url: str, account_id: str,
                            schedule_at: Optional[str] = None,
                            platform: str = "instagram",
                            ai_generated: bool = True,
                            max_wait_sec: int = 900,
                            poll_interval_sec: int = 30,
                            alert_on_timeout: bool = True,
                            alert_on_error: bool = True,
                            from_account: str = DEFAULT_FROM_ACCOUNT,
                            alert_to: str = DEFAULT_ALERT_TO) -> Dict[str, Any]:
    created = create_post(
        text=text,
        media_url=media_url,
        account_id=account_id,
        schedule_at=schedule_at,
        ai_generated=ai_generated,
    )
    created_post = (created.get("data") or {}).get("post") or {}
    post_id = created_post.get("_id")
    result = poll_post(
        post_id=post_id,
        platform=platform,
        max_wait_sec=max_wait_sec,
        poll_interval_sec=poll_interval_sec,
    )
    maybe_alert(
        result,
        from_account=from_account,
        alert_to=alert_to,
        max_wait_sec=max_wait_sec,
        alert_on_timeout=alert_on_timeout,
        alert_on_error=alert_on_error,
    )
    result["create_response"] = created_post
    return result
