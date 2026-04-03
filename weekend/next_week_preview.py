"""
weekend/next_week_preview.py (B-20B)
=======================================
일요일 다음 주 프리뷰 — "다음 주 전투 예고"

데이터:
  - event_calendar.py (다음 주 경제 이벤트)
  - weekly_tracker.json (마지막 레짐/시그널)
  - Gemini 텍스트 (다음 주 시나리오)

출력:
  - 예고 카드 (HTML → PNG 1080×1080)
  - X 트윗 텍스트
  - TG 메시지
"""
import logging
import os
from datetime import date, datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def generate_next_week_preview() -> dict:
    """
    다음 주 프리뷰 생성

    Returns:
        {
          "success": True/False,
          "tweet_text": str,
          "image_path": str | None,
          "tg_text": str,
          "events": list,
        }
    """
    try:
        # ── 1. 다음 주 이벤트 조회 ──
        events = _get_next_week_events()

        # ── 2. 마지막 레짐/시그널 조회 ──
        last_regime, last_signal, last_allocation = _get_last_analysis()

        # ── 3. Gemini 시나리오 생성 ──
        scenarios = _generate_scenarios(events, last_regime, last_signal)

        # ── 4. 트윗 텍스트 ──
        tweet = _build_tweet(events, last_regime, last_signal)

        # ── 5. 카드 이미지 ──
        image_path = _render_preview_card(events, last_regime, last_signal, scenarios)

        # ── 6. TG 메시지 ──
        tg_text = _build_tg_message(events, last_regime, last_signal, last_allocation, scenarios)

        logger.info(f"[NextWeekPreview] 생성 완료 | 이벤트 {len(events)}건")

        return {
            "success": True,
            "tweet_text": tweet,
            "image_path": image_path,
            "tg_text": tg_text,
            "events": events,
        }

    except Exception as e:
        logger.error(f"[NextWeekPreview] 생성 실패: {e}")
        return {"success": False, "error": str(e)}


def _get_next_week_events() -> list:
    """다음 주(월~금) 이벤트 조회"""
    from config.event_calendar import EVENT_CALENDAR_2026, EVENT_CHARACTER

    today = date.today()
    # 다음 월요일 ~ 금요일
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7  # 오늘이 월요일이면 다음 주 월요일
    next_monday = today + timedelta(days=days_until_monday)
    next_friday = next_monday + timedelta(days=4)

    events = []
    for ev in EVENT_CALENDAR_2026:
        ev_date = date.fromisoformat(ev["date"])
        if next_monday <= ev_date <= next_friday:
            char_info = EVENT_CHARACTER.get(ev["type"], {})
            events.append({
                **ev,
                "character": char_info.get("character", ""),
                "emoji": char_info.get("emoji", "📌"),
                "flavor": char_info.get("flavor", ""),
            })

    return events


def _get_last_analysis() -> tuple:
    """마지막 레짐/시그널/배분 조회 (weekly_tracker)"""
    try:
        from core.weekly_tracker import get_weekly_summary
        summary = get_weekly_summary()
        entries = summary.get("entries", [])
        if entries:
            last = entries[-1]
            regime = last.get("regime", "Unknown")
            signal = last.get("signal", "HOLD")
            allocation = last.get("allocation", {})
            return regime, signal, allocation
    except Exception:
        pass
    return "Unknown", "HOLD", {}


def _generate_scenarios(events: list, regime: str, signal: str) -> str:
    """Gemini로 다음 주 시나리오 생성"""
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return _fallback_scenarios(events, regime)

        event_str = "\n".join(
            f"- {e['date']} {e['emoji']} {e['name']} (캐릭터: {e['character']})"
            for e in events
        ) if events else "다음 주 주요 이벤트 없음"

        prompt = (
            f"투자 코믹 캐릭터 Max Bullhorn(강세), Baron Bearsworth(약세), "
            f"The Volatician(혼돈)의 다음 주 전투를 예고해줘.\n"
            f"- 현재 레짐: {regime}\n"
            f"- 현재 시그널: {signal}\n"
            f"- 다음 주 이벤트:\n{event_str}\n"
            f"한국어 3줄, 긴장감 있게 캐릭터 이름 사용. 이모지 1~2개."
        )

        result = call(prompt=prompt, model="flash-lite", max_tokens=150, temperature=0.8)
        if result.get("success"):
            return result["text"]

    except Exception as e:
        logger.warning(f"[NextWeekPreview] Gemini 시나리오 실패: {e}")

    return _fallback_scenarios(events, regime)


def _fallback_scenarios(events: list, regime: str) -> str:
    if events:
        ev = events[0]
        return f"{ev['emoji']} 다음 주 {ev['name']}가 다가옵니다. {regime} 레짐에서 어떤 변화가 올지 주목하세요."
    return f"📋 다음 주 특별 이벤트 없음. 현재 {regime} 레짐 유지 전망."


def _build_tweet(events: list, regime: str, signal: str) -> str:
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    next_monday = kst.date() + timedelta(days=(7 - kst.weekday()) % 7 or 7)
    week_str = next_monday.strftime("%m/%d")

    if events:
        event_lines = "\n".join(
            f"{e['emoji']} {e['date'][5:]} {e['name']}"
            for e in events[:3]
        )
    else:
        event_lines = "📋 주요 이벤트 없음"

    tweet = (
        f"⚔️ 다음 주 전투 예고 | {week_str}~\n\n"
        f"{event_lines}\n\n"
        f"🎯 현재 레짐: {regime}\n"
        f"📊 시그널: {signal}\n\n"
        f"#ETF #투자 #다음주전망 #미국증시"
    )
    return tweet


def _build_tg_message(events, regime, signal, allocation, scenarios) -> str:
    if events:
        event_lines = "\n".join(
            f"  {e['emoji']} {e['date']} — {e['name']}\n    └ {e['character']}: {e['flavor'][:50]}"
            for e in events
        )
    else:
        event_lines = "  📋 주요 이벤트 없음"

    alloc_str = ""
    if allocation:
        alloc_str = "\n📊 현재 ETF 배분:\n" + "\n".join(
            f"  {e}: {w}%" for e, w in allocation.items()
        )

    return (
        f"⚔️ 다음 주 전투 예고\n\n"
        f"🎯 현재 레짐: {regime} | 시그널: {signal}\n\n"
        f"📅 주요 이벤트:\n{event_lines}\n"
        f"{alloc_str}\n\n"
        f"💬 {scenarios}"
    )


def _render_preview_card(events, regime, signal, scenarios) -> str | None:
    """HTML → PNG 카드 이미지 생성"""
    try:
        # 이벤트 캐릭터별 색상
        TYPE_COLOR = {
            "FOMC": "#a855f7", "CPI": "#ef4444",
            "JOBS": "#22c55e", "GDP": "#3b82f6", "PPI": "#f97316",
        }

        event_rows = ""
        if events:
            for e in events:
                color = TYPE_COLOR.get(e["type"], "#888")
                event_rows += (
                    f'<div style="display:flex;align-items:center;gap:12px;'
                    f'padding:12px;background:#1a1a1a;border-radius:8px;'
                    f'border-left:4px solid {color};margin-bottom:8px">'
                    f'<span style="font-size:28px">{e["emoji"]}</span>'
                    f'<div>'
                    f'<div style="font-size:20px;font-weight:bold">{e["name"]}</div>'
                    f'<div style="font-size:16px;color:#888">{e["date"]} | {e["character"]}</div>'
                    f'</div></div>'
                )
        else:
            event_rows = (
                '<div style="text-align:center;padding:40px;color:#666;font-size:24px">'
                '📋 다음 주 주요 이벤트 없음</div>'
            )

        signal_color = "#22c55e" if signal == "BUY" else ("#ef4444" if signal == "REDUCE" else "#eab308")

        html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ margin:0; padding:0; background:#111; color:#fff; font-family:Arial,sans-serif; }}
.card {{ width:1080px; height:1080px; padding:60px; box-sizing:border-box;
  background:linear-gradient(135deg,#111 0%,#0f172a 100%); }}
.header {{ font-size:48px; font-weight:bold; margin-bottom:20px; }}
.sub {{ font-size:24px; color:#888; margin-bottom:40px; }}
.regime {{ display:flex; gap:30px; margin-bottom:30px; }}
.regime-item {{ background:#1a1a1a; border-radius:12px; padding:16px 24px;
  flex:1; text-align:center; }}
.regime-label {{ font-size:16px; color:#888; }}
.regime-value {{ font-size:28px; font-weight:bold; margin-top:4px; }}
.events {{ margin-bottom:30px; }}
.events-title {{ font-size:20px; color:#888; margin-bottom:12px; }}
.scenarios {{ font-size:18px; color:#aaa; line-height:1.6; margin-top:20px; }}
</style></head><body>
<div class="card">
  <div class="header">⚔️ 다음 주 전투 예고</div>
  <div class="sub">What's coming next week?</div>
  <div class="regime">
    <div class="regime-item">
      <div class="regime-label">현재 레짐</div>
      <div class="regime-value">{regime}</div>
    </div>
    <div class="regime-item">
      <div class="regime-label">시그널</div>
      <div class="regime-value" style="color:{signal_color}">{signal}</div>
    </div>
    <div class="regime-item">
      <div class="regime-label">이벤트</div>
      <div class="regime-value">{len(events)}건</div>
    </div>
  </div>
  <div class="events">
    <div class="events-title">📅 주요 이벤트</div>
    {event_rows}
  </div>
  <div class="scenarios">{scenarios[:150]}</div>
</div>
</body></html>'''

        kst = datetime.now(timezone.utc) + timedelta(hours=9)
        date_str = kst.strftime("%Y%m%d")
        output_path = f"data/images/next_week_preview_{date_str}.png"
        os.makedirs("data/images", exist_ok=True)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.set_content(html)
            page.screenshot(path=output_path, full_page=False)
            browser.close()

        logger.info(f"[NextWeekPreview] 카드 생성: {output_path}")
        return output_path

    except Exception as e:
        logger.warning(f"[NextWeekPreview] 카드 생성 실패: {e}")
        return None
