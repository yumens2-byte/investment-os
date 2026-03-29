"""
dashboard_builder_v2.py — 샘플 생성용 (FX + EDT Investment)
"""
import logging
import warnings
warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from datetime import datetime, timezone, timedelta
from pathlib import Path

W, H = 1080, 1080
DPI  = 96

# 컬러
BG      = "#0d1117"
CARD    = "#161b22"
BORDER  = "#2d3748"
TEXT    = "#e2e8f0"
SUBTEXT = "#8892a4"
RED     = "#ef4444"
GREEN   = "#22c55e"
YELLOW  = "#f59e0b"
PURPLE  = "#8b5cf6"
ORANGE  = "#f97316"
BLUE    = "#3b82f6"

REGIME_COLORS = {
    "Risk-On":          "#059669",
    "Risk-Off":         "#dc2626",
    "Oil Shock":        "#d97706",
    "Liquidity Crisis": "#7c3aed",
    "Recession Risk":   "#9f1239",
    "Stagflation Risk": "#b45309",
    "AI Bubble":        "#0369a1",
    "Transition":       "#4b5563",
}

STANCE_COLORS = {
    "Overweight":  GREEN,
    "Underweight": RED,
    "Neutral":     TEXT,
    "Hedge":       PURPLE,
}

RISK_COLORS  = {"LOW": GREEN, "MEDIUM": YELLOW, "HIGH": RED}
SIGNAL_COLORS = {
    "BUY": GREEN, "ADD": "#34d399", "HOLD": YELLOW,
    "REDUCE": ORANGE, "HEDGE": PURPLE, "SELL": RED,
}
SESSION_LABELS = {
    "morning":  "Morning Brief",
    "intraday": "Intraday Briefing",
    "close":    "Close Summary",
    "weekly":   "Weekly Review",
}


def _bg_rect(ax, x, y, w, h, color=CARD, radius=0.012):
    box = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=1, edgecolor=BORDER, facecolor=color,
        transform=ax.transAxes, zorder=1,
    )
    ax.add_patch(box)


def _t(ax, x, y, s, color=TEXT, size=11, weight="normal", ha="left", va="center"):
    ax.text(x, y, s, color=color, fontsize=size, fontweight=weight,
            va=va, ha=ha, transform=ax.transAxes, zorder=2)


def build_sample(data, fx, session="morning", dt_utc=None, out_path="/tmp/dashboard_v2.png"):
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)

    kst = dt_utc + timedelta(hours=9)
    et  = dt_utc - timedelta(hours=4)
    date_str = et.strftime("%b %d, %Y")
    et_str   = et.strftime("ET %H:%M")
    kst_str  = kst.strftime("KST %H:%M")

    snap    = data["market_snapshot"]
    regime  = data["market_regime"]
    strat   = data["etf_strategy"]
    alloc_d = data["etf_allocation"]["allocation"]
    signal  = data["trading_signal"]["trading_signal"]
    summary = data["output_helpers"].get("one_line_summary", "")
    stance  = strat["stance"]

    regime_name = regime["market_regime"]
    risk_level  = regime["market_risk_level"]
    reason      = regime.get("regime_reason", "")[:42]
    session_lbl = SESSION_LABELS.get(session, "Market Snapshot")

    VERSION  = "v1.5.3"
    CODENAME = "EDT Investment"

    fig = plt.figure(figsize=(W/DPI, H/DPI), facecolor=BG, dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off"); ax.set_facecolor(BG)

    PAD  = 0.022
    GAP  = 0.010
    MID  = 0.5

    HEADER_H  = 0.090
    FOOTER_H  = 0.042
    SIGNAL_H  = 0.090
    BODY_H    = 1 - PAD*2 - HEADER_H - FOOTER_H - SIGNAL_H - GAP*4
    ROW_H     = BODY_H / 2
    LW = MID - PAD - GAP/2
    RW = MID - PAD - GAP/2
    RX = MID + GAP/2

    # ── HEADER ────────────────────────────────────────────────
    hy = 1 - PAD - HEADER_H
    _bg_rect(ax, PAD, hy, 1-2*PAD, HEADER_H)
    _t(ax, PAD+0.015, hy+HEADER_H*0.70,
       f"Investment OS   |   {session_lbl}",
       color=TEXT, size=19, weight="bold")
    _t(ax, PAD+0.015, hy+HEADER_H*0.25,
       f"{date_str}     {et_str}   |   {kst_str}",
       color=SUBTEXT, size=10)

    # ── MARKET SNAPSHOT (좌상 — FX 포함으로 높이 확장) ─────────
    BODY_TOP = hy - GAP
    sx, sy = PAD, BODY_TOP - ROW_H
    _bg_rect(ax, sx, sy, LW, ROW_H)
    _t(ax, sx+0.014, sy+ROW_H-0.020, "MARKET SNAPSHOT",
       color=TEXT, size=10, weight="bold")

    sp500  = snap.get("sp500",  0) or 0
    nasdaq = snap.get("nasdaq", 0) or 0
    vix    = snap.get("vix",    0) or 0
    us10y  = snap.get("us10y",  0) or 0
    oil    = snap.get("oil",    0) or 0
    dxy    = snap.get("dollar_index", 0) or 0

    sp_c  = GREEN if sp500  >= 0 else RED
    nq_c  = GREEN if nasdaq >= 0 else RED
    vix_c = RED if vix >= 30 else (YELLOW if vix >= 20 else GREEN)

    # 기존 6개 수치
    rows_main = [
        (sx+0.014,    sy+ROW_H*0.82, f"S&P500:  {sp500:+.2f}%",   sp_c),
        (sx+0.014,    sy+ROW_H*0.68, f"Nasdaq:  {nasdaq:+.2f}%",  nq_c),
        (sx+0.014,    sy+ROW_H*0.54, f"VIX:  {vix:.1f}  {'⚠' if vix>=25 else ''}",   vix_c),
        (sx+LW*0.50,  sy+ROW_H*0.54, f"US10Y:  {us10y:.2f}%",    TEXT),
        (sx+0.014,    sy+ROW_H*0.40, f"WTI Oil:  ${oil:.1f}",     TEXT),
        (sx+LW*0.50,  sy+ROW_H*0.40, f"DXY:  {dxy:.1f}",         SUBTEXT),
    ]
    for tx, ty, label, color in rows_main:
        _t(ax, tx, ty, label, color=color, size=11)

    # FX 구분선
    fy_sep = sy + ROW_H * 0.32
    line = plt.Line2D([sx+0.012, sx+LW-0.012], [fy_sep, fy_sep],
                       transform=ax.transAxes, color=BORDER, linewidth=0.8, zorder=2)
    ax.add_line(line)
    _t(ax, sx+0.014, fy_sep+0.010, "FX RATES",
       color=SUBTEXT, size=8, weight="bold")

    # FX 3종
    usdkrw = fx.get("usdkrw") or 0
    eurusd = fx.get("eurusd") or 0
    usdjpy = fx.get("usdjpy") or 0

    fx_rows = [
        (sx+0.014,   sy+ROW_H*0.17, f"USD/KRW  {usdkrw:,.1f}"),
        (sx+LW*0.38, sy+ROW_H*0.17, f"EUR/USD  {eurusd:.4f}"),
        (sx+LW*0.72, sy+ROW_H*0.17, f"USD/JPY  {usdjpy:.1f}"),
    ]
    for tx, ty, label in fx_rows:
        _t(ax, tx, ty, label, color=BLUE, size=9.5)

    # ── MARKET REGIME (우상) ──────────────────────────────────
    rx_pos = RX
    ry = BODY_TOP - ROW_H
    _bg_rect(ax, rx_pos, ry, RW, ROW_H)
    _t(ax, rx_pos+0.014, ry+ROW_H-0.020, "MARKET REGIME",
       color=TEXT, size=10, weight="bold")

    # 레짐 배지
    bc = REGIME_COLORS.get(regime_name, "#4b5563")
    bh, by = 0.065, ry + ROW_H*0.58
    badge = FancyBboxPatch(
        (rx_pos+0.014, by), RW-0.028, bh,
        boxstyle="round,pad=0.005,rounding_size=0.014",
        linewidth=0, facecolor=bc, transform=ax.transAxes, zorder=2,
    )
    ax.add_patch(badge)
    _t(ax, rx_pos+RW/2, by+bh/2, regime_name.upper(),
       color="white", size=15, weight="bold", ha="center")

    # Risk
    rc = RISK_COLORS.get(risk_level, YELLOW)
    _t(ax, rx_pos+0.014, ry+ROW_H*0.38, "RISK: ", color=TEXT, size=10, weight="bold")
    _t(ax, rx_pos+0.085, ry+ROW_H*0.38, f"{risk_level}  ●", color=rc, size=10, weight="bold")

    # Reason
    _t(ax, rx_pos+0.014, ry+ROW_H*0.22, reason, color=SUBTEXT, size=8.5)

    # ── ETF STRATEGY (좌하) ───────────────────────────────────
    etx, ety = PAD, BODY_TOP - ROW_H*2 - GAP
    _bg_rect(ax, etx, ety, LW, ROW_H)
    _t(ax, etx+0.014, ety+ROW_H-0.020, "ETF STRATEGY",
       color=TEXT, size=10, weight="bold")

    etfs = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]
    sp = 0.135
    for i, etf in enumerate(etfs):
        s = stance.get(etf, "Neutral")
        sc = STANCE_COLORS.get(s, TEXT)
        ry2 = ety + ROW_H*(0.80 - i*sp)
        _t(ax, etx+0.014, ry2, etf, color=TEXT, size=10.5, weight="bold")
        _t(ax, etx+0.075, ry2, "—", color=SUBTEXT, size=10.5)
        _t(ax, etx+0.095, ry2, s,   color=sc,    size=10.5)

    # ── ETF ALLOCATION (우하) — 수평 바 ──────────────────────
    alx, aly = RX, BODY_TOP - ROW_H*2 - GAP
    _bg_rect(ax, alx, aly, RW, ROW_H)
    _t(ax, alx+0.014, aly+ROW_H-0.020, "ETF ALLOCATION",
       color=TEXT, size=10, weight="bold")

    sorted_alloc = sorted(alloc_d.items(), key=lambda x: x[1], reverse=True)
    max_pct = max((v for _, v in sorted_alloc), default=100)
    bar_x = alx + 0.10
    bar_w = RW - 0.18
    sp2 = 0.135

    for i, (etf, pct) in enumerate(sorted_alloc):
        ry3 = aly + ROW_H*(0.80 - i*sp2)
        bl  = (pct / max_pct) * bar_w
        s   = stance.get(etf, "Neutral")
        bc2 = RED if s == "Underweight" else (GREEN if s == "Overweight" else "#e05c3a")

        _t(ax, alx+0.014, ry3, etf, color=SUBTEXT, size=9)
        # 배경바
        ax.add_patch(FancyBboxPatch(
            (bar_x, ry3-0.011), bar_w, 0.022,
            boxstyle="round,pad=0,rounding_size=0.004",
            facecolor=BORDER, linewidth=0, transform=ax.transAxes, zorder=2))
        # 전경바
        if bl > 0.004:
            ax.add_patch(FancyBboxPatch(
                (bar_x, ry3-0.011), bl, 0.022,
                boxstyle="round,pad=0,rounding_size=0.004",
                facecolor=bc2, linewidth=0, transform=ax.transAxes, zorder=3))
        _t(ax, bar_x+bar_w+0.008, ry3, f"{pct}%", color=TEXT, size=9)

    # ── SIGNAL SECTION ────────────────────────────────────────
    sig_y = BODY_TOP - ROW_H*2 - GAP*2 - SIGNAL_H
    _bg_rect(ax, PAD, sig_y, 1-2*PAD, SIGNAL_H)
    _t(ax, PAD+0.014, sig_y+SIGNAL_H-0.018,
       "SIGNAL SECTION", color=SUBTEXT, size=8.5, weight="bold")

    sc2 = SIGNAL_COLORS.get(signal, YELLOW)
    _t(ax, PAD+0.014, sig_y+SIGNAL_H*0.38,
       f"SIGNAL:  {signal}", color=sc2, size=16, weight="bold")

    sm = data["trading_signal"].get("signal_matrix", {})
    buy_w  = "  ".join(sm.get("buy_watch", []))
    hold_w = "  ".join(sm.get("hold", []))
    red_w  = "  ".join(sm.get("reduce", []))

    tag_x = PAD + 0.30
    if buy_w:
        _t(ax, tag_x,        sig_y+SIGNAL_H*0.65, f"BUY: {buy_w}",    color=GREEN,  size=9)
    if hold_w:
        _t(ax, tag_x,        sig_y+SIGNAL_H*0.38, f"HOLD: {hold_w}",  color=YELLOW, size=9)
    if red_w:
        _t(ax, tag_x,        sig_y+SIGNAL_H*0.15, f"REDUCE: {red_w}", color=RED,    size=9)

    short = summary[:60] if summary else ""
    _t(ax, PAD+0.60, sig_y+SIGNAL_H*0.38, short, color=TEXT, size=8.5)

    # ── FOOTER ────────────────────────────────────────────────
    ft_y = PAD
    _bg_rect(ax, PAD, ft_y, 1-2*PAD, FOOTER_H, color="#0a0d13")
    _t(ax, 0.5, ft_y+FOOTER_H/2,
       f"Investment OS  {VERSION}   |   {CODENAME}",
       color=SUBTEXT, size=9, ha="center")

    fig.savefig(out_path, dpi=DPI, bbox_inches="tight",
                facecolor=BG, edgecolor="none", pad_inches=0)
    plt.close(fig)
    return out_path

