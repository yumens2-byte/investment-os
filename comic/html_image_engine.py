"""
comic/html_image_engine.py
Investment Comic v2.0 — HTML + Playwright 내부 이미지 엔진

외부 API 없음 / 비용 $0 / GitHub Actions 실행 가능
GPT-4o 결제 전 메인 엔진 또는 영구 대안으로 사용
"""

import io
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 리스크별 테마 ────────────────────────────────────────

THEMES = {
    "LOW": {
        "bg_gradient": "linear-gradient(135deg, #0a1628 0%, #0d2137 50%, #0a1e2e 100%)",
        "accent":      "#10b981",
        "accent2":     "#34d399",
        "border":      "#10b981",
        "header_bg":   "rgba(16,185,129,0.15)",
        "bubble_bg":   "rgba(16,185,129,0.12)",
        "badge_color": "#10b981",
        "badge_text":  "LOW RISK",
        "badge_icon":  "📈",
    },
    "MEDIUM": {
        "bg_gradient": "linear-gradient(135deg, #1a1200 0%, #2a1f00 50%, #1a1500 100%)",
        "accent":      "#f59e0b",
        "accent2":     "#fbbf24",
        "border":      "#f59e0b",
        "header_bg":   "rgba(245,158,11,0.15)",
        "bubble_bg":   "rgba(245,158,11,0.12)",
        "badge_color": "#f59e0b",
        "badge_text":  "MEDIUM RISK",
        "badge_icon":  "⚖️",
    },
    "HIGH": {
        "bg_gradient": "linear-gradient(135deg, #1a0505 0%, #2d0a0a 50%, #1a0606 100%)",
        "accent":      "#ef4444",
        "accent2":     "#f87171",
        "border":      "#ef4444",
        "header_bg":   "rgba(239,68,68,0.15)",
        "bubble_bg":   "rgba(239,68,68,0.12)",
        "badge_color": "#ef4444",
        "badge_text":  "HIGH RISK",
        "badge_icon":  "📉",
    },
}

# ── 캐릭터 SVG ───────────────────────────────────────────

CHARACTER_SVGS = {
    "MAX_BULLHORN": """
<svg viewBox="0 0 120 140" xmlns="http://www.w3.org/2000/svg">
  <!-- 황소 히어로 MAX BULLHORN -->
  <!-- 몸통 -->
  <ellipse cx="60" cy="95" rx="32" ry="38" fill="#d4a017"/>
  <!-- 황금 갑옷 -->
  <ellipse cx="60" cy="95" rx="28" ry="34" fill="#f5c842"/>
  <rect x="38" y="78" width="44" height="35" rx="4" fill="#e8b820" opacity="0.8"/>
  <!-- 목 -->
  <rect x="50" y="62" width="20" height="18" rx="3" fill="#d4a017"/>
  <!-- 머리 (황소) -->
  <ellipse cx="60" cy="52" rx="24" ry="22" fill="#d4a017"/>
  <!-- 귀 -->
  <ellipse cx="36" cy="45" rx="8" ry="10" fill="#d4a017"/>
  <ellipse cx="36" cy="45" rx="5" ry="7" fill="#c47a15"/>
  <ellipse cx="84" cy="45" rx="8" ry="10" fill="#d4a017"/>
  <ellipse cx="84" cy="45" rx="5" ry="7" fill="#c47a15"/>
  <!-- 뿔 (황금) -->
  <path d="M42,35 Q28,10 20,20" stroke="#f5c842" stroke-width="5" fill="none" stroke-linecap="round"/>
  <path d="M78,35 Q92,10 100,20" stroke="#f5c842" stroke-width="5" fill="none" stroke-linecap="round"/>
  <!-- 눈 -->
  <circle cx="52" cy="50" r="5" fill="white"/>
  <circle cx="68" cy="50" r="5" fill="white"/>
  <circle cx="53" cy="51" r="3" fill="#1a1a2e"/>
  <circle cx="69" cy="51" r="3" fill="#1a1a2e"/>
  <!-- 눈 빛 -->
  <circle cx="54" cy="50" r="1" fill="white"/>
  <circle cx="70" cy="50" r="1" fill="white"/>
  <!-- 코 -->
  <ellipse cx="60" cy="60" rx="8" ry="6" fill="#c47a15"/>
  <circle cx="56" cy="60" r="2" fill="#8b4513"/>
  <circle cx="64" cy="60" r="2" fill="#8b4513"/>
  <!-- 팔 + 메가폰 -->
  <path d="M28,85 Q15,95 20,110" stroke="#d4a017" stroke-width="10" fill="none" stroke-linecap="round"/>
  <!-- 메가폰 -->
  <path d="M8,95 L25,88 L25,108 L8,101 Z" fill="#f5c842"/>
  <path d="M5,95 L8,93 L8,103 L5,101 Z" fill="#e8b820"/>
  <!-- 방패 -->
  <path d="M88,85 Q100,90 98,110 Q88,120 88,110 Z" fill="#3b82f6" opacity="0.8"/>
  <text x="90" y="102" font-size="10" fill="white" font-weight="bold">★</text>
  <!-- 망토 -->
  <path d="M35,90 Q20,110 25,130 L35,125 Q32,112 40,105 Z" fill="#3b82f6" opacity="0.7"/>
  <path d="M85,90 Q100,110 95,130 L85,125 Q88,112 80,105 Z" fill="#3b82f6" opacity="0.7"/>
  <!-- 별 장식 -->
  <text x="50" y="92" font-size="14" fill="#f5c842">★</text>
</svg>""",

    "BARON_BEARSWORTH": """
<svg viewBox="0 0 120 140" xmlns="http://www.w3.org/2000/svg">
  <!-- 곰 빌런 BARON BEARSWORTH -->
  <!-- 망토 -->
  <path d="M30,85 Q10,115 15,138 L35,132 Q28,115 40,100 Z" fill="#1a0a2e" opacity="0.9"/>
  <path d="M90,85 Q110,115 105,138 L85,132 Q92,115 80,100 Z" fill="#1a0a2e" opacity="0.9"/>
  <!-- 몸통 -->
  <ellipse cx="60" cy="98" rx="34" ry="36" fill="#3d2b1f"/>
  <!-- 갑옷 -->
  <ellipse cx="60" cy="98" rx="29" ry="31" fill="#2d1f14"/>
  <rect x="37" y="80" width="46" height="30" rx="4" fill="#1f1510" opacity="0.8"/>
  <!-- 빨간 심볼 -->
  <path d="M50,88 L60,78 L70,88 L65,95 L60,98 L55,95 Z" fill="#ef4444" opacity="0.8"/>
  <!-- 목 -->
  <rect x="48" y="64" width="24" height="18" rx="3" fill="#3d2b1f"/>
  <!-- 머리 (곰) -->
  <ellipse cx="60" cy="52" rx="26" ry="24" fill="#3d2b1f"/>
  <!-- 귀 -->
  <circle cx="36" cy="34" r="10" fill="#3d2b1f"/>
  <circle cx="36" cy="34" r="6" fill="#2d1f14"/>
  <circle cx="84" cy="34" r="10" fill="#3d2b1f"/>
  <circle cx="84" cy="34" r="6" fill="#2d1f14"/>
  <!-- 탑햇 -->
  <rect x="38" y="18" width="44" height="6" rx="2" fill="#1a0a2e"/>
  <rect x="44" y="0" width="32" height="20" rx="3" fill="#1a0a2e"/>
  <rect x="46" y="2" width="28" height="4" rx="1" fill="#ef4444" opacity="0.6"/>
  <!-- 눈 (사악한) -->
  <circle cx="51" cy="52" r="6" fill="#8b0000"/>
  <circle cx="69" cy="52" r="6" fill="#8b0000"/>
  <circle cx="51" cy="52" r="4" fill="#1a0000"/>
  <circle cx="69" cy="52" r="4" fill="#1a0000"/>
  <circle cx="52" cy="51" r="1.5" fill="#ef4444"/>
  <circle cx="70" cy="51" r="1.5" fill="#ef4444"/>
  <!-- 눈썹 (찡그림) -->
  <path d="M45,46 L57,49" stroke="#ef4444" stroke-width="2.5" stroke-linecap="round"/>
  <path d="M75,46 L63,49" stroke="#ef4444" stroke-width="2.5" stroke-linecap="round"/>
  <!-- 코 -->
  <ellipse cx="60" cy="62" rx="9" ry="7" fill="#2d1f14"/>
  <circle cx="56" cy="62" r="2.5" fill="#1a0a0a"/>
  <circle cx="64" cy="62" r="2.5" fill="#1a0a0a"/>
  <!-- 입 (빈정거림) -->
  <path d="M50,70 Q60,67 70,70" stroke="#8b0000" stroke-width="2" fill="none" stroke-linecap="round"/>
  <!-- 지팡이 -->
  <line x1="95" y1="75" x2="105" y2="138" stroke="#4a3728" stroke-width="5" stroke-linecap="round"/>
  <circle cx="96" cy="74" r="7" fill="#c0a000"/>
  <text x="92" y="78" font-size="9" fill="#1a0a2e">💀</text>
  <!-- 팔 -->
  <path d="M26,85 Q18,100 22,115" stroke="#3d2b1f" stroke-width="11" fill="none" stroke-linecap="round"/>
  <path d="M94,85 Q100,98 97,112" stroke="#3d2b1f" stroke-width="11" fill="none" stroke-linecap="round"/>
</svg>""",

    "THE_VOLATICIAN": """
<svg viewBox="0 0 120 150" xmlns="http://www.w3.org/2000/svg">
  <!-- 혼돈 마법사 THE VOLATICIAN -->
  <!-- 전기 후광 -->
  <circle cx="60" cy="55" r="45" fill="none" stroke="#8b5cf6" stroke-width="1" stroke-dasharray="4,3" opacity="0.5"/>
  <circle cx="60" cy="55" r="38" fill="none" stroke="#a78bfa" stroke-width="1" stroke-dasharray="3,4" opacity="0.4"/>
  <!-- 로브 -->
  <path d="M20,90 Q10,130 15,148 L105,148 Q110,130 100,90 Q80,108 60,105 Q40,108 20,90Z" fill="#2d1b4e"/>
  <!-- 로브 별 패턴 -->
  <text x="30" y="125" font-size="8" fill="#8b5cf6" opacity="0.7">✦</text>
  <text x="75" y="135" font-size="6" fill="#a78bfa" opacity="0.6">✦</text>
  <text x="55" y="142" font-size="7" fill="#7c3aed" opacity="0.5">✦</text>
  <!-- 몸통 -->
  <ellipse cx="60" cy="92" rx="30" ry="28" fill="#3b1f6e"/>
  <!-- VIX 마스크/얼굴 -->
  <ellipse cx="60" cy="55" rx="28" ry="30" fill="#1a0a35"/>
  <ellipse cx="60" cy="55" rx="24" ry="26" fill="#2d1050"/>
  <!-- VIX 숫자 마스크 -->
  <rect x="38" y="42" width="44" height="26" rx="6" fill="#4c1d95" opacity="0.9"/>
  <text x="41" y="60" font-size="16" font-weight="bold" fill="#a78bfa" font-family="monospace">VIX</text>
  <!-- 전기 눈 -->
  <ellipse cx="48" cy="50" rx="5" ry="4" fill="#7c3aed"/>
  <ellipse cx="72" cy="50" rx="5" ry="4" fill="#7c3aed"/>
  <circle cx="48" cy="50" r="3" fill="#ede9fe"/>
  <circle cx="72" cy="50" r="3" fill="#ede9fe"/>
  <!-- 번개 이펙트 -->
  <path d="M25,30 L32,50 L20,50 L28,72" stroke="#fbbf24" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M95,25 L88,48 L100,45 L92,70" stroke="#fbbf24" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <!-- 소용돌이 -->
  <path d="M15,60 Q5,55 10,45 Q15,35 25,40" stroke="#8b5cf6" stroke-width="2" fill="none" opacity="0.7"/>
  <path d="M105,60 Q115,55 110,45 Q105,35 95,40" stroke="#8b5cf6" stroke-width="2" fill="none" opacity="0.7"/>
  <!-- 마법 파티클 -->
  <circle cx="20" cy="75" r="3" fill="#a78bfa" opacity="0.8"/>
  <circle cx="100" cy="70" r="4" fill="#7c3aed" opacity="0.7"/>
  <circle cx="15" cy="95" r="2" fill="#8b5cf6" opacity="0.6"/>
  <circle cx="108" cy="95" r="3" fill="#a78bfa" opacity="0.7"/>
  <!-- 지팡이 (차트 모양) -->
  <line x1="88" y1="85" x2="112" y2="145" stroke="#4c1d95" stroke-width="4" stroke-linecap="round"/>
  <path d="M82,72 L86,82 L90,75 L94,85 L98,78 L102,85" stroke="#fbbf24" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <!-- 두건 -->
  <path d="M34,30 Q32,10 60,8 Q88,10 86,30 Q76,20 60,22 Q44,20 34,30Z" fill="#1a0a35"/>
  <path d="M34,30 Q30,20 28,35" stroke="#2d1050" stroke-width="3" fill="none"/>
  <path d="M86,30 Q90,20 92,35" stroke="#2d1050" stroke-width="3" fill="none"/>
</svg>""",
}


def _get_character_svg(risk_level: str) -> tuple[str, str]:
    """리스크 레벨에 맞는 캐릭터 SVG와 이름 반환"""
    if risk_level == "LOW":
        return CHARACTER_SVGS["MAX_BULLHORN"], "MAX BULLHORN"
    elif risk_level == "MEDIUM":
        return CHARACTER_SVGS["BARON_BEARSWORTH"], "BARON BEARSWORTH"
    else:
        return CHARACTER_SVGS["THE_VOLATICIAN"], "THE VOLATICIAN"


def _build_panel_html(
    cut: dict,
    theme: dict,
    char_svg: str,
    char_name: str,
    market_data: dict,
    panel_w: int,
    panel_h: int,
) -> str:
    """단일 컷 패널 HTML 생성"""

    mood_icons = {
        "optimistic":  "✨",
        "tense":       "⚡",
        "chaotic":     "🌀",
        "warning":     "⚠️",
        "triumphant":  "🏆",
    }
    mood_icon = mood_icons.get(cut.get("mood", ""), "💫")

    return f"""
<div class="panel" style="
    width:{panel_w}px; height:{panel_h}px;
    background: {theme['bg_gradient']};
    border: 2px solid {theme['border']};
    border-radius: 12px;
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
">
    <!-- 패널 헤더 -->
    <div style="
        background:{theme['header_bg']};
        border-bottom: 1px solid {theme['border']}44;
        padding: 6px 10px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-shrink: 0;
    ">
        <span style="
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            color: {theme['accent']};
            font-weight: bold;
            letter-spacing: 1px;
        ">CUT #{cut['cut_number']}</span>
        <span style="font-size: 14px;">{mood_icon}</span>
    </div>

    <!-- 캐릭터 + 장면 영역 -->
    <div style="
        flex: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 8px;
        position: relative;
    ">
        <!-- 캐릭터 SVG -->
        <div style="
            width: 90px;
            height: 105px;
            flex-shrink: 0;
            filter: drop-shadow(0 4px 12px {theme['accent']}66);
        ">{char_svg}</div>

        <!-- 말풍선 -->
        <div style="
            flex: 1;
            margin-left: 8px;
            background: {theme['bubble_bg']};
            border: 1px solid {theme['border']}88;
            border-radius: 12px 12px 12px 4px;
            padding: 8px 10px;
            position: relative;
        ">
            <!-- 말풍선 꼬리 -->
            <div style="
                position: absolute;
                left: -8px;
                top: 14px;
                width: 0;
                height: 0;
                border-top: 5px solid transparent;
                border-bottom: 5px solid transparent;
                border-right: 8px solid {theme['border']}88;
            "></div>
            <p style="
                font-size: 12px;
                color: #e0f0ff;
                margin: 0 0 4px 0;
                line-height: 1.5;
                font-weight: 500;
            ">{cut.get('dialogue', '')}</p>
            <p style="
                font-size: 10px;
                color: #7a98b8;
                margin: 0;
                line-height: 1.4;
                font-style: italic;
            ">{cut.get('scene', '')}</p>
        </div>
    </div>
</div>"""


def _build_full_html(
    story: dict,
    cuts: list[dict],
    risk_level: str,
    market_data: dict,
    comic_type: str,
    episode_no: int,
) -> str:
    """전체 1080×1080 HTML 생성"""

    theme = THEMES.get(risk_level, THEMES["MEDIUM"])
    char_svg, char_name = _get_character_svg(risk_level)

    grid_cols = 2 if comic_type == "daily" else 4
    grid_rows = 2

    padding     = 12
    wm_height   = 52
    header_h    = 56
    usable_w    = 1080 - padding * 2
    usable_h    = 1080 - header_h - wm_height - padding * 3
    panel_w     = (usable_w - padding * (grid_cols - 1)) // grid_cols
    panel_h     = (usable_h - padding * (grid_rows - 1)) // grid_rows

    panels_html = ""
    for cut in cuts[:grid_cols * grid_rows]:
        panels_html += _build_panel_html(
            cut, theme, char_svg, char_name,
            market_data, panel_w, panel_h
        )

    vix_val = market_data.get("vix", "N/A")
    sp500   = market_data.get("sp500", "N/A")
    sp_sign = "+" if isinstance(sp500, (int, float)) and sp500 >= 0 else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&family=Noto+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width: 1080px; height: 1080px;
    background: {theme['bg_gradient']};
    font-family: 'Noto Sans KR', sans-serif;
    overflow: hidden;
    position: relative;
  }}
  body::before {{
    content: '';
    position: absolute; inset: 0;
    background: radial-gradient(ellipse at 20% 20%, {theme['accent']}08 0%, transparent 60%),
                radial-gradient(ellipse at 80% 80%, {theme['accent2']}06 0%, transparent 60%);
    pointer-events: none;
  }}
</style>
</head>
<body>

<!-- 헤더 -->
<div style="
    height: {header_h}px;
    margin: {padding}px {padding}px 0;
    background: {theme['header_bg']};
    border: 1px solid {theme['border']}66;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
">
    <div style="display:flex; align-items:center; gap:12px;">
        <div style="
            background: {theme['badge_color']}22;
            border: 1px solid {theme['badge_color']};
            border-radius: 6px;
            padding: 4px 12px;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 700;
            color: {theme['badge_color']};
            letter-spacing: 2px;
        ">{theme['badge_icon']} {theme['badge_text']}</div>
        <div style="
            font-size: 16px;
            font-weight: 700;
            color: #d4e8ff;
            letter-spacing: 0.5px;
        ">{story.get('title', 'Investment Comic')}</div>
    </div>
    <div style="display:flex; align-items:center; gap:16px;">
        <div style="
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            color: #7a98b8;
        ">VIX <span style="color:{theme['accent']}; font-weight:700;">{vix_val}</span></div>
        <div style="
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            color: #7a98b8;
        ">S&P <span style="color:{'#10b981' if isinstance(sp500,(int,float)) and sp500>=0 else '#ef4444'}; font-weight:700;">{sp_sign}{sp500}%</span></div>
        <div style="
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            color: {theme['accent']};
            font-weight: 700;
        ">Ep.{episode_no}</div>
    </div>
</div>

<!-- 패널 그리드 -->
<div style="
    display: grid;
    grid-template-columns: repeat({grid_cols}, {panel_w}px);
    grid-template-rows: repeat({grid_rows}, {panel_h}px);
    gap: {padding}px;
    margin: {padding}px {padding}px 0;
">
{panels_html}
</div>

<!-- 워터마크 -->
<div style="
    height: {wm_height}px;
    margin: {padding}px {padding}px 0;
    background: rgba(0,0,0,0.6);
    border: 1px solid {theme['border']}33;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
">
    <span style="
        font-family: 'IBM Plex Mono', monospace;
        font-size: 14px;
        font-weight: 700;
        color: {theme['accent']};
    ">@InvestmentComic</span>
    <span style="
        font-size: 13px;
        color: #7a98b8;
        font-weight: 500;
    ">{story.get('title', '')[:32]}</span>
    <span style="
        font-family: 'IBM Plex Mono', monospace;
        font-size: 12px;
        color: {theme['accent2']};
        font-weight: 600;
    ">{'DAILY' if comic_type=='daily' else 'WEEKLY'} · {comic_type == 'daily' and '4CUT' or '8CUT'}</span>
</div>

</body>
</html>"""


def generate_html_comic(
    story: dict,
    risk_level: str,
    market_data: dict,
    comic_type: str,
    episode_no: int,
) -> bytes:
    """
    HTML + Playwright로 1080×1080 코믹 이미지 생성

    Returns: PNG bytes
    Raises: RuntimeError (Playwright 미설치 등)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright 미설치. 'pip install playwright && playwright install chromium' 실행 필요"
        )

    html_content = _build_full_html(
        story      = story,
        cuts       = story.get("cuts", []),
        risk_level = risk_level,
        market_data = market_data,
        comic_type  = comic_type,
        episode_no  = episode_no,
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html_content)
        tmp_html = f.name

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.goto(f"file://{tmp_html}")
            # 폰트 로딩 대기
            page.wait_for_timeout(2000)
            img_bytes = page.screenshot(type="png", full_page=False)
            browser.close()

        logger.info(f"[HtmlEngine] 이미지 생성 완료 — {len(img_bytes):,} bytes")
        return img_bytes

    finally:
        try:
            import os
            os.unlink(tmp_html)
        except Exception:
            pass
