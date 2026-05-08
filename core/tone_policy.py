"""
core/tone_policy.py
====================
AI 톤 정책 단일 진입점 (x_formatter v1.5.0~).

목적:
  - 리스크/레짐/세션을 입력받아 ToneSpec 결정
  - 톤별 (페르소나 + 작문규칙 + 금지표현 + 예시) 4요소 패키지 제공
  - retry 시 동일 ToneSpec 재주입 → 톤 손실 방지

Q4.c 단계: morning 세션에만 신규 톤 매트릭스 적용.
           다른 세션은 select_persona_tone()이 None 반환 → x_formatter는
           기존 v1.4.0 인라인 로직 사용.

확장 계획:
  - D-7 검증 후 intraday/close/full/narrative 셀 추가
  - alert_formatter / narrative_engine 등 다른 모듈도 동일 인터페이스로 흡수 가능

변경이력:
  v1.0.0 (2026-05-06) 신설. morning 3셀(HIGH/MEDIUM/LOW) 정의.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

VERSION = "1.0.0"

logger = logging.getLogger(__name__)
logger.info(f"[TonePolicy] v{VERSION} 로드")


# ─────────────────────────────────────────────────────────────
# ToneSpec — 불변 톤 명세
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToneSpec:
    """
    톤 명세 — 한 번 결정되면 retry 시에도 변경되지 않음 (frozen=True).

    프롬프트의 4요소 패키지:
      1. persona            : 누가 쓰는 글인가
      2. voice_rules        : 어떻게 쓰는가 (4-6개 규칙)
      3. forbidden          : 어떤 표현을 피하는가
      4. example_snippets   : 톤 감각을 잡기 위한 짧은 예시
    """
    persona: str
    tone_name: str
    voice_rules: tuple[str, ...]
    forbidden: tuple[str, ...]
    example_snippets: tuple[str, ...]
    emoji_hint: str
    length_target: tuple[int, int]              # (min, max)
    hashtag_count_target: tuple[int, int]       # (min, max)
    risk_level: str                             # "HIGH" | "MEDIUM" | "LOW"
    regime: str                                 # market_regime 원문
    session: str                                # "morning" | ...

    def to_meta(self) -> dict:
        """ai_quality_store 적재용 메타 dict."""
        return {
            "persona":    self.persona,
            "tone_name":  self.tone_name,
            "risk_level": self.risk_level,
            "regime":     self.regime,
            "session":    self.session,
        }


# ─────────────────────────────────────────────────────────────
# 톤 매트릭스 (Q4.c — morning 3셀만)
# ─────────────────────────────────────────────────────────────

_PERSONA_BASE = "시장 데이터 큐레이터"

_GLOBAL_FORBIDDEN: tuple[str, ...] = (
    "투자 권유", "매수 추천", "매도 추천",
    "수익 보장", "확실한 수익", "원금 보장",
    "급등 임박", "대박",
    "지금이 마지막 기회",
)

_TONE_MORNING_HIGH = ToneSpec(
    persona=f"{_PERSONA_BASE} — 경고 신호 우선",
    tone_name="긴급 경계",
    voice_rules=(
        "첫 줄에 위험 신호와 핵심 수치를 1줄로 압축",
        "단정적 표현보다 '신호가 분명합니다', '흐름이 뚜렷합니다' 같은 데이터 기반 표현",
        "명령형('팔아라', '사라') 절대 금지",
        "짧은 문장 위주, 끝맺음 깔끔하게",
        "수치는 구체적으로 (예: 'VIX 31', 'SPY -1.2%')",
    ),
    forbidden=_GLOBAL_FORBIDDEN + (
        "확실히", "지금 진입",
        "ㅋㅋ", "ㅎㅎ", ":)",
        "두렵다", "공포에 떨고 있다", "지옥",
    ),
    example_snippets=(
        "🚨 시장 경계 모드. VIX 급등·SPY -1%대, 방어 우위 신호 뚜렷합니다.",
        "⚠️ 위험 자산 회피 흐름 분명. 채권으로 자금 이동, 수치가 말해줍니다.",
    ),
    emoji_hint="위험 신호 이모지 1개(🚨/⚠️/🔴) + 시장 이모지 0~1개",
    length_target=(140, 200),
    hashtag_count_target=(3, 4),
    risk_level="HIGH",
    regime="",
    session="morning",
)

_TONE_MORNING_MEDIUM = ToneSpec(
    persona=f"{_PERSONA_BASE} — 균형 분석가",
    tone_name="차분 점검",
    voice_rules=(
        "어느 쪽으로도 단정하지 않는 균형 잡힌 시각",
        "'관망', '점검', '확인' 같은 신중한 동사 활용",
        "수치를 객관적으로 나열한 뒤 1줄 해석",
        "감정 표현 최소화",
        "결론보다 조건부 표현 ('~할 경우', '~라면')",
    ),
    forbidden=_GLOBAL_FORBIDDEN + (
        "확실히", "분명히", "당연히",
        "폭발", "쓰나미", "지진",
        "지금 사야", "지금이 기회",
    ),
    example_snippets=(
        "📊 시장 균형 구간. SPY 보합권·VIX 안정, 큰 베팅보다 포지션 점검에 적합한 아침.",
        "🔍 혼조세로 출발. 매수·매도 신호 모두 약한 구간이라 관망이 합리적으로 보입니다.",
    ),
    emoji_hint="차분한 이모지 1~2개(📊/🔍/📈/📉)",
    length_target=(140, 200),
    hashtag_count_target=(3, 4),
    risk_level="MEDIUM",
    regime="",
    session="morning",
)

_TONE_MORNING_LOW = ToneSpec(
    persona=f"{_PERSONA_BASE} — 우호적 환경 해설가",
    tone_name="밝은 시작",
    voice_rules=(
        "긍정적이되 들뜨지 않게, '안정', '양호', '우호적' 톤",
        "수치 → 환경 해석 순서",
        "미래 단정 금지 ('계속 오를 것', '강세 지속' 등)",
        "자제된 낙관, 과한 흥분 금지",
        "긍정 해석에도 '~로 보입니다', '~흐름' 같은 부드러운 어미",
    ),
    forbidden=_GLOBAL_FORBIDDEN + (
        "ㅋ", "ㅎ",
        "확실한 수익", "보장된",
        "강세 지속", "계속 오를", "계속 상승",
    ),
    example_snippets=(
        "🌅 가벼운 출발. SPY 강보합·VIX 안정, 위험 자산에 우호적인 환경입니다.",
        "📈 공포지수 낮고 유동성 양호. 성장주 중심 흐름에 무게가 실리는 아침이네요.",
    ),
    emoji_hint="긍정 이모지 1개(🌅/📈/🟢) + 시장 이모지 0~1개",
    length_target=(140, 200),
    hashtag_count_target=(3, 4),
    risk_level="LOW",
    regime="",
    session="morning",
)

# (risk_level, session) → ToneSpec
_TONE_MATRIX: dict[tuple[str, str], ToneSpec] = {
    ("HIGH",   "morning"): _TONE_MORNING_HIGH,
    ("MEDIUM", "morning"): _TONE_MORNING_MEDIUM,
    ("LOW",    "morning"): _TONE_MORNING_LOW,
}


# ─────────────────────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────────────────────

def select_persona_tone(
    risk_level: str,
    regime: str,
    session: str,
) -> Optional[ToneSpec]:
    """
    리스크/레짐/세션을 입력받아 ToneSpec 결정.

    Q4.c 단계: morning이 아니면 None 반환 → x_formatter는 v1.4.0 로직 사용.

    Args:
        risk_level: "HIGH" | "MEDIUM" | "LOW" (그 외 입력은 MEDIUM으로 안전 처리)
        regime:     market_regime 원문 (Risk-On/Risk-Off/Liquidity Crisis 등)
        session:    "morning" | "intraday" | "close" | "full" | "narrative"

    Returns:
        ToneSpec or None.
    """
    if session != "morning":
        # Q4.c: 다른 세션은 미적용 — D-7 이후 확장 예정
        return None

    risk_norm = risk_level if risk_level in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
    base_spec = _TONE_MATRIX.get((risk_norm, session))

    if base_spec is None:
        # 매트릭스 누락 — 안전 fallback (개발 시점에만 발생 가능)
        logger.warning(
            f"[TonePolicy] 매트릭스 누락 risk={risk_level} session={session} → MEDIUM/morning"
        )
        base_spec = _TONE_MORNING_MEDIUM

    # regime은 적재 메타로만 사용 (톤 자체는 risk×session 결정)
    return ToneSpec(
        persona=base_spec.persona,
        tone_name=base_spec.tone_name,
        voice_rules=base_spec.voice_rules,
        forbidden=base_spec.forbidden,
        example_snippets=base_spec.example_snippets,
        emoji_hint=base_spec.emoji_hint,
        length_target=base_spec.length_target,
        hashtag_count_target=base_spec.hashtag_count_target,
        risk_level=risk_norm,
        regime=regime or "Unknown",
        session=session,
    )


def _format_voice_rules(rules: tuple[str, ...]) -> str:
    return "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rules))


def _format_examples(snippets: tuple[str, ...]) -> str:
    return "\n".join(f'  - "{s}"' for s in snippets)


def build_tweet_prompt(
    data: dict,
    spec: ToneSpec,
    session_label: str,
    sample_hashtags: str = "",
) -> str:
    """
    1차 트윗 프롬프트 빌더.

    v1.4.0 generate_ai_tweet의 인라인 프롬프트를 ToneSpec 기반 4요소 구조로 재작성.
    """
    snap         = data.get("market_snapshot", {}) or {}
    regime_info  = data.get("market_regime", {}) or {}
    trading_info = data.get("trading_signal", {}) or {}
    fg           = data.get("fear_greed", {}) or {}
    signals_data = data.get("signals", {}) or {}

    sp500  = snap.get("sp500")
    vix    = snap.get("vix")
    oil    = snap.get("oil")
    us10y  = snap.get("us10y")
    regime_name = regime_info.get("market_regime", "Unknown")
    risk        = regime_info.get("market_risk_level", "MEDIUM")
    signal      = trading_info.get("trading_signal", "HOLD")
    fg_val      = fg.get("value", 50) if fg else 50
    fg_label    = fg.get("label", "") if fg else ""

    # ETF Top3
    alloc_field = data.get("etf_allocation", {}) or {}
    alloc = alloc_field.get("allocation", alloc_field) if isinstance(alloc_field, dict) else {}
    top3 = []
    if alloc and isinstance(alloc, dict):
        sorted_etfs = sorted(alloc.items(), key=lambda x: x[1], reverse=True)
        top3 = [f"{e}({w}%)" for e, w in sorted_etfs[:3]]

    crypto_basis_state = signals_data.get("crypto_basis_state", "") or ""
    pcr_state          = signals_data.get("pcr_state", "") or ""

    # 데이터 라인 — None/Unknown 가드
    data_lines: list[str] = [f"- 세션: {session_label}"]
    if all(v is not None for v in (sp500, vix, oil, us10y)):
        data_lines.append(
            f"- SPY: {sp500:+.2f}%, VIX: {vix:.1f}, WTI: ${oil:.1f}, US10Y: {us10y:.2f}%"
        )
    data_lines.append(f"- 레짐: {regime_name}, 리스크: {risk}, 시그널: {signal}")
    data_lines.append(f"- F&G: {fg_val} ({fg_label})")
    if top3:
        data_lines.append(f"- Top ETF: {', '.join(top3)}")
    if crypto_basis_state and crypto_basis_state not in ("Unknown", ""):
        data_lines.append(f"- BTC Basis: {crypto_basis_state}")
    if pcr_state and pcr_state not in ("Unknown", "—"):
        data_lines.append(f"- PCR: {pcr_state}")

    data_block = "\n".join(data_lines)
    forbidden_block = ", ".join(spec.forbidden)
    sample_tag_line = (
        f"  - 해시태그 예시 (참고만, 매번 다르게): {sample_hashtags}"
        if sample_hashtags else ""
    )

    prompt = f"""당신은 [{spec.persona}]입니다.
오늘 톤: [{spec.tone_name}]

[시장 데이터]
{data_block}

[작문 규칙]
{_format_voice_rules(spec.voice_rules)}

[피해야 할 표현]
{forbidden_block}

[톤 감각 예시 — 그대로 베끼지 말고 분위기만 참고]
{_format_examples(spec.example_snippets)}

[작성 조건]
  - 길이: {spec.length_target[0]}~{spec.length_target[1]}자 (반드시)
  - 한국어, 영어 약어/숫자/이모지만 사용 (힌디·아랍·일본어 가나 등 절대 금지)
  - 이모지: {spec.emoji_hint}
  - 해시태그: {spec.hashtag_count_target[0]}~{spec.hashtag_count_target[1]}개, 본문 끝 1줄
{sample_tag_line}
  - 트윗 본문만 출력. 톤 라벨, 메타 표현(조건/출력/본문/결론), 마크다운 금지.
"""
    return prompt


def build_thread_prompt(
    data: dict,
    spec: ToneSpec,
) -> str:
    """
    스레드 5~7개 포스트 프롬프트 빌더.
    구조: 후킹 → 시장 현황 → 원인 → ETF 전략 → 리스크 → 전망 → 마무리.
    """
    # 트윗과 동일한 데이터 라인 구성
    snap         = data.get("market_snapshot", {}) or {}
    regime_info  = data.get("market_regime", {}) or {}
    trading_info = data.get("trading_signal", {}) or {}
    signals_data = data.get("signals", {}) or {}

    sp500  = snap.get("sp500")
    vix    = snap.get("vix")
    oil    = snap.get("oil")
    us10y  = snap.get("us10y")
    regime_name = regime_info.get("market_regime", "Unknown")
    risk        = regime_info.get("market_risk_level", "MEDIUM")
    signal      = trading_info.get("trading_signal", "HOLD")

    breadth_state      = signals_data.get("breadth_state", "") or ""
    crypto_basis_state = signals_data.get("crypto_basis_state", "") or ""
    pcr_state          = signals_data.get("pcr_state", "") or ""

    data_lines: list[str] = []
    if all(v is not None for v in (sp500, vix, oil, us10y)):
        data_lines.append(
            f"- SPY: {sp500:+.2f}%, VIX: {vix:.1f}, WTI: ${oil:.1f}, US10Y: {us10y:.2f}%"
        )
    data_lines.append(f"- 레짐: {regime_name}, 리스크: {risk}, 시그널: {signal}")
    if breadth_state:
        data_lines.append(f"- Breadth: {breadth_state}")
    if crypto_basis_state and crypto_basis_state not in ("Unknown", ""):
        data_lines.append(f"- BTC Basis: {crypto_basis_state}")
    if pcr_state and pcr_state not in ("Unknown", "—"):
        data_lines.append(f"- PCR: {pcr_state}")

    data_block = "\n".join(data_lines)
    forbidden_block = ", ".join(spec.forbidden)

    prompt = f"""당신은 [{spec.persona}]입니다.
오늘 톤: [{spec.tone_name}]

[시장 데이터]
{data_block}

[작문 규칙]
{_format_voice_rules(spec.voice_rules)}

[피해야 할 표현]
{forbidden_block}

[작성 요구]
  - 5~7개 포스트로 구성된 X 스레드
  - 각 포스트 200자 이내, 한국어
  - 이모지: {spec.emoji_hint}
  - 구조:
    1. 후킹 요약 (1줄 + 이모지)
    2. 오늘 시장 무슨 일? (레짐/수치)
    3. 왜 이런 일이? (뉴스 배경)
    4. ETF 전략 (어디에 투자?)
    5. 리스크 체크 (주의점)
    6. 내일 전망
    7. 마무리

[출력 형식]
JSON 배열로만 반환:
  [{{"post": "..."}}, ...]
설명·마크다운 없이 JSON만 출력.
"""
    return prompt


def build_retry_prompt(
    original_output: str,
    failure_reason: str,
    spec: ToneSpec,
) -> str:
    """
    Retry 프롬프트 빌더 — 톤·페르소나·작문규칙을 반드시 재주입.

    failure_reason 분기:
      - "length_too_long"   → 줄이기
      - "length_too_short"  → 늘리기
      - "non_korean"        → 한국어로 다시
      - "hashtag_count"     → 해시태그 개수 조정
      - "emoji_count"       → 이모지 개수 조정
      - "forbidden_phrase"  → 금지 표현 제거
      - "prompt_leak"       → 메타 라벨 제거
      - "awkward"           → 자연스럽게 다듬기
      - 그 외               → 톤 유지하며 다시 작성
    """
    forbidden_block = ", ".join(spec.forbidden)
    voice_rules     = _format_voice_rules(spec.voice_rules)

    # 사유별 추가 지시
    reason_instructions = {
        "length_too_long": (
            f"이전 출력이 {spec.length_target[1]}자를 초과합니다. "
            f"{spec.length_target[0]}~{spec.length_target[1]}자 이내로 줄이되, 핵심 수치와 해시태그는 유지."
        ),
        "length_too_short": (
            f"이전 출력이 {spec.length_target[0]}자에 못 미칩니다. "
            f"{spec.length_target[0]}~{spec.length_target[1]}자 이내로 보완."
        ),
        "non_korean": (
            "이전 출력에 한국어가 아닌 외국어 단어(힌디/아랍/일본어 가나/태국/러시아 등)가 포함됐습니다. "
            "반드시 한국어, 영어 약어, 숫자, 이모지만 사용해 다시 작성."
        ),
        "hashtag_count": (
            f"해시태그 개수가 부적절합니다. 본문 끝 1줄에 "
            f"{spec.hashtag_count_target[0]}~{spec.hashtag_count_target[1]}개로 조정."
        ),
        "emoji_count": (
            f"이모지 개수가 부적절합니다. {spec.emoji_hint}에 맞게 조정."
        ),
        "forbidden_phrase": (
            "이전 출력에 피해야 할 표현이 포함됐습니다. 아래 [피해야 할 표현]을 다시 확인하고 제거."
        ),
        "prompt_leak": (
            "이전 출력에 톤 라벨·메타 표현(조건/출력/본문/결론 등)이 본문에 노출됐습니다. "
            "트윗 본문 외 어떤 메타 텍스트도 출력하지 마세요."
        ),
        "awkward": (
            "이전 출력이 어색합니다 — 동일 어절 반복, 미완결 문장, 또는 톤-내용 불일치. "
            "톤을 유지하면서 자연스럽게 다시 작성."
        ),
    }
    extra = reason_instructions.get(
        failure_reason,
        "톤을 유지하며 자연스럽게 다시 작성.",
    )

    prompt = f"""당신은 [{spec.persona}]입니다.
오늘 톤: [{spec.tone_name}] — 이 톤을 절대 바꾸지 마세요.

[재작성 사유]
{extra}

[작문 규칙 — 반드시 지킬 것]
{voice_rules}

[피해야 할 표현]
{forbidden_block}

[이전 출력 — 사유에 맞게 수정해 다시 작성]
{original_output}

[작성 조건]
  - 길이: {spec.length_target[0]}~{spec.length_target[1]}자
  - 한국어/영어/숫자/이모지만
  - 해시태그 {spec.hashtag_count_target[0]}~{spec.hashtag_count_target[1]}개
  - 트윗 본문만 출력. 설명·마크다운·메타 라벨 금지.
"""
    return prompt
