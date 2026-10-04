"""
engines/streamer_fact_guard.py
==============================
F3 (2026-10-05): 유튜버 요약(C-16) 사실 정합성 가드.

배경
  streamer_analyzer 는 유튜버 영상 제목·설명을 Gemini 로 요약한다. 영상 원문의 표현이
  실제 지표와 반대여도 그대로 X 트윗(run_view Step 6-YT)으로 발행됐다.
    2026-10-05 실측: 요약 「긍정적인 고용지표 영향으로 시장 상승세」
                     vs 실제 NFP +29K → labor_state "Weak Labor" (NFP_MODERATE 50K 미만)

원칙
  - 트윗의 bullet(•) 한 줄 단위로 검사한다. 불일치 줄만 삭제하고 나머지 형식은 그대로 둔다.
  - 판정은 core_data 의 수치·상태값(사실)만 근거로 한다. 데이터가 없거나 판단 불가면 유지한다.
  - 증시 방향(R2)은 의견·전망 표현(우려·가능성·전망 등)이 있으면 사실 주장이 아니므로 검사하지 않는다.
    고용(R1)은 발표된 지표에 대한 서술이므로 의견 표현이 섞여 있어도 극성이 반대면 삭제한다(엄격).
  - 남은 bullet 이 0개면 트윗을 비워 발행하지 않는다.

검사 규칙 (사실로 검증 가능한 2종만)
  R1 고용   : 고용 키워드 + 긍정/부정 극성 ↔ signals.labor_state
              Weak Labor·Job Loss 인데 긍정 주장 → 불일치
              Strong Labor 인데 부정 주장       → 불일치
              Moderate Labor·No Data            → 판정 안 함
  R2 증시   : 증시 키워드 + 상승/하락 주장 ↔ market_snapshot.sp500·nasdaq 부호
              두 지수 모두 하락인데 상승 주장 → 불일치 / 모두 상승인데 하락 주장 → 불일치
  ※ 물가(CPI)는 데이터가 수준(YoY)뿐이라 「둔화·가속」 같은 방향 주장을 검증할 수 없어 제외.
  ※ 「실업」은 극성이 반대로 읽히므로(실업 증가 = 고용 악화) 고용 키워드에서 제외.
"""
from __future__ import annotations

import logging

VERSION = "1.0.0"

logger = logging.getLogger(__name__)

BULLET_PREFIXES: tuple[str, ...] = ("•",)

OPINION_MARKERS: tuple[str, ...] = (
    "우려", "가능성", "전망", "경계", "예상", "리스크", "주의", "대비", "여부", "관망", "주목", "?",
)

LABOR_KEYWORDS: tuple[str, ...] = ("고용", "일자리", "비농업", "NFP", "nfp", "취업자")
LABOR_POSITIVE: tuple[str, ...] = (
    "호조", "강세", "견조", "탄탄", "긍정", "개선", "서프라이즈", "강한", "양호", "증가", "호전",
)
LABOR_NEGATIVE: tuple[str, ...] = (
    "둔화", "부진", "약화", "쇼크", "악화", "감소", "약세", "냉각", "위축", "약한",
)
LABOR_WEAK_STATES: tuple[str, ...] = ("Weak Labor", "Job Loss")
LABOR_STRONG_STATES: tuple[str, ...] = ("Strong Labor",)

MARKET_KEYWORDS: tuple[str, ...] = (
    "증시", "주가", "시장", "나스닥", "S&P", "다우", "지수", "미장", "뉴욕증시",
)
MARKET_UP: tuple[str, ...] = ("상승", "강세", "랠리", "반등", "신고가", "오름")
MARKET_DOWN: tuple[str, ...] = ("하락", "약세", "급락", "조정", "내림", "폭락")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _polarity(text: str, pos: tuple[str, ...], neg: tuple[str, ...]) -> str:
    p, n = _has(text, pos), _has(text, neg)
    if p and not n:
        return "pos"
    if n and not p:
        return "neg"
    return ""


def check_point(text: str, data: dict) -> tuple[bool, str]:
    """한 줄 검사. (일치 여부, 불일치 사유). 판단 불가는 (True, "")."""
    if not text:
        return True, ""

    signals = (data or {}).get("signals") or {}
    snap = (data or {}).get("market_snapshot") or {}

    # R1 고용
    if _has(text, LABOR_KEYWORDS):
        claim = _polarity(text, LABOR_POSITIVE, LABOR_NEGATIVE)
        state = str(signals.get("labor_state") or "")
        nfp = signals.get("nfp_change")
        if claim == "pos" and state.startswith(LABOR_WEAK_STATES):
            return False, f"labor:claim=positive,actual={state},nfp={nfp}"
        if claim == "neg" and state.startswith(LABOR_STRONG_STATES):
            return False, f"labor:claim=negative,actual={state},nfp={nfp}"

    # R2 증시 방향 — 의견·전망 문장(「하락 우려」 등)은 사실 주장이 아니므로 제외
    if _has(text, MARKET_KEYWORDS) and not _has(text, OPINION_MARKERS):
        claim = _polarity(text, MARKET_UP, MARKET_DOWN)
        sp, nq = snap.get("sp500"), snap.get("nasdaq")
        if claim and isinstance(sp, (int, float)) and isinstance(nq, (int, float)):
            if claim == "pos" and sp < 0 and nq < 0:
                return False, f"market:claim=up,actual=spy{sp:+.2f}/nasdaq{nq:+.2f}"
            if claim == "neg" and sp > 0 and nq > 0:
                return False, f"market:claim=down,actual=spy{sp:+.2f}/nasdaq{nq:+.2f}"

    return True, ""


def sanitize_tweet(tweet: str, data: dict) -> dict:
    """
    트윗의 bullet 줄 중 불일치 줄을 삭제한다.

    Returns:
        {"tweet": str, "removed": [{"line", "reason"}], "bullets_before": int, "bullets_after": int}
        bullet 이 있었는데 전부 삭제되면 tweet="" (발행하지 않음).
    """
    if not tweet:
        return {"tweet": tweet or "", "removed": [], "bullets_before": 0, "bullets_after": 0}

    kept_lines, removed = [], []
    before = after = 0
    for line in tweet.split("\n"):
        stripped = line.strip()
        if stripped.startswith(BULLET_PREFIXES):
            before += 1
            ok, reason = check_point(stripped, data)
            if not ok:
                removed.append({"line": stripped, "reason": reason})
                continue
            after += 1
        kept_lines.append(line)

    if before and after == 0:
        return {"tweet": "", "removed": removed, "bullets_before": before, "bullets_after": 0}
    return {"tweet": "\n".join(kept_lines), "removed": removed,
            "bullets_before": before, "bullets_after": after}


def filter_points(points: list, data: dict) -> tuple[list, list]:
    """summary_points 목록에서 불일치 항목 제거. (유지 목록, 삭제 목록)."""
    kept, removed = [], []
    for p in points or []:
        ok, reason = check_point(str(p), data)
        if ok:
            kept.append(p)
        else:
            removed.append({"line": str(p), "reason": reason})
    return kept, removed


def apply(consensus: dict, data: dict) -> dict:
    """streamer_consensus dict 에 가드 적용 후 새 dict 반환 (원본 불변)."""
    result = dict(consensus or {})
    t = sanitize_tweet(result.get("tweet", ""), data)
    kept_points, removed_points = filter_points(result.get("summary_points", []), data)
    result["tweet"] = t["tweet"]
    result["summary_points"] = kept_points
    result["fact_guard"] = {
        "version": VERSION,
        "removed_tweet_lines": t["removed"],
        "removed_points": removed_points,
        "bullets_before": t["bullets_before"],
        "bullets_after": t["bullets_after"],
        "blocked": bool(t["bullets_before"]) and t["bullets_after"] == 0,
    }
    for r in t["removed"]:
        logger.warning(f"[StreamerFactGuard] 불일치 줄 삭제: {r['line']} ({r['reason']})")
    if result["fact_guard"]["blocked"]:
        logger.warning("[StreamerFactGuard] 정합 bullet 0개 — 유튜버 요약 트윗 발행 안 함")
    return result
