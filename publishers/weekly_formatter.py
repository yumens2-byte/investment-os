"""
publishers/weekly_formatter.py
================================
주간 성적표 X 쓰레드 + 텔레그램 포맷 생성
"""
from typing import List


def _etf_return_line(etf: str, ret: float) -> str:
    """ETF 수익률 한 줄 포맷"""
    arrow = "📈" if ret > 0 else "📉" if ret < 0 else "➡️"
    sign  = "+" if ret >= 0 else ""
    return f"{arrow} {etf}: {sign}{ret:.1f}%"


def format_weekly_thread(summary: dict) -> List[str]:
    """
    주간 성적표 X 쓰레드 포맷 (280자 이내 포스트 리스트)

    Returns:
        list of str — 각 항목이 쓰레드 1개
    """
    week   = summary.get("week", "")
    days   = summary.get("days", 0)
    regime = summary.get("dominant_regime", "—")
    signal = summary.get("dominant_signal", "—")
    sig_c  = summary.get("signal_counts", {})
    buy_c  = summary.get("buy_count", {})
    reduce_c = summary.get("reduce_count", {})
    returns  = summary.get("etf_week_return", {})

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴"}
    sig_e = SIGNAL_EMOJI.get(signal, "⚪")

    posts = []

    # ── 포스트 1: 헤더 ──────────────────────────────────────
    sig_summary = " / ".join(
        f"{k}×{v}" for k, v in sorted(sig_c.items(), key=lambda x: -x[1])
    ) if sig_c else signal

    posts.append(
        f"📊 주간 AI 시그널 성적표 ({week})\n"
        f"\n"
        f"이번 주 {days}일 분석 결과\n"
        f"{sig_e} 주도 시그널: {signal} ({sig_summary})\n"
        f"🌍 주도 레짐: {regime}\n"
        f"\n"
        f"#ETF #투자 #미국증시 #주간성적표"
    )

    # ── 포스트 2: ETF 성과 (수익률 있을 때) ────────────────
    if returns:
        lines = ["📈 이번 주 ETF 성과\n"]
        for etf, ret in sorted(returns.items(), key=lambda x: -x[1]):
            lines.append(_etf_return_line(etf, ret))
        posts.append("\n".join(lines[:8]))  # 280자 제한

    # ── 포스트 3: BUY 많이 받은 ETF ───────────────────────
    if buy_c or reduce_c:
        lines = ["🔍 이번 주 AI 판단\n"]
        if buy_c:
            top_buy = sorted(buy_c.items(), key=lambda x: -x[1])[:3]
            buy_str = " · ".join(f"{e}({d}일)" for e, d in top_buy)
            lines.append(f"🟢 BUY 집중: {buy_str}")
        if reduce_c:
            top_red = sorted(reduce_c.items(), key=lambda x: -x[1])[:3]
            red_str = " · ".join(f"{e}({d}일)" for e, d in top_red)
            lines.append(f"🔴 REDUCE 집중: {red_str}")
        lines.append("\n#AI투자 #ETF전략")
        posts.append("\n".join(lines))

    # ── 포스트 4: 다음 주 전략 ─────────────────────────────
    entries = summary.get("entries", [])
    last    = entries[-1] if entries else {}
    last_signal = last.get("signal", signal)
    last_buy    = last.get("buy_watch", [])
    last_regime = last.get("regime", regime)

    last_sig_e = SIGNAL_EMOJI.get(last_signal, "⚪")
    posts.append(
        f"📌 다음 주 전략\n"
        f"\n"
        f"{last_sig_e} Signal: {last_signal}\n"
        f"🌍 Regime: {last_regime}\n"
        + (f"🔍 Watch: {' · '.join(last_buy)}\n" if last_buy else "") +
        f"\n"
        f"매일 KST 06:30 Morning Brief 확인\n"
        f"#투자전략 #ETF"
    )

    return posts


def format_weekly_telegram(summary: dict) -> str:
    """
    주간 성적표 텔레그램 무료 채널 포맷 (HTML)
    """
    week   = summary.get("week", "")
    days   = summary.get("days", 0)
    regime = summary.get("dominant_regime", "—")
    signal = summary.get("dominant_signal", "—")
    sig_c  = summary.get("signal_counts", {})
    buy_c  = summary.get("buy_count", {})
    reduce_c = summary.get("reduce_count", {})
    returns  = summary.get("etf_week_return", {})

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴"}
    sig_e = SIGNAL_EMOJI.get(signal, "⚪")

    sig_summary = " / ".join(
        f"{k}×{v}" for k, v in sorted(sig_c.items(), key=lambda x: -x[1])
    ) if sig_c else signal

    lines = [
        f"📊 <b>주간 AI 시그널 성적표</b>  {week}",
        "",
        f"📅 {days}일 분석  |  {sig_e} 주도 시그널: <b>{signal}</b>",
        f"🌍 주도 레짐: <b>{regime}</b>  ({sig_summary})",
        "",
    ]

    # ETF 성과
    if returns:
        lines.append("📈 <b>이번 주 ETF 성과</b>")
        for etf, ret in sorted(returns.items(), key=lambda x: -x[1]):
            lines.append(_etf_return_line(etf, ret))
        lines.append("")

    # BUY/REDUCE 집중
    if buy_c:
        top_buy = sorted(buy_c.items(), key=lambda x: -x[1])[:3]
        buy_str = " · ".join(f"<b>{e}</b>({d}일)" for e, d in top_buy)
        lines.append(f"🟢 BUY 집중: {buy_str}")
    if reduce_c:
        top_red = sorted(reduce_c.items(), key=lambda x: -x[1])[:3]
        red_str = " · ".join(f"<b>{e}</b>({d}일)" for e, d in top_red)
        lines.append(f"🔴 REDUCE 집중: {red_str}")

    lines += [
        "",
        "💎 <i>풀버전 대시보드 → 유료 채널</i>",
        "",
        "#ETF #미국증시 #주간성적표 #AI투자",
    ]

    return "\n".join(lines)


def format_ai_scorecard_tweet(scorecard: dict, week: str = "") -> str:
    """
    'AI가 틀렸습니다' X 트윗 포맷 (280자 이내)
    """
    correct   = scorecard.get("correct", [])
    incorrect = scorecard.get("incorrect", [])
    hit_rate  = scorecard.get("hit_rate", 0)
    total     = scorecard.get("total", 0)

    pct = int(hit_rate * 100)
    lines = [f"🤖 이번 주 AI 성적표 {week}", ""]

    if correct:
        lines.append("✅ 맞춘 것")
        for c in correct[:2]:
            sign = "+" if c["return"] >= 0 else ""
            lines.append(f"→ {c['etf']} {c['signal']} → {sign}{c['return']:.1f}% ✓")

    if incorrect:
        lines.append("")
        lines.append("❌ 틀린 것")
        for w in incorrect[:2]:
            sign = "+" if w["return"] >= 0 else ""
            lines.append(f"→ {w['etf']} {w['signal']} 권고 but {sign}{w['return']:.1f}%")
            lines.append(f"   ({w.get('reason', '')})")

    lines += [
        "",
        f"📊 이번 주 적중률: {pct}% ({len(correct)}/{total})",
        "",
        "#AI투자 #성적표 #ETF #반성",
    ]
    tweet = "\n".join(lines)
    return tweet[:280]


def format_ai_scorecard_telegram(scorecard: dict, week: str = "") -> str:
    """
    'AI가 틀렸습니다' 텔레그램 HTML 포맷
    """
    correct   = scorecard.get("correct", [])
    incorrect = scorecard.get("incorrect", [])
    hit_rate  = scorecard.get("hit_rate", 0)
    total     = scorecard.get("total", 0)

    pct = int(hit_rate * 100)
    lines = [f"🤖 <b>이번 주 AI 성적표</b>  {week}", ""]

    if correct:
        lines.append("✅ <b>맞춘 것</b>")
        for c in correct[:3]:
            sign = "+" if c["return"] >= 0 else ""
            lines.append(f"→ <b>{c['etf']}</b> {c['signal']} → {sign}{c['return']:.1f}% ✓")

    if incorrect:
        lines.append("")
        lines.append("❌ <b>틀린 것</b>")
        for w in incorrect[:3]:
            sign = "+" if w["return"] >= 0 else ""
            lines.append(f"→ <b>{w['etf']}</b> {w['signal']} 권고 but {sign}{w['return']:.1f}%")
            lines.append(f"   <i>({w.get('reason', '')})</i>")

    lines += [
        "",
        f"📊 이번 주 적중률: <b>{pct}%</b> ({len(correct)}/{total})",
        "",
        "#AI투자 #성적표 #ETF #반성",
    ]
    return "\n".join(lines)
