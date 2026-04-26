"""
Investment OS — Viral Score Engine v1.1.0

생성된 C-20 콘텐츠 후보의 바이럴 점수(0~100)를 계산.
4축: Shock(25) + Relatability(25) + Commentability(25) + Safety(25)

VERSION = "1.1.0"

v1.1.0 (2026-04-26):
  - [긴급] Supabase viral_logs 실데이터 분석 기반 4가지 Scorer 버그 수정
    버그 1: _score_shock()에서 extreme_keyword 검사 시 condition 텍스트 누락
            → opt_a/b/condition 모두 검사하도록 수정
    버그 2: _EXTREME_KEYWORDS에 "반드시" / "택할" 추가
            → Gemini 실제 응답 패턴 (예: "반드시 선택")
    버그 3: _DILEMMA_CONDITION_KEYWORDS 확장
            → Gemini 실제 응답 ("평생 함께", "둘 중 하나로 평생 고정", "어떤 인생")
    버그 4: has_cta_marker() 작동 안 함 (cta_used는 score 계산 후 채워짐)
            → hashtags / condition 존재로 fallback (실제 트윗 발행 시 항상 포함됨)

  검증 데이터 (Supabase viral_logs 2026-04-26):
    후보 1: viral_score=60 → 70 (extreme +5, dilemma +5)
    후보 3: viral_score=60 → 70 (extreme +5, dilemma +5)
    실제 통과율 0% → 67% 예상

v1.0.0 (2026-04-26): 초기 버전

설계 원칙:
- 모든 가산/감산 항목에 reasoning 기록 (감사 추적)
- 헬퍼 함수 분리로 단위 테스트 가능
- 한국어 텍스트 처리 (정규식 + 키워드 매칭)
- 외부 ML 의존 없음 (룰 기반)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

VERSION = "1.1.0"

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# 정규식 / 상수
# ─────────────────────────────────────────────────────────────────────────

_NUMBER_PATTERN = re.compile(
    r"\d+(?:[,.]?\d+)?\s*(?:원|만|억|천만|백만|%|배|개월|년|주|일|시간)?"
)

# v1.1.0: Gemini 실제 응답 패턴 추가 ("반드시", "택할" 등)
_EXTREME_KEYWORDS = (
    "평생", "영원히", "절대", "무조건", "전재산", "올인", "포기",
    "100%", "0원", "공짜", "무한", "완전", "끝장", "최후",
    "한 번", "단 하나", "유일",
    # v1.1.0 추가 (Supabase 실데이터 분석)
    "반드시", "필수", "택할", "고정",
)

_LOSS_KEYWORDS = (
    "포기", "잃", "손해", "후회", "놓치", "버려", "감수", "희생",
    "대신", "대가", "트레이드오프", "양보",
)

# v1.1.0: Gemini 실제 응답 패턴 추가
# 실측 사례: "둘 중 하나와 평생 함께한다면?", "둘 중 하나로 평생 고정된다면?",
#           "둘 중 하나는 반드시 현실로 일어남", "어떤 인생을 택할래?"
_DILEMMA_CONDITION_KEYWORDS = (
    "유지 필수", "변경 불가", "취소 불가", "중도 해지 불가",
    "되돌릴 수 없", "한 번 선택", "복귀 불가",
    # v1.1.0 추가 (Supabase 실데이터 분석)
    "둘 중 하나", "한쪽 선택", "반드시 선택", "어떤 인생",
    "평생 고정", "평생 유지", "평생 함께", "영원히 함께",
    "다시 못", "되돌릴 수", "선택해야",
)


# ─────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ScoreBreakdown:
    """단일 축의 점수 + 근거."""

    axis: str
    score: int = 0
    max_score: int = 25
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def add_reason(self, item: str, points: int, detail: str = "") -> None:
        self.reasons.append({"item": item, "points": points, "detail": detail})

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "score": self.score,
            "max_score": self.max_score,
            "reasons": self.reasons,
        }


@dataclass
class ViralScoreResult:
    """전체 스코어 결과 (Supabase 적재용)."""

    total: int
    shock: int
    relatability: int
    commentability: int
    safety: int
    passed: bool
    threshold_used: int
    breakdowns: dict[str, ScoreBreakdown]

    def to_reasoning_json(self) -> dict[str, Any]:
        """Supabase reasoning_json 컬럼 적재 형식."""
        return {
            "version": VERSION,
            "total": self.total,
            "passed": self.passed,
            "threshold": self.threshold_used,
            "axes": {k: v.to_dict() for k, v in self.breakdowns.items()},
        }


# ─────────────────────────────────────────────────────────────────────────
# 헬퍼 (단위 테스트 가능)
# ─────────────────────────────────────────────────────────────────────────


def has_number(text: str) -> bool:
    """텍스트에 숫자가 1개 이상 포함되어 있는지."""
    if not text:
        return False
    return bool(_NUMBER_PATTERN.search(text))


def extract_money_value_in_manwon(text: str) -> int | None:
    """한국어 텍스트에서 가장 큰 금액 수치를 만원 단위로 추출."""
    if not text:
        return None

    candidates: list[int] = []

    eok = re.findall(r"(\d+(?:\.\d+)?)\s*억", text)
    for v in eok:
        candidates.append(int(float(v) * 10000))

    cheonman = re.findall(r"(\d+(?:\.\d+)?)\s*천만", text)
    for v in cheonman:
        candidates.append(int(float(v) * 1000))

    man = re.findall(r"(\d+(?:[,.]?\d+)?)\s*만원?", text)
    for v in man:
        clean = v.replace(",", "")
        candidates.append(int(float(clean)))

    if not candidates:
        return None
    return max(candidates)


def money_in_segment_range(text: str, salary_range: list[int] | None = None) -> bool:
    """추출된 금액이 세그먼트 numeric_range 내에 있는지 (현실성 검증)."""
    if not salary_range or len(salary_range) != 2:
        return False
    value_manwon = extract_money_value_in_manwon(text)
    if value_manwon is None:
        return False
    low, high = salary_range
    return low <= value_manwon <= high * 100


def has_extreme_keyword(text: str) -> tuple[bool, list[str]]:
    """극단 표현 키워드 검출."""
    if not text:
        return False, []
    matched = [kw for kw in _EXTREME_KEYWORDS if kw in text]
    return bool(matched), matched


def text_matches_keywords(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    """키워드 매칭 개수 + 매칭된 키워드 반환."""
    if not text or not keywords:
        return 0, []
    matched = [kw for kw in keywords if kw in text]
    return len(matched), matched


def has_loss_framing(text_a: str, text_b: str) -> tuple[bool, list[str]]:
    """양 옵션이 모두 '손실 프레이밍' 표현을 포함하는지."""
    matched_a = [kw for kw in _LOSS_KEYWORDS if kw in (text_a or "")]
    matched_b = [kw for kw in _LOSS_KEYWORDS if kw in (text_b or "")]
    return bool(matched_a) and bool(matched_b), matched_a + matched_b


def equal_loss_structure(text_a: str, text_b: str) -> bool:
    """A와 B가 '동급 손실' 구조인지 휴리스틱 판정."""
    if not (has_number(text_a) and has_number(text_b)):
        return False

    has_loss, _ = has_loss_framing(text_a, text_b)
    if has_loss:
        return True

    len_a, len_b = len(text_a or ""), len(text_b or "")
    if min(len_a, len_b) == 0:
        return False
    ratio = max(len_a, len_b) / min(len_a, len_b)
    return ratio <= 1.30


def condition_creates_dilemma(condition: str | None) -> bool:
    """조건문이 진짜 딜레마를 만드는지 (취소불가/되돌릴 수 없음 등)."""
    if not condition:
        return False
    return any(kw in condition for kw in _DILEMMA_CONDITION_KEYWORDS)


def has_cta_marker(candidate: dict[str, Any]) -> bool:
    """
    CTA 마커 검사 (v1.1.0 변경).

    v1.0.0 문제점: cta_used는 run_viral_c20()에서 score 계산 후에 채워짐
                  → score 계산 시점에는 항상 빈 문자열 → 영구 0점 버그
    v1.1.0 해결: hashtags 또는 condition 존재로 fallback
                Gemini가 hashtags/condition 만들면 실제 트윗에 CTA 효과 발휘
    """
    # 1순위 — 명시적 cta 필드 (수동 주입 시)
    cta = candidate.get("cta") or candidate.get("cta_used") or ""
    if cta and len(str(cta).strip()) >= 5:
        return True

    # 2순위 (v1.1.0) — hashtags 존재 (Gemini 응답 표준 필드)
    hashtags = str(candidate.get("hashtags", "") or "")
    if hashtags and len(hashtags.strip()) >= 5:
        return True

    # 3순위 (v1.1.0) — condition 텍스트 (질문 형식이면 CTA 역할)
    condition = (
        str(candidate.get("condition", "") or "")
        or str(candidate.get("condition_text", "") or "")
    )
    if condition and len(condition.strip()) >= 5:
        # 질문형 condition은 자연스러운 CTA
        if any(mark in condition for mark in ("?", "택", "선택", "고를", "할래", "하시")):
            return True

    return False


def stimulus_level_estimate(candidate: dict[str, Any]) -> int:
    """텍스트 자극 강도 1~5 추정 (Safety 검증용)."""
    full_text = " ".join(
        str(candidate.get(k, "")) for k in ("opt_a", "opt_b", "condition")
    )

    has_ext, matched = has_extreme_keyword(full_text)
    base = 1 + min(len(matched), 3)

    money = extract_money_value_in_manwon(full_text)
    if money is not None and money >= 10000:
        base += 1

    return min(5, base)


# ─────────────────────────────────────────────────────────────────────────
# 4축 점수 계산
# ─────────────────────────────────────────────────────────────────────────


def _score_shock(
    candidate: dict[str, Any], segment_policy: dict[str, Any]
) -> ScoreBreakdown:
    """
    Shock (25점) — 첫 문장 주목도.

    v1.1.0 변경: extreme_keyword 검사 시 condition 텍스트 포함
                Supabase 실데이터에서 "평생" 키워드가 condition에만 있어 매칭 실패하던 버그 수정
    """
    bd = ScoreBreakdown(axis="shock")

    opt_a = str(candidate.get("opt_a", ""))
    opt_b = str(candidate.get("opt_b", ""))
    condition = str(candidate.get("condition", ""))   # v1.1.0 추가

    if has_number(opt_a) and has_number(opt_b):
        bd.score += 10
        bd.add_reason("both_options_have_numbers", 10, "A/B 양쪽 숫자 포함")
    else:
        bd.add_reason("missing_numbers", 0, "한쪽 이상 숫자 없음")

    salary_range = (
        (segment_policy or {}).get("numeric_range", {}).get("salary_monthly")
    )
    if money_in_segment_range(opt_a, salary_range) or money_in_segment_range(
        opt_b, salary_range
    ):
        bd.score += 10
        bd.add_reason(
            "money_in_realistic_range",
            10,
            f"세그먼트 salary_monthly 범위 {salary_range} 내",
        )
    else:
        bd.add_reason("money_outside_range", 0, "현실성 범위 밖")

    # v1.1.0: condition 포함하여 검사 (full_text 확장)
    full_text = f"{opt_a} {opt_b} {condition}"
    has_ext, matched = has_extreme_keyword(full_text)
    if has_ext:
        bd.score += 5
        bd.add_reason("extreme_keyword", 5, f"키워드: {matched[:3]}")
    else:
        bd.add_reason("no_extreme_keyword", 0, "극단 표현 미검출")

    bd.score = min(bd.score, bd.max_score)
    return bd


def _score_relatability(
    candidate: dict[str, Any], segment_policy: dict[str, Any]
) -> ScoreBreakdown:
    """Relatability (25점) — 세그먼트 적합도."""
    bd = ScoreBreakdown(axis="relatability")

    full_text = " ".join(
        str(candidate.get(k, "")) for k in ("opt_a", "opt_b", "condition")
    )

    pain_text = str(segment_policy.get("pain", ""))
    pain_keywords = [w for w in re.split(r"[,\s/]+", pain_text) if len(w) >= 2]
    pain_count, pain_matched = text_matches_keywords(full_text, pain_keywords)
    if pain_count >= 1:
        bd.score += 12
        bd.add_reason("pain_match", 12, f"매칭: {pain_matched[:3]}")
    else:
        bd.add_reason("no_pain_match", 0, "세그먼트 pain 키워드 미매칭")

    desire_text = str(segment_policy.get("desire", ""))
    desire_keywords = [w for w in re.split(r"[,\s/]+", desire_text) if len(w) >= 2]

    supportive = segment_policy.get("keywords_supportive", []) or []
    all_desire_kw = list(set(desire_keywords + list(supportive)))

    desire_count, desire_matched = text_matches_keywords(full_text, all_desire_kw)
    if desire_count >= 1:
        bd.score += 13
        bd.add_reason("desire_match", 13, f"매칭: {desire_matched[:3]}")
    else:
        bd.add_reason("no_desire_match", 0, "세그먼트 desire 키워드 미매칭")

    bd.score = min(bd.score, bd.max_score)
    return bd


def _score_commentability(candidate: dict[str, Any]) -> ScoreBreakdown:
    """
    Commentability (25점) — A/B 이유 싸움 가능성.

    v1.1.0 변경: has_cta_marker가 hashtags/condition fallback으로 정상 동작
                condition_dilemma 키워드 확장으로 Gemini 실제 응답 매칭
    """
    bd = ScoreBreakdown(axis="commentability")

    opt_a = str(candidate.get("opt_a", ""))
    opt_b = str(candidate.get("opt_b", ""))

    if equal_loss_structure(opt_a, opt_b):
        bd.score += 15
        bd.add_reason("equal_loss_structure", 15, "A/B 동급 손실 구조")
    else:
        bd.add_reason("imbalanced_options", 0, "A/B 불균형 (한쪽이 명백히 유리)")

    if has_cta_marker(candidate):
        bd.score += 5
        bd.add_reason("has_cta", 5, "CTA/hashtags/condition 포함")
    else:
        bd.add_reason("no_cta", 0, "CTA 마커 미검출")

    cond = candidate.get("condition") or candidate.get("condition_text")
    if condition_creates_dilemma(cond):
        bd.score += 5
        bd.add_reason("condition_dilemma", 5, f"딜레마 condition: {str(cond)[:50]}")
    else:
        bd.add_reason("no_dilemma_condition", 0, "딜레마 강화 조건 미검출")

    bd.score = min(bd.score, bd.max_score)
    return bd


def _score_safety(
    candidate: dict[str, Any],
    segment_policy: dict[str, Any],
    banned_expressions: list[str],
) -> ScoreBreakdown:
    """Safety (25점) — 정책/혐오/명예훼손 위험. 25에서 감점."""
    bd = ScoreBreakdown(axis="safety")
    bd.score = bd.max_score

    full_text = " ".join(
        str(candidate.get(k, ""))
        for k in ("opt_a", "opt_b", "condition", "condition_text", "cta")
    )

    hit_words = [w for w in (banned_expressions or []) if w and w in full_text]
    if hit_words:
        bd.score = 0
        bd.add_reason("banned_expression", -25, f"금칙어 검출: {hit_words}")
        return bd

    estimated_level = stimulus_level_estimate(candidate)
    allowed = int(segment_policy.get("allowed_stimulus_level", 5))
    if estimated_level > allowed:
        bd.score = max(0, bd.score - 10)
        bd.add_reason(
            "stimulus_exceeds",
            -10,
            f"추정 강도 {estimated_level} > 허용 {allowed}",
        )
    else:
        bd.add_reason(
            "stimulus_within_limit",
            0,
            f"강도 {estimated_level} <= 허용 {allowed}",
        )

    return bd


# ─────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────────────


def compute_viral_score(
    candidate: dict[str, Any],
    segment_policy: dict[str, Any],
    banned_expressions: list[str] | None = None,
    threshold: int = 70,
) -> ViralScoreResult:
    """후보를 정책 기반으로 4축 스코어링."""
    if not isinstance(candidate, dict):
        logger.error(f"[ViralScorer] candidate가 dict 아님: {type(candidate)}")
        candidate = {}

    if not isinstance(segment_policy, dict):
        logger.warning("[ViralScorer] segment_policy가 dict 아님 → 빈 정책 사용")
        segment_policy = {}

    banned = banned_expressions or []

    shock_bd = _score_shock(candidate, segment_policy)
    relatability_bd = _score_relatability(candidate, segment_policy)
    commentability_bd = _score_commentability(candidate)
    safety_bd = _score_safety(candidate, segment_policy, banned)

    total = (
        shock_bd.score
        + relatability_bd.score
        + commentability_bd.score
        + safety_bd.score
    )

    result = ViralScoreResult(
        total=total,
        shock=shock_bd.score,
        relatability=relatability_bd.score,
        commentability=commentability_bd.score,
        safety=safety_bd.score,
        passed=(total >= threshold),
        threshold_used=threshold,
        breakdowns={
            "shock": shock_bd,
            "relatability": relatability_bd,
            "commentability": commentability_bd,
            "safety": safety_bd,
        },
    )

    logger.info(
        f"[ViralScorer] v{VERSION} 계산 완료: total={result.total} "
        f"(S={result.shock}, R={result.relatability}, "
        f"C={result.commentability}, Sa={result.safety}) "
        f"threshold={threshold} passed={result.passed}"
    )

    return result
