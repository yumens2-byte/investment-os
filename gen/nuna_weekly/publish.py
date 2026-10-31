"""
gen/nuna_weekly/publish.py — 4채널 예약 발행·검증 (설계서 8장 외부연계, 15장 보안)
=================================================================================
요청 4건(Q9 키 분리, DN2026_0085 플랫폼별 분리 — 한 계정 403 이 다른 채널을 막지 않게):
    main  [instagram]           미디어 main/main   Idempotency-Key "<ep>:main:instagram"
    main  [threads]             미디어 main/main   "<ep>:main:threads"
    sub   [youtube]             미디어 main/sub    "<ep>:sub:youtube"
    sub   [facebook]            미디어 fb_variant/sub "<ep>:sub:facebook"

원칙:
    · publishNow 금지 — scheduledFor + timezone=Asia/Seoul 예약 발행만.
    · 캡션 길이·면책 문구 검증은 어떤 호출보다 먼저 (PublishError).
    · 원장 upsert 를 POST 전에 기록, 이미 scheduled/published 인 채널은 건너뛴다(재실행 멱등).
    · 한 채널(요청) 실패가 다른 요청을 멈추지 않는다.
    · 원장에 남기는 요청·응답은 accountId → "***", URL query string 제거.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from . import config, policy, secret_env
from .models import Script, TargetResult
from .zernio import decompose

VERSION = "1.2.0"  # 1.2.0 (2026-10-31, DN2026_0085) 플랫폼별 요청 분리 · 1.1.0 DN2026_0084 계정 진단
log = logging.getLogger("nuna_weekly.publish")

DONE_STATES = ("scheduled", "published")
FB_VARIANTS = ("main", "fb_cut")
_MASK_KEYS = frozenset({"accountId", "account_id", "uploadUrl", "upload_url"})


class PublishError(RuntimeError):
    """발행 전 검증 실패 — 외부 호출 전에 발생한다."""


# --------------------------------------------------------------------------- #
# 시각
# --------------------------------------------------------------------------- #
def scheduled_for(publish_date: date, notified_at: datetime | None) -> datetime:
    """max(발행일 08:30 KST, 알림 시각 + 보류창 60분) — KST aware datetime."""
    base = datetime(publish_date.year, publish_date.month, publish_date.day,
                    config.PUBLISH_HOUR_KST, config.PUBLISH_MINUTE_KST, tzinfo=config.KST)
    if notified_at is None:
        return base
    n = notified_at if notified_at.tzinfo else notified_at.replace(tzinfo=config.KST)
    hold_end = (n + timedelta(minutes=config.HOLD_WINDOW_MIN)).astimezone(config.KST)
    # 보류창 종료 후 최종 콜백 확인 및 예약 API 호출 여유.
    return max(base, hold_end + timedelta(minutes=2))


def iso_kst(dt: datetime) -> str:
    """'2026-10-26T08:30:00+09:00' 형식."""
    d = dt if dt.tzinfo else dt.replace(tzinfo=config.KST)
    return d.astimezone(config.KST).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# 마스킹
# --------------------------------------------------------------------------- #
def redact_obj(obj: Any) -> Any:
    """원장 저장용 — accountId/uploadUrl 값 *** · 문자열은 secret_env.redact(query string 제거)."""
    if isinstance(obj, dict):
        return {k: ("***" if k in _MASK_KEYS and v else redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, str):
        return secret_env.redact(obj)
    return obj


# --------------------------------------------------------------------------- #
# 본문 조립
# --------------------------------------------------------------------------- #
def _caption(script: Script, platform: str) -> str:
    cap = (script.captions or {}).get(platform)
    if not isinstance(cap, str) or not cap.strip():
        raise PublishError(f"{platform} 캡션 없음")
    limit = config.CAPTION_LIMITS[platform]
    if len(cap) > limit:
        raise PublishError(f"{platform} 캡션 {len(cap)}자 > {limit}자")
    if not policy.has_disclaimer(cap):
        raise PublishError(f"{platform} 캡션에 면책 문구 없음")
    return cap


def build_entries(script: Script, accounts: dict[str, str]) -> dict[str, dict[str, Any]]:
    """platform → platforms[] 엔트리. 검증 실패 시 PublishError (외부 호출 전)."""
    if not (script.title or "").strip():
        raise PublishError("제목 없음")
    title = script.title.strip()[:config.YOUTUBE_TITLE_LIMIT]
    out: dict[str, dict[str, Any]] = {}
    for p in config.PLATFORMS:
        if p not in accounts:
            continue
        acct = accounts[p]
        if not acct:
            raise PublishError(f"{p} 계정 ID 없음")
        cap = _caption(script, p)
        if p == "instagram":
            psd: dict[str, Any] = {"shareToFeed": True, "thumbOffset": 2000}
        elif p == "threads":
            psd = {"topic_tag": config.THREADS_TOPIC_TAG}
        elif p == "youtube":
            psd = {"title": title, "visibility": "public", "madeForKids": False,
                   "containsSyntheticMedia": True}
        else:
            psd = {"contentType": "reel", "title": title}
        out[p] = {"platform": p, "accountId": acct, "customContent": cap, "platformSpecificData": psd}
    if not out:
        raise PublishError("발행 대상 계정 없음")
    return out


def plan_requests(episode_id: str, media: dict[str, dict[str, str]], fb_variant: str,
                  entries: dict[str, dict[str, Any]] | None = None, title: str = "",
                  scheduled_iso: str = "") -> list[dict[str, Any]]:
    """요청 4건 명세(플랫폼당 1건). entries 를 주면 각 명세에 body 를 채운다.

    DN2026_0085: main 키의 Instagram·Threads 를 한 요청으로 묶으면 한 계정의 403(계정 미연결 등)이
    다른 채널까지 실패시킨다(W44 베타). 플랫폼별로 나눠 실패를 격리한다.
    """
    if fb_variant not in FB_VARIANTS:
        raise PublishError(f"fb_variant 허용값 아님: {fb_variant}")
    raw = (("main", ["instagram"], "main", f"{episode_id}:main:instagram"),
           ("main", ["threads"], "main", f"{episode_id}:main:threads"),
           ("sub", ["youtube"], "main", f"{episode_id}:sub:youtube"),
           ("sub", ["facebook"], fb_variant, f"{episode_id}:sub:facebook"))
    specs: list[dict[str, Any]] = []
    for alias, plats, variant, idem in raw:
        url = (media.get(variant) or {}).get(alias)
        if not url or not str(url).lower().startswith("https://"):
            raise PublishError(f"미디어 공개 URL 없음/비HTTPS: variant={variant} key_alias={alias}")
        spec: dict[str, Any] = {"key_alias": alias, "platforms": plats, "variant": variant,
                                "media_url": url, "idempotency_key": idem}
        if entries is not None:
            spec["body"] = build_body(url, [entries[p] for p in plats if p in entries], title, scheduled_iso)
        specs.append(spec)
    return specs


def build_body(media_url: str, entries: list[dict[str, Any]], title: str, scheduled_iso: str) -> dict[str, Any]:
    if not entries:
        raise PublishError("platforms 비어 있음")
    if not scheduled_iso:
        raise PublishError("scheduledFor 필수 — publishNow 금지")
    return {"mediaItems": [{"url": media_url, "type": "video"}], "platforms": [dict(e) for e in entries],
            "content": title, "scheduledFor": scheduled_iso, "timezone": "Asia/Seoul"}


def accounts_from_env() -> dict[str, str]:
    return {p: secret_env.require(config.ACCOUNT_ENV[p]) for p in config.PLATFORMS}


# --------------------------------------------------------------------------- #
# 계정 진단 (DN2026_0084) — 읽기 전용. 키(main/sub)별 /accounts/health 에서
#   플랫폼 계정 수와 설정 계정 ID(NUNA_*_ACCOUNT_ID)가 어느 키에 있는지 판정한다. ID 값은 반환하지 않는다.
# --------------------------------------------------------------------------- #
def _row_ids(row: dict[str, Any]) -> set[str]:
    return {str(row.get(k)) for k in ("accountId", "_id", "id") if row.get(k) not in (None, "")}


def account_report(health_by_alias: dict[str, Any], accounts: dict[str, str]) -> list[dict[str, Any]]:
    """platform 별 진단 행. health 가 None 이면 그 키는 조회 실패로 표시한다."""
    out: list[dict[str, Any]] = []
    for p in config.PLATFORMS:
        acct = accounts.get(p) or ""
        row: dict[str, Any] = {"platform": p, "mapped_alias": config.KEY_ALIAS[p], "account_set": bool(acct),
                               "per_alias": {}, "found_in": []}
        for alias in sorted(health_by_alias):
            health = health_by_alias[alias]
            rows = health.get("accounts") if isinstance(health, dict) else None
            if not isinstance(rows, list):
                row["per_alias"][alias] = {"ok": False}
                continue
            cands = [a for a in rows if isinstance(a, dict) and str(a.get("platform", "")).lower() == p]
            match = [a for a in rows if isinstance(a, dict) and acct and acct in _row_ids(a)]
            info: dict[str, Any] = {"ok": True, "platform_accounts": len(cands), "id_match": bool(match)}
            if match:
                m = match[0]
                info.update({"match_platform": str(m.get("platform", "")).lower(),
                             "canPost": m.get("canPost"), "needsReconnect": m.get("needsReconnect")})
                row["found_in"].append(alias)
            row["per_alias"][alias] = info
        row["mapping_ok"] = row["mapped_alias"] in row["found_in"]
        out.append(row)
    return out


def account_report_lines(report: list[dict[str, Any]]) -> list[str]:
    """GitHub annotation 한 줄씩 (계정 ID 미포함)."""
    lines = []
    for r in report:
        parts = []
        for alias, info in r["per_alias"].items():
            if not info.get("ok"):
                parts.append(f"{alias}=조회실패")
                continue
            s = f"{alias}: {r['platform']}계정 {info['platform_accounts']}건·설정ID {'있음' if info['id_match'] else '없음'}"
            if info["id_match"]:
                s += (f"(platform={info.get('match_platform')}, canPost={info.get('canPost')}, "
                      f"needsReconnect={info.get('needsReconnect')})")
            parts.append(s)
        verdict = "OK" if r["mapping_ok"] else ("ID 미설정" if not r["account_set"] else
                                               (f"매핑 불일치 → {','.join(r['found_in'])}" if r["found_in"]
                                                else "어느 키에도 없음"))
        lines.append(f"[{r['platform']}] 코드 매핑={r['mapped_alias']} · 판정={verdict} · " + " / ".join(parts))
    return lines


# --------------------------------------------------------------------------- #
# 계정 헬스 — hero zernio_publish_watch.poll_health / runner2 A-G0 형식 기준
#   {"accounts":[{"platform","status","canPost","tokenValid","needsReconnect","issues"}]}
# --------------------------------------------------------------------------- #
def health_blocks(health: Any, platforms: list[str], accounts: dict[str, str]) -> dict[str, str]:
    """platform → 차단 사유. 형식 불명·계정 특정 불가 → 차단하지 않고 로그만."""
    blocked: dict[str, str] = {}
    rows = health.get("accounts") if isinstance(health, dict) else None
    if not isinstance(rows, list):
        log.warning("[NunaWeekly:publish] accounts/health 응답 형식 불명 — 차단 없이 진행")
        return blocked
    for p in platforms:
        cands = [a for a in rows if isinstance(a, dict) and str(a.get("platform", "")).lower() == p]
        acct = accounts.get(p)
        exact = [a for a in cands if acct and acct in (a.get("accountId"), a.get("_id"), a.get("id"))]
        chosen = exact[0] if exact else (cands[0] if len(cands) == 1 else None)
        if chosen is None:
            log.warning("[NunaWeekly:publish] %s 헬스 항목 특정 불가(후보 %d건) — 차단 없이 진행", p, len(cands))
            continue
        if chosen.get("canPost") is False or chosen.get("needsReconnect") is True:
            blocked[p] = (f"ACCOUNT_NOT_POSTABLE status={chosen.get('status')} canPost={chosen.get('canPost')} "
                          f"needsReconnect={chosen.get('needsReconnect')}")
        elif "canPost" not in chosen:
            log.warning("[NunaWeekly:publish] %s 헬스 canPost 필드 없음 — 차단 없이 진행", p)
    return blocked


# --------------------------------------------------------------------------- #
# 발행
# --------------------------------------------------------------------------- #
def _row_idem(header_key: str, platforms: list[str], platform: str) -> str:
    """원장 idempotency_key 는 (episode,platform) 행마다 유일해야 한다 — 다채널 요청은 #platform 접미."""
    return header_key if len(platforms) == 1 else f"{header_key}#{platform}"


def _header_key(base: str, send: list[str], plats: list[str], existing: dict[str, dict[str, Any]],
                attempts: dict[str, int]) -> str:
    """Idempotency-Key 헤더.

    · 기본: 설계 키 그대로(예: "<ep>:main:instagram").
    · 요청 플랫폼 일부만 보낼 때: ":<p1>+<p2>" 접미 — 본문이 달라지므로 다른 키여야 한다.
    · 직전 확정 실패 재시도: ":r<attempts>" 접미 — 24h 내 동일 키는 원본(실패) 응답을 돌려준다.
    """
    key = base
    if list(send) != list(plats):
        key += ":" + "+".join(send)
    failed = [attempts[p] for p in send if (existing.get(p) or {}).get("status") == "failed"]
    if failed and max(failed) > 0:
        key += f":r{max(failed)}"
    return key


def publish(episode_id: str, store: Any, clients: dict[str, Any], script: Script,
            media: dict[str, dict[str, str]], accounts: dict[str, str], notified_at: datetime | None,
            publish_date: date, fb_variant: str, dry: bool) -> dict[str, TargetResult]:
    log.info("[NunaWeekly:publish] v%s 시작 — %s dry=%s", VERSION, episode_id, dry)
    # 1) 검증 — 외부 호출 전
    entries = build_entries(script, accounts)
    missing = [p for p in config.PLATFORMS if p not in entries]
    if missing:
        raise PublishError(f"계정 누락 채널: {missing}")
    sched_iso = iso_kst(scheduled_for(publish_date, notified_at))
    title = script.title.strip()[:config.YOUTUBE_TITLE_LIMIT]
    specs = plan_requests(episode_id, media, fb_variant, entries, title, sched_iso)
    for s in specs:
        if s["key_alias"] not in clients:
            raise PublishError(f"클라이언트 없음: key_alias={s['key_alias']}")

    existing = {t["platform"]: t for t in store.list_targets(episode_id)}
    results: dict[str, TargetResult] = {}
    health_cache: dict[str, Any] = {}

    for spec in specs:
        alias, plats = spec["key_alias"], spec["platforms"]
        todo: list[str] = []
        for p in plats:
            prev = existing.get(p) or {}
            if prev.get("status") in DONE_STATES:
                log.info("[NunaWeekly:publish] %s 이미 %s — 건너뜀", p, prev["status"])
                results[p] = TargetResult(p, prev["status"], prev.get("zernio_post_id"),
                                          prev.get("platform_post_url"))
            elif (prev.get("zernio_post_id") or
                  (prev.get("status") == "pending" and int(prev.get("attempts") or 0) > 0)):
                # 타임아웃/응답 후 DB 기록 중단은 생성 여부가 불명확하다.
                # 계정 헬스·부분 플랫폼 구성이 달라져 새 키를 만들지 않는다.
                results[p] = TargetResult(p, prev.get("status") or "pending", prev.get("zernio_post_id"),
                                          error="RECONCILE_REQUIRED: 기존 요청 조회 후 처리")
                log.warning("[NunaWeekly:publish] %s 기존 요청 대사 필요 — POST 차단", p)
            elif int(prev.get("attempts") or 0) >= 5:
                results[p] = TargetResult(p, "blocked", error="ATTEMPTS_EXHAUSTED")
                log.warning("[NunaWeekly:publish] %s 재시도 상한 도달 — POST 차단", p)
            else:
                todo.append(p)
        if not todo:
            continue
        attempts = {p: int((existing.get(p) or {}).get("attempts") or 0) for p in todo}
        header_key = _header_key(spec["idempotency_key"], todo, plats, existing, attempts)
        body = build_body(spec["media_url"], [entries[p] for p in todo], title, sched_iso)
        red_req = redact_obj({"idempotency_key": header_key, "key_alias": alias, "body": body})

        for p in todo:
            store.upsert_target({"episode_id": episode_id, "platform": p, "key_alias": alias,
                                 "variant": spec["variant"], "status": "pending",
                                 "idempotency_key": _row_idem(header_key, plats, p),
                                 "attempts": attempts[p]})

        if dry:
            for p in todo:
                store.update_target(episode_id, p, {"status": "skipped", "last_error": "DRY_RUN",
                                                    "request_json": red_req})
                results[p] = TargetResult(p, "skipped", error="DRY_RUN")
            log.info("[NunaWeekly:publish] DRY_RUN — %s 본문 검증만 (POST 없음)", todo)
            continue

        client = clients[alias]
        # 2) 계정 헬스
        if alias not in health_cache:
            try:
                health_cache[alias] = client.accounts_health()
            except Exception as e:
                log.warning("[NunaWeekly:publish] 헬스 조회 실패(%s) — 차단 없이 진행: %s",
                            alias, secret_env.redact(f"{type(e).__name__}: {e}", 200))
                health_cache[alias] = None
        blocks = health_blocks(health_cache[alias], todo, accounts) if health_cache[alias] is not None else {}
        for p, reason in blocks.items():
            store.update_target(episode_id, p, {"status": "blocked", "last_error": reason[:500]})
            results[p] = TargetResult(p, "blocked", error=reason)
            log.warning("[NunaWeekly:publish] %s 차단: %s", p, reason)
        send = [p for p in todo if p not in blocks]
        if not send:
            continue
        if len(send) != len(todo):
            header_key = _header_key(spec["idempotency_key"], send, plats, existing, attempts)
            body = build_body(spec["media_url"], [entries[p] for p in send], title, sched_iso)
            red_req = redact_obj({"idempotency_key": header_key, "key_alias": alias, "body": body})

        # 3) POST
        # 전송 전에 멱등 키와 전송 시도를 기록한다. DB 기록 실패 시 POST하지 않는다.
        for p in send:
            store.update_target(episode_id, p, {"idempotency_key": _row_idem(header_key, plats, p),
                                                "request_json": red_req, "attempts": attempts[p] + 1,
                                                "last_error": "SUBMISSION_STARTED"})
        try:
            http, resp = client.create_post(body, header_key)
        except Exception as e:  # 전송 결과 불명 — 원장엔 pending 유지(같은 키로 재시도 → 이중발행 방지)
            err = secret_env.redact(f"TRANSPORT_UNKNOWN: {type(e).__name__}: {e}", 500)
            for p in send:
                store.update_target(episode_id, p, {"status": "pending", "attempts": attempts[p] + 1,
                                                    "last_error": err, "request_json": red_req})
                results[p] = TargetResult(p, "pending", error=err)
            log.error("[NunaWeekly:publish] %s 전송 실패 — 다른 채널 계속: %s", send, err)
            continue
        decided = decompose(send, http, resp, scheduled=True)
        if http >= 500 or http in (408, 409, 429):
            for p in send:
                if decided[p].status == "failed":
                    decided[p].status = "pending"
                    decided[p].error = f"RECONCILE_REQUIRED: HTTP {http} 결과 불명"
        red_resp = redact_obj({"http_status": http, "body": resp})
        for p in send:
            r = decided[p]
            if r.status == "published":
                r.status = "scheduled"  # 예약 발행 직후 published 는 verify 에서 확정한다
                r.platform_post_url = None
            if r.status == "scheduled" and not r.zernio_post_id:
                r.status = "pending"
                r.error = "RECONCILE_REQUIRED: 예약 응답에 post ID 없음"
            fields: dict[str, Any] = {"status": r.status, "zernio_post_id": r.zernio_post_id,
                                      "attempts": attempts[p] + 1,
                                      "last_error": secret_env.redact(r.error, 500) if r.error else None,
                                      "request_json": red_req, "response_json": red_resp}
            store.update_target(episode_id, p, fields)
            results[p] = r
        log.info("[NunaWeekly:publish] %s HTTP %s → %s", alias, http, {p: decided[p].status for p in send})
    return results


# --------------------------------------------------------------------------- #
# 검증 — 예약 이후 실제 발행 확인
# --------------------------------------------------------------------------- #
def verify(episode_id: str, store: Any, clients: dict[str, Any], max_rounds: int = 3,
           interval_sec: int = 300, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    log.info("[NunaWeekly:publish] v%s verify 시작 — %s", VERSION, episode_id)
    published: list[str] = []
    failed: list[str] = []
    rounds = 0
    for rnd in range(max(1, max_rounds)):
        rounds = rnd + 1
        rows = [t for t in store.list_targets(episode_id)
                if t.get("status") in ("scheduled", "pending", "failed") and t.get("zernio_post_id")]
        if not rows:
            break
        groups: dict[tuple[str, str], list[str]] = {}
        for t in rows:
            groups.setdefault((t.get("key_alias") or config.KEY_ALIAS[t["platform"]], str(t["zernio_post_id"])),
                              []).append(t["platform"])
        for (alias, pid), plats in groups.items():
            client = clients.get(alias)
            if client is None:
                log.warning("[NunaWeekly:publish] verify 클라이언트 없음: %s", alias)
                continue
            try:
                body = client.get_post(pid)
            except Exception as e:
                log.warning("[NunaWeekly:publish] GET /posts 실패 %s: %s", plats,
                            secret_env.redact(f"{type(e).__name__}: {e}", 200))
                continue
            dec = decompose(plats, 200, body, scheduled=True)
            for p in plats:
                r = dec[p]
                if r.status == "published":
                    store.update_target(episode_id, p, {"status": "published",
                                                        "platform_post_url": r.platform_post_url})
                    published.append(p)
                elif r.status == "failed":
                    store.update_target(episode_id, p, {"status": "failed",
                                                        "last_error": secret_env.redact(r.error, 500)})
                    failed.append(p)
                elif r.status == "scheduled":
                    store.update_target(episode_id, p, {"status": "scheduled"})
        remaining = [t["platform"] for t in store.list_targets(episode_id)
                     if t.get("status") in ("scheduled", "pending")]
        if not remaining:
            break
        if rnd + 1 < max_rounds:
            sleep(interval_sec)
    pending = [t["platform"] for t in store.list_targets(episode_id)
               if t.get("status") in ("scheduled", "pending")]
    log.info("[NunaWeekly:publish] verify 종료 — published=%s failed=%s pending=%s", published, failed, pending)
    return {"published": published, "failed": failed, "pending": pending, "rounds": rounds}
