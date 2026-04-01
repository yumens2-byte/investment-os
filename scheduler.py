"""
scheduler.py
=============
역할: 자동 스케줄 실행 (KST 기준)
실행: python scheduler.py

스케줄:
  평일 06:30 KST → Morning Brief   (run_market morning → run_view tweet)
  평일 23:30 KST → Intraday Update (run_market intraday → run_view tweet)
  평일 07:00 KST → Close Summary   (run_market close   → run_view tweet)

주간:
  매주 금요일 20:00 KST → Weekly Thread (run_market close → run_view thread)

수동 즉시 실행:
  python scheduler.py --run-now morning
  python scheduler.py --run-now intraday
  python scheduler.py --run-now close
  python scheduler.py --run-now weekly
"""
import argparse
import logging
import subprocess
import sys
from datetime import datetime

import pytz
import schedule
import time

from config.settings import LOG_LEVEL, SCHEDULE_MORNING, SCHEDULE_INTRADAY, SCHEDULE_CLOSE

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")

KST = pytz.timezone("Asia/Seoul")
PYTHON = sys.executable  # 현재 Python 인터프리터 경로


# ──────────────────────────────────────────────────────────────
# 실행 함수
# ──────────────────────────────────────────────────────────────

def _is_weekday() -> bool:
    """현재 KST 기준 평일 여부"""
    now_kst = datetime.now(KST)
    return now_kst.weekday() < 5  # 0=월 ~ 4=금


def _run_pipeline(session: str, mode: str = "tweet") -> None:
    """
    run_market → run_view 순차 실행.
    run_market 실패 시 run_view 실행 안 함.
    """
    if not _is_weekday() and session != "weekly":
        logger.info(f"[Scheduler] 주말 — {session} 건너뜀")
        return

    logger.info(f"[Scheduler] ▶ 파이프라인 시작: session={session} mode={mode}")

    # Step 1: run_market
    logger.info(f"[Scheduler] run_market.py --session {session}")
    result_market = subprocess.run(
        [PYTHON, "run_market.py", "--session", session],
        capture_output=False,  # 로그를 터미널에 출력
        text=True,
    )

    if result_market.returncode not in (0,):
        logger.error(
            f"[Scheduler] run_market 실패 (rc={result_market.returncode}) "
            f"— run_view 실행 차단"
        )
        return

    # Step 2: run_view
    logger.info(f"[Scheduler] run_view.py --mode {mode}")
    result_view = subprocess.run(
        [PYTHON, "run_view.py", "--mode", mode],
        capture_output=False,
        text=True,
    )

    if result_view.returncode == 0:
        logger.info(f"[Scheduler] ✅ 파이프라인 완료: {session}")
    elif result_view.returncode == 2:
        logger.warning(f"[Scheduler] ⚠️ 발행 차단됨 (중복 또는 Validation 실패): {session}")
    else:
        logger.error(f"[Scheduler] ❌ run_view 실패 (rc={result_view.returncode})")


# ──────────────────────────────────────────────────────────────
# 스케줄 잡 정의
# ──────────────────────────────────────────────────────────────

def job_morning():
    logger.info(f"[Scheduler] ⏰ Morning Brief 시작 ({SCHEDULE_MORNING} KST)")
    _run_pipeline("morning", mode="tweet")


def job_intraday():
    logger.info(f"[Scheduler] ⏰ Intraday Update 시작 ({SCHEDULE_INTRADAY} KST)")
    _run_pipeline("intraday", mode="tweet")


def job_close():
    logger.info(f"[Scheduler] ⏰ Close Summary 시작 ({SCHEDULE_CLOSE} KST)")
    _run_pipeline("close", mode="tweet")


def job_weekly():
    logger.info("[Scheduler] ⏰ Weekly Thread 시작 (금요일 20:00 KST)")
    _run_pipeline("weekly", mode="thread")


# ──────────────────────────────────────────────────────────────
# 스케줄 등록
# ──────────────────────────────────────────────────────────────

def setup_schedule():
    """schedule 라이브러리로 KST 기준 잡 등록"""
    schedule.every().day.at(SCHEDULE_MORNING).do(job_morning)
    schedule.every().day.at(SCHEDULE_INTRADAY).do(job_intraday)
    schedule.every().day.at(SCHEDULE_CLOSE).do(job_close)
    schedule.every().friday.at("20:00").do(job_weekly)

    logger.info("─── 스케줄 등록 완료 ───")
    logger.info(f"  Morning Brief   : 평일 {SCHEDULE_MORNING} KST")
    logger.info(f"  Intraday Update : 평일 {SCHEDULE_INTRADAY} KST")
    logger.info(f"  Close Summary   : 평일 {SCHEDULE_CLOSE} KST")
    logger.info(f"  Weekly Thread   : 매주 금요일 20:00 KST")
    logger.info("────────────────────────")


# ──────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Investment OS Scheduler")
    parser.add_argument(
        "--run-now",
        choices=["morning", "intraday", "close", "weekly"],
        help="즉시 특정 잡 실행 (스케줄 없이 단발 실행)",
    )
    args = parser.parse_args()

    # 즉시 실행 모드
    if args.run_now:
        logger.info(f"[Scheduler] 즉시 실행 모드: {args.run_now}")
        if args.run_now == "morning":
            job_morning()
        elif args.run_now == "intraday":
            job_intraday()
        elif args.run_now == "close":
            job_close()
        elif args.run_now == "weekly":
            job_weekly()
        return

    # 데몬 모드
    setup_schedule()
    logger.info("[Scheduler] 대기 중 (Ctrl+C로 종료)...")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # 30초마다 스케줄 체크
    except KeyboardInterrupt:
        logger.info("[Scheduler] 종료")


if __name__ == "__main__":
    main()
