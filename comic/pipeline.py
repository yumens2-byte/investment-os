"""
comic/pipeline.py
Investment Comic v2.0 — 파이프라인 오케스트레이터

실행:
  python -m comic.pipeline --type daily
  python -m comic.pipeline --type weekly

STEP 1. 시장 데이터 수집 + 리스크 판단
STEP 2. Claude 스토리 생성
STEP 3. GPT-4o 이미지 생성
STEP 4. Pillow 합성
STEP 5. 중복 발행 체크
STEP 6. X 발행 + DB 저장
"""

import os
import sys
import logging
import argparse
from datetime import date

# 로깅 설정 (GitHub Actions 로그에서 바로 확인 가능하도록)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def determine_risk(market_data: dict) -> str:
    """
    리스크 레벨 판단 (v1.7.0 기존 로직 재사용)
    임계값: VIX <20=LOW, 20-30=MEDIUM, >=30=HIGH
    """
    vix = market_data.get("vix", 20)
    try:
        vix = float(vix)
    except (TypeError, ValueError):
        vix = 20.0

    if vix < 20:
        return "LOW"
    elif vix < 30:
        return "MEDIUM"
    else:
        return "HIGH"


def _validate_before_publish(
    story: dict,
    final_image_bytes: bytes,
    comic_type: str,
) -> tuple[bool, str]:
    """
    X 발행 직전 최종 검증 게이트
    하나라도 실패하면 발행 차단

    Returns: (통과여부, 실패사유)
    """
    errors = []

    # ── 스토리 검증 ────────────────────────────────────────
    if not story:
        errors.append("story 객체 없음")
    else:
        title   = story.get("title", "").strip()
        caption = story.get("caption", "").strip()
        cuts    = story.get("cuts", [])
        summary = story.get("context_summary", "").strip()

        if not title:
            errors.append("story.title 비어있음")

        if not caption:
            errors.append("story.caption 비어있음")
        elif len(caption) > 280:
            errors.append(f"caption 길이 초과: {len(caption)}자 (X 한도 280자)")

        expected_cuts = 4 if comic_type == "daily" else 8
        if len(cuts) < expected_cuts:
            errors.append(f"컷 수 부족: {len(cuts)}/{expected_cuts}")

        if not summary:
            errors.append("story.context_summary 비어있음")

        for cut in cuts:
            if not cut.get("dialogue", "").strip():
                errors.append(f"Cut #{cut.get('cut_number')} dialogue 비어있음")
            if not cut.get("image_prompt", "").strip():
                errors.append(f"Cut #{cut.get('cut_number')} image_prompt 비어있음")

    # ── 이미지 검증 ────────────────────────────────────────
    if not final_image_bytes:
        errors.append("이미지 bytes 없음")
    elif len(final_image_bytes) < 1000:
        # 1×1 최소 PNG = ~67bytes, 실제 이미지는 반드시 1KB 이상
        errors.append(f"이미지 크기 비정상: {len(final_image_bytes)} bytes (최소 1KB 필요)")

    # ── 결과 ─────────────────────────────────────────────
    if errors:
        return False, " | ".join(errors)
    return True, "OK"


def run(comic_type: str) -> None:
    """
    메인 파이프라인 실행

    Args:
        comic_type: 'daily' | 'weekly'
    """
    from collectors.yahoo_finance import collect_market_snapshot
    from comic.story import generate_story
    from comic.image_gen import generate_images, CostLimitExceeded
    from comic.compositor import compose_final_image
    from db.supabase_client import (
        check_duplicate, get_next_episode_no, get_recent_episodes,
        save_publish_record, save_episode_context, save_publish_failure
    )
    from publishers.x_publisher import publish_tweet_with_image
    from notifier.telegram import send_alert

    today = date.today()
    logger.info(f"{'='*50}")
    logger.info(f"[Pipeline] 시작 — {today} / {comic_type}")
    logger.info(f"{'='*50}")

    # ── STEP 1. 시장 데이터 수집 ──────────────────────────
    logger.info("[STEP 1] 시장 데이터 수집")
    core_data = None
    try:
        market_data = collect_market_snapshot()
        risk_level  = determine_risk(market_data)
        logger.info(f"[STEP 1] 완료 — VIX={market_data.get('vix')}, RISK={risk_level}")
    except Exception as e:
        msg = f"[FAIL] STEP1 시장 데이터 수집 실패: {e}"
        logger.error(msg)
        send_alert(msg)
        sys.exit(1)

    # ── STEP 1-B: Investment OS core_data 로드 (B-19) ────
    try:
        from core.json_builder import load_core_data
        envelope = load_core_data()
        core_data = envelope.get("data", {})
        # core_data에서 regime 기반 risk_level 덮어쓰기
        regime_risk = core_data.get("market_regime", {}).get("market_risk_level", "")
        if regime_risk:
            risk_level = regime_risk
            logger.info(f"[STEP 1-B] core_data 로드 성공 — regime={core_data.get('market_regime', {}).get('market_regime', '?')}, risk={risk_level}")
    except Exception as e:
        logger.info(f"[STEP 1-B] core_data 없음 (정상 — VIX 기반 fallback): {e}")

    # ── STEP 5 선행: 중복 체크 ────────────────────────────
    # (이미지 생성 비용 낭비 방지를 위해 스토리 생성 전에 체크)
    logger.info("[STEP 5-pre] 중복 발행 체크")
    if check_duplicate(today, comic_type):
        logger.info(f"[SKIP] {today} {comic_type} 이미 발행됨 → 정상 종료")
        sys.exit(0)

    # ── STEP 2. 스토리 생성 ──────────────────────────────
    logger.info("[STEP 2] 스토리 생성 (Gemini → Claude fallback)")
    episode_no      = get_next_episode_no()
    recent_episodes = get_recent_episodes(limit=3)

    try:
        story = generate_story(
            risk_level      = risk_level,
            comic_type      = comic_type,
            market_data     = market_data,
            episode_no      = episode_no,
            recent_episodes = recent_episodes,
            core_data       = core_data,
        )
        logger.info(f"[STEP 2] 완료 — Ep.{episode_no}: '{story['title']}'")
    except Exception as e:
        msg = f"[FAIL] STEP2 스토리 생성 실패 (Gemini+Claude): {e}"
        logger.error(msg)
        send_alert(msg)
        sys.exit(1)

    # ── STEP 3. GPT-4o 이미지 생성 ───────────────────────
    logger.info("[STEP 3] GPT-4o 이미지 생성")
    try:
        image_results = generate_images(
            cuts                = story["cuts"],
            monthly_cost_so_far = 0.0,
            # HTML 엔진 전환용 파라미터
            story       = story,
            risk_level  = risk_level,
            market_data = market_data,
            comic_type  = comic_type,
            episode_no  = episode_no,
        )
        total_cost = sum(r["cost"] for r in image_results)
        fallback_n = sum(1 for r in image_results if r["is_fallback"])
        logger.info(f"[STEP 3] 완료 — {len(image_results)}컷, fallback={fallback_n}, 비용=${total_cost:.4f}")
    except CostLimitExceeded as e:
        msg = f"[FAIL] STEP3 GPT-4o 비용 상한 초과: {e}"
        logger.error(msg)
        send_alert(msg)
        sys.exit(1)
    except Exception as e:
        msg = f"[FAIL] STEP3 이미지 생성 전체 실패: {e}"
        logger.error(msg)
        send_alert(msg)
        sys.exit(1)

    # ── STEP 4. Pillow 합성 ──────────────────────────────
    logger.info("[STEP 4] Pillow 이미지 합성")
    try:
        final_image_bytes = compose_final_image(
            image_results = image_results,
            story         = story,
            comic_type    = comic_type,
            risk_level    = risk_level,
            episode_no    = episode_no,
        )
        logger.info(f"[STEP 4] 완료 — {len(final_image_bytes):,} bytes")
    except Exception as e:
        msg = f"[FAIL] STEP4 Pillow 합성 실패: {e}"
        logger.error(msg)
        send_alert(msg)
        sys.exit(1)

    # ── STEP 5. X 발행 ───────────────────────────────────
    # DRY_RUN 공백 or 미설정 시 안전하게 true 처리 (실 발행 방지)
    _dry_run_val = os.getenv("DRY_RUN", "true").strip().lower()
    dry_run = _dry_run_val != "false"  # "false"일 때만 실 발행, 나머지는 DRY_RUN
    logger.info(f"[Pipeline] DRY_RUN={dry_run} (raw='{_dry_run_val}')")

    if dry_run:
        logger.info(f"[DRY_RUN] 발행 생략 — '{story['title']}'")
        logger.info(f"[DRY_RUN] caption: {story['caption']}")
        logger.info("[Pipeline] DRY_RUN 정상 완료")
        sys.exit(0)

    # ── 발행 전 최종 검증 게이트 ─────────────────────────
    logger.info("[GATE] 발행 전 최종 검증")
    gate_ok, gate_msg = _validate_before_publish(story, final_image_bytes, comic_type)
    if not gate_ok:
        msg = f"[BLOCK] 발행 차단 — 검증 실패: {gate_msg}"
        logger.error(msg)
        send_alert(msg)
        sys.exit(1)
    logger.info(f"[GATE] 검증 통과 ✅")

    logger.info("[STEP 6] X 발행")
    tmp_path = None
    try:
        # 기존 x_publisher.publish_tweet_with_image는 image_path(파일경로) 방식
        # image_bytes → 임시파일 저장 후 전달
        import tempfile
        import os as _os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(final_image_bytes)
            tmp_path = tmp.name

        result   = publish_tweet_with_image(story["caption"], tmp_path)
        tweet_id = result.get("tweet_id") if isinstance(result, dict) else str(result)

        if not tweet_id or tweet_id == "DRY_RUN":
            raise RuntimeError(f"X 발행 반환값 비정상: {result}")

        logger.info(f"[STEP 6] 발행 완료 — tweet_id={tweet_id}")
    except Exception as e:
        msg = f"[FAIL] STEP6 X 발행 실패: {e}"
        logger.error(msg)
        save_publish_failure(today, comic_type, episode_no, risk_level, str(e))
        send_alert(msg)
        sys.exit(1)
    finally:
        # 임시파일 반드시 정리
        if tmp_path:
            try:
                import os as _os
                _os.unlink(tmp_path)
            except Exception:
                pass

    # ── STEP 6. DB 저장 ───────────────────────────────────
    logger.info("[STEP 6] DB 저장")
    try:
        save_publish_record(
            publish_date = today,
            comic_type   = comic_type,
            episode_no   = episode_no,
            risk_level   = risk_level,
            tweet_id     = tweet_id,
            cut_count    = len(image_results),
            cost_usd     = total_cost,
        )
        save_episode_context(
            episode_no = episode_no,
            comic_type = comic_type,
            risk_level = risk_level,
            title      = story["title"],
            summary    = story["context_summary"],
        )
        logger.info("[STEP 6] DB 저장 완료")
    except Exception as e:
        # DB 저장 실패는 발행은 성공했으므로 경고만
        logger.warning(f"[WARN] DB 저장 실패 (발행은 완료됨): {e}")
        send_alert(f"[WARN] DB 저장 실패 (발행 tweet_id={tweet_id}): {e}")

    logger.info(f"{'='*50}")
    logger.info(f"[Pipeline] 완료 ✅ Ep.{episode_no} tweet_id={tweet_id}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Investment Comic Pipeline")
    parser.add_argument(
        "--type",
        choices=["daily", "weekly"],
        required=True,
        help="daily=4컷(평일), weekly=8컷(금요일)"
    )
    args = parser.parse_args()
    run(args.type)
