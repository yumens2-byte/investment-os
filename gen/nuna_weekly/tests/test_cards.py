"""cards.py 테스트 — 실제 Noto CJK 폰트로 PNG 렌더."""
from __future__ import annotations

import pytest
from PIL import Image

from gen.nuna_weekly import cards
from gen.nuna_weekly.cards import CardError, render_card


def test_none_kind_returns_none(tmp_path):
    out = tmp_path / "none.png"
    assert render_card("none", "t", ["a"], out) is None
    assert not out.exists()


def test_unknown_kind_raises(tmp_path):
    with pytest.raises(CardError):
        render_card("bogus", "t", [], tmp_path / "x.png")


def test_overlay_card_rgba_lower_panel(tmp_path):
    out = render_card("week", "이번 주 미국 증시", ["S&P500 +1.2%", "나스닥 -0.8%", "VIX 15.3"],
                      tmp_path / "week.png")
    img = Image.open(out)
    assert img.mode == "RGBA" and img.size == (1080, 1920)
    alpha = img.getchannel("A")
    assert alpha.getpixel((540, 100)) == 0          # 상단 투명
    assert alpha.getpixel((540, int(1920 * 0.89))) > 150   # 하단 패널
    # 패널 상단이 하단 ~45% 안에 있어야 함
    bbox = alpha.getbbox()
    assert bbox is not None and bbox[1] >= int(1920 * 0.45)


def test_line_colors():
    assert cards.line_color("S&P500 +1.2%") == cards.GREEN
    assert cards.line_color("나스닥 -0.8%") == cards.RED
    assert cards.line_color("나스닥 −0.8 %") == cards.RED
    assert cards.line_color("VIX 15.3") == cards.WHITE
    assert cards.line_color("금리 4.1%") == cards.WHITE


def test_long_text_fits_width(tmp_path):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    long = "아주 긴 제목 " * 12
    font, rows = cards.fit_text(draw, long, True, cards.TITLE_PX)
    assert len(rows) > 1
    assert all(draw.textlength(r, font=font) <= cards.TEXT_MAX_W for r in rows)
    no_space = "가" * 60
    font2, rows2 = cards.fit_text(draw, no_space, False, cards.LINE_PX)
    assert all(draw.textlength(r, font=font2) <= cards.TEXT_MAX_W for r in rows2)
    out = render_card("risk", long, [no_space] * 6, tmp_path / "long.png")
    assert out.is_file()


def test_disclaimer_opaque(tmp_path):
    out = render_card("disclaimer", "", [], tmp_path / "d.png")
    img = Image.open(out)
    assert img.getchannel("A").getextrema() == (255, 255)
    assert img.getpixel((5, 5))[:3] == cards.NAVY_OPAQUE[:3]
    # 중앙부에 텍스트 픽셀(배경과 다른 색) 존재
    center = img.crop((70, 860, 1010, 1060)).convert("RGB")
    assert len(center.getcolors(maxcolors=100000) or []) > 1


def test_no_font_fail_closed(monkeypatch, tmp_path):
    cards.find_font.cache_clear()
    monkeypatch.setattr(cards, "FONT_CANDIDATES_BOLD", (("/nonexistent/a.ttc", 0),))
    monkeypatch.setattr(cards, "FONT_CANDIDATES_REGULAR", (("/nonexistent/b.ttc", 0),))
    monkeypatch.setattr(cards, "_fc_match", lambda bold: None)
    try:
        with pytest.raises(CardError):
            render_card("week", "제목", ["줄"], tmp_path / "x.png")
    finally:
        cards.find_font.cache_clear()


# --------------------------------------------------------------------------- #
# DN2026_0086 방송 그래픽
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["breaking", "chart", "issue"])
def test_news_kinds_render_with_label_tab(tmp_path, kind):
    from PIL import Image

    chart = [("월", 0.7), ("화", -0.6), ("수", None), ("목", 1.2), ("금", 0.1)] if kind == "chart" else None
    out = render_card(kind, "이번 주 핵심", ["S&P500 +1.58%"], tmp_path / f"{kind}.png", chart=chart)
    with Image.open(out) as im:
        top = im.getchannel("A").getbbox()[1]
        assert top < int(1920 * 0.90) and top > 1920 * 0.40   # 하단 영역 안
        assert im.getpixel((im.width // 2, 300))[3] == 0         # 상단은 투명


def test_bug_ticker_band(tmp_path):
    from PIL import Image

    from gen.nuna_weekly import cards
    from gen.nuna_weekly.models import DayFacts, Facts

    bug = cards.render_bug(tmp_path / "bug.png")
    with Image.open(bug) as im:
        assert im.getpixel((cards.PANEL_MARGIN_X + 5, cards.BUG_Y + 40))[3] == 255
        assert im.getpixel((540, 1500))[3] == 0
    facts = Facts(episode_id="e", source="direct", fetched_at="", week_sp500_chg=1.58, week_nasdaq_chg=2.29,
                  fear_greed=51, regime="Oil Shock",
                  days=[DayFacts("2026-10-26", vix=16.91, wti=101.93, us10y=4.1),
                        DayFacts("2026-10-30", closed=True)])
    items = cards.ticker_items(facts)
    assert items == ["S&P 주간 +1.58%", "나스닥 주간 +2.29%", "VIX 16.91", "WTI 101.93", "미 10년물 4.10%",
                     "공포탐욕 51", "국면 오일쇼크"]
    path, unit = cards.render_ticker(items, tmp_path / "t.png")
    with Image.open(path) as im:
        assert im.height == cards.TICKER_H and im.width >= 2 * 1080 and unit > 0
    assert cards.render_ticker([], tmp_path / "none.png") is None
    assert cards.ticker_items(None) == []
    band = cards.render_disclaimer_band(tmp_path / "band.png")
    with Image.open(band) as im:
        assert im.getpixel((540, 200))[3] == 0 and im.getchannel("A").getbbox()[3] <= int(1920 * 0.90) + 1
