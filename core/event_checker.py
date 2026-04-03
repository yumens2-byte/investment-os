"""
core/event_checker.py (B-23A)
================================
빅이벤트 D-1 / D-day 자동 감지

매 세션 시작 시 호출하여:
  - D-1: 예고 카드 생성 + X/TG 발행
  - D-day: event_context 반환 → comic/pipeline에 전달

사용처:
  - run_market.py Step 0 (이벤트 체크)
  - comic/pipeline.py (event_context 주입)
"""
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def check_today() -> dict:
    """
    오늘 날짜 기준 이벤트 체크.

    Returns:
        {
          "has_event": True/False,
          "is_d_day": True/False,
          "is_d_minus_1": True/False,
          "event": {"date": "2026-04-10", "type": "CPI", "name": "3월 CPI 발표"} | None,
          "event_context": {...} | None,  # D-day 시 comic/story에 전달할 컨텍스트
        }
    """
    from config.event_calendar import EVENT_CALENDAR_2026, EVENT_CHARACTER

    today = date.today()
    tomorrow = today + timedelta(days=1)

    today_str = today.isoformat()
    tomorrow_str = tomorrow.isoformat()

    # D-day 체크
    d_day_event = next((e for e in EVENT_CALENDAR_2026 if e["date"] == today_str), None)
    if d_day_event:
        event_type = d_day_event["type"]
        char_info = EVENT_CHARACTER.get(event_type, {})

        event_context = {
            "event_type": event_type,
            "event_name": d_day_event["name"],
            "character": char_info.get("character", "The Volatician"),
            "flavor": char_info.get("flavor", ""),
            "emoji": char_info.get("emoji", "📊"),
            "force_risk": char_info.get("force_risk"),
        }

        logger.info(f"[EventChecker] D-day 감지: {d_day_event['name']} ({event_type})")
        return {
            "has_event": True,
            "is_d_day": True,
            "is_d_minus_1": False,
            "event": d_day_event,
            "event_context": event_context,
        }

    # D-1 체크
    d_minus_1_event = next((e for e in EVENT_CALENDAR_2026 if e["date"] == tomorrow_str), None)
    if d_minus_1_event:
        logger.info(f"[EventChecker] D-1 감지: 내일 {d_minus_1_event['name']}")
        return {
            "has_event": True,
            "is_d_day": False,
            "is_d_minus_1": True,
            "event": d_minus_1_event,
            "event_context": None,
        }

    return {
        "has_event": False,
        "is_d_day": False,
        "is_d_minus_1": False,
        "event": None,
        "event_context": None,
    }


def generate_preview_card(event: dict, core_data: dict = None) -> str | None:
    """
    D-1 예고 카드 생성 — Gemini로 시나리오 3개 생성 + HTML 이미지

    Args:
        event: 캘린더 이벤트 dict
        core_data: 현재 시장 데이터 (선택)

    Returns:
        이미지 파일 경로 (str) 또는 None
    """
    from config.event_calendar import EVENT_CHARACTER

    event_type = event.get("type", "")
    event_name = event.get("name", "이벤트")
    char_info = EVENT_CHARACTER.get(event_type, {})
    character = char_info.get("character", "The Volatician")
    emoji = char_info.get("emoji", "📊")

    # Gemini로 시나리오 3개 생성
    scenarios = _generate_scenarios(event, core_data)

    # HTML 렌더링
    html = _build_preview_html(event_name, character, emoji, scenarios, core_data)
    return _render_html_to_image(html, f"preview_{event_type}")


def _generate_scenarios(event: dict, core_data: dict = None) -> list:
    """Gemini로 예상 시나리오 3개 생성"""
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return _fallback_scenarios(event)

        event_name = event.get("name", "")
        event_type = event.get("type", "")

        regime = ""
        fg = ""
        rate = ""
        if core_data:
            regime = core_data.get("market_regime", {}).get("market_regime", "")
            fg = core_data.get("signals", {}).get("fear_greed_state", "")
            rate = core_data.get("macro_data", {}).get("fed_funds_rate", "")

        prompt = (
            f"내일 {event_name} 예정.\n"
            f"현재: 레짐={regime}, F&G={fg}, 기준금리={rate}%\n\n"
            f"예상 시나리오 3개를 한국어로 작성 (각 20자 이내):\n"
            f"1. 시장 기대 부합 시나리오\n"
            f"2. 긍정적 서프라이즈 시나리오\n"
            f"3. 부정적 서프라이즈 시나리오\n\n"
            f"형식: 줄바꿈으로 3줄만 출력. 번호/설명 없이 텍스트만."
        )

        result = call(prompt=prompt, model="flash-lite", max_tokens=150, temperature=0.7)

        if result["success"] and result["text"]:
            lines = [l.strip() for l in result["text"].strip().split("\n") if l.strip()]
            if len(lines) >= 3:
                return lines[:3]

    except Exception as e:
        logger.warning(f"[EventChecker] 시나리오 생성 실패: {e}")

    return _fallback_scenarios(event)


def _fallback_scenarios(event: dict) -> list:
    """Gemini 실패 시 기본 시나리오"""
    scenarios = {
        "FOMC": ["금리 동결 유지", "25bp 인하 서프라이즈", "매파적 발언 충격"],
        "CPI": ["예상치 부합 안도", "예상 하회 랠리", "예상 상회 하락"],
        "JOBS": ["고용 안정 유지", "고용 서프라이즈 상승", "고용 둔화 우려"],
        "GDP": ["성장 유지 확인", "예상 상회 호재", "성장 둔화 충격"],
    }
    return scenarios.get(event.get("type", ""), ["시나리오 1", "시나리오 2", "시나리오 3"])


def _build_preview_html(event_name, character, emoji, scenarios, core_data) -> str:
    """D-1 예고 카드 HTML (1080×1080)"""
    scenario_html = ""
    labels = ["📗 기대 부합", "📈 긍정 서프라이즈", "📉 부정 서프라이즈"]
    colors = ["#10b981", "#3b82f6", "#ef4444"]
    for i, (sc, lb, cl) in enumerate(zip(scenarios, labels, colors)):
        scenario_html += (
            f'<div style="background:{cl}15;border-left:3px solid {cl};'
            f'padding:14px 18px;border-radius:0 8px 8px 0;margin-bottom:12px;">'
            f'<div style="font-size:14px;color:{cl};margin-bottom:4px;">{lb}</div>'
            f'<div style="font-size:22px;color:#fff;">{sc}</div></div>'
        )

    regime = ""
    if core_data:
        regime = core_data.get("market_regime", {}).get("market_regime", "—")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  width:1080px; height:1080px;
  background: linear-gradient(135deg, #0f0f1a 0%, #1a1a3e 100%);
  font-family: -apple-system, 'Segoe UI', sans-serif; color:#fff;
  display:flex; flex-direction:column; align-items:center;
  justify-content:center; padding:60px;
}}
</style></head><body>
  <div style="font-size:14px;color:#888;letter-spacing:4px;margin-bottom:20px;">
    MARKET EVENT PREVIEW
  </div>
  <div style="font-size:80px;margin-bottom:16px;">{emoji}</div>
  <div style="font-size:16px;color:#aaa;margin-bottom:8px;">내일</div>
  <div style="font-size:40px;font-weight:900;margin-bottom:8px;text-align:center;">
    {event_name}
  </div>
  <div style="font-size:18px;color:#888;margin-bottom:40px;">
    {character} | {regime}
  </div>
  <div style="width:100%;max-width:700px;">
    <div style="font-size:16px;color:#aaa;margin-bottom:16px;letter-spacing:2px;">
      예상 시나리오
    </div>
    {scenario_html}
  </div>
  <div style="position:absolute;bottom:30px;font-size:13px;color:#555;">
    Investment Comic | Powered by Gemini AI
  </div>
</body></html>"""


def _render_html_to_image(html: str, prefix: str = "event") -> Optional[str]:
    """HTML → Playwright 스크린샷"""
    try:
        import os
        import tempfile
        from pathlib import Path
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
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.goto(f"file://{html_path}")
            page.screenshot(path=image_path)
            browser.close()

        os.unlink(html_path)
        logger.info(f"[EventChecker] 이미지 생성: {image_path}")
        return image_path

    except Exception as e:
        logger.warning(f"[EventChecker] Playwright 실패: {e}")
        return None


def format_preview_tweet(event: dict) -> str:
    """D-1 예고 트윗 텍스트"""
    from config.event_calendar import EVENT_CHARACTER
    char_info = EVENT_CHARACTER.get(event.get("type", ""), {})
    emoji = char_info.get("emoji", "📊")

    return (
        f"{emoji} 내일 {event.get('name', '이벤트')}\n\n"
        f"시장 긴장 고조 — 변동성 확대 가능\n\n"
        f"#InvestmentComic #미국증시 #{event.get('type', '')}"
    )
