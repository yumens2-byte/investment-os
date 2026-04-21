"""
tests/test_viral_prompts.py
===================================================
이미지 프롬프트 모드 분기 검증 (12건).

실행:
  python -m pytest tests/test_viral_prompts.py -v

설계 출처:
  Notion "🎨 C-20 바이럴 고도화 상세 설계서 v2.0" §5.2
"""
import pytest

from engines.viral_prompts import (
    get_visual_mode,
    build_image_prompt,
    build_object_prompt,
    build_situation_prompt,
    build_lifestyle_prompt,
)


# ──────────────────────────────────────────────────────────────
# 1~3. 모드 라우팅 — visual 카테고리
# ──────────────────────────────────────────────────────────────

class TestVisualModeRouting:
    def test_01_category_10_object(self):
        """카테고리 10 (플렉스 소비) → object 모드"""
        assert get_visual_mode(10) == "object"

    def test_02_category_11_object(self):
        """카테고리 11 (럭셔리 거지) → object 모드"""
        assert get_visual_mode(11) == "object"

    def test_03_category_7_9_situation(self):
        """카테고리 7 (외모/매력), 9 (배우자) → situation 모드"""
        assert get_visual_mode(7) == "situation"
        assert get_visual_mode(9) == "situation"


# ──────────────────────────────────────────────────────────────
# 4~5. abstract 카테고리 (None 반환)
# ──────────────────────────────────────────────────────────────

class TestAbstractCategoryNone:
    def test_04_numeric_categories_none(self):
        """숫자 중심 카테고리는 None"""
        for idx in [0, 1, 2, 3]:
            assert get_visual_mode(idx) is None, f"카테고리 {idx} 이미지 생성 안 함"

    def test_05_career_lifestyle_none(self):
        """직장/SNS/FIRE/연애 카테고리는 None (4는 lifestyle 예비 매핑)"""
        # 카 5, 6, 8은 None
        assert get_visual_mode(5) is None
        assert get_visual_mode(6) is None
        assert get_visual_mode(8) is None
        # 카 4는 lifestyle (예비)
        assert get_visual_mode(4) == "lifestyle"


# ──────────────────────────────────────────────────────────────
# 6~8. build_image_prompt — 3모드 생성
# ──────────────────────────────────────────────────────────────

class TestBuildImagePrompt:
    def test_06_object_mode_prompt(self):
        prompt = build_image_prompt(
            "object", "A Lamborghini", "A bicycle", ""
        )
        assert prompt is not None
        assert "split-screen product photography" in prompt.lower()
        assert "lamborghini" in prompt.lower()
        assert "bicycle" in prompt.lower()
        assert "EXTREME CHOICE" in prompt
        # 가드레일 키워드 확인
        assert "NO real brand logos" in prompt
        assert "NO real people's faces" in prompt

    def test_07_situation_mode_prompt(self):
        prompt = build_image_prompt(
            "situation", "Attractive appearance", "Wealthy status", ""
        )
        assert prompt is not None
        assert "silhouette" in prompt.lower()
        assert "no facial features" in prompt.lower()
        # 신체 노출 금지 키워드
        assert "No sexually suggestive poses" in prompt
        assert "Fully clothed silhouettes only" in prompt

    def test_08_lifestyle_mode_prompt(self):
        prompt = build_image_prompt(
            "lifestyle", "Corporate office", "Beach workspace", ""
        )
        assert prompt is not None
        assert "environmental" in prompt.lower() or "lifestyle" in prompt.lower()
        # 인물 없음 제약
        assert "no people visible" in prompt.lower()


# ──────────────────────────────────────────────────────────────
# 9. None 입력 시 None 반환
# ──────────────────────────────────────────────────────────────

class TestNoneInput:
    def test_09_none_mode_returns_none(self):
        """mode=None → None 반환"""
        assert build_image_prompt(None, "A", "B", "") is None

    def test_09b_invalid_mode_returns_none(self):
        """잘못된 모드 → None 반환"""
        assert build_image_prompt("invalid_mode", "A", "B", "") is None


# ──────────────────────────────────────────────────────────────
# 10~11. condition 유무 처리
# ──────────────────────────────────────────────────────────────

class TestConditionHandling:
    def test_10_with_condition(self):
        """condition_en 있으면 프롬프트에 포함"""
        prompt = build_image_prompt(
            "object",
            "Premium object",
            "Basic object",
            "Must choose one",
        )
        assert prompt is not None
        assert "Must choose one" in prompt
        assert "Bottom center" in prompt

    def test_11_without_condition(self):
        """condition_en 없으면 caption 블록 미포함"""
        prompt = build_image_prompt(
            "object", "Premium object", "Basic object", ""
        )
        assert prompt is not None
        # condition 없을 때는 "Bottom center: small caption" 블록이 없어야 함
        assert "Bottom center: small caption" not in prompt


# ──────────────────────────────────────────────────────────────
# 12. 문자열 길이 제한
# ──────────────────────────────────────────────────────────────

class TestStringTruncation:
    def test_12_opt_truncated_at_80(self):
        """opt_a_en이 100자여도 프롬프트에는 80자까지만 포함"""
        long_text = "A" * 100  # 100자
        prompt = build_image_prompt(
            "object", long_text, "B", ""
        )
        assert prompt is not None
        # 80자는 포함, 81자 째부터는 제외
        assert "A" * 80 in prompt
        # 100자 전체가 그대로 들어가면 안 됨
        assert "A" * 100 not in prompt

    def test_12b_condition_truncated_at_60(self):
        """condition_en이 100자여도 60자까지만 포함"""
        long_condition = "X" * 100
        prompt = build_image_prompt(
            "object", "A", "B", long_condition
        )
        assert prompt is not None
        assert "X" * 60 in prompt
        assert "X" * 100 not in prompt
