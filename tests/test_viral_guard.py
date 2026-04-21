"""
tests/test_viral_guard.py
===================================================
L1 보안 필터 검증 (35건).

실행:
  python -m pytest tests/test_viral_guard.py -v

설계 출처:
  Notion "🎨 C-20 바이럴 고도화 상세 설계서 v2.0" §5.1
"""
import pytest

from engines.viral_guard import (
    sanitize_image_prompt,
    is_person_blocked,
    is_ip_blocked,
)


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_prompt(opt_a: str, opt_b: str) -> str:
    return (
        f"Split-screen image. LEFT: {opt_a}. RIGHT: {opt_b}. "
        "Dramatic lighting."
    )


# ──────────────────────────────────────────────────────────────
# 1~5. IP/브랜드 치환 — 영문
# ──────────────────────────────────────────────────────────────

class TestIPReplacementEnglish:
    def test_01_lamborghini_replaced(self):
        prompt = _make_prompt("A Lamborghini", "A bike")
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "A Lamborghini", "A bike"
        )
        assert safe is True
        assert "lamborghini" not in sanitized.lower()
        assert "luxury sports car" in sanitized.lower()
        assert any("lamborghini" in w for w in warnings)

    def test_02_ferrari_replaced(self):
        prompt = _make_prompt("Ferrari", "Toyota")
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "Ferrari", "Toyota"
        )
        assert safe is True
        assert "ferrari" not in sanitized.lower()
        assert "luxury sports car" in sanitized.lower()

    def test_03_rolex_replaced(self):
        prompt = _make_prompt("Rolex watch", "Casio watch")
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "Rolex watch", "Casio watch"
        )
        assert safe is True
        assert "rolex" not in sanitized.lower()
        assert "luxury watch" in sanitized.lower()

    def test_04_hermes_replaced(self):
        prompt = _make_prompt("Hermes bag", "canvas bag")
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "Hermes bag", "canvas bag"
        )
        assert safe is True
        assert "hermes" not in sanitized.lower()
        assert "luxury handbag" in sanitized.lower()

    def test_05_louis_vuitton_replaced(self):
        prompt = _make_prompt("Louis Vuitton bag", "eco bag")
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "Louis Vuitton bag", "eco bag"
        )
        assert safe is True
        assert "louis vuitton" not in sanitized.lower()
        assert "luxury handbag" in sanitized.lower()


# ──────────────────────────────────────────────────────────────
# 6~10. IP/브랜드 치환 — 한글
# ──────────────────────────────────────────────────────────────

class TestIPReplacementKorean:
    def test_06_lambo_korean_replaced(self):
        prompt = "이미지: 람보르기니 vs 경차"
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "람보르기니", "경차"
        )
        assert safe is True
        assert "람보르기니" not in sanitized
        assert "luxury sports car" in sanitized.lower()

    def test_07_ferrari_korean_replaced(self):
        prompt = "LEFT: 페라리, RIGHT: 현대차"
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "페라리", "현대차"
        )
        assert safe is True
        assert "페라리" not in sanitized

    def test_08_rolex_korean_replaced(self):
        prompt = "A: 롤렉스 시계, B: 애플워치"
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "롤렉스", "애플워치"
        )
        assert safe is True
        assert "롤렉스" not in sanitized
        assert "luxury watch" in sanitized.lower()

    def test_09_hermes_korean_replaced(self):
        prompt = "A: 에르메스, B: 보세"
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "에르메스", "보세"
        )
        assert safe is True
        assert "에르메스" not in sanitized

    def test_10_lv_korean_replaced(self):
        prompt = "A: 루이비통 지갑, B: 캔버스 지갑"
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "루이비통 지갑", "캔버스 지갑"
        )
        assert safe is True
        assert "루이비통" not in sanitized


# ──────────────────────────────────────────────────────────────
# 11~15. 실존 인물 감지 — 정치인/연준
# ──────────────────────────────────────────────────────────────

class TestBlockedPersonPolitics:
    def test_11_trump_blocked(self):
        prompt = _make_prompt("Donald Trump", "average person")
        _, safe, warnings = sanitize_image_prompt(
            prompt, "Donald Trump", "average person"
        )
        assert safe is False
        assert any("trump" in w.lower() for w in warnings)

    def test_12_biden_blocked(self):
        prompt = _make_prompt("Biden style", "regular style")
        _, safe, warnings = sanitize_image_prompt(
            prompt, "Biden style", "regular style"
        )
        assert safe is False

    def test_13_powell_blocked(self):
        prompt = _make_prompt("Jerome Powell speaks", "normal person")
        _, safe, warnings = sanitize_image_prompt(
            prompt, "Jerome Powell speaks", "normal person"
        )
        assert safe is False

    def test_14_yellen_blocked(self):
        prompt = _make_prompt("Yellen meeting", "regular meeting")
        _, safe, warnings = sanitize_image_prompt(
            prompt, "Yellen meeting", "regular meeting"
        )
        assert safe is False

    def test_15_musk_blocked(self):
        prompt = _make_prompt("Elon Musk tweets", "anyone's tweet")
        _, safe, warnings = sanitize_image_prompt(
            prompt, "Elon Musk tweets", "anyone's tweet"
        )
        assert safe is False


# ──────────────────────────────────────────────────────────────
# 16~20. 실존 인물 감지 — 기업인
# ──────────────────────────────────────────────────────────────

class TestBlockedPersonBusiness:
    def test_16_zuckerberg_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "Zuckerberg", "random guy"
        )
        assert safe is False

    def test_17_bezos_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "Jeff Bezos", "regular CEO"
        )
        assert safe is False

    def test_18_buffett_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "Warren Buffett portrait", "random investor"
        )
        assert safe is False

    def test_19_altman_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "Sam Altman", "developer"
        )
        assert safe is False

    def test_20_cook_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "Tim Cook keynote", "regular keynote"
        )
        assert safe is False


# ──────────────────────────────────────────────────────────────
# 21~25. 실존 인물 감지 — 연예인
# ──────────────────────────────────────────────────────────────

class TestBlockedPersonCelebrity:
    def test_21_bts_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "BTS performance", "street dance"
        )
        assert safe is False

    def test_22_blackpink_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "BLACKPINK concert", "random concert"
        )
        assert safe is False

    def test_23_iu_korean_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "아이유 콘서트", "일반 공연"
        )
        assert safe is False

    def test_24_son_korean_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "손흥민 골", "일반 선수"
        )
        assert safe is False

    def test_25_taylor_swift_blocked(self):
        _, safe, _ = sanitize_image_prompt(
            "A vs B", "Taylor Swift", "any singer"
        )
        assert safe is False


# ──────────────────────────────────────────────────────────────
# 26~28. IP 캐릭터 감지
# ──────────────────────────────────────────────────────────────

class TestBlockedIPCharacter:
    def test_26_marvel_detected(self):
        _, safe, warnings = sanitize_image_prompt(
            _make_prompt("Marvel hero", "regular hero"),
            "Marvel hero", "regular hero"
        )
        # marvel은 _REPLACEMENTS에 없음 → detected_ip_no_replacement 경고
        assert safe is True  # 인물이 아니므로 safe=True
        assert any("marvel" in w.lower() for w in warnings)

    def test_27_disney_detected(self):
        _, safe, warnings = sanitize_image_prompt(
            _make_prompt("Disney style castle", "regular castle"),
            "Disney style castle", "regular castle"
        )
        assert safe is True
        assert any("disney" in w.lower() for w in warnings)

    def test_28_pokemon_detected(self):
        _, safe, warnings = sanitize_image_prompt(
            _make_prompt("Pokemon card", "trading card"),
            "Pokemon card", "trading card"
        )
        assert safe is True
        assert any("pokemon" in w.lower() for w in warnings)


# ──────────────────────────────────────────────────────────────
# 29~31. 다중 감지/치환
# ──────────────────────────────────────────────────────────────

class TestMultipleDetection:
    def test_29_multiple_ip_replaced(self):
        """람보르기니 + 롤렉스 동시 치환"""
        prompt = "LEFT: Lamborghini and Rolex. RIGHT: bike and Casio."
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "Lamborghini and Rolex", "bike and Casio"
        )
        assert safe is True
        assert "lamborghini" not in sanitized.lower()
        assert "rolex" not in sanitized.lower()
        assert "luxury sports car" in sanitized.lower()
        assert "luxury watch" in sanitized.lower()

    def test_30_korean_english_mixed(self):
        """한영 혼합 감지"""
        prompt = "A: 람보르기니 (Lamborghini), B: bicycle"
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "람보르기니 Lamborghini", "bicycle"
        )
        assert safe is True
        assert "람보르기니" not in sanitized
        assert "lamborghini" not in sanitized.lower()

    def test_31_person_overrides_ip(self):
        """인물 + IP 동시 감지 시 인물 거부가 우선"""
        _, safe, warnings = sanitize_image_prompt(
            "Elon Musk with Lamborghini",
            "Elon Musk", "Lamborghini"
        )
        assert safe is False
        # 인물 감지로 즉시 반환되므로 IP 치환은 발생하지 않음
        assert any("musk" in w.lower() for w in warnings)


# ──────────────────────────────────────────────────────────────
# 32~33. 대소문자 무관 감지
# ──────────────────────────────────────────────────────────────

class TestCaseInsensitive:
    def test_32_uppercase_detected(self):
        prompt = _make_prompt("LAMBORGHINI", "bike")
        sanitized, safe, _ = sanitize_image_prompt(
            prompt, "LAMBORGHINI", "bike"
        )
        assert safe is True
        assert "LAMBORGHINI" not in sanitized
        assert "lamborghini" not in sanitized.lower()

    def test_33_mixed_case_detected(self):
        prompt = _make_prompt("LaMbOrGhInI", "bike")
        sanitized, safe, _ = sanitize_image_prompt(
            prompt, "LaMbOrGhInI", "bike"
        )
        assert safe is True
        assert "lamborghini" not in sanitized.lower()


# ──────────────────────────────────────────────────────────────
# 34~35. 정상 프롬프트 통과 (무변경)
# ──────────────────────────────────────────────────────────────

class TestCleanPromptPassthrough:
    def test_34_generic_prompt_unchanged(self):
        """일반 프롬프트는 변경되지 않아야 함"""
        prompt = _make_prompt("luxury sports car", "economy car")
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "luxury sports car", "economy car"
        )
        assert safe is True
        assert sanitized == prompt
        assert warnings == []

    def test_35_generic_lifestyle_unchanged(self):
        """일반 라이프스타일 프롬프트는 변경되지 않아야 함"""
        prompt = "Beach sunset vs mountain snow, cinematic"
        sanitized, safe, warnings = sanitize_image_prompt(
            prompt, "Beach sunset", "mountain snow"
        )
        assert safe is True
        assert sanitized == prompt
        assert warnings == []


# ──────────────────────────────────────────────────────────────
# 편의 함수 검증 (보너스)
# ──────────────────────────────────────────────────────────────

class TestHelperFunctions:
    def test_is_person_blocked_true(self):
        assert is_person_blocked("Elon Musk is here") is True

    def test_is_person_blocked_false(self):
        assert is_person_blocked("Normal person walking") is False

    def test_is_ip_blocked_true(self):
        assert is_ip_blocked("A lamborghini") is True

    def test_is_ip_blocked_false(self):
        assert is_ip_blocked("A generic car") is False
