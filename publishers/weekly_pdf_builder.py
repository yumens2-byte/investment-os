"""
publishers/weekly_pdf_builder.py
===================================
주간 성적표 PDF 리포트 생성 (reportlab)

PDF 구성:
  - Page 1: 헤더 + 주간 시그널 요약 + ETF 성과 테이블
  - Page 2: 일별 시그널 이력 + 다음 주 전략
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("PDF_OUTPUT_DIR", "data/outputs"))


def build_weekly_pdf(summary: dict) -> str:
    """
    주간 성적표 PDF 생성

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
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError as e:
        raise ImportError(f"reportlab 미설치: {e}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    filename = f"weekly_report_{now_kst.strftime('%Y%m%d')}.pdf"
    filepath = str(OUTPUT_DIR / filename)

    # ── 데이터 추출 ──────────────────────────────────────────
    week    = summary.get("week", "—")
    days    = summary.get("days", 0)
    regime  = summary.get("dominant_regime", "—")
    signal  = summary.get("dominant_signal", "—")
    sig_c   = summary.get("signal_counts", {})
    buy_c   = summary.get("buy_count", {})
    reduce_c = summary.get("reduce_count", {})
    returns = summary.get("etf_week_return", {})
    entries = summary.get("entries", [])

    # ── 스타일 정의 ──────────────────────────────────────────
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=22,
        spaceAfter=6,
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#4a4a8a"),
        alignment=TA_CENTER,
        spaceAfter=14,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#1a1a2e"),
        spaceBefore=14,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#333333"),
        spaceAfter=4,
    )
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.gray,
        alignment=TA_CENTER,
    )

    # ── 문서 빌드 ─────────────────────────────────────────────
    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        topMargin=20*mm,
        bottomMargin=20*mm,
        leftMargin=20*mm,
        rightMargin=20*mm,
    )

    story = []

    # ── 헤더 ─────────────────────────────────────────────────
    story.append(Paragraph("Investment OS — Weekly Report", title_style))
    story.append(Paragraph(
        f"{week}  |  {days} Days  |  Generated {now_kst.strftime('%Y-%m-%d %H:%M')} KST",
        subtitle_style
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#4a4a8a")))
    story.append(Spacer(1, 10))

    # ── 주간 시그널 요약 ──────────────────────────────────────
    story.append(Paragraph("Weekly Signal Summary", section_style))

    sig_summary = " / ".join(
        f"{k} x{v}" for k, v in sorted(sig_c.items(), key=lambda x: -x[1])
    ) if sig_c else signal

    summary_data = [
        ["Metric", "Value"],
        ["Dominant Regime", regime],
        ["Dominant Signal", f"{signal}  ({sig_summary})"],
        ["Analysis Days", str(days)],
    ]
    if buy_c:
        top_buy = ", ".join(f"{e}({d}d)" for e, d in sorted(buy_c.items(), key=lambda x: -x[1])[:3])
        summary_data.append(["BUY Focus", top_buy])
    if reduce_c:
        top_red = ", ".join(f"{e}({d}d)" for e, d in sorted(reduce_c.items(), key=lambda x: -x[1])[:3])
        summary_data.append(["REDUCE Focus", top_red])

    summary_table = Table(summary_data, colWidths=[60*mm, 110*mm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 10),
        ("BACKGROUND",   (0, 1), (0, -1), colors.HexColor("#f0f0f8")),
        ("FONTNAME",     (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 1), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f8fc")]),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#ccccdd")),
        ("PADDING",      (0, 0), (-1, -1), 7),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 14))

    # ── ETF 성과 테이블 ───────────────────────────────────────
    if returns:
        story.append(Paragraph("ETF Weekly Performance", section_style))

        perf_data = [["ETF", "Week Return", "Direction"]]
        for etf, ret in sorted(returns.items(), key=lambda x: -x[1]):
            direction = "UP" if ret > 0 else "DOWN" if ret < 0 else "FLAT"
            perf_data.append([etf, f"{'+' if ret >= 0 else ''}{ret:.2f}%", direction])

        perf_table = Table(perf_data, colWidths=[40*mm, 60*mm, 60*mm])
        row_styles = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.HexColor("#ccccdd")),
            ("PADDING",    (0, 0), (-1, -1), 7),
            ("ALIGN",      (1, 0), (-1, -1), "CENTER"),
        ]
        for i, (_, ret, _) in enumerate(perf_data[1:], 1):
            val = float(ret.replace("+", "").replace("%", ""))
            if val > 0:
                row_styles.append(("TEXTCOLOR", (1, i), (1, i), colors.HexColor("#006600")))
            elif val < 0:
                row_styles.append(("TEXTCOLOR", (1, i), (1, i), colors.HexColor("#cc0000")))

        perf_table.setStyle(TableStyle(row_styles))
        story.append(perf_table)
        story.append(Spacer(1, 14))

    # ── 일별 시그널 이력 ──────────────────────────────────────
    if entries:
        story.append(Paragraph("Daily Signal History", section_style))

        daily_data = [["Date", "Regime", "Signal", "Risk", "BUY Watch"]]
        for e in entries:
            daily_data.append([
                e.get("date", ""),
                e.get("regime", "—"),
                e.get("signal", "—"),
                e.get("risk", "—"),
                ", ".join(e.get("buy_watch", [])[:2]) or "—",
            ])

        daily_table = Table(daily_data, colWidths=[30*mm, 38*mm, 28*mm, 28*mm, 42*mm])
        daily_table.setStyle(TableStyle([
            ("BACKGROUND",     (0, 0), (-1, 0), colors.HexColor("#4a4a8a")),
            ("TEXTCOLOR",      (0, 0), (-1, 0), colors.white),
            ("FONTNAME",       (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",       (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5fa")]),
            ("GRID",           (0, 0), (-1, -1), 0.5, colors.HexColor("#ccccdd")),
            ("PADDING",        (0, 0), (-1, -1), 5),
            ("ALIGN",          (0, 0), (-1, -1), "CENTER"),
        ]))
        story.append(daily_table)
        story.append(Spacer(1, 14))

    # ── 다음 주 전략 ──────────────────────────────────────────
    last_entry = entries[-1] if entries else {}
    last_signal = last_entry.get("signal", signal)
    last_regime = last_entry.get("regime", regime)
    last_buy    = ", ".join(last_entry.get("buy_watch", []))
    last_hold   = ", ".join(last_entry.get("hold", []))
    last_reduce = ", ".join(last_entry.get("reduce", []))

    story.append(Paragraph("Next Week Strategy", section_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#ccccdd")))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Signal: <b>{last_signal}</b>  |  Regime: <b>{last_regime}</b>", body_style))
    if last_buy:
        story.append(Paragraph(f"BUY Watch: {last_buy}", body_style))
    if last_hold:
        story.append(Paragraph(f"Hold: {last_hold}", body_style))
    if last_reduce:
        story.append(Paragraph(f"Reduce: {last_reduce}", body_style))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#ccccdd")))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Investment OS — Auto-generated report. Not financial advice.",
        footer_style
    ))

    doc.build(story)
    logger.info(f"[WeeklyPDF] PDF 생성 완료: {filepath}")
    return filepath
