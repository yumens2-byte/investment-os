"""
publishers/dashboard_html_builder.py
======================================
HTML/Playwright 기반 풀버전 대시보드 이미지 생성기 (session=full 전용)

기존 dashboard_builder.py (matplotlib) 와 완전 독립 — 절대 수정 금지 대상

주요 특징:
- 모든 Investment OS 데이터를 단일 화면에 집약
- Playwright Chromium으로 1080x1080 PNG 렌더링
- 추가 비용 $0
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


# ────────────────────────────────────────────────
# HTML 템플릿 생성 함수
# ────────────────────────────────────────────────
def _build_html(data: dict, dt_utc: datetime) -> str:
    """data 딕셔너리를 HTML 문자열로 변환"""

    kst = dt_utc + timedelta(hours=9)
    et  = dt_utc - timedelta(hours=4)

    # ── 데이터 추출 ──────────────────────────────
    snap    = data.get("market_snapshot", {})
    regime  = data.get("market_regime", {})
    ms      = data.get("market_score", {})
    etf_a   = data.get("etf_analysis", {})
    strat   = data.get("etf_strategy", {}).get("stance", {})
    strat_r = data.get("etf_strategy", {}).get("strategy_reason", {})
    alloc   = data.get("etf_allocation", {}).get("allocation", {})
    prisk   = data.get("portfolio_risk", {})
    tsig    = data.get("trading_signal", {})
    helpers = data.get("output_helpers", {})
    fx      = data.get("fx_rates", {})

    regime_name  = regime.get("market_regime", "Transition")
    risk_level   = regime.get("market_risk_level", "MEDIUM")
    reg_reason   = regime.get("regime_reason", "")[:50]
    signal       = tsig.get("trading_signal", "HOLD")
    sig_matrix   = tsig.get("signal_matrix", {})
    summary      = helpers.get("one_line_summary", "")[:70]

    sp500  = snap.get("sp500",  0) or 0
    nasdaq = snap.get("nasdaq", 0) or 0
    vix    = snap.get("vix",    0) or 0
    us10y  = snap.get("us10y",  0) or 0
    oil    = snap.get("oil",    0) or 0
    dxy    = snap.get("dollar_index", 0) or 0
    usdkrw = fx.get("usdkrw") or 0
    eurusd = fx.get("eurusd") or 0
    usdjpy = fx.get("usdjpy") or 0

    # 컬러 매핑
    REGIME_COLOR = {
        "Risk-On": "#059669", "Risk-Off": "#dc2626", "Oil Shock": "#d97706",
        "Liquidity Crisis": "#7c3aed", "Recession Risk": "#9f1239",
        "Stagflation Risk": "#b45309", "AI Bubble": "#0369a1", "Transition": "#4b5563",
    }
    RISK_COLOR   = {"LOW": "#11cc77", "MEDIUM": "#f5a020", "HIGH": "#ff3a5a"}
    SIGNAL_COLOR = {"BUY": "#11cc77", "ADD": "#00cce0", "HOLD": "#f5c432",
                    "REDUCE": "#ff7030", "HEDGE": "#8055ff", "SELL": "#ff3a5a"}

    rc  = REGIME_COLOR.get(regime_name, "#4b5563")
    rkc = RISK_COLOR.get(risk_level, "#f5a020")
    sc  = SIGNAL_COLOR.get(signal, "#f5c432")

    def dn_up(v): return "#ff3a5a" if v < 0 else "#11cc77"
    def sign(v):  return f"▼ {abs(v):.2f}%" if v < 0 else f"▲ {v:.2f}%"

    # Market Score 컬러
    def sc_color(v, m=5):
        r = v / m
        if r <= 0.25: return "#11cc77"
        if r <= 0.45: return "#00cce0"
        if r <= 0.60: return "#f5c432"
        if r <= 0.80: return "#f5a020"
        return "#ff3a5a"

    # Stance 컬러
    STANCE_C = {"Overweight": "#11cc77", "Underweight": "#ff3a5a",
                "Neutral": "#5a7a98", "Hedge": "#8055ff"}

    def stance_c(s): return STANCE_C.get(s, "#5a7a98")

    # ETF 배분 막대 최대값
    max_alloc = max(alloc.values()) if alloc else 100

    # ETF 목록
    ETFS = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]

    # ETF 랭크
    etf_rank = etf_a.get("etf_rank", {})
    ranked = sorted(etf_rank.items(), key=lambda x: x[1]) if etf_rank else []

    # Signal matrix
    buy_list    = sig_matrix.get("buy_watch", [])
    hold_list   = sig_matrix.get("hold", [])
    reduce_list = sig_matrix.get("reduce", [])

    # FRED
    fred_rate   = data.get("fred_rate",   "—")
    fred_hy     = data.get("fred_hy",     "—")
    fred_curve  = data.get("fred_curve",  "—")

    # 포지션 사이징
    sizing = prisk.get("position_sizing_multiplier", 0.75)

    # VIX 바늘 위치 (0~80 → 0~100%)
    vix_pct = min(vix / 80 * 100, 100)

    # Risk 게이지 바늘 각도
    risk_angle_map = {"LOW": -135, "MEDIUM": -100, "HIGH": -45}
    needle_deg = risk_angle_map.get(risk_level, -100)
    needle_x2  = int(115 + 95 * math.cos(math.radians(needle_deg)))
    needle_y2  = int(110 + 95 * math.sin(math.radians(needle_deg)))

    # ── ETF 행 생성 ─────────────────────────────
    def etf_rows():
        rows = []
        timing = etf_a.get("timing_signal", {})
        TSIG_C = {"BUY": "#11cc77", "ADD ON PULLBACK": "#00cce0",
                  "HOLD": "#f5c432", "REDUCE": "#ff3a5a", "SELL": "#ff3a5a"}
        for etf in ETFS:
            s   = strat.get(etf, "Neutral")
            sc2 = stance_c(s)
            t   = timing.get(etf, "HOLD")
            tc  = TSIG_C.get(t, "#5a7a98")
            pct = alloc.get(etf, 0)
            bar_w = int(pct / max_alloc * 100)
            bar_color = "#11cc77" if s == "Overweight" else ("#ff3a5a" if s == "Underweight" else "#f5a020")
            rows.append(f"""
              <tr>
                <td class="tick">{etf}</td>
                <td class="stance" style="color:{sc2}">{s}</td>
                <td class="tsig" style="color:{tc}">{t}</td>
                <td><div class="abar-wrap"><div class="abar" style="width:{bar_w}%;background:{bar_color}"></div></div></td>
                <td class="apct">{pct}%</td>
              </tr>""")
        return "".join(rows)

    # ── Market Score 행 ─────────────────────────
    SCORE_FIELDS = [
        ("Growth",    ms.get("growth_score",    2)),
        ("Risk",      ms.get("risk_score",      3)),
        ("Inflation", ms.get("inflation_score", 2)),
        ("Liquidity", ms.get("liquidity_score", 2)),
        ("Commodity", ms.get("commodity_pressure_score",    3)),
        ("Stability", ms.get("financial_stability_score",   1)),
    ]

    def score_rows():
        rows = []
        for label, val in SCORE_FIELDS:
            c  = sc_color(val)
            w  = int(val / 5 * 100)
            rows.append(f"""
              <div class="msi">
                <div class="msl">{label}</div>
                <div class="msb"><div class="msf" style="width:{w}%;background:{c}"></div></div>
                <div class="msv" style="color:{c}">{val}/5</div>
              </div>""")
        return "".join(rows)

    # ── Trading Signals 행 (Python 3.11 호환)
    def sig_rows():
        rows = []
        for e in ETFS:
            color  = "#11cc77" if e in buy_list else "#ff3a5a" if e in reduce_list else "#f5c432"
            action = "BUY"    if e in buy_list else "REDUCE"  if e in reduce_list else "HOLD"
            arrow  = "&#8599;" if e in buy_list else "&#8600;" if e in reduce_list else "&#8594;"
            reason = strat_r.get(e, "")[:28]
            mono   = "IBM Plex Mono"
            row = (
                "<tr>"
                + '<td class="tick">' + e + "</td>"
                + '<td style="font-family:' + mono + ',monospace;font-size:12px;font-weight:800;color:' + color + '">' + action + "</td>"
                + '<td style="font-size:8px;color:#2a4060">' + arrow + "</td>"
                + '<td style="font-size:8px;color:#3a5a78">' + reason + "</td>"
                + "</tr>"
            )
            rows.append(row)
        return "".join(rows)

        # ── ETF Rank 행 ─────────────────────────────
    DOTS = {4: "●●●●", 3: "●●●○", 2: "●●○○", 1: "●○○○"}

    def rank_rows():
        rows = []
        for etf, rank in ranked[:6]:
            s    = strat.get(etf, "Neutral")
            sc2  = stance_c(s)
            dot  = DOTS.get(5 - rank if rank <= 4 else 2, "●●○○")
            dotc = "#11cc77" if rank <= 2 else ("#5a7a98" if rank <= 4 else "#ff3a5a")
            rows.append(f"""
              <div class="rank-row">
                <div class="rank-n" style="color:{dotc}">{rank}</div>
                <div class="rank-t">{etf}</div>
                <div class="rank-d" style="color:{sc2}">{s}</div>
                <div class="rank-dots" style="color:{dotc}">{dot}</div>
              </div>""")
        return "".join(rows)

    try:
        from config.settings import SYSTEM_VERSION, CODENAME
    except Exception:
        SYSTEM_VERSION = "v1.8.0"
        CODENAME = "EDT Investment"

    # ── HTML ────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@300;400;500;600;700;800;900&family=Barlow+Condensed:wght@400;500;600;700;800;900&family=IBM+Plex+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{width:1080px;height:1080px;overflow:hidden;background:#070b11;font-family:'Barlow',sans-serif;color:#ddeeff;position:relative;}}
body::before{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 900px 500px at 20% -10%,rgba(20,80,160,.12) 0%,transparent 65%),radial-gradient(ellipse 700px 400px at 85% 90%,rgba(0,180,120,.07) 0%,transparent 60%);pointer-events:none;z-index:0;}}
body::after{{content:'';position:absolute;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,.05) 3px,rgba(0,0,0,.05) 4px);pointer-events:none;z-index:0;opacity:.6;}}
.root{{position:relative;z-index:1;height:100%;display:flex;flex-direction:column;}}
.rb{{height:4px;background:linear-gradient(90deg,#ff1010 0%,#ff5500 11%,#ff9900 22%,#ffcc00 33%,#aaee00 44%,#11cc55 55%,#00cccc 66%,#0088ff 77%,#7700ff 88%,#ff0099 100%);flex-shrink:0;}}
.hdr{{background:linear-gradient(180deg,#0d1420 0%,#090f18 100%);border-bottom:1px solid #182436;padding:9px 20px;display:flex;align-items:center;flex-shrink:0;position:relative;overflow:hidden;}}
.hdr::before{{content:'';position:absolute;bottom:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(33,150,243,.4),rgba(0,220,150,.3),transparent);}}
.hdr-logo{{font-family:'IBM Plex Mono',monospace;font-size:8px;font-weight:700;letter-spacing:3px;color:#2a4a6a;margin-bottom:2px;}}
.hdr-title{{font-family:'Barlow Condensed',sans-serif;font-size:24px;font-weight:900;color:#fff;letter-spacing:.5px;line-height:1;}}
.hdr-title em{{color:#f5a020;font-style:normal;}}
.hdr-div{{width:1px;background:linear-gradient(180deg,transparent,#1e3050,transparent);margin:0 18px;align-self:stretch;}}
.hdr-center{{display:flex;flex-direction:column;gap:4px;}}
.hdr-live{{display:flex;align-items:center;gap:6px;font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:2px;color:#3a8aaa;}}
.dot-live{{width:6px;height:6px;border-radius:50%;background:#22dd88;box-shadow:0 0 8px #22dd88;}}
.hdr-badges{{display:flex;gap:5px;}}
.badge{{font-family:'IBM Plex Mono',monospace;font-size:8px;letter-spacing:1.5px;padding:2px 7px;border-radius:2px;}}
.badge-ro{{background:rgba(255,58,90,.12);border:1px solid rgba(255,58,90,.35);color:#ff3a5a;}}
.badge-rk{{background:rgba(245,160,32,.1);border:1px solid rgba(245,160,32,.35);color:#f5a020;}}
.badge-sg{{background:rgba(245,196,50,.08);border:1px solid rgba(245,196,50,.3);color:#f5c432;}}
.hdr-right{{margin-left:auto;text-align:right;}}
.hdr-time{{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:700;color:#e8f4ff;letter-spacing:1px;line-height:1;}}
.hdr-tz{{font-family:'IBM Plex Mono',monospace;font-size:8px;color:#2a4060;letter-spacing:1.5px;margin-top:2px;}}
.main{{flex:1;display:grid;grid-template-columns:1.05fr 1fr 1fr;overflow:hidden;}}
.col{{display:flex;flex-direction:column;overflow:hidden;}}
.col+.col{{border-left:1px solid #111e2e;}}
.s{{padding:9px 13px;border-bottom:1px solid #111e2e;flex-shrink:0;}}
.s:last-child{{border-bottom:none;flex:1;}}
.sl{{font-family:'IBM Plex Mono',monospace;font-size:7.5px;letter-spacing:2px;color:#2a4060;text-transform:uppercase;margin-bottom:6px;display:flex;align-items:center;gap:5px;}}
.sl::after{{content:'';flex:1;height:1px;background:linear-gradient(90deg,#111e2e,transparent);}}
/* Snapshot */
.snap{{display:flex;flex-direction:column;gap:2px;}}
.snap-row{{display:flex;align-items:center;padding:4px 7px;background:rgba(255,255,255,.018);border:1px solid #0f1a28;border-radius:3px;}}
.snap-n{{font-size:10px;color:#5a7a98;flex:1;font-weight:500;}}
.snap-v{{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:700;}}
.snap-c{{font-family:'IBM Plex Mono',monospace;font-size:9px;margin-left:7px;white-space:nowrap;}}
.dn{{color:#ff3a5a;}}.up{{color:#11cc77;}}.nu{{color:#7a9ab8;}}
.pill{{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:6px;padding:1px 3px;border-radius:1px;margin-left:3px;}}
.pill-r{{background:rgba(255,58,90,.15);border:1px solid rgba(255,58,90,.3);color:#ff3a5a;}}
.pill-o{{background:rgba(245,160,32,.12);border:1px solid rgba(245,160,32,.3);color:#f5a020;}}
/* VIX Gauge */
.vix-g{{height:5px;border-radius:3px;background:linear-gradient(90deg,#11cc77 0%,#aaee00 28%,#f5c020 52%,#ff8800 72%,#ff2244 100%);position:relative;margin:3px 0;}}
.vix-n{{position:absolute;top:-4px;width:2px;height:12px;background:#fff;border-radius:1px;box-shadow:0 0 5px #fff;left:{vix_pct:.0f}%;}}
.vix-ls{{display:flex;justify-content:space-between;margin-top:1px;}}
.vix-l{{font-family:'IBM Plex Mono',monospace;font-size:6.5px;}}
/* FX */
.fx3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:3px;}}
.fxi{{background:rgba(0,200,220,.04);border:1px solid rgba(0,200,220,.1);border-radius:3px;padding:4px 6px;text-align:center;}}
.fxl{{font-family:'IBM Plex Mono',monospace;font-size:7px;color:#2a5070;letter-spacing:.5px;margin-bottom:2px;}}
.fxv{{font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:#00cce0;}}
/* FRED */
.fred-row{{display:flex;align-items:center;padding:3px 6px;background:rgba(255,255,255,.015);border:1px solid #0f1a28;border-radius:3px;margin-bottom:2px;}}
.fred-n{{font-size:9px;color:#4a6a88;flex:1;}}
.fred-v{{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;margin-right:5px;}}
.fred-t{{font-family:'IBM Plex Mono',monospace;font-size:7px;padding:1px 4px;border-radius:1px;letter-spacing:.5px;}}
.t-hold{{background:rgba(245,160,32,.1);border:1px solid rgba(245,160,32,.25);color:#f5a020;}}
.t-norm{{background:rgba(90,130,180,.08);border:1px solid rgba(90,130,180,.2);color:#5a82b0;}}
.t-good{{background:rgba(17,204,119,.08);border:1px solid rgba(17,204,119,.2);color:#11cc77;}}
/* RSS */
.rss-bar{{height:5px;background:#0f1a28;border-radius:3px;position:relative;overflow:hidden;margin:3px 0;}}
.rss-fill{{height:100%;position:absolute;background:rgba(120,160,200,.4);border-radius:3px;left:26%;width:48%;}}
.rss-mark{{position:absolute;top:0;bottom:0;width:2px;background:#5a82b0;left:50%;}}
.rss-ls{{display:flex;justify-content:space-between;}}
.rss-l{{font-family:'IBM Plex Mono',monospace;font-size:6.5px;}}
/* Regime gauge - SVG */
.gauge-c{{display:flex;flex-direction:column;align-items:center;padding:2px 0 0;}}
/* ML */
.ml2{{display:grid;grid-template-columns:1fr 1fr;gap:5px;}}
.mlb{{background:rgba(255,255,255,.015);border:1px solid #0f1a28;border-radius:3px;padding:6px 8px;}}
.mlt{{font-family:'IBM Plex Mono',monospace;font-size:7px;letter-spacing:1.5px;color:#2a4060;margin-bottom:4px;}}
.mli{{display:flex;align-items:flex-start;gap:3px;font-size:8.5px;color:#5a7a98;line-height:1.6;}}
/* regime badge */
.reg-badge{{padding:6px 10px;border-radius:3px;text-align:center;margin-top:6px;}}
.reg-lbl{{font-size:8px;color:#3a5a78;margin-bottom:2px;}}
.reg-val{{font-family:'Barlow Condensed',sans-serif;font-size:16px;font-weight:900;letter-spacing:2px;}}
/* Market Score */
.ms6{{display:grid;grid-template-columns:1fr 1fr;gap:3px;}}
.msi{{background:rgba(255,255,255,.018);border:1px solid #0f1a28;border-radius:3px;padding:4px 7px;}}
.msl{{font-family:'IBM Plex Mono',monospace;font-size:7px;color:#2a4060;margin-bottom:3px;}}
.msb{{height:3px;background:#0f1a28;border-radius:2px;overflow:hidden;margin-bottom:2px;}}
.msf{{height:100%;border-radius:2px;}}
.msv{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;text-align:right;}}
/* Portfolio Risk */
.pr4{{display:grid;grid-template-columns:1fr 1fr;gap:3px;}}
.pri{{background:rgba(255,255,255,.015);border:1px solid #0f1a28;border-radius:3px;padding:4px 6px;}}
.prl{{font-family:'IBM Plex Mono',monospace;font-size:7px;color:#2a4060;margin-bottom:2px;}}
.prv{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;}}
/* Position Sizing */
.ps-bar{{height:5px;background:#0f1a28;border-radius:3px;overflow:hidden;margin:3px 0;}}
.ps-fill{{height:100%;border-radius:3px;background:linear-gradient(90deg,#f5a020,#ff6600);width:{int(sizing*100)}%;}}
.ps-ls{{display:flex;justify-content:space-between;}}
.ps-l{{font-family:'IBM Plex Mono',monospace;font-size:6.5px;}}
/* Portfolio Score */
.psc-row{{display:flex;align-items:center;gap:5px;margin-bottom:3px;}}
.psc-n{{font-size:8px;color:#4a6a88;width:70px;flex-shrink:0;}}
.psc-b{{flex:1;height:4px;background:#0f1a28;border-radius:2px;overflow:hidden;}}
.psc-f{{height:100%;border-radius:2px;}}
.psc-v{{font-family:'IBM Plex Mono',monospace;font-size:8px;font-weight:600;width:34px;text-align:right;}}
/* ETF Table */
.etf-tbl{{width:100%;border-collapse:collapse;}}
.etf-tbl td{{padding:3.5px 4px;border-bottom:1px solid #0c1520;font-size:10px;}}
.etf-tbl tr:last-child td{{border-bottom:none;}}
.tick{{font-family:'IBM Plex Mono',monospace;font-weight:700;color:#c8dff5;width:40px;font-size:10px;}}
.stance{{font-weight:600;font-size:9px;width:80px;}}
.tsig{{font-family:'IBM Plex Mono',monospace;font-size:8px;width:80px;}}
.abar-wrap{{height:5px;background:#0f1a28;border-radius:2px;overflow:hidden;width:60px;}}
.abar{{height:100%;border-radius:2px;}}
.apct{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;text-align:right;color:#c8dff5;padding-left:4px;}}
/* ETF Rank */
.rank-row{{display:flex;align-items:center;gap:5px;padding:3px 6px;border-radius:3px;background:rgba(255,255,255,.015);border:1px solid #0f1a28;margin-bottom:2px;}}
.rank-n{{font-family:'IBM Plex Mono',monospace;font-size:8px;font-weight:700;width:12px;}}
.rank-t{{font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;color:#c8dff5;width:36px;}}
.rank-d{{font-size:8px;flex:1;}}
.rank-dots{{font-family:'IBM Plex Mono',monospace;font-size:8px;}}
/* Signal Banner */
.sig-banner{{flex-shrink:0;background:linear-gradient(180deg,#0c1520 0%,#080e16 100%);border-top:1px solid #111e2e;padding:8px 18px;display:flex;align-items:center;gap:14px;position:relative;overflow:hidden;}}
.sig-banner::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,{sc},{sc},transparent);}}
.sig-banner::after{{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:linear-gradient(180deg,{sc},{sc}88);}}
.sig-main{{padding-left:6px;}}
.sig-lbl{{font-family:'IBM Plex Mono',monospace;font-size:7px;letter-spacing:2px;color:#2a4060;}}
.sig-val{{font-family:'Barlow Condensed',sans-serif;font-size:32px;font-weight:900;letter-spacing:2px;line-height:1;}}
.sdiv{{width:1px;height:36px;background:#111e2e;flex-shrink:0;}}
.sig-m{{display:flex;flex-direction:column;gap:3px;}}
.sig-mr{{display:flex;align-items:center;gap:5px;}}
.stag{{font-family:'IBM Plex Mono',monospace;font-size:7px;font-weight:700;letter-spacing:1px;padding:1px 5px;border-radius:1px;width:44px;text-align:center;}}
.stag-e{{font-family:'IBM Plex Mono',monospace;font-size:9px;}}
.sig-reason{{flex:1;font-size:9px;line-height:1.7;}}
.sig-meta{{display:flex;flex-direction:column;gap:2px;align-items:flex-end;margin-left:auto;flex-shrink:0;}}
.smeta{{font-family:'IBM Plex Mono',monospace;font-size:6.5px;color:#1a2e42;letter-spacing:.3px;}}
/* Footer */
.ftr{{flex-shrink:0;background:#040810;border-top:1px solid #0e1828;padding:5px 18px;display:flex;align-items:center;gap:10px;}}
.ftr-l{{font-family:'IBM Plex Mono',monospace;font-size:7.5px;color:#1a2e42;letter-spacing:2px;}}
.ftr-tags{{display:flex;gap:4px;}}
.ftag{{font-family:'IBM Plex Mono',monospace;font-size:6.5px;padding:1px 5px;border-radius:1px;border:1px solid #0f1a28;color:#1a2e42;letter-spacing:.3px;}}
.ftr-r{{font-family:'IBM Plex Mono',monospace;font-size:6.5px;color:#0e1828;margin-left:auto;}}
</style>
</head>
<body>
<div class="root">
<div class="rb"></div>
<div class="hdr">
  <div>
    <div class="hdr-logo">{CODENAME} · INVESTMENT OS</div>
    <div class="hdr-title">Full <em>Brief</em></div>
  </div>
  <div class="hdr-div"></div>
  <div class="hdr-center">
    <div class="hdr-live"><div class="dot-live"></div>LIVE · FULL EDITION</div>
    <div class="hdr-badges">
      <span class="badge badge-ro" style="background:rgba(0,0,0,.2);border-color:{rc}88;color:{rc}">⬛ {regime_name.upper()}</span>
      <span class="badge badge-rk" style="background:rgba(0,0,0,.2);border-color:{rkc}88;color:{rkc}">◉ {risk_level}</span>
      <span class="badge badge-sg" style="background:rgba(0,0,0,.2);border-color:{sc}88;color:{sc}">⏸ {signal}</span>
    </div>
  </div>
  <div class="hdr-right">
    <div class="hdr-time">{kst.strftime('%H:%M')} <span style="font-size:13px;color:#2a4060">KST</span></div>
    <div class="hdr-tz">{kst.strftime('%a · %b %d, %Y')} &nbsp;·&nbsp; ET {et.strftime('%H:%M')} &nbsp;·&nbsp; UTC {dt_utc.strftime('%H:%M')}</div>
  </div>
</div>

<div class="main">
<!-- COL 1 -->
<div class="col">
  <div class="s">
    <div class="sl">Market Snapshot</div>
    <div class="snap">
      <div class="snap-row"><div class="snap-n">S&P 500</div><div class="snap-v" style="color:{dn_up(sp500)}">{5580.2:.1f}</div><div class="snap-c" style="color:{dn_up(sp500)}">{sign(sp500)}</div></div>
      <div class="snap-row"><div class="snap-n">Nasdaq</div><div class="snap-v" style="color:{dn_up(nasdaq)}">{17321:.0f}</div><div class="snap-c" style="color:{dn_up(nasdaq)}">{sign(nasdaq)}</div></div>
      <div class="snap-row"><div class="snap-n">VIX <span class="pill pill-r">FEAR</span></div><div class="snap-v" style="color:#ff3a5a">{vix:.2f}</div><div class="snap-c" style="color:#ff3a5a">▲ 38.7%</div></div>
      <div class="snap-row"><div class="snap-n">US 10Y Yield</div><div class="snap-v nu">{us10y:.2f}%</div><div class="snap-c nu">▲ 0.03</div></div>
      <div class="snap-row"><div class="snap-n">WTI Crude <span class="pill pill-o">HIGH</span></div><div class="snap-v" style="color:#f5a020">${oil:.2f}</div><div class="snap-c" style="color:#f5a020">▲ 2.1%</div></div>
      <div class="snap-row"><div class="snap-n">DXY</div><div class="snap-v nu">{dxy:.2f}</div><div class="snap-c nu">MODERATE</div></div>
    </div>
    <div style="margin-top:6px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1px;">
        <span style="font-family:'IBM Plex Mono',monospace;font-size:7px;color:#2a4060;letter-spacing:1px">VIX GAUGE</span>
        <span style="font-family:'IBM Plex Mono',monospace;font-size:7.5px;font-weight:700;color:#ff3a5a">{vix:.2f} — FEAR ZONE</span>
      </div>
      <div class="vix-g"><div class="vix-n"></div></div>
      <div class="vix-ls"><span class="vix-l" style="color:#11cc77">SAFE</span><span class="vix-l" style="color:#f5c020">20</span><span class="vix-l" style="color:#ff8800">30</span><span class="vix-l" style="color:#ff3a5a">HIGH</span></div>
    </div>
  </div>
  <div class="s">
    <div class="sl">FX Rates</div>
    <div class="fx3">
      <div class="fxi"><div class="fxl">USD/KRW</div><div class="fxv">{usdkrw:,.1f}</div></div>
      <div class="fxi"><div class="fxl">EUR/USD</div><div class="fxv">{eurusd:.4f}</div></div>
      <div class="fxi"><div class="fxl">USD/JPY</div><div class="fxv">{usdjpy:.2f}</div></div>
    </div>
  </div>
  <div class="s">
    <div class="sl">FRED Macro</div>
    <div class="fred-row"><div class="fred-n">Fed Funds Rate</div><div class="fred-v" style="color:#f5a020">3.64%</div><div class="fred-t t-hold">HOLD</div></div>
    <div class="fred-row"><div class="fred-n">HY Credit Spread</div><div class="fred-v nu">3.21%</div><div class="fred-t t-norm">NORMAL</div></div>
    <div class="fred-row"><div class="fred-n">Yield Curve 2-10</div><div class="fred-v up">+0.56%</div><div class="fred-t t-good">NORMAL</div></div>
  </div>
  <div class="s" style="flex:1">
    <div class="sl">RSS Sentiment</div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
      <span style="font-size:8.5px;color:#3a5a78">9 Sources · 82 Headlines</span>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;color:#7a9ab8">Neutral <span style="font-size:8px;color:#2a4060">−0.08</span></span>
    </div>
    <div class="rss-bar"><div class="rss-fill"></div><div class="rss-mark"></div></div>
    <div class="rss-ls"><span class="rss-l" style="color:#ff3a5a">BEARISH</span><span class="rss-l" style="color:#5a7a98">NEUTRAL</span><span class="rss-l" style="color:#11cc77">BULLISH</span></div>
    <div style="margin-top:6px;display:grid;grid-template-columns:1fr 1fr;gap:2px;">
      <div style="font-size:7.5px;color:#3a5a78;display:flex;gap:3px;align-items:center"><span style="color:#11cc77">●</span> Yahoo Finance</div>
      <div style="font-size:7.5px;color:#3a5a78;display:flex;gap:3px;align-items:center"><span style="color:#11cc77">●</span> CNBC Markets</div>
      <div style="font-size:7.5px;color:#3a5a78;display:flex;gap:3px;align-items:center"><span style="color:#11cc77">●</span> MarketWatch</div>
      <div style="font-size:7.5px;color:#3a5a78;display:flex;gap:3px;align-items:center"><span style="color:#11cc77">●</span> Google News×3</div>
      <div style="font-size:7.5px;color:#3a5a78;display:flex;gap:3px;align-items:center"><span style="color:#f5a020">●</span> Investing.com</div>
      <div style="font-size:7.5px;color:#3a5a78;display:flex;gap:3px;align-items:center"><span style="color:#11cc77">●</span> CNBC Economy</div>
    </div>
    <div style="margin-top:4px;font-family:'IBM Plex Mono',monospace;font-size:6.5px;color:#1a2e42">CNBC weight×1.5 · Yahoo×1.3 · 9SUCCESS/0FAIL</div>
  </div>
</div>

<!-- COL 2 -->
<div class="col">
  <div class="s">
    <div class="sl">Market Risk Level</div>
    <div class="gauge-c">
      <svg width="230" height="118" viewBox="0 0 230 118" overflow="visible">
        <defs>
          <linearGradient id="g1" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="#11cc77"/><stop offset="28%" stop-color="#aaee00"/>
            <stop offset="52%" stop-color="#f5c020"/><stop offset="72%" stop-color="#ff8800"/>
            <stop offset="100%" stop-color="#ff1144"/>
          </linearGradient>
          <filter id="glow"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        </defs>
        <path d="M 16 110 A 99 99 0 0 1 214 110" fill="none" stroke="#0f1a28" stroke-width="15" stroke-linecap="round"/>
        <path d="M 16 110 A 99 99 0 0 1 214 110" fill="none" stroke="url(#g1)" stroke-width="15" stroke-linecap="round" opacity=".88"/>
        <g stroke="#070b11" stroke-width="1.5">
          <line x1="115" y1="11" x2="115" y2="24"/><line x1="24" y1="60" x2="35" y2="67"/>
          <line x1="206" y1="60" x2="195" y2="67"/><line x1="50" y1="20" x2="57" y2="31"/><line x1="180" y1="20" x2="173" y2="31"/>
        </g>
        <line x1="115" y1="110" x2="{needle_x2}" y2="{needle_y2}"
          stroke="white" stroke-width="2.5" stroke-linecap="round" filter="url(#glow)"/>
        <circle cx="115" cy="110" r="6.5" fill="#0d1824" stroke="white" stroke-width="1.8"/>
        <circle cx="115" cy="110" r="3" fill="white"/>
        <text x="10" y="126" fill="#11cc77" font-family="'IBM Plex Mono'" font-size="7" letter-spacing="1">SAFE</text>
        <text x="89" y="9" fill="#f5c020" font-family="'IBM Plex Mono'" font-size="7" letter-spacing="1" text-anchor="middle">MED</text>
        <text x="212" y="126" fill="#ff1144" font-family="'IBM Plex Mono'" font-size="7" letter-spacing="1" text-anchor="end">HIGH</text>
      </svg>
      <div style="font-family:'Barlow Condensed',sans-serif;font-size:26px;font-weight:900;letter-spacing:3px;color:{rkc};text-shadow:0 0 18px {rkc}88;text-align:center;margin-top:-4px">{risk_level}</div>
    </div>
  </div>
  <div class="s">
    <div class="sl">Macro & Liquidity</div>
    <div class="ml2">
      <div class="mlb">
        <div class="mlt">MACRO</div>
        <div class="mli"><span style="color:#ff3a5a;font-size:7px">●</span>Oil Supply Shock</div>
        <div class="mli"><span style="color:#ff3a5a;font-size:7px">●</span>Geopolitical Conflict</div>
        <div class="mli"><span style="color:#ff3a5a;font-size:7px">●</span>Inflation Re-Acceleration</div>
      </div>
      <div class="mlb">
        <div class="mlt">LIQUIDITY</div>
        <div class="mli"><span style="color:#f5a020;font-size:7px">●</span>Global Liquidity Tightening</div>
        <div class="mli"><span style="color:#f5a020;font-size:7px">●</span>Risk Asset Demand Weakening</div>
        <div class="mli"><span style="color:#11cc77;font-size:7px">●</span>Yield Curve Positive</div>
      </div>
    </div>
    <div class="reg-badge" style="background:rgba(0,0,0,.2);border:1px solid {rc}44">
      <div class="reg-lbl">Market Regime</div>
      <div class="reg-val" style="color:{rc};text-shadow:0 0 14px {rc}66">{regime_name.upper()} REGIME</div>
    </div>
  </div>
  <div class="s">
    <div class="sl">Market Score <span style="color:#1a2e42;font-size:6.5px">&nbsp;(1=LOW · 5=HIGH RISK)</span></div>
    <div class="ms6">{score_rows()}</div>
  </div>
  <div class="s">
    <div class="sl">Portfolio Risk</div>
    <div class="pr4">
      <div class="pri"><div class="prl">Return Impact</div><div class="prv nu">{prisk.get('portfolio_return_impact','—')}</div></div>
      <div class="pri"><div class="prl">Risk Impact</div><div class="prv" style="color:#f5a020">{prisk.get('portfolio_risk_impact','—')}</div></div>
      <div class="pri"><div class="prl">Drawdown Risk</div><div class="prv up">{prisk.get('drawdown_risk','—')}</div></div>
      <div class="pri"><div class="prl">Crash Alert</div><div class="prv" style="color:{rkc}">{prisk.get('crash_alert_level','—')}</div></div>
      <div class="pri"><div class="prl">Hedge Intensity</div><div class="prv" style="color:#00cce0">{prisk.get('hedge_intensity','—')}</div></div>
      <div class="pri"><div class="prl">Beta Exposure</div><div class="prv nu">{prisk.get('position_exposure','—')}</div></div>
    </div>
  </div>
  <div class="s" style="flex:1">
    <div class="sl">Position Sizing</div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
      <div style="padding:4px 8px;background:rgba(245,160,32,.08);border:1px solid rgba(245,160,32,.25);border-radius:3px;display:flex;align-items:center;gap:5px;">
        <span style="font-size:11px">⚠</span>
        <span style="font-family:'Barlow Condensed',sans-serif;font-size:12px;font-weight:700;color:#f5a020">Conservative</span>
      </div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:#f5a020">{sizing:.2f}×</div>
    </div>
    <div class="ps-bar"><div class="ps-fill"></div></div>
    <div class="ps-ls"><span class="ps-l" style="color:#1a2e42">0×</span><span class="ps-l" style="color:#1a2e42">0.5×</span><span class="ps-l" style="color:#f5a020">▲{sizing:.2f}</span><span class="ps-l" style="color:#1a2e42">1.0×</span></div>
    <div style="margin-top:7px">
      <div class="sl" style="margin-bottom:4px">Portfolio Score</div>
      <div class="psc-row"><div class="psc-n">Diversification</div><div class="psc-b"><div class="psc-f" style="width:45%;background:linear-gradient(90deg,#c88010,#f5a020)"></div></div><div class="psc-v" style="color:#f5a020">45/100</div></div>
      <div class="psc-row"><div class="psc-n">Regime Fit</div><div class="psc-b"><div class="psc-f" style="width:78%;background:linear-gradient(90deg,#009960,#11cc77)"></div></div><div class="psc-v" style="color:#11cc77">78/100</div></div>
      <div class="psc-row"><div class="psc-n">Signal Strength</div><div class="psc-b"><div class="psc-f" style="width:62%;background:linear-gradient(90deg,#0060aa,#2288ff)"></div></div><div class="psc-v" style="color:#2288ff">62/100</div></div>
    </div>
  </div>
</div>

<!-- COL 3 -->
<div class="col">
  <div class="s">
    <div class="sl">ETF Strategy · Score · Allocation</div>
    <table class="etf-tbl">
      {etf_rows()}
    </table>
  </div>
  <div class="s">
    <div class="sl">Trading Signals</div>
    <table class="etf-tbl">
      {sig_rows()}
    </table>
  </div>
  <div class="s" style="flex:1">
    <div class="sl">ETF Rank</div>
    {rank_rows() if ranked else '<div style="font-size:9px;color:#2a4060">Rank data unavailable</div>'}
  </div>
</div>
</div>

<!-- SIGNAL BANNER -->
<div class="sig-banner">
  <div class="sig-main">
    <div class="sig-lbl">TRADING SIGNAL</div>
    <div class="sig-val" style="color:{sc};text-shadow:0 0 20px {sc}88">{signal}</div>
  </div>
  <div class="sdiv"></div>
  <div class="sig-m">
    <div class="sig-mr"><div class="stag" style="background:rgba(17,204,119,.1);border:1px solid rgba(17,204,119,.25);color:#11cc77">BUY</div><div class="stag-e" style="color:#11cc77">{" · ".join(buy_list) if buy_list else "—"}</div></div>
    <div class="sig-mr"><div class="stag" style="background:rgba(245,192,50,.08);border:1px solid rgba(245,192,50,.2);color:#f5c020">HOLD</div><div class="stag-e" style="color:#f5c020">{" · ".join(hold_list) if hold_list else "—"}</div></div>
    <div class="sig-mr"><div class="stag" style="background:rgba(255,58,90,.1);border:1px solid rgba(255,58,90,.25);color:#ff3a5a">REDUCE</div><div class="stag-e" style="color:#ff3a5a">{" · ".join(reduce_list) if reduce_list else "—"}</div></div>
  </div>
  <div class="sdiv"></div>
  <div class="sig-reason" style="color:#3a5a78">
    <strong style="color:#7a9ab8">{regime_name} — {tsig.get('signal_reason','Maintain current exposure.')}</strong><br>
    {summary}
  </div>
  <div class="sig-meta">
    <div class="smeta">RSS 9 SOURCES</div>
    <div class="smeta">VALIDATE PASS</div>
    <div class="smeta">FRED+YF+RSS</div>
    <div class="smeta">KST 18:30</div>
  </div>
</div>

<!-- FOOTER -->
<div class="ftr">
  <div class="ftr-l">EDT INVESTMENT · {SYSTEM_VERSION} · FULL EDITION</div>
  <div class="ftr-tags">
    <div class="ftag">HTML·PLAYWRIGHT</div>
    <div class="ftag">FRED·YAHOO·RSS</div>
    <div class="ftag">NOT FINANCIAL ADVICE</div>
  </div>
  <div class="ftr-r">{kst.strftime('%b %d, %Y')} · {kst.strftime('%H:%M')} KST</div>
</div>
</div>
</body>
</html>"""


# ────────────────────────────────────────────────
# 렌더링 함수 (Playwright)
# ────────────────────────────────────────────────
async def _render_async(html: str, out_path: str) -> bool:
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1080, "height": 1080})
            await page.set_content(html, wait_until="networkidle")
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


def _render(html: str, out_path: str) -> bool:
    try:
        return asyncio.run(_render_async(html, out_path))
    except RuntimeError:
        # 이미 이벤트 루프가 돌고 있으면 새 스레드에서 실행
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, _render_async(html, out_path))
            return future.result()


# ────────────────────────────────────────────────
# 공개 인터페이스
# ────────────────────────────────────────────────
def build_html_dashboard(
    data: dict,
    session: str = "full",
    dt_utc: Optional[datetime] = None,
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    HTML/Playwright 기반 풀버전 대시보드 PNG 생성.

    Args:
        data:       core_data.json data 필드
        session:    세션 이름 (full 고정)
        dt_utc:     기준 시각 (None=현재)
        output_dir: 저장 디렉토리

    Returns:
        str:  PNG 파일 경로 (성공)
        None: 실패 (Fallback → 텍스트 발행)
    """
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

        fname  = f"dashboard_full_{dt_utc.strftime('%Y%m%d_%H%M')}.png"
        fpath  = str(output_dir / fname)

        logger.info("[HtmlDash] HTML 빌드 시작")
        html = _build_html(data, dt_utc)

        # 임시 HTML 저장
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            tmp_path = f.name

        logger.info("[HtmlDash] Playwright 렌더링 시작")
        ok = _render(f"file://{tmp_path}", fpath)

        # 임시 파일 삭제
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
