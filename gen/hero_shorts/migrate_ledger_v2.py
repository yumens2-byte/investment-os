"""
gen/hero_shorts/migrate_ledger_v2.py — 원장 스키마 v2 마이그레이션 CLI
======================================================================
설계서 §3.2. 기본은 미리보기(--apply 없으면 쓰기 없음).

사용:
    python3 -m gen.hero_shorts.migrate_ledger_v2                  # 미리보기
    python3 -m gen.hero_shorts.migrate_ledger_v2 --apply          # 적용(+ .bak 백업)
    python3 -m gen.hero_shorts.migrate_ledger_v2 --apply --mirror # 적용 + AI Drive 미러

종료코드:
    0 정상(변경 여부는 changed 필드로 판단)
    5 미러 실패
    6 틱톡 블록 변경 감지 — 중단(무손실 보존 위반)
"""
import argparse
import json
import logging
import sys

from .publish_targets import ledger_v2 as L

VERSION = "1.0.0"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=f"ledger v2 migration v{VERSION}")
    ap.add_argument("--ledger", default=str(L.DEFAULT_LEDGER))
    ap.add_argument("--apply", action="store_true", help="실제 쓰기(기본은 미리보기)")
    ap.add_argument("--mirror", action="store_true", help="적용 후 AI Drive 미러")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        result = L.migrate(args.ledger, apply=args.apply, mirror=args.mirror)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 6 if "틱톡" in str(exc) else 5

    out = {"ok": True, "applied": bool(args.apply), **result}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        r = result["report"]
        print(f"스키마 v2 · 회차 {r['episodes']}건 · 변경={'있음' if result['changed'] else '없음'}")
        print(f"  _schema 설정: {r['schema_set']} · targets 추가: {r['targets_added']}")
        print(f"  틱톡 블록 보존: {'OK' if result['tiktok_ok'] else 'FAIL'}")
        if not args.apply:
            print("  (미리보기 — 쓰기 없음. 적용하려면 --apply)")
        else:
            print(f"  기록: {result['written']} · 백업: {result['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
