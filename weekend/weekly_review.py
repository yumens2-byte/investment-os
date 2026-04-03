"""
weekend/weekly_review.py (B-20A)
===================================
토요일 주간 리뷰 — "이번 주 전투 결과"

데이터:
  - weekly_tracker.json (주간 레짐/시그널/ETF 누적)
  - Gemini 텍스트 (주간 해설 3~5줄)

출력:
  - 주간 리뷰 카드 (HTML → PNG 1080×1080)
  - X 트윗 텍스트
  - TG 메시지
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_weekly_review() -> dict:
    """
    주간 리뷰 생성 — weekly_tracker + Gemini

    Returns:
        {
          "success": True/False,
          "tweet_text": str,
          "image_path": str | None,
          "tg_text": str,
          "summary": dict,
        }
    """
    try:
        # ── 1. 주간 데이터 조회 ──
        from core.weekly_tracker import get_weekly_summary
        summary = get_weekly_summary()

        if summary.get("days", 0) == 0:
            logger.warning("[WeeklyReview] 주간 데이터 없음 — 스킵")
            return {"success": False, "error": "주간 데이터 없음"}

        # ── 2. Max vs Baron 전적 산출 ──
        max_wins, baron_wins, draws = _calc_battle_score(summary)

        # ── 3. Gemini 주간 해설 ──
        commentary = _generate_commentary(summary, max_wins, baron_wins)

        # ── 4. 트윗 텍스트 ──
        tweet = _build_tweet(summary, max_wins, baron_wins, draws)

        # ── 5. 카드 이미지 생성 ──
        image_path = _render_review_card(summary, max_wins, baron_wins, draws, commentary)

        # ── 6. TG 메시지 ──
        tg_text = _build_tg_message(summary, max_wins, baron_wins, draws, commentary)

        logger.info(
            f"[WeeklyReview] 생성 완료 | {summary['week']} | "
            f"Max {max_wins} vs Baron {baron_wins}"
        )

        return {
            "success": True,
            "tweet_text": tweet,
            "image_path": image_path,
            "tg_text": tg_text,
            "summary": summary,
        }

    except Exception as e:
        logger.error(f"[WeeklyReview] 생성 실패: {e}")
        return {"success": False, "error": str(e)}


def _calc_battle_score(summary: dict) -> tuple:
    """
    Max(강세) vs Baron(약세) 전적 산출
    BUY → Max 승, REDUCE → Baron 승, HOLD → 무승부
    """
    sig = summary.get("signal_counts", {})
    max_wins = sig.get("BUY", 0)
    baron_wins = sig.get("REDUCE", 0)
    draws = sig.get("HOLD", 0)
    return max_wins, baron_wins, draws


def _generate_commentary(summary: dict, max_wins: int, baron_wins: int) -> str:
    """Gemini로 주간 해설 생성"""
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return _fallback_commentary(summary, max_wins, baron_wins)

        regime = summary.get("dominant_regime", "Unknown")
        signal = summary.get("dominant_signal", "HOLD")
        days = summary.get("days", 0)
        etf_returns = summary.get("etf_week_return", {})

        # ETF 주간 수익률 Top3
        sorted_etfs = sorted(etf_returns.items(), key=lambda x: x[1], reverse=True)
        top3 = sorted_etfs[:3] if sorted_etfs else []
        top3_str = ", ".join(f"{e}: {r:+.1f}%" for e, r in top3)

        prompt = (
            f"투자 코믹 캐릭터 Max Bullhorn(강세)과 Baron Bearsworth(약세)의 "
            f"이번 주 전투 결과를 해설해줘.\n"
            f"- 거래일: {days}일\n"
            f"- 주요 레짐: {regime}\n"
            f"- 주요 시그널: {signal}\n"
            f"- Max 승리: {max_wins}일, Baron 승리: {baron_wins}일\n"
            f"- ETF 주간 Top3: {top3_str}\n"
            f"한국어로 3줄, 재미있게 캐릭터 이름 사용. 이모지 1~2개."
        )

        result = call(prompt=prompt, model="flash-lite", max_tokens=150, temperature=0.8)
        if result.get("success"):
            return result["text"]

    except Exception as e:
        logger.warning(f"[WeeklyReview] Gemini 해설 실패: {e}")

    return _fallback_commentary(summary, max_wins, baron_wins)


def _fallback_commentary(summary: dict, max_wins: int, baron_wins: int) -> str:
    regime = summary.get("dominant_regime", "Unknown")
    if max_wins > baron_wins:
        return f"🐂 이번 주는 Max의 승리! {regime} 레짐 속에서도 강세를 유지했습니다."
    elif baron_wins > max_wins:
        return f"🐻 이번 주는 Baron이 지배! {regime} 레짐이 시장을 압박했습니다."
    else:
        return f"⚖️ 이번 주는 팽팽한 접전! {regime} 레짐에서 Max와 Baron이 균형을 이뤘습니다."


def _build_tweet(summary: dict, max_wins: int, baron_wins: int, draws: int) -> str:
    week = summary.get("week", "")
    regime = summary.get("dominant_regime", "")
    signal = summary.get("dominant_signal", "")
    days = summary.get("days", 0)

    # ETF Top/Worst
    etf_returns = summary.get("etf_week_return", {})
    sorted_etfs = sorted(etf_returns.items(), key=lambda x: x[1], reverse=True)
    top_etf = sorted_etfs[0] if sorted_etfs else ("—", 0)
    worst_etf = sorted_etfs[-1] if sorted_etfs else ("—", 0)

    winner = "🐂 Max" if max_wins > baron_wins else ("🐻 Baron" if baron_wins > max_wins else "⚖️ 무승부")

    tweet = (
        f"📊 주간 전투 결과 | {week}\n\n"
        f"🏆 {winner} | Max {max_wins}승 Baron {baron_wins}승 무 {draws}\n"
        f"🎯 레짐: {regime} | 시그널: {signal}\n"
        f"📈 Top: {top_etf[0]} {top_etf[1]:+.1f}%\n"
        f"📉 Worst: {worst_etf[0]} {worst_etf[1]:+.1f}%\n\n"
        f"#ETF #투자 #주간리뷰 #미국증시"
    )
    return tweet


def _build_tg_message(summary, max_wins, baron_wins, draws, commentary) -> str:
    week = summary.get("week", "")
    regime = summary.get("dominant_regime", "")
    signal = summary.get("dominant_signal", "")

    etf_returns = summary.get("etf_week_return", {})
    sorted_etfs = sorted(etf_returns.items(), key=lambda x: x[1], reverse=True)

    etf_lines = "\n".join(
        f"  {e}: {r:+.1f}%" for e, r in sorted_etfs
    )

    return (
        f"📊 주간 전투 결과 | {week}\n\n"
        f"🐂 Max {max_wins}승 vs 🐻 Baron {baron_wins}승 (무 {draws})\n"
        f"레짐: {regime} | 시그널: {signal}\n\n"
        f"📈 ETF 주간 수익률:\n{etf_lines}\n\n"
        f"💬 {commentary}"
    )


def _render_review_card(summary, max_wins, baron_wins, draws, commentary) -> str | None:
    """HTML → PNG 카드 이미지 생성"""
    try:
        week = summary.get("week", "")
        regime = summary.get("dominant_regime", "")
        etf_returns = summary.get("etf_week_return", {})
        sorted_etfs = sorted(etf_returns.items(), key=lambda x: x[1], reverse=True)

        winner = "MAX" if max_wins > baron_wins else ("BARON" if baron_wins > max_wins else "DRAW")
        winner_color = "#22c55e" if winner == "MAX" else ("#ef4444" if winner == "BARON" else "#eab308")

        etf_rows = "".join(
            f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
            f'border-bottom:1px solid #333">'
            f'<span>{e}</span>'
            f'<span style="color:{"#22c55e" if r >= 0 else "#ef4444"}">{r:+.1f}%</span>'
            f'</div>'
            for e, r in sorted_etfs
        )

        html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ margin:0; padding:0; background:#111; color:#fff; font-family:Arial,sans-serif; }}
.card {{ width:1080px; height:1080px; padding:60px; box-sizing:border-box;
  background:linear-gradient(135deg,#111 0%,#1a1a2e 100%); }}
.header {{ font-size:48px; font-weight:bold; margin-bottom:20px; }}
.week {{ font-size:28px; color:#888; margin-bottom:40px; }}
.score {{ display:flex; justify-content:center; gap:60px; margin:40px 0;
  font-size:64px; font-weight:bold; }}
.max {{ color:#22c55e; }}
.baron {{ color:#ef4444; }}
.vs {{ color:#666; font-size:36px; align-self:center; }}
.winner {{ text-align:center; font-size:36px; color:{winner_color};
  margin:20px 0 40px; }}
.regime {{ text-align:center; font-size:24px; color:#888; margin-bottom:30px; }}
.etfs {{ background:#1a1a1a; border-radius:12px; padding:20px; font-size:22px; }}
.etfs-title {{ font-size:20px; color:#888; margin-bottom:12px; }}
.commentary {{ margin-top:30px; font-size:20px; color:#aaa; line-height:1.5; }}
</style></head><body>
<div class="card">
  <div class="header">📊 주간 전투 결과</div>
  <div class="week">{week} | {regime}</div>
  <div class="score">
    <div class="max">🐂 {max_wins}</div>
    <div class="vs">vs</div>
    <div class="baron">🐻 {baron_wins}</div>
  </div>
  <div class="winner">{"Max Bullhorn 승리! 🏆" if winner == "MAX" else ("Baron Bearsworth 승리! 🏆" if winner == "BARON" else "팽팽한 접전! ⚖️")}</div>
  <div class="etfs">
    <div class="etfs-title">ETF 주간 수익률</div>
    {etf_rows}
  </div>
  <div class="commentary">{commentary[:120]}</div>
</div>
</body></html>'''

        kst = datetime.now(timezone.utc) + timedelta(hours=9)
        date_str = kst.strftime("%Y%m%d")
        output_path = f"data/images/weekly_review_{date_str}.png"
        os.makedirs("data/images", exist_ok=True)

        # HTML → PNG (Playwright)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.set_content(html)
            page.screenshot(path=output_path, full_page=False)
            browser.close()

        logger.info(f"[WeeklyReview] 카드 생성: {output_path}")
        return output_path

    except Exception as e:
        logger.warning(f"[WeeklyReview] 카드 생성 실패: {e}")
        return None
