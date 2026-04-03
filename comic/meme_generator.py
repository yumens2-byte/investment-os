"""
comic/meme_generator.py (B-21A)
==================================
Alert 발동 시 1컷 시장 밈 자동 생성

흐름:
  Alert 발동 → Gemini Flash-Lite로 밈 텍스트 생성 → HTML 렌더링 → Playwright 스크린샷
  → X 이미지 트윗 + TG 발행

캐릭터 매핑:
  VIX/CRISIS → The Volatician (혼돈)
  OIL/SPY↓/Risk-Off → Baron Bearsworth (공포)
  SPY↑/Risk-On → Max Bullhorn (희망)
  ETF_RANK_CHANGE → Max vs Baron (대결)

이미지: HTML+Playwright ($0)
크기: 1080×1080 (인스타그램 호환)
"""
import logging
import os
import tempfile
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# Alert 유형별 캐릭터 + 색상 매핑
ALERT_CHARACTER = {
    "VIX":              ("The Volatician", "#7c3aed", "⚡"),
    "VIX_COUNTDOWN":    ("The Volatician", "#7c3aed", "⏳"),
    "CRISIS":           ("The Volatician", "#dc2626", "🚨"),
    "OIL":              ("Baron Bearsworth", "#f59e0b", "🛢️"),
    "SPY_CRASH":        ("Baron Bearsworth", "#dc2626", "📉"),
    "REGIME_BEARISH":   ("Baron Bearsworth", "#ef4444", "🐻"),
    "SPY_RALLY":        ("Max Bullhorn",    "#10b981", "📈"),
    "REGIME_BULLISH":   ("Max Bullhorn",    "#10b981", "🐂"),
    "ETF_RANK_CHANGE":  ("Max vs Baron",    "#3b82f6", "⚔️"),
}

# 캐릭터별 실루엣 CSS (HTML 내장)
CHARACTER_STYLE = {
    "The Volatician": "color: #a78bfa; text-shadow: 0 0 20px #7c3aed;",
    "Baron Bearsworth": "color: #fbbf24; text-shadow: 0 0 20px #f59e0b;",
    "Max Bullhorn": "color: #34d399; text-shadow: 0 0 20px #10b981;",
    "Max vs Baron": "color: #60a5fa; text-shadow: 0 0 20px #3b82f6;",
}


def generate_meme(alert_type: str, alert_level: str,
                  snapshot: dict, core_data: dict = None) -> str | None:
    """
    Alert용 1컷 밈 이미지 생성.

    Args:
        alert_type: "VIX" | "OIL" | "SPY" | "CRISIS" | "REGIME_CHANGE" 등
        alert_level: "L1" | "L2" | "L3"
        snapshot: 시장 스냅샷 (vix, oil, sp500 등)
        core_data: core_data.json data dict (선택)

    Returns:
        이미지 파일 경로 (str) 또는 None (실패 시)
    """
    # 캐릭터 결정
    char_key = _resolve_character_key(alert_type, alert_level, snapshot, core_data)
    character, accent_color, emoji = ALERT_CHARACTER.get(
        char_key, ("The Volatician", "#7c3aed", "⚡")
    )

    # Gemini로 밈 텍스트 생성
    meme_text = _generate_meme_text(alert_type, alert_level, snapshot, character)

    # ── 1순위: Gemini 이미지 생성 ──
    gemini_path = _generate_via_gemini_image(alert_type, alert_level, character, meme_text, snapshot)
    if gemini_path:
        return gemini_path

    # ── 2순위: HTML+Playwright fallback ──
    logger.info("[Meme] Gemini 이미지 실패 → HTML fallback")
    html = _build_meme_html(
        character=character,
        meme_text=meme_text,
        accent_color=accent_color,
        emoji=emoji,
        alert_type=alert_type,
        alert_level=alert_level,
        snapshot=snapshot,
    )

    image_path = _render_html_to_image(html)
    return image_path


def _generate_via_gemini_image(alert_type: str, alert_level: str,
                               character: str, meme_text: str,
                               snapshot: dict) -> str | None:
    """Gemini Flash Image로 밈 이미지 생성 (Main/Sub 키 자동전환)"""
    try:
        from core.gemini_gateway import generate_image, is_available
        if not is_available():
            return None

        vix = snapshot.get("vix", "?")
        oil = snapshot.get("oil", "?")
        sp500 = snapshot.get("sp500", "?")

        prompt = (
            f"Create a dramatic 1080x1080 comic-style meme image for a financial market alert. "
            f"Style: Dark cinematic comic book art, vibrant colors, clean composition. "
            f"Character: {character} - "
        )
        if "Volatician" in character:
            prompt += "a mysterious robed wizard with a glowing VIX mask, purple lightning energy. "
        elif "Baron" in character:
            prompt += "a menacing dark bear villain in black and crimson armor with a top hat. "
        else:
            prompt += "a heroic golden bull warrior in gleaming gold and blue armor. "

        prompt += (
            f"Text overlay: '{meme_text}' in bold Korean text. "
            f"Bottom stats bar: VIX {vix} | OIL ${oil} | SPY {sp500}%. "
            f"Alert badge: '{alert_type} {alert_level}' in corner. "
            f"Dark gradient background. No real brand logos. Safe for all ages."
        )

        output_dir = Path("data/images")
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        image_path = str(output_dir / f"meme_{today}.png")

        result = generate_image(prompt=prompt, output_path=image_path)
        if result["success"]:
            logger.info(f"[Meme] Gemini 이미지 생성 완료: {image_path} (key={result['key_used']})")
            return image_path

        logger.warning(f"[Meme] Gemini 이미지 실패: {result['error'][:80]}")
        return None

    except Exception as e:
        logger.warning(f"[Meme] Gemini 이미지 예외: {str(e)[:80]}")
        return None


def _resolve_character_key(alert_type: str, alert_level: str,
                           snapshot: dict, core_data: dict = None) -> str:
    """Alert 유형 + 컨텍스트로 캐릭터 키 결정"""
    if alert_type in ("VIX", "VIX_COUNTDOWN", "CRISIS"):
        return alert_type

    if alert_type == "OIL":
        return "OIL"

    if alert_type == "SPY":
        sp500 = snapshot.get("sp500", 0)
        try:
            return "SPY_RALLY" if float(sp500) > 0 else "SPY_CRASH"
        except (TypeError, ValueError):
            return "SPY_CRASH"

    if alert_type == "REGIME_CHANGE":
        if core_data:
            regime = core_data.get("market_regime", {}).get("market_regime", "")
            if regime in ("Risk-On",):
                return "REGIME_BULLISH"
        return "REGIME_BEARISH"

    if alert_type == "ETF_RANK_CHANGE":
        return "ETF_RANK_CHANGE"

    return "VIX"  # fallback


def _generate_meme_text(alert_type: str, alert_level: str,
                        snapshot: dict, character: str) -> str:
    """Gemini Flash-Lite로 밈 텍스트 1줄 생성"""
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return _fallback_meme_text(alert_type, snapshot)

        vix = snapshot.get("vix", "?")
        oil = snapshot.get("oil", "?")
        sp500 = snapshot.get("sp500", "?")

        prompt = (
            f"Alert: {alert_type} Level {alert_level}. "
            f"VIX={vix}, Oil=${oil}, SPY={sp500}%. "
            f"Character: {character}.\n"
            f"20자 이내 한국어 밈 텍스트 1줄만 생성.\n"
            f"예시: '변동성의 마법이 시작된다', '곰의 시대가 열린다', "
            f"'황금빛 돌진이 시작된다'\n"
            f"텍스트만 출력. 따옴표/설명 없이."
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=50,
            temperature=0.9,
        )

        if result["success"] and result["text"]:
            text = result["text"].strip().strip("'\"")
            if len(text) <= 30:
                logger.info(f"[Meme] Gemini 텍스트: {text}")
                return text

    except Exception as e:
        logger.warning(f"[Meme] Gemini 실패: {e}")

    return _fallback_meme_text(alert_type, snapshot)


def _fallback_meme_text(alert_type: str, snapshot: dict) -> str:
    """Gemini 실패 시 rule-based 밈 텍스트"""
    texts = {
        "VIX": f"VIX {snapshot.get('vix', '?')} — 폭풍이 온다",
        "VIX_COUNTDOWN": f"VIX {snapshot.get('vix', '?')} 카운트다운",
        "CRISIS": "위기 경보 발령",
        "OIL": f"유가 ${snapshot.get('oil', '?')} 충격",
        "SPY": f"SPY {snapshot.get('sp500', '?')}% 급변",
        "REGIME_CHANGE": "레짐 전환 감지",
        "ETF_RANK_CHANGE": "전략 변경 필요",
    }
    return texts.get(alert_type, "시장 경보")


def _build_meme_html(character: str, meme_text: str, accent_color: str,
                     emoji: str, alert_type: str, alert_level: str,
                     snapshot: dict) -> str:
    """1컷 밈 HTML 생성 (1080×1080)"""
    char_style = CHARACTER_STYLE.get(character, "color: #fff;")
    vix = snapshot.get("vix", "—")
    oil = snapshot.get("oil", "—")
    sp500 = snapshot.get("sp500", "—")

    try:
        sp500_str = f"{float(sp500):+.1f}%"
    except (TypeError, ValueError):
        sp500_str = str(sp500)

    # SVG 캐릭터 삽입 (B 고도화)
    char_svg = ""
    try:
        from comic.assets.character_svg import (
            get_character_svg, get_character_for_regime, get_pose_for_context
        )
        char_key_map = {
            "The Volatician": "vol", "Baron Bearsworth": "baron", "Max Bullhorn": "max",
            "Max vs Baron": "max",
        }
        char_id = char_key_map.get(character, "max")
        pose = get_pose_for_context(char_id, "", "HIGH" if "VIX" in alert_type else "MEDIUM")
        char_svg = get_character_svg(char_id, pose, 120)
    except Exception:
        char_svg = f'<div style="font-size:120px;">{emoji}</div>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  width: 1080px; height: 1080px;
  background: linear-gradient(135deg, #0f0f1a 0%, {accent_color}22 50%, #0f0f1a 100%);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  font-family: -apple-system, 'Segoe UI', sans-serif; color: #fff;
  overflow: hidden; position: relative;
}}
.glow {{
  position: absolute; width: 400px; height: 400px; border-radius: 50%;
  background: {accent_color}33; filter: blur(100px);
  top: 50%; left: 50%; transform: translate(-50%, -50%);
}}
.character-svg {{ z-index: 1; margin-bottom: 10px; }}
.character-label {{
  font-size: 22px; font-weight: 300; letter-spacing: 4px;
  text-transform: uppercase; margin-bottom: 20px; z-index: 1;
  {char_style}
}}
.meme-text {{
  font-size: 52px; font-weight: 900; text-align: center;
  max-width: 800px; line-height: 1.3; z-index: 1;
  text-shadow: 0 4px 20px rgba(0,0,0,0.5);
}}
.stats {{
  position: absolute; bottom: 60px; display: flex; gap: 40px; z-index: 1;
  font-size: 22px; color: #aaa; letter-spacing: 1px;
}}
.stats span {{ color: {accent_color}; font-weight: 700; }}
.badge {{
  position: absolute; top: 40px; right: 40px;
  background: {accent_color}; color: #fff; padding: 8px 20px;
  border-radius: 20px; font-size: 18px; font-weight: 700;
  z-index: 1;
}}
.logo {{
  position: absolute; bottom: 20px; right: 30px;
  font-size: 14px; color: #555; z-index: 1;
}}
</style></head><body>
  <div class="glow"></div>
  <div class="badge">{alert_type} {alert_level}</div>
  <div class="character-svg">{char_svg}</div>
  <div class="character-label">{character}</div>
  <div class="meme-text">{meme_text}</div>
  <div class="stats">
    <div>VIX <span>{vix}</span></div>
    <div>OIL <span>${oil}</span></div>
    <div>SPY <span>{sp500_str}</span></div>
  </div>
  <div class="logo">Investment Comic</div>
</body></html>"""


def _render_html_to_image(html: str) -> str | None:
    """HTML → Playwright 스크린샷"""
    try:
        from playwright.sync_api import sync_playwright

        output_dir = Path("data/images")
        output_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        image_path = str(output_dir / f"meme_{today}.png")

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write(html)
            html_path = f.name

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.goto(f"file://{html_path}")
            page.screenshot(path=image_path)
            browser.close()

        os.unlink(html_path)
        logger.info(f"[Meme] 이미지 생성 완료: {image_path}")
        return image_path

    except Exception as e:
        logger.warning(f"[Meme] Playwright 렌더링 실패: {e}")
        return None
