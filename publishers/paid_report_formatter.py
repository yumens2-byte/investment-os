"""
publishers/paid_report_formatter.py
======================================
유료 채널 전용 리포트 포맷

① ETF 상세 전략 리포트 — 6종 Stance/Score/이유 전체
② 포지션 사이징 가이드 — 배율 + 섹터별 비중 해설
"""
import logging

logger = logging.getLogger(__name__)


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

    # ── B-7: 시그널 기반 ETF별 근거 자동 생성 (2026-04-01 추가) ──
    signals = data.get("signals", {})
    etf_rationales = {}
    if signals:
        try:
            from engines.etf_engine import generate_all_etf_rationales
            etf_rationales = generate_all_etf_rationales(stance, signals, regime)
        except Exception:
            pass

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
        pos = rank_map.get(etf, "—")
        st_e = STANCE_EMOJI.get(s, "➡️")
        ti_e = TIMING_EMOJI.get(t, "🟡")

        lines.append(
            f"{st_e} <b>{etf}</b>  {pos}위  |  배분: <b>{pct}%</b>"
        )
        lines.append(f"   {ti_e} {t}  |  {s}")

        # B-7: 시그널 기반 근거 (있으면 사용, 없으면 기존 strategy_reason)
        rat = etf_rationales.get(etf, {})
        rationale = rat.get("rationale", "")
        risk_text = rat.get("risk", "")

        if rationale:
            lines.append(f"   🔍 근거: <i>{rationale[:60]}</i>")
        else:
            r = str_r.get(etf, "")
            if r:
                lines.append(f"   <i>{r[:40]}</i>")

        if risk_text:
            lines.append(f"   ⚠️ 리스크: <i>{risk_text[:50]}</i>")

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


    if reason:
        lines.append(f"\n<i>{reason}</i>")

    # ── Priority B: 거시 환경 지표 (2026-04-11 추가) ──────────
    signals = data.get("signals", {})
    cpi_yoy      = signals.get("cpi_yoy")
    core_cpi     = signals.get("core_cpi_yoy")
    labor_state  = signals.get("labor_state", "")
    nfp_change   = signals.get("nfp_change")
    cu_au_state  = signals.get("copper_gold_state", "")
    fed_bs_state = signals.get("fed_bs_state", "")
    fed_bs_chg   = signals.get("fed_bs_change_bn")
    sofr_state   = signals.get("sofr_state", "")

    # 하나라도 데이터가 있으면 섹션 출력
    has_b_data = any([
        cpi_yoy is not None,
        labor_state and labor_state != "No Data",
        cu_au_state and cu_au_state != "No Data",
        fed_bs_state and fed_bs_state != "No Data",
        sofr_state and sofr_state != "No Data",
    ])

    if has_b_data:
        lines += [
            "",
            "─────────────────────────",
            "📊 <b>거시 환경 (Priority B)</b>",
            "",
        ]
        if cpi_yoy is not None:
            core_str = f" · Core {core_cpi:.1f}%" if core_cpi else ""
            lines.append(f"  💹 CPI: <b>{cpi_yoy:.2f}% YoY</b>{core_str}")
        if labor_state and labor_state != "No Data":
            nfp_str = f" (NFP {nfp_change:+.0f}K)" if nfp_change is not None else ""
            lines.append(f"  👷 고용: <b>{labor_state}</b>{nfp_str}")
        if cu_au_state and cu_au_state != "No Data":
            lines.append(f"  🔶 Cu/Au: <b>{cu_au_state}</b>")
        if fed_bs_state and fed_bs_state != "No Data":
            chg_str = f" ({fed_bs_chg:+.0f}B/주)" if fed_bs_chg is not None else ""
            lines.append(f"  🏦 연준자산: <b>{fed_bs_state}</b>{chg_str}")
        if sofr_state and sofr_state != "No Data":
            lines.append(f"  💧 SOFR: <b>{sofr_state}</b>")

    # ── B-16: Gemini 뉴스 심층 분석 (있으면 표시) ──
    news_analysis = data.get("news_analysis", {})
    top_issues = news_analysis.get("top_issues", [])
    if top_issues:
        lines += [
            "",
            "─────────────────────────",
            "📰 <b>AI 뉴스 심층 분석</b>",
            "",
        ]
        IMPACT_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}
        for i, iss in enumerate(top_issues, 1):
            ie = IMPACT_EMOJI.get(iss.get("impact", "neutral"), "🟡")
            conf = iss.get("confidence", 0)
            lines.append(
                f"{ie} <b>{i}. {iss.get('topic', '?')}</b> "
                f"(신뢰도 {conf:.0%})"
            )
            summary = iss.get("summary", "")
            if summary:
                lines.append(f"   <i>{summary}</i>")
        key_risk = news_analysis.get("key_risk", "")
        if key_risk:
            lines.append(f"\n⚠️ 핵심 리스크: <i>{key_risk}</i>")

    lines += [
        "",
        "#ETF #프리미엄 #포지션사이징 #ETF전략",
    ]

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# C-3: ETF 추천 근거 AI 자연어화 (Gemini)
# ──────────────────────────────────────────────────────────────

def generate_ai_etf_rationale(data: dict) -> str:
    """
    Gemini로 ETF 배분 근거를 자연어로 설명.
    유료 TG 채널에 추가 발행.

    Returns: ETF 근거 자연어 텍스트 (빈 문자열 = 실패)
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return ""

        regime = data.get("market_regime", {}).get("market_regime", "Unknown")
        risk = data.get("market_regime", {}).get("market_risk_level", "MEDIUM")
        signal = data.get("trading_signal", {}).get("trading_signal", "HOLD")
        alloc = data.get("etf_allocation", {}).get("allocation", {})
        signals = data.get("signals", {})

        vix_state = signals.get("vix_state", "Normal")
        oil_state = signals.get("oil_state", "Moderate")
        fg_score = signals.get("fear_greed_score", 3)

        alloc_str = ", ".join(f"{e} {w}%" for e, w in
                              sorted(alloc.items(), key=lambda x: x[1], reverse=True))

        prompt = (
            f"ETF 포트폴리오 배분 근거를 투자자에게 설명해줘.\n"
            f"- 레짐: {regime}, 리스크: {risk}, 시그널: {signal}\n"
            f"- 배분: {alloc_str}\n"
            f"- VIX 상태: {vix_state}, Oil 상태: {oil_state}, F&G 점수: {fg_score}\n"
            f"조건:\n"
            f"- 각 ETF별 1줄씩 (6개), 한국어\n"
            f"- '왜 이 비중인지' 자연어로 설명\n"
            f"- 총 200자 이내\n"
            f"- 텍스트만 출력\n"
        )

        result = call(prompt=prompt, model="flash-lite", max_tokens=250, temperature=0.7)

        if result.get("success"):
            text = result["text"].strip()
            if len(text) > 20:
                logger.info(f"[PaidReport] AI ETF 근거 생성 ({len(text)}자)")
                return text

    except Exception as e:
        logger.warning(f"[PaidReport] AI ETF 근거 실패: {e}")

    return ""
