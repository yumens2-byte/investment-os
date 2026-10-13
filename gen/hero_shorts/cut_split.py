"""
gen/hero_shorts/cut_split.py — TikTok 분할 발행용 컷 분할기
============================================================
마스터 승인(2026-10-13): JEN 70초 7컷 조립물을 틱톡용 20~30초 3편으로 분할한다.

분할 규격 (사전 고정 — 승인 기준):
    편1 = 컷 1~3 (30초) / 편2 = 컷 4~5 (20초) / 편3 = 컷 6~7 (20초)

원칙:
    - 원본 컷 순서·역할을 재배열하지 않는다(캐논 보존).
    - 7컷이 아닌 계획은 거부한다(추측 분할 금지).
    - 캡션 문구는 창작하지 않는다 — 시리즈 마커만 부여한다(문구는 caption.py/마스터 입력).
"""
import logging

log = logging.getLogger("hero_shorts.cut_split")

VERSION = "1.0.0"

CUT_DURATION_SEC = 10
EXPECTED_CUTS = 7
SEGMENTS = ((1, 3), (4, 5), (6, 7))
TOTAL_SEGMENTS = len(SEGMENTS)


def _series_marker(segment_no):
    """결정적 시리즈 마커 — 문구 창작 없음."""
    return f"{segment_no}/{TOTAL_SEGMENTS}"


def split_plan(plan):
    """7컷 컷계획 → 3편 분할.

    Args:
        plan: [{"cut_no":1..7, "role":..., "prompt":..., ...}, ...]

    Returns:
        [{"segment_no","cut_nos","roles","total_duration_sec","series_marker","cuts"}, ...]

    Raises:
        ValueError: 컷 수 불일치 또는 컷 번호 결손(추측 분할 금지).
    """
    if len(plan) != EXPECTED_CUTS:
        raise ValueError(
            f"분할 거부 — 컷 수 {len(plan)} (기대 {EXPECTED_CUTS}). 추측 분할 금지."
        )
    cuts_by_no = {c["cut_no"]: c for c in plan}
    missing = [n for n in range(1, EXPECTED_CUTS + 1) if n not in cuts_by_no]
    if missing:
        raise ValueError(f"컷 번호 결손: {missing}")

    segments = []
    for idx, (start, end) in enumerate(SEGMENTS):
        nos = list(range(start, end + 1))
        cuts = [cuts_by_no[n] for n in nos]
        segments.append({
            "segment_no": idx + 1,
            "cut_nos": nos,
            "roles": [c.get("role") for c in cuts],
            "total_duration_sec": len(nos) * CUT_DURATION_SEC,
            "series_marker": _series_marker(idx + 1),
            "cuts": cuts,
        })
    log.info(
        "컷 분할 완료 v%s: %d편 %s",
        VERSION, len(segments),
        [(s["segment_no"], s["total_duration_sec"]) for s in segments],
    )
    return segments


def total_duration(segments):
    """분할 결과 총 길이(초). 원본 70초와 일치해야 한다."""
    return sum(s["total_duration_sec"] for s in segments)
