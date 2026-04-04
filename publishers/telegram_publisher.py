"""
publishers/telegram_publisher.py
==================================
Telegram Bot API 발행 모듈 — 듀얼 채널 (무료 / 유료)

무료 채널: 시그널 텍스트 요약
유료 채널: 풀버전 대시보드 이미지 (PNG)
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── 환경변수 ────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
FREE_CHAT_ID = os.getenv("TELEGRAM_FREE_CHANNEL_ID", "")
PAID_CHAT_ID = os.getenv("TELEGRAM_PAID_CHANNEL_ID", "")
EN_CHAT_ID   = os.getenv("TELEGRAM_EN_CHANNEL_ID", "")   # C-11: 영어 채널
JP_CHAT_ID   = os.getenv("TELEGRAM_JP_CHANNEL_ID", "")   # C-11: 일본어 채널
# 텔레그램은 무료 서비스 — DRY_RUN 무관하게 항상 실제 전송
# X(Twitter)의 DRY_RUN과 독립적으로 동작
API_BASE     = f"https://api.telegram.org/bot{BOT_TOKEN}"
TIMEOUT_MSG  = 15   # 텍스트 타임아웃
TIMEOUT_IMG  = 30   # 이미지 타임아웃


# ── 내부 헬퍼 ───────────────────────────────────────────────
def _chat_ids(channel: str) -> list[str]:
    """channel 인자를 실제 chat_id 리스트로 변환"""
    if channel == "both":
        return [c for c in [FREE_CHAT_ID, PAID_CHAT_ID] if c]
    if channel == "paid":
        return [PAID_CHAT_ID] if PAID_CHAT_ID else []
    if channel == "en":    # C-11: 영어 채널
        return [EN_CHAT_ID] if EN_CHAT_ID else []
    if channel == "ja":    # C-11: 일본어 채널
        return [JP_CHAT_ID] if JP_CHAT_ID else []
    return [FREE_CHAT_ID] if FREE_CHAT_ID else []


def _is_configured() -> bool:
    return bool(BOT_TOKEN)


# ── 공개 인터페이스 ─────────────────────────────────────────
def send_message(
    text: str,
    channel: str = "free",
    parse_mode: str = "HTML",
) -> list[dict]:
    """
    텍스트 메시지 발행

    Args:
        text:       발행할 텍스트 (HTML 태그 허용)
        channel:    'free' | 'paid' | 'both'
        parse_mode: 'HTML' | 'Markdown'

    Returns:
        각 채널별 API 응답 리스트
    """
    if not _is_configured():
        logger.warning("[TG] BOT_TOKEN 미설정 — 텔레그램 발행 건너뜀")
        return []

    targets = _chat_ids(channel)
    if not targets:
        logger.warning(f"[TG] chat_id 미설정 (channel={channel}) — 건너뜀")
        return []

    results = []
    for cid in targets:
        try:
            res = requests.post(
                f"{API_BASE}/sendMessage",
                json={"chat_id": cid, "text": text, "parse_mode": parse_mode},
                timeout=TIMEOUT_MSG,
            )
            data = res.json()
            if data.get("ok"):
                logger.info(f"[TG] 텍스트 발행 완료 → {cid}")
            else:
                logger.error(f"[TG] 발행 실패 → {cid}: {data}")
                # DLQ 저장 (B-17) — DLQ 알림 자체는 저장하지 않음 (무한루프 방지)
                if "[DLQ]" not in text:
                    try:
                        from core.dlq import enqueue
                        enqueue("tg_message", {"text": text[:500], "channel": channel}, f"API 실패: {str(data)[:100]}")
                    except Exception:
                        pass
            results.append(data)
        except Exception as e:
            logger.error(f"[TG] 텍스트 발행 예외 → {cid}: {e}")
            # DLQ 저장 (B-17)
            if "[DLQ]" not in text:
                try:
                    from core.dlq import enqueue
                    enqueue("tg_message", {"text": text[:500], "channel": channel}, str(e)[:200])
                except Exception:
                    pass
            results.append({"ok": False, "error": str(e), "chat_id": cid})
    return results


def send_photo(
    image_path: str,
    caption: str = "",
    channel: str = "paid",
    parse_mode: str = "HTML",
) -> list[dict]:
    """
    이미지 + 캡션 발행

    Args:
        image_path: PNG 파일 경로
        caption:    이미지 캡션 (1024자 제한)
        channel:    'free' | 'paid' | 'both'
        parse_mode: 'HTML' | 'Markdown'

    Returns:
        각 채널별 API 응답 리스트
    """
    if not _is_configured():
        logger.warning("[TG] BOT_TOKEN 미설정 — 텔레그램 발행 건너뜀")
        return []

    targets = _chat_ids(channel)
    if not targets:
        logger.warning(f"[TG] chat_id 미설정 (channel={channel}) — 건너뜀")
        return []

    results = []
    for cid in targets:
        try:
            with open(image_path, "rb") as f:
                res = requests.post(
                    f"{API_BASE}/sendPhoto",
                    data={"chat_id": cid, "caption": caption[:1024], "parse_mode": parse_mode},
                    files={"photo": f},
                    timeout=TIMEOUT_IMG,
                )
            data = res.json()
            if data.get("ok"):
                logger.info(f"[TG] 이미지 발행 완료 → {cid}")
            else:
                logger.error(f"[TG] 이미지 발행 실패 → {cid}: {data}")
            results.append(data)
        except Exception as e:
            logger.error(f"[TG] 이미지 발행 예외 → {cid}: {e}")
            results.append({"ok": False, "error": str(e), "chat_id": cid})
    return results


def send_document(
    file_path: str,
    caption: str = "",
    channel: str = "paid",
    parse_mode: str = "HTML",
) -> list[dict]:
    """
    PDF 등 문서 파일 발행

    Args:
        file_path: 파일 경로 (PDF 등)
        caption:   문서 캡션 (1024자 제한)
        channel:   'free' | 'paid' | 'both'
        parse_mode: 'HTML' | 'Markdown'

    Returns:
        각 채널별 API 응답 리스트
    """
    if not _is_configured():
        logger.warning("[TG] BOT_TOKEN 미설정 — 텔레그램 발행 건너뜀")
        return []

    targets = _chat_ids(channel)
    if not targets:
        logger.warning(f"[TG] chat_id 미설정 (channel={channel}) — 건너뜀")
        return []

    results = []
    for cid in targets:
        try:
            with open(file_path, "rb") as f:
                res = requests.post(
                    f"{API_BASE}/sendDocument",
                    data={"chat_id": cid, "caption": caption[:1024], "parse_mode": parse_mode},
                    files={"document": f},
                    timeout=TIMEOUT_IMG,
                )
            data = res.json()
            if data.get("ok"):
                logger.info(f"[TG] 문서 발행 완료 → {cid}")
            else:
                logger.error(f"[TG] 문서 발행 실패 → {cid}: {data}")
            results.append(data)
        except Exception as e:
            logger.error(f"[TG] 문서 발행 예외 → {cid}: {e}")
            results.append({"ok": False, "error": str(e), "chat_id": cid})
    return results


# ── 포맷 헬퍼 ───────────────────────────────────────────────
def _build_hashtags(regime: str, risk: str, signal: str, session: str) -> str:
    """세션 + 시장 상황별 동적 해시태그 생성 (3~5개)"""
    tags = ["#ETF", "#미국증시"]

    # 레짐별
    if regime == "Risk-Off":
        tags.append("#RiskOff")
    elif regime == "Risk-On":
        tags.append("#RiskOn")
    elif "Oil" in regime:
        tags.append("#오일쇼크")

    # 리스크 레벨별
    if risk == "HIGH":
        tags.append("#공포구간")
    elif risk == "LOW":
        tags.append("#강세장")

    # 시그널별
    if signal == "BUY":
        tags.append("#매수")
    elif signal == "REDUCE":
        tags.append("#매도")

    # 세션별
    SESSION_TAGS = {
        "morning":  "#모닝브리프",
        "intraday": "#장중업데이트",
        "close":    "#마감요약",
        "full":     "#풀브리프",
        "weekly":   "#주간분석",
    }
    if session in SESSION_TAGS:
        tags.append(SESSION_TAGS[session])

    return " ".join(tags[:5])  # 최대 5개


def format_free_signal(data: dict, session: str = "morning") -> str:
    """
    무료 채널 발행용 텔레그램 텍스트 생성 — 세션별 포맷 분리

    session:
        morning  → 전략 중심 (오늘 어떻게 대응할지)
        close    → 결과 중심 (어제 어떻게 됐는지)
        intraday → 장중 업데이트
        full     → 종합 요약
        weekly   → 주간 분석
    """
    # ── 공통 데이터 추출 ─────────────────────────────
    regime  = data.get("market_regime", {}).get("market_regime", "—")
    risk    = data.get("market_regime", {}).get("market_risk_level", "—")
    signal  = data.get("trading_signal", {}).get("trading_signal", "—")
    reason  = data.get("trading_signal", {}).get("signal_reason", "")
    buy     = data.get("trading_signal", {}).get("signal_matrix", {}).get("buy_watch", [])
    hold    = data.get("trading_signal", {}).get("signal_matrix", {}).get("hold", [])
    reduce  = data.get("trading_signal", {}).get("signal_matrix", {}).get("reduce", [])
    vix     = data.get("market_snapshot", {}).get("vix", 0)
    sp500   = data.get("market_snapshot", {}).get("sp500", 0)
    us10y   = data.get("market_snapshot", {}).get("us10y", 0)
    oil     = data.get("market_snapshot", {}).get("oil", 0)
    summary = data.get("output_helpers", {}).get("one_line_summary", "")

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴", "SELL": "🔴"}
    RISK_EMOJI   = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}
    sig_e   = SIGNAL_EMOJI.get(signal, "⚪")
    risk_e  = RISK_EMOJI.get(risk, "⚪")
    sp_sign = "▼" if sp500 < 0 else "▲"
    sp_col  = "📉" if sp500 < 0 else "📈"
    tags    = _build_hashtags(regime, risk, signal, session)

    # ── Morning Brief — 전략 중심 + 뉴스 요약 + Fear & Greed ──
    if session == "morning":
        # Fear & Greed 추출
        fg = data.get("fear_greed", {})
        fg_val    = fg.get("value")
        fg_label  = fg.get("label", "")
        fg_emoji  = fg.get("emoji", "😐")
        fg_change = fg.get("change", 0)

        # BTC/ETH 추출
        crypto   = data.get("crypto", {})
        btc      = crypto.get("btc_usd")
        btc_chg  = crypto.get("btc_change_pct", 0)

        # 뉴스 헤드라인 추출
        headlines = data.get("output_helpers", {}).get("top_headlines", [])

        lines = [
            "🌅 <b>Morning Brief</b>",
            "",
            f"{risk_e} {regime}  |  Risk <b>{risk}</b>",
            "",
            "📋 <b>오늘의 전략</b>",
            f"{sig_e} SIGNAL: <b>{signal}</b>",
        ]
        if buy:
            lines.append(f"🔍 Watch: <b>{' · '.join(buy)}</b>")
        if hold:
            lines.append(f"⏸ Hold: {' · '.join(hold)}")
        if reduce:
            lines.append(f"📉 Reduce: {' · '.join(reduce)}")
        if reason:
            lines.append(f"\n💡 <i>{reason}</i>")

        # BTC 추가
        if btc:
            btc_sign = "▲" if btc_chg >= 0 else "▼"
            lines += [
                "",
                f"₿ BTC: <b>${btc:,.0f}</b>  {btc_sign}{abs(btc_chg):.1f}%",
            ]

        # Fear & Greed 추가
        if fg_val is not None:
            change_str = f"({'▲' if fg_change > 0 else '▼'}{abs(fg_change)}pt)" if fg_change != 0 else ""
            lines += [
                "",
                f"{fg_emoji} 시장심리: <b>{fg_val}/100 {fg_label}</b> {change_str}",
            ]

        # 뉴스 헤드라인 추가
        if headlines:
            lines.append("")
            lines.append("📰 <b>오늘의 헤드라인</b>")
            for i, h in enumerate(headlines[:3], 1):
                lines.append(f"{i}️⃣ {h[:40]}")

        lines += ["", tags]

    # ── Close Summary — 결과 중심 ────────────────────
    elif session == "close":
        lines = [
            "🔔 <b>Close Summary</b>  |  미국 장 마감",
            "",
            "📊 <b>오늘 결과</b>",
            f"{sp_col} SPY <b>{sp_sign}{abs(sp500):.2f}%</b>  |  VIX <b>{vix:.1f}</b>",
            f"💵 WTI <b>${oil:.1f}</b>  |  US10Y <b>{us10y:.2f}%</b>",
            "",
            f"✅ 레짐 유지: <b>{regime}</b>  {risk_e}",
            f"📌 내일 전략: <b>{signal}</b> 유지",
        ]
        if summary:
            lines.append(f"\n<i>{summary}</i>")
        lines += ["", tags]

    # ── Intraday Update — 장중 업데이트 ─────────────
    elif session == "intraday":
        lines = [
            "📡 <b>Intraday Update</b>",
            "",
            f"{sp_col} SPY <b>{sp_sign}{abs(sp500):.2f}%</b>  |  VIX <b>{vix:.1f}</b>",
            f"{risk_e} Regime: <b>{regime}</b>  |  {sig_e} Signal: <b>{signal}</b>",
        ]
        if buy:
            lines.append(f"🔍 Watch: <b>{' · '.join(buy)}</b>")
        if reason:
            lines.append(f"\n<i>{reason}</i>")
        lines += ["", tags]

    # ── Full / Weekly / 기타 — 종합 요약 ────────────
    else:
        crypto  = data.get("crypto", {})
        btc     = crypto.get("btc_usd")
        btc_chg = crypto.get("btc_change_pct", 0)

        lines = [
            "📊 <b>Investment OS — Signal</b>",
            "",
            f"{risk_e} {regime}  |  Risk <b>{risk}</b>",
            f"{sp_col} SPY <b>{sp_sign}{abs(sp500):.2f}%</b>  |  VIX <b>{vix:.1f}</b>",
        ]
        if btc:
            btc_sign = "▲" if btc_chg >= 0 else "▼"
            lines.append(f"₿ BTC <b>${btc:,.0f}</b>  {btc_sign}{abs(btc_chg):.1f}%")
        lines += [
            "",
            f"{sig_e} Signal: <b>{signal}</b>",
        ]
        if buy:
            lines.append(f"🔍 BUY Watch: <b>{' · '.join(buy)}</b>")
        if hold:
            lines.append(f"⏸ Hold: {' · '.join(hold)}")
        if reduce:
            lines.append(f"📉 Reduce: {' · '.join(reduce)}")
        if reason:
            lines.append(f"\n<i>{reason}</i>")
        if summary:
            lines.append(f"<i>{summary}</i>")
        lines += ["", "💎 <i>풀버전 대시보드 → 유료 채널</i>", "", tags]

    return "\n".join(lines)


def format_paid_report(data: dict) -> str:
    """
    유료 채널 전용 — ETF 상세 전략 + 포지션 사이징 가이드 텍스트
    풀버전 대시보드 이미지 이후 텍스트로 전송
    """
    regime  = data.get("market_regime", {}).get("market_regime", "—")
    risk    = data.get("market_regime", {}).get("market_risk_level", "—")
    signal  = data.get("trading_signal", {}).get("trading_signal", "—")
    reason  = data.get("trading_signal", {}).get("signal_reason", "")
    matrix  = data.get("trading_signal", {}).get("signal_matrix", {})
    stance  = data.get("etf_strategy", {}).get("stance", {})
    s_reason = data.get("etf_strategy", {}).get("strategy_reason", {})
    timing  = data.get("etf_analysis", {}).get("timing_signal", {})
    alloc   = data.get("etf_allocation", {}).get("allocation", {})
    prisk   = data.get("portfolio_risk", {})
    sizing  = prisk.get("position_sizing_multiplier", 0.75)
    crash   = prisk.get("crash_alert_level", "—")
    hedge   = prisk.get("hedge_intensity", "—")
    div_sc  = prisk.get("diversification_score", 0)

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴", "SELL": "🔴"}
    RISK_EMOJI   = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}
    STANCE_EMOJI = {"Overweight": "⬆️", "Underweight": "⬇️", "Neutral": "➡️"}
    TIMING_EMOJI = {
        "BUY": "🟢", "ADD ON PULLBACK": "🔵",
        "HOLD": "🟡", "REDUCE": "🔴", "SELL": "🔴"
    }

    sig_e  = SIGNAL_EMOJI.get(signal, "⚪")
    risk_e = RISK_EMOJI.get(risk, "⚪")

    ETFS = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]
    buy_list    = matrix.get("buy_watch", [])
    reduce_list = matrix.get("reduce", [])

    lines = [
        "💎 <b>Premium Report</b>  |  ETF 상세 전략",
        "",
        f"{risk_e} {regime}  |  {sig_e} <b>{signal}</b>",
        f"<i>{reason}</i>",
        "",
        "📋 <b>ETF 상세 전략</b>",
        "",
    ]

    # ETF별 상세 전략
    for etf in ETFS:
        st  = stance.get(etf, "Neutral")
        tm  = timing.get(etf, "HOLD")
        pct = alloc.get(etf, 0)
        sr  = s_reason.get(etf, "")[:30]
        se  = STANCE_EMOJI.get(st, "➡️")
        te  = TIMING_EMOJI.get(tm, "🟡")
        lines.append(
            f"{se} <b>{etf}</b>  {pct}%  |  {te} {tm}\n"
            f"   <i>{sr}</i>"
        )

    # 포지션 사이징
    sizing_pct = int(sizing * 100)
    if sizing >= 1.0:
        sizing_label = "Full Position"
        sizing_emoji = "🟢"
    elif sizing >= 0.75:
        sizing_label = "Conservative"
        sizing_emoji = "🟡"
    elif sizing >= 0.5:
        sizing_label = "Defensive"
        sizing_emoji = "🔴"
    else:
        sizing_label = "Minimal"
        sizing_emoji = "🔴"

    lines += [
        "",
        "📐 <b>포지션 사이징 가이드</b>",
        "",
        f"{sizing_emoji} 권장 포지션: <b>{sizing_label}</b>  ({sizing_pct}%)",
        f"⚠️ 크래시 경보: <b>{crash}</b>",
        f"🛡 헤지 강도: <b>{hedge}</b>",
        f"📊 분산 점수: <b>{div_sc}/100</b>",
        "",
        "💡 <b>실전 적용 방법</b>",
        f"  • 전체 투자금 × {sizing:.2f} = 실제 투자금액",
        f"  • BUY 집중: {' · '.join(buy_list) if buy_list else '—'}",
        f"  • REDUCE 대상: {' · '.join(reduce_list) if reduce_list else '—'}",
        "",
        "#프리미엄 #ETF전략 #포지션사이징",
    ]

    return "\n".join(lines)


def format_rank_change(change: dict, channel: str = "free") -> str:
    """
    ETF 랭킹 변화 알림 포맷

    ⚠️ DEPRECATED (2026-04-01): 이 함수 대신 아래 함수를 사용하세요.
       - 무료: publishers/alert_formatter.py → format_etf_rank_telegram()
       - 유료: publishers/premium_alert_formatter.py → format_etf_rank_premium()
    둘 다 signal_diff 원인 분석을 포함하는 고도화 버전입니다.
    하위호환을 위해 이 함수는 삭제하지 않습니다.

    channel: 'free' → 간결, 'paid' → 상세
    """
    top1_changed = change.get("top1_changed", False)
    old_top1     = change.get("old_top1", "—")
    new_top1     = change.get("new_top1", "—")
    moved_up     = change.get("moved_up", [])
    moved_down   = change.get("moved_down", [])
    new_rank     = change.get("new_rank", {})

    if channel == "free":
        # 무료: 핵심 변화만
        lines = ["🔄 <b>ETF 랭킹 변경</b>", ""]
        if top1_changed:
            lines.append(f"👑 1위 변경: <b>{old_top1}</b> → <b>{new_top1}</b>")
        if moved_up:
            top = moved_up[0]
            lines.append(f"📈 상승: <b>{top['etf']}</b> ({top['from']}위 → {top['to']}위)")
        if moved_down:
            bot = moved_down[0]
            lines.append(f"📉 하락: <b>{bot['etf']}</b> ({bot['from']}위 → {bot['to']}위)")
        lines += ["", "#ETF #랭킹변화 #투자"]
        return "\n".join(lines)

    else:
        # 유료: 전체 랭킹 상세
        lines = ["🔄 <b>[PREMIUM] ETF 랭킹 변경 상세</b>", ""]
        if top1_changed:
            lines.append(f"👑 1위 교체: <b>{old_top1}</b> → <b>{new_top1}</b>")
        lines.append("")
        lines.append("📊 <b>현재 랭킹</b>")
        MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
        for etf, pos in sorted(new_rank.items(), key=lambda x: x[1]):
            medal = MEDAL.get(pos, f"{pos}위")
            up_tag = " ▲" if any(x["etf"] == etf for x in moved_up) else ""
            dn_tag = " ▼" if any(x["etf"] == etf for x in moved_down) else ""
            lines.append(f"{medal} {etf}{up_tag}{dn_tag}")
        if moved_up or moved_down:
            lines.append("")
            lines.append("📌 <b>변동 상세</b>")
            for x in moved_up:
                lines.append(f"📈 {x['etf']}: {x['from']}위 → {x['to']}위")
            for x in moved_down:
                lines.append(f"📉 {x['etf']}: {x['from']}위 → {x['to']}위")
        lines += ["", "#ETF #랭킹변화 #프리미엄"]
        return "\n".join(lines)
