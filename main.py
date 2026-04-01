"""
main.py — Investment OS 단일 진입점
=====================================
모든 실행은 이 파일 하나로 처리한다.

사용법:
  python main.py --help

  python main.py run market [--session morning|intraday|close|auto]
  python main.py run view   [--mode tweet|thread]
  python main.py run all    [--session morning|intraday|close|full|auto] [--mode tweet|thread]

  python main.py schedule            ← 데몬 모드 (scheduler.py 동등)
  python main.py schedule --now morning

  python main.py test                ← 파일럿 테스트 전체
  python main.py test --round 1
  python main.py test --round 2

  python main.py status              ← 최근 실행 결과 요약

예시:
  python main.py run all --session morning --mode tweet
  python main.py run all --session close  --mode thread
  python main.py test
  python main.py schedule --now morning
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ─── 로거 설정 (main.py 자체 로거) ─────────────────────────────
def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

logger = logging.getLogger("main")


# ══════════════════════════════════════════════════════════════════
# 커맨드 핸들러
# ══════════════════════════════════════════════════════════════════

def cmd_run_market(session: str) -> int:
    """수집 + 분석 실행 → core_data.json 저장"""
    logger.info(f"[main] ▶ run market (session={session})")
    import run_market
    try:
        result = run_market.run(session)
        _print_result("run market", result)
        return 0 if result.get("publish_eligible") else 2
    except Exception as e:
        logger.critical(f"[main] run market 실패: {e}", exc_info=True)
        return 1


def cmd_run_view(mode: str, session: str = None) -> int:
    """검증 + X 발행"""
    logger.info(f"[main] ▶ run view (mode={mode})")
    import run_view
    try:
        result = run_view.run(mode=mode, session=session)
        _print_result("run view", result)
        return 0 if result.get("success") else 2
    except Exception as e:
        logger.critical(f"[main] run view 실패: {e}", exc_info=True)
        return 1


def cmd_run_all(session: str, mode: str) -> int:
    """수집 + 분석 + 발행 전체 파이프라인"""
    logger.info(f"[main] ▶ run all (session={session}, mode={mode})")

    rc_market = cmd_run_market(session)
    if rc_market not in (0,):
        logger.error(f"[main] run market 실패(rc={rc_market}) — run view 차단")
        return rc_market

    rc_view = cmd_run_view(mode, session=session)
    return rc_view


def cmd_schedule(run_now: str | None) -> int:
    """스케줄러 실행"""
    import scheduler

    if run_now:
        logger.info(f"[main] ▶ schedule --now {run_now}")
        job_map = {
            "morning":  scheduler.job_morning,
            "intraday": scheduler.job_intraday,
            "close":    scheduler.job_close,
            "weekly":   scheduler.job_weekly,
        }
        job = job_map.get(run_now)
        if job is None:
            logger.error(f"[main] 알 수 없는 세션: {run_now}")
            return 1
        job()
        return 0

    logger.info("[main] ▶ 스케줄러 데몬 시작")
    scheduler.setup_schedule()
    import time, schedule as sched
    try:
        while True:
            sched.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("[main] 스케줄러 종료")
    return 0


def cmd_test(round_: str) -> int:
    """파일럿 테스트 실행"""
    logger.info(f"[main] ▶ pilot test --round {round_}")
    import pilot_test

    r1 = {"name": "Round 1", "success": True, "passed": 0, "total": 0}
    r2 = {"name": "Round 2", "success": True, "passed": 0, "total": 0}

    if round_ in ("1", "all"):
        r1 = pilot_test.run_round1()
    if round_ in ("2", "all"):
        r2 = pilot_test.run_round2()

    pilot_test.print_final_report(r1, r2)
    return 0 if (r1["success"] and r2["success"]) else 1


def cmd_status() -> int:
    """최근 실행 결과 요약 출력"""
    print("\n" + "="*55)
    print("  Investment OS — 상태 요약")
    print("="*55)

    # core_data.json
    core_path = BASE_DIR / "data" / "outputs" / "core_data.json"
    if core_path.exists():
        with open(core_path, encoding="utf-8") as f:
            core = json.load(f)
        data = core.get("data", {})
        ts = core.get("timestamp", "unknown")[:19].replace("T", " ")
        regime = data.get("market_regime", {})
        snap = data.get("market_snapshot", {})
        signal = data.get("trading_signal", {})
        alloc = data.get("etf_allocation", {}).get("allocation", {})

        print(f"\n  📊 마지막 분석: {ts} UTC")
        print(f"  레짐    : {regime.get('market_regime')} / {regime.get('market_risk_level')}")
        print(f"  시장    : SPY {snap.get('sp500', 0):+.2f}% | VIX {snap.get('vix', 0):.1f} | US10Y {snap.get('us10y', 0):.2f}%")
        print(f"  시그널  : {signal.get('trading_signal')}")
        print(f"  배분    : {alloc}")
    else:
        print("\n  ⚠️  core_data.json 없음 — python main.py run market 먼저 실행")

    # validation
    val_path = BASE_DIR / "data" / "outputs" / "validation_result.json"
    if val_path.exists():
        with open(val_path, encoding="utf-8") as f:
            val = json.load(f)
        dv = val.get("data_validation", {}).get("status", "?")
        ov = val.get("output_validation", {}).get("status", "?")
        icon_d = "✅" if dv == "PASS" else "❌"
        icon_o = "✅" if ov == "PASS" else "❌"
        print(f"\n  검증    : {icon_d} validate_data={dv} | {icon_o} validate_output={ov}")

    # history
    hist_path = BASE_DIR / "data" / "published" / "history.json"
    if hist_path.exists():
        with open(hist_path, encoding="utf-8") as f:
            hist = json.load(f)
        if hist:
            last = hist[-1]
            print(f"\n  마지막 발행: {last.get('timestamp', '')[:19]} UTC")
            print(f"  tweet_id : {last.get('tweet_id')}")
            print(f"  내용 미리보기: {last.get('preview', '')[:60]}...")
            print(f"  총 발행 이력: {len(hist)}건")
    else:
        print("\n  발행 이력 없음")

    print("="*55 + "\n")
    return 0


# ══════════════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════════════

def _print_result(cmd: str, result: dict) -> None:
    """결과 딕셔너리를 보기 좋게 출력"""
    print(f"\n  [{cmd}] 결과")
    for k, v in result.items():
        print(f"    {k}: {v}")
    print()


def _detect_session() -> str:
    """현재 KST 시간 기반 세션 자동 감지"""
    try:
        import pytz
        kst = pytz.timezone("Asia/Seoul")
        hour = datetime.now(kst).hour
    except ImportError:
        hour = datetime.utcnow().hour

    if 5 <= hour < 9:
        return "morning"
    elif 22 <= hour or hour < 2:
        return "intraday"
    else:
        return "close"


# ══════════════════════════════════════════════════════════════════
# CLI 파서
# ══════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Investment OS — 단일 진입점",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py run market --session morning
  python main.py run view   --mode tweet
  python main.py run all    --session close --mode thread
  python main.py schedule   --now morning
  python main.py test
  python main.py status
        """,
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="로그 레벨 (기본: INFO)")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ─────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="수집/분석/발행 실행")
    run_sub = run_p.add_subparsers(dest="run_target", required=True)

    # run market
    rm = run_sub.add_parser("market", help="수집 + 분석 (core_data.json 저장)")
    rm.add_argument("--session",
                    choices=["morning", "intraday", "close", "full", "weekly", "auto"],
                    default="auto",
                    help="실행 세션 (auto=현재 시간 자동 감지)")

    # run view
    rv = run_sub.add_parser("view", help="검증 + X 발행")
    rv.add_argument("--mode",
                    choices=["tweet", "thread"],
                    default="tweet",
                    help="발행 모드")

    # run all
    ra = run_sub.add_parser("all", help="수집 + 분석 + 발행 전체")
    ra.add_argument("--session",
                    choices=["morning", "intraday", "close", "full", "weekly", "auto"],
                    default="auto")
    ra.add_argument("--mode",
                    choices=["tweet", "thread"],
                    default="tweet")

    # ── schedule ─────────────────────────────────────────────────
    sch = sub.add_parser("schedule", help="자동 스케줄 데몬 실행")
    sch.add_argument("--now",
                     choices=["morning", "intraday", "close", "weekly"],
                     default=None,
                     help="즉시 특정 잡 실행 (데몬 아님)")

    # ── test ─────────────────────────────────────────────────────
    tst = sub.add_parser("test", help="파일럿 테스트 실행")
    tst.add_argument("--round",
                     choices=["1", "2", "all"],
                     default="all",
                     help="실행할 라운드 (기본: all)")

    # ── status ───────────────────────────────────────────────────
    sub.add_parser("alert", help="Alert 감지 + 발송 (즉시 실행)")
    sub.add_parser("status", help="최근 실행 결과 요약")

    return parser


# ══════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 환경 변수에서 로그 레벨 우선 적용
    import os
    log_level = os.getenv("LOG_LEVEL", args.log_level)
    _setup_logging(log_level)

    rc = 0

    if args.command == "run":
        if args.run_target == "market":
            session = _detect_session() if args.session == "auto" else args.session
            rc = cmd_run_market(session)

        elif args.run_target == "view":
            rc = cmd_run_view(args.mode)

        elif args.run_target == "all":
            session = _detect_session() if args.session == "auto" else args.session
            rc = cmd_run_all(session, args.mode)

    elif args.command == "schedule":
        rc = cmd_schedule(run_now=args.now)

    elif args.command == "test":
        rc = cmd_test(args.round)

    elif args.command == "alert":
        import run_alert
        result = run_alert.run()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == "status":
        rc = cmd_status()

    sys.exit(rc)


if __name__ == "__main__":
    main()
