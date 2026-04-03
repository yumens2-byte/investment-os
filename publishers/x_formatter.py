"""
publishers/x_formatter.py
JSON Core Data → X 트윗 텍스트 변환.
출력 형식은 project-summary.html 기준.
"""
import logging
from typing import Optional
from config.settings import X_MAX_TWEET_LENGTH, X_HASHTAGS

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 이모지 매핑
# ──────────────────────────────────────────────────────────────

_REGIME_EMOJI = {
    "Risk-On": "🟢",
    "Risk-Off": "🔴",
    "Oil Shock": "🛢️",
    "Liquidity Crisis": "💧",
    "Recession Risk": "📉",
    "Stagflation Risk": "🔥",
    "AI Bubble": "🤖",
    "Transition": "🔄",
}

_RISK_EMOJI = {
    "LOW": "🟢",
    "MEDIUM": "🟡",
    "HIGH": "🔴",
}

_SIGNAL_EMOJI = {
    "BUY": "🚀",
    "ADD": "📈",
    "HOLD": "⏸️",
    "REDUCE": "⚠️",
    "HEDGE": "🛡️",
    "SELL": "🚨",
}

_STRATEGY_MAP = {
    "Risk-On": "공격 가능 구간",
    "Risk-Off": "방어 우선",
    "Oil Shock": "에너지/방산 중심",
    "Liquidity Crisis": "채권·현금 우선",
    "Recession Risk": "방어 포지션",
    "Stagflation Risk": "에너지·실물 헷지",
    "AI Bubble": "성장주 주도",
    "Transition": "선택적 접근",
}

_STRATEGY_QUOTE = {
    "Risk-On": "공격 개시 구간",
    "Risk-Off": "공격 금지 구간",
    "Oil Shock": "에너지 섹터 우선",
    "Liquidity Crisis": "현금이 왕",
    "Recession Risk": "방어가 최선의 공격",
    "Stagflation Risk": "실물 자산을 주목",
    "AI Bubble": "모멘텀 따라가되 출구 확인",
    "Transition": "섣부른 베팅 금지",
}


# ──────────────────────────────────────────────────────────────
# 포맷 생성
# ──────────────────────────────────────────────────────────────

def _format_change(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def format_market_snapshot_tweet(data: dict, session_label: str = "Market Snapshot") -> str:
    """
    project-summary.html 기준 출력 형식.

    📊 Market Snapshot (미국 시간 표기)
    📉 SPY -1.2% | VIX +8%
    🧭 Risk-Off
    🎯 방어 우선
    ⚡ "공격 금지 구간"
    #ETF #투자 #미국증시
    """
    snap = data.get("market_snapshot", {})
    regime_info = data.get("market_regime", {})
    trading_info = data.get("trading_signal", {})

    regime = regime_info.get("market_regime", "Unknown")
    risk_level = regime_info.get("market_risk_level", "MEDIUM")
    trading_signal = trading_info.get("trading_signal", "HOLD")

    sp500 = snap.get("sp500", 0.0)
    vix = snap.get("vix", 20.0)
    us10y = snap.get("us10y", 4.0)
    oil = snap.get("oil", 75.0)

    regime_emoji = _REGIME_EMOJI.get(regime, "📊")
    risk_emoji = _RISK_EMOJI.get(risk_level, "🟡")
    signal_emoji = _SIGNAL_EMOJI.get(trading_signal, "⏸️")

    strategy = _STRATEGY_MAP.get(regime, "시장 상황 판단 중")
    quote = _STRATEGY_QUOTE.get(regime, "신중한 접근 필요")

    lines = [
        f"📊 {session_label}",
        f"",
        f"{'📈' if sp500 >= 0 else '📉'} SPY {_format_change(sp500)} | VIX {vix:.1f} | US10Y {us10y:.2f}%",
        f"🛢️ WTI ${oil:.1f}",
        f"",
        f"{regime_emoji} {regime}",
        f"{risk_emoji} Risk {risk_level}",
        f"🎯 {strategy}",
        f"{signal_emoji} \"{quote}\"",
        f"",
        f"{X_HASHTAGS}",
    ]

    tweet = "\n".join(lines)

    # 길이 초과 시 요약 버전
    if len(tweet) > X_MAX_TWEET_LENGTH:
        tweet = _format_compact(data, session_label)

    return tweet


def _format_compact(data: dict, session_label: str) -> str:
    """280자 초과 시 compact 포맷"""
    snap = data.get("market_snapshot", {})
    regime = data.get("market_regime", {}).get("market_regime", "Unknown")
    risk = data.get("market_regime", {}).get("market_risk_level", "MED")
    signal = data.get("trading_signal", {}).get("trading_signal", "HOLD")

    sp500 = snap.get("sp500", 0.0)
    vix = snap.get("vix", 20.0)

    emoji = _REGIME_EMOJI.get(regime, "📊")
    quote = _STRATEGY_QUOTE.get(regime, "신중")

    lines = [
        f"📊 {session_label}",
        f"SPY {_format_change(sp500)} | VIX {vix:.1f} | {risk}",
        f"{emoji} {regime}",
        f"⚡ \"{quote}\"",
        X_HASHTAGS,
    ]
    return "\n".join(lines)


def format_thread_posts(data: dict) -> list:
    """
    X 쓰레드 형식 (1~N 포스트).
    scoreboard + why + allocation 포함.
    """
    snap = data.get("market_snapshot", {})
    regime = data.get("market_regime", {})
    etf_rank = data.get("etf_analysis", {}).get("etf_rank", {})
    signal_matrix = data.get("trading_signal", {}).get("signal_matrix", {})
    alloc = data.get("etf_allocation", {}).get("allocation", {})
    summary = data.get("output_helpers", {}).get("one_line_summary", "")

    # Top ETF
    top3 = [k for k, _ in sorted(etf_rank.items(), key=lambda x: x[1])[:3]]

    sp500 = snap.get("sp500", 0.0)
    nasdaq = snap.get("nasdaq", 0.0)
    vix = snap.get("vix", 20.0)

    posts = [
        # [1] 요약
        f"📊 Investment OS — {summary}\n\n{X_HASHTAGS}",
        # [2] 레짐
        f"📍 시장 레짐: {regime.get('market_regime')} | 리스크: {regime.get('market_risk_level')}\n\n💬 {regime.get('regime_reason', '')}",
        # [3] 스냅샷
        f"📈 시장 현황\nSPY {_format_change(sp500)} | Nasdaq {_format_change(nasdaq)} | VIX {vix:.1f}",
        # [4] ETF 리더십
        f"🏆 ETF 순위\n1위: {top3[0] if len(top3) > 0 else '-'} | 2위: {top3[1] if len(top3) > 1 else '-'} | 3위: {top3[2] if len(top3) > 2 else '-'}",
        # [5] 시그널
        f"🎯 Buy/Add: {', '.join(signal_matrix.get('buy_watch', []))}\n⏸️ Hold: {', '.join(signal_matrix.get('hold', []))}\n⚠️ Reduce: {', '.join(signal_matrix.get('reduce', []))}",
        # [6] 배분
        f"💼 포트폴리오 배분\n" + " | ".join([f"{k} {v}%" for k, v in alloc.items()]),
    ]

    # 각 포스트 길이 제한 체크
    validated = []
    for post in posts:
        if len(post) > X_MAX_TWEET_LENGTH:
            post = post[:X_MAX_TWEET_LENGTH - 3] + "..."
        validated.append(post)

    return validated


# ── 세션별 해시태그 ──────────────────────────────────────────
SESSION_TAGS = {
    "morning":  "#아침시장",
    "intraday": "#장중",
    "close":    "#마감",
    "weekly":   "#주간분석",
}
REGIME_TAGS = {
    "Risk-On":          "#RiskOn #성장주",
    "Risk-Off":         "#RiskOff #방어",
    "Oil Shock":        "#OilShock #에너지",
    "Recession Risk":   "#경기침체 #Recession",
    "Stagflation Risk": "#스태그플레이션",
    "Liquidity Crisis": "#유동성위기",
    "Crisis Regime":    "#위기경보 #Crisis",
    "Transition":       "#전환구간",
}


def format_image_tweet(data: dict, session: str = "morning") -> str:
    """
    이미지 첨부 시 간결한 트윗 텍스트 생성 (60~80자).
    이미지가 상세 정보를 담으므로 텍스트는 핵심만.

    Returns: 트윗 텍스트 (280자 이내)
    """
    snap        = data.get("market_snapshot", {})
    regime      = data.get("market_regime", {})
    signal_data = data.get("trading_signal", {})
    helpers     = data.get("output_helpers", {})

    regime_name = regime.get("market_regime", "")
    risk_level  = regime.get("market_risk_level", "")
    signal      = signal_data.get("trading_signal", "HOLD")
    sp500       = snap.get("sp500", 0) or 0
    vix         = snap.get("vix", 0) or 0
    oil         = snap.get("oil", 0) or 0
    summary     = helpers.get("one_line_summary", "")[:50]

    session_labels = {
        "morning":  "Morning Brief",
        "intraday": "Intraday Briefing",
        "close":    "Close Summary",
        "weekly":   "Weekly Review",
    }
    session_lbl = session_labels.get(session, "Market Snapshot")

    # 시그널 이모지
    sig_emoji = {"BUY": "🟢", "ADD": "🟢", "HOLD": "🟡",
                 "REDUCE": "🟠", "HEDGE": "🔵", "SELL": "🔴"}.get(signal, "🟡")
    trend_emoji = "📈" if sp500 >= 0 else "📉"

    # 라인 구성
    line1 = f"📊 {session_lbl}  |  {regime_name}"
    line2 = f"{trend_emoji} SPY {sp500:+.2f}%  |  VIX {vix:.1f}  |  WTI ${oil:.1f}"
    line3 = f"{sig_emoji} SIGNAL: {signal}"
    line4 = summary

    # Fear & Greed (morning 세션만 추가)
    fg_line = ""
    if session == "morning":
        fg = data.get("fear_greed", {})
        if fg and fg.get("value"):
            fg_emoji = fg.get("emoji", "😐")
            fg_val   = fg.get("value", 0)
            fg_lbl   = fg.get("label", "")
            fg_chg   = fg.get("change", 0)
            chg_str  = f" ({fg_chg:+d})" if fg_chg else ""
            fg_line  = f"{fg_emoji} F&G: {fg_val}/100 {fg_lbl}{chg_str}"

    # 해시태그
    base_tags    = "#ETF #투자 #미국증시"
    regime_tag   = REGIME_TAGS.get(regime_name, "")
    session_tag  = SESSION_TAGS.get(session, "")
    tags = f"{base_tags} {regime_tag} {session_tag}".strip()

    body = f"{line1}\n\n{line2}\n{line3}"
    if fg_line:
        body += f"\n{fg_line}"
    if line4:
        body += f"\n{line4}"
    tweet = f"{body}\n\n{tags}"
    return tweet[:280]


# ──────────────────────────────────────────────────────────────
# C-1: AI 트윗 생성 (Gemini)
# ──────────────────────────────────────────────────────────────

def generate_ai_tweet(data: dict, session_label: str = "Market Snapshot") -> str:
    """
    Gemini로 매 세션 다른 톤/표현의 트윗 생성.
    실패 시 기존 하드코딩 트윗 fallback.

    Returns: 트윗 텍스트 (280자 이내)
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return format_market_snapshot_tweet(data, session_label)

        snap = data.get("market_snapshot", {})
        regime_info = data.get("market_regime", {})
        trading_info = data.get("trading_signal", {})
        alloc = data.get("etf_allocation", {})
        fg = data.get("fear_greed", {})

        sp500 = snap.get("sp500", 0.0)
        vix = snap.get("vix", 20.0)
        oil = snap.get("oil", 75.0)
        us10y = snap.get("us10y", 4.0)
        regime = regime_info.get("market_regime", "Unknown")
        risk = regime_info.get("market_risk_level", "MEDIUM")
        signal = trading_info.get("trading_signal", "HOLD")
        fg_val = fg.get("value", 50) if fg else 50
        fg_label = fg.get("label", "") if fg else ""

        # ETF Top3
        top3 = []
        if alloc:
            sorted_etfs = sorted(alloc.items(), key=lambda x: x[1], reverse=True)
            top3 = [f"{e}({w}%)" for e, w in sorted_etfs[:3]]

        prompt = (
            f"투자 분석 X 트윗을 작성해줘. 캐릭터 Max Bullhorn(강세), Baron Bearsworth(약세)를 활용.\n"
            f"- 세션: {session_label}\n"
            f"- SPY: {sp500:+.2f}%, VIX: {vix:.1f}, WTI: ${oil:.1f}, US10Y: {us10y:.2f}%\n"
            f"- 레짐: {regime}, 리스크: {risk}, 시그널: {signal}\n"
            f"- F&G: {fg_val} ({fg_label})\n"
            f"- Top ETF: {', '.join(top3)}\n"
            f"조건:\n"
            f"- 140~200자, 한국어\n"
            f"- 이모지 2~3개 포함\n"
            f"- 매번 다른 톤으로 (진지한/유머러스/긴급/여유로운 중 랜덤)\n"
            f"- 해시태그 3개 포함 (#ETF #투자 + 레짐태그)\n"
            f"- 트윗 텍스트만 출력, 설명 없이\n"
        )

        result = call(prompt=prompt, model="flash-lite", max_tokens=200, temperature=0.9)

        if result.get("success"):
            ai_tweet = result["text"].strip()
            # 따옴표 제거
            ai_tweet = ai_tweet.strip('"').strip("'").strip("`")
            if 50 < len(ai_tweet) <= 280:
                logger.info(f"[XFormatter] AI 트윗 생성 ({len(ai_tweet)}자) | source=gemini")
                return ai_tweet
            else:
                logger.warning(f"[XFormatter] AI 트윗 길이 부적합 ({len(ai_tweet)}자) → fallback")

    except Exception as e:
        logger.warning(f"[XFormatter] AI 트윗 생성 실패 → fallback: {e}")

    return format_market_snapshot_tweet(data, session_label)


# ──────────────────────────────────────────────────────────────
# C-12: AI 스레드 자동 생성 (2026-04-03)
# ──────────────────────────────────────────────────────────────

def generate_ai_thread(data: dict) -> list:
    """
    Gemini로 5~7개 포스트 X 스레드 자동 생성.
    실패 시 기존 하드코딩 스레드 fallback.

    Returns: list[str] — 각 포스트 텍스트
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return format_thread_posts(data)

        snap = data.get("market_snapshot", {})
        regime_info = data.get("market_regime", {})
        trading_info = data.get("trading_signal", {})
        alloc = data.get("etf_allocation", {})
        news = data.get("news_analysis", {})
        signals_data = data.get("signals", {})

        sp500 = snap.get("sp500", 0.0)
        vix = snap.get("vix", 20.0)
        oil = snap.get("oil", 75.0)
        regime = regime_info.get("market_regime", "Unknown")
        risk = regime_info.get("market_risk_level", "MEDIUM")
        signal = trading_info.get("trading_signal", "HOLD")

        # ETF 배분
        alloc_str = ", ".join(f"{e}:{w}%" for e, w in alloc.items()) if alloc else "없음"

        # 뉴스 Top3
        top_issues = news.get("top_issues", [])
        issues_str = ", ".join(
            iss.get("topic", "") for iss in top_issues[:3] if isinstance(iss, dict)
        ) if top_issues else "특이사항 없음"

        prompt = (
            f"투자 분석 X 스레드를 JSON 배열로 생성해줘.\n"
            f"캐릭터: Max Bullhorn(강세), Baron Bearsworth(약세), The Volatician(혼돈)\n\n"
            f"데이터:\n"
            f"- SPY: {sp500:+.2f}%, VIX: {vix:.1f}, WTI: ${oil:.1f}\n"
            f"- 레짐: {regime}, 리스크: {risk}, 시그널: {signal}\n"
            f"- ETF 배분: {alloc_str}\n"
            f"- 주요 뉴스: {issues_str}\n\n"
            f"구조 (5~7개):\n"
            f"1. 후킹 요약 (1줄 + 이모지)\n"
            f"2. 오늘 시장 무슨 일? (레짐/수치)\n"
            f"3. 왜 이런 일이? (뉴스 배경)\n"
            f"4. ETF 전략 (어디에 투자?)\n"
            f"5. 리스크 체크 (주의점)\n"
            f"6. 내일 전망\n"
            f"7. 마무리 + CTA\n\n"
            f"조건:\n"
            f"- 각 포스트 200자 이내, 한국어\n"
            f"- 이모지 포함\n"
            f"- JSON 배열로만 반환: [{{\"post\": \"...\"}}, ...]\n"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=1000,
            temperature=0.8,
            response_json=True,
        )

        if result.get("success") and result.get("data"):
            parsed = result["data"]
            # list[dict] → list[str]
            if isinstance(parsed, list):
                posts = []
                for item in parsed:
                    if isinstance(item, dict):
                        post = item.get("post", "")
                    elif isinstance(item, str):
                        post = item
                    else:
                        continue
                    post = post.strip().strip('"').strip("'")
                    if 10 < len(post) <= 280:
                        posts.append(post)

                if len(posts) >= 3:
                    logger.info(
                        f"[XFormatter] AI 스레드 생성 ({len(posts)}개) | source=gemini"
                    )
                    return posts

            logger.warning("[XFormatter] AI 스레드 파싱 실패 → fallback")

    except Exception as e:
        logger.warning(f"[XFormatter] AI 스레드 생성 실패 → fallback: {e}")

    return format_thread_posts(data)
