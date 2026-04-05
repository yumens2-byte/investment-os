"""
engines/stock_analyzer.py (C-17)
==================================
빅테크 실적 시즌 단일종목 실적 팩트 요약.

D-3 earnings_checker.py가 빅테크 실적 발표를 감지하면
yfinance + Gemini로 실적 팩트 트윗 1개 생성.

대상: AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA (7종목)
원칙: 팩트 전달만 — 매수/매도 의견 절대 불포함
RPD: +1 (실적 발표일만, 연 ~20일)

VERSION = "1.0.0"
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ── 빅테크 7종목 ──
BIG_TECH_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]

# ── 기업명 매핑 ──
COMPANY_NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
    "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta",
    "TSLA": "Tesla",
}

# ── Gemini 프롬프트 ──
EARNINGS_PROMPT = """다음은 {company}({ticker})의 최신 실적 발표 관련 정보입니다.

시장 데이터:
- 현재가: ${price}
- 등락률: {change}%
- 시가총액: {market_cap}
- PER: {pe_ratio}
- 52주 최고/최저: ${high_52w} / ${low_52w}

아래 형식으로 실적 요약 트윗 1개를 생성하세요.
- 280자 이내 (한국어)
- EPS/매출 서프라이즈 여부 + 가이던스 방향 + 시간외 반응 포함
- 매수/매도 의견 절대 불포함
- 마지막 줄: ⚠️ 투자 참고 정보, 투자 권유 아님

트윗 형식 예시:
📊 NVDA 실적 발표 요약

EPS: $0.82 (예상 $0.75, +9.3% 서프라이즈)
매출: $35.1B (예상 $33.2B, +5.7%)
가이던스: 다음 분기 $37B (예상 상회)

시간외: +4.2%

⚠️ 투자 참고 정보, 투자 권유 아님

트윗만 출력. 따옴표/설명/마크다운 없이.
"""


def analyze_big_tech_earnings(tickers: list) -> list:
    """
    빅테크 종목 실적 요약 트윗 생성.

    Args:
        tickers: 빅테크 해당 티커 리스트 (예: ["NVDA", "AAPL"])

    Returns:
        [
            {
                "success": True,
                "ticker": "NVDA",
                "company": "NVIDIA",
                "tweet": "📊 NVDA 실적 발표 요약\n...",
                "tg_text": "📊 <b>NVDA 실적 발표 요약</b>\n...",
            },
            ...
        ]
    """
    if not tickers:
        return []

    results = []
    for ticker in tickers:
        if ticker not in BIG_TECH_TICKERS:
            continue

        try:
            result = _analyze_single(ticker)
            if result.get("success"):
                results.append(result)
                logger.info(f"[StockAnalyzer] {ticker} 실적 요약 완료")
            else:
                logger.warning(f"[StockAnalyzer] {ticker} 분석 실패: {result.get('error', '?')}")
        except Exception as e:
            logger.warning(f"[StockAnalyzer] {ticker} 예외: {e}")

    logger.info(f"[StockAnalyzer] 빅테크 실적 분석 완료: {len(results)}/{len(tickers)}건")
    return results


def _analyze_single(ticker: str) -> dict:
    """단일 종목 yfinance 데이터 수집 + Gemini 실적 요약"""
    company = COMPANY_NAMES.get(ticker, ticker)

    # ── 1. yfinance 데이터 수집 ──
    stock_data = _fetch_stock_data(ticker)
    if not stock_data:
        return {"success": False, "ticker": ticker, "error": "yfinance 데이터 수집 실패"}

    # ── 2. Gemini 실적 요약 트윗 생성 ──
    tweet = _generate_earnings_tweet(ticker, company, stock_data)
    if not tweet:
        # Gemini 실패 시 fallback 트윗
        tweet = _fallback_tweet(ticker, company, stock_data)

    # ── 3. TG 포맷 생성 ──
    tg_text = tweet.replace(
        f"📊 {ticker} 실적 발표 요약",
        f"📊 <b>{ticker} 실적 발표 요약</b>"
    )

    return {
        "success": True,
        "ticker": ticker,
        "company": company,
        "tweet": tweet,
        "tg_text": tg_text,
        "stock_data": stock_data,
    }


def _fetch_stock_data(ticker: str) -> dict | None:
    """yfinance로 종목 데이터 수집"""
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        info = stock.info

        if not info or not info.get("currentPrice"):
            # requests fallback 시도
            logger.warning(f"[StockAnalyzer] {ticker} yfinance info 없음")
            return None

        price = info.get("currentPrice", 0)
        prev_close = info.get("previousClose", price)
        change_pct = round(((price - prev_close) / prev_close) * 100, 2) if prev_close else 0

        # 시가총액 포맷 (B/T)
        market_cap_raw = info.get("marketCap", 0)
        if market_cap_raw >= 1e12:
            market_cap = f"${market_cap_raw / 1e12:.2f}T"
        elif market_cap_raw >= 1e9:
            market_cap = f"${market_cap_raw / 1e9:.1f}B"
        else:
            market_cap = f"${market_cap_raw / 1e6:.0f}M"

        pe_ratio = info.get("trailingPE") or info.get("forwardPE")
        pe_str = f"{pe_ratio:.1f}" if pe_ratio else "N/A"

        data = {
            "price": round(price, 2),
            "change": change_pct,
            "market_cap": market_cap,
            "pe_ratio": pe_str,
            "high_52w": round(info.get("fiftyTwoWeekHigh", 0), 2),
            "low_52w": round(info.get("fiftyTwoWeekLow", 0), 2),
        }

        logger.info(f"[StockAnalyzer] {ticker} 데이터 수집: ${price} ({change_pct:+.2f}%)")
        return data

    except Exception as e:
        logger.warning(f"[StockAnalyzer] {ticker} yfinance 실패: {e}")
        return None


def _generate_earnings_tweet(ticker: str, company: str, stock_data: dict) -> str | None:
    """Gemini로 실적 요약 트윗 생성"""
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return None

        prompt = EARNINGS_PROMPT.format(
            company=company,
            ticker=ticker,
            price=stock_data["price"],
            change=stock_data["change"],
            market_cap=stock_data["market_cap"],
            pe_ratio=stock_data["pe_ratio"],
            high_52w=stock_data["high_52w"],
            low_52w=stock_data["low_52w"],
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=500,
            temperature=0.3,
        )

        if result.get("success") and result.get("text"):
            tweet = result["text"].strip().strip("'\"")

            # 280자 초과 시 축약
            if len(tweet) > 280:
                tweet = tweet[:277] + "..."

            # 면책조항 없으면 추가
            if "투자 권유 아님" not in tweet:
                tweet = tweet.rstrip() + "\n\n⚠️ 투자 참고 정보, 투자 권유 아님"
                if len(tweet) > 280:
                    tweet = tweet[:277] + "..."

            logger.info(f"[StockAnalyzer] {ticker} Gemini 트윗 생성 ({len(tweet)}자)")
            return tweet

    except Exception as e:
        logger.warning(f"[StockAnalyzer] {ticker} Gemini 실패: {e}")

    return None


def _fallback_tweet(ticker: str, company: str, stock_data: dict) -> str:
    """Gemini 실패 시 rule-based fallback 트윗"""
    change_emoji = "📈" if stock_data["change"] >= 0 else "📉"

    tweet = (
        f"📊 {ticker} 실적 발표 요약\n\n"
        f"{change_emoji} 현재가: ${stock_data['price']} ({stock_data['change']:+.2f}%)\n"
        f"시총: {stock_data['market_cap']} | PER: {stock_data['pe_ratio']}\n"
        f"52주: ${stock_data['low_52w']} ~ ${stock_data['high_52w']}\n\n"
        f"⚠️ 투자 참고 정보, 투자 권유 아님"
    )

    return tweet[:280]
