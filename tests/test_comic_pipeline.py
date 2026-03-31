"""
tests/test_comic_pipeline.py
Investment Comic v2.0 — 신규 테스트 케이스 3개

기존 full_test.py의 23개 항목에 추가로 삽입할 테스트
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch
from datetime import date

# ── 테스트 유틸 ───────────────────────────────────────────

class TestResult:
    def __init__(self):
        self.results = []

    def check(self, name: str, passed: bool, msg: str = ""):
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}" + (f" — {msg}" if msg else ""))
        self.results.append((name, passed))

    def summary(self):
        total  = len(self.results)
        passed = sum(1 for _, p in self.results if p)
        print(f"\n  Comic 테스트: {passed}/{total} PASS")
        return passed == total


# ── 테스트 24: Claude 스토리 JSON 유효성 ─────────────────

def test_story_output_valid(t: TestResult):
    """
    Claude API mock으로 story.py JSON 파싱 검증
    필수 키(title, cuts, image_prompt)가 모두 존재하는지 확인
    """
    mock_story = {
        "title": "황소의 귀환",
        "caption": "오늘 시장은 MAX BULLHORN이 지킨다! #InvestmentComic #주식",
        "context_summary": "Max가 하락 압력을 이겨내고 시장을 안정시켰다.",
        "cuts": [
            {
                "cut_number": 1,
                "scene": "Max가 NYSE 앞에 등장한다",
                "dialogue": "시장은 내가 지킨다!",
                "image_prompt": "golden bull warrior in armor standing in front of stock exchange, dynamic pose",
                "mood": "triumphant"
            },
            {
                "cut_number": 2,
                "scene": "VIX 수치가 떨어진다",
                "dialogue": "두려움은 이겼다.",
                "image_prompt": "golden bull warrior smashing a fear meter, green numbers rising",
                "mood": "optimistic"
            },
            {
                "cut_number": 3,
                "scene": "차트가 상승한다",
                "dialogue": "모두 전진!",
                "image_prompt": "golden bull warrior leading a charge, rising green chart behind him",
                "mood": "triumphant"
            },
            {
                "cut_number": 4,
                "scene": "시장이 안정된다",
                "dialogue": "오늘도 승리다.",
                "image_prompt": "golden bull warrior standing victorious, calm market charts glowing green",
                "mood": "optimistic"
            }
        ]
    }

    # 필수 키 검증
    required_keys = {"title", "caption", "context_summary", "cuts"}
    has_keys = required_keys.issubset(set(mock_story.keys()))
    t.check("스토리 필수 키 존재", has_keys)

    # 컷 수 검증
    t.check("daily 컷 수 = 4", len(mock_story["cuts"]) == 4)

    # 각 컷 image_prompt 존재 검증
    all_have_prompt = all(
        "image_prompt" in cut and len(cut["image_prompt"]) > 10
        for cut in mock_story["cuts"]
    )
    t.check("모든 컷에 image_prompt 존재", all_have_prompt)

    # _validate_story 직접 호출
    try:
        from comic.story import _validate_story
        _validate_story(mock_story, "daily")
        t.check("_validate_story 통과", True)
    except Exception as e:
        t.check("_validate_story 통과", False, str(e))


# ── 테스트 25: GPT-4o fallback 동작 ──────────────────────

def test_image_fallback(t: TestResult):
    """
    GPT-4o API 실패 시 fallback 이미지 반환 검증
    - fallback 경로가 반환되어야 함
    - 파이프라인이 중단되지 않아야 함
    """
    from comic.image_gen import generate_single_cut, COST_PER_IMAGE

    # OpenAI API를 강제 실패시키는 mock
    with patch("openai.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.images.generate.side_effect = Exception("API 연결 실패 (mock)")

        # OPENAI_API_KEY mock 설정
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-mock"}):
            try:
                img_bytes, cost = generate_single_cut(
                    image_prompt="golden bull warrior in armor",
                    cut_number=1,
                    current_cost=0.0
                )
                # fallback이면 cost=0.0
                t.check("GPT-4o 실패 시 fallback 반환", img_bytes is not None)
                t.check("fallback 비용 = $0", cost == 0.0)
                t.check("fallback bytes 존재", len(img_bytes) > 0)
            except Exception as e:
                t.check("GPT-4o 실패 시 예외 없음", False, str(e))


# ── 테스트 26: 중복 발행 방지 ────────────────────────────

def test_duplicate_check(t: TestResult):
    """
    동일 날짜 동일 타입 2회 실행 시 2번째는 SKIP 검증
    Supabase를 mock으로 대체
    """
    # 이미 발행된 케이스 mock
    with patch("db.supabase_client.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # 1회 발행됨 (data에 1개 레코드)
        mock_client.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .execute.return_value.data = [{"id": 1}]

        from db.supabase_client import check_duplicate
        is_dup = check_duplicate(date.today(), "daily")
        t.check("기존 발행 → check_duplicate=True", is_dup is True)

    # 미발행 케이스 mock
    with patch("db.supabase_client.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .execute.return_value.data = []

        from db.supabase_client import check_duplicate
        is_dup = check_duplicate(date.today(), "daily")
        t.check("미발행 → check_duplicate=False", is_dup is False)


# ── 메인 실행 ────────────────────────────────────────────

def run_comic_tests() -> bool:
    """
    full_test.py에서 호출되는 진입점
    Returns: True = 전체 PASS
    """
    print("\n[Comic v2.0 신규 테스트]")
    t = TestResult()

    test_story_output_valid(t)
    test_image_fallback(t)
    test_duplicate_check(t)

    return t.summary()


if __name__ == "__main__":
    success = run_comic_tests()
    sys.exit(0 if success else 1)
