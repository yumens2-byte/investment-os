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
            results.append(data)
        except Exception as e:
            logger.error(f"[TG] 텍스트 발행 예외 → {cid}: {e}")
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


# ── 포맷 헬퍼 ───────────────────────────────────────────────
def format_free_signal(data: dict) -> str:
    """무료 채널 발행용 시그널 요약 텍스트 생성"""
    regime  = data.get("market_regime", {}).get("market_regime", "—")
    risk    = data.get("market_regime", {}).get("market_risk_level", "—")
    signal  = data.get("trading_signal", {}).get("trading_signal", "—")
    reason  = data.get("trading_signal", {}).get("signal_reason", "")
    buy     = data.get("trading_signal", {}).get("signal_matrix", {}).get("buy_watch", [])
    hold    = data.get("trading_signal", {}).get("signal_matrix", {}).get("hold", [])
    reduce  = data.get("trading_signal", {}).get("signal_matrix", {}).get("reduce", [])
    vix     = data.get("market_snapshot", {}).get("vix", 0)
    sp500   = data.get("market_snapshot", {}).get("sp500", 0)
    summary = data.get("output_helpers", {}).get("one_line_summary", "")

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴", "SELL": "🔴"}
    RISK_EMOJI   = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}

    sig_e  = SIGNAL_EMOJI.get(signal, "⚪")
    risk_e = RISK_EMOJI.get(risk, "⚪")
    sp_sign = "▼" if sp500 < 0 else "▲"

    lines = [
        "📊 <b>Investment OS — Daily Signal</b>",
        "",
        f"{risk_e} Regime: <b>{regime}</b>  |  Risk: <b>{risk}</b>",
        f"📈 S&P: <b>{sp_sign}{abs(sp500):.2f}%</b>  |  VIX: <b>{vix:.1f}</b>",
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
    lines.append("")
    lines.append("💎 <i>풀버전 대시보드 → 유료 채널</i>")

    return "\n".join(lines)
