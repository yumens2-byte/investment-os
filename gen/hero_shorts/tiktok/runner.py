"""
gen/hero_shorts/tiktok/runner.py — 틱톡 라인 CLI
================================================
    python3 -m gen.hero_shorts.tiktok.runner manifest --plan <7컷JSON> --title <제목> [--hook <훅>] --out <매니페스트JSON>
    python3 -m gen.hero_shorts.tiktok.runner gates    --manifest <매니페스트JSON>
    python3 -m gen.hero_shorts.tiktok.runner publish  --manifest <매니페스트JSON> --segment <1|2|3> [--schedule-at <ISO8601>]

발행은 게이트 통과 편만 수행한다(fail-closed). 실제 발행은 크레딧/과금이 아닌
Zernio API 호출이지만, 마스터 운영 규칙상 실행 전 확인 대상이다.
"""
import argparse
import json
import logging
import sys
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

    dur = args.duration if args.duration is not None else probe_duration(args.video)
    m = tk_manifest.from_combined(
        args.video, dur, args.title, hook=args.hook,
        max_duration_sec=args.max_duration,
        privacy_level=args.privacy_level, draft=args.draft,
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
    health = tk_account.check_token_health(account_id=args.account_id,
                                           warn_hours=args.warn_hours)
    print(json.dumps(health.to_dict(), ensure_ascii=False))
    if health.blocking:
        print(tk_account.format_alert(health), file=sys.stderr)
        return 70
    if health.status != tk_account.OK:
        print(tk_account.format_alert(health), file=sys.stderr)
    return 0


def cmd_publish(args):
    m = tk_manifest.load_manifest(args.manifest)
    seg = next((s for s in m["segments"] if s["segment_no"] == args.segment), None)
    if seg is None:
        print(f"segment {args.segment} 없음", file=sys.stderr)
        return 64
    if args.media_url:
        seg["media_url"] = args.media_url
    result = tk_publish.publish_segment(seg, account_id=args.account_id,
                                        schedule_at=args.schedule_at,
                                        privacy_level=args.privacy_level,
                                        preflight=not args.skip_preflight)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("outcome") == "published" else 2


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
    pf.add_argument("--warn-hours", type=float, default=None, help="만료 임박 임계(기본 12h)")
    pf.set_defaults(func=cmd_preflight)

    c = sub.add_parser("combined", help="합본 1편 매니페스트 생성(정기 발행 기본)")
    c.add_argument("--video", required=True, help="합본 영상 파일 경로")
    c.add_argument("--duration", type=float, default=None, help="미지정 시 ffprobe 실측")
    c.add_argument("--title", required=True, help="회차 제목(캡션 헤드)")
    c.add_argument("--hook", default=None, help="1편 오프닝 훅(선택)")
    c.add_argument("--out", default=None, help="매니페스트 저장 경로")
    c.add_argument("--max-duration", type=int, default=tk_manifest.DEFAULT_MAX_DURATION_SEC)
    c.add_argument("--privacy-level", default=None)
    c.add_argument("--draft", action="store_true")
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
    pb.set_defaults(func=cmd_publish)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
