"""
run_view.py
===========
역할: 출력(검증 → 중복체크 → X 발행)
실행: python run_view.py [--mode tweet|thread]

파이프라인:
  core_data.json 로드 (run_market.py 결과)
  → validate_data + validate_output (Hard Gate)
  → 중복 검사 (history.json 비교)
  → 트윗 포맷 생성
  → X 발행 (DRY_RUN or 실제)
  → 발행 이력 기록

run_market.py 없이 단독 실행 불가.
"""
import argparse
import logging
import sys
from datetime import datetime, timezone

from config.settings import LOG_LEVEL, DRY_RUN

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_view")


def run(mode: str = "tweet", session: str = None) -> dict:
    """
    출력 파이프라인 실행.
    mode:    "tweet" (단일 트윗) or "thread" (X 쓰레드)
    session: 외부에서 강제 지정 시 output_helpers보다 우선 (full 세션용)
    """
    logger.info(f"{'='*50}")
    logger.info(f"[run_view] 시작 — mode={mode} | DRY_RUN={DRY_RUN}")
    logger.info(f"{'='*50}")

    # ── Step 1: core_data.json 로드 ────────────────────────────
    logger.info("[Step 1] core_data.json 로드")
    from core.json_builder import load_core_data
    try:
        envelope = load_core_data()
    except FileNotFoundError as e:
        logger.error(f"[run_view] {e}")
        return {"success": False, "reason": "core_data_not_found"}

    data = envelope.get("data", {})

    # ── Step 2: Validation Hard Gate ──────────────────────────
    logger.info("[Step 2] Validation 실행")
    from core.validator import validate_data, validate_output

    data_result = validate_data(data)
    output_result = validate_output(data)

    if data_result["status"] != "PASS":
        logger.error(f"[run_view] validate_data FAIL: {data_result['errors']}")
        return {
            "success": False,
            "reason": "validate_data_fail",
            "errors": data_result["errors"],
        }

    if output_result["status"] != "PASS":
        logger.error(f"[run_view] validate_output FAIL: {output_result['errors']}")
        return {
            "success": False,
            "reason": "validate_output_fail",
            "errors": output_result["errors"],
        }

    logger.info("[Step 2] Validation PASS")

    # ── Step 3: 트윗 텍스트 생성 ───────────────────────────────
    logger.info("[Step 3] 트윗 포맷 생성")
    from publishers.x_formatter import format_market_snapshot_tweet, format_thread_posts, format_image_tweet

    # session 인자가 있으면 우선 사용 (full 세션 등 외부 강제 지정)
    _inner_session = data.get("output_helpers", {}).get("session_type", "postmarket")
    session_type = session if session else _inner_session
    session_labels = {
        "morning":   "Morning Brief 🌅",
        "intraday":  "Intraday Update 📡",
        "close":     "Close Summary 🔔",
        "postmarket":"Market Snapshot 📊",
        "full":      "Full Brief 📊",          # v1.8.0 신규
    }
    session_label = session_labels.get(session_type, "Market Snapshot 📊")

    # v1.8.0: session=full 은 mode 무시 — 이미지 트윗 고정
    if session_type == "full":
        primary_text     = format_image_tweet(data, "full")
        image_tweet_text = primary_text
        posts            = [primary_text]
    elif mode == "thread":
        posts = format_thread_posts(data)
        primary_text = posts[0] if posts else ""
        image_tweet_text = None
    else:
        primary_text = format_market_snapshot_tweet(data, session_label)
        image_tweet_text = format_image_tweet(data, session_type)
        posts = [primary_text]

    logger.info(f"[Step 3] 생성 완료 ({len(primary_text)}자)")

    # ── Step 4: 중복 검사 ──────────────────────────────────────
    logger.info("[Step 4] 중복 검사")
    from core.duplicate_checker import is_duplicate, record_published

    if is_duplicate(primary_text, data):
        logger.warning("[run_view] 중복 감지 — 발행 차단")
        return {
            "success": False,
            "reason": "duplicate_detected",
            "text_preview": primary_text[:80],
        }

    logger.info("[Step 4] 중복 없음 — 발행 진행")

    # ── Step 5: 발행 직전 최종 데이터 검증 로그 ───────────────
    logger.info("[Step 5] 발행 직전 데이터 확인")
    _log_publish_summary(data, primary_text)

    # ── Step 5.5: 이미지 생성 ───────────────────────────────────
    logger.info("[Step 5.5] 대시보드 이미지 생성")
    image_path = None
    try:
        from publishers.image_generator import generate_image
        from datetime import datetime, timezone
        image_path = generate_image(data=data, session=session_type)
        if image_path:
            logger.info(f"[Step 5.5] 이미지 생성 완료: {image_path}")
        else:
            logger.warning("[Step 5.5] 이미지 생성 실패 — 텍스트만 발행")
    except Exception as e:
        logger.warning(f"[Step 5.5] 이미지 생성 예외 — 텍스트만 발행: {e}")

    # ── Step 6: X 발행 ─────────────────────────────────────────
    logger.info(f"[Step 6] X 발행 (mode={mode})")
    from publishers.x_publisher import publish_tweet, publish_tweet_with_image, publish_thread

    if mode == "thread":
        pub_result = publish_thread(posts)
    elif image_path and image_tweet_text:
        pub_result = publish_tweet_with_image(image_tweet_text, image_path)
    else:
        pub_result = publish_tweet(primary_text)

    tweet_id = pub_result.get("tweet_id") or pub_result.get("tweet_ids", [""])[0]

    # ── Step 6-TG: 텔레그램 발행 (session=full 전용) ────────────
    if session_type == "full":
        logger.info("[Step 6-TG] 텔레그램 발행 시작")
        try:
            from publishers.telegram_publisher import (
                send_message, send_photo, format_free_signal
            )
            # 무료 채널 — 시그널 텍스트
            free_text = format_free_signal(data)
            send_message(free_text, channel="free")

            # 유료 채널 — 풀버전 대시보드 이미지
            if image_path:
                send_photo(image_path, caption=free_text, channel="paid")
            else:
                logger.warning("[Step 6-TG] 이미지 없음 — 유료 채널 텍스트만 발행")
                send_message(free_text, channel="paid")

            logger.info("[Step 6-TG] 텔레그램 발행 완료")
        except Exception as e:
            logger.warning(f"[Step 6-TG] 텔레그램 발행 예외 (X 발행 영향 없음): {e}")

    # ── Step 7: 이력 기록 ──────────────────────────────────────
    if pub_result.get("success"):
        logger.info("[Step 7] 발행 이력 기록")
        record_published(primary_text, data, tweet_id=str(tweet_id))

    # 결과
    result = {
        "success": pub_result.get("success", False),
        "mode": mode,
        "tweet_id": tweet_id,
        "dry_run": DRY_RUN,
        "regime": data.get("market_regime", {}).get("market_regime", ""),
        "risk_level": data.get("market_regime", {}).get("market_risk_level", ""),
        "text_preview": primary_text[:80],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"{'='*50}")
    logger.info(f"[run_view] 완료 — success={result['success']} | id={tweet_id}")
    logger.info(f"{'='*50}")

    return result


def _log_publish_summary(data: dict, text: str) -> None:
    """발행 직전 핵심 데이터 로그 출력"""
    snap = data.get("market_snapshot", {})
    regime = data.get("market_regime", {})
    alloc = data.get("etf_allocation", {}).get("allocation", {})

    logger.info("─── 발행 직전 데이터 확인 ───")
    logger.info(f"  SPY: {snap.get('sp500', 0):+.2f}% | VIX: {snap.get('vix', 0):.1f} | US10Y: {snap.get('us10y', 0):.2f}%")
    logger.info(f"  Regime: {regime.get('market_regime')} | Risk: {regime.get('market_risk_level')}")
    logger.info(f"  Allocation: {alloc}")
    logger.info(f"  Tweet({len(text)}자):\n{text}")
    logger.info("─────────────────────────────")


def main():
    parser = argparse.ArgumentParser(description="Investment OS — X Publisher")
    parser.add_argument(
        "--mode",
        choices=["tweet", "thread"],
        default="tweet",
        help="발행 모드 (tweet=단일 | thread=쓰레드)",
    )
    args = parser.parse_args()

    try:
        result = run(mode=args.mode)
        if not result.get("success"):
            logger.warning(f"[run_view] 발행 실패/차단: {result.get('reason', 'unknown')}")
            sys.exit(2)
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[run_view] 예외: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
