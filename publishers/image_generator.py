"""
publishers/image_generator.py
===============================
세션/이벤트별 이미지 종류 선택 라우터.
- morning / intraday / close → 시장 대시보드
- weekly                     → 시장 대시보드 (주간)
- alert                      → (추후 Alert 배너)

Returns: PNG 파일 경로 or None
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def generate_image(
    data: dict,
    session: str = "morning",
    dt_utc: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    세션에 맞는 이미지를 생성하고 PNG 경로를 반환한다.

    Args:
        data: core_data.json의 data 필드
        session: morning / intraday / close / weekly / alert
        dt_utc: 기준 시각 (None이면 현재)
        output_dir: 저장 디렉토리 (None이면 기본값 사용)

    Returns:
        str: PNG 파일 경로 (성공)
        None: 생성 실패 → 텍스트만 발행으로 폴백
    """
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)

    logger.info(f"[ImageGen] 이미지 생성 시작 — session={session}")

    # F-2: 전 세션 HTML/Playwright 우선 시도
    try:
        from publishers.dashboard_html_builder import build_html_dashboard
        path = build_html_dashboard(
            data=data,
            session=session,
            dt_utc=dt_utc,
            output_dir=output_dir,
        )
        if path:
            logger.info(f"[ImageGen] HTML 대시보드 생성 완료: {path}")
            return path
        logger.warning("[ImageGen] HTML 렌더링 실패 → matplotlib fallback")
    except Exception as e:
        logger.warning(f"[ImageGen] HTML 렌더링 예외 → matplotlib fallback: {e}")

    # matplotlib fallback (Playwright 미설치 등)
    # ★ dashboard_builder.py 절대 수정 금지 — 기존 로직 완전 유지
    try:
        from publishers.dashboard_builder import build_dashboard
        path = build_dashboard(
            data=data,
            session=session,
            dt_utc=dt_utc,
            output_dir=output_dir,
        )
    except Exception as e:
        logger.error(f"[ImageGen] matplotlib fallback도 실패: {e}")
        path = None

    if path:
        logger.info(f"[ImageGen] 생성 완료: {path}")
    else:
        logger.warning("[ImageGen] 이미지 생성 실패 — 텍스트 전용 발행으로 폴백")

    return path
