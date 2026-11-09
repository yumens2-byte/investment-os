"""
kr_sleeve/run.py — KR 슬리브 진입점 (DN2026_0108)

실행: python -m kr_sleeve.run --input data/outputs/core_data.json --out data/outputs/kr_sleeve.json
  - main.yml job "KR Sleeve"(needs: morning)에서 morning artifact를 받은 뒤 실행한다.
  - 모드 KR_SLEEVE_MODE: dry_run(계산·파일만) / shadow(+DB 기록·운영자 알림) / live(+발행).
    그 외 값은 dry_run.
  - core_data.json 은 읽기만 한다.
종료코드: 0 = computed 또는 처리된 skipped / 1 = shadow·live 기록 실패 또는 예상하지 못한 예외(STD-OBS-02)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import UTC, datetime, timedelta, timezone

from kr_sleeve import config, engine, formatter, input_contract
from kr_sleeve import publisher as default_publisher
from kr_sleeve.collector import MARKET_CLOSED, MARKET_OPEN, collect
from kr_sleeve.errors import ALREADY_DONE, BENIGN, DB_WRITE_FAILED, SleeveSkip

VERSION = config.VERSION
KST = timezone(timedelta(hours=9))
logger = logging.getLogger("kr_sleeve")


def _setup_logging() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in ("hpack", "httpcore", "httpx", "h2", "hyperframe", "urllib3", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)   # STD-SEC-01


def _write_json(path: str, obj: dict) -> None:
    """artifact는 공개 레포 Actions에서 내려받을 수 있으므로 종목코드·정식명을 마스킹해 쓴다(R4 W5)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(config.mask_codes(json.dumps(obj, ensure_ascii=False, indent=2)))


def _summary(out: dict) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## KR Sleeve v{out['version']} — {out['status']}",
             f"- mode: {out['mode']} · run_date: {out['run_date']}",
             f"- reason: {out.get('reason') or '-'}"]
    total = (out.get("result") or {}).get("total_allocation")
    if total:
        lines.append("- 합산 비중: " + " · ".join(f"{k} {v}" for k, v in total.items()))
    for ch, r in ((out.get("publish") or {}).get("channels") or {}).items():
        lines.append(f"- 발행 {ch}: {r.get('status')} {r.get('reason') or ''}")
    with open(path, "a", encoding="utf-8") as f:
        f.write(config.mask_codes("\n".join(lines)) + "\n")


def _window_reason(input_ts: datetime, now: datetime) -> str | None:
    age = (now - input_ts).total_seconds() / 60
    if age > config.PUBLISH_WINDOW_MIN:
        return f"PUBLISH_WINDOW_EXCEEDED({age:.0f}분>{config.PUBLISH_WINDOW_MIN}분)"
    return None


def _publish_gate(mode: str, inp, market: str, now: datetime) -> tuple[bool, str | None]:
    if mode != "live":
        return False, f"mode={mode}"
    from config.settings import is_dry_run
    if is_dry_run():                       # R4 S3: 전역 DRY_RUN이면 claim 전에 막는다
        return False, "GLOBAL_DRY_RUN"
    if market == MARKET_CLOSED:
        return False, "KR_MARKET_CLOSED"
    if market != MARKET_OPEN:              # R4 C1: 장 시작 전에는 휴장 여부를 알 수 없음 → 발행 안 함
        return False, "KR_OPEN_UNVERIFIED"
    why = _window_reason(inp.timestamp, now)
    return (why is None), why


def run(input_path: str, out_path: str, *, now: datetime | None = None, mode: str | None = None,
        http_get=None, store_factory=None, pub=None, sleep=time.sleep,
        rng: random.Random | None = None, clock=None) -> tuple[int, dict]:
    now = now or datetime.now(UTC)
    clock = clock or (lambda: datetime.now(UTC))
    mode = mode or config.current_mode()
    pub = pub or default_publisher
    rng = rng or random.Random()
    run_id, sha = os.getenv("GITHUB_RUN_ID"), os.getenv("GITHUB_SHA")
    logger.info("[KRSleeve] v%s 시작 | mode=%s | run_id=%s | sha=%s", VERSION, mode, run_id, sha)

    out: dict = {"version": VERSION, "mode": mode, "run_id": run_id, "sha": sha,
                 "run_date": now.astimezone(KST).date().isoformat(),
                 "status": "skipped", "reason": None, "detail": None}
    store = None
    inp = None
    exit_code = 0
    try:
        inp = input_contract.validate(input_contract.load_core_data(input_path), now)
        out.update({"run_date": inp.timestamp.astimezone(KST).date().isoformat(),
                    "input_timestamp": inp.timestamp.isoformat(), "regime": inp.regime,
                    "risk_level": inp.risk_level, "us_allocation": inp.us_allocation})

        if mode in ("shadow", "live"):
            try:
                store = (store_factory or _default_store)()
                done = store.already_done(out["run_date"])
            except Exception as e:  # noqa: BLE001
                raise SleeveSkip(DB_WRITE_FAILED, f"DB 연결·조회 실패: {type(e).__name__}") from e
            if done:
                raise SleeveSkip(ALREADY_DONE, out["run_date"])

        kwargs = {"http_get": http_get} if http_get else {}
        quotes, market = collect(now, **kwargs)
        result = engine.compute(inp.us_allocation)
        q = {code: v.to_dict() for code, v in quotes.items()}
        ctx = {"result": result, "quotes": q, "regime": inp.regime, "risk_level": inp.risk_level,
               "us_session": formatter.us_session_label(inp.timestamp)}
        texts = {"x": formatter.format_x(ctx, rng), "telegram": formatter.format_telegram(ctx),
                 "facebook": formatter.format_facebook(ctx)}
        out.update({"status": "computed", "kr_quotes": q, "kr_market_state": market,
                    "kr_market_closed": {MARKET_OPEN: False, MARKET_CLOSED: True}.get(market),
                    "result": result, "texts": texts,
                    "x_weighted_length": formatter.x_weighted_length(texts["x"])})

        if store is not None:
            try:
                store.upsert_daily(_daily_row(out))
            except Exception as e:  # noqa: BLE001
                raise SleeveSkip(DB_WRITE_FAILED, f"daily 기록 실패: {type(e).__name__}") from e

        allowed, why = _publish_gate(mode, inp, market, now)
        out["publish"] = {"allowed": allowed, "blocked_reason": why, "channels": {}}
        if allowed:
            _publish_all(out, texts, store, pub, sleep, rng, inp.timestamp, clock)

    except SleeveSkip as s:
        out.update({"status": "skipped", "reason": s.code, "detail": s.detail})
        logger.warning("[KRSleeve] skipped: %s %s", s.code, config.mask_codes(s.detail))
        if s.code == DB_WRITE_FAILED:
            exit_code = 1
        if store is not None and s.code not in BENIGN and s.code != DB_WRITE_FAILED:
            try:
                store.upsert_daily(_daily_row(out))
            except Exception as e:  # noqa: BLE001
                logger.error("[KRSleeve] skipped 기록 실패: %s", type(e).__name__)
                exit_code = 1
        if mode in ("shadow", "live") and s.code not in BENIGN:
            pub.notify_operator(formatter.format_operator("skipped", s.code, s.detail, out))
    except Exception as e:  # noqa: BLE001 — 예상하지 못한 오류도 결과 파일은 남긴다
        logger.exception("[KRSleeve] 예상하지 못한 오류")
        out.update({"status": "error", "reason": type(e).__name__, "detail": str(e)[:200]})
        exit_code = 1
        if mode in ("shadow", "live"):
            pub.notify_operator(formatter.format_operator("error", type(e).__name__, str(e)[:200], out))

    _write_json(out_path, out)
    _summary(out)
    logger.info("[KRSleeve] 종료 status=%s reason=%s exit=%s", out["status"], out.get("reason"), exit_code)
    return exit_code, out


def _default_store():
    from kr_sleeve.store import SleeveStore
    return SleeveStore()


def _daily_row(out: dict) -> dict:
    res = out.get("result") or {}
    return {
        "run_date": out["run_date"], "status": out["status"], "reason": out.get("reason"),
        "mode": out["mode"], "input_timestamp": out.get("input_timestamp"),
        "regime": out.get("regime"), "risk_level": out.get("risk_level"),
        "k_pct": res.get("k_pct"), "internal_a": res.get("internal_a"), "internal_b": res.get("internal_b"),
        "total_allocation": res.get("total_allocation"), "total_allocation_b": res.get("total_allocation_b"),
        "lookthrough_us_pct": res.get("lookthrough_us_pct"), "kr_prices": out.get("kr_quotes"),
        "kr_market_closed": out.get("kr_market_closed"), "version": out["version"],
        "run_id": out.get("run_id"), "sha": out.get("sha"),
    }


def _publish_all(out: dict, texts: dict, store, pub, sleep, rng, input_ts: datetime, clock) -> None:
    """채널별 pending 선기록 → 1회 발행 → 결과 기록. 선기록 실패 채널은 발행하지 않는다(STD-PUB-01).
    60분 창은 채널마다 발행 직전에 다시 판정한다(R4 C2: X 무작위 대기 뒤 창 초과 방지)."""
    run_date = out["run_date"]
    channels = out["publish"]["channels"]
    plan = (("telegram", pub.publish_telegram), ("facebook", pub.publish_facebook), ("x", pub.publish_x))
    for name, fn in plan:
        if name == "x":
            if formatter.x_weighted_length(texts["x"]) > formatter.X_MAX_WEIGHTED:   # R4 S1
                channels[name] = {"status": "blocked", "reason": "X_TOO_LONG"}
                continue
            sleep(rng.uniform(*config.X_DELAY_RANGE_SEC))
        why = _window_reason(input_ts, clock())
        if why:
            channels[name] = {"status": "blocked", "reason": why}
            continue
        try:
            if not store.claim_publish(run_date, name):
                channels[name] = {"status": "skipped", "reason": "already_claimed"}
                continue
        except Exception as e:  # noqa: BLE001
            channels[name] = {"status": "failed", "reason": f"claim_failed:{type(e).__name__}"}
            continue
        try:
            res = fn(texts[name])
        except Exception as e:  # noqa: BLE001 — R4 W3: 전송 여부 미확정 → unknown, 다음 채널 계속
            res = {"status": "unknown", "post_id": None, "reason": f"publish_exception:{type(e).__name__}"}
        channels[name] = res
        try:
            store.finish_publish(run_date, name, res["status"], res.get("post_id"), res.get("reason"))
        except Exception as e:  # noqa: BLE001
            logger.error("[KRSleeve] %s 결과 기록 실패: %s", name, type(e).__name__)
            res["record_error"] = type(e).__name__

    oks = [c for c in channels.values() if c.get("status") == "ok"]
    bad = {n: c for n, c in channels.items() if c.get("status") in ("failed", "unknown")
           or c.get("record_error")}
    if oks:
        out["status"] = "published"
        try:
            store.set_daily_status(run_date, "published")
        except Exception as e:  # noqa: BLE001
            logger.error("[KRSleeve] published 상태 기록 실패: %s", type(e).__name__)
    if bad:
        detail = ", ".join(f"{n}={c.get('status')}:{c.get('reason')}" for n, c in bad.items())
        pub.notify_operator(formatter.format_operator("publish_issue", "PUBLISH_FAILED", detail, out))


def main() -> int:
    _setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/core_data.json")
    ap.add_argument("--out", default="data/outputs/kr_sleeve.json")
    args = ap.parse_args()
    code, _ = run(args.input, args.out)
    return code


if __name__ == "__main__":
    sys.exit(main())
