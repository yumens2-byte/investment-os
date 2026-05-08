"""
tests/test_x_formatter_v150.py
================================
D-4 통합 테스트 — publishers/x_formatter.py v1.5.0.

검증 항목:
  - _label_to_session 매핑
  - _clean_tweet 확장 (마크다운/메타라벨/끝부분 회복)
  - _recover_incomplete_trailing
  - generate_ai_tweet morning vs non-morning 분기 (Q4.c)
  - v1.5.0 신규 흐름: tone_policy + validator + ai_quality_store 연동
  - 모든 retry 실패 시 format_market_snapshot_tweet fallback
  - import 실패 시 v1.4.0 fallback (방어 로직)

실행: pytest tests/test_x_formatter_v150.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publishers.x_formatter import (
    VERSION as XF_VERSION,
    _label_to_session,
    _clean_tweet,
    _recover_incomplete_trailing,
    generate_ai_tweet,
    format_market_snapshot_tweet,
    _select_tone,
    _detect_non_publishable_chars,
)


# ════════════════════════════════════════════════════════════
# Fixture
# ════════════════════════════════════════════════════════════

@pytest.fixture
def sample_data_high():
    """HIGH 리스크 morning 시나리오."""
    return {
        "market_snapshot": {
            "sp500": -1.25, "vix": 28.5, "oil": 78.3,
            "us10y": 4.42, "nasdaq": -1.5, "dollar_index": 102.0,
        },
        "market_regime": {
            "market_regime": "Risk-Off",
            "market_risk_level": "HIGH",
            "regime_reason": "VIX 급등",
        },
        "trading_signal": {
            "trading_signal": "REDUCE",
            "signal_matrix": {"buy_watch": ["TLT"], "hold": [], "reduce": ["QQQM"]},
        },
        "fear_greed": {"value": 22, "label": "Extreme Fear", "emoji": "😱", "change": -8},
        "etf_allocation": {
            "allocation": {"TLT": 35, "XLE": 25, "QQQM": 15, "XLK": 10, "ITA": 10, "SPYM": 5},
        },
        "etf_analysis": {"etf_rank": {"TLT": 1, "XLE": 2, "QQQM": 3}},
        "signals": {
            "crypto_basis_state": "Contango",
            "pcr_state": "0.95 Normal",
            "btc_social_sentiment": 35.0,
            "pcr_value": 0.95,
        },
        "output_helpers": {"one_line_summary": "Risk-Off 방어 모드"},
    }


@pytest.fixture
def fake_ai_output_valid():
    """검증 통과 가능한 AI 출력 (HIGH/morning 톤)."""
    return (
        "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
        "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴이 명확히 관찰됩니다. "
        "오늘은 방어 포지션 우선 검토 권장.\n"
        "#미국증시 #시장경보 #ETF #투자전략"
    )


# ════════════════════════════════════════════════════════════
# 1. 모듈 메타
# ════════════════════════════════════════════════════════════

def test_version_loaded():
    assert XF_VERSION == "1.5.0"


# ════════════════════════════════════════════════════════════
# 2. _label_to_session — 세션 매핑
# ════════════════════════════════════════════════════════════

class TestLabelToSession:

    def test_morning_label(self):
        assert _label_to_session("Morning Brief 🌅") == "morning"
        assert _label_to_session("Morning Brief") == "morning"

    def test_intraday_label(self):
        assert _label_to_session("Intraday Update 📡") == "intraday"

    def test_close_label(self):
        assert _label_to_session("Close Summary 🔔") == "close"

    def test_full_label(self):
        assert _label_to_session("Full Brief 📊") == "full"

    def test_unknown_label(self):
        assert _label_to_session("Market Snapshot") == "unknown"
        assert _label_to_session("") == "unknown"
        assert _label_to_session(None) == "unknown"


# ════════════════════════════════════════════════════════════
# 3. _clean_tweet — 후처리 확장 (1차 분석 2.5 해소 검증)
# ════════════════════════════════════════════════════════════

class TestCleanTweet:

    def test_quotes_removed(self):
        assert _clean_tweet('"트윗 본문"') == "트윗 본문"
        assert _clean_tweet("'트윗 본문'") == "트윗 본문"
        assert _clean_tweet("`트윗 본문`") == "트윗 본문"

    def test_code_block_removed(self):
        text = "```\n실제 트윗 본문\n```"
        result = _clean_tweet(text)
        assert "```" not in result
        assert "실제 트윗 본문" in result

    def test_tone_label_first_line_removed(self):
        text = "**[긴급 톤]**\n실제 트윗 본문이 여기 시작."
        result = _clean_tweet(text)
        assert "긴급 톤" not in result
        assert "실제 트윗 본문" in result

    def test_meta_prefix_removed(self):
        for prefix in ("결론:", "요약:", "트윗:", "본문:", "내용:", "출력:", "답변:"):
            text = f"{prefix} 실제 트윗 본문"
            result = _clean_tweet(text)
            assert prefix not in result, f"prefix '{prefix}' 남아있음"
            assert "실제 트윗 본문" in result

    def test_막_prefix_removed(self):
        """v1.4.0 호환 — '막:' 잔여 prefix"""
        text = "막: 실제 트윗 본문"
        result = _clean_tweet(text)
        assert not result.startswith("막:")

    def test_markdown_header_first_line_removed(self):
        text = "## 시장 분석\n실제 트윗 본문이 여기 시작."
        result = _clean_tweet(text)
        assert not result.startswith("##")
        assert "실제 트윗 본문" in result

    def test_bold_markdown_inline_removed(self):
        text = "🚨 **VIX 급등** 신호. 시장 경계."
        result = _clean_tweet(text)
        assert "**" not in result
        assert "VIX 급등" in result

    def test_inline_code_removed(self):
        text = "🚨 `VIX` 급등 신호. 시장 경계."
        result = _clean_tweet(text)
        assert "`" not in result
        assert "VIX 급등" in result

    def test_multiple_meta_lines_removed(self):
        """첫 줄 메타 라벨 여러 개 누설 시도 (반복 처리 검증)."""
        text = "**[긴급 톤]**\n결론: 실제 트윗 본문이 여기 시작."
        result = _clean_tweet(text)
        assert "긴급 톤" not in result
        assert "결론:" not in result
        assert "실제 트윗 본문" in result

    def test_empty_input(self):
        assert _clean_tweet("") == ""
        assert _clean_tweet("   ") == ""


# ════════════════════════════════════════════════════════════
# 4. _recover_incomplete_trailing — 끝부분 회복
# ════════════════════════════════════════════════════════════

class TestRecoverIncompleteTrailing:

    def test_complete_text_preserved(self):
        text = (
            "🚨 시장 경계 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다."
            "\n#미국증시 #시장경보 #ETF #투자전략"
        )
        assert _recover_incomplete_trailing(text) == text

    def test_short_text_preserved(self):
        """50자 미만은 회복 시도 안 함."""
        text = "짧은 트윗을"  # '를'로 끝나지만 짧음
        assert _recover_incomplete_trailing(text) == text

    def test_too_aggressive_truncation_avoided(self):
        """회복 후 70% 미만이 되면 원문 유지."""
        text = "🚨 시장 경계 모드. 매우 길고 긴 한국어 본문이 이어지다가 마지막에 갑자기 채권으로"
        # 마지막이 '으로'로 끝남 → 회복 시도. 하지만 마침표가 1개라 직전 문장 너무 짧음.
        result = _recover_incomplete_trailing(text)
        # 70% 보존 룰에 따라 원문 또는 회복본 중 더 긴 쪽
        assert len(result) >= len(text) * 0.7


# ════════════════════════════════════════════════════════════
# 5. _detect_non_publishable_chars (백워드 호환)
# ════════════════════════════════════════════════════════════

class TestLegacyHelpers:

    def test_detect_non_publishable_chars_legacy_preserved(self):
        """v1.4.0 함수 보존 — non-morning 분기에서 사용."""
        assert _detect_non_publishable_chars("위험 सावधानी 발생") is not None
        assert _detect_non_publishable_chars("VIX 28 SPY -1.2%") is None

    def test_select_tone_legacy_preserved(self):
        """v1.4.0 _select_tone 보존."""
        tone = _select_tone("HIGH", "Risk-Off")
        assert tone in ("긴급", "경계")

        tone = _select_tone("MEDIUM", "Transition")
        assert tone in ("진지한", "신중한", "분석적")

        tone = _select_tone("LOW", "Risk-On")
        assert tone in ("낙관적", "여유로운", "유머러스")


# ════════════════════════════════════════════════════════════
# 6. generate_ai_tweet 분기 — Q4.c morning만 신규 흐름
# ════════════════════════════════════════════════════════════

class TestGenerateAITweetRouting:
    """morning vs non-morning 분기를 호출 여부로 검증."""

    def test_non_morning_uses_legacy_v140(self, sample_data_high):
        """intraday 호출은 _generate_ai_tweet_legacy_v140 사용."""
        with patch("publishers.x_formatter._generate_ai_tweet_legacy_v140") as mock_legacy, \
             patch("publishers.x_formatter._generate_ai_tweet_morning_v150") as mock_v150:
            mock_legacy.return_value = "legacy_output"

            result = generate_ai_tweet(sample_data_high, "Intraday Update 📡")

            assert result == "legacy_output"
            mock_legacy.assert_called_once()
            mock_v150.assert_not_called()

    def test_morning_uses_v150(self, sample_data_high):
        """morning 호출은 _generate_ai_tweet_morning_v150 사용."""
        with patch("publishers.x_formatter._generate_ai_tweet_legacy_v140") as mock_legacy, \
             patch("publishers.x_formatter._generate_ai_tweet_morning_v150") as mock_v150:
            mock_v150.return_value = "v150_output"

            result = generate_ai_tweet(sample_data_high, "Morning Brief 🌅")

            assert result == "v150_output"
            mock_v150.assert_called_once()
            mock_legacy.assert_not_called()

    def test_close_full_narrative_use_legacy(self, sample_data_high):
        """close/full/narrative 모두 v1.4.0 흐름."""
        with patch("publishers.x_formatter._generate_ai_tweet_legacy_v140") as mock_legacy, \
             patch("publishers.x_formatter._generate_ai_tweet_morning_v150") as mock_v150:
            mock_legacy.return_value = "legacy"

            for label in ("Close Summary 🔔", "Full Brief 📊", "Weekly Review"):
                generate_ai_tweet(sample_data_high, label)

            assert mock_legacy.call_count == 3
            mock_v150.assert_not_called()


# ════════════════════════════════════════════════════════════
# 7. v1.5.0 신규 흐름 통합 (mock Gemini)
# ════════════════════════════════════════════════════════════

class TestV150MorningFlow:

    def test_gemini_unavailable_falls_back(self, sample_data_high):
        """Gemini 미설정 → fallback + 적재 1건."""
        with patch("core.gemini_gateway.is_available", return_value=False), \
             patch("db.ai_quality_store._get_client") as mock_get_client:

            result = generate_ai_tweet(sample_data_high, "Morning Brief 🌅")
            # fallback 결과는 format_market_snapshot_tweet 출력 (해시태그 포함)
            assert "📊" in result or "Morning" in result
            # 적재 시도 1회 (lazy-init이라 실제 호출까지 trigger 됐는지)
            assert mock_get_client.called

    def test_first_attempt_passes(self, sample_data_high, fake_ai_output_valid):
        """1차 시도에서 검증 통과."""
        gemini_response = {
            "success": True,
            "text": fake_ai_output_valid,
            "data": None,
            "model": "gemini-2.5-flash-lite",
            "key_used": "main",
            "error": None,
        }

        with patch("core.gemini_gateway.is_available", return_value=True), \
             patch("core.gemini_gateway.call", return_value=gemini_response), \
             patch("db.ai_quality_store._get_client") as mock_client:

            result = generate_ai_tweet(sample_data_high, "Morning Brief 🌅")

            assert result == fake_ai_output_valid
            # 적재 1건 (1차 통과)
            assert mock_client.return_value.table.return_value.insert.call_count == 1

    def test_first_fails_second_passes(self, sample_data_high, fake_ai_output_valid):
        """1차 비한국어 실패 → 2차 통과."""
        bad_text = (
            "🚨 시장 सावधानी 모드. VIX 28·SPY -1.2%, 방어 우위 신호 뚜렷합니다.\n"
            "리스크 자산 회피 흐름 분명, 채권으로 자금 이동 패턴 명확히 관찰됩니다. "
            "방어 포지션 우선 검토.\n"
            "#미국증시 #시장경보 #ETF #투자전략"
        )

        responses = [
            {  # 1차: 비한국어 포함
                "success": True, "text": bad_text, "data": None,
                "model": "gemini-2.5-flash-lite", "key_used": "main", "error": None,
            },
            {  # 2차: 정상
                "success": True, "text": fake_ai_output_valid, "data": None,
                "model": "gemini-2.5-flash-lite", "key_used": "main", "error": None,
            },
        ]

        with patch("core.gemini_gateway.is_available", return_value=True), \
             patch("core.gemini_gateway.call", side_effect=responses), \
             patch("db.ai_quality_store._get_client") as mock_client:

            result = generate_ai_tweet(sample_data_high, "Morning Brief 🌅")

            assert result == fake_ai_output_valid
            # 적재 2건 (1차 실패 + 2차 통과)
            assert mock_client.return_value.table.return_value.insert.call_count == 2

    def test_all_attempts_fail_falls_back(self, sample_data_high):
        """3회 모두 실패 → format_market_snapshot_tweet fallback + 적재 4건(3시도+fallback로그)."""
        bad_response = {
            "success": False, "text": "", "data": None,
            "model": "gemini-2.5-flash-lite", "key_used": "main", "error": "rate_limit",
        }

        with patch("core.gemini_gateway.is_available", return_value=True), \
             patch("core.gemini_gateway.call", return_value=bad_response), \
             patch("db.ai_quality_store._get_client") as mock_client:

            result = generate_ai_tweet(sample_data_high, "Morning Brief 🌅")

            # fallback 텍스트 (format_market_snapshot_tweet 출력)
            assert isinstance(result, str)
            assert len(result) > 0
            # 적재 횟수: 3시도 + 1 fallback = 4
            assert mock_client.return_value.table.return_value.insert.call_count == 4

    def test_supabase_failure_does_not_block_publishing(self, sample_data_high, fake_ai_output_valid):
        """Supabase 적재 실패해도 발행은 정상 진행 (장애 격리 검증)."""
        gemini_response = {
            "success": True, "text": fake_ai_output_valid, "data": None,
            "model": "gemini-2.5-flash-lite", "key_used": "main", "error": None,
        }

        with patch("core.gemini_gateway.is_available", return_value=True), \
             patch("core.gemini_gateway.call", return_value=gemini_response), \
             patch("db.ai_quality_store._get_client") as mock_get_client:

            mock_get_client.return_value.table.return_value.insert.return_value.execute.side_effect = (
                RuntimeError("simulated supabase outage")
            )

            # 적재 실패해도 정상 발행 텍스트 반환
            result = generate_ai_tweet(sample_data_high, "Morning Brief 🌅")
            assert result == fake_ai_output_valid


# ════════════════════════════════════════════════════════════
# 8. format_market_snapshot_tweet — fallback 동작 보장
# ════════════════════════════════════════════════════════════

class TestFallbackFormat:

    def test_fallback_output_is_valid_tweet(self, sample_data_high):
        """fallback 출력이 X 280자 제한 통과."""
        result = format_market_snapshot_tweet(sample_data_high, "Morning Brief 🌅")
        assert isinstance(result, str)
        assert len(result) > 0
        # X_MAX_TWEET_LENGTH=280 + 약간의 마진 (compact 분기 있음)
        assert len(result) <= 280

    def test_fallback_contains_session_label(self, sample_data_high):
        result = format_market_snapshot_tweet(sample_data_high, "Morning Brief 🌅")
        # 세션 라벨이 통째로 들어가지는 않더라도 핵심 요소 포함
        assert "📊" in result or "SPY" in result
