"""
gen/hero_shorts/tiktok/runner.py — 틱톡 라인 CLI
================================================
    python3 -m gen.hero_shorts.tiktok.runner manifest --plan <7컷JSON> --title <제목> [--hook <훅>] --out <매니페스트JSON>
    python3 -m gen.hero_shorts.tiktok.runner combined --title <제목> --duration <초> [--media-url <공개URL>] --out <매니페스트JSON>
    python3 -m gen.hero_shorts.tiktok.runner gates    --manifest <매니페스트JSON>
    python3 -m gen.hero_shorts.tiktok.runner preflight [--account-id <_id>]
    python3 -m gen.hero_shorts.tiktok.runner publish  --manifest <매니페스트JSON> --segment <1|2|3> [--media-url <공개URL>] [--schedule-at <ISO8601>]

발행은 게이트 통과 편만 수행한다(fail-closed). 실제 발행은 크레딧/과금이 아닌
Zernio API 호출이지만, 마스터 운영 규칙상 실행 전 확인 대상이다.
"""
import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import VERSION
from . import account as tk_account
from . import manifest as tk_manifest
from . import publish as tk_publish


def _load_cuts(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("cuts", [])
    return data


def cmd_manifest(args):
    cuts = _load_cuts(args.plan)
    m = tk_manifest.build_manifest(cuts, args.title, hook=args.hook,
                                   max_duration_sec=args.max_duration,
                                   privacy_level=args.privacy_level, draft=args.draft)
    out = args.out or "gen/data/out/tiktok_manifest.json"
    tk_manifest.save_manifest(out, m)
    print(json.dumps({
        "out": out,
        "segment_count": m["segment_count"],
        "total_duration_sec": m["total_duration_sec"],
        "privacy_level": m["privacy_level"],
        "draft": m["draft"],
        "all_gates_ok": m["all_gates_ok"],
    }, ensure_ascii=False))
    return 0 if m["all_gates_ok"] else 2


def cmd_gates(args):
    m = tk_manifest.load_manifest(args.manifest)
    failed = [s["segment_no"] for s in m["segments"] if not s["gates"]["ok"]]
    print(json.dumps({"all_gates_ok": m["all_gates_ok"], "blocked_segments": failed},
                     ensure_ascii=False))
    return 0 if m["all_gates_ok"] else 2


def cmd_combined(args):
    from ..timestamps import probe_duration

    if args.duration is not None:
        dur = float(args.duration)
    elif args.video:
        dur = probe_duration(args.video)
    else:
        print("합본 매니페스트 — --duration 미지정 시 --video 필수(ffprobe 실측)", file=sys.stderr)
        return 64
    m = tk_manifest.from_combined(
        args.video, dur, args.title, hook=args.hook,
        max_duration_sec=args.max_duration,
        privacy_level=args.privacy_level, draft=args.draft,
        media_url=args.media_url,
    )
    out = args.out or "gen/data/out/tiktok_combined.json"
    tk_manifest.save_manifest(out, m)
    print(json.dumps({
        "out": out,
        "mode": m["mode"],
        "segment_count": m["segment_count"],
        "total_duration_sec": m["total_duration_sec"],
        "privacy_level": m["privacy_level"],
        "draft": m["draft"],
        "all_gates_ok": m["all_gates_ok"],
    }, ensure_ascii=False))
    return 0 if m["all_gates_ok"] else 2


def cmd_preflight(args):
    """0크레딧 토큰 사전 점검 + 이력 적재(B안) + 경보(A안)."""
    from . import next_run as tk_next_run
    nx = tk_next_run.apply(force=True)
    health = tk_account.check_token_health(account_id=args.account_id,
                                           warn_hours=args.warn_hours)
    hist = None
    if not args.no_history:
        hist = tk_account.record_observation(health, source=args.source,
                                             do_mirror=args.mirror_history)
    payload = health.to_dict()
    payload["next_run"] = nx
    if hist:
        payload["history"] = hist
    print(json.dumps(payload, ensure_ascii=False))
    if not args.no_notify and health.status != tk_account.OK:
        tk_account.send_alert(health)
    if health.blocking:
        print(tk_account.format_alert(health), file=sys.stderr)
        return 70
    if health.status != tk_account.OK:
        print(tk_account.format_alert(health), file=sys.stderr)
    return 0


def cmd_token_history(args):
    """B안 CLI — 이력 리포트/판정/소급/정리(0크레딧)."""
    from . import token_history as th
    if args.action == "backfill":
        entries = []
        for f in args.files:
            try:
                entries.append({"name": Path(f).name,
                                "text": Path(f).read_text(encoding="utf-8", errors="replace")})
            except Exception as exc:  # noqa: BLE001
                print(f"경고: {f} 읽기 실패 — {exc}", file=sys.stderr)
        print(json.dumps(th.backfill(entries, path=args.path), ensure_ascii=False, indent=1))
        return 0
    if args.action == "prune":
        print(json.dumps(th.prune(args.path, args.keep), ensure_ascii=False, indent=1))
        return 0
    until = args.until
    if until in (None, "", "auto") and not getattr(args, "no_query", False):
        from . import next_run as tk_next_run
        until = tk_next_run.apply(force=True).get("next_run_at")
    obs = th.load(args.path)["observations"]
    summary = th.summarize(obs)
    verify = th.verify_until(obs, until) if until else None
    if args.json:
        print(json.dumps({"summary": summary, "verify": verify}, ensure_ascii=False, indent=1))
    else:
        print(th.format_report(summary, verify))
    return 0


def cmd_verify(args):
    """발행 후 플랫폼 대조 검증(읽기 전용 · 0크레딧).

    exit 0 = 검증 통과 / 4 = 불일치·미발견(발행 실패와 구분) / 5 = 조회 불가.
    """
    from . import verify as tk_verify
    entry = None
    if args.ep is not None:
        state = load_publish_state(_state_path(getattr(args, "state", None)))
        entry = state.get(f"ep{args.ep}")
        if entry is None:
            print(f"ep{args.ep} 원장 없음", file=sys.stderr)
            return 66
    tk = (entry or {}).get("tiktok") or {}
    v = tk_verify.verify_published(
        post_id=args.post_id or tk.get("post_id"),
        post_url=args.post_url or tk.get("post_url") or tk.get("platform_post_url"),
        account_id=args.account_id,
        limit=args.verify_limit,
        allow_latest=args.recover_latest,
    )
    led = tk_verify.check_ledger(entry, v) if entry else {"problems": [], "drift_min": None}
    payload = {"verdict": v, "ledger": led}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(tk_verify.format_verdict(v, ledger_problems=led.get("problems"), ep=args.ep))
    if args.notify and (not v.get("ok") or led.get("problems")):
        tk_verify.alert(v, ledger_problems=led.get("problems"), ep=args.ep)
    if v.get("verdict") == tk_verify.UNKNOWN:
        return 5
    return 0 if (v.get("ok") and not led.get("problems")) else 4


def cmd_publish(args):
    m = tk_manifest.load_manifest(args.manifest)
    seg = next((s for s in m["segments"] if s["segment_no"] == args.segment), None)
    if seg is None:
        print(f"segment {args.segment} 없음", file=sys.stderr)
        return 64
    if args.media_url:
        seg["media_url"] = args.media_url
    result = tk_publish.publish_segment(seg, account_id=args.account_id,
                                        fallback_media_url=getattr(args, "fallback_media_url", None),
                                        schedule_at=args.schedule_at,
                                        privacy_level=args.privacy_level,
                                        preflight=not args.skip_preflight)
    # 발행 후 플랫폼 대조 검증(읽기 전용) — "발행했다"를 자기 보고로 끝내지 않는다.
    if result.get("outcome") == "published" and not getattr(args, "no_verify", False):
        from . import verify as tk_verify
        v = tk_verify.verify_published(
            post_id=result.get("post_id"),
            post_url=result.get("post_url") or result.get("platform_post_url"),
            account_id=args.account_id,
            limit=getattr(args, "verify_limit", 20) or 20,
        )
        result["verification"] = v
        if not v.get("ok"):
            result["verification_alerted"] = tk_verify.alert(v, ep=getattr(args, "ep", None))
            print(tk_verify.format_verdict(v, ep=getattr(args, "ep", None)), file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("outcome") == "published" else 2


READY_EXIT_NONE = 10        # 발행 대상 없음(정상 종료 — 미승인/보류/무응답/전량 발행 완료)
READY_EXIT_NO_STATE = 66    # 발행 원장 부재 — statestore 복원 실패(설계 F-1)


def _state_path(explicit=None):
    """발행 원장 경로 — pipeline.py 와 동일 규칙(HERO_STATE_FILE 우선)."""
    if explicit:
        return Path(explicit)
    env = os.getenv("HERO_STATE_FILE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "publish_state.json"


def load_publish_state(path=None):
    p = _state_path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"발행 원장 없음: {p} — statestore.restore_if_missing(AI Drive /hero-shorts/) 선행 필요"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def collect_ready(state):
    """원장 → 발행 대상 목록(승인 완료 + 틱톡 미발행).

    없는 필드는 추측하지 않고 None 으로 남기고 missing 에 이름을 적는다(설계 F-3).
    반환: (items, stats)
    """
    items = []
    stats = {"published": 0, "not_approved": 0, "no_approval": 0}
    for key, val in state.items():
        tag = str(key)
        if not (tag.startswith("ep") and tag[2:].isdigit()) or not isinstance(val, dict):
            continue
        ap = val.get("approval") if isinstance(val.get("approval"), dict) else None
        tk = val.get("tiktok") if isinstance(val.get("tiktok"), dict) else {}
        if tk.get("status") == "published":          # 멱등성 — 재발행 금지
            stats["published"] += 1
            continue
        if ap is None:
            stats["no_approval"] += 1
            continue
        if ap.get("status") != "APPROVED":           # fail-closed — 승인 없으면 발행 없음
            stats["not_approved"] += 1
            continue
        comb = val.get("combined") if isinstance(val.get("combined"), dict) else {}
        item = {
            "ep": int(tag[2:]),
            "title": comb.get("title") or val.get("title"),
            "duration_sec": comb.get("duration_sec"),
            "media_url": comb.get("media_url") or ap.get("media_url"),
            "aidrive_path": comb.get("aidrive_path"),
            "video_path": comb.get("video_path"),
            "decided_at": ap.get("decided_at"),
        }
        item["missing"] = [f for f in ("title", "duration_sec", "media_url") if not item.get(f)]
        items.append(item)
    items.sort(key=lambda i: i["ep"])
    return items, stats



def cmd_record(args):
    """러너3 S7 — 발행 결과를 원장에 기록 + AI Drive 즉시 미러링. 멱등(재기록 방지).

    2026-10-15 실측 결함 2건을 코드 경로로 봉합한다.
      D-2: post_id/platform_post_id/segments/mode 누락 → Zernio 실조회로 회수.
      D-3: published_at 에 로컬 now 가 박힘(플랫폼 값과 46분 불일치) → 플랫폼 값 우선.
    회수 실패 시 추측하지 않고 `published_at_source` 로 출처를 명시한다(감사 정직성).
    """
    p = _state_path(getattr(args, "state", None))
    state = load_publish_state(p)
    key = f"ep{args.ep}"
    rec = state.setdefault(key, {})
    tk = rec.setdefault("tiktok", {})
    if tk.get("status") == "published" and not getattr(args, "force", False):
        print(json.dumps({"ok": False, "reason": "already_published",
                          "post_url": tk.get("post_url")}, ensure_ascii=False))
        return 0
    now = datetime.now(UTC).isoformat()
    post_url = args.post_url or tk.get("post_url") or tk.get("platform_post_url")
    post_id = args.post_id or tk.get("post_id")
    platform_post_id = args.platform_post_id or tk.get("platform_post_id")
    published_at = args.published_at or tk.get("published_at")
    source = "arg" if args.published_at else ("ledger" if published_at else None)

    recovered = None
    needs_recovery = not (post_id and platform_post_id and published_at)
    if args.status == "published" and needs_recovery \
            and not getattr(args, "no_recover", False):
        try:
            from .recover import resolve_published_post
            recovered = resolve_published_post(
                post_url=post_url, post_id=post_id,
                account_id=getattr(args, "account_id", None),
                limit=getattr(args, "recover_limit", 20),
                allow_latest=bool(getattr(args, "recover_latest", False)),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"발행 결과 회수 실패(원장은 기록): {exc}", file=sys.stderr)
        if recovered:
            post_url = post_url or recovered.get("platform_post_url")
            post_id = post_id or recovered.get("post_id")
            platform_post_id = platform_post_id or recovered.get("platform_post_id")
            if recovered.get("published_at") and not args.published_at:
                published_at = recovered["published_at"]
                source = "zernio_platform"

    if not published_at:
        published_at = now
        source = "local_now"

    tk.update({
        "status": args.status,
        "post_id": post_id,
        "platform_post_id": platform_post_id,
        "post_url": post_url,
        "platform_post_url": post_url,
        "published_at": published_at,
        "published_at_source": source,
        "recorded_at": now,
    })
    if recovered:
        tk["recovered_by"] = recovered.get("matched_by")
    if args.status == "published" and not tk.get("post_url"):
        print(json.dumps({"ok": False, "reason": "post_url_required"}, ensure_ascii=False))
        return 2

    privacy_level = (getattr(args, "privacy_level", None) or tk.get("privacy_level")
                     or os.environ.get("HERO_TIKTOK_PRIVACY_LEVEL"))
    if privacy_level:
        tk["privacy_level"] = privacy_level
    if args.status == "published":
        # D-2 — ep86/ep87 과 형식 정합(mode·segment_count·segments[])을 코드로 보장한다.
        tk.setdefault("mode", getattr(args, "mode", None) or tk.get("mode") or "combined")
        tk.setdefault("segment_count", tk.get("segment_count") or 1)
        seg_no = getattr(args, "segment", None)
        if seg_no is None and tk.get("mode") == "combined":
            seg_no = 1
        segs = tk.setdefault("segments", [])
        entry = {
            "segment_no": seg_no,
            "post_id": post_id,
            "platform_post_id": platform_post_id,
            "platform_post_url": post_url,
            "published_at": published_at,
            "outcome": args.status,
        }
        dup = any((s.get("post_id") and post_id and s.get("post_id") == post_id)
                  or (s.get("platform_post_url") and post_url
                      and s.get("platform_post_url") == post_url)
                  for s in segs if isinstance(s, dict))
        if not dup:
            segs.append(entry)

    if getattr(args, "media_url", None):
        comb = rec.setdefault("combined", {})
        comb["media_url"] = args.media_url
        comb["media_url_source"] = "zernio_ingested"
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    mirrored = False
    try:
        from ..statestore import sync_states
        mirrored = bool(sync_states([p]))
    except Exception as exc:  # noqa: BLE001
        print(f"미러링 실패(무시): {exc}", file=sys.stderr)
    print(json.dumps({"ok": True, "ep": args.ep, "status": args.status,
                      "post_id": post_id, "platform_post_id": platform_post_id,
                      "post_url": tk.get("post_url"),
                      "published_at": published_at, "published_at_source": source,
                      "recovered": bool(recovered), "mirrored": mirrored},
                     ensure_ascii=False))
    return 0


def cmd_ready(args):
    """러너3 S3 — 발행 대상 회차를 JSON 으로 출력한다. 발행 없음 · 0크레딧."""
    p = _state_path(getattr(args, "state", None))
    if not p.exists():
        # D-1: 원장 부재는 '발행 0건'이 아니라 '조용한 미실행'이다. 먼저 복원을 시도한다.
        try:
            from ..statestore import restore_if_missing
            if restore_if_missing([p]):
                print(json.dumps({"ok": True, "recovered": str(p)}, ensure_ascii=False),
                      file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"원장 복원 시도 실패(계속): {exc}", file=sys.stderr)
    try:
        state = load_publish_state(p)
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "reason": "state_missing", "detail": str(exc)},
                         ensure_ascii=False))
        return READY_EXIT_NO_STATE
    items, stats = collect_ready(state)
    ready = [i for i in items if not i["missing"]]
    payload = {
        "ok": bool(items),
        "count": len(items),
        "ready_count": len(ready),
        "incomplete": [{"ep": i["ep"], "missing": i["missing"]} for i in items if i["missing"]],
        "items": items,
        "skipped": stats,
    }
    print(json.dumps(payload, ensure_ascii=False))
    if items:
        return 0
    print("발행 대상 없음 — 미승인/보류/무응답 또는 전량 발행 완료", file=sys.stderr)
    return READY_EXIT_NONE


def cmd_next_run(args):
    """B안 후속 — 다음 실행 시각 해석(및 --apply 시 env 주입). 0크레딧."""
    from . import next_run as tk_next_run
    if args.apply:
        res = tk_next_run.apply(force=True, query=not args.no_query)
    else:
        res = tk_next_run.resolve(query=not args.no_query)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(tk_next_run.report_line(res))
        if res.get("reason"):
            print(f"사유: {res['reason']}")
    return 0 if res.get("next_run_at") else 3


def cmd_backfill(args):
    """원장 소급 복구 — 기본 dry-run, --apply 로만 반영."""
    from . import backfill as tk_backfill
    argv = ["--ledger", args.ledger, "--limit", str(args.backfill_limit)]
    for ep in (args.ep or []):
        argv += ["--ep", ep]
    if args.account_id:
        argv += ["--account-id", args.account_id]
    if args.apply:
        argv.append("--apply")
    if args.out:
        argv += ["--out", args.out]
    if args.mirror:
        argv.append("--mirror")
    if args.as_json:
        argv.append("--json")
    return tk_backfill.main(argv)


def cmd_catchup(args):
    """러너3 회수 계획 — 읽기 전용. 발행하지 않는다."""
    from . import catchup as tk_catchup
    argv = ["--state", str(_state_path(getattr(args, "state", None))),
            "--max-publish", str(args.max_publish),
            "--retention-hours", str(args.retention_hours)]
    if args.cron:
        argv += ["--cron", args.cron]
    if args.tz:
        argv += ["--tz", args.tz]
    if args.now:
        argv += ["--now", args.now]
    if args.as_json:
        argv.append("--json")
    return tk_catchup.main(argv)


def cmd_reapprove(args):
    """승인 요청 재발행 위임 — 기본 dry-run, --send 로만 전송."""
    from .. import reapproval as ra
    argv = ["--state", str(_state_path(getattr(args, "state", None))),
            "--max-requests", str(args.max_requests),
            "--cooldown-hours", str(args.cooldown_hours),
            "--retention-hours", str(args.retention_hours)]
    for ep in (args.ep or []):
        argv += ["--ep", str(ep)]
    if args.now:
        argv += ["--now", args.now]
    if args.send:
        argv.append("--send")
    if args.as_json:
        argv.append("--json")
    return ra.main(argv)


def main(argv=None):
    ap = argparse.ArgumentParser(description=f"tiktok line v{VERSION}")
    sub = ap.add_subparsers(dest="step", required=True)

    p = sub.add_parser("manifest", help="7컷 → 3편 매니페스트 생성")
    p.add_argument("--plan", required=True, help="7컷 컷계획 JSON 경로")
    p.add_argument("--title", required=True, help="회차 제목(캡션 헤드)")
    p.add_argument("--hook", default=None, help="편1 세계관 훅(선택)")
    p.add_argument("--out", default=None, help="매니페스트 저장 경로")
    p.add_argument("--max-duration", type=int, default=tk_manifest.DEFAULT_MAX_DURATION_SEC)
    p.add_argument("--privacy-level", default=None, help="TikTok 공개 레벨(기본 PUBLIC_TO_EVERYONE)")
    p.add_argument("--draft", action="store_true", help="TikTok 인박스 초안으로 업로드(비공개 경로)")
    p.set_defaults(func=cmd_manifest)

    pf = sub.add_parser("preflight", help="틱톡 토큰 유효성 사전 점검(0크레딧)")
    pf.add_argument("--account-id", default=None, help="미지정 시 HERO_TIKTOK_ACCOUNT_ID")
    pf.add_argument("--warn-hours", type=float, default=None, help="만료 임박 임계(기본 2h)")
    pf.add_argument("--source", default="runner.preflight", help="이력 출처 라벨")
    pf.add_argument("--no-history", dest="no_history", action="store_true",
                    help="토큰 이력 적재 생략(B안)")
    pf.add_argument("--mirror-history", dest="mirror_history", action="store_true",
                    help="이력 적재 후 AI Drive 미러링")
    pf.add_argument("--no-notify", dest="no_notify", action="store_true",
                    help="경보 발송 생략(판정만)")
    pf.set_defaults(func=cmd_preflight)

    c = sub.add_parser("combined", help="합본 1편 매니페스트 생성(정기 발행 기본)")
    c.add_argument("--video", default=None, help="합본 영상 파일 경로(미지정 시 --duration 필수)")
    c.add_argument("--duration", type=float, default=None, help="미지정 시 ffprobe 실측")
    c.add_argument("--title", required=True, help="회차 제목(캡션 헤드)")
    c.add_argument("--hook", default=None, help="1편 오프닝 훅(선택)")
    c.add_argument("--out", default=None, help="매니페스트 저장 경로")
    c.add_argument("--max-duration", type=int, default=tk_manifest.DEFAULT_MAX_DURATION_SEC)
    c.add_argument("--privacy-level", default=None)
    c.add_argument("--draft", action="store_true")
    c.add_argument("--media-url", default=None,
                   help="발행할 공개 영상 URL — 로컬 파일 없이 매니페스트 생성(Actions 경로)")
    c.set_defaults(func=cmd_combined)

    g = sub.add_parser("gates", help="매니페스트 게이트 재검증")
    g.add_argument("--manifest", required=True)
    g.set_defaults(func=cmd_gates)

    pb = sub.add_parser("publish", help="게이트 통과 편 발행")
    pb.add_argument("--manifest", required=True)
    pb.add_argument("--segment", type=int, required=True, choices=[1, 2, 3])
    pb.add_argument("--account-id", default=None, help="미지정 시 HERO_TIKTOK_ACCOUNT_ID")
    pb.add_argument("--privacy-level", default=None, help="TikTok 공개 레벨(미지정 시 매니페스트/환경변수)")
    pb.add_argument("--skip-preflight", action="store_true",
                    help="토큰 preflight 생략(비상용 — 기본은 검사)")
    pb.add_argument("--media-url", default=None, help="발행 직전 media_url 주입(합본 경로)")
    pb.add_argument("--schedule-at", default=None, help="예약 발행 ISO8601")
    pb.add_argument("--fallback-media-url", dest="fallback_media_url",
                    help="1차 발행 실패가 미디어 URL 문제일 때 쓸 대체 URL(1회 재시도)")
    pb.add_argument("--no-verify", dest="no_verify", action="store_true",
                    help="발행 후 플랫폼 대조 검증 생략")
    pb.add_argument("--verify-limit", type=int, default=20,
                    help="검증 list_posts 조회 창(기본 20)")
    pb.add_argument("--ep", type=int, default=None, help="회차(검증 경보 표기용)")
    pb.set_defaults(func=cmd_publish)
    rc = sub.add_parser("record", help="발행 결과를 원장에 기록(멱등)")
    rc.add_argument("--ep", type=int, required=True)
    rc.add_argument("--status", default="published")
    rc.add_argument("--post-id", dest="post_id")
    rc.add_argument("--platform-post-id", dest="platform_post_id")
    rc.add_argument("--post-url", dest="post_url")
    rc.add_argument("--published-at", dest="published_at")
    rc.add_argument("--platform-post-url", dest="platform_post_url")
    rc.add_argument("--media-url", dest="media_url",
                    help="Zernio 에 실제 인게스트된 media_url (발행 감사 기록)")
    rc.add_argument("--mode", choices=["combined", "split"], default=None,
                    help="발행 모드(미지정 시 combined) — 원장 형식 정합(D-2)")
    rc.add_argument("--segment", type=int, choices=[1, 2, 3], default=None)
    rc.add_argument("--privacy-level", default=None)
    rc.add_argument("--account-id", default=None,
                    help="회수 대상 Zernio 계정(미지정 시 HERO_TIKTOK_ACCOUNT_ID)")
    rc.add_argument("--recover-limit", type=int, default=20,
                    help="list_posts 조회 개수(회수 범위)")
    rc.add_argument("--recover-latest", action="store_true",
                    help="post_url 매칭 실패 시 최근 발행건 사용(감사 전용·기본 fail-closed)")
    rc.add_argument("--no-recover", dest="no_recover", action="store_true",
                    help="Zernio 회수 생략(오프라인 기록)")
    rc.add_argument("--force", action="store_true")
    rc.add_argument("--state")
    rc.set_defaults(func=cmd_record)

    rd = sub.add_parser("ready", help="러너3 — 발행 대상 회차 판별(승인 완료·틱톡 미발행)")
    rd.add_argument("--state", default=None,
                    help="발행 원장 경로(미지정 시 HERO_STATE_FILE 또는 gen/data/publish_state.json)")
    rd.set_defaults(func=cmd_ready)

    th = sub.add_parser("token-history",
                        help="틱톡 토큰 만료시각 이력 리포트·판정·소급(B안, 0크레딧)")
    th.add_argument("--action", choices=["report", "backfill", "prune"], default="report")
    th.add_argument("--until", default="auto",
                    help="report: 목표 실행시각 ISO8601. auto=다음 실행 시각 자동 해석")
    th.add_argument("--no-query", dest="no_query", action="store_true",
                    help="다음 실행 해석 시 스케줄러 조회 생략(크론 계산만)")
    th.add_argument("--files", nargs="*", default=[], help="backfill: 로그 파일 경로")
    th.add_argument("--keep", type=int, default=500, help="prune: 보존 관측 수")
    th.add_argument("--path", default=None)
    th.add_argument("--json", action="store_true")
    th.set_defaults(func=cmd_token_history)

    vf = sub.add_parser("verify", help="발행 후 플랫폼 대조 검증(읽기 전용, 0크레딧)")
    vf.add_argument("--ep", type=int, default=None, help="원장 회차(지정 시 원장 대조 포함)")
    vf.add_argument("--post-id", default=None, help="Zernio 게시물 id(미지정 시 원장 값)")
    vf.add_argument("--post-url", default=None, help="플랫폼 URL(미지정 시 원장 값)")
    vf.add_argument("--account-id", default=None)
    vf.add_argument("--verify-limit", type=int, default=20, help="list_posts 조회 창")
    vf.add_argument("--recover-latest", action="store_true",
                    help="식별자 없이 최근 발행건으로 대조(감사 전용)")
    vf.add_argument("--notify", action="store_true", help="불일치 시 텔레그램 경보")
    vf.add_argument("--state", default=None)
    vf.add_argument("--json", action="store_true")
    vf.set_defaults(func=cmd_verify)
    bf = sub.add_parser("ledger-backfill",
                        help="과거 회차 메타 소급 복구 — list_posts 실조회로 원장 교정(기본 dry-run)")
    bf.add_argument("--ledger", default="gen/data/publish_state.json", help="원장 경로")
    bf.add_argument("--ep", action="append", default=None, help="대상 회차(반복). 미지정=전체")
    bf.add_argument("--account-id", default=None, help="미지정 시 HERO_TIKTOK_ACCOUNT_ID")
    bf.add_argument("--backfill-limit", type=int, default=30, help="list_posts 조회 창")
    bf.add_argument("--apply", action="store_true", help="원장에 실제 반영(미지정 시 dry-run)")
    bf.add_argument("--out", default=None, help="복구본 저장 경로")
    bf.add_argument("--mirror", action="store_true",
                    help="반영 후 AI Drive 미러 갱신(--apply 또는 --out 필요)")
    bf.add_argument("--json", dest="as_json", action="store_true", help="계획 JSON 출력")
    bf.set_defaults(func=cmd_backfill)
    cu = sub.add_parser("catchup",
                        help="러너3 실행 누락 회수 계획 — 읽기 전용(발행 없음, 0크레딧)")
    cu.add_argument("--state", default=None)
    cu.add_argument("--cron", default=None, help="미지정 시 30 10 * * 2,4(KST)")
    cu.add_argument("--tz", default=None, help="미지정 시 Asia/Seoul(스킬 크론 기준)")
    cu.add_argument("--now", default=None, help="기준 시각 ISO8601(검증용)")
    cu.add_argument("--max-publish", type=int, default=1, help="실행 1회 발행 한도")
    cu.add_argument("--retention-hours", type=float, default=24.0,
                    help="Telegram 콜백 보존 한도(기본 24h)")
    cu.add_argument("--json", dest="as_json", action="store_true")
    cu.set_defaults(func=cmd_catchup)
    ra = sub.add_parser("reapprove",
                        help="승인 요청 자동 재발행 — 24h 콜백 소실 대응(기본 dry-run)")
    ra.add_argument("--state", default=None)
    ra.add_argument("--ep", action="append", default=None, type=int)
    ra.add_argument("--now", default=None, help="기준 시각 ISO8601(검증용)")
    ra.add_argument("--retention-hours", type=float, default=24.0)
    ra.add_argument("--max-requests", type=int, default=3)
    ra.add_argument("--cooldown-hours", type=float, default=24.0)
    ra.add_argument("--send", action="store_true", help="실제 전송(미지정 시 dry-run)")
    ra.add_argument("--json", dest="as_json", action="store_true")
    ra.set_defaults(func=cmd_reapprove)




    nr = sub.add_parser("next-run", help="다음 실행 시각 해석·주입(B안 후속, 0크레딧)")
    nr.add_argument("--json", action="store_true")
    nr.add_argument("--no-query", dest="no_query", action="store_true",
                    help="스케줄러 조회 생략(크론 계산만)")
    nr.add_argument("--apply", action="store_true", help="현재 프로세스 env 에 주입")
    nr.set_defaults(func=cmd_next_run)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
