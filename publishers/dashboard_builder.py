import warnings; warnings.filterwarnings("ignore")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import FancyBboxPatch, Circle
from datetime import datetime, timezone, timedelta
import os, logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ==================== v1.7.1 변경 ====================
# 1. 화면 크기: 1080x1080 → 1080x1440 (세로 비율 개선)
# 2. 최소 폰트: 8.5pt → 11pt (가독성)
# 3. 색상 표준화: 중복 정의 제거
# 4. 그림자 효과 제거: 성능 최적화
# 5. VERSION 관리: config.settings 확인 + 명확한 기본값
# ===================================================

W, H = 1080, 1440  # ⭐️ 세로 비율 개선 (1080x1440)
DPI  = 96

# 색상: 기본 팔레트 (중복 제거)
BG="#080c12"; CARD="#0f1520"; CARD2="#131923"; BORDER="#1e2d42"; BORDER2="#243550"
TEXT="#ddeeff"; SUBTEXT="#6a8aaa"; DIM="#2a3f58"
RED="#ef4444"; GREEN="#10b981"; YELLOW="#f59e0b"; PURPLE="#9b6dff"
BLUE="#38bdf8"; CYAN="#06d6d6"; ORANGE="#fb923c"

# ⭐️ 색상 표준화: Regime/Stance/Risk/Signal은 위 기본색을 참조
REGIME_COLORS={
    "Risk-On":"#059669",
    "Risk-Off":"#dc2626", 
    "Oil Shock":"#f97316",
    "Liquidity Crisis":"#7c3aed",
    "Recession Risk":"#be123c",
    "Stagflation Risk":"#b45309",
    "AI Bubble":"#0369a1",
    "Transition":"#4b5563"
}
STANCE_COLORS={"Overweight":GREEN, "Underweight":RED, "Neutral":TEXT, "Hedge":PURPLE}
RISK_COLORS={"LOW":GREEN, "MEDIUM":YELLOW, "HIGH":RED}
SIGNAL_COLORS={"BUY":GREEN, "ADD":"#34d399", "HOLD":YELLOW, "REDUCE":ORANGE, "HEDGE":PURPLE, "SELL":RED}
SESSION_LABELS={
    "morning":"Morning Brief",
    "intraday":"Intraday Briefing",
    "close":"Close Summary",
    "weekly":"Weekly Review"
}

CHAR_W  = 0.0072
DOT_GAP = 0.014

def _score_color(s, m=5):
    r = s / m
    if r <= 0.25: return GREEN
    if r <= 0.45: return CYAN
    if r <= 0.60: return YELLOW
    if r <= 0.80: return ORANGE
    return RED

def _fig():
    fig = plt.figure(figsize=(W/DPI, H/DPI), facecolor=BG, dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0,0,1,1])
    ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off"); ax.set_facecolor(BG)
    return fig, ax

def _card(ax, x, y, w, h, accent=None, radius=0.014):
    ax.add_patch(FancyBboxPatch((x,y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=0.8, edgecolor=BORDER2, facecolor=CARD,
        transform=ax.transAxes, zorder=1))
    if accent:
        ax.add_patch(FancyBboxPatch((x+0.001, y+h-0.004), w-0.002, 0.003,
            boxstyle="round,pad=0,rounding_size=0.002",
            linewidth=0, facecolor=accent, transform=ax.transAxes, zorder=2, alpha=0.9))

# ⭐️ shadow 파라미터 제거 (성능 최적화)
def _t(ax, x, y, s, c=TEXT, sz=13, w="normal", ha="left", va="center", alpha=1.0):
    kw = dict(color=c, fontsize=sz, fontweight=w, va=va, ha=ha,
              transform=ax.transAxes, zorder=4, alpha=alpha)
    ax.text(x, y, s, **kw)

def _hline(ax, x1, x2, y, color=BORDER2, lw=0.7, alpha=0.5):
    ax.add_line(plt.Line2D([x1,x2],[y,y], transform=ax.transAxes, color=color, linewidth=lw, zorder=2, alpha=alpha))

def _vline(ax, x, y1, y2, color=BORDER2, lw=0.7, alpha=0.4):
    ax.add_line(plt.Line2D([x,x],[y1,y2], transform=ax.transAxes, color=color, linewidth=lw, zorder=2, alpha=alpha))

def _badge(ax, x, y, w, h, color, text, tsz=17):
    ax.add_patch(FancyBboxPatch((x,y), w, h,
        boxstyle="round,pad=0.008,rounding_size=0.018",
        linewidth=0, facecolor=color, transform=ax.transAxes, zorder=3))
    _t(ax, x+w/2, y+h/2, text.upper(), c="white", sz=tsz, w="bold", ha="center")

def _mini_bar(ax, x, y, w, h, pct, max_pct, color):
    ax.add_patch(FancyBboxPatch((x,y), w, h,
        boxstyle="round,pad=0,rounding_size=0.003",
        linewidth=0, facecolor=DIM, transform=ax.transAxes, zorder=2))
    fw = max(0.004, (pct/max_pct)*w)
    ax.add_patch(FancyBboxPatch((x,y), fw, h,
        boxstyle="round,pad=0,rounding_size=0.003",
        linewidth=0, facecolor=color, transform=ax.transAxes, zorder=3, alpha=0.9))

def _risk_circle(ax, cx, cy, radius, color):
    for rm, a in [(1.6,0.06),(1.35,0.12),(1.15,0.20)]:
        ax.add_patch(Circle((cx,cy), radius*rm, transform=ax.transAxes, zorder=2, facecolor=color, linewidth=0, alpha=a))
    ax.add_patch(Circle((cx,cy), radius, transform=ax.transAxes, zorder=3, facecolor=color, linewidth=0, alpha=0.95))
    ax.add_patch(Circle((cx-radius*0.25, cy+radius*0.25), radius*0.35, transform=ax.transAxes, zorder=4, facecolor="white", linewidth=0, alpha=0.15))

def _score_item(ax, x, y, label, score, max_s=5, dot_r=0.009, sz=9.0):
    dot_c = _score_color(score, max_s)
    _t(ax, x, y, label, c=SUBTEXT, sz=sz, ha="left", va="center")
    dot_cx = x + len(label) * CHAR_W + DOT_GAP + dot_r
    for rm, a in [(1.7,0.05),(1.35,0.12),(1.1,0.22)]:
        ax.add_patch(Circle((dot_cx,y), dot_r*rm, transform=ax.transAxes, zorder=3, facecolor=dot_c, linewidth=0, alpha=a))
    ax.add_patch(Circle((dot_cx,y), dot_r, transform=ax.transAxes, zorder=4, facecolor=dot_c, linewidth=0))


def build_dashboard(data: dict, session: str = "morning", dt_utc=None, output_dir=None) -> Optional[str]:
    try:
        if dt_utc is None: dt_utc = datetime.now(timezone.utc)
        if output_dir is None:
            from config.settings import IMAGES_DIR
            output_dir = IMAGES_DIR
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        fname = f"dashboard_{session}_{dt_utc.strftime('%Y%m%d_%H%M')}.png"
        fpath = str(output_dir / fname)
        _render(data, session, dt_utc, fpath)
        logger.info(f"[Dashboard] 저장: {fpath}")
        return fpath
    except Exception as e:
        logger.error(f"[Dashboard] 생성 실패: {e}", exc_info=True)
        return None


def _render(data: dict, session: str, dt_utc: datetime, fpath: str):
    fx = data.get("fx_rates", {})
    kst = dt_utc + timedelta(hours=9)
    et  = dt_utc - timedelta(hours=4)

    snap    = data.get("market_snapshot", {})
    regime  = data.get("market_regime", {})
    strat   = data.get("etf_strategy", {}).get("stance", {})
    alloc_d = data.get("etf_allocation", {}).get("allocation", {})
    signal  = data.get("trading_signal", {}).get("trading_signal", "HOLD")
    summary = data.get("output_helpers", {}).get("one_line_summary", "")
    sm      = data.get("trading_signal", {}).get("signal_matrix", {})
    ms      = data.get("market_score", {})

    regime_name = regime.get("market_regime", "Transition")
    risk_level  = regime.get("market_risk_level", "MEDIUM")
    reason      = regime.get("regime_reason", "")[:44]
    session_lbl = SESSION_LABELS.get(session, "Market Snapshot")
    rc          = REGIME_COLORS.get(regime_name, "#4b5563")
    sig_c       = SIGNAL_COLORS.get(signal, YELLOW)
    risk_c      = RISK_COLORS.get(risk_level, YELLOW)

    sp500  = snap.get("sp500",  0) or 0
    nasdaq = snap.get("nasdaq", 0) or 0
    vix    = snap.get("vix",    0) or 0
    us10y  = snap.get("us10y",  0) or 0
    oil    = snap.get("oil",    0) or 0
    dxy    = snap.get("dollar_index", 0) or 0
    sp_c   = GREEN if sp500  >= 0 else RED
    nq_c   = GREEN if nasdaq >= 0 else RED
    vix_c  = RED if vix >= 30 else (YELLOW if vix >= 20 else GREEN)

    usdkrw = fx.get("usdkrw") or 0
    eurusd = fx.get("eurusd") or 0
    usdjpy = fx.get("usdjpy") or 0

    # ⭐️ VERSION 관리 개선: config.settings 확인 후 명확한 기본값
    try:
        from config.settings import SYSTEM_VERSION, CODENAME
    except Exception:
        SYSTEM_VERSION = "v1.7.1"  # 현재 버전으로 업데이트
        CODENAME = "Investment OS"

    fig, ax = _fig()
    P=0.020; G=0.010; MID=0.502
    HH=0.075  # ⭐️ Header 높이 약간 축소 (공간 확보)
    FH=0.040  # ⭐️ Footer 높이 축소
    SH=0.105  # ⭐️ Signal 높이 증가 (중요도 반영)
    BH=(1-P*2-HH-FH-SH-G*4)/2
    HY=1-P-HH; BY1=HY-G-BH; BY2=BY1-G-BH; SY=BY2-G-SH; FY=P
    LW=MID-P-G/2; RW=1-MID-P-G/2; RX=MID+G/2

    # ==================== HEADER ====================
    _card(ax,P,HY,1-2*P,HH,accent=rc,radius=0.016)
    _t(ax,P+0.020,HY+HH*0.68,f"Investment OS   |   {session_lbl}",c=TEXT,sz=22,w="bold")
    _t(ax,P+0.020,HY+HH*0.25,
       f"{et.strftime('%b %d, %Y')}     ET {et.strftime('%H:%M')}   |   KST {kst.strftime('%H:%M')}",
       c=SUBTEXT,sz=12)

    # ==================== BODY 1: Market Snapshot & FX ====================
    _card(ax,P,BY1,LW,BH,accent=BLUE)
    _t(ax,P+0.018,BY1+BH-0.022,"MARKET SNAPSHOT",c=SUBTEXT,sz=11,w="bold")
    col1,col2=P+0.018,P+LW*0.52
    for lx,ly,lbl,val,vc in [
        (col1,BY1+BH*0.80,"S&P500:",f"{sp500:+.2f}%",sp_c),
        (col1,BY1+BH*0.63,"Nasdaq:",f"{nasdaq:+.2f}%",nq_c),
        (col1,BY1+BH*0.46,"VIX:",f"{vix:.1f}  {'⚠' if vix>=25 else ''}",vix_c),
        (col2,BY1+BH*0.46,"US10Y:",f"{us10y:.2f}%",TEXT),
        (col1,BY1+BH*0.31,"WTI:",f"${oil:.1f}",TEXT),
        (col2,BY1+BH*0.31,"DXY:",f"{dxy:.1f}",SUBTEXT),
    ]:
        _t(ax,lx,ly,lbl,c=SUBTEXT,sz=12)
        _t(ax,lx+0.065,ly,val,c=vc,sz=14,w="bold")

    fx_sep=BY1+BH*0.22
    _hline(ax,P+0.012,P+LW-0.012,fx_sep,alpha=0.7)
    _t(ax,P+0.018,fx_sep+0.012,"FX RATES",c=SUBTEXT,sz=11,w="bold")  # ⭐️ 폰트 크기 올림 (9.5→11)
    fx_y=BY1+BH*0.10; fx_cw=LW/3
    for fxx,fy2,fl,fv in [
        (P+fx_cw*0.05,fx_y,"USD/KRW",f"{usdkrw:,.1f}"),
        (P+fx_cw*1.05,fx_y,"EUR/USD",f"{eurusd:.4f}"),
        (P+fx_cw*2.05,fx_y,"USD/JPY",f"{usdjpy:.2f}"),
    ]:
        _t(ax,fxx,fy2+0.015,fl,c=SUBTEXT,sz=11)  # ⭐️ 폰트 크기 올림 (9.5→11)
        _t(ax,fxx,fy2-0.005,fv,c=CYAN,sz=13,w="bold")

    # ==================== BODY 1: Market Regime & Risk ====================
    _card(ax,RX,BY1,RW,BH,accent=rc)
    _t(ax,RX+0.018,BY1+BH-0.022,"MARKET REGIME",c=SUBTEXT,sz=11,w="bold")
    bh2=0.068; by2=BY1+BH*0.63
    _badge(ax,RX+0.018,by2,RW-0.036,bh2,rc,regime_name,tsz=17)
    _risk_circle(ax,RX+0.038,BY1+BH*0.445,0.018,risk_c)
    _t(ax,RX+0.070,BY1+BH*0.445,f"RISK: {risk_level}",c=risk_c,sz=13,w="bold")
    _t(ax,RX+0.018,BY1+BH*0.31,reason,c=SUBTEXT,sz=9.5)

    score_sep=BY1+BH*0.245
    _hline(ax,RX+0.012,RX+RW-0.012,score_sep,alpha=0.4)
    _t(ax,RX+0.018,score_sep+0.010,"MARKET SCORE",c=SUBTEXT,sz=11,w="bold")  # ⭐️ 폰트 크기 올림 (8.5→11)
    scores=[
        ("Growth",   ms.get("growth_score",2),     5),
        ("Risk",     ms.get("risk_score",3),        5),
        ("Inflation",ms.get("inflation_score",2),   5),
        ("Liquidity",ms.get("liquidity_score",2),   5),
        ("Commodity",ms.get("commodity_pressure_score",3),5),
        ("Stability",ms.get("financial_stability_score",2),5),
    ]
    col_w=(RW-0.016)/3
    available=score_sep-BY1
    row1_y=BY1+available*0.72
    row2_y=BY1+available*0.28
    for i,(label,score,max_s) in enumerate(scores):
        col=i%3; row=i//3
        x_start=RX+0.012+col*col_w
        y_pos=row1_y if row==0 else row2_y
        _score_item(ax,x_start,y_pos,label,score,max_s,dot_r=0.009,sz=10)  # ⭐️ 폰트 크기 올림 (9→10)

    # ==================== BODY 2: ETF Strategy & Allocation ====================
    etfs=["QQQM","XLK","SPYM","XLE","ITA","TLT"]
    row_sp=(BH-0.055)/len(etfs)

    _card(ax,P,BY2,LW,BH,accent=PURPLE)
    _t(ax,P+0.018,BY2+BH-0.022,"ETF STRATEGY",c=SUBTEXT,sz=11,w="bold")
    for i,etf in enumerate(etfs):
        s=strat.get(etf,"Neutral"); sc=STANCE_COLORS.get(s,TEXT)
        ry=BY2+BH*0.85-i*row_sp
        ax.add_patch(FancyBboxPatch((P+0.018,ry-0.015),0.072,0.030,
            boxstyle="round,pad=0,rounding_size=0.005",
            linewidth=0.5,edgecolor=BORDER2,facecolor=CARD2,
            transform=ax.transAxes,zorder=2))
        _t(ax,P+0.018+0.036,ry,etf,c=TEXT,sz=11,w="bold",ha="center")
        _t(ax,P+0.105,ry,"—",c=DIM,sz=11)
        _t(ax,P+0.120,ry,s,c=sc,sz=12)

    _card(ax,RX,BY2,RW,BH,accent=ORANGE)
    _t(ax,RX+0.018,BY2+BH-0.022,"ETF ALLOCATION",c=SUBTEXT,sz=11,w="bold")
    sorted_alloc=sorted(alloc_d.items(),key=lambda x:x[1],reverse=True)
    max_pct=max((v for _,v in sorted_alloc),default=100)
    bar_x=RX+0.095; bar_w=RW-0.130; bar_h=0.024
    for i,(etf,pct) in enumerate(sorted_alloc):
        ry=BY2+BH*0.85-i*row_sp
        s=strat.get(etf,"Neutral")
        bc=GREEN if s=="Overweight" else(RED if s=="Underweight" else "#e05c3a")
        _t(ax,RX+0.018,ry,etf,c=SUBTEXT,sz=10)
        _mini_bar(ax,bar_x,ry-bar_h/2,bar_w,bar_h,pct,max_pct,bc)
        _t(ax,bar_x+bar_w+0.010,ry,f"{pct}%",c=TEXT,sz=11,w="bold")

    # ==================== SIGNAL SECTION ====================
    _card(ax,P,SY,1-2*P,SH,accent=sig_c)
    _t(ax,P+0.018,SY+SH-0.020,"SIGNAL SECTION",c=SUBTEXT,sz=11,w="bold")
    _t(ax,P+0.018,SY+SH*0.44,"SIGNAL:",c=SUBTEXT,sz=15,w="bold")
    _t(ax,P+0.118,SY+SH*0.44,signal,c=sig_c,sz=18,w="bold")  # ⭐️ shadow 제거
    tag_x=P+0.27
    for etf_list,tc,label in [
        (sm.get("buy_watch",[]),GREEN,"BUY"),
        (sm.get("hold",[]),YELLOW,"HOLD"),
        (sm.get("reduce",[]),RED,"REDUCE"),
    ]:
        if not etf_list: continue
        _t(ax,tag_x,SY+SH*0.72,label+":",c=tc,sz=11,w="bold")  # ⭐️ 폰트 크기 올림 (9.5→11)
        _t(ax,tag_x+0.062,SY+SH*0.72,"  ".join(etf_list),c=tc,sz=10)
        tag_x+=0.16
    _t(ax,P+0.60,SY+SH*0.44,summary[:55],c=TEXT,sz=9.5,alpha=0.85)

    # ==================== FOOTER ====================
    _card(ax,P,FY,1-2*P,FH,radius=0.014)
    _t(ax,0.5,FY+FH/2,f"Investment OS  {SYSTEM_VERSION}   |   {CODENAME}",
       c=SUBTEXT,sz=10.5,ha="center")

    # ==================== 구분선 ====================
    _vline(ax,MID,BY1,BY1+BH)
    _vline(ax,MID,BY2,BY2+BH)

    fig.savefig(fpath,dpi=DPI,bbox_inches="tight",
                facecolor=BG,edgecolor="none",pad_inches=0)
    plt.close(fig)
