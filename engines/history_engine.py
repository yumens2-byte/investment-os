"""
engines/history_engine.py (C-5)
================================
"오늘의 시장 역사" 콘텐츠 생성

매일 "20XX년 오늘 무슨 일이?" 금융 역사 사건을 Gemini로 생성.
Morning Brief 또는 narrative 세션에서 독립 발행.

Gemini 실패 시 스킵 (기존 발행에 영향 없음).
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_history_today() -> dict:
    """
    오늘 날짜에 해당하는 금융 시장 역사적 사건 1건을 Gemini로 생성.

    Returns:
        {
          "success": True/False,
          "year": 2008,
          "event": "리만 브라더스 파산 신청",
          "market_impact": "S&P500 -4.7%, VIX 46돌파",
          "lesson": "위기 시 현금 확보의 중요성",
          "category": "crisis",
          "tweet": "📅 오늘의 시장 역사 | ...",
          "telegram": "📅 오늘의 시장 역사 | ...",
        }
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return {"success": False, "reason": "Gemini 미설정"}

        now = datetime.now()
        month = now.month
        day = now.day

        prompt = (
            f"오늘 {month}월 {day}일에 일어난 가장 중요한 금융/주식 시장 역사적 사건 1건을 알려줘.\n"
            f"JSON으로 응답:\n"
            f'{{"year": 2008,\n'
            f' "event": "리만 브라더스 파산 신청",\n'
            f' "market_impact": "S&P500 -4.7%, VIX 46돌파",\n'
            f' "lesson": "위기 시 현금 확보의 중요성",\n'
            f' "category": "crisis"}}\n\n'
            f"조건:\n"
            f"- 실제 역사적 사실만 (추측 금지)\n"
            f"- 금융/주식/경제 관련 사건만\n"
            f"- category: crisis/policy/milestone/crash/recovery 중 하나\n"
            f"- 한국어로\n"
            f"- JSON만 출력"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=200,
            temperature=0.5,
            response_json=True,
        )

        if result.get("success") and result.get("data"):
            data = result["data"]
            year = int(data.get("year", 0))
            event = str(data.get("event", ""))
            impact = str(data.get("market_impact", ""))
            lesson = str(data.get("lesson", ""))
            category = str(data.get("category", ""))

            if not event or year == 0:
                logger.warning("[HistoryEngine] Gemini 응답 불완전 → 스킵")
                return {"success": False, "reason": "응답 불완전"}

            # 트윗 포맷
            tweet = (
                f"📅 오늘의 시장 역사 | {year}년 {month}/{day}\n\n"
                f"{event}\n\n"
                f"📊 시장 영향: {impact}\n"
                f"💡 교훈: {lesson}\n\n"
                f"#시장역사 #투자교훈 #TodayInMarket"
            )

            # TG 포맷
            telegram = (
                f"📅 <b>오늘의 시장 역사</b> | {year}년 {month}월 {day}일\n\n"
                f"📌 {event}\n\n"
                f"📊 시장 영향: {impact}\n"
                f"💡 교훈: {lesson}\n\n"
                f"<i>#{category}</i>"
            )

            logger.info(
                f"[HistoryEngine] 생성 완료 | {year}년 {month}/{day} | "
                f"{category} | {event[:30]}..."
            )

            return {
                "success": True,
                "year": year,
                "event": event,
                "market_impact": impact,
                "lesson": lesson,
                "category": category,
                "tweet": tweet,
                "telegram": telegram,
            }

        logger.warning(f"[HistoryEngine] Gemini 실패 → 스킵: {result.get('error', '?')[:50]}")

    except Exception as e:
        logger.warning(f"[HistoryEngine] 생성 실패 (무시): {e}")

    return {"success": False, "reason": "Gemini 실패"}
