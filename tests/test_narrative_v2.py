"""
tests/test_narrative_v2.py
===========================
narrative 고도화(N-1 ~ N-3, R-1) 회귀 테스트.

검증 항목:
  1. narrative_engine v2.0.0  — 헤더 풀 랜덤성 / 포스트 분할 / 빈 입력
  2. narrative_visual v1.0.0  — 직전 2회 제외 / 순차 폴백 / 전 후보 실패
  3. dashboard_html_builder   — variant=None 회귀 가드
  4. duplicate_checker        — skip_regime_hash 동작
  5. x_formatter              — narrative 해시태그 풀

실행: pytest tests/test_narrative_v2.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines.narrative_engine import (  # noqa: E402
    VERSION as NARR_VERSION,
    _HEADER_POOL,
    _LINE_TARGET_POOL,
    _SINGLE_POST_MAX,
    _STYLE_POOL,
    _split_body,
    build_narrative_posts,
)
from publishers import narrative_visual  # noqa: E402
from publishers.narrative_visual import (  # noqa: E402
    VISUAL_WEIGHTS,
    VERSION as VISUAL_VERSION,
    _pick_order,
)


# ════════════════════════════════════════════════════════════
# 픽스처
# ════════════════════════════════════════════════════════════

@pytest.fixture
def sample_data() -> dict:
    return {
        "market_snapshot": {
            "sp500": -1.24, "nasdaq": -1.8, "vix": 22.4,
            "us10y": 4.31, "oil": 78.2, "dollar_index": 104.1,
        },
        "market_regime": {
            "market_regime": "Risk-Off",
            "market_risk_level": "HIGH",
            "regime_reason": "VIX 급등 + 신용 스프레드 확대",
        },
        "market_score": {
            "growth_score": 3, "inflation_score": 2, "liquidity_score": 4,
            "risk_score": 4, "financial_stability_score": 3,
            "commodity_pressure_score": 2,
        },
        "signals": {"volatility_score": 4.2, "vix_state": "Elevated"},
        "trading_signal": {
            "trading_signal": "REDUCE",
            "signal_matrix": {"buy_watch": ["TLT"], "reduce": ["QQQ"]},
        },
        "etf_allocation": {"allocation": {"TLT": 30, "SPY": 25, "QQQ": 15}},
        "etf_analysis": {"etf_rank": {"TLT": 1, "SPY": 2, "QQQ": 6}},
        "output_helpers": {"one_line_summary": "Risk-Off — 방어 조건. TLT, SPY 집중."},
    }


# ════════════════════════════════════════════════════════════
# 1. narrative_engine — 헤더 풀 / 변칙화
# ════════════════════════════════════════════════════════════

class TestNarrativeVariation:

    def test_version_is_v2(self):
        assert NARR_VERSION.startswith("2.")

    def test_header_pool_has_empty_entries(self):
        """무헤더 옵션이 존재해야 '항상 제목으로 시작' 패턴이 깨진다."""
        assert "" in _HEADER_POOL
        assert len(_HEADER_POOL) >= 8

    def test_header_pool_has_no_legacy_fixed_header(self):
        """v1.1.0 고정 헤더가 풀의 유일값으로 남아있으면 안 된다."""
        assert len(set(_HEADER_POOL)) >= 6

    def test_style_and_line_pools_non_trivial(self):
        assert set(_STYLE_POOL) == {"prose", "bullet", "lead"}
        assert len(set(_LINE_TARGET_POOL)) >= 3

    def test_header_randomness_across_calls(self, sample_data):
        """100회 호출 시 최소 2종 이상의 서로 다른 결과가 나와야 한다."""
        with patch(
            "engines.narrative_engine._build_hashtags", return_value="#미국증시"
        ):
            outs = {
                build_narrative_posts(sample_data, "짧은 해설 한 줄입니다.")[0]
                for _ in range(100)
            }
        assert len(outs) >= 2


# ════════════════════════════════════════════════════════════
# 2. narrative_engine — 포스트 구성
# ════════════════════════════════════════════════════════════

class TestBuildNarrativePosts:

    def test_empty_narrative_returns_empty_list(self, sample_data):
        assert build_narrative_posts(sample_data, "") == []
        assert build_narrative_posts(sample_data, "   \n  ") == []

    def test_short_body_single_post(self, sample_data):
        with patch(
            "engines.narrative_engine._build_hashtags", return_value="#미국증시"
        ):
            posts = build_narrative_posts(sample_data, "한 줄짜리 짧은 해설.")
        assert len(posts) == 1

    def test_long_body_splits_into_two_posts(self, sample_data):
        body = "\n".join(f"{i}번째 줄입니다. " + "가" * 60 for i in range(6))
        assert len(body) > _SINGLE_POST_MAX
        with patch(
            "engines.narrative_engine._build_hashtags", return_value="#미국증시"
        ):
            posts = build_narrative_posts(sample_data, body)
        assert len(posts) == 2
        assert all(p.strip() for p in posts)

    def test_hashtags_appear_only_in_first_post(self, sample_data):
        body = "\n".join(f"{i}번째 줄. " + "나" * 60 for i in range(6))
        with patch(
            "engines.narrative_engine._build_hashtags", return_value="#고유태그XYZ"
        ):
            posts = build_narrative_posts(sample_data, body)
        assert "#고유태그XYZ" in posts[0]
        if len(posts) > 1:
            assert "#고유태그XYZ" not in posts[1]

    def test_split_body_single_line_not_split(self):
        lead, rest = _split_body("한 줄만 있는 본문")
        assert lead == ""
        assert rest == "한 줄만 있는 본문"

    def test_hashtag_failure_falls_back(self, sample_data):
        """x_formatter가 예외를 던져도 기본 태그로 폴백하고 포스트는 생성된다."""
        from engines import narrative_engine as ne

        with patch(
            "publishers.x_formatter.build_hashtags",
            side_effect=RuntimeError("boom"),
        ):
            tags = ne._build_hashtags(sample_data)
            posts = build_narrative_posts(sample_data, "본문 한 줄.")

        assert tags == "#미국증시 #ETF투자"
        assert len(posts) == 1
        assert "#미국증시" in posts[0]


# ════════════════════════════════════════════════════════════
# 3. narrative_visual — 로테이션
# ════════════════════════════════════════════════════════════

class TestVisualRotation:

    def test_version(self):
        assert VISUAL_VERSION == "1.1.0"

    def test_weights_cover_five_candidates(self):
        assert set(VISUAL_WEIGHTS) == {
            "full_dashboard", "compact_dashboard",
            "vs_card", "card_market", "none",
        }

    def test_recent_two_excluded_from_front(self):
        """직전 2회 사용 variant는 우선 후보에서 빠진다."""
        history = ["full_dashboard", "vs_card"]
        order = _pick_order(history)
        head = order[: len(VISUAL_WEIGHTS) - 2]
        assert "full_dashboard" not in head
        assert "vs_card" not in head

    def test_order_contains_all_candidates(self):
        order = _pick_order(["full_dashboard", "vs_card"])
        assert set(order) == set(VISUAL_WEIGHTS)
        assert len(order) == len(VISUAL_WEIGHTS)

    def test_empty_history_uses_all(self):
        order = _pick_order([])
        assert set(order) == set(VISUAL_WEIGHTS)

    def test_select_visual_falls_through_on_failure(self, sample_data):
        """첫 후보가 None을 반환하면 다음 후보로 넘어간다."""
        calls: list[str] = []

        def _fail(_data):
            calls.append("fail")
            return None

        def _ok(_data):
            calls.append("ok")
            return "/tmp/ok.png"

        with patch.object(
            narrative_visual, "_pick_order",
            return_value=["full_dashboard", "vs_card", "none"],
        ), patch.dict(
            narrative_visual._GENERATORS,
            {"full_dashboard": _fail, "vs_card": _ok},
        ), patch.object(narrative_visual, "_record"), \
            patch.object(narrative_visual, "_load_history", return_value=[]):
            path, variant = narrative_visual.select_visual(sample_data)

        assert path == "/tmp/ok.png"
        assert variant == "vs_card"
        assert calls == ["fail", "ok"]

    def test_select_visual_all_fail_returns_none(self, sample_data):
        def _fail(_data):
            return None

        with patch.object(
            narrative_visual, "_pick_order",
            return_value=["full_dashboard", "vs_card"],
        ), patch.dict(
            narrative_visual._GENERATORS,
            {"full_dashboard": _fail, "vs_card": _fail},
        ), patch.object(narrative_visual, "_record"), \
            patch.object(narrative_visual, "_load_history", return_value=[]):
            path, variant = narrative_visual.select_visual(sample_data)

        assert path is None
        assert variant == "none"

    def test_select_visual_generator_exception_is_swallowed(self, sample_data):
        def _boom(_data):
            raise RuntimeError("render crash")

        def _ok(_data):
            return "/tmp/ok.png"

        with patch.object(
            narrative_visual, "_pick_order",
            return_value=["full_dashboard", "vs_card"],
        ), patch.dict(
            narrative_visual._GENERATORS,
            {"full_dashboard": _boom, "vs_card": _ok},
        ), patch.object(narrative_visual, "_record"), \
            patch.object(narrative_visual, "_load_history", return_value=[]):
            path, variant = narrative_visual.select_visual(sample_data)

        assert variant == "vs_card"
        assert path == "/tmp/ok.png"

    def test_card_market_forces_html(self, sample_data):
        """v1.1.0 — card_market은 Gemini를 건너뛰고 HTML로만 생성해야 한다."""
        called = {}

        def _spy(core_data, card_no=1, force_html=False):
            called["card_no"] = card_no
            called["force_html"] = force_html
            return "/tmp/card1.png"

        with patch("comic.card_news_generator.generate_single_card", _spy):
            path = narrative_visual._make_card_market(sample_data)

        assert path == "/tmp/card1.png"
        assert called["card_no"] == 1
        assert called["force_html"] is True

    def test_generate_single_card_html_skips_gemini(self, sample_data):
        """force_html=True면 Gemini 경로가 호출되지 않아야 한다."""
        from comic import card_news_generator as cng

        gemini_called = []

        with patch.object(
            cng, "_generate_card_via_gemini",
            side_effect=lambda *a, **k: gemini_called.append(1) or "/tmp/gem.png",
        ), patch.object(cng, "_render_html", return_value="/tmp/html.png"):
            path = cng.generate_single_card(sample_data, card_no=1, force_html=True)

        assert path == "/tmp/html.png"
        assert gemini_called == []

    def test_generate_single_card_default_keeps_gemini_first(self, sample_data):
        """force_html 미지정 시 기존 Gemini 우선 동작 유지 (코믹 파이프라인 회귀 가드)."""
        from comic import card_news_generator as cng

        with patch.object(
            cng, "_generate_card_via_gemini", return_value="/tmp/gem.png"
        ), patch.object(cng, "_render_html", return_value="/tmp/html.png"):
            assert cng.generate_single_card(sample_data, card_no=1) == "/tmp/gem.png"

    def test_none_variant_short_circuits(self, sample_data):
        with patch.object(
            narrative_visual, "_pick_order", return_value=["none", "vs_card"],
        ), patch.object(narrative_visual, "_record"), \
            patch.object(narrative_visual, "_load_history", return_value=[]):
            path, variant = narrative_visual.select_visual(sample_data)

        assert path is None
        assert variant == "none"


# ════════════════════════════════════════════════════════════
# 4. duplicate_checker — R-1
# ════════════════════════════════════════════════════════════

class TestDuplicateCheckerSkipRegime:

    def _history(self, content_hash: str, regime_hash: str) -> list[dict]:
        return [{"content_hash": content_hash, "regime_hash": regime_hash}]

    def test_regime_hash_blocks_by_default(self, sample_data):
        from core import duplicate_checker as dc

        rh = dc._compute_regime_hash(sample_data)
        with patch.object(dc, "_load_history", return_value=self._history("x", rh)):
            assert dc.is_duplicate("완전히 새로운 본문", sample_data) is True

    def test_skip_regime_hash_allows(self, sample_data):
        from core import duplicate_checker as dc

        rh = dc._compute_regime_hash(sample_data)
        with patch.object(dc, "_load_history", return_value=self._history("x", rh)):
            assert dc.is_duplicate(
                "완전히 새로운 본문", sample_data, skip_regime_hash=True
            ) is False

    def test_content_hash_still_blocks_when_skipping(self, sample_data):
        from core import duplicate_checker as dc

        text = "동일한 본문입니다."
        ch = dc._compute_content_hash(text)
        with patch.object(dc, "_load_history", return_value=self._history(ch, "zzz")):
            assert dc.is_duplicate(text, sample_data, skip_regime_hash=True) is True


# ════════════════════════════════════════════════════════════
# 5. x_formatter — narrative 해시태그 / 라벨
# ════════════════════════════════════════════════════════════

class TestNarrativeHashtags:

    def test_build_hashtags_is_public(self):
        from publishers.x_formatter import build_hashtags

        tags = build_hashtags(regime="Risk-Off", session="narrative")
        assert tags.startswith("#")

    def test_narrative_tag_pool_is_used(self):
        from publishers.x_formatter import _NARRATIVE_TAG_POOL, build_hashtags

        seen = {
            build_hashtags(regime="Risk-Off", session="narrative")
            for _ in range(200)
        }
        joined = " ".join(seen)
        assert any(tag in joined for tag in _NARRATIVE_TAG_POOL)

    def test_hashtags_vary(self):
        from publishers.x_formatter import build_hashtags

        seen = {
            build_hashtags(regime="Risk-Off", session="narrative")
            for _ in range(50)
        }
        assert len(seen) >= 2

    def test_session_label_has_narrative(self):
        from run_view import _session_label

        assert "Narrative" in _session_label("narrative")

    def test_label_to_session_roundtrip(self):
        from publishers.x_formatter import _label_to_session
        from run_view import _session_label

        assert _label_to_session(_session_label("narrative")) == "narrative"


# ════════════════════════════════════════════════════════════
# 6. dashboard_html_builder — variant 회귀 가드
# ════════════════════════════════════════════════════════════

class TestDashboardVariant:

    def test_variant_none_matches_legacy_dispatch(self, sample_data):
        """variant=None이면 기존 세션 기반 분기와 동일해야 한다."""
        from datetime import datetime, timezone

        from publishers import dashboard_html_builder as dhb

        dt = datetime(2026, 9, 6, 2, 36, tzinfo=timezone.utc)

        # narrative는 기존에 full 레이아웃을 탔다
        legacy = dhb._build_html(sample_data, dt, session="narrative")
        forced = dhb._build_html(sample_data, dt, session="narrative", variant="full")
        assert legacy == forced

        # morning은 기존에 compact 레이아웃을 탔다
        legacy_m = dhb._build_html(sample_data, dt, session="morning")
        forced_m = dhb._build_html(
            sample_data, dt, session="morning", variant="compact"
        )
        assert legacy_m == forced_m

    def test_compact_variant_differs_from_full(self, sample_data):
        from datetime import datetime, timezone

        from publishers import dashboard_html_builder as dhb

        dt = datetime(2026, 9, 6, 2, 36, tzinfo=timezone.utc)
        full = dhb._build_html(sample_data, dt, session="narrative", variant="full")
        compact = dhb._build_html(
            sample_data, dt, session="narrative", variant="compact"
        )
        assert full != compact
