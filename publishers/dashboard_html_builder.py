"""
publishers/dashboard_html_builder.py
======================================
HTML/Playwright 기반 풀버전 대시보드 이미지 생성기 (session=full 전용)
v2.2.1 — Crypto 기준 높이 압축, ETF 상단 정렬

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

VERSION = "v2.2.1"

REGIME_COLOR = {
    "Risk-On": "#22ee88", "Risk-Off": "#ff4466", "Oil Shock": "#ffbb00",
    "Liquidity Crisis": "#bb88ff", "Recession Risk": "#ff2266",
    "Stagflation Risk": "#ffaa33", "AI Bubble": "#44aaff", "Transition": "#668899",
}
RISK_COLOR   = {"LOW": "#33ff99", "MEDIUM": "#ffbb33", "HIGH": "#ff4466"}
STANCE_COLOR = {"Overweight": "#33ff99", "Underweight": "#ff4466", "Neutral": "#99bbdd", "Hedge": "#bb88ff"}
SIGNAL_COLOR = {"BUY": "#33ff99", "ADD": "#55eeff", "ADD ON PULLBACK": "#33ff99", "HOLD": "#ffee44", "REDUCE": "#ff4466", "HEDGE": "#bb88ff", "SELL": "#ff4466"}
SCORE_THRESHOLDS = [(1, "#33ff99"), (2, "#55eeff"), (3, "#ffee44"), (4, "#ffbb33"), (5, "#ff4466")]
ETFS = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]

def _sc(v):
    for t, c in SCORE_THRESHOLDS:
        if v <= t: return c
    return "#ff4466"

def _sign(v):
    return f"▼{abs(v):.2f}%" if v < 0 else f"▲{v:.2f}%"

def _dn_up(v):
    return "#ff4466" if v < 0 else "#33ff99"

def _fg_color(v):
    if v <= 20: return "#ff4466"
    if v <= 40: return "#ffbb33"
    if v <= 60: return "#99bbdd"
    if v <= 80: return "#33ff99"
    return "#22ee88"


def _build_html(data: dict, dt_utc: datetime) -> str:
    kst = dt_utc + timedelta(hours=9)
    et  = dt_utc - timedelta(hours=4)

    snap    = data.get("market_snapshot", {})
    regime  = data.get("market_regime", {})
    ms      = data.get("market_score", {})
    strat   = data.get("etf_strategy", {}).get("stance", {})
    alloc   = data.get("etf_allocation", {}).get("allocation", {})
    prisk   = data.get("portfolio_risk", {})
    helpers = data.get("output_helpers", {})
    fx      = data.get("fx_rates", {})
    timing  = data.get("etf_analysis", {}).get("timing_signal", {})
    fg      = data.get("fear_greed", {})
    crypto  = data.get("crypto", {})
    macro   = data.get("macro_data", {})

    sp500_chg  = snap.get("sp500", 0) or 0
    nasdaq_chg = snap.get("nasdaq", 0) or 0
    vix        = snap.get("vix", 0) or 0
    us10y      = snap.get("us10y", 0) or 0
    oil        = snap.get("oil", 0) or 0
    dxy        = snap.get("dollar_index", 0) or 0
    usdkrw = fx.get("usdkrw") or 0
    eurusd = fx.get("eurusd") or 0
    usdjpy = fx.get("usdjpy") or 0

    fg_value = fg.get("value", 0) or 0
    fg_label = fg.get("label", "—")
    fg_prev  = fg.get("prev_value", 0) or 0
    fg_chg   = fg.get("change", 0) or 0
    fg_emoji = fg.get("emoji", "")
    fg_c     = _fg_color(fg_value)

    btc_usd = crypto.get("btc_usd", 0) or 0
    btc_chg = crypto.get("btc_change_pct", 0) or 0
    eth_usd = crypto.get("eth_usd", 0) or 0
    eth_chg = crypto.get("eth_change_pct", 0) or 0

    fed_rate    = macro.get("fed_funds_rate")
    hy_spread   = macro.get("hy_spread")
    yield_curve = macro.get("yield_curve")
    curve_inverted = macro.get("yield_curve_inverted", False)

    def _fmt_fred(v):
        if v is None: return "—"
        try: return f"{float(v):.2f}%"
        except (ValueError, TypeError): return str(v)

    yc_color = "#ff4466" if curve_inverted else "#33ff99"

    regime_name = regime.get("market_regime", "Transition")
    risk_level  = regime.get("market_risk_level", "MEDIUM")
    rc  = REGIME_COLOR.get(regime_name, "#668899")
    rkc = RISK_COLOR.get(risk_level, "#ffbb33")

    needle_deg = {"LOW": -145, "MEDIUM": -100, "HIGH": -45}.get(risk_level, -100)

    pr_return = prisk.get("portfolio_return_impact", "—")
    pr_risk   = prisk.get("portfolio_risk_impact", "—")
    pr_dd     = prisk.get("drawdown_risk", "—")
    pr_crash  = prisk.get("crash_alert_level", "—")
    pr_hedge  = prisk.get("hedge_intensity", "—")
    pr_beta   = prisk.get("position_exposure", "—")

    def _prc(val):
        v = str(val).lower()
        if v in ("low","contained","defensive"): return "#33ff99" if v != "defensive" else "#bbddee"
        if v in ("medium","moderate"): return "#ffbb33"
        if v in ("high","aggressive","severe"): return "#ff4466"
        return "#bbddee"

    brief_text = helpers.get("one_line_summary", "—")
    max_alloc = max(alloc.values()) if alloc else 30

    scores = [
        ("Growth", ms.get("growth_score",0)), ("Risk", ms.get("risk_score",0)),
        ("Inflation", ms.get("inflation_score",0)), ("Liquidity", ms.get("liquidity_score",0)),
        ("Commodity", ms.get("commodity_pressure_score",0)), ("Stability", ms.get("financial_stability_score",0)),
    ]

    def _etf_rows():
        r = []
        for etf in ETFS:
            s = strat.get(etf,"Neutral"); sc = STANCE_COLOR.get(s,"#99bbdd")
            sig = timing.get(etf,"HOLD"); sigc = SIGNAL_COLOR.get(sig,"#ffee44")
            pct = alloc.get(etf,0); bw = int(pct/max_alloc*100) if max_alloc>0 else 0
            r.append(f'<div class="er"><div class="et">{etf}</div><div class="em"><div class="es" style="color:{sc}">{s}</div><div class="ei" style="color:{sigc}">{sig}</div></div><div class="ew"><div class="eb"><div class="ef" style="width:{bw}%;background:{sc}"></div></div><div class="ep">{pct}%</div></div></div>')
        return "\n".join(r)

    def _score_rows():
        r = []
        for l, v in scores:
            c = _sc(v); w = int(v/5*100)
            r.append(f'<div class="sr"><div class="sn">{l}</div><div class="sb"><div class="sf" style="width:{w}%;background:{c}"></div></div><div class="sv" style="color:{c}">{v}/5</div></div>')
        return "\n".join(r)

    snap_data = [
        ("S&P 500", f"{sp500_chg:+.2f}%", _dn_up(sp500_chg), ""),
        ("Nasdaq", f"{nasdaq_chg:+.2f}%", _dn_up(nasdaq_chg), ""),
        ("VIX", f"{vix:.2f}", "#ff4466" if vix>=25 else ("#ffbb33" if vix>=20 else "#33ff99"),
         f'<div class="dot" style="background:#ff4466;box-shadow:0 0 5px #ff446688"></div>' if vix>=25 else ""),
        ("US 10Y", f"{us10y:.2f}%", "#bbddee", ""),
        ("WTI", f"${oil:.2f}", "#ffbb33" if oil>=90 else "#bbddee",
         f'<div class="dot" style="background:#ffbb33;box-shadow:0 0 5px #ffbb3388"></div>' if oil>=90 else ""),
        ("DXY", f"{dxy:.2f}", "#bbddee", ""),
    ]

    def _snap_rows():
        r = []
        for n, v, c, d in snap_data:
            r.append(f'<div class="mr"><div class="mn">{n}{d}</div><div class="mv" style="color:{c}">{v}</div></div>')
        return "\n".join(r)

    try:
        from config.settings import SYSTEM_VERSION, CODENAME
    except Exception:
        SYSTEM_VERSION = VERSION
        CODENAME = "EDT Investment"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800;900&family=Barlow+Condensed:wght@400;600;700;800;900&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:1080px;overflow:hidden;background:#070b11;font-family:'Barlow',sans-serif;color:#eef6ff;}}
.root{{display:flex;flex-direction:column;}}
.rb{{height:3px;background:linear-gradient(90deg,#ff1010,#ff5500,#ff9900,#ffcc00,#aaee00,#11cc55,#00cccc,#0088ff,#7700ff,#ff0099);flex-shrink:0;}}
.hd{{background:#0c1420;border-bottom:1px solid #2e4868;padding:5px 14px;display:flex;align-items:center;flex-shrink:0;}}
.hl{{display:flex;align-items:center;gap:5px;}}
.hd .dg{{width:6px;height:6px;border-radius:50%;background:#33ff99;box-shadow:0 0 6px #33ff99;}}
.hb{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:2px;color:#99bbdd;}}
.hs{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#7799bb;}}
.hc{{flex:1;text-align:center;font-size:16px;font-weight:900;color:#fff;}}
.hc em{{color:#ffbb33;font-style:normal;}}
.hr{{display:flex;align-items:baseline;gap:4px;}}
.ht{{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:#f0f8ff;}}
.hz{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;color:#99bbdd;}}
.hx{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#99bbdd;margin-left:4px;}}
.main{{display:grid;grid-template-columns:1fr 1fr 1fr;}}
.col{{display:flex;flex-direction:column;}}.col+.col{{border-left:1px solid #1e3048;}}
.s{{padding:3px 8px;border-bottom:1px solid #1e3048;}}.s:last-child{{border-bottom:none;}}
.sl{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:1.5px;color:#7799bb;text-transform:uppercase;margin-bottom:2px;display:flex;align-items:center;gap:4px;}}.sl::after{{content:'';flex:1;height:1px;background:#1e3048;}}
.mr{{display:flex;align-items:center;padding:2px 5px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:3px;margin-bottom:1px;}}
.mn{{flex:1;font-size:11px;font-weight:500;display:flex;align-items:center;gap:3px;}}
.mv{{font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:700;}}
.dot{{width:5px;height:5px;border-radius:50%;flex-shrink:0;}}
.fx{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px;}}
.fi{{background:rgba(68,238,255,.08);border:1px solid rgba(68,238,255,.2);border-radius:3px;padding:2px 3px;text-align:center;}}
.fl{{font-family:'IBM Plex Mono',monospace;font-size:8px;font-weight:600;color:#aaeeff;}}
.fv{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;color:#55eeff;}}
.fg{{display:flex;align-items:center;gap:6px;padding:2px 4px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:3px;}}
.fgv{{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:800;line-height:1;}}
.fgl{{font-size:10px;font-weight:600;}}
.fgs{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#7799bb;}}
.fd{{display:flex;align-items:center;padding:2px 5px;background:rgba(255,255,255,.025);border:1px solid #1e3048;border-radius:3px;margin-bottom:1px;}}
.fdn{{flex:1;font-size:11px;font-weight:500;color:#eef6ff;}}
.fdv{{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;margin-right:4px;}}
.cr{{display:flex;align-items:center;padding:2px 5px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:3px;margin-bottom:1px;}}
.cn{{flex:1;font-size:11px;font-weight:600;}}
.cv{{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;color:#ffbb33;}}
.cc{{font-family:'IBM Plex Mono',monospace;font-size:8px;font-weight:600;margin-left:4px;}}
.gc{{text-align:center;}}
.gl{{font-family:'Barlow',sans-serif;font-size:18px;font-weight:900;letter-spacing:3px;margin-top:-2px;}}
.rb2{{padding:5px 7px;border-radius:4px;text-align:center;}}
.rv{{font-family:'Barlow',sans-serif;font-size:14px;font-weight:900;letter-spacing:2px;}}
.sr{{display:flex;align-items:center;gap:4px;margin-bottom:1px;}}
.sn{{width:58px;font-size:9px;color:#99bbdd;}}
.sb{{flex:1;height:3px;background:#1e3048;border-radius:2px;overflow:hidden;}}
.sf{{height:100%;border-radius:2px;}}
.sv{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;width:24px;text-align:right;}}
.pg{{display:grid;grid-template-columns:1fr 1fr;gap:2px;}}
.pi{{background:rgba(255,255,255,.035);border:1px solid #1e3048;border-radius:3px;padding:2px 5px;}}
.pl{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;color:#99bbdd;}}
.pv{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;}}
.er{{display:flex;align-items:center;padding:2px 5px;background:rgba(255,255,255,.03);border:1px solid #1e3048;border-radius:3px;margin-bottom:1px;}}
.et{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:800;color:#eef6ff;width:38px;}}
.em{{display:flex;flex-direction:column;width:72px;}}
.es{{font-size:8px;font-weight:600;}}
.ei{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:800;}}
.ew{{flex:1;display:flex;align-items:center;gap:3px;}}
.eb{{flex:1;height:3px;background:#1e3048;border-radius:2px;overflow:hidden;}}
.ef{{height:100%;border-radius:2px;}}
.ep{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;color:#eef6ff;width:24px;text-align:right;}}
.br{{font-size:10px;color:#99bbdd;line-height:1.6;}}
.ft{{background:#050a10;border-top:1px solid #1e3048;padding:2px 12px;display:flex;align-items:center;gap:5px;flex-shrink:0;}}
.ftl{{font-family:'IBM Plex Mono',monospace;font-size:7px;color:#7799bb;letter-spacing:1.5px;}}
.ftg{{font-family:'IBM Plex Mono',monospace;font-size:6px;padding:1px 3px;border-radius:2px;border:1px solid #1e3048;color:#557799;}}
.ftr{{font-family:'IBM Plex Mono',monospace;font-size:7px;color:#7799bb;margin-left:auto;}}
</style>
</head>
<body>
<div class="root">
<div class="rb"></div>
<div class="hd">
  <div class="hl"><div class="dg"></div><span class="hb">EDT INVESTMENT</span><span class="hs">· INVESTMENT OS</span></div>
  <div class="hc">Full <em>Brief</em></div>
  <div class="hr"><span class="ht">{kst.strftime('%H:%M')}</span><span class="hz">KST</span><span class="hx">{kst.strftime('%b %d')} · ET {et.strftime('%H:%M')}</span></div>
</div>
<div class="main">
<div class="col">
  <div class="s">{_snap_rows()}</div>
  <div class="s"><div class="sl">FX Rates</div>
    <div class="fx">
      <div class="fi"><div class="fl">USD/KRW</div><div class="fv">{usdkrw:,.1f}</div></div>
      <div class="fi"><div class="fl">EUR/USD</div><div class="fv">{eurusd:.4f}</div></div>
      <div class="fi"><div class="fl">USD/JPY</div><div class="fv">{usdjpy:.2f}</div></div>
    </div>
  </div>
  <div class="s"><div class="sl">Fear & Greed</div>
    <div class="fg"><div class="fgv" style="color:{fg_c}">{fg_value}</div><div><div class="fgl" style="color:{fg_c}">{fg_emoji} {fg_label}</div><div class="fgs">prev {fg_prev} · chg {fg_chg:+d}</div></div></div>
  </div>
  <div class="s"><div class="sl">FRED Macro</div>
    <div class="fd"><div class="fdn">Fed Funds Rate</div><div class="fdv" style="color:#99bbdd">{_fmt_fred(fed_rate)}</div><div class="dot" style="background:#99bbdd;box-shadow:0 0 4px #99bbdd88"></div></div>
    <div class="fd"><div class="fdn">HY Spread</div><div class="fdv" style="color:#99bbdd">{_fmt_fred(hy_spread)}</div><div class="dot" style="background:#99bbdd;box-shadow:0 0 4px #99bbdd88"></div></div>
    <div class="fd"><div class="fdn">Yield Curve</div><div class="fdv" style="color:{yc_color}">{_fmt_fred(yield_curve)}</div><div class="dot" style="background:{yc_color};box-shadow:0 0 4px {yc_color}88"></div></div>
  </div>
  <div class="s"><div class="sl">Crypto</div>
    <div class="cr"><div class="cn">BTC</div><div class="cv">${btc_usd:,.0f}</div><div class="cc" style="color:{_dn_up(btc_chg)}">{_sign(btc_chg)}</div></div>
    <div class="cr"><div class="cn">ETH</div><div class="cv">${eth_usd:,.0f}</div><div class="cc" style="color:{_dn_up(eth_chg)}">{_sign(eth_chg)}</div></div>
  </div>
</div>
<div class="col">
  <div class="s">
    <div class="sl">Market Risk Level</div>
    <div class="gc">
      <svg width="160" height="100" viewBox="0 0 160 100" overflow="visible" style="display:block;margin:0 auto;">
        <defs><linearGradient id="g1" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#33ff99"/><stop offset="28%" stop-color="#aaff00"/><stop offset="52%" stop-color="#ffee44"/><stop offset="72%" stop-color="#ff8800"/><stop offset="100%" stop-color="#ff2244"/></linearGradient></defs>
        <path d="M 18 75 A 62 62 0 0 1 142 75" fill="none" stroke="#162030" stroke-width="11" stroke-linecap="round"/>
        <path d="M 18 75 A 62 62 0 0 1 142 75" fill="none" stroke="url(#g1)" stroke-width="11" stroke-linecap="round" opacity=".9"/>
        <line x1="80" y1="75" x2="{int(80+52*math.cos(math.radians(needle_deg)))}" y2="{int(75+52*math.sin(math.radians(needle_deg)))}" stroke="white" stroke-width="2" stroke-linecap="round"/>
        <circle cx="80" cy="75" r="4" fill="#0d1824" stroke="white" stroke-width="1.5"/>
        <circle cx="80" cy="75" r="2" fill="white"/>
        <text x="12" y="92" fill="#33ff99" font-family="IBM Plex Mono" font-size="7" font-weight="700">SAFE</text>
        <text x="80" y="8" fill="#ffee44" font-family="IBM Plex Mono" font-size="7" font-weight="700" text-anchor="middle">MED</text>
        <text x="148" y="92" fill="#ff2244" font-family="IBM Plex Mono" font-size="7" font-weight="700" text-anchor="end">HIGH</text>
      </svg>
      <div class="gl" style="color:{rkc};text-shadow:0 0 10px {rkc}66">{risk_level}</div>
    </div>
  </div>
  <div class="s"><div class="sl">Market Regime</div>
    <div class="rb2" style="background:{rc}1a;border:2px solid {rc}55"><div class="rv" style="color:{rc};text-shadow:0 0 8px {rc}44">{regime_name.upper()} REGIME</div></div>
  </div>
  <div class="s"><div class="sl">Market Score</div>{_score_rows()}</div>
  <div class="s"><div class="sl">Portfolio Risk</div>
    <div class="pg">
      <div class="pi"><div class="pl">Return</div><div class="pv" style="color:{_prc(pr_return)}">{pr_return}</div></div>
      <div class="pi"><div class="pl">Risk</div><div class="pv" style="color:{_prc(pr_risk)}">{pr_risk}</div></div>
      <div class="pi"><div class="pl">Drawdown</div><div class="pv" style="color:{_prc(pr_dd)}">{pr_dd}</div></div>
      <div class="pi"><div class="pl">Crash</div><div class="pv" style="color:{_prc(pr_crash)}">{pr_crash}</div></div>
      <div class="pi"><div class="pl">Hedge</div><div class="pv" style="color:{_prc(pr_hedge)}">{pr_hedge}</div></div>
      <div class="pi"><div class="pl">Beta</div><div class="pv" style="color:{_prc(pr_beta)}">{pr_beta}</div></div>
    </div>
  </div>
</div>
<div class="col">
  <div class="s"><div class="sl">ETF Strategy · Signal · Allocation</div>{_etf_rows()}</div>
  <div class="s"><div class="sl">Market Brief</div><div class="br">{brief_text}</div></div>
</div>
</div>
<div class="ft">
  <span class="ftl">{CODENAME} · {SYSTEM_VERSION}</span>
  <span class="ftg">FRED·YAHOO·RSS</span>
  <span class="ftg">NOT FINANCIAL ADVICE</span>
  <span class="ftr">{kst.strftime('%b %d, %Y')} · {kst.strftime('%H:%M')} KST</span>
</div>
</div>
</body>
</html>"""


async def _render_async(url: str, out_path: str) -> bool:
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1080, "height": 1080})
            await page.goto(url, wait_until="load")
            await page.wait_for_timeout(1500)
            await page.screenshot(path=out_path, clip={"x": 0, "y": 0, "width": 1080, "height": 1080})
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
            return ex.submit(asyncio.run, _render_async(url, out_path)).result()

def build_html_dashboard(data: dict, session: str = "full", dt_utc: Optional[datetime] = None, output_dir: Optional[Path] = None) -> Optional[str]:
    try:
        if dt_utc is None: dt_utc = datetime.now(timezone.utc)
        if output_dir is None:
            try:
                from config.settings import IMAGES_DIR
                output_dir = Path(IMAGES_DIR)
            except Exception:
                output_dir = Path("data/images")
        output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
        fname = f"dashboard_full_{dt_utc.strftime('%Y%m%d_%H%M')}.png"
        fpath = str(output_dir / fname)
        logger.info(f"[HtmlDash] {VERSION} HTML 빌드 시작")
        html = _build_html(data, dt_utc)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html); tmp_path = f.name
        logger.info("[HtmlDash] Playwright 렌더링 시작")
        ok = _render(f"file://{tmp_path}", fpath)
        try: os.unlink(tmp_path)
        except Exception: pass
        if ok and os.path.exists(fpath):
            logger.info(f"[HtmlDash] 저장 완료: {fpath}"); return fpath
        else:
            logger.error("[HtmlDash] 렌더링 실패"); return None
    except Exception as e:
        logger.error(f"[HtmlDash] 예외: {e}", exc_info=True); return None
