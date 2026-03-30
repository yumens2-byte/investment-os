"""
publishers/paid_report_formatter.py
======================================
유료 채널 전용 리포트 포맷

① ETF 상세 전략 리포트 — 6종 Stance/Score/이유 전체
② 포지션 사이징 가이드 — 배율 + 섹터별 비중 해설
"""


def format_paid_report(data: dict) -> str:
    """
    유료 채널 ETF 상세 전략 + 포지션 사이징 통합 리포트

    Args:
        data: core_data.json의 data 필드

    Returns:
        HTML 포맷 텔레그램 텍스트
    """
    regime   = data.get("market_regime", {}).get("market_regime", "—")
    risk     = data.get("market_regime", {}).get("market_risk_level", "—")
    signal   = data.get("trading_signal", {}).get("trading_signal", "—")
    reason   = data.get("trading_signal", {}).get("signal_reason", "")
    matrix   = data.get("trading_signal", {}).get("signal_matrix", {})
    buy      = matrix.get("buy_watch", [])
    hold     = matrix.get("hold", [])
    reduce   = matrix.get("reduce", [])

    stance   = data.get("etf_strategy", {}).get("stance", {})
    str_r    = data.get("etf_strategy", {}).get("strategy_reason", {})
    alloc    = data.get("etf_allocation", {}).get("allocation", {})
    timing   = data.get("etf_analysis", {}).get("timing_signal", {})
    etf_rank = data.get("etf_analysis", {}).get("etf_rank", {})

    prisk    = data.get("portfolio_risk", {})
    sizing   = prisk.get("position_sizing_multiplier", 1.0)
    hedge    = prisk.get("hedge_intensity", "—")
    exposure = prisk.get("position_exposure", "—")
    crash    = prisk.get("crash_alert_level", "—")

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴", "SELL": "🔴"}
    STANCE_EMOJI = {
        "Overweight": "📈", "Underweight": "📉",
        "Neutral": "➡️", "Hedge": "🛡️"
    }
    TIMING_EMOJI = {
        "BUY": "🟢", "ADD ON PULLBACK": "🔵",
        "HOLD": "🟡", "REDUCE": "🔴", "SELL": "🔴"
    }
    RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}

    sig_e  = SIGNAL_EMOJI.get(signal, "⚪")
    risk_e = RISK_EMOJI.get(risk, "⚪")

    ETFS = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]

    lines = [
        "💎 <b>[PREMIUM] ETF 상세 전략 리포트</b>",
        "",
        f"{risk_e} Regime: <b>{regime}</b>  |  Risk: <b>{risk}</b>",
        f"{sig_e} 종합 시그널: <b>{signal}</b>",
        "",
        "─────────────────────────",
        "📊 <b>ETF별 상세 전략</b>",
        "",
    ]

    # ETF별 상세
    ranked = sorted(etf_rank.items(), key=lambda x: x[1]) if etf_rank else []
    rank_map = {e: r for e, r in ranked}

    for etf in ETFS:
        s   = stance.get(etf, "Neutral")
        t   = timing.get(etf, "HOLD")
        pct = alloc.get(etf, 0)
        r   = str_r.get(etf, "")
        pos = rank_map.get(etf, "—")
        st_e = STANCE_EMOJI.get(s, "➡️")
        ti_e = TIMING_EMOJI.get(t, "🟡")

        lines.append(
            f"{st_e} <b>{etf}</b>  {pos}위  |  배분: <b>{pct}%</b>"
        )
        lines.append(f"   {ti_e} {t}  |  {s}")
        if r:
            lines.append(f"   <i>{r[:40]}</i>")
        lines.append("")

    lines += [
        "─────────────────────────",
        "⚖️ <b>포지션 사이징 가이드</b>",
        "",
        f"📐 배율: <b>{sizing:.2f}×</b>",
    ]

    # 배율 해설
    if sizing >= 1.0:
        lines.append("   → 풀 포지션 — 공격적 운영 가능")
    elif sizing >= 0.8:
        lines.append("   → 약간 축소 — 리스크 모니터링")
    elif sizing >= 0.6:
        lines.append("   → 보수적 운영 — 방어 우선")
    else:
        lines.append("   → 최소 포지션 — 현금 비중 확대")

    lines += [
        f"🛡️ 헤지 강도: <b>{hedge}</b>",
        f"⚡ 베타 노출: <b>{exposure}</b>",
        f"🚨 Crash Alert: <b>{crash}</b>",
        "",
    ]

    # BUY/HOLD/REDUCE 요약
    if buy:
        lines.append(f"🟢 집중 매수: <b>{' · '.join(buy)}</b>")
    if hold:
        lines.append(f"🟡 유지: {' · '.join(hold)}")
    if reduce:
        lines.append(f"🔴 축소: {' · '.join(reduce)}")
    if reason:
        lines.append(f"\n<i>{reason}</i>")

    lines += [
        "",
        "#ETF #프리미엄 #포지션사이징 #ETF전략",
    ]

    return "\n".join(lines)
