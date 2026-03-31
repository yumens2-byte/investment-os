"""
comic/html_image_engine.py
Investment Comic v2.0 — VS 배틀 카드 엔진

DAILY  (4컷): 좌2컷 VS 우2컷 + 시장데이터 대형카드
WEEKLY (8컷): 좌4컷 VS 우4컷 + 에피소드 아크 배너

디자인 원칙:
  - 색상 3종만: #10b981(BUY) / #f59e0b(HOLD) / #ef4444(SELL)
  - 모든 텍스트 최소 #aaaaaa 이상 (모바일 OLED 보장)
  - BUY/HOLD/SELL 3파전 → 댓글·하트·리트윗 유도
  - 해시태그 10개 고정
"""

import logging
import os
import tempfile
from datetime import date

logger = logging.getLogger(__name__)

FIXED_HASHTAGS = (
    "#InvestmentComic #투자코믹 #미국증시 #미국주식 #ETF투자 "
    "#지금팔아야할까 #폭락오나 #주식전쟁 #버틸까팔까 #개미투자자"
)


def _cut(cuts, n):
    c = next((x for x in cuts if x.get("cut_number") == n), {})
    dlg   = c.get("dialogue", "").replace('"', "&quot;").replace("<", "&lt;")
    scene = c.get("scene",    "").replace('"', "&quot;").replace("<", "&lt;")
    return dlg, scene


def _cut_block_left(n, dlg, scene, color, font_dlg, font_scene, gap):
    opacity = "1" if dlg else "0.3"
    return f"""<div style="border-left:5px solid {color};padding-left:16px;opacity:{opacity};">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:{color};letter-spacing:2px;margin-bottom:6px;">CUT {n:02d}</div>
  <div style="font-size:{font_dlg}px;font-weight:900;color:#ffffff;line-height:1.5;">{dlg or "—"}</div>
  <div style="font-size:{font_scene}px;color:#aaaaaa;margin-top:{gap}px;line-height:1.55;">{scene}</div>
</div>"""


def _cut_block_right(n, dlg, scene, color, font_dlg, font_scene, gap):
    opacity = "1" if dlg else "0.3"
    return f"""<div style="border-right:5px solid {color};padding-right:16px;text-align:right;opacity:{opacity};">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:{color};letter-spacing:2px;margin-bottom:6px;">CUT {n:02d}</div>
  <div style="font-size:{font_dlg}px;font-weight:900;color:#ffffff;line-height:1.5;">{dlg or "—"}</div>
  <div style="font-size:{font_scene}px;color:#aaaaaa;margin-top:{gap}px;line-height:1.55;">{scene}</div>
</div>"""


def _build_daily(story, risk_level, market_data, episode_no):
    """Daily 4컷 — 시장 데이터 대형카드 + BUY VS SELL 2컷씩"""
    cuts      = story.get("cuts", [])
    title     = story.get("title", "")[:38]
    vix       = market_data.get("vix", "N/A")
    sp500     = market_data.get("sp500", "N/A")
    sp_color  = "#ef4444" if isinstance(sp500, (int, float)) and sp500 < 0 else "#10b981"
    sp_sign   = "+" if isinstance(sp500, (int, float)) and sp500 >= 0 else ""
    today     = date.today().strftime("%Y.%m.%d")
    signal_map = {"LOW": ("BUY","#10b981"), "MEDIUM": ("HOLD","#f59e0b"), "HIGH": ("SELL","#ef4444")}
    sig_txt, sig_col = signal_map.get(risk_level, ("HOLD","#f59e0b"))

    d1,s1 = _cut(cuts,1); d3,s3 = _cut(cuts,3)
    d2,s2 = _cut(cuts,2); d4,s4 = _cut(cuts,4)

    left_cuts  = "".join([
        _cut_block_left(1, d1, s1, "#10b981", 24, 13, 8),
        '<div style="margin:14px 0;height:1px;background:#1e1e1e;"></div>',
        _cut_block_left(3, d3, s3, "rgba(16,185,129,0.5)", 24, 13, 8),
    ])
    right_cuts = "".join([
        _cut_block_right(2, d2, s2, "#ef4444", 24, 13, 8),
        '<div style="margin:14px 0;height:1px;background:#1e1e1e;"></div>',
        _cut_block_right(4, d4, s4, "rgba(239,68,68,0.5)", 24, 13, 8),
    ])

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@700&family=Noto+Sans+KR:wght@400;700;900&display=swap" rel="stylesheet">
<style>*{{margin:0;padding:0;box-sizing:border-box;}}html,body{{width:1080px;height:1080px;overflow:hidden;}}body{{background:#0a0a0a;font-family:'Noto Sans KR',sans-serif;}}</style>
</head><body>
<div style="width:1080px;height:1080px;display:flex;flex-direction:column;background:#0a0a0a;">

  <div style="display:flex;height:12px;flex-shrink:0;">
    <div style="flex:1;background:#10b981;"></div><div style="flex:1;background:#f59e0b;"></div><div style="flex:1;background:#ef4444;"></div>
  </div>

  <!-- 헤더 -->
  <div style="padding:24px 44px 18px;border-bottom:2px solid #222;flex-shrink:0;">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#f59e0b;letter-spacing:4px;margin-bottom:10px;">INVESTMENT COMIC · Ep.{episode_no} · {today} · DAILY</div>
    <div style="font-size:33px;font-weight:900;color:#ffffff;letter-spacing:-1px;line-height:1.15;margin-bottom:16px;">{title}</div>
    <div style="display:flex;gap:10px;">
      <div style="flex:1;padding:13px 16px;background:#111;border-radius:10px;display:flex;flex-direction:column;gap:4px;">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#aaaaaa;letter-spacing:2px;">VIX</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:36px;font-weight:700;color:#f59e0b;line-height:1;">{vix}</div>
        <div style="font-size:13px;color:#aaaaaa;">공포 구간</div>
      </div>
      <div style="flex:1;padding:13px 16px;background:#111;border-radius:10px;display:flex;flex-direction:column;gap:4px;">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#aaaaaa;letter-spacing:2px;">S&P500</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:36px;font-weight:700;color:{sp_color};line-height:1;">{sp_sign}{sp500}%</div>
        <div style="font-size:13px;color:#aaaaaa;">전일 대비</div>
      </div>
      <div style="flex:1;padding:13px 16px;background:#111;border-radius:10px;display:flex;flex-direction:column;gap:4px;">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#aaaaaa;letter-spacing:2px;">SIGNAL</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:36px;font-weight:700;color:{sig_col};line-height:1;">{sig_txt}</div>
        <div style="font-size:13px;color:#aaaaaa;">{risk_level} RISK</div>
      </div>
    </div>
  </div>

  <!-- 배틀 -->
  <div style="flex:1;display:grid;grid-template-columns:1fr 64px 1fr;min-height:0;padding:20px 44px;">

    <!-- 좌: BUY -->
    <div style="display:flex;flex-direction:column;gap:0;padding-right:22px;">
      <div style="background:#10b981;border-radius:8px;padding:9px 16px;display:inline-flex;align-items:center;gap:8px;align-self:flex-start;margin-bottom:16px;">
        <span style="font-size:14px;font-weight:900;color:#ffffff;letter-spacing:1px;">🐂 MAX BULLHORN</span>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;justify-content:space-between;">
        {left_cuts}
      </div>
      <div style="background:#10b981;border-radius:12px;padding:14px;text-align:center;margin-top:16px;">
        <div style="font-size:21px;font-weight:900;color:#ffffff;">🐂 BUY</div>
        <div style="font-size:13px;color:rgba(255,255,255,0.9);margin-top:4px;font-weight:700;">좋아요(하트) 누르기</div>
      </div>
    </div>

    <!-- VS -->
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;">
      <div style="flex:1;width:2px;background:#222;"></div>
      <div style="width:56px;height:56px;border-radius:50%;background:#111;border:2px solid #333;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
        <span style="font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:700;color:#ffffff;">VS</span>
      </div>
      <div style="flex:1;width:2px;background:#222;"></div>
    </div>

    <!-- 우: SELL -->
    <div style="display:flex;flex-direction:column;gap:0;padding-left:22px;align-items:flex-end;">
      <div style="background:#ef4444;border-radius:8px;padding:9px 16px;display:inline-flex;align-items:center;gap:8px;align-self:flex-end;margin-bottom:16px;">
        <span style="font-size:14px;font-weight:900;color:#ffffff;letter-spacing:1px;">BARON BEARSWORTH 🐻</span>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;justify-content:space-between;width:100%;">
        {right_cuts}
      </div>
      <div style="background:#ef4444;border-radius:12px;padding:14px;text-align:center;margin-top:16px;width:100%;">
        <div style="font-size:21px;font-weight:900;color:#ffffff;">🐻 SELL</div>
        <div style="font-size:13px;color:rgba(255,255,255,0.9);margin-top:4px;font-weight:700;">리트윗으로 경고 전파</div>
      </div>
    </div>
  </div>

  <!-- HOLD 배너 -->
  <div style="padding:0 44px 14px;flex-shrink:0;">
    <div style="background:#f59e0b;border-radius:12px;padding:14px 22px;display:flex;align-items:center;justify-content:space-between;">
      <div>
        <div style="font-size:19px;font-weight:900;color:#000000;">⚖️ HOLD — 포지션 유지</div>
        <div style="font-size:13px;color:rgba(0,0,0,0.75);margin-top:2px;font-weight:700;">{risk_level} RISK 레짐 · 방어 포지션 권장</div>
      </div>
      <div style="font-size:15px;font-weight:900;color:#000000;">💬 댓글로 이유 써줘</div>
    </div>
  </div>

  <!-- 하단 -->
  <div style="padding:10px 44px 12px;border-top:2px solid #222;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;color:#f59e0b;">@InvestmentComic</div>
    <div style="font-size:13px;font-weight:700;color:#ffffff;">당신의 선택은?</div>
    <div style="font-size:12px;color:#aaaaaa;font-weight:700;">#주식전쟁 #버틸까팔까</div>
  </div>

  <div style="display:flex;height:8px;flex-shrink:0;">
    <div style="flex:1;background:#10b981;"></div><div style="flex:1;background:#f59e0b;"></div><div style="flex:1;background:#ef4444;"></div>
  </div>
</div></body></html>"""


def _build_weekly(story, risk_level, market_data, episode_no):
    """Weekly 8컷 — 헤더 1줄 압축 + 2열 4행 풀팩킹"""
    cuts      = story.get("cuts", [])
    title     = story.get("title", "")[:42]
    vix       = market_data.get("vix", "N/A")
    sp500     = market_data.get("sp500", "N/A")
    sp_color  = "#ef4444" if isinstance(sp500,(int,float)) and sp500 < 0 else "#10b981"
    sp_sign   = "+" if isinstance(sp500,(int,float)) and sp500 >= 0 else ""
    today     = date.today().strftime("%Y.%m.%d")
    signal_map = {"LOW":("BUY","#10b981"),"MEDIUM":("HOLD","#f59e0b"),"HIGH":("SELL","#ef4444")}
    sig_txt, sig_col = signal_map.get(risk_level, ("HOLD","#f59e0b"))

    cuts_data = {n: _cut(cuts, n) for n in range(1, 9)}

    # 좌(BUY): 홀수컷 1,3,5,7 / 우(SELL): 짝수컷 2,4,6,8
    def left_block(n):
        dlg, scene = cuts_data[n]
        border_opacity = "1" if n in (1,3) else "0.45"
        color = f"rgba(16,185,129,{border_opacity})"
        return f"""<div style="border-left:4px solid {color};padding-left:14px;flex:1;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#10b981;letter-spacing:2px;margin-bottom:5px;">CUT {n:02d}</div>
  <div style="font-size:22px;font-weight:900;color:#ffffff;line-height:1.45;">{dlg}</div>
  <div style="font-size:13px;color:#aaaaaa;margin-top:5px;line-height:1.5;">{scene}</div>
</div>"""

    def right_block(n):
        dlg, scene = cuts_data[n]
        border_opacity = "1" if n in (2,4) else "0.45"
        color = f"rgba(239,68,68,{border_opacity})"
        return f"""<div style="border-right:4px solid {color};padding-right:14px;flex:1;text-align:right;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#ef4444;letter-spacing:2px;margin-bottom:5px;">CUT {n:02d}</div>
  <div style="font-size:22px;font-weight:900;color:#ffffff;line-height:1.45;">{dlg}</div>
  <div style="font-size:13px;color:#aaaaaa;margin-top:5px;line-height:1.5;">{scene}</div>
</div>"""

    # 8개 블록 생성
    lb = {n: left_block(n)  for n in (1,3,5,7)}
    rb = {n: right_block(n) for n in (2,4,6,8)}

    rows = ""
    pairs = [(1,2),(3,4),(5,6),(7,8)]
    for ln, rn in pairs:
        rows += f"""<div style="display:grid;grid-template-columns:1fr 48px 1fr;flex:1;min-height:0;border-top:1px solid #1a1a1a;padding:10px 0 0;">
  <div style="padding-right:16px;">{lb[ln]}</div>
  <div style="display:flex;align-items:center;justify-content:center;">
    <div style="width:38px;height:38px;border-radius:50%;background:#111;border:1px solid #2a2a2a;display:flex;align-items:center;justify-content:center;">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;color:#ffffff;">VS</span>
    </div>
  </div>
  <div style="padding-left:16px;">{rb[rn]}</div>
</div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@700&family=Noto+Sans+KR:wght@400;700;900&display=swap" rel="stylesheet">
<style>*{{margin:0;padding:0;box-sizing:border-box;}}html,body{{width:1080px;height:1080px;overflow:hidden;}}body{{background:#0a0a0a;font-family:'Noto Sans KR',sans-serif;}}</style>
</head><body>
<div style="width:1080px;height:1080px;display:flex;flex-direction:column;background:#0a0a0a;">

  <div style="display:flex;height:12px;flex-shrink:0;">
    <div style="flex:1;background:#10b981;"></div><div style="flex:1;background:#f59e0b;"></div><div style="flex:1;background:#ef4444;"></div>
  </div>

  <!-- 헤더: 1줄 압축 -->
  <div style="padding:16px 44px 14px;border-bottom:2px solid #222;flex-shrink:0;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#f59e0b;letter-spacing:3px;margin-bottom:6px;">INVESTMENT COMIC · Ep.{episode_no} · {today} · WEEKLY·8CUT</div>
        <div style="font-size:26px;font-weight:900;color:#ffffff;letter-spacing:-0.5px;line-height:1.15;">{title}</div>
      </div>
      <!-- 시장 데이터 인라인 -->
      <div style="display:flex;gap:8px;flex-shrink:0;margin-left:20px;">
        <div style="padding:10px 14px;background:#111;border-radius:8px;text-align:center;min-width:84px;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#aaaaaa;letter-spacing:1px;margin-bottom:3px;">VIX</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:700;color:#f59e0b;line-height:1;">{vix}</div>
        </div>
        <div style="padding:10px 14px;background:#111;border-radius:8px;text-align:center;min-width:84px;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#aaaaaa;letter-spacing:1px;margin-bottom:3px;">S&P</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:700;color:{sp_color};line-height:1;">{sp_sign}{sp500}%</div>
        </div>
        <div style="padding:10px 14px;background:#111;border-radius:8px;text-align:center;min-width:84px;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#aaaaaa;letter-spacing:1px;margin-bottom:3px;">SIGNAL</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:700;color:{sig_col};line-height:1;">{sig_txt}</div>
        </div>
      </div>
    </div>
    <!-- 캐릭터 태그 -->
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="background:#10b981;border-radius:6px;padding:7px 14px;">
        <span style="font-size:13px;font-weight:900;color:#ffffff;letter-spacing:1px;">🐂 MAX BULLHORN</span>
      </div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#aaaaaa;">8CUT WEEKLY EPISODE</div>
      <div style="background:#ef4444;border-radius:6px;padding:7px 14px;">
        <span style="font-size:13px;font-weight:900;color:#ffffff;letter-spacing:1px;">BARON BEARSWORTH 🐻</span>
      </div>
    </div>
  </div>

  <!-- 8컷 풀팩킹 -->
  <div style="flex:1;display:flex;flex-direction:column;padding:0 44px;min-height:0;overflow:hidden;">
    {rows}
  </div>

  <!-- BUY/HOLD/SELL -->
  <div style="padding:10px 44px 10px;flex-shrink:0;">
    <div style="display:flex;gap:10px;">
      <div style="background:#10b981;border-radius:10px;padding:12px 16px;text-align:center;flex:1;">
        <div style="font-size:18px;font-weight:900;color:#ffffff;">🐂 BUY → 하트</div>
      </div>
      <div style="background:#f59e0b;border-radius:10px;padding:12px 16px;text-align:center;flex:1.4;">
        <div style="font-size:18px;font-weight:900;color:#000000;">⚖️ HOLD → 댓글로 이유!</div>
      </div>
      <div style="background:#ef4444;border-radius:10px;padding:12px 16px;text-align:center;flex:1;">
        <div style="font-size:18px;font-weight:900;color:#ffffff;">🐻 SELL → 리트윗</div>
      </div>
    </div>
  </div>

  <!-- 하단 -->
  <div style="padding:8px 44px 10px;border-top:2px solid #222;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:700;color:#f59e0b;">@InvestmentComic</div>
    <div style="font-size:13px;font-weight:700;color:#ffffff;">당신의 선택은?</div>
    <div style="font-size:12px;color:#aaaaaa;font-weight:700;">#주식전쟁 #버틸까팔까</div>
  </div>

  <div style="display:flex;height:8px;flex-shrink:0;">
    <div style="flex:1;background:#10b981;"></div><div style="flex:1;background:#f59e0b;"></div><div style="flex:1;background:#ef4444;"></div>
  </div>
</div></body></html>"""


def _build_html(story, risk_level, market_data, comic_type, episode_no):
    if comic_type == "weekly":
        return _build_weekly(story, risk_level, market_data, episode_no)
    return _build_daily(story, risk_level, market_data, episode_no)


def generate_html_comic(story: dict, risk_level: str, market_data: dict,
                        comic_type: str, episode_no: int) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright 미설치. pip install playwright && playwright install chromium")

    html = _build_html(story, risk_level, market_data, comic_type, episode_no)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp = f.name

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
            page    = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.goto(f"file://{tmp}")
            page.wait_for_timeout(2500)
            img = page.screenshot(type="png", full_page=False)
            browser.close()
        logger.info(f"[HtmlEngine] 이미지 생성 완료 — {len(img):,} bytes")
        return img
    finally:
        try: os.unlink(tmp)
        except: pass
