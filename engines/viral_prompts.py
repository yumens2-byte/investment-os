"""
engines/viral_prompts.py
===================================================
C-20 바이럴 이미지 프롬프트 템플릿 (3모드).

VERSION = "1.0.0"

역할:
  1. 카테고리 인덱스 → 시각화 모드 매핑 (object / situation / lifestyle / None)
  2. 모드별 영문 이미지 프롬프트 생성
  3. viral_engine.py의 _generate_dilemma_image_only() 전용

설계 출처:
  Notion "🎨 C-20 바이럴 고도화 상세 설계서 v2.0 (2026-04-21)" §2

모드 설명:
  - object:    오브젝트 대비 (럭셔리 제품 vs 일반 제품) — 카 10, 11
  - situation: 추상 실루엣 대비 (얼굴 없음, 신체 노출 금지) — 카 7, 9
  - lifestyle: 환경/공간 대비 (인물 없음) — 예비 매핑 (현재 카 4)
  - None:      이미지 생성 안 함 (숫자/추상 비교 카테고리)
"""
import logging

logger = logging.getLogger(__name__)

VERSION = "1.0.0"


# ──────────────────────────────────────────────────────────────
# 카테고리 인덱스 → 모드 매핑
# viral_engine.py _C20_CATEGORIES 리스트 인덱스와 동기화
# ──────────────────────────────────────────────────────────────

_MODE_MAP: dict[int, str | None] = {
    0:  None,        # 극단적 수익 vs 안정 (abstract)
    1:  None,        # 인생 한 방 vs 평생 안정
    2:  None,        # 자산 선택 (부동산/주식/코인)
    3:  None,        # 복리 vs 일시불
    4:  "lifestyle", # 직장 현실 (예비 — _should_generate_image에서 False 반환)
    5:  None,        # SNS 크리에이터 vs 안정 직장
    6:  None,        # N잡러/FIRE vs 대기업
    7:  "situation", # 외모/매력 vs 돈
    8:  None,        # 연애 vs 재테크
    9:  "situation", # 배우자 선택 (외모 vs 자산)
    10: "object",    # 플렉스 소비 (람보르기니/명품)
    11: "object",    # 럭셔리 거지 vs 검소 부자
}


def get_visual_mode(category_index: int) -> str | None:
    """
    카테고리 인덱스를 받아 시각화 모드 문자열 반환.

    Args:
        category_index: 0~11 (viral_engine._C20_CATEGORIES 인덱스)

    Returns:
        "object" | "situation" | "lifestyle" | None
        None이면 이미지 생성 안 함.
    """
    return _MODE_MAP.get(category_index)


# ──────────────────────────────────────────────────────────────
# 프롬프트 템플릿 3종
# ──────────────────────────────────────────────────────────────

def build_object_prompt(
    opt_a_en: str,
    opt_b_en: str,
    condition_en: str = "",
) -> str:
    """
    Mode A — 오브젝트 대비.
    플렉스/소비 카테고리용. 럭셔리 제품 vs 일반 제품의 시각 대비.
    """
    return (
        "Cinematic split-screen product photography, 1080x1080 square, "
        "high-contrast dramatic lighting. "
        "LEFT half: deep navy blue gradient background, "
        f"visual representation of '{opt_a_en[:80]}' as a premium object "
        "or lifestyle item (no text inside the image). "
        "RIGHT half: warm amber gradient background, "
        f"visual representation of '{opt_b_en[:80]}' as a contrasting object "
        "or lifestyle item (no text inside the image). "
        "Center divider: glowing white 'VS' inside a circular halo. "
        "Top banner: 'EXTREME CHOICE' in bold uppercase English. "
        + (f"Bottom center: small caption '{condition_en[:60]}' in yellow. "
           if condition_en else "")
        + "Bottom right corner: small watermark 'EDT Investment'. "
        "Style: luxury editorial, magazine cover feel, high production value. "
        "CRITICAL CONSTRAINTS: "
        "- NO real brand logos (no Apple, Tesla, Nike, Rolex, etc.). "
        "- NO real people's faces. "
        "- NO copyrighted characters (Marvel, Disney, Pokemon, etc.). "
        "- All visible text MUST be in English. "
        "- Generic stylized objects only."
    )


def build_situation_prompt(
    opt_a_en: str,
    opt_b_en: str,
    condition_en: str = "",
) -> str:
    """
    Mode B — 추상 실루엣 대비.
    외모/배우자 카테고리용. 얼굴 없는 실루엣으로 대비 표현.
    신체 노출·성적 포즈 금지.
    """
    return (
        "Artistic split-screen conceptual illustration, 1080x1080 square, "
        "soft painterly style with cinematic lighting. "
        "LEFT half: cool purple-blue gradient, "
        f"abstract silhouette representing the concept '{opt_a_en[:80]}' "
        "(back-lit silhouette, no facial features, no identifiable person). "
        "RIGHT half: warm golden-rose gradient, "
        f"abstract silhouette representing the concept '{opt_b_en[:80]}' "
        "(back-lit silhouette, no facial features, no identifiable person). "
        "Center divider: a thin vertical light beam, with 'VS' floating in the middle. "
        "Top banner: 'EXTREME CHOICE' in bold uppercase English. "
        + (f"Bottom center: small caption '{condition_en[:60]}' in yellow. "
           if condition_en else "")
        + "Bottom right corner: small watermark 'EDT Investment'. "
        "Style: dreamy, introspective, dilemma mood, editorial illustration. "
        "CRITICAL CONSTRAINTS: "
        "- Silhouettes ONLY — absolutely no facial features, no identifiable people. "
        "- No real brand logos. "
        "- No copyrighted characters. "
        "- No sexually suggestive poses or body exposure. "
        "- Fully clothed silhouettes only. "
        "- All visible text in English."
    )


def build_lifestyle_prompt(
    opt_a_en: str,
    opt_b_en: str,
    condition_en: str = "",
) -> str:
    """
    Mode C — 환경/공간 대비.
    라이프스타일 카테고리용 (현재 예비). 인물 없는 환경으로 대비.
    """
    return (
        "Cinematic environmental split-screen, 1080x1080 square, "
        "mood-driven photography style. "
        "LEFT half: "
        f"an environmental scene depicting the lifestyle of '{opt_a_en[:80]}' "
        "(empty room interior, workspace, or outdoor setting — no people visible). "
        "RIGHT half: "
        f"an environmental scene depicting the lifestyle of '{opt_b_en[:80]}' "
        "(contrasting empty room, workspace, or outdoor setting — no people visible). "
        "Center divider: subtle vertical gradient line with 'VS' emblem. "
        "Top banner: 'EXTREME CHOICE' in bold uppercase English. "
        + (f"Bottom center: small caption '{condition_en[:60]}' in yellow. "
           if condition_en else "")
        + "Bottom right corner: small watermark 'EDT Investment'. "
        "Style: architectural digest, lifestyle magazine feel, atmospheric. "
        "CRITICAL CONSTRAINTS: "
        "- No people visible in either scene. "
        "- No real brand logos (no Apple, Tesla, Nike, etc.). "
        "- No copyrighted characters. "
        "- All visible text in English."
    )


# ──────────────────────────────────────────────────────────────
# 공개 API — 라우터
# ──────────────────────────────────────────────────────────────

def build_image_prompt(
    mode: str | None,
    opt_a_en: str,
    opt_b_en: str,
    condition_en: str = "",
) -> str | None:
    """
    모드별 프롬프트 생성 라우터.

    Args:
        mode:         "object" | "situation" | "lifestyle" | None
        opt_a_en:     영어 선택지 A
        opt_b_en:     영어 선택지 B
        condition_en: 영어 조건 (선택)

    Returns:
        프롬프트 문자열 | None (mode=None 또는 미지원 모드)
    """
    if mode == "object":
        return build_object_prompt(opt_a_en, opt_b_en, condition_en)
    elif mode == "situation":
        return build_situation_prompt(opt_a_en, opt_b_en, condition_en)
    elif mode == "lifestyle":
        return build_lifestyle_prompt(opt_a_en, opt_b_en, condition_en)
    else:
        if mode is not None:
            logger.warning(f"[ViralPrompts] 미지원 모드: {mode}")
        return None
