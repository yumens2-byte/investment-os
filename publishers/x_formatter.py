"""
publishers/x_formatter.py
JSON Core Data → X 트윗 텍스트 변환.
출력 형식은 project-summary.html 기준.

v1.5.0 (2026-05-06): morning 세션 AI 톤 보정 P0 개선 (Q4.c — morning 한정)
  - core/tone_policy.py 연동 (페르소나+톤+규칙+예시 4요소 프롬프트)
  - core/ai_output_validator.py 연동 (7종 통합 검증 + 어색함 휴리스틱)
  - db/ai_quality_store.py 연동 (Supabase ai_quality_log 적재)
  - generate_ai_tweet:
    * morning 세션  → 신규 v1.5.0 흐름 (3회 retry, 톤 보존, 검증, 적재)
    * 그 외 세션    → 기존 v1.4.0 흐름 그대로 (Q4.c 백워드 호환 100%)
    * 외부 시그니처 동일 — run_view.py 무수정
  - _clean_tweet 확장: 마크다운 헤더, 굵은체, 메타 prefix(결론/요약/트윗/본문/내용/출력/답변),
                       대괄호 메타라벨, 끝부분 미완결 절단 회복
  - generate_ai_thread는 v1.4.0 그대로 유지 (D-7 이후 확장 검토)
  - _select_tone, _NON_PUBLISHABLE_PATTERN, _detect_non_publishable_chars
    백워드 호환 위해 모두 보존 (non-morning 분기에서 사용)

v1.4.0 (2026-04-11): format_image_tweet에 crypto_basis·btc_sentiment·PCR 직접 출력
  - signals_line 1줄 추가: ₿ Basis: Contango · 소셜 72 · PCR 0.88 Normal
  - 전 세션(morning/close/intraday/full) 적용
  - 값 없거나 Unknown이면 라인 생략 (graceful)

v1.3.0 (2026-04-11): AI 트윗/스레드 프롬프트에 미사용 신호 추가
  - generate_ai_tweet(): crypto_basis_state, pcr_state 프롬프트 입력 추가
  - generate_ai_thread(): crypto_basis_state, pcr_state, breadth_state 추가
  - Gemini가 크립토 시장 심리·옵션 심리를 트윗 내러티브에 자연스럽게 반영 가능

v1.2.0 (2026-04-07): Gemini AI 트윗 비한국어 문자 가드
  - 14:06 morning에서 Gemini가 'सावधानी'(힌디어) 단어를 트윗에 포함하여 발행
    가능 상태로 통과한 사고 발생
  - _detect_non_publishable_chars() 헬퍼 추가:
    Devanagari/Arabic/Hebrew/Thai/Cyrillic/Hiragana/Katakana 등 명확한
    비한국어 스크립트 감지
  - generate_ai_tweet, generate_ai_thread 모두에 길이 체크와 동일한 패턴으로
    언어 검증 추가 → 검증 실패 시 재시도 → 모두 실패 시 fallback
  - 한국어/영어/숫자/이모지/한자/일반 기호는 모두 통과 (false positive 방지)

v1.1.0: 해시태그 랜덤화 추가 (X 자동화 감지 방지)
"""
import logging
import random as _random
import re as _re
from typing import Optional
from config.settings import X_MAX_TWEET_LENGTH, X_HASHTAGS  # noqa: F401

logger = logging.getLogger(__name__)

VERSION = "1.5.0"
logger.info(f"[XFormatter] v{VERSION} 로드")


# ──────────────────────────────────────────────────────────────
# v1.2.0: 비한국어 문자 가드
# ──────────────────────────────────────────────────────────────
# 명확한 비한국어 스크립트 — 한국어 콘텐츠에 절대 등장하지 않음
# 한자(CJK)는 한국 콘텐츠에서 가끔 사용 가능하므로 제외 (false positive 방지)
_NON_PUBLISHABLE_PATTERN = _re.compile(
    "["
    "\u0900-\u097F"   # Devanagari (힌디, 산스크리트, 마라티)
    "\u0980-\u09FF"   # Bengali
    "\u0A00-\u0A7F"   # Gurmukhi (펀자브)
    "\u0A80-\u0AFF"   # Gujarati
    "\u0B00-\u0B7F"   # Oriya
    "\u0B80-\u0BFF"   # Tamil
    "\u0C00-\u0C7F"   # Telugu
    "\u0C80-\u0CFF"   # Kannada
    "\u0D00-\u0D7F"   # Malayalam
    "\u0E00-\u0E7F"   # Thai
    "\u0E80-\u0EFF"   # Lao
    "\u1000-\u109F"   # Myanmar
    "\u0600-\u06FF"   # Arabic
    "\u0750-\u077F"   # Arabic Supplement
    "\u08A0-\u08FF"   # Arabic Extended-A
    "\u0590-\u05FF"   # Hebrew
    "\u0400-\u04FF"   # Cyrillic (러시아어 등)
    "\u0500-\u052F"   # Cyrillic Supplement
    "\u3040-\u309F"   # Hiragana (일본어)
    "\u30A0-\u30FF"   # Katakana (일본어)
    "\u0370-\u03FF"   # Greek
    "]"
)


def _detect_non_publishable_chars(text: str) -> Optional[str]:
    """
    텍스트에서 비한국어 문자(힌디/아랍/히브리/태국/키릴/일본 가나 등) 감지.

    한국어 콘텐츠에 등장해서는 안 되는 명확한 외국어 스크립트만 차단.
    한자(CJK)는 false positive 방지 위해 통과시킴.

    Returns:
        감지된 첫 외국어 토큰 (str), 없으면 None
    """
    if not text:
        return None
    matches = _NON_PUBLISHABLE_PATTERN.findall(text)
    if not matches:
        return None
    # 첫 매칭 위치 주변 컨텍스트 추출 (디버깅용)
    m = _NON_PUBLISHABLE_PATTERN.search(text)
    if m:
        start = max(0, m.start() - 5)
        end = min(len(text), m.end() + 10)
        return text[start:end]
    return matches[0]


# ──────────────────────────────────────────────────────────────
# 이모지 매핑
# ──────────────────────────────────────────────────────────────

_REGIME_EMOJI = {
    "Risk-On": "🟢",
    "Risk-Off": "🔴",
    "Oil Shock": "🛢️",
    "Liquidity Crisis": "💧",
    "Recession Risk": "📉",
    "Stagflation Risk": "🔥",
    "AI Bubble": "🤖",
    "Transition": "🔄",
}

_RISK_EMOJI = {
    "LOW": "🟢",
    "MEDIUM": "🟡",
    "HIGH": "🔴",
}

_SIGNAL_EMOJI = {
    "BUY": "🚀",
    "ADD": "📈",
    "HOLD": "⏸️",
    "REDUCE": "⚠️",
    "HEDGE": "🛡️",
    "SELL": "🚨",
}

_STRATEGY_MAP = {
    "Risk-On": "공격 가능 구간",
    "Risk-Off": "방어 우선",
    "Oil Shock": "에너지/방산 중심",
    "Liquidity Crisis": "채권·현금 우선",
    "Recession Risk": "방어 포지션",
    "Stagflation Risk": "에너지·실물 헷지",
    "AI Bubble": "성장주 주도",
    "Transition": "선택적 접근",
}

_STRATEGY_QUOTE = {
    "Risk-On": "공격 개시 구간",
    "Risk-Off": "공격 금지 구간",
    "Oil Shock": "에너지 섹터 우선",
    "Liquidity Crisis": "현금이 왕",
    "Recession Risk": "방어가 최선의 공격",
    "Stagflation Risk": "실물 자산을 주목",
    "AI Bubble": "모멘텀 따라가되 출구 확인",
    "Transition": "섣부른 베팅 금지",
}

# ── 세션별 해시태그 ──────────────────────────────────────────
SESSION_TAGS = {
    "morning":  "#아침시장",
    "intraday": "#장중",
    "close":    "#마감",
    "weekly":   "#주간분석",
}
REGIME_TAGS = {
    "Risk-On":          "#RiskOn #성장주",
    "Risk-Off":         "#RiskOff #방어",
    "Oil Shock":        "#OilShock #에너지",
    "Recession Risk":   "#경기침체 #Recession",
    "Stagflation Risk": "#스태그플레이션",
    "Liquidity Crisis": "#유동성위기",
    "Crisis Regime":    "#위기경보 #Crisis",
    "Transition":       "#전환구간",
}

# ── 해시태그 랜덤화 풀 (X 자동화 감지 방지) ──────────────────
_BASE_TAGS_POOL = [
    "#ETF #투자 #미국증시",
    "#미국주식 #ETF투자 #시장분석",
    "#투자전략 #ETF #월가",
    "#주식 #미국시장 #투자분석",
    "#ETF전략 #투자일지 #미국증시",
    "#미국ETF #시장브리핑 #투자",
    "#투자 #미국주식 #포트폴리오",
    "#ETF분석 #미장 #투자전략",
]

_EXTRA_TAGS_POOL = [
    "#매크로", "#거시경제", "#시장전망", "#포트폴리오",
    "#자산배분", "#리스크관리", "#투자일기", "#시황",
    "#월가브리핑", "#데일리분석", "#미장브리핑", "#투자메모",
    "#시장분석", "#경제지표", "#글로벌시장", "#AI투자",
]


def _random_hashtags(regime: str = "", session: str = "") -> str:
    """매번 다른 해시태그 조합 생성 — X 자동화 감지 방지"""
    # 기본 태그 (8종 중 1개 랜덤)
    base = _random.choice(_BASE_TAGS_POOL)

    # 레짐 태그 (있으면 추가)
    regime_tag = REGIME_TAGS.get(regime, "")

    # 세션 태그 (있으면 추가)
    session_tag = SESSION_TAGS.get(session, "")

    # 추가 태그 (16종 중 1개 랜덤)
    extra = _random.choice(_EXTRA_TAGS_POOL)

    parts = [base]
    if regime_tag:
        parts.append(regime_tag)
    if session_tag:
        parts.append(session_tag)
    parts.append(extra)

    return " ".join(parts).strip()


# ──────────────────────────────────────────────────────────────
# 포맷 생성
# ──────────────────────────────────────────────────────────────

def _format_change(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def format_market_snapshot_tweet(data: dict, session_label: str = "Market Snapshot") -> str:
    """
    project-summary.html 기준 출력 형식.

    📊 Market Snapshot (미국 시간 표기)
    📉 SPY -1.2% | VIX +8%
    🧭 Risk-Off
    🎯 방어 우선
    ⚡ "공격 금지 구간"
    #ETF #투자 #미국증시
    """
    snap = data.get("market_snapshot", {})
    regime_info = data.get("market_regime", {})
    trading_info = data.get("trading_signal", {})

    regime = regime_info.get("market_regime", "Unknown")
    risk_level = regime_info.get("market_risk_level", "MEDIUM")
    trading_signal = trading_info.get("trading_signal", "HOLD")

    sp500 = snap.get("sp500", 0.0)
    vix = snap.get("vix", 20.0)
    us10y = snap.get("us10y", 4.0)
    oil = snap.get("oil", 75.0)

    regime_emoji = _REGIME_EMOJI.get(regime, "📊")
    risk_emoji = _RISK_EMOJI.get(risk_level, "🟡")
    signal_emoji = _SIGNAL_EMOJI.get(trading_signal, "⏸️")

    strategy = _STRATEGY_MAP.get(regime, "시장 상황 판단 중")
    quote = _STRATEGY_QUOTE.get(regime, "신중한 접근 필요")

    # 해시태그 랜덤 생성
    tags = _random_hashtags(regime=regime)

    lines = [
        f"📊 {session_label}",
        f"",
        f"{'📈' if sp500 >= 0 else '📉'} SPY {_format_change(sp500)} | VIX {vix:.1f} | US10Y {us10y:.2f}%",
        f"🛢️ WTI ${oil:.1f}",
        f"",
        f"{regime_emoji} {regime}",
        f"{risk_emoji} Risk {risk_level}",
        f"🎯 {strategy}",
        f"{signal_emoji} \"{quote}\"",
        f"",
        tags,
    ]

    tweet = "\n".join(lines)

    # 길이 초과 시 요약 버전
    if len(tweet) > X_MAX_TWEET_LENGTH:
        tweet = _format_compact(data, session_label)

    return tweet


def _format_compact(data: dict, session_label: str) -> str:
    """280자 초과 시 compact 포맷"""
    snap = data.get("market_snapshot", {})
    regime = data.get("market_regime", {}).get("market_regime", "Unknown")
    risk = data.get("market_regime", {}).get("market_risk_level", "MED")
    signal = data.get("trading_signal", {}).get("trading_signal", "HOLD")

    sp500 = snap.get("sp500", 0.0)
    vix = snap.get("vix", 20.0)

    emoji = _REGIME_EMOJI.get(regime, "📊")
    quote = _STRATEGY_QUOTE.get(regime, "신중")

    # 해시태그 랜덤 생성
    tags = _random_hashtags(regime=regime)

    lines = [
        f"📊 {session_label}",
        f"SPY {_format_change(sp500)} | VIX {vix:.1f} | {risk}",
        f"{emoji} {regime}",
        f"⚡ \"{quote}\"",
        tags,
    ]
    return "\n".join(lines)


def format_thread_posts(data: dict) -> list:
    """
    X 쓰레드 형식 (1~N 포스트).
    scoreboard + why + allocation 포함.
    """
    snap = data.get("market_snapshot", {})
    regime = data.get("market_regime", {})
    etf_rank = data.get("etf_analysis", {}).get("etf_rank", {})
    signal_matrix = data.get("trading_signal", {}).get("signal_matrix", {})
    alloc = data.get("etf_allocation", {}).get("allocation", {})
    summary = data.get("output_helpers", {}).get("one_line_summary", "")

    # Top ETF
    top3 = [k for k, _ in sorted(etf_rank.items(), key=lambda x: x[1])[:3]]

    sp500 = snap.get("sp500", 0.0)
    nasdaq = snap.get("nasdaq", 0.0)
    vix = snap.get("vix", 20.0)

    # 해시태그 랜덤 생성
    tags = _random_hashtags(regime=regime.get("market_regime", ""))

    posts = [
        # [1] 요약
        f"📊 Investment OS — {summary}\n\n{tags}",
        # [2] 레짐
        f"📍 시장 레짐: {regime.get('market_regime')} | 리스크: {regime.get('market_risk_level')}\n\n💬 {regime.get('regime_reason', '')}",
        # [3] 스냅샷
        f"📈 시장 현황\nSPY {_format_change(sp500)} | Nasdaq {_format_change(nasdaq)} | VIX {vix:.1f}",
        # [4] ETF 리더십
        f"🏆 ETF 순위\n1위: {top3[0] if len(top3) > 0 else '-'} | 2위: {top3[1] if len(top3) > 1 else '-'} | 3위: {top3[2] if len(top3) > 2 else '-'}",
        # [5] 시그널
        f"🎯 Buy/Add: {', '.join(signal_matrix.get('buy_watch', []))}\n⏸️ Hold: {', '.join(signal_matrix.get('hold', []))}\n⚠️ Reduce: {', '.join(signal_matrix.get('reduce', []))}",
        # [6] 배분
        f"💼 포트폴리오 배분\n" + " | ".join([f"{k} {v}%" for k, v in alloc.items()]),
    ]

    # 각 포스트 길이 제한 체크
    validated = []
    for post in posts:
        if len(post) > X_MAX_TWEET_LENGTH:
            post = post[:X_MAX_TWEET_LENGTH - 3] + "..."
        validated.append(post)

    return validated


def format_image_tweet(data: dict, session: str = "morning") -> str:
    """
    이미지 첨부 시 간결한 트윗 텍스트 생성 (60~80자).
    이미지가 상세 정보를 담으므로 텍스트는 핵심만.

    v1.4.0: signals_line 추가 — Basis · 소셜감성 · PCR 직접 표시

    Returns: 트윗 텍스트 (280자 이내)
    """
    snap        = data.get("market_snapshot", {})
    regime      = data.get("market_regime", {})
    signal_data = data.get("trading_signal", {})
    helpers     = data.get("output_helpers", {})
    signals     = data.get("signals", {})          # v1.4.0

    regime_name = regime.get("market_regime", "")
    risk_level  = regime.get("market_risk_level", "")
    signal      = signal_data.get("trading_signal", "HOLD")
    sp500       = snap.get("sp500", 0) or 0
    vix         = snap.get("vix", 0) or 0
    oil         = snap.get("oil", 0) or 0
    summary     = helpers.get("one_line_summary", "")[:50]

    session_labels = {
        "morning":  "Morning Brief",
        "intraday": "Intraday Briefing",
        "close":    "Close Summary",
        "weekly":   "Weekly Review",
    }
    session_lbl = session_labels.get(session, "Market Snapshot")

    # 시그널 이모지
    sig_emoji = {"BUY": "🟢", "ADD": "🟢", "HOLD": "🟡",
                 "REDUCE": "🟠", "HEDGE": "🔵", "SELL": "🔴"}.get(signal, "🟡")
    trend_emoji = "📈" if sp500 >= 0 else "📉"

    # 라인 구성
    line1 = f"📊 {session_lbl}  |  {regime_name}"
    line2 = f"{trend_emoji} SPY {sp500:+.2f}%  |  VIX {vix:.1f}  |  WTI ${oil:.1f}"
    line3 = f"{sig_emoji} SIGNAL: {signal}"
    line4 = summary

    # Fear & Greed (morning 세션만 추가)
    fg_line = ""
    if session == "morning":
        fg = data.get("fear_greed", {})
        if fg and fg.get("value"):
            fg_emoji = fg.get("emoji", "😐")
            fg_val   = fg.get("value", 0)
            fg_lbl   = fg.get("label", "")
            fg_chg   = fg.get("change", 0)
            chg_str  = f" ({fg_chg:+d})" if fg_chg else ""
            fg_line  = f"{fg_emoji} F&G: {fg_val}/100 {fg_lbl}{chg_str}"

    # ── v1.4.0: Crypto Basis · BTC 소셜감성 · PCR 직접 표시 ──────
    signals_line = ""
    _basis_state = signals.get("crypto_basis_state", "") or ""
    _btc_sent    = signals.get("btc_social_sentiment")
    _pcr_val     = signals.get("pcr_value", 0) or 0
    _pcr_state   = signals.get("pcr_state", "") or ""

    _parts = []
    if _basis_state and _basis_state not in ("Unknown", ""):
        _basis_short = "Con↑" if "Contango" in _basis_state else ("Back↓" if "Backwardation" in _basis_state else _basis_state[:4])
        _parts.append(f"Basis {_basis_short}")
    if _btc_sent is not None:
        _sent_emoji = "🔥" if _btc_sent >= 70 else ("❄️" if _btc_sent <= 30 else "")
        _parts.append(f"소셜 {_btc_sent:.0f}{_sent_emoji}")
    if _pcr_val > 0 and _pcr_state and _pcr_state not in ("Unknown", "—"):
        _parts.append(f"PCR {_pcr_val:.2f}")
    if _parts:
        signals_line = f"₿ {' · '.join(_parts)}"
    # ──────────────────────────────────────────────────────────────

    # 해시태그 랜덤 생성 (고정 패턴 제거)
    tags = _random_hashtags(regime=regime_name, session=session)

    body = f"{line1}\n\n{line2}\n{line3}"
    if fg_line:
        body += f"\n{fg_line}"
    if signals_line:                                # v1.4.0
        body += f"\n{signals_line}"
    if line4:
        body += f"\n{line4}"
    tweet = f"{body}\n\n{tags}"
    return tweet[:280]


# ──────────────────────────────────────────────────────────────
# C-1: AI 트윗 생성 (Gemini)
# ──────────────────────────────────────────────────────────────

def _select_tone(risk: str, regime: str) -> str:
    """F-4: 레짐/리스크 연동 톤 선택 — 시장 상황에 맞는 톤만 허용"""
    if risk == "HIGH" or regime in ("Liquidity Crisis", "Recession Risk"):
        return _random.choice(["긴급", "경계"])
    elif risk == "MEDIUM" or "Shock" in regime or "Stagflation" in regime:
        return _random.choice(["진지한", "신중한", "분석적"])
    else:  # LOW / Normal
        return _random.choice(["낙관적", "여유로운", "유머러스"])


def generate_ai_tweet(data: dict, session_label: str = "Market Snapshot") -> str:
    """
    Gemini로 매 세션 다른 톤/표현의 트윗 생성.

    v1.5.0:
      - morning 세션  → tone_policy 기반 신규 흐름 (페르소나+톤+규칙+예시 4요소)
      - 그 외 세션    → v1.4.0 인라인 흐름 그대로 (Q4.c 백워드 호환)

    Returns: 트윗 텍스트 (280자 이내)
    """
    session = _label_to_session(session_label)
    if session == "morning":
        return _generate_ai_tweet_morning_v150(data, session_label, session)

    # Q4.c — non-morning 세션은 v1.4.0 흐름 100% 그대로
    return _generate_ai_tweet_legacy_v140(data, session_label)


def _label_to_session(session_label: str) -> str:
    """
    run_view.py가 전달하는 표시 라벨을 세션 식별자로 변환.
    예: "Morning Brief 🌅" → "morning"
    매칭 실패 시 'unknown' 반환 → tone_policy.select_persona_tone이 None 반환 →
    v1.4.0 흐름으로 분기.
    """
    if not session_label:
        return "unknown"
    label_lower = session_label.lower()
    if "morning" in label_lower:
        return "morning"
    if "intraday" in label_lower:
        return "intraday"
    if "close" in label_lower:
        return "close"
    if "full" in label_lower:
        return "full"
    if "weekly" in label_lower:
        return "weekly"
    if "narrative" in label_lower:
        return "narrative"
    return "unknown"


# ──────────────────────────────────────────────────────────────
# v1.5.0 신규 흐름 (morning 한정)
# ──────────────────────────────────────────────────────────────

_MAX_ATTEMPTS_V150 = 3
_TEMPERATURE_SCHEDULE = (0.85, 0.70, 0.55)   # 1차 → 2차 → 3차 (점진 보수화)
_MAX_TOKENS_V150 = 350                         # 한국어 200자 + 마진


def _generate_ai_tweet_morning_v150(
    data: dict,
    session_label: str,
    session: str,
) -> str:
    """morning 세션 신규 흐름 — tone_policy + ai_output_validator + ai_quality_store."""
    try:
        from core.gemini_gateway import call, is_available
        from core.tone_policy import (
            select_persona_tone,
            build_tweet_prompt,
            build_retry_prompt,
        )
        from core.ai_output_validator import validate
        from db.ai_quality_store import log_ai_attempt
    except ImportError as e:
        logger.warning(
            f"[XFormatter] v1.5.0 모듈 import 실패 → v1.4.0 fallback: {e}"
        )
        return _generate_ai_tweet_legacy_v140(data, session_label)

    # 1) Gemini 미설정 → 즉시 fallback
    if not is_available():
        log_ai_attempt(
            session=session, mode="tweet", attempt=0,
            tone_spec=None, output_text=None, validation=None,
            success=False, fallback_used=True,
            gemini_meta={"model": None, "key_used": None, "error": "gemini_unavailable"},
        )
        return format_market_snapshot_tweet(data, session_label)

    # 2) ToneSpec 결정 (한 번만 — retry 시 동일 객체 재주입 → 톤 보존)
    risk   = data.get("market_regime", {}).get("market_risk_level", "MEDIUM")
    regime = data.get("market_regime", {}).get("market_regime", "Unknown")
    spec = select_persona_tone(risk, regime, session)

    if spec is None:
        # 매트릭스 누락 — 안전 fallback (이론상 morning에서는 발생 안 함)
        logger.warning("[XFormatter] ToneSpec None (매트릭스 누락) → v1.4.0 fallback")
        return _generate_ai_tweet_legacy_v140(data, session_label)

    # 3) 1차 프롬프트
    sample_tags = _random_hashtags(regime=regime)
    prompt = build_tweet_prompt(data, spec, session_label, sample_hashtags=sample_tags)

    last_text: Optional[str] = None
    last_validation = None

    for attempt in range(1, _MAX_ATTEMPTS_V150 + 1):
        temperature = _TEMPERATURE_SCHEDULE[attempt - 1]
        gemini_meta_default = {"model": None, "key_used": None, "error": None}

        try:
            result = call(
                prompt=prompt,
                model="flash-lite",
                max_tokens=_MAX_TOKENS_V150,
                temperature=temperature,
            )
        except Exception as e:
            logger.warning(f"[XFormatter] Gemini 호출 예외 (attempt={attempt}): {e}")
            log_ai_attempt(
                session=session, mode="tweet", attempt=attempt,
                tone_spec=spec, output_text=None, validation=None,
                success=False, fallback_used=False,
                gemini_meta={**gemini_meta_default, "error": str(e)},
            )
            continue

        gemini_meta = {
            "model":    result.get("model"),
            "key_used": result.get("key_used"),
            "error":    result.get("error"),
        }

        if not result.get("success"):
            log_ai_attempt(
                session=session, mode="tweet", attempt=attempt,
                tone_spec=spec, output_text=None, validation=None,
                success=False, fallback_used=False,
                gemini_meta=gemini_meta,
            )
            continue

        text = _clean_tweet(result.get("text", "") or "")
        validation = validate(text, spec)

        log_ai_attempt(
            session=session, mode="tweet", attempt=attempt,
            tone_spec=spec, output_text=text, validation=validation,
            success=validation.passed,
            fallback_used=False,
            gemini_meta=gemini_meta,
        )

        if validation.passed:
            logger.info(
                f"[XFormatter] v1.5.0 AI 트윗 생성 ({len(text)}자, attempt={attempt}, "
                f"awk={validation.awkwardness_score:.2f}, tone={spec.tone_name})"
            )
            return text

        last_text = text
        last_validation = validation
        logger.info(
            f"[XFormatter] v1.5.0 검증 실패 attempt={attempt} reason={validation.failure_reason} "
            f"awk={validation.awkwardness_score:.2f} → retry"
        )
        prompt = build_retry_prompt(
            original_output=text,
            failure_reason=validation.failure_reason or "awkward",
            spec=spec,
        )

    # 4) 모든 시도 실패 → fallback
    logger.warning(
        f"[XFormatter] v1.5.0 AI 트윗 {_MAX_ATTEMPTS_V150}회 실패 → fallback "
        f"(last_reason={last_validation.failure_reason if last_validation else 'unknown'})"
    )
    log_ai_attempt(
        session=session, mode="tweet", attempt=_MAX_ATTEMPTS_V150 + 1,
        tone_spec=spec, output_text=last_text, validation=last_validation,
        success=False, fallback_used=True,
        gemini_meta={"model": None, "key_used": None, "error": "all_attempts_failed"},
    )
    return format_market_snapshot_tweet(data, session_label)


# ──────────────────────────────────────────────────────────────
# v1.4.0 기존 흐름 (non-morning 세션 보존)
# 본체는 v1.4.0 generate_ai_tweet과 한 글자도 다르지 않음 — rename only.
# ──────────────────────────────────────────────────────────────

def _generate_ai_tweet_legacy_v140(data: dict, session_label: str = "Market Snapshot") -> str:
    """
    Gemini로 매 세션 다른 톤/표현의 트윗 생성.
    글자수 초과 시 2회 재시도 (요약 요청).
    실패 시 기존 하드코딩 트윗 fallback.

    Returns: 트윗 텍스트 (280자 이내)
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return format_market_snapshot_tweet(data, session_label)

        snap = data.get("market_snapshot", {})
        regime_info = data.get("market_regime", {})
        trading_info = data.get("trading_signal", {})
        alloc = data.get("etf_allocation", {}).get("allocation", data.get("etf_allocation", {}))
        fg = data.get("fear_greed", {})
        signals_data = data.get("signals", {})

        sp500 = snap.get("sp500", 0.0)
        vix = snap.get("vix", 20.0)
        oil = snap.get("oil", 75.0)
        us10y = snap.get("us10y", 4.0)
        regime = regime_info.get("market_regime", "Unknown")
        risk = regime_info.get("market_risk_level", "MEDIUM")
        signal = trading_info.get("trading_signal", "HOLD")
        fg_val = fg.get("value", 50) if fg else 50
        fg_label = fg.get("label", "") if fg else ""

        # v1.3.0: 미사용 신호 추출
        crypto_basis_state = signals_data.get("crypto_basis_state", "") or ""
        pcr_state          = signals_data.get("pcr_state", "") or ""

        # ETF Top3
        top3 = []
        if alloc and isinstance(alloc, dict):
            sorted_etfs = sorted(alloc.items(), key=lambda x: x[1], reverse=True)
            top3 = [f"{e}({w}%)" for e, w in sorted_etfs[:3]]

        # F-4: 레짐/리스크 연동 톤 선택 (시장 상황에 맞는 톤만 허용)
        tone = _select_tone(risk, regime)

        # 해시태그도 랜덤으로 지시 (Gemini가 매번 다른 태그 조합 사용)
        sample_tags = _random_hashtags(regime=regime)

        prompt = (
            f"투자 분석 X 트윗 1개만 작성해줘.\n"
            f"- 세션: {session_label}\n"
            f"- SPY: {sp500:+.2f}%, VIX: {vix:.1f}, WTI: ${oil:.1f}, US10Y: {us10y:.2f}%\n"
            f"- 레짐: {regime}, 리스크: {risk}, 시그널: {signal}\n"
            f"- F&G: {fg_val} ({fg_label})\n"
            f"- Top ETF: {', '.join(top3)}\n"
            + (f"- BTC Basis: {crypto_basis_state}\n" if crypto_basis_state and crypto_basis_state not in ("Unknown", "") else "")
            + (f"- PCR: {pcr_state}\n" if pcr_state and pcr_state not in ("Unknown", "—") else "")
            + f"조건:\n"
            f"- 반드시 140~200자 이내, 한국어\n"
            f"- 이모지 2~3개 포함\n"
            f"- 톤: {tone} (이 톤 하나로만 작성)\n"
            f"- 해시태그 3~4개 포함 (예시: {sample_tags} 참고하되 매번 다르게)\n"
            f"- 트윗 본문만 출력. 톤 라벨, 설명, 부연, 선택지 없이 트윗 1개만\n"
        )

        # 1차 시도
        result = call(prompt=prompt, model="flash-lite", max_tokens=200, temperature=0.9)

        if result.get("success"):
            ai_tweet = _clean_tweet(result["text"])
            non_kr = _detect_non_publishable_chars(ai_tweet)
            if 50 < len(ai_tweet) <= 280 and non_kr is None:
                logger.info(f"[XFormatter] AI 트윗 생성 ({len(ai_tweet)}자) | source=gemini | attempt=1")
                return ai_tweet

            # ── 길이 초과 또는 비한국어 문자 감지 → 재시도 ──
            if non_kr is not None:
                logger.warning(
                    f"[XFormatter] 비한국어 문자 감지: '{non_kr}' → 재시도"
                )
            for retry in range(2):
                if non_kr is not None:
                    retry_prompt = (
                        f"아래 트윗에 한국어가 아닌 외국어 단어가 포함되어 있습니다. "
                        f"반드시 100% 한국어, 영어, 숫자, 이모지만 사용해서 다시 작성해주세요. "
                        f"힌디어, 아랍어, 일본어 가나, 태국어, 러시아어 등 절대 금지.\n"
                        f"길이는 200자 이내, 핵심 수치와 해시태그 유지.\n"
                        f"설명 없이 트윗 본문만 출력:\n\n"
                        f"{ai_tweet}"
                    )
                else:
                    retry_prompt = (
                        f"아래 트윗이 {len(ai_tweet)}자로 너무 깁니다. "
                        f"반드시 200자 이내로 줄여줘. 핵심 수치와 해시태그만 남기고 축약.\n"
                        f"설명 없이 축약된 트윗 본문만 출력:\n\n"
                        f"{ai_tweet}"
                    )
                retry_result = call(
                    prompt=retry_prompt,
                    model="flash-lite",
                    max_tokens=200,
                    temperature=0.5,
                )
                if retry_result.get("success"):
                    shortened = _clean_tweet(retry_result["text"])
                    shortened_non_kr = _detect_non_publishable_chars(shortened)
                    if 50 < len(shortened) <= 280 and shortened_non_kr is None:
                        logger.info(
                            f"[XFormatter] AI 트윗 재시도 성공 ({len(shortened)}자) | "
                            f"source=gemini | attempt={retry + 2}"
                        )
                        return shortened
                    ai_tweet = shortened  # 다음 재시도에 사용
                    non_kr = shortened_non_kr  # 비한국어 상태 갱신

            logger.warning(
                f"[XFormatter] AI 트윗 3회 시도 실패 "
                f"({len(ai_tweet)}자, non_kr={non_kr is not None}) → fallback"
            )

    except Exception as e:
        logger.warning(f"[XFormatter] AI 트윗 생성 실패 → fallback: {e}")

    return format_market_snapshot_tweet(data, session_label)


# ──────────────────────────────────────────────────────────────
# AI 응답 후처리 — v1.5.0 확장
# ──────────────────────────────────────────────────────────────

# 1) 줄 전체가 메타 라벨인 경우 (그 줄 통째로 제거)
_META_LINE_PATTERNS = (
    # [긴급 톤], **[긴급 톤]**, [톤: 긴급] 등 라벨 줄
    _re.compile(r"^\s*\*{0,2}\[?\s*톤\s*[:：]?\s*[^\]\n]*\]?\*{0,2}\s*$"),
    _re.compile(
        r"^\s*\*{0,2}\[?\s*(긴급|경계|진지한|신중한|분석적|낙관적|여유로운|유머러스)\s*톤\s*\]?\*{0,2}\s*$"
    ),
    # 굵은체로만 이뤄진 한 줄
    _re.compile(r"^\s*\*{2}[^*\n]+\*{2}\s*$"),
)

# 2) prefix만 매칭 — 본문 시작에서 잘라냄
_META_INLINE_PREFIX_PATTERN = _re.compile(
    r"^\s*\*{0,2}\s*(결론|요약|트윗|본문|내용|출력|답변|결과|막)\s*[:：]\s*"
)

# 3) 마크다운 헤더 prefix (## 시장 분석 → 시장 분석)
_MARKDOWN_HEADER_PATTERN = _re.compile(r"^\s*#{1,6}\s+")

# 끝부분 미완결 절단 회복용 — 마지막 문장 경계
_SENTENCE_END_RE = _re.compile(r'[.!?。…]\s')


def _clean_tweet(text: str) -> str:
    """
    v1.5.0: AI 응답에서 트윗 텍스트만 추출 (확장).

    처리 순서:
      1. 따옴표/백틱 제거
      2. ``` 블록 제거
      3. 첫 줄이 메타 라벨이면 줄 제거 (반복, 최대 3회)
      4. 본문 시작 prefix(결론/요약/트윗/본문/내용/출력/답변/막) 제거
      5. 본문 시작 마크다운 헤더(#) prefix 제거
      6. 굵은체(**텍스트**), 인라인 코드(`텍스트`) 마크다운 잔존 제거
      7. 끝부분 미완결 절단 회복

    v1.4.0 처리(따옴표·블록·톤라벨·"막:")는 모두 보존, 그 외 항목 추가.
    """
    if not text:
        return ""

    tweet = text.strip()

    # 1) 따옴표/백틱 제거
    tweet = tweet.strip('"').strip("'").strip("`")

    # 2) ``` 블록 제거
    if tweet.startswith("```"):
        tweet = tweet.split("\n", 1)[-1] if "\n" in tweet else tweet[3:]
    if tweet.endswith("```"):
        tweet = tweet.rsplit("```", 1)[0]
    tweet = tweet.strip()

    # 3) 첫 줄 메타 라벨 줄 통째로 제거 (반복)
    for _ in range(3):
        if not tweet:
            break
        lines = tweet.split("\n")
        first_line = lines[0]
        matched = False
        for pat in _META_LINE_PATTERNS:
            if pat.search(first_line):
                lines = lines[1:]
                tweet = "\n".join(lines).strip()
                matched = True
                break
        if not matched:
            break

    # 4) 본문 시작 prefix만 제거 (결론:/요약:/트윗:/본문:/내용:/출력:/답변:/막:)
    tweet = _META_INLINE_PREFIX_PATTERN.sub("", tweet, count=1)

    # 5) 마크다운 헤더 prefix 제거 (## 제목 → 제목)
    tweet = _MARKDOWN_HEADER_PATTERN.sub("", tweet, count=1)

    # 6) 굵은체/인라인코드 마크다운 잔존 제거
    tweet = _re.sub(r"\*{2}([^*\n]+)\*{2}", r"\1", tweet)
    tweet = _re.sub(r"`([^`\n]+)`", r"\1", tweet)

    # 7) 끝부분 미완결 절단 회복
    tweet = _recover_incomplete_trailing(tweet)

    return tweet.strip()


def _recover_incomplete_trailing(text: str) -> str:
    """
    마지막 어절이 조사/접속어로 끝나면 직전 완결 문장에서 절단.
    너무 짧아질 위험이 있으면 원문 유지 (>= 70% 보존).
    """
    if not text or len(text) < 50:
        return text

    body = text.rstrip()
    # 해시태그 라인은 그대로 두고 본문만 검사
    if "\n" in body:
        parts = body.rsplit("\n", 1)
        main_body = parts[0]
        tail = "\n" + parts[1]
    else:
        main_body = body
        tail = ""

    last_token_match = _re.search(r"\S+$", main_body)
    if not last_token_match:
        return text

    last_token = last_token_match.group()
    cleaned_token = _re.sub(r"[.!?…」』#\s]+$", "", last_token)
    cleaned_token = _re.sub(
        r"["
        "\U0001F300-\U0001F9FF"
        "\U00002600-\U000027BF"
        "]+$",
        "",
        cleaned_token,
    )
    if not cleaned_token:
        return text

    incomplete = _re.compile(
        r"(은|는|이|가|을|를|에|에서|와|과|도|만|의|로|으로|및|또는|그리고|하지만|그런데|즉)$"
    )
    if not incomplete.search(cleaned_token):
        return text

    boundary = -1
    for m in _SENTENCE_END_RE.finditer(main_body):
        boundary = m.end()
    if boundary < 0:
        return text

    truncated = main_body[:boundary].rstrip() + tail
    if len(truncated) < len(text) * 0.7:
        return text
    return truncated


# ──────────────────────────────────────────────────────────────
# C-12: AI 스레드 자동 생성 (2026-04-03)
# ──────────────────────────────────────────────────────────────

def generate_ai_thread(data: dict) -> list:
    """
    Gemini로 5~7개 포스트 X 스레드 자동 생성.
    실패 시 기존 하드코딩 스레드 fallback.

    Returns: list[str] — 각 포스트 텍스트
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return format_thread_posts(data)

        snap = data.get("market_snapshot", {})
        regime_info = data.get("market_regime", {})
        trading_info = data.get("trading_signal", {})
        alloc = data.get("etf_allocation", {}).get("allocation", data.get("etf_allocation", {}))
        news = data.get("news_analysis", {})
        signals_data = data.get("signals", {})

        sp500 = snap.get("sp500", 0.0)
        vix = snap.get("vix", 20.0)
        oil = snap.get("oil", 75.0)
        regime = regime_info.get("market_regime", "Unknown")
        risk = regime_info.get("market_risk_level", "MEDIUM")
        signal = trading_info.get("trading_signal", "HOLD")

        # v1.3.0: 미사용 신호 추출
        crypto_basis_state = signals_data.get("crypto_basis_state", "") or ""
        pcr_state          = signals_data.get("pcr_state", "") or ""
        breadth_state      = signals_data.get("breadth_state", "") or ""

        # ETF 배분
        alloc_str = ", ".join(f"{e}:{w}%" for e, w in alloc.items()) if alloc else "없음"

        # 뉴스 Top3
        top_issues = news.get("top_issues", [])
        issues_str = ", ".join(
            iss.get("topic", "") for iss in top_issues[:3] if isinstance(iss, dict)
        ) if top_issues else "특이사항 없음"

        # 시장 심리 보조 지표 문자열 (값 있을 때만 포함)
        extra_signals = []
        if crypto_basis_state and crypto_basis_state not in ("Unknown", ""):
            extra_signals.append(f"BTC Basis: {crypto_basis_state}")
        if pcr_state and pcr_state not in ("Unknown", "—"):
            extra_signals.append(f"PCR: {pcr_state}")
        if breadth_state and breadth_state not in ("Unknown", "—", "No Data"):
            extra_signals.append(f"시장폭: {breadth_state}")
        extra_str = ", ".join(extra_signals) if extra_signals else ""

        prompt = (
            f"투자 분석 X 스레드를 JSON 배열로 생성해줘.\n"
            f"캐릭터: Max Bullhorn(강세), Baron Bearsworth(약세), The Volatician(혼돈)\n\n"
            f"데이터:\n"
            f"- SPY: {sp500:+.2f}%, VIX: {vix:.1f}, WTI: ${oil:.1f}\n"
            f"- 레짐: {regime}, 리스크: {risk}, 시그널: {signal}\n"
            f"- ETF 배분: {alloc_str}\n"
            f"- 주요 뉴스: {issues_str}\n"
            + (f"- 시장 심리 보조: {extra_str}\n" if extra_str else "")
            + f"\n구조 (5~7개):\n"
            f"1. 후킹 요약 (1줄 + 이모지)\n"
            f"2. 오늘 시장 무슨 일? (레짐/수치)\n"
            f"3. 왜 이런 일이? (뉴스 배경)\n"
            f"4. ETF 전략 (어디에 투자?)\n"
            f"5. 리스크 체크 (주의점)\n"
            f"6. 내일 전망\n"
            f"7. 마무리 + CTA\n\n"
            f"조건:\n"
            f"- 각 포스트 200자 이내, 한국어\n"
            f"- 이모지 포함\n"
            f"- JSON 배열로만 반환: [{{\"post\": \"...\"}}, ...]\n"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=1000,
            temperature=0.8,
            response_json=True,
        )

        if result.get("success") and result.get("data"):
            parsed = result["data"]
            # list[dict] → list[str]
            if isinstance(parsed, list):
                posts = []
                rejected_non_kr = 0
                for item in parsed:
                    if isinstance(item, dict):
                        post = item.get("post", "")
                    elif isinstance(item, str):
                        post = item
                    else:
                        continue
                    post = post.strip().strip('"').strip("'")
                    if not (10 < len(post) <= 280):
                        continue
                    # v1.2.0: 비한국어 문자 감지 시 해당 포스트만 제외
                    non_kr = _detect_non_publishable_chars(post)
                    if non_kr is not None:
                        logger.warning(
                            f"[XFormatter] 스레드 포스트 비한국어 감지: '{non_kr}' → 제외"
                        )
                        rejected_non_kr += 1
                        continue
                    posts.append(post)

                if len(posts) >= 3:
                    logger.info(
                        f"[XFormatter] AI 스레드 생성 ({len(posts)}개"
                        f"{f', non_kr 제외 {rejected_non_kr}개' if rejected_non_kr else ''}) | "
                        f"source=gemini"
                    )
                    return posts

                # 가드 통과 포스트가 3개 미만 → fallback
                if rejected_non_kr > 0:
                    logger.warning(
                        f"[XFormatter] AI 스레드 비한국어 제외 후 {len(posts)}개만 남음 "
                        f"(rejected={rejected_non_kr}) → fallback"
                    )

            logger.warning("[XFormatter] AI 스레드 파싱 실패 → fallback")

    except Exception as e:
        logger.warning(f"[XFormatter] AI 스레드 생성 실패 → fallback: {e}")

    return format_thread_posts(data)


# ── x_formatter.py 하단에 추가 (기존 함수 무변경) ──────────
from publishers.thread_builder import build_thread, build_single_tweet

# x_formatter.py 하단의 format_thread_auto 함수를 아래로 교체

def format_thread_auto(content: str, session: str, core_data: dict) -> list:
    """
    감성 트리거 스레드 생성.
    v2: 항상 build_thread — 후킹 + 본문 + CTA = 최소 3트윗
    """
    risk_level = (
        core_data.get("market_regime", {}).get("market_risk_level", "MEDIUM")
    ) or "MEDIUM"
    if risk_level not in ("HIGH", "MEDIUM", "LOW"):
        risk_level = "MEDIUM"
    return build_thread(
        content=content,
        session=session,
        risk_level=risk_level,
        add_hook=True,
        add_cta=True,
        add_counter=False,
    )
