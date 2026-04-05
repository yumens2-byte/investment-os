"""
engines/earnings_checker.py (D-3 + C-17)
==========================================
Gemini로 오늘 실적 발표 기업 자동 조회.
morning 세션 TG에 "📅 오늘 실적: AAPL(AMC), MSFT(BMO)" 표시.

C-17 확장: 빅테크 7종목 실적 감지 시 stock_analyzer 트리거 정보 반환.

VERSION = "1.1.0"
RPD: +1/일 (Gemini flash-lite)
"""
import logging
import re
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ── C-17: 빅테크 7종목 (실적 시즌 단일종목 분석 대상) ──
BIG_TECH_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]


def get_today_earnings() -> dict:
    """
    D-3: Gemini에게 오늘 미국 주요 실적 발표 기업 조회.

    Returns:
        {
            "success": True,
            "earnings": [
                {"company": "AAPL", "time": "AMC"},
                {"company": "MSFT", "time": "BMO"},
            ],
            "tweet_line": "📅 오늘 실적: AAPL(AMC), MSFT(BMO)",
            "tg_line": "📅 <b>오늘 실적</b>: AAPL(AMC), MSFT(BMO)",
            "big_tech": ["AAPL", "MSFT"],  # C-17: 빅테크 해당 종목
        }
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            logger.info("[Earnings] Gemini 미사용 → 스킵")
            return _empty_result()

        today = datetime.now(KST).strftime("%Y-%m-%d")
        prompt = (
            f"오늘 {today} 미국 장에서 실적(earnings)을 발표하는 주요 기업 목록을 알려줘.\n"
            f"조건:\n"
            f"- 시가총액 상위 기업 위주 (최대 5개)\n"
            f"- 각 기업의 티커와 발표 시간 (BMO=장전, AMC=장후) 포함\n"
            f"- 실적 발표가 없는 날이면 '없음'이라고만 답해\n"
            f"- 형식: TICKER(BMO/AMC), 쉼표 구분\n"
            f"- 예시: AAPL(AMC), MSFT(BMO), GOOGL(AMC)\n"
            f"- 티커와 시간만 출력. 설명, 부연 없이 한 줄로\n"
        )

        result = call(prompt=prompt, model="flash-lite", max_tokens=150, temperature=0.3)

        if not result.get("success"):
            logger.warning("[Earnings] Gemini 호출 실패")
            return _empty_result()

        text = result["text"].strip()

        # "없음" 체크
        if "없음" in text or "없" in text or len(text) < 3:
            logger.info("[Earnings] 오늘 실적 발표 없음")
            return {
                "success": True,
                "earnings": [],
                "tweet_line": "",
                "tg_line": "",
                "big_tech": [],
            }

        # 파싱: "AAPL(AMC), MSFT(BMO)" → 구조화
        earnings = _parse_earnings(text)

        if not earnings:
            logger.info("[Earnings] 파싱 결과 없음")
            return _empty_result()

        # 표시 라인 생성
        items = [f"{e['company']}({e['time']})" for e in earnings]
        tweet_line = f"📅 오늘 실적: {', '.join(items)}"
        tg_line = f"📅 <b>오늘 실적</b>: {', '.join(items)}"

        # ── C-17: 빅테크 해당 종목 필터 ──
        big_tech = [e["company"] for e in earnings if e["company"] in BIG_TECH_TICKERS]
        if big_tech:
            logger.info(f"[Earnings] 🎯 빅테크 실적 감지: {', '.join(big_tech)}")

        logger.info(f"[Earnings] {len(earnings)}개 감지: {', '.join(items)}")

        return {
            "success": True,
            "earnings": earnings,
            "tweet_line": tweet_line,
            "tg_line": tg_line,
            "big_tech": big_tech,
        }

    except Exception as e:
        logger.warning(f"[Earnings] 실패 (무시): {e}")
        return _empty_result()


def check_big_tech_earnings(earnings_result: dict = None) -> list:
    """
    C-17: 오늘 실적 발표 기업 중 빅테크 해당 종목 반환.

    Args:
        earnings_result: get_today_earnings() 결과 (None이면 내부에서 호출)

    Returns:
        ["AAPL", "NVDA"] — 빅테크 해당 티커 리스트 (없으면 [])
    """
    if earnings_result is None:
        earnings_result = get_today_earnings()

    return earnings_result.get("big_tech", [])


def _parse_earnings(text: str) -> list:
    """Gemini 응답 텍스트에서 TICKER(BMO/AMC) 파싱"""
    # TICKER(BMO) 또는 TICKER(AMC) 또는 TICKER (BMO) 패턴
    pattern = r'([A-Z]{1,5})\s*\((BMO|AMC|장전|장후)\)'
    matches = re.findall(pattern, text.upper())

    earnings = []
    seen = set()
    for ticker, time_str in matches:
        if ticker in seen:
            continue
        seen.add(ticker)
        # 한국어 → 영어 변환
        t = "BMO" if time_str in ("BMO", "장전") else "AMC"
        earnings.append({"company": ticker, "time": t})

    return earnings[:5]  # 최대 5개


def _empty_result() -> dict:
    return {
        "success": False,
        "earnings": [],
        "tweet_line": "",
        "tg_line": "",
        "big_tech": [],
    }
