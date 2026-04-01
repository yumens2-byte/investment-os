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


# ──────────────────────────────────────────────────────────────
# B-5: ETF 랭킹 프리미엄 (2026-04-01 추가)
# ──────────────────────────────────────────────────────────────

def format_etf_rank_premium(
    rank_change: dict,
    signal_diff_result: dict = None,
    regime: str = "",
    risk_level: str = "",
    trading_signal: str = "",
) -> str:
    """
    [B-5] ETF 랭킹 변화 — 유료 채널 프리미엄 포맷
    L2(Top1 변경) 시에만 발송.
    전체 랭킹 + 원인 분석 + 전략 제안 포함.
    """
    top1_changed = rank_change.get("top1_changed", False)
    old_top1 = rank_change.get("old_top1", "—")
    new_top1 = rank_change.get("new_top1", "—")
    moved_up = rank_change.get("moved_up", [])
    moved_down = rank_change.get("moved_down", [])
    new_rank = rank_change.get("new_rank", {})

    lines = ["📊 <b>[PREMIUM] ETF 랭킹 전환</b>", ""]

    # 1위 교체
    if top1_changed:
        lines.append(f"👑 1위 교체: <b>{old_top1}</b> → <b>{new_top1}</b>")
        lines.append("")

    # 전체 랭킹 (메달 표시)
    MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines.append("📊 <b>현재 랭킹</b>")
    for etf, pos in sorted(new_rank.items(), key=lambda x: x[1]):
        medal = MEDAL.get(pos, f"{pos}위")
        up_tag = " ▲" if any(x["etf"] == etf for x in moved_up) else ""
        dn_tag = " ▼" if any(x["etf"] == etf for x in moved_down) else ""
        lines.append(f"{medal} {etf}{up_tag}{dn_tag}")

    # 원인 분석 (signal_diff)
    if signal_diff_result and signal_diff_result.get("top_movers"):
        lines.append("")
        lines.append("🔍 <b>원인 분석</b>")
        for m in signal_diff_result["top_movers"][:3]:
            direction = "↑" if m["change"] > 0 else "↓"
            state = f" {m['state']}" if m.get("state") else ""
            change_str = f" ({m['change']:+d})" if isinstance(m.get("change"), (int, float)) else ""
            lines.append(f"• {m['label_kr']}{state} {direction}{change_str}")

    # 레짐/리스크 정보
    if regime or risk_level:
        lines.append("")
        parts = []
        if regime:
            parts.append(f"Regime: <b>{regime}</b>")
        if risk_level:
            parts.append(f"Risk: <b>{risk_level}</b>")
        if trading_signal:
            parts.append(f"Signal: <b>{trading_signal}</b>")
        lines.append("⚡ " + " | ".join(parts))

    # 전략 제안
    lines.append("")
    if new_top1 in ("TLT", "ITA", "SPYM"):
        lines.append("📌 방어자산 중심 전환 시점 — 리밸런싱 검토")
    elif new_top1 in ("QQQM", "XLK"):
        lines.append("📌 성장주 복귀 — 공격적 포지션 재진입 검토")
    elif new_top1 == "XLE":
        lines.append("📌 에너지 섹터 강세 — 원자재 익스포저 확대 검토")
    else:
        lines.append("📌 포트폴리오 리밸런싱 검토 시점")

    lines.extend(["", "#ETF #프리미엄 #랭킹전환"])
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# B-6: 레짐 전환 프리미엄 v2 (2026-04-01 추가)
# ──────────────────────────────────────────────────────────────

def format_regime_change_premium_v2(
    regime_change: dict,
    signal_diff_result: dict = None,
    score_diff_result: dict = None,
    trading_signal: str = "",
    etf_top1: str = "",
    etf_hints: list = None,
    avoid_etfs: list = None,
) -> str:
    """
    [B-6] 레짐 전환 — 유료 채널 프리미엄 포맷 v2
    L2(danger/Shock) 시에만 발송.
    Score 변화 + 시그널 원인 + ETF 전략 전환 포함.
    기존 format_regime_change_premium()의 고도화 버전.
    """
    old_r = regime_change.get("old_regime", "—")
    new_r = regime_change.get("new_regime", "—")
    old_risk = regime_change.get("old_risk_level", "—")
    new_risk = regime_change.get("new_risk_level", "—")
    direction = regime_change.get("direction", "danger")

    dir_emoji = "🔴" if direction == "danger" else "🟢"

    lines = [
        f"⚡ <b>[PREMIUM] 레짐 전환 감지</b>",
        "",
        f"🔄 <b>{old_r}</b>  →  <b>{new_r}</b>",
        f"Risk Level: <b>{old_risk}</b> → <b>{new_risk}</b>",
    ]

    # Score 변화 상세
    if score_diff_result:
        score_lines = []
        _SCORE_LABELS = {
            "growth_score": "Growth",
            "inflation_score": "Inflation",
            "liquidity_score": "Liquidity",
            "risk_score": "Risk",
            "financial_stability_score": "Stability",
            "commodity_pressure_score": "Commodity",
        }
        for key in ["growth_score", "inflation_score", "liquidity_score",
                     "risk_score", "financial_stability_score", "commodity_pressure_score"]:
            sd = score_diff_result.get(key, {})
            if sd and sd.get("change", 0) != 0:
                label = _SCORE_LABELS.get(key, key)
                direction_str = "↑" if sd["change"] > 0 else "↓"
                score_lines.append(
                    f"• {label}: {sd['old']} → {sd['new']} "
                    f"({sd['change']:+d}) {direction_str}"
                )
        if score_lines:
            lines.append("")
            lines.append("📊 <b>Score 변화</b>")
            lines.extend(score_lines[:5])

    # 시그널 원인 Top 3
    if signal_diff_result and signal_diff_result.get("top_movers"):
        lines.append("")
        lines.append("🔍 <b>원인 시그널 Top 3</b>")
        for m in signal_diff_result["top_movers"][:3]:
            direction_str = "↑" if m["change"] > 0 else "↓"
            state = f" {m['state']}" if m.get("state") else ""
            lines.append(f"• {m['label_kr']}{state} {direction_str}")

    # ETF 전략 전환 가이드
    hints = etf_hints or []
    avoids = avoid_etfs or []
    if hints or avoids:
        lines.append("")
        lines.append("📈 <b>ETF 전략 전환</b>")
        if hints:
            lines.append(f"• 주목: <b>{' · '.join(hints[:3])}</b>")
        if avoids:
            lines.append(f"• 회피: <b>{' · '.join(avoids[:3])}</b>")

    # Trading Signal + ETF 1위
    footer_parts = []
    if etf_top1:
        footer_parts.append(f"ETF 1위: <b>{etf_top1}</b>")
    if trading_signal:
        SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴", "HEDGE": "🛡️"}
        sig_e = SIGNAL_EMOJI.get(trading_signal, "⚪")
        footer_parts.append(f"Signal: {sig_e} <b>{trading_signal}</b>")
    if footer_parts:
        lines.append("")
        lines.append(" | ".join(footer_parts))

    lines.append("")
    lines.append("📌 포트폴리오 리밸런싱 검토 시점")
    lines.extend(["", "#레짐전환 #프리미엄 #ETF전략"])
    return "\n".join(lines)
