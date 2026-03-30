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
