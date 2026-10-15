"""gen/hero_shorts/tiktok/backfill.py — 원장 소급 복구(과거 회차 메타)
======================================================================
배경(2026-10-15 실측): 원장의 ep86·ep87 은 틱톡 정본 필드가
`tiktok.segments[]` 안에만 있고 ep88 처럼 평평한(flat) 필드가 없다.
그런데 그 안의 Zernio `post_id` 가 **실조회 정본과 불일치**한다.

실측(Zernio list_posts 11건):
    ep86 seg  ledger …678b52233e  live …678b52233d
    ep87      ledger …390d4a1e8b  live …390d4a1e8a
    ep88      ledger …ccb84a4ba5  live …ccb84a4ba5   ← 러너 기록, 정확
ep86·ep87 의 post_id 는 마지막 16진 자리가 1 큰 값으로 박혀 있다.
`platform_post_id`(틱톡 숫자 ID)는 전부 정확하다 — 그래서 매칭 키는
platform_post_id 로 두고, 정본 `_id` 를 되돌려 채운다.

원칙
  · 조회는 list_posts 읽기 전용 — 발행 없음, 크레딧 0
  · 매칭은 정확한 키로만. 못 맞추면 None(추측 금지, fail-closed)
  · 기본 dry-run. `--apply` 없이는 원장을 건드리지 않는다
  · 구조가 모호한 것(구간 수 불명 등)은 고치지 않고 문제로 올린다
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import logging
import pathlib
import sys

from . import recover

log = logging.getLogger("tiktok.backfill")

VERSION = "1.0.0"
DEFAULT_LEDGER = pathlib.Path("gen/data/publish_state.json")
CANONICAL_SOURCE = "zernio.list_posts"
FLAT_FIELDS = ("post_id", "platform_post_id", "platform_post_url", "post_url", "published_at")
BACKFILL_ACTOR = "Genspark Super Agent — 원장 소급 복구(4번 작업)"

EXIT_OK = 0
EXIT_DRIFT = 3      # dry-run 에서 불일치/결손 발견
EXIT_QUERY = 4      # 플랫폼 조회 불가
EXIT_MIRROR = 5     # 원장 반영 후 AI Drive 미러 실패

DRIVE_PATH = "/hero-shorts/publish_state.json"


def _now_iso():
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).isoformat(timespec="seconds")


def _iter_segments(tk):
    """tiktok 노드의 세그먼트를 (출처, 세그먼트) 로 순회."""
    for seg in tk.get("segments") or []:
        yield "segments", seg
    hist = tk.get("split_history")
    if isinstance(hist, dict):
        for seg in hist.get("segments") or []:
            yield "split_history", seg
    elif isinstance(hist, list):
        for seg in hist:
            yield "split_history", seg


def index_live(posts):
    """platform_post_id → post, _id → post 인덱스."""
    by_pid, by_id = {}, {}
    for post in posts:
        pid = recover.tiktok_post_id_from_url(post.get("platformPostUrl"))
        if pid:
            by_pid[pid] = post
        if post.get("_id"):
            by_id[post["_id"]] = post
    return by_pid, by_id


def audit_segment(seg, source, by_pid, by_id):
    """세그먼트 1건 ▸ 정본 대조. 매칭 실패는 problems 로만 보고."""
    pid = str(seg.get("platform_post_id") or "")
    ledger_post_id = seg.get("post_id")
    row = {
        "source": source,
        "segment_no": seg.get("segment_no"),
        "ledger_post_id": ledger_post_id,
        "platform_post_id": pid,
        "matched_by": None,
        "live_post_id": None,
        "live_published_at": None,
        "post_id_ok": None,
        "published_at_ok": None,
        "url_ok": None,
        "status": None,
        "problems": [],
        "proposed": {},
    }
    if not pid:
        row["problems"].append("platform_post_id 없음 — 매칭 키 부재")
        return row
    live = by_pid.get(pid)
    if live is not None:
        row["matched_by"] = "platform_post_id"
    else:
        cand = by_id.get(str(ledger_post_id or ""))
        if cand and recover.tiktok_post_id_from_url(cand.get("platformPostUrl")) == pid:
            live, row["matched_by"] = cand, "post_id"
    if live is None:
        row["problems"].append(f"플랫폼 미매칭(platform_post_id={pid}) — 원장값 유지")
        return row

    url = live.get("platformPostUrl")
    pub = live.get("publishedAt") or live.get("scheduledFor")
    row["live_post_id"] = live.get("_id")
    row["live_published_at"] = live.get("publishedAt")
    row["status"] = live.get("status")
    row["post_id_ok"] = str(ledger_post_id) == str(live.get("_id"))
    row["published_at_ok"] = seg.get("published_at") == live.get("publishedAt")
    row["url_ok"] = seg.get("platform_post_url") == url

    proposed = {"post_id": live.get("_id"), "platform_post_url": url,
                "published_at": live.get("publishedAt")}
    row["proposed"] = {k: v for k, v in proposed.items() if seg.get(k) != v}
    if not row["post_id_ok"]:
        row["problems"].append(
            f"post_id 불일치 — 원장 {ledger_post_id} vs 정본 {live.get('_id')}")
    if not row["published_at_ok"]:
        row["problems"].append(
            f"published_at 불일치 — 원장 {seg.get('published_at')} vs 정본 {live.get('publishedAt')}")
    if not row["url_ok"]:
        row["problems"].append("platform_post_url 불일치")
    return row


def audit_record(ep, rec, by_pid, by_id):
    """회차 1건 ▸ 세그먼트 대조 + flat 필드 결손 판정."""
    tk = rec.get("tiktok") or {}
    live_segs = tk.get("segments") or []
    out = {
        "ep": ep,
        "mode": tk.get("mode"),
        "status": tk.get("status"),
        "segment_count": tk.get("segment_count"),
        "segments": [],
        "corrections": 0,
        "flat_add": {},
        "flat_missing": [],
        "problems": [],
        "ambiguous": [],
    }
    segs = list(_iter_segments(tk))
    if not segs:
        out["problems"].append("틱톡 발행 이력 없음 — 소급 대상 아님")
        return out
    for source, seg in segs:
        row = audit_segment(seg, source, by_pid, by_id)
        out["segments"].append(row)
        out["corrections"] += len(row["proposed"])
        out["problems"] += [f"[{source}] {p}" for p in row["problems"]]

    nos = [s.get("segment_no") for s in live_segs]
    if len(nos) != len(set(nos)):
        out["ambiguous"].append(
            f"segment_no 중복 {nos} — 논리 구간 수 확정 불가(사람 판단 필요)")

    # flat 필드: 세그먼트가 정확히 1개일 때만 정본을 그대로 올린다.
    if len(live_segs) == 1:
        canon = live_segs[0]
        pid = str(canon.get("platform_post_id") or "")
        live = by_pid.get(pid)
        if live is not None:
            url = live.get("platformPostUrl")
            want = {
                "post_id": live.get("_id"),
                "platform_post_id": pid,
                "platform_post_url": url,
                "post_url": url,
                "published_at": live.get("publishedAt"),
            }
            out["flat_missing"] = [f for f in FLAT_FIELDS if not tk.get(f)]
            out["flat_add"] = {k: v for k, v in want.items() if tk.get(k) != v}
        else:
            out["problems"].append("단일 세그먼트 미매칭 — flat 채움 보류")
    elif live_segs:
        out["ambiguous"].append(
            f"다중 세그먼트 {len(live_segs)}건 — flat 필드는 단일 정본이 없어 채우지 않음")
    return out


def build_plan(ledger, posts, eps=None, actor=BACKFILL_ACTOR):
    """원장 전체 ▸ 복구 계획(원장 미변경)."""
    by_pid, by_id = index_live(posts)
    targets = eps or [k for k in ledger if not k.startswith("_")]
    records = [audit_record(ep, ledger.get(ep) or {}, by_pid, by_id) for ep in targets]
    return {
        "version": VERSION,
        "generated_at": _now_iso(),
        "actor": actor,
        "source": CANONICAL_SOURCE,
        "live_posts": len(posts),
        "records": records,
        "total_corrections": sum(r["corrections"] for r in records),
        "total_flat_add": sum(len(r["flat_add"]) for r in records),
        "total_problems": sum(len(r["problems"]) for r in records),
        "total_ambiguous": sum(len(r["ambiguous"]) for r in records),
    }


def apply_plan(ledger, plan, actor=BACKFILL_ACTOR):
    """계획을 원장에 적용(깊은 복사). 반환: (새 원장, 적용 요약)."""
    new = copy.deepcopy(ledger)
    applied = {"corrected_segments": 0, "flat_filled": 0, "records": [], "skipped": []}
    audit_by_ep = {r["ep"]: r for r in plan["records"]}
    for ep, audit in audit_by_ep.items():
        rec = new.get(ep)
        if rec is None:
            applied["skipped"].append(f"{ep}: 원장에 없음")
            continue
        tk = rec.setdefault("tiktok", {})
        row_counts = {"segments": 0, "flat": 0}
        for row in audit["segments"]:
            container = None
            for _source, seg in _iter_segments(tk):
                if seg.get("platform_post_id") == row["platform_post_id"] \
                        and str(seg.get("post_id")) == str(row["ledger_post_id"]):
                    container = seg
                    break
            if container is None or not row["proposed"]:
                continue
            for key, val in row["proposed"].items():
                if container.get(key) != val:
                    container[f"{key}_was"] = container.get(key)
                    container[key] = val
                    row_counts["segments"] += 1
        for key, val in audit["flat_add"].items():
            if tk.get(key) != val:
                tk[key] = val
                row_counts["flat"] += 1
        if row_counts["segments"] or row_counts["flat"]:
            tk["backfill"] = {
                "at": _now_iso(),
                "by": actor,
                "version": VERSION,
                "source": CANONICAL_SOURCE,
                "corrected_segments": row_counts["segments"],
                "flat_filled": row_counts["flat"],
                "note": "정본(_id·platformPostUrl·publishedAt)으로 소급 복구. 원값은 *_was 에 보존.",
            }
            tk["published_at_source"] = "platform"
        applied["corrected_segments"] += row_counts["segments"]
        applied["flat_filled"] += row_counts["flat"]
        applied["records"].append({"ep": ep, **row_counts})
    return new, applied


def _gsk_run(args, timeout=180):
    """gsk 호출 — 테스트에서 monkeypatch 하도록 분리."""
    return subprocess.run(["gsk", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def mirror_to_drive(local_path, drive_path=DRIVE_PATH, timeout=180):
    """로컬 원장 ▸ AI Drive 미러(`--overwrite true` = 같은 링크 유지).

    실패는 조용히 넘기지 않는다 — 반영된 원장이 미러되지 않으면
    샌드박스 폐기 시 복원 정본이 낡은 채로 남는다.
    """
    proc = _gsk_run(["aidrive", "upload", "--local_file", str(local_path),
                     "--upload_path", drive_path, "--overwrite", "true"], timeout=timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "drive_path": drive_path,
                "error": f"gsk aidrive upload rc={proc.returncode} — {(proc.stderr or out)[:200]}"}
    link = None
    idx = out.find("{")
    if idx >= 0:
        try:
            payload = json.loads(out[idx:])
            for key in ("download_url", "url", "link", "file_url"):
                if isinstance(payload.get("data"), dict) and payload["data"].get(key):
                    link = payload["data"][key]
                    break
                if payload.get(key):
                    link = payload[key]
                    break
        except json.JSONDecodeError:
            pass
    return {"ok": True, "drive_path": drive_path, "link": link,
            "bytes": pathlib.Path(local_path).stat().st_size}


def format_report(plan, applied=None):
    """사람이 읽는 보고문."""
    lines = [f"[hero-shorts] 원장 소급 복구 v{VERSION} — {plan['source']}",
             f"대상 {len(plan['records'])}회차 · 실조회 {plan['live_posts']}건 · "
             f"세그먼트 교정 {plan['total_corrections']} · flat 채움 {plan['total_flat_add']}",
             f"문제 {plan['total_problems']} · 모호 {plan['total_ambiguous']}"]
    for r in plan["records"]:
        head = f"· {r['ep']} [{r['mode'] or '?'} · 세그먼트 {len(r['segments'])} · {r['status'] or '?'}]"
        lines.append(head)
        for row in r["segments"]:
            flag = "OK " if not row["problems"] else "!! "
            detail = f"    {flag}{row['source']} seg={row['segment_no']} pid={row['platform_post_id']}"
            if row["problems"]:
                detail += " — " + "; ".join(row["problems"])
            lines.append(detail)
            if row["proposed"]:
                lines.append(f"       → 교정 {json.dumps(row['proposed'], ensure_ascii=False)}")
        if r["flat_add"]:
            lines.append(f"    ++ flat 채움 {json.dumps(r['flat_add'], ensure_ascii=False)}")
        for amb in r["ambiguous"]:
            lines.append(f"    ?? {amb}")
    if applied is not None:
        lines.append(f"적용: 세그먼트 {applied['corrected_segments']} · flat {applied['flat_filled']}")
        for rec in applied["records"]:
            lines.append(f"    {rec['ep']}: 세그먼트 {rec['segments']} · flat {rec['flat']}")
        for sk in applied["skipped"]:
            lines.append(f"    건너뜀 {sk}")
    return "\n".join(lines)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(
        prog="tiktok-backfill", description=f"원장 소급 복구 v{VERSION} — 기본 dry-run")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--ep", action="append", default=None, help="대상 회차(반복). 미지정=전체")
    ap.add_argument("--account-id", default=None)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--apply", action="store_true", help="원장에 실제 반영(미지정 시 dry-run)")
    ap.add_argument("--out", default=None, help="복구본 저장 경로(--apply 없이도 기록)")
    ap.add_argument("--json", dest="as_json", action="store_true")
    ap.add_argument("--mirror", action="store_true",
                    help="반영/저장 후 AI Drive 미러 갱신(--apply 또는 --out 필요)")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.ledger)
    if not path.exists():
        print(json.dumps({"ok": False, "error": f"원장 없음: {path}"}, ensure_ascii=False))
        return EXIT_QUERY
    ledger = json.loads(path.read_text(encoding="utf-8"))
    try:
        posts = recover.list_tiktok_posts(account_id=args.account_id, limit=args.limit)
    except Exception as exc:  # 조회 불가 — fail-closed
        print(json.dumps({"ok": False, "error": f"플랫폼 조회 실패: {exc}"}, ensure_ascii=False))
        return EXIT_QUERY
    if not posts:
        print(json.dumps({"ok": False, "error": "실조회 0건 — 원장을 건드리지 않는다"},
                         ensure_ascii=False))
        return EXIT_QUERY

    plan = build_plan(ledger, posts, eps=args.ep)
    if args.as_json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(format_report(plan))

    # ⚠️ 소급 대상 축소는 '계획'에만 적용한다 — 기록은 반드시 원장 '전체'로.
    #    부분집합을 쓰면 대상 밖 회차가 통째로 사라진다(2026-10-15 실측:
    #    --ep ep86 --ep ep87 경로에서 ep88 — 유일한 실제 발행 이력 — 이 소실).
    new_ledger, applied = apply_plan(ledger, plan)
    if args.out:
        out_path = pathlib.Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(new_ledger, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"복구본 저장: {out_path}")
    if applied["corrected_segments"] or applied["flat_filled"]:
        if not args.as_json:
            print(format_report(plan, applied=applied))
    if args.apply:
        if applied["corrected_segments"] or applied["flat_filled"]:
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            path.write_text(json.dumps(new_ledger, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            print(f"적용 완료 · 백업 {backup}")
        else:
            # 교정 0건이어도 미러까지는 진행한다 — 조기 return 은 미러를 건너뛴다.
            print("적용할 교정 없음 — 원장 무변경")
    if args.mirror:
        if args.apply:
            target = path
        elif args.out:
            target = pathlib.Path(args.out)
        else:
            print(json.dumps({"ok": False,
                              "error": "--mirror 는 --apply 또는 --out 과 함께 써야 한다"},
                             ensure_ascii=False))
            return EXIT_MIRROR
        res = mirror_to_drive(target)
        print(json.dumps({"mirror": res}, ensure_ascii=False))
        if not res.get("ok"):
            return EXIT_MIRROR

    if args.apply:
        return EXIT_OK  # 반영 완료 — exit 3(불일치 발견)은 dry-run 전용 의미
    drift = plan["total_corrections"] + plan["total_flat_add"]
    return EXIT_DRIFT if drift else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
