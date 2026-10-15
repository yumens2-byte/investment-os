"""gen/hero_shorts/tiktok/verify.py — 발행 후 플랫폼 대조 검증
================================================================================
문제(2026-10-15 ep88 실측)
    발행 성공은 **자기 보고**(`poll_post` 가 돌려준 `outcome`)로만 판정됐다.
    그 결과가 실제 플랫폼 상태와 어긋난 사례가 있다.

      · 원장 `post_id=None` · `platform_post_id=None` (D-2) — 발행은 됐는데
        플랫폼 식별자가 원장에 안 들어갔다.
      · 원장 `published_at` = 로컬 now 02:24Z, 플랫폼 실값 01:38Z — **46분 불일치**(D-3).

    즉 "발행했다"와 "그 발행이 플랫폼에 그 값으로 존재한다"는 다른 명제인데,
    후자를 확인하는 단계가 없었다.

해결
    발행 직후 Zernio `list_posts` 를 **읽기만** 하여 플랫폼 정본과 대조한다.
    발행·크레딧 0. 통과 여부를 판정하고, 불일치는 경보 + 원장 플래그로 남긴다.
    **추측하지 않는다** — 매칭 실패는 `not_found` 로 보고하고 값을 지어내지 않는다.

판정값
    confirmed       플랫폼에 존재 · URL 유효 · 발행시각 있음 · 중복 없음
    url_missing     존재하나 `platformPostUrl` 없음/비숫자 (D-2 재발)
    time_missing    존재하나 `publishedAt` 없음
    not_found       list_posts 에서 매칭 실패(미도달·삭제·지연)
    duplicate       같은 플랫폼 ID 게시물이 2건 이상(중복 발행 위험)
    unknown         조회 자체 실패(gsk 부재 등) — 정상/비정상을 주장하지 않는다

한계(추측 금지)
    `list_posts` 는 최근 N건 창만 준다(기본 20). 그보다 오래된 발행은 `not_found`
    로 나올 수 있으며, 이는 실패가 아니라 **검증 범위 밖**이다. 따라서
    `not_found` 는 "없다"가 아니라 "이 창에서 못 찾았다"로 읽어야 한다.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("tiktok.verify")

VERSION = "1.0.0"

CONFIRMED = "confirmed"
URL_MISSING = "url_missing"
TIME_MISSING = "time_missing"
NOT_FOUND = "not_found"
DUPLICATE = "duplicate"
UNKNOWN = "unknown"

OK_VERDICTS = (CONFIRMED,)
# 원장 정본과 플랫폼 값의 허용 드리프트(분). 로컬 now 오기(D-3 46분)를 잡는다.
DEFAULT_DRIFT_TOLERANCE_MIN = 5.0
DEFAULT_LIMIT = 20


def _posts(account_id=None, limit=DEFAULT_LIMIT, lister=None):
    if lister is not None:
        return lister(account_id=account_id, limit=limit)
    from . import recover
    return recover.list_tiktok_posts(account_id=account_id, limit=limit)


def _plat_id(url):
    from . import recover
    return recover.tiktok_post_id_from_url(url)


def _parse_iso(value):
    from datetime import datetime
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def verify_published(post_id=None, post_url=None, account_id=None, limit=DEFAULT_LIMIT,
                     allow_latest=False, lister=None):
    """플랫폼 실조회로 발행 결과를 검증. 항상 dict 반환(예외 없음).

    `allow_latest=True` 는 post_id·post_url 이 모두 없을 때 '가장 최근 발행건'을
    쓰는 명시적 예외다. 기본은 fail-closed(매칭 없으면 not_found).
    """
    out = {"ok": False, "verdict": UNKNOWN, "problems": [], "checked": 0,
           "post_id": post_id, "platform_post_id": None, "platform_post_url": None,
           "published_at": None, "published_at_source": None, "matched_by": None,
           "account_id": account_id or os.environ.get("HERO_TIKTOK_ACCOUNT_ID")}
    try:
        posts = _posts(account_id=out["account_id"], limit=limit, lister=lister)
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"list_posts 조회 실패: {exc}")
        out["verdict"] = UNKNOWN
        return out
    out["checked"] = len(posts)
    plat_id = _plat_id(post_url)
    matched, how = None, None
    for post in posts:
        if post_id and post.get("_id") == post_id:
            matched, how = post, "post_id"
            break
        if plat_id and _plat_id(post.get("platformPostUrl")) == plat_id:
            matched, how = post, "post_url"
            break
    if matched is None and allow_latest and posts:
        matched, how = posts[0], "latest"
        out["problems"].append("식별자 없이 최근 발행건으로 대조(감사 전용 경로)")
    if matched is None:
        out["verdict"] = NOT_FOUND
        out["problems"].append(
            f"list_posts 최근 {len(posts)}건에서 매칭 없음"
            " — '없다'가 아니라 '이 창에서 못 찾았다'(검증 범위 밖 포함)"
        )
        return out

    purl = matched.get("platformPostUrl")
    resolved_plat = _plat_id(purl)
    out.update({"post_id": matched.get("_id"), "platform_post_url": purl,
                "platform_post_id": resolved_plat, "matched_by": how})
    published_at = matched.get("publishedAt") or matched.get("scheduledFor")
    if published_at:
        out.update({"published_at": published_at, "published_at_source": "platform"})

    if resolved_plat:
        dupes = [p for p in posts if _plat_id(p.get("platformPostUrl")) == resolved_plat]
        if len(dupes) > 1:
            out["problems"].append(
                f"동일 플랫폼 ID 게시물 {len(dupes)}건 — 중복 발행 의심"
            )
            out["verdict"] = DUPLICATE
            out["ok"] = False
            return out
    if not resolved_plat:
        out["verdict"] = URL_MISSING
        out["problems"].append(
            "platformPostUrl 없음/비숫자 — 플랫폼 링크 회수 불가(D-2 재발 패턴)"
        )
        return out
    if not published_at:
        out["verdict"] = TIME_MISSING
        out["problems"].append("publishedAt 없음 — 플랫폼 발행시각 미회수")
        return out
    out["verdict"] = CONFIRMED
    out["ok"] = True
    return out


def check_ledger(entry, verdict, tolerance_min=DEFAULT_DRIFT_TOLERANCE_MIN):
    """원장 기록값과 플랫폼 정본의 불일치 검사(읽기 전용).

    반환 problems[] 가 비면 일치. `published_at` 은 드리프트(분)로 비교한다.
    """
    from datetime import UTC, datetime
    problems = []
    tk = (entry or {}).get("tiktok") or {}
    if not tk:
        return {"problems": ["원장에 tiktok 블록 없음"], "drift_min": None}
    for field, plat in (("post_id", verdict.get("post_id")),
                        ("platform_post_id", verdict.get("platform_post_id")),
                        ("post_url", verdict.get("platform_post_url"))):
        led = tk.get(field)
        if plat and not led:
            problems.append(f"원장 {field} 누락 — 플랫폼 값 {plat} 회수 가능(D-2)")
        elif led and plat and str(led) != str(plat):
            problems.append(f"원장 {field} 불일치 — 원장 {led} vs 플랫폼 {plat}")
    drift = None
    led_at = _parse_iso(tk.get("published_at"))
    plat_at = _parse_iso(verdict.get("published_at"))
    if led_at and plat_at:
        if led_at.tzinfo is None:
            led_at = led_at.replace(tzinfo=UTC)
        if plat_at.tzinfo is None:
            plat_at = plat_at.replace(tzinfo=UTC)
        drift = abs((led_at - plat_at).total_seconds()) / 60.0
        if drift > float(tolerance_min):
            problems.append(
                f"published_at 드리프트 {drift:.1f}분(허용 {tolerance_min}분) — "
                f"원장 {tk.get('published_at')} vs 플랫폼 {verdict.get('published_at')}"
            )
    elif plat_at and not led_at:
        problems.append("원장 published_at 누락 — 플랫폼 값 회수 가능(D-3)")
    return {"problems": problems, "drift_min": round(drift, 2) if drift is not None else None}


def format_verdict(verdict, ledger_problems=None, ep=None):
    """사람이 읽는 검증 결과(텔레그램/로그)."""
    head = f"[hero-shorts] 발행 검증 — {str(verdict.get('verdict')).upper()}"
    if ep:
        head += f" (ep{ep})"
    lines = [head,
             f"플랫폼: post_id={verdict.get('post_id') or '-'}"
             f" · 틱톡ID={verdict.get('platform_post_id') or '-'}"
             f" · 매칭={verdict.get('matched_by') or '-'}"
             f" (조회 {verdict.get('checked')}건)",
             f"발행시각: {verdict.get('published_at') or '-'}"
             f" ({verdict.get('published_at_source') or '-'})"]
    if verdict.get("platform_post_url"):
        lines.append(f"링크: {verdict['platform_post_url']}")
    problems = list(verdict.get("problems") or []) + list(ledger_problems or [])
    if problems:
        lines.append("문제: " + " / ".join(problems))
    return "\n".join(lines)


def record_verification(verdict, entry=None, ledger_problems=None):
    """원장 항목에 검증 결과를 남긴다(발행 상태는 건드리지 않는다)."""
    if entry is None:
        return None
    entry.setdefault("tiktok", {})["verification"] = {
        "verdict": verdict.get("verdict"),
        "ok": bool(verdict.get("ok")),
        "platform_post_id": verdict.get("platform_post_id"),
        "platform_post_url": verdict.get("platform_post_url"),
        "published_at": verdict.get("published_at"),
        "published_at_source": verdict.get("published_at_source"),
        "checked": verdict.get("checked"),
        "problems": list(verdict.get("problems") or []) + list(ledger_problems or []),
    }
    return entry


def alert(verdict, ledger_problems=None, ep=None):
    """검증 실패 경보 — 실패해도 호출자 흐름을 죽이지 않는다."""
    if verdict.get("ok") and not ledger_problems:
        return "(경보 없음 — 검증 통과)"
    body = format_verdict(verdict, ledger_problems=ledger_problems, ep=ep)
    subject = f"[hero-shorts] 발행 검증 {verdict.get('verdict')}"
    try:
        from . import account as tk_account
        if tk_account.send_telegram(f"{subject}\n{body}"):
            return "telegram"
    except Exception as exc:  # noqa: BLE001
        log.warning("검증 경보 발송 실패: %s", exc)
    return "none"
