"""
gen/hero_shorts/publish_targets/runner.py — 2채널 발행 CLI (W8)
==============================================================
사용:
    python3 -m gen.hero_shorts.publish_targets.runner plan     [--eps ep89] [--json]
    python3 -m gen.hero_shorts.publish_targets.runner dry-run  [--eps ep89]
    python3 -m gen.hero_shorts.publish_targets.runner publish  --ep ep89 --apply
    python3 -m gen.hero_shorts.publish_targets.runner verify   --ep ep89
    python3 -m gen.hero_shorts.publish_targets.runner stage --ep ep88 --file <mp4> [--write]
    python3 -m gen.hero_shorts.publish_targets.runner migrate  [--apply] [--mirror]

원칙(fail-closed):
    - 기본값은 **미전송**. `publish` 는 `--apply` 없이는 게이트·페이로드까지만 수행한다.
    - 게이트 미통과 타깃은 어떤 경우에도 전송하지 않는다.
    - 시크릿(계정 ID·API 키)은 출력하지 않는다 — 변수명만 노출한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import ledger_v2, planner
from .transport import TransportError, is_genspark_wrapper, make_transport

log = logging.getLogger("hero_shorts.publish_targets.runner")

EXIT_OK = 0
EXIT_GATE_BLOCKED = 10      # 게이트 미통과 — 사람이 원인 제거
EXIT_TRANSPORT = 20         # 전송 실패
EXIT_MIRROR = 5             # 미러 실패(backfill 관례)
EXIT_TIKTOK_CHANGED = 6     # 틱톡 블록 변경(무손실 보존 위반)
EXIT_NOT_APPLIED = 30       # --apply 없이 실제 전송 요구


def _load(path):
    return ledger_v2.load_ledger(path)


def _select_eps(doc: Dict[str, Any], eps: Optional[Sequence[str]]) -> List[str]:
    if eps:
        out = []
        for e in eps:
            key = e if str(e).startswith("ep") else f"ep{e}"
            out.append(key)
        return out
    return ledger_v2.episode_keys(doc)


def _target_instances(platforms, args) -> Dict[str, Any]:
    kw: Dict[str, Any] = {}
    if "threads" in platforms:
        kw["threads"] = {"first_comment": getattr(args, "first_comment", None)}
    if "youtube" in platforms:
        kw["youtube"] = {"visibility": getattr(args, "visibility", "public"),
                         "regular_publish": not getattr(args, "nonregular", False)}
    return planner.make_targets(platforms, **kw)


def _plan(doc, args, platforms):
    targets = _target_instances(platforms, args)
    eps = _select_eps(doc, getattr(args, "eps", None))
    return planner.plan_ledger(doc, targets=targets, platforms=platforms, eps=eps,
                               topic_tag=args.topic_tag, max_attempts=args.max_attempts)


# --------------------------------------------------------------------------- #
# 명령
# --------------------------------------------------------------------------- #
def cmd_plan(args) -> int:
    doc = _load(args.ledger)
    plans = _plan(doc, args, args.platforms)
    out = [{"ep": p.get("ep"), "pending": p.get("pending"),
            "blocked": p.get("blocked"), "skipped": p.get("skipped"),
            "error": p.get("error")} for p in plans]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for p in out:
            note = f"  ! {p['error']}" if p.get("error") else ""
            print(f"{p['ep']}: 발행대상={p['pending'] or '없음'} "
                  f"차단={p['blocked'] or '없음'} 제외={p['skipped'] or '없음'}{note}")
    return EXIT_OK


def cmd_dry_run(args) -> int:
    doc = _load(args.ledger)
    plans = _plan(doc, args, args.platforms)
    report = []
    for p in plans:
        entry = {"ep": p.get("ep"), "pending": p.get("pending"),
                 "blocked": p.get("blocked"), "error": p.get("error"),
                 "projections": {k: (v if k != "threads" else {kk: vv for kk, vv in v.items()})
                                 for k, v in (p.get("projections") or {}).items()},
                 "payloads": {plat: pl.payload for plat, pl in (p.get("plans") or {}).items()},
                 "gates": {plat: {"ok": pl.gate.get("ok"),
                                  "failed": pl.gate.get("failed"),
                                  "problems": pl.gate.get("problems")}
                           for plat, pl in (p.get("plans") or {}).items()}}
        report.append(entry)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:20000])
    print("\n[미전송] dry-run — 전송 0회. 실제 발행은 publish --apply.", file=sys.stderr)
    return EXIT_OK


def cmd_publish(args) -> int:
    doc = _load(args.ledger)
    ep = args.ep if str(args.ep).startswith("ep") else f"ep{args.ep}"
    record = doc.get(ep)
    if not isinstance(record, dict):
        print(f"원장에 {ep} 없음", file=sys.stderr)
        return EXIT_GATE_BLOCKED

    plans_for = _plan(doc, argparse.Namespace(**{**vars(args), "eps": [ep]}), args.platforms)
    if not plans_for:
        return EXIT_GATE_BLOCKED
    p = plans_for[0]
    if p.get("error"):
        print(f"{ep} 계획 불가: {p['error']}", file=sys.stderr)
        return EXIT_GATE_BLOCKED

    media_url = (p.get("facts") or {}).get("media_url")
    targets = p["plans"]

    if not p["pending"]:
        print(f"{ep}: 발행 대상 없음(차단={p['blocked']} 제외={p['skipped']})")
        return EXIT_GATE_BLOCKED if p["blocked"] else EXIT_OK
    if not media_url:
        print(f"{ep}: media_url 없음 — 업로드 단계 선행 필요", file=sys.stderr)
        return EXIT_GATE_BLOCKED

    if not args.apply:
        print(json.dumps({"ep": ep, "pending": p["pending"],
                          "payloads": {k: v.payload for k, v in targets.items()}},
                         ensure_ascii=False, indent=2)[:8000])
        print("\n[미전송] --apply 없이는 전송하지 않습니다.", file=sys.stderr)
        return EXIT_NOT_APPLIED

    transport = make_transport(args.transport)
    batch = planner.publish_batch(targets, transport=transport, media_url=media_url,
                                  platforms=args.platforms,
                                  publish_now=not args.schedule_at,
                                  scheduled_at=args.schedule_at)
    written = planner.apply_results(record, batch["results"])
    if args.write:
        ledger_v2.backup_ledger(args.ledger)
        ledger_v2.save_ledger(doc, args.ledger)
    print(json.dumps({"ep": ep, "summary": planner.summary(targets, batch["results"]),
                      "written": {k: v.get("status") for k, v in written.items()}},
                     ensure_ascii=False, indent=2))
    failed = [x for x in batch["results"].values() if x.outcome == "failed"]
    return EXIT_TRANSPORT if failed else EXIT_OK


def cmd_stage(args) -> int:
    """스테이징 업로드 — 로컬 실물 → Zernio 저장소 publicUrl (블로커#3 해소 경로, D).

    승인 A 실측(2026-10-18): 원장 래퍼 URL 은 외부 서버에서 403 — 발행
    mediaItems 로 쓸 수 없다. 스테이징 `public_url` 이 새 정본이다.

    fail-closed:
        - `--write` 없이는 **원장을 바꾸지 않는다**(업로드만 — 재시도 안전).
        - 틱톡 블록 해시 가드 — 저장 전후 해시가 다르면 쓰기를 중단한다.
        - `upload_url`(1시간짜리 쓰기 자격증명)은 어디에도 기록하지 않는다.
    """
    doc = _load(args.ledger)
    ep = args.ep if str(args.ep).startswith("ep") else f"ep{args.ep}"
    record = doc.get(ep)
    if not isinstance(record, dict):
        print(f"원장에 {ep} 없음", file=sys.stderr)
        return EXIT_GATE_BLOCKED

    transport = make_transport(args.transport)
    try:
        result = transport.stage_media_file(args.file, filename=args.filename)
    except TransportError as exc:                 # 파일·스펙·네트워크 — 원장 무변경
        print(f"{ep}: 스테이징 실패 — {exc}", file=sys.stderr)
        return EXIT_TRANSPORT

    public_url = result["public_url"]
    if not args.write:
        print(json.dumps({"ep": ep, "staged": result, "ledger_written": False},
                         ensure_ascii=False, indent=2))
        print("\n[미기록] --write 없이는 원장을 바꾸지 않습니다.", file=sys.stderr)
        return EXIT_OK

    comb = record.get("combined") if isinstance(record.get("combined"), dict) else {}
    if "combined" not in record:
        record["combined"] = comb
    prev = comb.get("media_url")
    if prev and prev != public_url and not comb.get("media_staged"):
        comb["media_url_prev"] = prev             # 최초 교체 시 구값 출처 보존(가산)
    comb["media_url"] = public_url
    comb["media_staged"] = {k: result[k] for k in
                            ("filename", "content_type", "size_bytes", "sha256",
                             "key", "etag")}
    comb["media_staged"]["at"] = ledger_v2.now_utc()
    if isinstance(record.get("media_url"), str) and record["media_url"] != public_url:
        if not record.get("media_url_staged"):
            record["media_url_prev"] = record["media_url"]
        record["media_url"] = public_url

    before = ledger_v2.tiktok_hashes(doc)
    ledger_v2.assert_tiktok_unchanged(before, ledger_v2.tiktok_hashes(doc))
    ledger_v2.backup_ledger(args.ledger)
    ledger_v2.save_ledger(doc, args.ledger)
    print(json.dumps({"ep": ep, "staged": result, "ledger_written": True,
                      "replaced_wrapper": is_genspark_wrapper(prev)},
                     ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_verify(args) -> int:
    doc = _load(args.ledger)
    ep = args.ep if str(args.ep).startswith("ep") else f"ep{args.ep}"
    record = doc.get(ep) or {}
    block = ledger_v2.read_target(record, args.platform)
    if not block:
        print(f"{ep}:{args.platform} 원장 기록 없음")
        return EXIT_GATE_BLOCKED
    from . import get_target

    target = get_target(args.platform)
    url = block.get("platform_post_url")
    recovered = target.extract_id(url) if url else None
    print(json.dumps({"ep": ep, "platform": args.platform,
                      "ledger_status": block.get("status"),
                      "url": url, "recovered_id": recovered,
                      "ledger_id": block.get("platform_post_id"),
                      "match": bool(recovered and recovered == block.get("platform_post_id"))},
                     ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_migrate(args) -> int:
    try:
        result = ledger_v2.migrate(args.ledger, apply=args.apply, mirror=args.mirror)
    except RuntimeError as exc:
        print(f"중단: {exc}", file=sys.stderr)
        return EXIT_TIKTOK_CHANGED
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# 파서
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="publish_targets.runner",
        description="Hero Shorts 2채널(유튜브 쇼츠·쓰레드) 발행")
    ap.add_argument("--ledger", default=str(ledger_v2.DEFAULT_LEDGER))
    ap.add_argument("--platforms", nargs="+", default=list(ledger_v2.TARGET_PLATFORMS))
    ap.add_argument("--topic-tag", default="JCUniverse")
    ap.add_argument("--max-attempts", type=int, default=planner.DEFAULT_MAX_ATTEMPTS)
    ap.add_argument("--visibility", default="public")
    ap.add_argument("--nonregular", action="store_true",
                    help="비정기(시험) 발행 — Y-G4 의 정기 public 강제만 해제"
                         "(visibility 유효집합 {public,private,unlisted} 검사는 유지)")
    ap.add_argument("--first-comment", default=None)
    ap.add_argument("--transport", default="rest", choices=["rest", "cli"])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="발행 계획(미전송)")
    p.add_argument("--eps", nargs="*", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    d = sub.add_parser("dry-run", help="게이트·페이로드까지(미전송)")
    d.add_argument("--eps", nargs="*", default=None)
    d.set_defaults(func=cmd_dry_run)

    pb = sub.add_parser("publish", help="실제 전송(--apply 필수)")
    pb.add_argument("--ep", required=True)
    pb.add_argument("--apply", action="store_true")
    pb.add_argument("--write", action="store_true", help="원장에 기록")
    pb.add_argument("--schedule-at", dest="schedule_at", default=None)
    pb.set_defaults(func=cmd_publish)

    v = sub.add_parser("verify", help="발행 검증")
    v.add_argument("--ep", required=True)
    v.add_argument("--platform", default="youtube",
                   choices=list(ledger_v2.TARGET_PLATFORMS))
    v.set_defaults(func=cmd_verify)

    m = sub.add_parser("migrate", help="원장 v2 마이그레이션")
    m.add_argument("--apply", action="store_true")
    m.add_argument("--mirror", action="store_true")
    m.set_defaults(func=cmd_migrate)

    st = sub.add_parser("stage",
                        help="로컬 미디어 실물 → Zernio 저장소 업로드(publicUrl, 발행 아님)")
    st.add_argument("--ep", required=True)
    st.add_argument("--file", required=True, help="업로드할 로컬 미디어 실물 경로")
    st.add_argument("--filename", default=None, help="스토리지 파일명(기본: 원본 파일명)")
    st.add_argument("--write", action="store_true",
                    help="원장 media_url 을 staged publicUrl 로 교체(미지정 시 원장 무변경)")
    st.set_defaults(func=cmd_stage)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
