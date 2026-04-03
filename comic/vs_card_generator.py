"""
comic/vs_card_generator.py (B-21C)
=====================================
VS 배틀 카드 — Top ETF(Max) vs Worst ETF(Baron)

Narrative 세션 (11:30 KST)에 자동 첨부.
HTML+Playwright 렌더링 ($0).
크기: 1200×675 (X 이미지 최적화)
"""
import logging
import os
import tempfile
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# Score 색상
SCORE_COLORS = {1: "#10b981", 2: "#34d399", 3: "#f59e0b", 4: "#f97316", 5: "#ef4444"}


def generate_vs_card(core_data: dict) -> str | None:
    """
    core_data에서 Top ETF vs Worst ETF VS 카드 생성.

    Returns:
        이미지 파일 경로 (str) 또는 None
    """
    if not core_data:
        logger.info("[VSCard] core_data 없음 → 스킵")
        return None

    rank = core_data.get("etf_analysis", {}).get("etf_rank", {})
    alloc = core_data.get("etf_allocation", {}).get("allocation", {})
    stance = core_data.get("etf_strategy", {}).get("stance", {})
    regime = core_data.get("market_regime", {}).get("market_regime", "—")
    risk = core_data.get("market_regime", {}).get("market_risk_level", "—")
    ms = core_data.get("market_score", {})
    ts = core_data.get("trading_signal", {}).get("trading_signal", "HOLD")

    if not rank:
        logger.info("[VSCard] ETF 랭킹 없음 → 스킵")
        return None

    top_etf = min(rank, key=rank.get)
    worst_etf = max(rank, key=rank.get)

    top_alloc = alloc.get(top_etf, 0)
    worst_alloc = alloc.get(worst_etf, 0)
    top_stance = stance.get(top_etf, "Neutral")
    worst_stance = stance.get(worst_etf, "Neutral")

    # ── 1순위: Gemini 이미지 생성 ──
    gemini_path = _generate_vs_via_gemini(top_etf, worst_etf, top_alloc, worst_alloc,
                                          top_stance, worst_stance, regime, risk, ts)
    if gemini_path:
        return gemini_path

    # ── 2순위: HTML+Playwright fallback ──
    logger.info("[VSCard] Gemini 이미지 실패 → HTML fallback")
    html = _build_vs_html(
        top_etf=top_etf, worst_etf=worst_etf,
        top_alloc=top_alloc, worst_alloc=worst_alloc,
        top_stance=top_stance, worst_stance=worst_stance,
        regime=regime, risk=risk, signal=ts, score=ms,
    )

    image_path = _render_html_to_image(html)
    return image_path


def _generate_vs_via_gemini(top_etf, worst_etf, top_alloc, worst_alloc,
                           top_stance, worst_stance, regime, risk, signal) -> str | None:
    """Gemini Flash Image로 VS 카드 이미지 생성"""
    try:
        import os, base64
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            return None

        import google.generativeai as genai
        genai.configure(api_key=gemini_key)

        prompt = (
            f"Create a dramatic 1200x675 VS battle card for financial ETF comparison. "
            f"Style: Dark cinematic, vibrant neon colors, clean composition, professional. "
            f"Left side (GREEN): A heroic golden bull warrior labeled 'MAX BULLHORN' with "
            f"'{top_etf} {top_alloc}%' and '{top_stance}' badge in green. "
            f"Right side (RED): A menacing dark bear villain labeled 'BARON BEARSWORTH' with "
            f"'{worst_etf} {worst_alloc}%' and '{worst_stance}' badge in red. "
            f"Center: Large 'VS' text with electric energy. "
            f"Top banner: '{regime} | {risk} | {signal}'. "
            f"Bottom: 'Investment Comic' watermark. "
            f"Dark gradient background. No real brand logos. Safe for all ages."
        )

        model = genai.GenerativeModel("gemini-2.5-flash-preview-04-17")
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
                    image_path = str(output_dir / f"vs_card_{today}.png")
                    with open(image_path, "wb") as f:
                        f.write(img_bytes)
                    logger.info(f"[VSCard] Gemini 이미지 생성 완료: {image_path}")
                    return image_path

        logger.warning("[VSCard] Gemini 응답에 이미지 없음")
        return None
    except Exception as e:
        logger.warning(f"[VSCard] Gemini 이미지 실패: {str(e)[:100]}")
        return None


def _score_bar_html(label: str, value: int) -> str:
    """6축 점수 미니 바 1개"""
    color = SCORE_COLORS.get(value, "#aaa")
    pct = value * 20
    return (
        f'<div style="display:flex;align-items:center;gap:6px;">'
        f'<span style="width:30px;font-size:11px;color:#aaa;text-align:right;">{label}</span>'
        f'<div style="width:80px;height:8px;background:#333;border-radius:4px;">'
        f'<div style="width:{pct}%;height:100%;background:{color};border-radius:4px;"></div>'
        f'</div>'
        f'<span style="font-size:11px;color:{color};width:14px;">{value}</span></div>'
    )


def _build_vs_html(top_etf, worst_etf, top_alloc, worst_alloc,
                   top_stance, worst_stance, regime, risk, signal, score) -> str:
    """VS 카드 HTML (1200×675)"""

    score_bars = ""
    labels = {"growth_score": "GRW", "inflation_score": "INF", "liquidity_score": "LIQ",
              "risk_score": "RSK", "financial_stability_score": "STB", "commodity_pressure_score": "CMD"}
    for key, lbl in labels.items():
        v = score.get(key, 2)
        score_bars += _score_bar_html(lbl, v)

    stance_color_top = "#10b981" if top_stance == "Overweight" else "#f59e0b"
    stance_color_worst = "#ef4444" if worst_stance == "Underweight" else "#f59e0b"

    # SVG 캐릭터 생성
    max_svg = ""
    baron_svg = ""
    try:
        from comic.assets.character_svg import get_character_svg, get_pose_for_context
        max_pose = get_pose_for_context("max", regime, risk)
        baron_pose = get_pose_for_context("baron", regime, risk)
        max_svg = get_character_svg("max", max_pose, 80)
        baron_svg = get_character_svg("baron", baron_pose, 80)
    except Exception:
        max_svg = '<div style="font-size:80px;">🐂</div>'
        baron_svg = '<div style="font-size:80px;">🐻</div>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  width:1200px; height:675px;
  background: linear-gradient(135deg, #0a1628 0%, #1a1a2e 100%);
  font-family: -apple-system, 'Segoe UI', sans-serif; color:#fff;
  overflow:hidden; position:relative;
}}
.container {{ display:flex; height:100%; }}
.side {{
  flex:1; display:flex; flex-direction:column;
  align-items:center; justify-content:center; padding:30px;
}}
.left {{ background: linear-gradient(180deg, #10b98122 0%, transparent 60%); }}
.right {{ background: linear-gradient(180deg, #ef444422 0%, transparent 60%); }}
.center {{
  width:200px; display:flex; flex-direction:column;
  align-items:center; justify-content:center; position:relative;
}}
.vs {{
  font-size:64px; font-weight:900; color:#fff;
  text-shadow: 0 0 30px rgba(255,255,255,0.3);
}}
.char-emoji {{ font-size:80px; margin-bottom:10px; }}
.etf-name {{ font-size:48px; font-weight:900; margin-bottom:8px; }}
.alloc {{ font-size:36px; font-weight:700; margin-bottom:8px; }}
.stance {{
  padding:6px 16px; border-radius:12px; font-size:16px; font-weight:700;
  margin-bottom:12px;
}}
.label {{ font-size:14px; color:#888; letter-spacing:2px; text-transform:uppercase; }}
.regime-badge {{
  position:absolute; top:20px; left:50%; transform:translateX(-50%);
  background:#1e293b; border:1px solid #334155; padding:6px 16px;
  border-radius:16px; font-size:14px; color:#94a3b8;
  white-space:nowrap;
}}
.score-panel {{
  position:absolute; bottom:20px; left:50%; transform:translateX(-50%);
  display:flex; flex-direction:column; gap:4px;
}}
.date {{
  position:absolute; bottom:10px; right:20px;
  font-size:12px; color:#555;
}}
</style></head><body>
<div class="regime-badge">{regime} | {risk} | {signal}</div>
<div class="container">
  <div class="side left">
    <div class="label">TOP ETF</div>
    <div class="char-emoji">{max_svg}</div>
    <div class="etf-name" style="color:#10b981;">{top_etf}</div>
    <div class="alloc" style="color:#10b981;">{top_alloc}%</div>
    <div class="stance" style="background:{stance_color_top}22;color:{stance_color_top};border:1px solid {stance_color_top};">{top_stance}</div>
    <div class="label">MAX BULLHORN</div>
  </div>
  <div class="center">
    <div class="vs">VS</div>
    <div class="score-panel">{score_bars}</div>
  </div>
  <div class="side right">
    <div class="label">WORST ETF</div>
    <div class="char-emoji">{baron_svg}</div>
    <div class="etf-name" style="color:#ef4444;">{worst_etf}</div>
    <div class="alloc" style="color:#ef4444;">{worst_alloc}%</div>
    <div class="stance" style="background:{stance_color_worst}22;color:{stance_color_worst};border:1px solid {stance_color_worst};">{worst_stance}</div>
    <div class="label">BARON BEARSWORTH</div>
  </div>
</div>
<div class="date">{date.today()} | Investment Comic</div>
</body></html>"""


def _render_html_to_image(html: str) -> str | None:
    """HTML → Playwright 스크린샷"""
    try:
        from playwright.sync_api import sync_playwright

        output_dir = Path("data/images")
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        image_path = str(output_dir / f"vs_card_{today}.png")

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write(html)
            html_path = f.name

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 675})
            page.goto(f"file://{html_path}")
            page.screenshot(path=image_path)
            browser.close()

        os.unlink(html_path)
        logger.info(f"[VSCard] 이미지 생성 완료: {image_path}")
        return image_path

    except Exception as e:
        logger.warning(f"[VSCard] Playwright 렌더링 실패: {e}")
        return None
