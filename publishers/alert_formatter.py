"""
publishers/alert_formatter.py
==============================
Alert 전용 트윗 포맷 생성.
280자 이내 보장.
"""
import logging
from engines.alert_engine import AlertSignal
from config.settings import X_MAX_TWEET_LENGTH

logger = logging.getLogger(__name__)

# Alert 타입별 이모지
_TYPE_EMOJI = {
    "VIX":           "🚨",
    "SPY":           "📉",
    "OIL":           "🛢️",
    "FED_SHOCK":     "🏦",
    "CRISIS":        "🆘",
    "VIX_COUNTDOWN": "⚠️",
    "ETF_RANK":      "📊",      # B-5: ETF 랭킹 변화
    "REGIME_CHANGE": "🔄",      # B-6: 레짐 전환
}

# 등급별 이모지
_LEVEL_EMOJI = {
    "L1": "⚠️",
    "L2": "🔴",
    "L3": "🆘",
}

# Alert 타입별 제목
_TYPE_TITLE = {
    "VIX":           "VIX ALERT",
    "SPY":           "MARKET ALERT",
    "OIL":           "OIL SHOCK ALERT",
    "FED_SHOCK":     "FED SHOCK ALERT",
    "CRISIS":        "CRISIS ALERT",
    "VIX_COUNTDOWN": "VIX 카운트다운",
    "ETF_RANK":      "ETF 랭킹 전환",     # B-5
    "REGIME_CHANGE": "레짐 전환 감지",     # B-6
}

# 등급별 행동 지침
_LEVEL_ACTION = {
    "L1": "포지션 점검 권고",
    "L2": "방어 전환 검토",
    "L3": "즉각 방어 전환",
}


def format_alert_tweet(signal: AlertSignal) -> str:
    """
    AlertSignal → X 트윗 텍스트 (280자 이내).

    형식:
    {level_emoji} {type_emoji} {title}
    {reason}
    📈 주목: {etf_hints}
    ⚠️ 회피: {avoid_etfs}
    🎯 {action}
    #위험경보 #ETF #미국증시
    """
    snap = signal.snapshot
    type_emoji  = _TYPE_EMOJI.get(signal.alert_type, "🚨")
    level_emoji = _LEVEL_EMOJI.get(signal.level, "⚠️")
    title       = _TYPE_TITLE.get(signal.alert_type, "ALERT")
    action      = _LEVEL_ACTION.get(signal.level, "주의")

    spy   = snap.get("sp500", 0)
    vix   = snap.get("vix", 0)
    us10y = snap.get("us10y", 0)
    oil   = snap.get("oil", 0)

    # 스냅샷 핵심 수치 (타입별로 관련 지표 강조)
    if signal.alert_type == "OIL":
        snapshot_line = f"🛢️ WTI ${oil:.1f} | SPY {spy:+.1f}% | VIX {vix:.1f}"
    elif signal.alert_type == "FED_SHOCK":
        snapshot_line = f"📉 SPY {spy:+.1f}% | US10Y {us10y:.2f}% | VIX {vix:.1f}"
    else:
        snapshot_line = f"📉 SPY {spy:+.1f}% | VIX {vix:.1f} | US10Y {us10y:.2f}%"

    hints  = " ".join(signal.etf_hints[:3]) if signal.etf_hints else "-"
    avoids = " ".join(signal.avoid_etfs[:3]) if signal.avoid_etfs else "-"

    lines = [
        f"{level_emoji} {type_emoji} {title}",
        f"",
        snapshot_line,
        f"",
        f"📌 {signal.reason}",
        f"",
        f"📈 주목: {hints}",
        f"⚠️ 회피: {avoids}",
        f"🎯 {action}",
        f"",
        f"#위험경보 #ETF #미국증시",
    ]

    tweet = "\n".join(lines)

    # 280자 초과 시 compact
    if len(tweet) > X_MAX_TWEET_LENGTH:
        tweet = _format_compact(signal, snapshot_line)

    logger.debug(f"[AlertFormatter] {signal.alert_type}/{signal.level}: {len(tweet)}자")
    return tweet


def _format_compact(signal: AlertSignal, snapshot_line: str) -> str:
    """280자 초과 시 압축 포맷"""
    type_emoji  = _TYPE_EMOJI.get(signal.alert_type, "🚨")
    level_emoji = _LEVEL_EMOJI.get(signal.level, "⚠️")
    title       = _TYPE_TITLE.get(signal.alert_type, "ALERT")
    action      = _LEVEL_ACTION.get(signal.level, "주의")
    hints       = " ".join(signal.etf_hints[:2])

    lines = [
        f"{level_emoji} {type_emoji} {title}",
        snapshot_line,
        signal.reason[:60],
        f"주목: {hints} | {action}",
        "#위험경보 #ETF",
    ]
    return "\n".join(lines)


def format_countdown_tweet(signal) -> str:
    """
    VIX 카운트다운 전용 트윗 포맷 (투표 유도형)

    형식:
      ⚠️ VIX [N] 돌파 — 공포구간까지 [N]pt
      현재 VIX: [N] | 전일대비 [▲/▼]
      역사적으로 VIX 30 이상 → 3개월 후 SPY +9.2%
      지금 팔까요? 버틸까요? 🗳️
      #VIX #공포구간 #투자심리
    """
    vix = signal.snapshot.get("vix", 0)
    distance = max(0, 30 - vix)
    reason = signal.reason

    lines = [
        f"⚠️ {reason}",
        "",
        f"📊 역사적 데이터",
        f"→ VIX 30 이상 평균 지속: 3주",
        f"→ 이후 3개월 SPY 평균: +9.2%",
        "",
        "지금 팔까요? 버틸까요? 🗳️",
        "",
        "#VIX #공포구간 #투자심리 #ETF",
    ]
    tweet = "\n".join(lines)
    return tweet[:280]


# ──────────────────────────────────────────────────────────────
# B-5/B-6 텔레그램 상세 포맷 (2026-04-01 추가)
# ──────────────────────────────────────────────────────────────

def format_etf_rank_telegram(
    rank_change: dict,
    signal_diff_result: dict = None,
    regime: str = "",
) -> str:
    """
    [B-5] ETF 랭킹 변화 — 텔레그램 상세 포맷

    예시:
    📊 ETF 랭킹 변화 감지

    ▲ 상승: TLT (3위→1위) | ITA (5위→2위)
    ▼ 하락: QQQM (1위→4위) | XLK (2위→5위)

    주요 원인:
    • VIX Extreme ↑
    • 신흥국 EM Crisis Spillover ↑
    • 실업수당 Weak Labor ↑

    현재 Regime: Risk-Off
    """
    lines = ["📊 ETF 랭킹 변화 감지", ""]

    # 상승/하락 ETF
    moved_up = rank_change.get("moved_up", [])
    moved_down = rank_change.get("moved_down", [])

    if moved_up:
        up_str = " | ".join(
            f"{m['etf']} ({m['from']}위→{m['to']}위)" for m in moved_up
        )
        lines.append(f"▲ 상승: {up_str}")

    if moved_down:
        dn_str = " | ".join(
            f"{m['etf']} ({m['from']}위→{m['to']}위)" for m in moved_down
        )
        lines.append(f"▼ 하락: {dn_str}")

    # 원인 분석
    if signal_diff_result and signal_diff_result.get("top_movers"):
        lines.append("")
        lines.append("주요 원인:")
        for m in signal_diff_result["top_movers"][:3]:
            direction = "↑" if m["change"] > 0 else "↓"
            state = f" {m['state']}" if m.get("state") else ""
            lines.append(f"• {m['label_kr']}{state} {direction}")

    if regime:
        lines.append("")
        lines.append(f"현재 Regime: {regime}")

    return "\n".join(lines)


def format_regime_change_telegram(
    regime_change: dict,
    signal_diff_result: dict = None,
    score_diff_result: dict = None,
    trading_signal: str = "",
    etf_top1: str = "",
) -> str:
    """
    [B-6] 레짐 전환 — 텔레그램 상세 포맷

    예시:
    ⚠️ 레짐 전환 감지

    Risk-On → Risk-Off
    Risk Level: LOW → HIGH

    주요 변화 Score:
    • Growth: 2 → 4 (+2) ↑
    • Risk: 2 → 4 (+2) ↑
    • Liquidity: 2 → 3 (+1) ↑

    원인 시그널 Top 3:
    • VIX Extreme ↑
    • 신흥국 EM Crisis Spillover ↑
    • 실업수당 Weak Labor ↑

    현재 ETF 1위: TLT | Trading Signal: HEDGE
    """
    old_r = regime_change.get("old_regime", "—")
    new_r = regime_change.get("new_regime", "—")
    old_risk = regime_change.get("old_risk_level", "—")
    new_risk = regime_change.get("new_risk_level", "—")
    direction = regime_change.get("direction", "")

    dir_emoji = "🔴" if direction == "danger" else "🟢"

    lines = [
        f"{dir_emoji} 레짐 전환 감지",
        "",
        f"{old_r} → {new_r}",
        f"Risk Level: {old_risk} → {new_risk}",
    ]

    # Score 변화
    if score_diff_result:
        score_lines = []
        for key in ["growth_score", "inflation_score", "liquidity_score",
                     "risk_score", "financial_stability_score", "commodity_pressure_score"]:
            sd = score_diff_result.get(key, {})
            if sd and sd.get("change", 0) != 0:
                label = key.replace("_score", "").replace("_", " ").title()
                direction_str = "↑" if sd["change"] > 0 else "↓"
                score_lines.append(
                    f"• {label}: {sd['old']} → {sd['new']} "
                    f"({sd['change']:+d}) {direction_str}"
                )
        if score_lines:
            lines.append("")
            lines.append("주요 변화 Score:")
            lines.extend(score_lines[:4])  # 최대 4개

    # 시그널 원인
    if signal_diff_result and signal_diff_result.get("top_movers"):
        lines.append("")
        lines.append("원인 시그널 Top 3:")
        for m in signal_diff_result["top_movers"][:3]:
            direction_str = "↑" if m["change"] > 0 else "↓"
            state = f" {m['state']}" if m.get("state") else ""
            lines.append(f"• {m['label_kr']}{state} {direction_str}")

    # ETF + Trading Signal
    footer_parts = []
    if etf_top1:
        footer_parts.append(f"ETF 1위: {etf_top1}")
    if trading_signal:
        footer_parts.append(f"Signal: {trading_signal}")
    if footer_parts:
        lines.append("")
        lines.append(" | ".join(footer_parts))

    return "\n".join(lines)
