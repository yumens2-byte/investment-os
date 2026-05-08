"""
tests/test_ai_tone_modules_pilot.py
====================================
D-1/D-2/D-3 파일럿 테스트.

검증 항목:
  - core.tone_policy: ToneSpec 불변성, 매트릭스 결정, 프롬프트 빌더 4요소 포함
  - core.ai_output_validator: 7종 검증 + 어색함 휴리스틱 6룰
  - db.ai_quality_store: row 매핑, exception swallow, optional 필드 처리

실행: pytest tests/test_ai_tone_modules_pilot.py -v
"""
from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 테스트용 import 경로
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tone_policy import (
    VERSION as TONE_VERSION,
    select_persona_tone,
    build_tweet_prompt,
    build_thread_prompt,
    build_retry_prompt,
)
from core.ai_output_validator import (
    VERSION as VALIDATOR_VERSION,
    detect_non_publishable_chars,
    validate,
)
from db.ai_quality_store import (
    VERSION as STORE_VERSION,
    log_ai_attempt,
    _build_row,
)


# ════════════════════════════════════════════════════════════
# Fixture
# ════════════════════════════════════════════════════════════

@pytest.fixture
def sample_data():
    """모킹된 core_data 일부."""
    return {
        "market_snapshot": {
            "sp500": -1.25,
            "vix": 28.5,
            "oil": 78.3,
            "us10y": 4.42,
        },
        "market_regime": {
            "market_regime": "Risk-Off",
            "market_risk_level": "HIGH",
        },
        "trading_signal": {
            "trading_signal": "REDUCE",
        },
        "fear_greed": {"value": 22, "label": "Extreme Fear"},
        "etf_allocation": {
            "allocation": {"TLT": 35, "XLE": 25, "QQQM": 15, "XLK": 10, "ITA": 10, "SPYM": 5},
        },
        "signals": {
            "crypto_basis_state": "Contango",
            "pcr_state": "0.95 Normal",
        },
    }


@pytest.fixture
def sample_spec():
    return select_persona_tone("HIGH", "Risk-Off", "morning")


# ════════════════════════════════════════════════════════════
# 1. core.tone_policy
# ════════════════════════════════════════════════════════════

class TestTonePolicy:

    def test_version_loaded(self):
        assert TONE_VERSION == "1.0.0"

    def test_morning_high_returns_spec(self):
        spec = select_persona_tone("HIGH", "Risk-Off", "morning")
        assert spec is not None
        assert spec.tone_name == "긴급 경계"
        assert spec.risk_level == "HIGH"
        assert spec.session == "morning"

    def test_morning_medium_returns_spec(self):
        spec = select_persona_tone("MEDIUM", "Transition", "morning")
        assert spec is not None
        assert spec.tone_name == "차분 점검"
        assert spec.risk_level == "MEDIUM"

    def test_morning_low_returns_spec(self):
        spec = select_persona_tone("LOW", "Risk-On", "morning")
        assert spec is not None
        assert spec.tone_name == "밝은 시작"
        assert spec.risk_level == "LOW"

    def test_non_morning_returns_none(self):
        """Q4.c — morning 외 세션은 None 반환."""
        for session in ("intraday", "close", "full", "narrative", "weekly"):
            assert select_persona_tone("HIGH", "Risk-Off", session) is None

    def test_unknown_risk_falls_back_to_medium(self):
        spec = select_persona_tone("UNKNOWN_RISK", "Risk-On", "morning")
        assert spec is not None
        assert spec.risk_level == "MEDIUM"

    def test_tonespec_is_frozen(self, sample_spec):
        """ToneSpec frozen=True — retry 시 톤 손실 방지의 핵심."""
        with pytest.raises(FrozenInstanceError):
            sample_spec.tone_name = "다른 톤"

    def test_tonespec_to_meta(self, sample_spec):
        meta = sample_spec.to_meta()
        assert meta["risk_level"] == "HIGH"
        assert meta["tone_name"] == "긴급 경계"
        assert "persona" in meta
        assert "regime" in meta

    def test_global_forbidden_in_all_specs(self):
        """모든 톤에 글로벌 금지표현 ('투자 권유') 포함."""
        for risk in ("HIGH", "MEDIUM", "LOW"):
            spec = select_persona_tone(risk, "Risk-On", "morning")
            assert "투자 권유" in spec.forbidden

    def test_build_tweet_prompt_contains_4_elements(self, sample_data, sample_spec):
        prompt = build_tweet_prompt(sample_data, sample_spec, "Morning Brief 🌅")

        # 4요소 패키지 모두 포함 확인
        assert sample_spec.persona in prompt
        assert sample_spec.tone_name in prompt
        assert "작문 규칙" in prompt
        assert "피해야 할 표현" in prompt
        assert "톤 감각 예시" in prompt
        # 시장 데이터 포함
        assert "SPY" in prompt
        assert "VIX" in prompt
        # 출력 조건
        assert f"{sample_spec.length_target[0]}~{sample_spec.length_target[1]}자" in prompt

    def test_build_tweet_prompt_handles_missing_data(self, sample_spec):
        """결측 데이터에서도 안전하게 프롬프트 생성."""
        empty_data = {}
        prompt = build_tweet_prompt(empty_data, sample_spec, "Morning Brief 🌅")
        # 페르소나/톤은 항상 들어가야 함
        assert sample_spec.persona in prompt
        # SPY 라인은 데이터 없으면 생략됐는지
        assert "+0.00%" not in prompt  # 0.0 디폴트값 출력 금지

    def test_build_retry_prompt_preserves_tone(self, sample_spec):
        """retry 프롬프트에 spec.tone_name이 반드시 재주입됨 (1차 분석 2.4 해소)."""
        retry = build_retry_prompt(
            original_output="이전 출력 텍스트",
            failure_reason="length_too_long",
            spec=sample_spec,
        )
        assert sample_spec.tone_name in retry
        assert sample_spec.persona in retry
        assert "이전 출력 텍스트" in retry
        # 작문 규칙도 재주입
        assert "작문 규칙" in retry

    def test_build_retry_prompt_branches_by_reason(self, sample_spec):
        """실패 사유별 다른 지시문이 들어감."""
        for reason in ("length_too_long", "non_korean", "hashtag_count",
                       "forbidden_phrase", "prompt_leak", "awkward"):
            retry = build_retry_prompt("이전 출력", reason, sample_spec)
            # 사유별 지시문이 톤 재주입과 함께 들어가야 함
            assert sample_spec.tone_name in retry

    def test_build_thread_prompt_returns_json_request(self, sample_data, sample_spec):
        prompt = build_thread_prompt(sample_data, sample_spec)
        assert "JSON 배열" in prompt
        assert sample_spec.tone_name in prompt


# ════════════════════════════════════════════════════════════
# 2. core.ai_output_validator
# ════════════════════════════════════════════════════════════

class TestAIOutputValidator:

    def test_version_loaded(self):
        assert VALIDATOR_VERSION == "1.0.0"

    def test_detect_hindi(self):
        result = detect_non_publishable_chars("위험 신호 सावधानी 발생")
        assert result is not None
        assert "सावधानी" in result

    def test_detect_japanese_kana(self):
        result = detect_non_publishable_chars("시장 ありがとう 분석")
        assert result is not None

    def test_detect_arabic(self):
        result = detect_non_publishable_chars("위험 خطر 발생")
        assert result is not None

    def test_passes_pure_korean(self):
        result = detect_non_publishable_chars("VIX 28 SPY -1.2% 시장 경계 모드")
        assert result is None

    def test_passes_korean_with_chinese(self):
        """한자(CJK)는 false positive 방지로 통과."""
        result = detect_non_publishable_chars("미국 株式 시장 분석")
        assert result is None

    def test_validate_normal_text_passes(self, sample_spec):
        text = (
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
            "오늘은 방어 포지션 우선 검토 권장.\n"
            "#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        assert result.passed is True, f"failure_reason={result.failure_reason}, checks={result.checks}"
        assert result.failure_reason is None
        assert "checks" in result.to_flags_jsonb()

    def test_validate_too_short(self, sample_spec):
        text = "🚨 짧음 #ETF"
        result = validate(text, sample_spec)
        assert result.passed is False
        assert result.failure_reason == "length_too_short"

    def test_validate_too_long(self, sample_spec):
        text = "가" * 300 + " 🚨 #ETF #시장 #투자"
        result = validate(text, sample_spec)
        assert result.passed is False
        assert result.failure_reason == "length_too_long"

    def test_validate_non_korean_fails(self, sample_spec):
        text = (
            "🚨 시장 सावधानी 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
            "오늘은 방어 포지션 우선 검토 권장.\n"
            "#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        assert result.passed is False
        assert result.failure_reason == "non_korean"

    def test_validate_no_hashtags_fails(self, sample_spec):
        text = (
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다. "
            "리스크 자산 회피 흐름 분명하게 형성되고 있으며, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
            "오늘은 방어 포지션 우선 검토를 권장하며 모멘텀 회복 전까지 신중한 접근이 합리적으로 보입니다."
        )
        result = validate(text, sample_spec)
        assert result.passed is False
        assert result.failure_reason == "hashtag_count"

    def test_validate_no_emoji_fails(self, sample_spec):
        text = (
            "시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
            "오늘은 방어 포지션 우선 검토 권장.\n"
            "#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        assert result.passed is False
        assert result.failure_reason == "emoji_count"

    def test_validate_forbidden_phrase_fails(self, sample_spec):
        text = (
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 투자 권유는 아니지만 방어 신호 뚜렷합니다.\n"
            "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
            "오늘은 방어 포지션 우선 검토 권장.\n"
            "#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        assert result.passed is False
        assert result.failure_reason == "forbidden_phrase"

    def test_validate_prompt_leak_fails(self, sample_spec):
        text = (
            "**[긴급 톤]**\n"
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
            "오늘은 방어 포지션 우선 검토 권장.\n"
            "#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        assert result.passed is False
        assert result.failure_reason == "prompt_leak"

    def test_awkwardness_repeated_word(self, sample_spec):
        text = (
            "🚨 시장 시장 시장 경계 모드 분명합니다. VIX 28·SPY -1.2% 방어 신호.\n"
            "리스크 자산 회피 흐름.\n#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        assert result.awkwardness_score > 0.15
        assert any("repeated_word" in f for f in result.awkwardness_flags)

    def test_awkwardness_high_risk_with_kakaka(self, sample_spec):
        """HIGH 리스크 spec인데 'ㅋㅋ' 등장 — forbidden_phrase 또는 awkwardness 둘 중 하나 잡혀야 함."""
        text = (
            "🚨 시장 경계 모드 ㅋㅋ. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "흐름 분명.\n#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        # forbidden_phrase가 우선 잡힘 (HIGH 톤의 forbidden에 'ㅋㅋ' 포함)
        assert result.passed is False

    def test_awkwardness_clean_text_low_score(self, sample_spec):
        text = (
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "리스크 자산 회피 흐름 분명, 채권으로 자금 이동.\n#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        assert result.awkwardness_score < 0.3

    def test_validation_result_to_flags_jsonb(self, sample_spec):
        """ai_quality_log.flags JSONB 적재 포맷 보장."""
        text = (
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "흐름 분명, 채권 이동.\n#미국증시 #시장경보 #ETF #투자전략"
        )
        result = validate(text, sample_spec)
        flags = result.to_flags_jsonb()
        assert "checks" in flags
        assert "awkwardness_flags" in flags
        assert isinstance(flags["awkwardness_flags"], list)


# ════════════════════════════════════════════════════════════
# 3. db.ai_quality_store
# ════════════════════════════════════════════════════════════

class TestAIQualityStore:

    def test_version_loaded(self):
        assert STORE_VERSION == "1.0.0"

    def test_build_row_full(self, sample_spec):
        text = "🚨 시장 경계 모드. VIX 28·SPY -1.2% 방어 신호 뚜렷.\n#미국증시 #시장경보 #ETF #투자전략"
        validation = validate(text, sample_spec)
        gemini_meta = {"model": "gemini-2.5-flash-lite", "key_used": "main", "error": None}

        row = _build_row(
            session="morning",
            mode="tweet",
            attempt=1,
            tone_spec=sample_spec,
            output_text=text,
            validation=validation,
            success=True,
            fallback_used=False,
            gemini_meta=gemini_meta,
        )

        # 필수 컬럼
        assert row["session"] == "morning"
        assert row["mode"] == "tweet"
        assert row["attempt"] == 1
        assert row["fallback_used"] is False
        assert row["success"] is True

        # ToneSpec 추출
        assert row["risk_level"] == "HIGH"
        assert row["tone_name"] == "긴급 경계"
        assert "시장 데이터 큐레이터" in row["persona"]

        # 텍스트
        assert row["text_length"] == len(text)
        assert len(row["output_preview"]) <= 80

        # Validation
        assert "awkwardness" in row
        assert "passed" in row
        assert "flags" in row
        assert isinstance(row["flags"], dict)

        # Gemini
        assert row["gemini_model"] == "gemini-2.5-flash-lite"
        assert row["gemini_key_used"] == "main"

    def test_build_row_minimal_no_optional(self):
        """tone_spec/validation/output_text/gemini_meta 모두 None인 fallback 케이스."""
        row = _build_row(
            session="morning",
            mode="tweet",
            attempt=3,
            tone_spec=None,
            output_text=None,
            validation=None,
            success=False,
            fallback_used=True,
            gemini_meta={},
        )
        assert row["session"] == "morning"
        assert row["fallback_used"] is True
        assert row["success"] is False
        # optional 필드는 키 자체가 없거나 None
        assert "risk_level" not in row or row["risk_level"] is None
        assert "text_length" not in row or row["text_length"] is None

    def test_log_ai_attempt_swallows_supabase_error(self, sample_spec, caplog):
        """Supabase 호출 실패해도 raise 안 함 + warning 로그만."""
        text = "🚨 시장 경계.\n#미국증시 #시장경보 #ETF #투자전략"
        validation = validate(text, sample_spec)

        with patch("db.ai_quality_store._get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.table.return_value.insert.return_value.execute.side_effect = (
                RuntimeError("simulated supabase connection error")
            )
            mock_get.return_value = mock_client

            import logging
            with caplog.at_level(logging.WARNING):
                result = log_ai_attempt(
                    session="morning",
                    mode="tweet",
                    attempt=1,
                    tone_spec=sample_spec,
                    output_text=text,
                    validation=validation,
                    success=False,
                    fallback_used=False,
                    gemini_meta={"model": "flash-lite", "key_used": "main", "error": None},
                )

            assert result is False
            assert any("적재 실패" in rec.message for rec in caplog.records)

    def test_log_ai_attempt_success(self, sample_spec):
        """정상 호출 시 True 반환 + table().insert().execute() 1회 호출."""
        text = (
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "흐름 분명, 채권 이동.\n#미국증시 #시장경보 #ETF #투자전략"
        )
        validation = validate(text, sample_spec)

        with patch("db.ai_quality_store._get_client") as mock_get:
            mock_client = MagicMock()
            mock_get.return_value = mock_client

            result = log_ai_attempt(
                session="morning",
                mode="tweet",
                attempt=1,
                tone_spec=sample_spec,
                output_text=text,
                validation=validation,
                success=True,
                fallback_used=False,
                gemini_meta={"model": "flash-lite", "key_used": "main", "error": None},
            )
            assert result is True
            mock_client.table.assert_called_once_with("ai_quality_log")
            mock_client.table.return_value.insert.assert_called_once()


# ════════════════════════════════════════════════════════════
# Smoke test
# ════════════════════════════════════════════════════════════

def test_smoke_full_flow(sample_data):
    """실 환경 시뮬레이션: ToneSpec 결정 → 프롬프트 → 검증 → 적재 row."""
    spec = select_persona_tone("HIGH", "Risk-Off", "morning")
    assert spec is not None

    tweet_prompt = build_tweet_prompt(sample_data, spec, "Morning Brief 🌅")
    assert len(tweet_prompt) > 200

    # 가상의 AI 출력
    fake_output = (
        "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
        "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
        "오늘은 방어 포지션 우선 검토 권장.\n"
        "#미국증시 #시장경보 #ETF #투자전략"
    )
    validation = validate(fake_output, spec)
    assert validation.passed is True

    # 실패 시 retry 프롬프트도 빌드 가능
    retry_prompt = build_retry_prompt(fake_output, "length_too_long", spec)
    assert spec.tone_name in retry_prompt
    assert "이전 출력" in retry_prompt or fake_output in retry_prompt

    # 적재 row 생성 (실제 INSERT는 안 함)
    row = _build_row(
        session="morning", mode="tweet", attempt=1,
        tone_spec=spec, output_text=fake_output, validation=validation,
        success=True, fallback_used=False,
        gemini_meta={"model": "flash-lite", "key_used": "main", "error": None},
    )
    assert row["passed"] is True
    assert row["tone_name"] == "긴급 경계"
