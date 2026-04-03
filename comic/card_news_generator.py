"""
comic/card_news_generator.py (B-21B)
=======================================
카드뉴스 3장 — Full Dashboard 세션 (18:30 KST) 유료 채널 전용

Card 1: 오늘의 시장 (레짐 + 주요 수치)
Card 2: ETF 전략 (6개 ETF 배분 + BUY/REDUCE)
Card 3: AI 뉴스 분석 (B-16 Top3 이슈)

이미지: HTML+Playwright ($0)
크기: 1080×1350 (세로형, 인스타그램 호환)
"""
import logging
import os
import tempfile
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

SCORE_COLORS = {1: "#10b981", 2: "#34d399", 3: "#f59e0b", 4: "#f97316", 5: "#ef4444"}
REGIME_COLORS = {
    "Risk-On": "#10b981", "Risk-Off": "#ef4444", "Oil Shock": "#f59e0b",
    "Transition": "#3b82f6", "Stagflation Risk": "#7c3aed",
}


def generate_cards(core_data: dict) -> list[str]:
    """
    카드뉴스 3장 생성.

    Returns:
        이미지 파일 경로 리스트 [card1_path, card2_path, card3_path]
    """
    if not core_data:
        return []

    paths = []
    generators = [_card1_market, _card2_etf, _card3_news]
    card_labels = ["시장 현황", "ETF 전략", "AI 뉴스 분석"]

    for i, (gen, label) in enumerate(zip(generators, card_labels), 1):
        try:
            # 1순위: Gemini 이미지
            gemini_path = _generate_card_via_gemini(core_data, i, label)
            if gemini_path:
                paths.append(gemini_path)
                continue

            # 2순위: HTML fallback
            logger.info(f"[CardNews] Card {i} Gemini 실패 → HTML fallback")
            html = gen(core_data)
            path = _render_html(html, f"card{i}")
            if path:
                paths.append(path)
        except Exception as e:
            logger.warning(f"[CardNews] Card {i} 생성 실패: {e}")

    logger.info(f"[CardNews] {len(paths)}/3장 생성 완료")
    return paths


def _generate_card_via_gemini(core_data: dict, card_no: int, label: str) -> str | None:
    """Gemini Flash Image로 카드뉴스 이미지 생성"""
    try:
        import base64
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            return None

        import google.generativeai as genai
        genai.configure(api_key=gemini_key)

        regime = core_data.get("market_regime", {}).get("market_regime", "—")
        risk = core_data.get("market_regime", {}).get("market_risk_level", "—")
        snap = core_data.get("market_snapshot", {})
        alloc = core_data.get("etf_allocation", {}).get("allocation", {})
        news = core_data.get("news_analysis", {})

        if card_no == 1:
            prompt = (
                f"Create a sleek 1080x1350 financial infographic card titled '오늘의 시장' (Today's Market). "
                f"Dark gradient background. Professional data visualization style. "
                f"Show: Regime '{regime}', Risk '{risk}', "
                f"SPY {snap.get('sp500', '?')}%, VIX {snap.get('vix', '?')}, "
                f"WTI ${snap.get('oil', '?')}, US10Y {snap.get('us10y', '?')}%. "
                f"Include 6-axis radar chart for market score. "
                f"Bottom: '{date.today()} | Investment Comic | 1/3'. "
                f"Clean Korean text. No real brand logos."
            )
        elif card_no == 2:
            sorted_etfs = sorted(alloc.items(), key=lambda x: -x[1])
            etf_str = ", ".join(f"{e} {p}%" for e, p in sorted_etfs)
            prompt = (
                f"Create a sleek 1080x1350 financial infographic card titled 'ETF 전략' (ETF Strategy). "
                f"Dark gradient background. Show horizontal bar chart of ETF allocation: {etf_str}. "
                f"Green bars for high allocation, red for low. "
                f"Include BUY/REDUCE indicators. "
                f"Bottom: '{date.today()} | Investment Comic | 2/3'. "
                f"Clean Korean text. No real brand logos."
            )
        else:
            issues = news.get("top_issues", [])
            issues_str = ", ".join(i.get("topic", "?") for i in issues[:3])
            sentiment = news.get("overall_sentiment", "neutral")
            prompt = (
                f"Create a sleek 1080x1350 financial infographic card titled 'AI 뉴스 분석' (AI News Analysis). "
                f"Dark gradient background. Overall sentiment: {sentiment}. "
                f"Top 3 issues: {issues_str}. "
                f"Show confidence bars for each issue. Include risk warning section. "
                f"Bottom: '{date.today()} | Investment Comic | Powered by Gemini AI | 3/3'. "
                f"Clean Korean text. No real brand logos."
            )

        model = genai.GenerativeModel("gemini-2.5-flash-image")
        response = model.generate_content(
            prompt,
            generation_config={"response_modalities": ["IMAGE", "TEXT"], "max_output_tokens": 1024},
        )

        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                img_data = part.inline_data.data
                img_bytes = base64.b64decode(img_data) if isinstance(img_data, str) else img_data
                if len(img_bytes) > 500:
                    output_dir = Path("data/images")
                    output_dir.mkdir(parents=True, exist_ok=True)
                    today = date.today().strftime("%Y%m%d")
                    image_path = str(output_dir / f"card{card_no}_{today}.png")
                    with open(image_path, "wb") as f:
                        f.write(img_bytes)
                    logger.info(f"[CardNews] Card {card_no} Gemini 이미지 완료: {image_path}")
                    return image_path

        logger.warning(f"[CardNews] Card {card_no} Gemini 응답에 이미지 없음")
        return None
    except Exception as e:
        logger.warning(f"[CardNews] Card {card_no} Gemini 실패: {str(e)[:100]}")
        return None


def _card1_market(data: dict) -> str:
    """Card 1: 오늘의 시장"""
    regime = data.get("market_regime", {}).get("market_regime", "—")
    risk = data.get("market_regime", {}).get("market_risk_level", "—")
    snap = data.get("market_snapshot", {})
    ms = data.get("market_score", {})
    ts = data.get("trading_signal", {}).get("trading_signal", "HOLD")
    rc = REGIME_COLORS.get(regime, "#3b82f6")

    # SVG 캐릭터
    char_svg = ""
    try:
        from comic.assets.character_svg import get_character_svg, get_character_for_regime, get_pose_for_context
        char = get_character_for_regime(regime)
        pose = get_pose_for_context(char, regime, risk)
        char_svg = get_character_svg(char, pose, 100)
    except Exception:
        pass

    sp500 = snap.get("sp500", 0)
    try:
        sp_str = f"{float(sp500):+.1f}%"
        sp_color = "#10b981" if float(sp500) >= 0 else "#ef4444"
    except (TypeError, ValueError):
        sp_str, sp_color = str(sp500), "#aaa"

    score_html = ""
    labels = {"growth_score": "Growth", "inflation_score": "Inflation", "liquidity_score": "Liquidity",
              "risk_score": "Risk", "financial_stability_score": "Stability", "commodity_pressure_score": "Commodity"}
    for key, label in labels.items():
        v = ms.get(key, 2)
        color = SCORE_COLORS.get(v, "#aaa")
        pct = v * 20
        score_html += f'''<div style="display:flex;align-items:center;gap:10px;margin:6px 0;">
            <span style="width:80px;font-size:14px;color:#aaa;">{label}</span>
            <div style="flex:1;height:12px;background:#222;border-radius:6px;">
                <div style="width:{pct}%;height:100%;background:{color};border-radius:6px;"></div>
            </div>
            <span style="width:20px;font-size:14px;color:{color};font-weight:700;">{v}</span>
        </div>'''

    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<style>*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:1080px;height:1350px;background:linear-gradient(180deg,#0a1628 0%,#111827 100%);
font-family:-apple-system,'Segoe UI',sans-serif;color:#fff;padding:50px;}}</style></head><body>
<div style="text-align:center;margin-bottom:30px;">
    <div style="font-size:14px;color:#666;letter-spacing:4px;">INVESTMENT COMIC DAILY</div>
    <div style="font-size:36px;font-weight:900;margin:10px 0;">오늘의 시장</div>
    <div style="display:inline-block;background:{rc}22;border:1px solid {rc};padding:8px 24px;border-radius:20px;color:{rc};font-weight:700;font-size:18px;">{regime} | {risk} | {ts}</div>
</div>
<div style="display:flex;justify-content:center;margin:20px 0;">{char_svg}</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:30px 0;">
    <div style="background:#1a1a2e;border-radius:12px;padding:24px;text-align:center;">
        <div style="font-size:14px;color:#888;">S&P 500</div>
        <div style="font-size:36px;font-weight:900;color:{sp_color};">{sp_str}</div>
    </div>
    <div style="background:#1a1a2e;border-radius:12px;padding:24px;text-align:center;">
        <div style="font-size:14px;color:#888;">VIX</div>
        <div style="font-size:36px;font-weight:900;">{snap.get('vix', '—')}</div>
    </div>
    <div style="background:#1a1a2e;border-radius:12px;padding:24px;text-align:center;">
        <div style="font-size:14px;color:#888;">WTI Oil</div>
        <div style="font-size:36px;font-weight:900;color:#f59e0b;">${snap.get('oil', '—')}</div>
    </div>
    <div style="background:#1a1a2e;border-radius:12px;padding:24px;text-align:center;">
        <div style="font-size:14px;color:#888;">US 10Y</div>
        <div style="font-size:36px;font-weight:900;">{snap.get('us10y', '—')}%</div>
    </div>
</div>
<div style="background:#1a1a2e;border-radius:12px;padding:24px;margin:20px 0;">
    <div style="font-size:16px;color:#aaa;margin-bottom:12px;letter-spacing:2px;">MARKET SCORE</div>
    {score_html}
</div>
<div style="text-align:center;font-size:13px;color:#555;margin-top:20px;">{date.today()} | Investment Comic | 1/3</div>
</body></html>'''


def _card2_etf(data: dict) -> str:
    """Card 2: ETF 전략"""
    alloc = data.get("etf_allocation", {}).get("allocation", {})
    stance = data.get("etf_strategy", {}).get("stance", {})
    rank = data.get("etf_analysis", {}).get("etf_rank", {})
    ts = data.get("trading_signal", {}).get("signal_matrix", {})

    etf_html = ""
    sorted_etfs = sorted(alloc.items(), key=lambda x: -x[1])
    max_alloc = max(alloc.values()) if alloc else 1

    for etf, pct in sorted_etfs:
        st = stance.get(etf, "Neutral")
        r = rank.get(etf, "?")
        bar_w = int((pct / max_alloc) * 100) if max_alloc > 0 else 0
        sc = "#10b981" if st == "Overweight" else "#ef4444" if st == "Underweight" else "#f59e0b"
        etf_html += f'''<div style="margin:10px 0;">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                <span style="font-size:18px;font-weight:700;">{etf} <span style="font-size:13px;color:#888;">#{r}</span></span>
                <span style="font-size:18px;font-weight:700;color:{sc};">{pct}%</span>
            </div>
            <div style="height:16px;background:#222;border-radius:8px;">
                <div style="width:{bar_w}%;height:100%;background:{sc};border-radius:8px;"></div>
            </div>
            <div style="font-size:12px;color:{sc};margin-top:2px;">{st}</div>
        </div>'''

    buy = ts.get("buy_watch", [])
    reduce = ts.get("reduce", [])

    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<style>*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:1080px;height:1350px;background:linear-gradient(180deg,#0a1628 0%,#111827 100%);
font-family:-apple-system,'Segoe UI',sans-serif;color:#fff;padding:50px;}}</style></head><body>
<div style="text-align:center;margin-bottom:30px;">
    <div style="font-size:14px;color:#666;letter-spacing:4px;">INVESTMENT COMIC DAILY</div>
    <div style="font-size:36px;font-weight:900;margin:10px 0;">ETF 전략</div>
</div>
<div style="background:#1a1a2e;border-radius:16px;padding:30px;margin:20px 0;">
    {etf_html}
</div>
<div style="display:flex;gap:20px;margin:20px 0;">
    <div style="flex:1;background:#10b98122;border:1px solid #10b981;border-radius:12px;padding:20px;">
        <div style="font-size:14px;color:#10b981;margin-bottom:8px;">🟢 BUY Watch</div>
        <div style="font-size:24px;font-weight:900;color:#10b981;">{' · '.join(buy) if buy else '—'}</div>
    </div>
    <div style="flex:1;background:#ef444422;border:1px solid #ef4444;border-radius:12px;padding:20px;">
        <div style="font-size:14px;color:#ef4444;margin-bottom:8px;">🔴 Reduce</div>
        <div style="font-size:24px;font-weight:900;color:#ef4444;">{' · '.join(reduce) if reduce else '—'}</div>
    </div>
</div>
<div style="text-align:center;font-size:13px;color:#555;margin-top:20px;">{date.today()} | Investment Comic | 2/3</div>
</body></html>'''


def _card3_news(data: dict) -> str:
    """Card 3: AI 뉴스 분석"""
    news = data.get("news_analysis", {})
    top_issues = news.get("top_issues", [])
    key_risk = news.get("key_risk", "")
    overall = news.get("overall_sentiment", "neutral")

    IMPACT = {"bullish": ("🟢", "#10b981"), "bearish": ("🔴", "#ef4444"), "neutral": ("🟡", "#f59e0b")}

    issues_html = ""
    if top_issues:
        for i, iss in enumerate(top_issues[:3], 1):
            imp = iss.get("impact", "neutral")
            emoji, color = IMPACT.get(imp, ("🟡", "#f59e0b"))
            conf = iss.get("confidence", 0)
            conf_pct = int(conf * 100)
            issues_html += f'''<div style="background:#1a1a2e;border-radius:12px;padding:24px;margin:12px 0;border-left:4px solid {color};">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-size:20px;font-weight:700;">{emoji} {iss.get('topic', '?')}</span>
                    <span style="font-size:14px;color:{color};">{conf_pct}%</span>
                </div>
                <div style="font-size:16px;color:#ccc;">{iss.get('summary', '')}</div>
                <div style="margin-top:8px;height:6px;background:#222;border-radius:3px;">
                    <div style="width:{conf_pct}%;height:100%;background:{color};border-radius:3px;"></div>
                </div>
            </div>'''
    else:
        issues_html = '<div style="text-align:center;color:#888;padding:40px;">뉴스 분석 데이터 없음</div>'

    ov_emoji, ov_color = IMPACT.get(overall, ("🟡", "#f59e0b"))

    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<style>*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:1080px;height:1350px;background:linear-gradient(180deg,#0a1628 0%,#111827 100%);
font-family:-apple-system,'Segoe UI',sans-serif;color:#fff;padding:50px;}}</style></head><body>
<div style="text-align:center;margin-bottom:30px;">
    <div style="font-size:14px;color:#666;letter-spacing:4px;">INVESTMENT COMIC DAILY</div>
    <div style="font-size:36px;font-weight:900;margin:10px 0;">AI 뉴스 분석</div>
    <div style="display:inline-block;background:{ov_color}22;border:1px solid {ov_color};padding:6px 20px;border-radius:16px;color:{ov_color};font-size:16px;">
        {ov_emoji} Overall: {overall.upper()}
    </div>
</div>
<div style="margin:20px 0;">{issues_html}</div>
{f'<div style="background:#7c3aed22;border:1px solid #7c3aed;border-radius:12px;padding:20px;margin:20px 0;"><div style="font-size:14px;color:#7c3aed;margin-bottom:6px;">⚠️ 핵심 리스크</div><div style="font-size:18px;color:#ddd;">{key_risk}</div></div>' if key_risk else ''}
<div style="text-align:center;font-size:13px;color:#555;margin-top:20px;">{date.today()} | Investment Comic | Powered by Gemini AI | 3/3</div>
</body></html>'''


def _render_html(html: str, prefix: str) -> str | None:
    """HTML → Playwright 스크린샷"""
    try:
        from playwright.sync_api import sync_playwright

        output_dir = Path("data/images")
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        image_path = str(output_dir / f"{prefix}_{today}.png")

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write(html)
            html_path = f.name

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1350})
            page.goto(f"file://{html_path}")
            page.screenshot(path=image_path)
            browser.close()

        os.unlink(html_path)
        logger.info(f"[CardNews] {prefix} 생성: {image_path}")
        return image_path

    except Exception as e:
        logger.warning(f"[CardNews] {prefix} 렌더링 실패: {e}")
        return None
