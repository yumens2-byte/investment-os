"""
publishers/weekly_pdf_builder.py (v2.0 — B-8 고도화)
=====================================================
주간 성적표 PDF 리포트 생성 (reportlab)

PDF 구성 (4페이지):
  - Page 1: 헤더 + 주간 시그널 요약 + ETF 성과 + AI 성적표
  - Page 2: 일별 시그널 이력 + Market Score 6축 추이
  - Page 3: ETF 상세 전략 (B-7 근거) + 주간 레짐 전환 이력
  - Page 4: 주간 주요 시그널 하이라이트 + 다음 주 전략 + Footer

변경 이력:
  v1.0: 기본 2페이지 (시그널 요약 + ETF 성과 + 일별 이력)
  v2.0 (B-8): 4페이지 확장 (AI성적표 + Score추이 + ETF근거 + 시그널하이라이트)
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("PDF_OUTPUT_DIR", "data/outputs"))

# 시그널 한국어 라벨
_SIGNAL_LABEL = {
    "volatility_score": "VIX", "rate_score": "금리",
    "commodity_pressure_score": "유가", "financial_stability_score": "금융안정",
    "sentiment_score": "시장심리", "fear_greed_score": "공포탐욕",
    "crypto_risk_score": "BTC", "equity_momentum_score": "주가모멘텀",
    "xlf_gld_score": "금융/금", "breadth_score": "시장참여도",
    "vol_term_score": "변동성구조", "claims_score": "실업수당",
    "infl_exp_score": "기대인플레", "em_stress_score": "신흥국",
    "ai_momentum_score": "AI모멘텀", "nasdaq_rel_score": "나스닥상대",
    "banking_stress_score": "은행스트레스",
}


def build_weekly_pdf(summary: dict) -> str:
    """
    주간 성적표 PDF 생성 (v2.0 — 4페이지)

    Args:
        summary: weekly_tracker.get_weekly_summary() 반환값

    Returns:
        생성된 PDF 파일 경로
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, PageBreak,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError as e:
        raise ImportError(f"reportlab 미설치: {e}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    filename = f"weekly_report_{now_kst.strftime('%Y%m%d')}.pdf"
    filepath = str(OUTPUT_DIR / filename)

    # ── 데이터 추출 ──
    week    = summary.get("week", "—")
    days    = summary.get("days", 0)
    regime  = summary.get("dominant_regime", "—")
    signal  = summary.get("dominant_signal", "—")
    sig_c   = summary.get("signal_counts", {})
    buy_c   = summary.get("buy_count", {})
    reduce_c = summary.get("reduce_count", {})
    returns = summary.get("etf_week_return", {})
    entries = summary.get("entries", [])

    # B-8 확장 데이터
    daily_scores     = summary.get("daily_scores", [])
    regime_changes   = summary.get("regime_changes", [])
    weekly_top_sigs  = summary.get("weekly_top_signals", [])
    alloc_start      = summary.get("allocation_start", {})
    alloc_end        = summary.get("allocation_end", {})

    # AI 성적표 (있으면)
    try:
        from core.weekly_tracker import get_ai_scorecard
        scorecard = get_ai_scorecard(summary)
    except Exception:
        scorecard = {"correct": [], "incorrect": [], "hit_rate": 0, "total": 0}

    # B-7 ETF 근거 (마지막 날 기준)
    etf_rationales = {}
    try:
        last_entry = entries[-1] if entries else {}
        last_signals = {}
        # signals는 weekly_log에 직접 저장되지 않으므로 core_data에서 로드
        from core.json_builder import load_core_data
        cd = load_core_data()
        last_signals = cd.get("data", {}).get("signals", {})
        last_stance = cd.get("data", {}).get("etf_strategy", {}).get("stance", {})
        last_regime = cd.get("data", {}).get("market_regime", {}).get("market_regime", regime)
        if last_signals and last_stance:
            from engines.etf_engine import generate_all_etf_rationales
            etf_rationales = generate_all_etf_rationales(last_stance, last_signals, last_regime)
    except Exception:
        pass

    # ── 스타일 정의 ──
    styles = getSampleStyleSheet()
    NAVY = colors.HexColor("#1a1a2e")
    PURPLE = colors.HexColor("#4a4a8a")
    LIGHT_BG = colors.HexColor("#f0f0f8")
    GRID_COLOR = colors.HexColor("#ccccdd")

    title_s = ParagraphStyle("T", parent=styles["Title"], fontSize=22,
                             spaceAfter=6, textColor=NAVY, alignment=TA_CENTER)
    sub_s = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=11,
                           textColor=PURPLE, alignment=TA_CENTER, spaceAfter=14)
    sec_s = ParagraphStyle("Sec", parent=styles["Heading2"], fontSize=13,
                           textColor=NAVY, spaceBefore=14, spaceAfter=6)
    body_s = ParagraphStyle("B", parent=styles["Normal"], fontSize=10,
                            textColor=colors.HexColor("#333333"), spaceAfter=4)
    small_s = ParagraphStyle("Sm", parent=styles["Normal"], fontSize=9,
                             textColor=colors.HexColor("#555555"), spaceAfter=3)
    footer_s = ParagraphStyle("F", parent=styles["Normal"], fontSize=8,
                              textColor=colors.gray, alignment=TA_CENTER)

    def _table_style(header_color=NAVY):
        return TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), header_color),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f8fc")]),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID_COLOR),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ])

    # ── 문서 빌드 ──
    doc = SimpleDocTemplate(filepath, pagesize=A4,
                            topMargin=18*mm, bottomMargin=18*mm,
                            leftMargin=18*mm, rightMargin=18*mm)
    story = []

    # ════════════════════════════════════════════
    # PAGE 1: 헤더 + 주간 요약 + ETF 성과 + AI 성적표
    # ════════════════════════════════════════════
    story.append(Paragraph("Investment OS — Weekly Report", title_s))
    story.append(Paragraph(
        f"{week}  |  {days} Days  |  Generated {now_kst.strftime('%Y-%m-%d %H:%M')} KST", sub_s))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PURPLE))
    story.append(Spacer(1, 8))

    # 주간 시그널 요약
    story.append(Paragraph("Weekly Signal Summary", sec_s))
    sig_summary = " / ".join(
        f"{k} x{v}" for k, v in sorted(sig_c.items(), key=lambda x: -x[1])
    ) if sig_c else signal

    s_data = [["Metric", "Value"],
              ["Dominant Regime", regime],
              ["Dominant Signal", f"{signal}  ({sig_summary})"],
              ["Analysis Days", str(days)]]
    if buy_c:
        s_data.append(["BUY Focus", ", ".join(f"{e}({d}d)" for e, d in
                       sorted(buy_c.items(), key=lambda x: -x[1])[:3])])
    if reduce_c:
        s_data.append(["REDUCE Focus", ", ".join(f"{e}({d}d)" for e, d in
                       sorted(reduce_c.items(), key=lambda x: -x[1])[:3])])

    st = Table(s_data, colWidths=[55*mm, 115*mm])
    st.setStyle(_table_style())
    story.append(st)
    story.append(Spacer(1, 10))

    # ETF 성과
    if returns:
        story.append(Paragraph("ETF Weekly Performance", sec_s))
        p_data = [["ETF", "Week Return", "Direction"]]
        for etf, ret in sorted(returns.items(), key=lambda x: -x[1]):
            d = "UP" if ret > 0 else "DOWN" if ret < 0 else "FLAT"
            p_data.append([etf, f"{'+' if ret >= 0 else ''}{ret:.2f}%", d])
        pt = Table(p_data, colWidths=[40*mm, 60*mm, 60*mm])
        pt.setStyle(_table_style())
        story.append(pt)
        story.append(Spacer(1, 10))

    # AI 성적표 (신규)
    if scorecard.get("total", 0) > 0:
        story.append(Paragraph("AI Signal Scorecard", sec_s))
        hr = scorecard.get("hit_rate", 0)
        total = scorecard.get("total", 0)
        correct = scorecard.get("correct", [])
        incorrect = scorecard.get("incorrect", [])

        story.append(Paragraph(
            f"Hit Rate: <b>{hr:.0%}</b>  ({len(correct)}/{total})", body_s))
        sc_data = [["ETF", "Signal", "Return", "Result"]]
        for c in correct:
            sc_data.append([c["etf"], c["signal"],
                           f"{'+' if c['return']>=0 else ''}{c['return']:.1f}%", "✅"])
        for c in incorrect:
            sc_data.append([c["etf"], c["signal"],
                           f"{'+' if c['return']>=0 else ''}{c['return']:.1f}%", "❌"])
        sct = Table(sc_data, colWidths=[35*mm, 35*mm, 45*mm, 45*mm])
        sct.setStyle(_table_style())
        story.append(sct)

    # ════════════════════════════════════════════
    # PAGE 2: 일별 이력 + Market Score 추이
    # ════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("Daily Signal History", sec_s))

    if entries:
        d_data = [["Date", "Regime", "Signal", "Risk", "BUY Watch"]]
        for e in entries:
            d_data.append([
                e.get("date", "")[-5:],  # MM-DD
                e.get("regime", "—"),
                e.get("signal", "—"),
                e.get("risk", "—"),
                ", ".join(e.get("buy_watch", [])[:2]) or "—",
            ])
        dt = Table(d_data, colWidths=[25*mm, 40*mm, 28*mm, 28*mm, 45*mm])
        dt.setStyle(_table_style(PURPLE))
        story.append(dt)
        story.append(Spacer(1, 12))

    # Market Score 6축 추이 (신규)
    if daily_scores:
        story.append(Paragraph("Market Score Trend (6-Axis)", sec_s))
        sc_keys = ["growth_score", "inflation_score", "liquidity_score",
                   "risk_score", "financial_stability_score", "commodity_pressure_score"]
        sc_labels = ["Date", "Growth", "Infl.", "Liq.", "Risk", "Stab.", "Comm."]
        ms_data = [sc_labels]

        for ds in daily_scores:
            row = [ds.get("date", "")[-5:]]
            for k in sc_keys:
                v = ds.get(k, "—")
                row.append(str(v))
            ms_data.append(row)

        # 주간 변화 행 추가
        if len(daily_scores) >= 2:
            first = daily_scores[0]
            last = daily_scores[-1]
            diff_row = ["Change"]
            for k in sc_keys:
                f_v = first.get(k, 0)
                l_v = last.get(k, 0)
                if isinstance(f_v, (int, float)) and isinstance(l_v, (int, float)):
                    d = l_v - f_v
                    diff_row.append(f"{d:+d}" if d != 0 else "0")
                else:
                    diff_row.append("—")
            ms_data.append(diff_row)

        mst = Table(ms_data, colWidths=[25*mm] + [25*mm]*6)
        mst.setStyle(_table_style(PURPLE))
        story.append(mst)

    # ════════════════════════════════════════════
    # PAGE 3: ETF 상세 전략 + 레짐 전환 이력
    # ════════════════════════════════════════════
    story.append(PageBreak())

    # ETF 상세 전략 (B-7 근거)
    if etf_rationales:
        story.append(Paragraph("ETF Strategy Detail (Signal-Based)", sec_s))
        ETFS = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]
        for etf in ETFS:
            rat = etf_rationales.get(etf, {})
            rationale = rat.get("rationale", "—")
            risk_text = rat.get("risk", "—")
            alloc = alloc_end.get(etf, 0)
            stance = "—"
            try:
                from core.json_builder import load_core_data as _lcd
                cd2 = _lcd()
                stance = cd2.get("data", {}).get("etf_strategy", {}).get("stance", {}).get(etf, "—")
            except Exception:
                pass

            story.append(Paragraph(
                f"<b>{etf}</b>  |  {stance}  |  Alloc: {alloc}%", body_s))
            story.append(Paragraph(
                f"  Rationale: <i>{rationale[:80]}</i>", small_s))
            story.append(Paragraph(
                f"  Risk: <i>{risk_text[:60]}</i>", small_s))
            story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("ETF Strategy Detail", sec_s))
        story.append(Paragraph("Signal data not available for this week.", body_s))

    story.append(Spacer(1, 10))

    # 배분 변화 비교
    if alloc_start and alloc_end and alloc_start != alloc_end:
        story.append(Paragraph("Allocation Change (Start vs End)", sec_s))
        a_data = [["ETF", "Week Start", "Week End", "Change"]]
        for etf in ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]:
            s_v = alloc_start.get(etf, 0)
            e_v = alloc_end.get(etf, 0)
            diff = e_v - s_v
            diff_s = f"{diff:+d}%" if diff != 0 else "—"
            a_data.append([etf, f"{s_v}%", f"{e_v}%", diff_s])
        at = Table(a_data, colWidths=[35*mm, 40*mm, 40*mm, 40*mm])
        at.setStyle(_table_style())
        story.append(at)
        story.append(Spacer(1, 10))

    # 레짐 전환 이력
    if regime_changes:
        story.append(Paragraph("Regime Transitions This Week", sec_s))
        r_data = [["Date", "From", "To"]]
        for rc in regime_changes:
            r_data.append([rc.get("date", "")[-5:], rc["from"], rc["to"]])
        rt = Table(r_data, colWidths=[40*mm, 60*mm, 60*mm])
        rt.setStyle(_table_style())
        story.append(rt)
    else:
        story.append(Paragraph("Regime Transitions This Week", sec_s))
        story.append(Paragraph("No regime changes detected.", body_s))

    # ════════════════════════════════════════════
    # PAGE 4: 시그널 하이라이트 + 다음 주 전략 + Footer
    # ════════════════════════════════════════════
    story.append(PageBreak())

    # 주간 주요 시그널 하이라이트 Top 5
    if weekly_top_sigs:
        story.append(Paragraph("Weekly Signal Highlights (Top 5)", sec_s))
        h_data = [["Signal", "Frequency", "Peak Value", "State"]]
        for ts in weekly_top_sigs:
            label = _SIGNAL_LABEL.get(ts["signal"], ts["signal"])
            h_data.append([
                label,
                f"{ts['count']} days",
                str(ts["max_value"]),
                ts.get("state", "—"),
            ])
        ht = Table(h_data, colWidths=[40*mm, 35*mm, 35*mm, 50*mm])
        ht.setStyle(_table_style())
        story.append(ht)
        story.append(Spacer(1, 12))

    # 다음 주 전략
    last_entry = entries[-1] if entries else {}
    last_signal = last_entry.get("signal", signal)
    last_regime = last_entry.get("regime", regime)
    last_risk = last_entry.get("risk", "—")
    last_buy = ", ".join(last_entry.get("buy_watch", []))
    last_hold = ", ".join(last_entry.get("hold", []))
    last_reduce = ", ".join(last_entry.get("reduce", []))

    story.append(Paragraph("Next Week Strategy", sec_s))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRID_COLOR))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Signal: <b>{last_signal}</b>  |  Regime: <b>{last_regime}</b>  |  Risk: <b>{last_risk}</b>",
        body_s))
    if last_buy:
        story.append(Paragraph(f"BUY Watch: <b>{last_buy}</b>", body_s))
    if last_hold:
        story.append(Paragraph(f"Hold: {last_hold}", body_s))
    if last_reduce:
        story.append(Paragraph(f"Reduce: {last_reduce}", body_s))

    # 배분 가이드 (마지막 날 기준)
    if alloc_end:
        story.append(Spacer(1, 8))
        story.append(Paragraph("Recommended Allocation:", body_s))
        alloc_str = " | ".join(f"{e} {v}%" for e, v in
                               sorted(alloc_end.items(), key=lambda x: -x[1]))
        story.append(Paragraph(f"  {alloc_str}", small_s))

    # Footer
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRID_COLOR))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Investment OS — Auto-generated weekly report. Not financial advice.",
        footer_s))

    doc.build(story)
    logger.info(f"[WeeklyPDF] PDF 생성 완료: {filepath} (v2.0 — 4페이지)")
    return filepath
