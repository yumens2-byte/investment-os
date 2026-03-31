"""
publishers/dashboard_html_builder.py
======================================
HTML/Playwright 기반 풀버전 대시보드 이미지 생성기 (session=full 전용)
v2.1.0 — core_data.json 실제 필드 100% 매핑

데이터 소스: core_data.json["data"] 필드만 사용
추측값: 0건
"""
import asyncio
import logging
import math
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "v2.1.0"

# ── 컬러 상수 ──
REGIME_COLOR = {
    "Risk-On": "#22ee88", "Risk-Off": "#ff4466", "Oil Shock": "#ffbb00",
    "Liquidity Crisis": "#bb88ff", "Recession Risk": "#ff2266",
    "Stagflation Risk": "#ffaa33", "AI Bubble": "#44aaff", "Transition": "#668899",
}
RISK_COLOR   = {"LOW": "#33ff99", "MEDIUM": "#ffbb33", "HIGH": "#ff4466"}
STANCE_COLOR = {"Overweight": "#33ff99", "Underweight": "#ff4466", "Neutral": "#99bbdd", "Hedge": "#bb88ff"}
SIGNAL_COLOR = {"BUY": "#33ff99", "ADD": "#55eeff", "HOLD": "#ffee44", "REDUCE": "#ff4466", "HEDGE": "#bb88ff", "SELL": "#ff4466"}
SCORE_THRESHOLDS = [(1, "#33ff99"), (2, "#55eeff"), (3, "#ffee44"), (4, "#ffbb33"), (5, "#ff4466")]
ETFS = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]


def _sc(v, m=5):
    for threshold, color in SCORE_THRESHOLDS:
        if v <= threshold:
            return color
    return "#ff4466"


def _sign(v):
    return f"▼{abs(v):.2f}%" if v < 0 else f"▲{v:.2f}%"


def _dn_up(v):
    return "#ff4466" if v < 0 else "#33ff99"


# ── Fear & Greed 색상 (0~100 스케일, 실제 값 기반) ──
def _fg_color(v):
    if v <= 20: return "#ff4466"     # Extreme Fear
    if v <= 40: return "#ffbb33"     # Fear
    if v <= 60: return "#99bbdd"     # Neutral
    if v <= 80: return "#33ff99"     # Greed
    return "#22ee88"                 # Extreme Greed


def _build_html(data: dict, dt_utc: datetime) -> str:
    kst = dt_utc + timedelta(hours=9)
    et  = dt_utc - timedelta(hours=4)

    # ── 데이터 추출 (core_data.json 실제 필드만) ──
    snap    = data.get("market_snapshot", {})
    regime  = data.get("market_regime", {})
    ms      = data.get("market_score", {})
    strat   = data.get("etf_strategy", {}).get("stance", {})
    alloc   = data.get("etf_allocation", {}).get("allocation", {})
    prisk   = data.get("portfolio_risk", {})
    tsig    = data.get("trading_signal", {})
    helpers = data.get("output_helpers", {})
    fx      = data.get("fx_rates", {})
    timing  = data.get("etf_analysis", {}).get("timing_signal", {})
    fg      = data.get("fear_greed", {})
    crypto  = data.get("crypto", {})

    # ── Market Snapshot (core_data 실제 필드) ──
    sp500_chg  = snap.get("sp500", 0) or 0
    nasdaq_chg = snap.get("nasdaq", 0) or 0
    vix        = snap.get("vix", 0) or 0
    us10y      = snap.get("us10y", 0) or 0
    oil        = snap.get("oil", 0) or 0
    dxy        = snap.get("dollar_index", 0) or 0

    # ── FX (core_data 실제 필드) ──
    usdkrw = fx.get("usdkrw") or 0
    eurusd = fx.get("eurusd") or 0
    usdjpy = fx.get("usdjpy") or 0

    # ── Fear & Greed (core_data 실제 필드) ──
    fg_value = fg.get("value", 0) or 0
    fg_label = fg.get("label", "—")
    fg_prev  = fg.get("prev_value", 0) or 0
    fg_chg   = fg.get("change", 0) or 0
    fg_emoji = fg.get("emoji", "")
    fg_c     = _fg_color(fg_value)

    # ── Crypto (core_data 실제 필드) ──
    btc_usd = crypto.get("btc_usd", 0) or 0
    btc_chg = crypto.get("btc_change_pct", 0) or 0
    eth_usd = crypto.get("eth_usd", 0) or 0
    eth_chg = crypto.get("eth_change_pct", 0) or 0

    # ── Regime / Risk ──
    regime_name = regime.get("market_regime", "Transition")
    risk_level  = regime.get("market_risk_level", "MEDIUM")
    rc  = REGIME_COLOR.get(regime_name, "#668899")
    rkc = RISK_COLOR.get(risk_level, "#ffbb33")

    # Risk gauge 바늘
    risk_angle_map = {"LOW": -145, "MEDIUM": -100, "HIGH": -45}
    needle_deg = risk_angle_map.get(risk_level, -100)
    nx2 = int(100 + 62 * math.cos(math.radians(needle_deg)))
    ny2 = int(95  + 62 * math.sin(math.radians(needle_deg)))

    # ── Portfolio Risk (core_data 실제 필드) ──
    pr_return = prisk.get("portfolio_return_impact", "—")
    pr_risk   = prisk.get("portfolio_risk_impact", "—")
    pr_dd     = prisk.get("drawdown_risk", "—")
    pr_crash  = prisk.get("crash_alert_level", "—")
    pr_hedge  = prisk.get("hedge_intensity", "—")
    pr_beta   = prisk.get("position_exposure", "—")
    pr_divscore = prisk.get("diversification_score", 0)

    def _pr_color(val):
        v = str(val).lower()
        if v in ("low", "contained", "defensive"):
            return "#33ff99" if v != "defensive" else "#bbddee"
        if v in ("medium", "moderate"):
            return "#ffbb33"
        if v in ("high", "aggressive", "severe"):
            return "#ff4466"
        return "#bbddee"

    # ── Market Brief (core_data 실제 필드) ──
    brief_text = helpers.get("one_line_summary", "—")

    # ── Scores (core_data 실제 필드) ──
    scores = [
        ("Growth",    ms.get("growth_score", 0)),
        ("Risk",      ms.get("risk_score", 0)),
        ("Inflation", ms.get("inflation_score", 0)),
        ("Liquidity", ms.get("liquidity_score", 0)),
        ("Commodity", ms.get("commodity_pressure_score", 0)),
        ("Stability", ms.get("financial_stability_score", 0)),
    ]

    # ── ETF 행 ──
    max_alloc = max(alloc.values()) if alloc else 30

    def _etf_rows():
        rows = []
        for etf in ETFS:
            s    = strat.get(etf, "Neutral")
            sc   = STANCE_COLOR.get(s, "#99bbdd")
            sig  = timing.get(etf, "HOLD")
            sigc = SIGNAL_COLOR.get(sig, "#ffee44")
            pct  = alloc.get(etf, 0)
            bar_w = int(pct / max_alloc * 100) if max_alloc > 0 else 0
            rows.append(f"""<div class="etf-row">
              <div class="etf-tick">{etf}</div>
              <div class="etf-mid">
                <div class="etf-stance" style="color:{sc}">{s}</div>
                <div class="etf-sig" style="color:{sigc}">{sig}</div>
              </div>
              <div class="etf-bar-wrap">
                <div class="etf-bar-bg"><div class="etf-bar-fill" style="width:{bar_w}%;background:{sc}"></div></div>
                <div class="etf-pct">{pct}%</div>
              </div>
            </div>""")
        return "\n".join(rows)

    def _score_rows():
        rows = []
        for label, val in scores:
            c = _sc(val)
            w = int(val / 5 * 100)
            rows.append(f"""<div class="sc-row">
              <div class="sc-lbl">{label}</div>
              <div class="sc-bar"><div class="sc-fill" style="width:{w}%;background:{c}"></div></div>
              <div class="sc-val" style="color:{c}">{val}/5</div>
            </div>""")
        return "\n".join(rows)

    # ── Snapshot 행 (core_data 실제 필드만, 추측 0건) ──
    # sp500/nasdaq: % 변동만 존재 → % 값을 메인으로 표시
    # vix/us10y/oil/dxy: 절대값만 존재 → 절대값 표시
    snap_rows_data = [
        ("S&P 500", f"{sp500_chg:+.2f}%", _dn_up(sp500_chg), ""),
        ("Nasdaq",  f"{nasdaq_chg:+.2f}%", _dn_up(nasdaq_chg), ""),
        ("VIX",     f"{vix:.2f}", "#ff4466" if vix >= 25 else ("#ffbb33" if vix >= 20 else "#33ff99"),
         f'<div class="dot" style="background:#ff4466;box-shadow:0 0 6px #ff446688"></div>' if vix >= 25 else ""),
        ("US 10Y",  f"{us10y:.2f}%", "#bbddee", ""),
        ("WTI",     f"${oil:.2f}", "#ffbb33" if oil >= 90 else "#bbddee",
         f'<div class="dot" style="background:#ffbb33;box-shadow:0 0 6px #ffbb3388"></div>' if oil >= 90 else ""),
        ("DXY",     f"{dxy:.2f}", "#bbddee", ""),
    ]

    def _snap_rows():
        rows = []
        for name, val, color, dot_html in snap_rows_data:
            rows.append(f"""<div class="snap-row">
              <div class="snap-n">{name}{dot_html}</div>
              <div class="snap-v" style="color:{color}">{val}</div>
            </div>""")
        return "\n".join(rows)

    try:
        from config.settings import SYSTEM_VERSION, CODENAME
    except Exception:
        SYSTEM_VERSION = VERSION
        CODENAME = "EDT Investment"

    # ── HTML ──
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800;900&family=Barlow+Condensed:wght@400;600;700;800;900&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:1080px;height:1080px;overflow:hidden;background:#070b11;font-family:'Barlow',sans-serif;color:#eef6ff;}}
.root{{height:100%;display:flex;flex-direction:column;}}
.rb{{height:3px;background:linear-gradient(90deg,#ff1010,#ff5500,#ff9900,#ffcc00,#aaee00,#11cc55,#00cccc,#0088ff,#7700ff,#ff0099);flex-shrink:0;}}
.hdr{{background:#0c1420;border-bottom:1px solid #2e4868;padding:7px 16px;display:flex;align-items:center;flex-shrink:0;}}
.hdr-left{{display:flex;align-items:center;gap:7px;}}
.hdr-dot{{width:7px;height:7px;border-radius:50%;background:#33ff99;box-shadow:0 0 8px #33ff99;}}
.hdr-brand{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;letter-spacing:2px;color:#99bbdd;}}
.hdr-sep{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#7799bb;}}
.hdr-sub{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:500;letter-spacing:1.5px;color:#7799bb;}}
.hdr-center{{flex:1;text-align:center;}}
.hdr-title{{font-size:20px;font-weight:900;color:#fff;}}
.hdr-title em{{color:#ffbb33;font-style:normal;}}
.hdr-right{{display:flex;align-items:baseline;gap:6px;}}
.hdr-time{{font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:700;color:#f0f8ff;}}
.hdr-tz{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#99bbdd;}}
.hdr-date{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#99bbdd;margin-left:6px;}}
.main{{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;}}
.col{{display:flex;flex-direction:column;}}
.col+.col{{border-left:1px solid #1e3048;}}
.sec{{padding:6px 12px;border-bottom:1px solid #1e3048;}}
.sec:last-child{{border-bottom:none;}}
.sl{{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:2px;color:#7799bb;text-transform:uppercase;margin-bottom:4px;display:flex;align-items:center;gap:6px;}}
.sl::after{{content:'';flex:1;height:1px;background:#1e3048;}}
.snap-row{{display:flex;align-items:center;padding:4px 7px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:4px;margin-bottom:2px;}}
.snap-n{{flex:1;font-size:13px;font-weight:500;display:flex;align-items:center;gap:5px;}}
.snap-v{{font-family:'IBM Plex Mono',monospace;font-size:17px;font-weight:700;}}
.dot{{width:7px;height:7px;border-radius:50%;flex-shrink:0;}}
.fx3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:3px;}}
.fxi{{background:rgba(68,238,255,.08);border:1px solid rgba(68,238,255,.25);border-radius:4px;padding:4px 5px;text-align:center;}}
.fxl{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:#aaeeff;margin-bottom:2px;}}
.fxv{{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;color:#55eeff;}}
.fg-box{{display:flex;align-items:center;gap:10px;padding:6px 8px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:5px;}}
.fg-val{{font-family:'IBM Plex Mono',monospace;font-size:28px;font-weight:800;line-height:1;}}
.fg-label{{font-size:12px;font-weight:600;}}
.fg-sub{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#7799bb;margin-top:2px;}}
.crypto-row{{display:flex;align-items:center;padding:4px 8px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:4px;margin-bottom:2px;}}
.crypto-n{{flex:1;font-size:13px;font-weight:600;color:#eef6ff;}}
.crypto-v{{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:700;color:#ffbb33;}}
.crypto-c{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;margin-left:6px;}}
.gauge-c{{text-align:center;}}
.gauge-lbl{{font-family:'Barlow',sans-serif;font-size:22px;font-weight:900;letter-spacing:4px;margin-top:-4px;}}
.reg-badge{{padding:7px 10px;border-radius:5px;text-align:center;}}
.reg-val{{font-family:'Barlow',sans-serif;font-size:18px;font-weight:900;letter-spacing:3px;}}
.sc-row{{display:flex;align-items:center;gap:6px;margin-bottom:3px;}}
.sc-lbl{{width:70px;font-size:11px;color:#99bbdd;}}
.sc-bar{{flex:1;height:5px;background:#1e3048;border-radius:3px;overflow:hidden;}}
.sc-fill{{height:100%;border-radius:3px;}}
.sc-val{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:700;width:28px;text-align:right;}}
.pr-grid{{display:grid;grid-template-columns:1fr 1fr;gap:3px;}}
.pri{{background:rgba(255,255,255,.035);border:1px solid #1e3048;border-radius:4px;padding:4px 7px;}}
.pr-label{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:#99bbdd;margin-bottom:2px;}}
.pr-val{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:700;}}
.etf-row{{display:flex;align-items:center;padding:4px 7px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:4px;margin-bottom:2px;}}
.etf-tick{{font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:800;color:#eef6ff;width:46px;}}
.etf-mid{{display:flex;flex-direction:column;width:80px;}}
.etf-stance{{font-size:10px;font-weight:600;}}
.etf-sig{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:800;margin-top:1px;}}
.etf-bar-wrap{{flex:1;display:flex;align-items:center;gap:4px;}}
.etf-bar-bg{{flex:1;height:5px;background:#1e3048;border-radius:3px;overflow:hidden;}}
.etf-bar-fill{{height:100%;border-radius:3px;}}
.etf-pct{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:700;color:#eef6ff;width:28px;text-align:right;}}
.brief{{font-size:12px;color:#99bbdd;line-height:1.8;}}
.ftr{{background:#050a10;border-top:1px solid #1e3048;padding:4px 16px;display:flex;align-items:center;gap:8px;flex-shrink:0;}}
.ftr-l{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#7799bb;letter-spacing:2px;}}
.ftag{{font-family:'IBM Plex Mono',monospace;font-size:8px;padding:1px 5px;border-radius:2px;border:1px solid #1e3048;color:#557799;}}
.ftr-r{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#7799bb;margin-left:auto;}}
</style>
</head>
<body>
<div class="root">
<div class="rb"></div>
<div class="hdr">
  <div class="hdr-left">
    <div class="hdr-dot"></div>
    <span class="hdr-brand">EDT INVESTMENT</span>
    <span class="hdr-sep">·</span>
    <span class="hdr-sub">INVESTMENT OS</span>
  </div>
  <div class="hdr-center"><span class="hdr-title">Full <em>Brief</em></span></div>
  <div class="hdr-right">
    <span class="hdr-time">{kst.strftime('%H:%M')}</span>
    <span class="hdr-tz">KST</span>
    <span class="hdr-date">{kst.strftime('%b %d')} · ET {et.strftime('%H:%M')}</span>
  </div>
</div>

<div class="main">
<!-- COL 1: Snapshot + FX + Fear&Greed + Crypto -->
<div class="col">
  <div class="sec">
    <div class="sl">Market Snapshot</div>
    {_snap_rows()}
  </div>
  <div class="sec">
    <div class="sl">FX Rates</div>
    <div class="fx3">
      <div class="fxi"><div class="fxl">USD/KRW</div><div class="fxv">{usdkrw:,.1f}</div></div>
      <div class="fxi"><div class="fxl">EUR/USD</div><div class="fxv">{eurusd:.4f}</div></div>
      <div class="fxi"><div class="fxl">USD/JPY</div><div class="fxv">{usdjpy:.2f}</div></div>
    </div>
  </div>
  <div class="sec">
    <div class="sl">Fear & Greed Index</div>
    <div class="fg-box">
      <div class="fg-val" style="color:{fg_c}">{fg_value}</div>
      <div>
        <div class="fg-label" style="color:{fg_c}">{fg_emoji} {fg_label}</div>
        <div class="fg-sub">prev {fg_prev} · chg {fg_chg:+d}</div>
      </div>
    </div>
  </div>
  <div class="sec">
    <div class="sl">Crypto</div>
    <div class="crypto-row">
      <div class="crypto-n">BTC</div>
      <div class="crypto-v">${btc_usd:,.0f}</div>
      <div class="crypto-c" style="color:{_dn_up(btc_chg)}">{_sign(btc_chg)}</div>
    </div>
    <div class="crypto-row">
      <div class="crypto-n">ETH</div>
      <div class="crypto-v">${eth_usd:,.0f}</div>
      <div class="crypto-c" style="color:{_dn_up(eth_chg)}">{_sign(eth_chg)}</div>
    </div>
  </div>
</div>

<!-- COL 2: Risk + Regime + Score + PRisk -->
<div class="col">
  <div class="sec">
    <div class="sl">Market Risk Level</div>
    <div class="gauge-c">
      <svg width="200" height="130" viewBox="0 0 200 130" overflow="visible" style="display:block;margin:0 auto;">
        <defs><linearGradient id="g1" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#33ff99"/><stop offset="28%" stop-color="#aaff00"/>
          <stop offset="52%" stop-color="#ffee44"/><stop offset="72%" stop-color="#ff8800"/>
          <stop offset="100%" stop-color="#ff2244"/>
        </linearGradient></defs>
        <path d="M 28 95 A 72 72 0 0 1 172 95" fill="none" stroke="#162030" stroke-width="14" stroke-linecap="round"/>
        <path d="M 28 95 A 72 72 0 0 1 172 95" fill="none" stroke="url(#g1)" stroke-width="14" stroke-linecap="round" opacity=".9"/>
        <line x1="100" y1="95" x2="{nx2}" y2="{ny2}" stroke="white" stroke-width="2.5" stroke-linecap="round"/>
        <circle cx="100" cy="95" r="5" fill="#0d1824" stroke="white" stroke-width="2"/>
        <circle cx="100" cy="95" r="2.5" fill="white"/>
        <text x="16" y="118" fill="#33ff99" font-family="IBM Plex Mono" font-size="9" font-weight="700">SAFE</text>
        <text x="100" y="14" fill="#ffee44" font-family="IBM Plex Mono" font-size="9" font-weight="700" text-anchor="middle">MED</text>
        <text x="184" y="118" fill="#ff2244" font-family="IBM Plex Mono" font-size="9" font-weight="700" text-anchor="end">HIGH</text>
      </svg>
      <div class="gauge-lbl" style="color:{rkc};text-shadow:0 0 14px {rkc}66">{risk_level}</div>
    </div>
  </div>
  <div class="sec">
    <div class="sl">Market Regime</div>
    <div class="reg-badge" style="background:{rc}1a;border:2px solid {rc}55">
      <div class="reg-val" style="color:{rc};text-shadow:0 0 12px {rc}44">{regime_name.upper()} REGIME</div>
    </div>
  </div>
  <div class="sec">
    <div class="sl">Market Score</div>
    {_score_rows()}
  </div>
  <div class="sec">
    <div class="sl">Portfolio Risk</div>
    <div class="pr-grid">
      <div class="pri"><div class="pr-label">Return</div><div class="pr-val" style="color:{_pr_color(pr_return)}">{pr_return}</div></div>
      <div class="pri"><div class="pr-label">Risk</div><div class="pr-val" style="color:{_pr_color(pr_risk)}">{pr_risk}</div></div>
      <div class="pri"><div class="pr-label">Drawdown</div><div class="pr-val" style="color:{_pr_color(pr_dd)}">{pr_dd}</div></div>
      <div class="pri"><div class="pr-label">Crash</div><div class="pr-val" style="color:{_pr_color(pr_crash)}">{pr_crash}</div></div>
      <div class="pri"><div class="pr-label">Hedge</div><div class="pr-val" style="color:{_pr_color(pr_hedge)}">{pr_hedge}</div></div>
      <div class="pri"><div class="pr-label">Beta</div><div class="pr-val" style="color:{_pr_color(pr_beta)}">{pr_beta}</div></div>
    </div>
  </div>
</div>

<!-- COL 3: ETF + Brief -->
<div class="col">
  <div class="sec">
    <div class="sl">ETF Strategy · Signal · Allocation</div>
    {_etf_rows()}
  </div>
  <div class="sec">
    <div class="sl">Market Brief</div>
    <div class="brief">{brief_text}</div>
  </div>
</div>
</div>

<div class="ftr">
  <span class="ftr-l">{CODENAME} · {SYSTEM_VERSION}</span>
  <span class="ftag">YAHOO·RSS·API</span>
  <span class="ftag">NOT FINANCIAL ADVICE</span>
  <span class="ftr-r">{kst.strftime('%b %d, %Y')} · {kst.strftime('%H:%M')} KST</span>
</div>
</div>
</body>
</html>"""


# ── Playwright 렌더링 (기존 유지) ──
async def _render_async(url: str, out_path: str) -> bool:
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1080, "height": 1080})
            await page.goto(url, wait_until="load")
            await page.wait_for_timeout(1500)
            await page.screenshot(
                path=out_path,
                clip={"x": 0, "y": 0, "width": 1080, "height": 1080},
            )
            await browser.close()
        return True
    except Exception as e:
        logger.error(f"[HtmlDash] Playwright 렌더링 실패: {e}", exc_info=True)
        return False


def _render(url: str, out_path: str) -> bool:
    try:
        return asyncio.run(_render_async(url, out_path))
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, _render_async(url, out_path))
            return future.result()


def build_html_dashboard(
    data: dict,
    session: str = "full",
    dt_utc: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    try:
        if dt_utc is None:
            dt_utc = datetime.now(timezone.utc)
        if output_dir is None:
            try:
                from config.settings import IMAGES_DIR
                output_dir = Path(IMAGES_DIR)
            except Exception:
                output_dir = Path("data/images")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        fname = f"dashboard_full_{dt_utc.strftime('%Y%m%d_%H%M')}.png"
        fpath = str(output_dir / fname)

        logger.info(f"[HtmlDash] {VERSION} HTML 빌드 시작")
        html = _build_html(data, dt_utc)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            tmp_path = f.name

        logger.info("[HtmlDash] Playwright 렌더링 시작")
        ok = _render(f"file://{tmp_path}", fpath)

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if ok and os.path.exists(fpath):
            logger.info(f"[HtmlDash] 저장 완료: {fpath}")
            return fpath
        else:
            logger.error("[HtmlDash] 렌더링 실패 — PNG 미생성")
            return None
    except Exception as e:
        logger.error(f"[HtmlDash] 예외 발생: {e}", exc_info=True)
        return None
