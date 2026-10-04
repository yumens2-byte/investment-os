"""F1 (2026-10-05): Fear & Greed 표기 정정 테스트.

픽스처: tests/fixtures/core_data_morning_20261005.json
  = 운영 morning run 37231539740 (KST 2026-10-05 05:20) 산출물 core_data.json 원본.
  fear_greed = alternative.me 65 Greed (코인), cnn_fg = 31.2 fear (주식).
"""
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import tone_policy
from publishers import dashboard_html_builder as hb
from publishers import fg_display as fgd
from publishers.fb_formatter import to_facebook_text
from publishers.telegram_publisher import format_free_signal
from publishers.x_formatter import format_image_tweet

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "core_data_morning_20261005.json"


@pytest.fixture
def data():
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["data"])


# ── 헬퍼 ─────────────────────────────────────────────────────
def test_fixture_is_the_divergent_case(data):
    assert data["fear_greed"]["source"] == "alternative.me" and data["fear_greed"]["value"] == 65
    assert data["cnn_fg"]["rating"] == "fear" and data["cnn_fg"]["value"] == 31.2


def test_stock_and_crypto_split(data):
    assert fgd.stock_fg(data) == {"value": 31, "label": "Fear", "emoji": "😨"}
    assert fgd.crypto_fg(data) == {"value": 65, "label": "Greed", "emoji": "😊", "change": -2}
    assert fgd.legacy_fg(data) is None


def test_cnn_fallback_source_is_stock(data):
    data["cnn_fg"] = {"success": False, "value": None}
    data["fear_greed"] = {"value": 40, "label": "Fear", "source": "cnn", "change": 1}
    assert fgd.stock_fg(data)["value"] == 40
    assert fgd.crypto_fg(data) is None


def test_extreme_rating_title_case(data):
    data["cnn_fg"]["rating"] = "extreme greed"
    assert fgd.stock_fg(data)["label"] == "Extreme Greed"
    assert fgd.stock_fg(data)["emoji"] == "🤑"


def test_legacy_without_source_keeps_old_wording():
    d = {"fear_greed": {"value": 18, "label": "Extreme Fear", "emoji": "😱", "change": -4}}
    assert fgd.prompt_lines(d) == ["- F&G: 18 (Extreme Fear)"]
    assert fgd.compact_line(d) == "😱 F&G: 18/100 Extreme Fear (-4)"


def test_nothing_available():
    assert fgd.prompt_lines({}) == []
    assert fgd.compact_line({}) == ""
    assert fgd.panel_title({}) == "Fear &amp; Greed"


# ── 발행물 ───────────────────────────────────────────────────
def test_tg_and_fb_morning_label_sources(data):
    tg = format_free_signal(data, session="morning")
    assert "주식 심리(CNN F&G): <b>31/100 Fear</b>" in tg
    assert "코인 심리(Crypto F&G): <b>65/100 Greed</b> (▼2pt)" in tg
    assert "시장심리: <b>65" not in tg            # 코인 지수를 무표기 '시장심리'로 노출 금지
    fb = to_facebook_text(tg)
    assert "주식 심리(CNN F&G): 31/100 Fear" in fb and "<b>" not in fb


def test_tg_morning_legacy_line_unchanged():
    sample = {
        "market_regime": {"market_regime": "Risk-Off", "market_risk_level": "HIGH"},
        "trading_signal": {"trading_signal": "REDUCE", "signal_reason": "High risk",
                           "signal_matrix": {"buy_watch": [], "hold": [], "reduce": []}},
        "market_snapshot": {"vix": 31.0, "sp500": -1.5, "us10y": 4.4, "oil": 99.0},
        "output_helpers": {"one_line_summary": "Risk-Off", "top_headlines": []},
        "fear_greed": {"value": 18, "label": "Extreme Fear", "emoji": "😱", "change": -4},
    }
    tg = format_free_signal(sample, session="morning")
    assert "😱 시장심리: <b>18/100 Extreme Fear</b> (▼4pt)" in tg


def test_x_image_tweet_line(data):
    txt = format_image_tweet(data, "morning")
    assert "😨 주식 F&G 31 Fear · 코인 F&G 65 Greed (-2)" in txt
    assert "F&G: 65" not in txt


def test_tone_prompt_lines(data):
    spec = tone_policy.select_persona_tone("MEDIUM", "Transition", "morning")
    prompt = tone_policy.build_tweet_prompt(data, spec, "Morning Brief 🌅")
    assert "- 주식 F&G(CNN): 31 (Fear)" in prompt
    assert "- 코인 F&G(alternative.me, 주식 지표 아님): 65 (Greed)" in prompt
    assert "- F&G: 65" not in prompt


def test_tone_prompt_no_fabricated_default(data):
    data.pop("fear_greed")
    data.pop("cnn_fg")
    spec = tone_policy.select_persona_tone("MEDIUM", "Transition", "morning")
    prompt = tone_policy.build_tweet_prompt(data, spec, "Morning Brief 🌅")
    assert "F&G" not in prompt                     # 기존: 기본값 50 기재


@pytest.mark.parametrize("builder", ["compact", "full"])
def test_dashboard_panel_title(data, builder):
    dt = datetime(2026, 10, 4, 20, 20, tzinfo=timezone.utc)
    html = (hb._build_compact_html(data, dt, "morning") if builder == "compact"
            else hb._build_full_html(data, dt))
    assert "Crypto Fear &amp; Greed" in html
    assert '<div class="sl">Fear & Greed</div>' not in html
