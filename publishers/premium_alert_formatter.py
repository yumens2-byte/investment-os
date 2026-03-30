"""
publishers/premium_alert_formatter.py
========================================
유료 채널 전용 알람 포맷 생성

- VIX 레벨별 세분화 (20/25/30/35)
- 레짐 전환 즉시 알람
"""

VIX_LEVELS = {
    35: ("EXTREME", "🔴", "매우 공포 구간 — 포지션 최소화"),
    30: ("FEAR",    "🟠", "공포 구간 진입 — 방어 포지션"),
    25: ("WARNING", "🟡", "경고 구간 — 포지션 축소 권고"),
    20: ("CAUTION", "🟡", "주의 구간 — 변동성 증가"),
}


def format_vix_premium(vix: float, prev_vix: float, regime: str, risk: str) -> str:
    """유료 채널 VIX 레벨별 알람 포맷"""
    level_name, emoji, action = "ELEVATED", "⚠️", "모니터링 강화"
    for threshold in sorted(VIX_LEVELS.keys(), reverse=True):
        if vix >= threshold:
            level_name, emoji, action = VIX_LEVELS[threshold]
            break

    direction = "▲" if vix > prev_vix else "▼"
    diff = abs(vix - prev_vix)

    RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}
    risk_e = RISK_EMOJI.get(risk, "⚪")

    return (
        f"🚨 <b>[PREMIUM] VIX {level_name}</b>\n"
        f"\n"
        f"📊 VIX <b>{vix:.1f}</b>  {direction}{diff:.1f}  (이전 {prev_vix:.1f})\n"
        f"{risk_e} Regime: <b>{regime}</b>  |  Risk: <b>{risk}</b>\n"
        f"\n"
        f"⚡ <b>{action}</b>\n"
        f"\n"
        f"#VIX #{level_name} #ETF #투자"
    )


def format_regime_change_premium(
    old_regime: str, new_regime: str,
    signal: str, risk: str,
    buy_watch: list
) -> str:
    """유료 채널 레짐 전환 즉시 알람 포맷"""
    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴"}
    sig_e = SIGNAL_EMOJI.get(signal, "⚪")

    buy_str = " · ".join(buy_watch) if buy_watch else "—"

    return (
        f"⚡ <b>[PREMIUM] 레짐 전환 감지</b>\n"
        f"\n"
        f"🔄 <b>{old_regime}</b>  →  <b>{new_regime}</b>\n"
        f"\n"
        f"{sig_e} 즉시 시그널: <b>{signal}</b>\n"
        f"🔍 Watch: <b>{buy_str}</b>\n"
        f"\n"
        f"📌 포트폴리오 리밸런싱 검토 시점\n"
        f"\n"
        f"#레짐전환 #ETF #투자전략"
    )
