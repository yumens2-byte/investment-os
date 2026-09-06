"""
publishers/narrative_visual.py (N-3)
=======================================
narrative 세션 전용 이미지 후보 로테이션 선택기.

문제:
  narrative 세션은 dashboard_html_builder의 full 레이아웃 1종만 사용해
  매일 동일한 색상·폰트·섹션 배치의 이미지를 발행했다(F-3).
  같은 날 full 세션(18:36 KST)이 발행하는 이미지와도 레이아웃이 동일하다.

설계:
  1. 5종 후보를 가중치로 추첨한다.
  2. 직전 2회 사용한 variant는 후보에서 제외해 연속 반복을 차단한다.
  3. 선택한 후보 생성이 실패하면 남은 후보로 순차 폴백한다.
  4. 전부 실패하면 (None, "none") — 호출부는 텍스트 전용 발행으로 진행한다.

이력 파일:
  data/published/narrative_visual_history.json  (최근 _HISTORY_KEEP건)
  ※ GitHub Actions에서는 cache로 복원되지 않으면 매 실행이 빈 이력으로
    시작한다. 그 경우에도 가중 추첨은 정상 동작하며 '연속 반복 차단'만
    적용되지 않는다.

변경이력:
  v1.0.0 (2026-09-06) 신설.
  v1.1.0 (2026-09-06) card_market을 HTML 전용으로 고정.
    dry_run 실측에서 Gemini 생성 카드에 텍스트 오타 2건과 레이더 축 라벨
    임의 생성이 확인됐다(축 6개 중 4개가 실제 Market Score 키와 무관).
    투자 정보 콘텐츠는 정확도가 화풍보다 우선하므로 force_html=True로 호출한다.
    ※ vs_card는 dry_run에서 텍스트·수치 모두 정확해 Gemini 우선 유지(마스터 판단).
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Optional

VERSION = "1.1.0"

logger = logging.getLogger(__name__)

# ── 후보 정의 ────────────────────────────────────────────────
#   variant 이름 → 가중치
VISUAL_WEIGHTS: dict[str, int] = {
    "full_dashboard":    25,
    "compact_dashboard": 25,
    "vs_card":           20,
    "card_market":       15,
    "none":              15,
}

# 직전 N회 사용한 variant는 후보에서 제외
_RECENT_BLOCK = 2

# 이력 보관 건수
_HISTORY_KEEP = 7

_HISTORY_FILENAME = "narrative_visual_history.json"


# ══════════════════════════════════════════════════════════════
# 이력 입출력
# ══════════════════════════════════════════════════════════════

def _history_path() -> Path:
    try:
        from config.settings import PUBLISHED_DIR
        base = Path(PUBLISHED_DIR)
    except Exception:
        base = Path("data/published")
    return base / _HISTORY_FILENAME


def _load_history() -> list[str]:
    path = _history_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return [str(v) for v in raw]
        logger.warning("[NarrVisual] 이력 형식 불일치 → 빈 이력으로 처리")
        return []
    except Exception as e:
        logger.warning(f"[NarrVisual] 이력 로드 실패 → 빈 이력으로 처리: {e}")
        return []


def _save_history(history: list[str]) -> None:
    path = _history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history[-_HISTORY_KEEP:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[NarrVisual] 이력 저장 실패 (발행 영향 없음): {e}")


# ══════════════════════════════════════════════════════════════
# 후보 추첨
# ══════════════════════════════════════════════════════════════

def _pick_order(history: list[str]) -> list[str]:
    """
    가중 추첨으로 후보 순서를 만든다.
    직전 _RECENT_BLOCK개는 제외하되, 제외 후 후보가 비면 전체를 사용한다.
    """
    recent = set(history[-_RECENT_BLOCK:]) if history else set()
    pool = {k: w for k, w in VISUAL_WEIGHTS.items() if k not in recent}
    if not pool:
        logger.info("[NarrVisual] 최근 사용 제외 후 후보 없음 → 전체 후보 사용")
        pool = dict(VISUAL_WEIGHTS)

    order: list[str] = []
    remaining = dict(pool)
    while remaining:
        names = list(remaining.keys())
        weights = [remaining[n] for n in names]
        chosen = random.choices(names, weights=weights, k=1)[0]
        order.append(chosen)
        del remaining[chosen]

    # 제외됐던 항목은 마지막 폴백 후보로 뒤에 붙인다.
    for name in VISUAL_WEIGHTS:
        if name not in order:
            order.append(name)
    return order


# ══════════════════════════════════════════════════════════════
# 후보별 생성기
# ══════════════════════════════════════════════════════════════

def _make_full_dashboard(data: dict) -> Optional[str]:
    from publishers.dashboard_html_builder import build_html_dashboard
    return build_html_dashboard(data=data, session="narrative", variant="full")


def _make_compact_dashboard(data: dict) -> Optional[str]:
    from publishers.dashboard_html_builder import build_html_dashboard
    return build_html_dashboard(data=data, session="narrative", variant="compact")


def _make_vs_card(data: dict) -> Optional[str]:
    from comic.vs_card_generator import generate_vs_card
    return generate_vs_card(data)


def _make_card_market(data: dict) -> Optional[str]:
    # v1.1.0: force_html=True — Gemini 텍스트 렌더 오류 회피
    from comic.card_news_generator import generate_single_card
    return generate_single_card(data, card_no=1, force_html=True)


_GENERATORS = {
    "full_dashboard":    _make_full_dashboard,
    "compact_dashboard": _make_compact_dashboard,
    "vs_card":           _make_vs_card,
    "card_market":       _make_card_market,
}


# ══════════════════════════════════════════════════════════════
# 공개 API
# ══════════════════════════════════════════════════════════════

def select_visual(data: dict) -> tuple[Optional[str], str]:
    """
    narrative 세션 이미지를 후보 로테이션으로 선택·생성한다.

    Args:
        data: core_data.json의 data 필드

    Returns:
        (image_path, variant)
          - image_path: 생성된 PNG 경로. 이미지 없음이거나 전부 실패하면 None
          - variant:    실제 사용된 후보 이름 ("none" 포함)
    """
    logger.info(f"[NarrVisual] v{VERSION} 시작")

    if not data:
        logger.info("[NarrVisual] data 없음 → 이미지 없이 진행")
        return None, "none"

    history = _load_history()
    order = _pick_order(history)
    logger.info(f"[NarrVisual] 최근 이력={history[-_RECENT_BLOCK:]} | 후보 순서={order}")

    for variant in order:
        if variant == "none":
            logger.info("[NarrVisual] 선택: none — 텍스트 전용 발행")
            _record(history, "none")
            return None, "none"

        gen = _GENERATORS.get(variant)
        if gen is None:
            continue

        try:
            path = gen(data)
        except Exception as e:
            logger.warning(f"[NarrVisual] {variant} 생성 예외 → 다음 후보: {e}")
            continue

        if path:
            logger.info(f"[NarrVisual] 선택: {variant} → {path}")
            _record(history, variant)
            return path, variant

        logger.warning(f"[NarrVisual] {variant} 생성 실패(None) → 다음 후보")

    logger.warning("[NarrVisual] 전 후보 생성 실패 — 텍스트 전용 발행으로 폴백")
    _record(history, "none")
    return None, "none"


def _record(history: list[str], variant: str) -> None:
    history.append(variant)
    _save_history(history)
