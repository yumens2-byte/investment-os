"""
engines/narrative_engine.py (B-15)
====================================
Gemini 기반 시장 내러티브 자동 생성

19개 시그널 + Regime + Market Score → Gemini Flash-Lite로
한국어 시장 해설 자동 생성.

발행 채널: X(스레드) + TG 무료/유료
발행 시점: 11:36 KST (UTC 02:36)
Fallback:  Gemini 실패 시 기존 rule-based summary 유지

변경이력:
  v1.1.0  기존 — 고정 헤더/고정 해시태그/고정 temperature.
  v2.0.0 (2026-09-06) N-2 텍스트 변칙화.
    - 헤더 풀 10종(무헤더 3종 포함) 랜덤
    - 해시태그를 x_formatter.build_hashtags(session="narrative")로 위임
    - tone_policy narrative 3셀 연동 (ToneSpec 기반 프롬프트)
    - temperature 0.7 고정 → 0.62~0.92 랜덤
    - 문단 스타일 3종(prose/bullet/lead) × 줄수 3~6 랜덤
    - build_narrative_posts() 신설 — 스레드 분할 담당
    ※ format_narrative_tweet()은 하위호환용으로 존치(신규 경로 미사용)
"""
from __future__ import annotations

import logging
import random

from config.settings import X_PREMIUM_TWEET_LENGTH

VERSION = "2.0.0"

logger = logging.getLogger(__name__)

# 시그널 한국어 라벨
_SIGNAL_LABEL = {
    "volatility_score": "VIX", "rate_score": "금리",
    "commodity_pressure_score": "유가", "financial_stability_score": "금융안정",
    "sentiment_score": "시장심리", "fear_greed_score": "공포탐욕",
    "crypto_risk_score": "BTC", "equity_momentum_score": "주가모멘텀",
    "xlf_gld_score": "금융/금", "breadth_score": "시장참여도",
    "vol_term_score": "변동성구조", "claims_score": "실업수당",
    "infl_exp_score": "기대인플레", "em_stress_score": "신흥국",
    "ai_momentum_score": "AI모멘텀", "nasdaq_rel_score": "나스닥상대",
    "banking_stress_score": "은행스트레스",
}

# ── v1.1.0 호환용 정적 시스템 지침 (ToneSpec 미확보 시 사용) ──
SYSTEM_INSTRUCTION = """당신은 미국 금융시장 전문 애널리스트입니다.
규칙:
1. 투자 권유 금지 — 정보 제공만
2. 팩트 기반 분석만 작성
3. 한국어로 작성
4. 전문 용어 사용 가능 (일반 투자자 대상)
5. 시장 레짐 전환 근거를 명확히 설명"""


# ══════════════════════════════════════════════════════════════
# N-2: 변칙화 풀
# ══════════════════════════════════════════════════════════════

# 헤더 풀 — 빈 문자열은 '헤더 없이 본문부터 시작'을 의미한다.
# 고정 문자열 1종("📝 AI 시장 해설")이 매일 반복되던 문제(F-1) 해소용.
_HEADER_POOL: tuple[str, ...] = (
    "",
    "",
    "",
    "📝 오늘의 시장 해설",
    "🧭 시장 내러티브",
    "📊 마켓 코멘트",
    "오늘 시장, 이렇게 읽었습니다",
    "🔍 장 마감 이후 정리",
    "시장 기록 — 무슨 일이 있었나",
    "📌 데이터로 본 오늘의 흐름",
)

# 문단 스타일 — 프롬프트 말미 조건절만 교체하므로 호출 비용 변화 없음.
_STYLE_POOL: tuple[str, ...] = ("prose", "bullet", "lead")

_STYLE_INSTRUCTION: dict[str, str] = {
    "prose": (
        "형식: 줄바꿈으로 구분된 평서형 문단. 불릿 기호나 번호를 쓰지 마세요."
    ),
    "bullet": (
        "형식: 각 줄을 '・'로 시작하는 짧은 항목으로 작성하세요. "
        "숫자 번호나 마크다운 기호(-, *, #)는 쓰지 마세요."
    ),
    "lead": (
        "형식: 첫 줄은 오늘 시장을 한 문장으로 요약한 후킹 문장으로 쓰고, "
        "빈 줄 하나를 둔 뒤 나머지를 평서형 문단으로 이어가세요."
    ),
}

# 줄 수 타깃 — 4·5줄에 가중치를 둬 자연스러운 분포 유지
_LINE_TARGET_POOL: tuple[int, ...] = (3, 4, 4, 5, 5, 6)

# temperature 랜덤 범위 (v1.1.0은 0.7 고정)
_TEMPERATURE_RANGE: tuple[float, float] = (0.62, 0.92)

# 단일 트윗 유지 상한(자). 초과 시 2트윗 스레드로 분할한다.
_SINGLE_POST_MAX = 380
# 스레드 1번 포스트 목표 상한(자)
_LEAD_POST_MAX = 300

_MAX_TOKENS = 768


# ══════════════════════════════════════════════════════════════
# 프롬프트 빌더
# ══════════════════════════════════════════════════════════════

def _build_data_block(data: dict) -> str:
    """core_data에서 시장 데이터 블록 생성 (v1.1.0 _build_prompt 본문 유지)."""
    snapshot = data.get("market_snapshot", {})
    regime_info = data.get("market_regime", {})
    ms = data.get("market_score", {})
    signals = data.get("signals", {})
    ts = data.get("trading_signal", {})
    alloc = data.get("etf_allocation", {}).get("allocation", {})

    # 극단값 시그널 추출 (|v-2.5| 기준 Top 5)
    score_keys = [
        k for k in signals
        if k.endswith("_score") and isinstance(signals.get(k), (int, float))
    ]
    extremes = sorted(score_keys, key=lambda k: abs(signals[k] - 2.5), reverse=True)[:5]
    extreme_lines = []
    for k in extremes:
        label = _SIGNAL_LABEL.get(k, k)
        state_map = {
            "volatility_score": "vix_state", "rate_score": "rate_environment",
            "commodity_pressure_score": "oil_state",
            "financial_stability_score": "credit_stress_signal",
            "sentiment_score": "sentiment_state",
        }
        sk = state_map.get(k, k.replace("_score", "_state"))
        state = signals.get(sk, "")
        extreme_lines.append(f"  - {label}: {signals[k]} ({state})")

    block = f"""[시장 스냅샷]
  SPY: {snapshot.get('sp500', 0):+.2f}% | VIX: {snapshot.get('vix', 0)} | US10Y: {snapshot.get('us10y', 0)}%
  WTI: ${snapshot.get('oil', 0)} | DXY: {snapshot.get('dollar_index', 0)}

[시장 레짐]
  레짐: {regime_info.get('market_regime', '?')}
  Risk Level: {regime_info.get('market_risk_level', '?')}
  근거: {regime_info.get('regime_reason', '?')}

[Market Score (1~5, 높을수록 위험)]
  Growth: {ms.get('growth_score', '?')} | Inflation: {ms.get('inflation_score', '?')}
  Liquidity: {ms.get('liquidity_score', '?')} | Risk: {ms.get('risk_score', '?')}
  Stability: {ms.get('financial_stability_score', '?')} | Commodity: {ms.get('commodity_pressure_score', '?')}

[주요 시그널 (극단값 Top 5)]
{chr(10).join(extreme_lines)}

[트레이딩 시그널]
  Signal: {ts.get('trading_signal', '?')}
  BUY Watch: {', '.join(ts.get('signal_matrix', {}).get('buy_watch', []))}
  Reduce: {', '.join(ts.get('signal_matrix', {}).get('reduce', []))}

[ETF 배분]
  {' | '.join(f'{e} {v}%' for e, v in sorted(alloc.items(), key=lambda x: -x[1]))}"""

    # B-16: Gemini 뉴스 분석 결과가 있으면 블록에 추가
    news_analysis = data.get("news_analysis", {})
    top_issues = news_analysis.get("top_issues", [])
    if top_issues:
        issues_text = "\n".join(
            f"  {i + 1}. {iss.get('topic', '?')} ({iss.get('impact', '?')}, "
            f"confidence={iss.get('confidence', 0):.1f}) — {iss.get('summary', '')}"
            for i, iss in enumerate(top_issues)
        )
        key_risk = news_analysis.get("key_risk", "")
        block += f"""

[뉴스 심층 분석 (AI)]
{issues_text}
  핵심 리스크: {key_risk}"""

    return block


def _build_prompt(data: dict) -> str:
    """v1.1.0 호환 진입점 — ToneSpec 없이 데이터 블록만으로 프롬프트 구성."""
    return (
        "다음 미국 금융시장 데이터를 분석하여 한국어 시장 해설을 작성하세요.\n\n"
        + _build_data_block(data)
        + "\n\n위 데이터를 종합하여 한국어 3~5줄 시장 해설을 작성하세요."
        "\n조건: 투자 권유 금지, 팩트 기반, 레짐 전환 근거 명확히."
    )


def _build_prompt_v2(data: dict, spec, style: str, line_target: int) -> str:
    """
    N-2 프롬프트 빌더 — 데이터 블록 + ToneSpec 4요소 + 스타일/줄수 조건.

    Args:
        data:        core_data.json의 data 필드
        spec:        core.tone_policy.ToneSpec (None 허용)
        style:       _STYLE_POOL 중 하나
        line_target: 목표 줄 수
    """
    data_block = _build_data_block(data)
    style_line = _STYLE_INSTRUCTION.get(style, _STYLE_INSTRUCTION["prose"])

    if spec is None:
        return (
            "다음 미국 금융시장 데이터를 분석하여 한국어 시장 해설을 작성하세요.\n\n"
            f"{data_block}\n\n"
            f"[작성 조건]\n"
            f"  - 한국어 {line_target}줄 내외\n"
            f"  - {style_line}\n"
            f"  - 투자 권유 금지, 팩트 기반, 레짐 전환 근거 명확히\n"
            f"  - 해시태그와 제목은 쓰지 마세요. 본문만 출력하세요."
        )

    voice_rules = "\n".join(f"  {i + 1}. {r}" for i, r in enumerate(spec.voice_rules))
    examples = "\n".join(f'  - "{s}"' for s in spec.example_snippets)
    forbidden = ", ".join(spec.forbidden)

    return f"""당신은 [{spec.persona}]입니다.
오늘 톤: [{spec.tone_name}]

{data_block}

[작문 규칙]
{voice_rules}

[피해야 할 표현]
{forbidden}

[톤 감각 예시 — 그대로 베끼지 말고 분위기만 참고]
{examples}

[작성 조건]
  - 한국어 {line_target}줄 내외, 전체 {spec.length_target[0]}~{spec.length_target[1]}자
  - {style_line}
  - 이모지: {spec.emoji_hint}
  - 투자 권유 금지, 팩트 기반, 레짐 전환 근거를 수치와 함께 최소 1개 명시
  - 해시태그와 제목은 쓰지 마세요. 본문만 출력하세요.
  - 톤 라벨, 메타 표현(조건/출력/본문/결론), 마크다운 금지"""


def _build_system_instruction(spec) -> str:
    """ToneSpec이 있으면 페르소나 기반 지침, 없으면 v1.1.0 정적 지침."""
    if spec is None:
        return SYSTEM_INSTRUCTION
    return (
        f"당신은 {spec.persona}입니다. 오늘의 톤은 '{spec.tone_name}'입니다.\n"
        "규칙:\n"
        "1. 투자 권유 금지 — 정보 제공만\n"
        "2. 팩트 기반 분석만 작성\n"
        "3. 한국어로 작성\n"
        "4. 시장 레짐 전환 근거를 명확히 설명\n"
        "5. 제목·해시태그·마크다운 없이 본문만 출력"
    )


# ══════════════════════════════════════════════════════════════
# 내러티브 생성
# ══════════════════════════════════════════════════════════════

def generate_narrative(data: dict) -> dict:
    """
    Gemini로 시장 내러티브 생성.

    Args:
        data: core_data.json의 data 필드

    Returns:
        {
          "success": True/False,
          "narrative": "한국어 시장 해설",
          "source": "gemini" | "fallback",
          "variant_meta": {
              "style": "prose|bullet|lead",
              "line_target": int,
              "temperature": float,
              "tone_name": str | None,
              "risk_level": str | None,
          },
        }
    """
    from core.gemini_gateway import call, is_available

    logger.info(f"[Narrative] v{VERSION} 시작")

    style = random.choice(_STYLE_POOL)
    line_target = random.choice(_LINE_TARGET_POOL)
    temperature = round(random.uniform(*_TEMPERATURE_RANGE), 3)

    spec = _select_spec(data)
    variant_meta = {
        "style": style,
        "line_target": line_target,
        "temperature": temperature,
        "tone_name": getattr(spec, "tone_name", None),
        "risk_level": getattr(spec, "risk_level", None),
    }
    logger.info(
        f"[Narrative] variant: style={style} lines={line_target} "
        f"temp={temperature} tone={variant_meta['tone_name']}"
    )

    # Gemini 미설정 시 fallback
    if not is_available():
        logger.info("[Narrative] Gemini 미설정 → fallback")
        return _fallback_narrative(data, variant_meta)

    result = call(
        prompt=_build_prompt_v2(data, spec, style, line_target),
        model="flash-lite",
        system_instruction=_build_system_instruction(spec),
        max_tokens=_MAX_TOKENS,
        temperature=temperature,
        response_json=False,
        fallback_value=None,
    )

    if result["success"] and result["text"]:
        narrative = _trim_lines(result["text"].strip(), line_target)
        logger.info(f"[Narrative] Gemini 생성 완료 ({len(narrative)}자)")
        return {
            "success": True,
            "narrative": narrative,
            "source": "gemini",
            "variant_meta": variant_meta,
        }

    # Gemini 실패 → fallback
    logger.warning(
        f"[Narrative] Gemini 실패 → fallback: {str(result.get('error', '?'))[:80]}"
    )
    return _fallback_narrative(data, variant_meta)


def _select_spec(data: dict):
    """tone_policy에서 narrative ToneSpec 조회. 실패 시 None(정적 지침 사용)."""
    try:
        from core.tone_policy import select_persona_tone
        regime_info = data.get("market_regime", {}) or {}
        return select_persona_tone(
            regime_info.get("market_risk_level", "MEDIUM"),
            regime_info.get("market_regime", "Unknown"),
            "narrative",
        )
    except Exception as e:
        logger.warning(f"[Narrative] ToneSpec 조회 실패 → 정적 지침 사용: {e}")
        return None


def _trim_lines(narrative: str, line_target: int) -> str:
    """목표 줄 수 대비 과도하게 긴 출력만 절단 (여유 +2줄)."""
    lines = [ln for ln in narrative.split("\n")]
    limit = line_target + 2
    non_empty = [ln for ln in lines if ln.strip()]
    if len(non_empty) <= limit:
        return narrative

    kept: list[str] = []
    count = 0
    for ln in lines:
        if ln.strip():
            if count >= limit:
                break
            count += 1
        kept.append(ln)
    return "\n".join(kept).strip()


def _fallback_narrative(data: dict, variant_meta: dict | None = None) -> dict:
    """Gemini 실패 시 기존 rule-based summary 사용."""
    summary = data.get("output_helpers", {}).get("one_line_summary", "")
    regime = data.get("market_regime", {}).get("market_regime", "")
    risk = data.get("market_regime", {}).get("market_risk_level", "")
    signal = data.get("trading_signal", {}).get("trading_signal", "")

    narrative = f"{regime} 국면 | Risk {risk} | Signal {signal}\n{summary}"

    return {
        "success": True,
        "narrative": narrative,
        "source": "fallback",
        "variant_meta": variant_meta or {},
    }


# ══════════════════════════════════════════════════════════════
# N-1: X 발행 포스트 구성
# ══════════════════════════════════════════════════════════════

def _build_hashtags(data: dict) -> str:
    """narrative 세션용 랜덤 해시태그. 실패 시 최소 태그로 폴백."""
    regime = data.get("market_regime", {}).get("market_regime", "")
    try:
        from publishers.x_formatter import build_hashtags
        return build_hashtags(regime=regime, session="narrative")
    except Exception as e:
        logger.warning(f"[Narrative] 해시태그 생성 실패 → 기본값: {e}")
        return "#미국증시 #ETF투자"


def _compose(header: str, body: str, tags: str) -> str:
    """헤더/본문/태그 조립. 헤더가 빈 문자열이면 본문부터 시작."""
    parts = []
    if header:
        parts.append(header)
    parts.append(body.strip())
    if tags:
        parts.append(tags)
    return "\n\n".join(parts)


def _split_body(body: str) -> tuple[str, str]:
    """
    본문을 (리드, 나머지)로 분할.
    줄 단위로 누적하다 _LEAD_POST_MAX를 넘기기 직전에서 끊는다.
    분할 지점을 찾지 못하면 ("", body)를 반환한다.
    """
    lines = [ln for ln in body.split("\n") if ln.strip()]
    if len(lines) < 2:
        return "", body

    lead: list[str] = []
    rest: list[str] = []
    total = 0
    for ln in lines:
        if not rest and (total + len(ln) <= _LEAD_POST_MAX or not lead):
            lead.append(ln)
            total += len(ln)
        else:
            rest.append(ln)

    if not rest:
        return "", body
    return "\n".join(lead), "\n".join(rest)


def build_narrative_posts(data: dict, narrative_text: str) -> list[str]:
    """
    내러티브를 X 발행용 포스트 리스트로 변환.

    - 본문 <= _SINGLE_POST_MAX  → 1트윗
    - 본문 >  _SINGLE_POST_MAX  → 2트윗 스레드 (리드 + 나머지)

    run_view.py Step 3에서 호출되며, 반환된 리스트는 Step 6의
    기존 '이미지 + 스레드' 경로가 그대로 처리한다.

    Returns:
        list[str] — 비어 있으면 발행 스킵
    """
    if not narrative_text or not narrative_text.strip():
        logger.warning("[Narrative] 본문 비어있음 — 포스트 생성 스킵")
        return []

    body = narrative_text.strip()
    header = random.choice(_HEADER_POOL)
    tags = _build_hashtags(data)

    if len(body) <= _SINGLE_POST_MAX:
        posts = [_compose(header, body, tags)]
    else:
        lead, rest = _split_body(body)
        if not lead:
            posts = [_compose(header, body, tags)]
        else:
            posts = [_compose(header, lead, tags), rest.strip()]

    posts = [_cap_length(p) for p in posts if p.strip()]
    logger.info(
        f"[Narrative] 포스트 구성 완료: {len(posts)}트윗 "
        f"(header={'있음' if header else '없음'}, 본문 {len(body)}자)"
    )
    return posts


def _cap_length(text: str) -> str:
    """X Premium 한도 초과 시에만 문장 경계에서 절단."""
    if len(text) <= X_PREMIUM_TWEET_LENGTH:
        return text
    logger.warning(
        f"[Narrative] 포스트 길이 {len(text)}자가 "
        f"Premium 한도({X_PREMIUM_TWEET_LENGTH})를 초과 — 절단"
    )
    truncated = text[:X_PREMIUM_TWEET_LENGTH]
    last_period = max(
        truncated.rfind("."), truncated.rfind("?"),
        truncated.rfind("!"), truncated.rfind("。"),
    )
    if last_period > X_PREMIUM_TWEET_LENGTH * 0.7:
        truncated = truncated[:last_period + 1]
    return truncated


# ══════════════════════════════════════════════════════════════
# 포맷터 (하위호환 / 텔레그램)
# ══════════════════════════════════════════════════════════════

def format_narrative_tweet(narrative: str) -> str:
    """
    [DEPRECATED — v2.0.0부터 X 발행 경로에서 사용하지 않음]

    v1.1.0 호환용. 고정 헤더/고정 해시태그를 사용하므로 신규 코드에서는
    build_narrative_posts()를 사용할 것.
    """
    tweet = f"📝 AI 시장 해설\n\n{narrative}\n\n#ETF #투자 #미국증시 #AI분석"
    if len(tweet) <= X_PREMIUM_TWEET_LENGTH:
        return tweet

    logger.warning(
        f"[Narrative] 트윗 길이 {len(tweet)}자가 "
        f"Premium 한도({X_PREMIUM_TWEET_LENGTH})를 초과 — 절단"
    )
    suffix = "\n\n#ETF #투자 #미국증시 #AI분석"
    header = "📝 AI 시장 해설\n\n"
    max_narr = X_PREMIUM_TWEET_LENGTH - len(header) - len(suffix)
    truncated = narrative[:max_narr]
    last_period = max(
        truncated.rfind("."), truncated.rfind("?"),
        truncated.rfind("!"), truncated.rfind("。"),
    )
    if last_period > max_narr * 0.7:
        truncated = truncated[:last_period + 1]
    return f"{header}{truncated}{suffix}"


# TG 헤더 풀 — X와 동일한 고정 문구 반복을 피한다.
_TG_HEADER_POOL: tuple[str, ...] = (
    "📝 <b>오늘의 시장 해설</b>",
    "🧭 <b>시장 내러티브</b>",
    "📊 <b>마켓 코멘트</b>",
    "🔍 <b>장 마감 이후 정리</b>",
)


def format_narrative_telegram(narrative: str, data: dict) -> str:
    """내러티브를 TG 포맷으로 확장."""
    regime = data.get("market_regime", {}).get("market_regime", "")
    risk = data.get("market_regime", {}).get("market_risk_level", "")
    signal = data.get("trading_signal", {}).get("trading_signal", "")
    snapshot = data.get("market_snapshot", {})

    header = random.choice(_TG_HEADER_POOL)

    return (
        f"{header}\n\n"
        f"📊 SPY {snapshot.get('sp500', 0):+.1f}% | VIX {snapshot.get('vix', 0)} | "
        f"WTI ${snapshot.get('oil', 0):.0f}\n"
        f"🔄 {regime} | {risk} | {signal}\n\n"
        f"─────────────────\n"
        f"{narrative}\n"
        f"─────────────────\n\n"
        f"<i>Powered by Investment OS + Gemini AI</i>"
    )
