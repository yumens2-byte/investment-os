"""
publishers/thread_builder.py (v2.0.0)
======================================
X 스레드 자동 분할 + 감정 트리거 CTA + 후킹 문구 고도화.

v2.0.0 (2026-04-13)
  - 감정 트리거 4종 시스템 신설 (공포/욕망/비교/분노)
  - CTA 20종 (감정별 5종) — 레짐 × 감정 연동 선택
  - 후킹 문구 3차원 매핑 (세션 × 레짐 → 감정 → 풀)
  - Alert 전용 감정 suffix 추가
  - 배치 확장 방식 채택: 풀 직접 추가로 점진 확장 (AI 매 호출 X)
  - X 안티봇: 감정 조합 랜덤화로 동일 패턴 반복 방지

설계 원칙:
  - 디스클레이머(투자 권유 아님) 전 CTA 포함 유지 (규정 준수)
  - 허위 사실 없음 (수익 보장 · 특정 종목 추천 금지)
  - 감정 자극 = "정보 손실 공포" + "관찰자 욕망" + "정보 비대칭 분노" 기반

문구 확장 방법 (배치 방식):
  - 월 1회 Gemini로 새 문구 10종 생성 후 마스터 검수
  - 검수 통과한 문구를 _CTA_FOMO / _CTA_DESIRE 등 해당 풀에 추가
  - AI 매 호출 없음 → 안정성 유지 + 점진적 다양성 확보
"""
import random
import logging
from typing import List, Dict, Optional

VERSION = "2.0.0"
logger = logging.getLogger(__name__)

# ── 글자 수 상한 ────────────────────────────────────────────
_TWEET_LIMIT      = 25000   # X Premium 상한
_TWEET_SOFT_LIMIT = 4500    # 실용 분할 기준 (가독성)
_ALERT_TWEET_MAX  = 500     # Alert 단일 트윗 상한


# ═══════════════════════════════════════════════════════════
# 감정 트리거 시스템
# ═══════════════════════════════════════════════════════════

# 레짐별 감정 가중치 풀
# HIGH   → 공포(FOMO) 우선: 리스크 구간에서 손실 회피 본능 자극
# MEDIUM → 비교(COMPARE) + 욕망(DESIRE): 정보 격차 인식
# LOW    → 욕망(DESIRE) 우선: 기회 포착 기대감
_REGIME_EMOTION_WEIGHT: Dict[str, List[str]] = {
    "HIGH":   ["FOMO", "FOMO", "FOMO", "ANGER",   "COMPARE"],
    "MEDIUM": ["COMPARE", "COMPARE", "DESIRE",  "FOMO",   "ANGER"],
    "LOW":    ["DESIRE",  "DESIRE",  "COMPARE", "FOMO",   "DESIRE"],
}


def _pick_emotion(risk_level: str) -> str:
    """레짐 기반 감정 트리거 선택."""
    pool = _REGIME_EMOTION_WEIGHT.get(risk_level, _REGIME_EMOTION_WEIGHT["MEDIUM"])
    return random.choice(pool)


# ═══════════════════════════════════════════════════════════
# CTA 풀 — 감정별 5종 × 4감정 = 20종
# ═══════════════════════════════════════════════════════════
# 설계 기준:
#   1. 명령형 어법  : "받으려면" X → "지금 팔로우" O
#   2. 손실 프레임  : "안 보면 늦습니다" (FOMO)
#   3. 사회적 증거  : "이미 본 사람들은 움직였습니다" (COMPARE)
#   4. 구체적 혜택  : "매일/자동/무료" 명시 (DESIRE)
#   5. 디스클레이머 : 모든 CTA 포함 필수 (규정)
#
# 확장 시: 아래 리스트에 항목 추가 (배치 방식)

# ── FOMO (공포 — 놓칠까봐) ────────────────────────────────
_CTA_FOMO: List[str] = [
    "📛 이 분석, 내일 아침엔 늦습니다\n지금 팔로우하면 오늘 밤 먼저 받습니다\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "⏰ 미장 열리기 전 이미 포지션 잡은 사람들이 있습니다\n매일 아침 자동으로 받으려면 지금 팔로우\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "🔕 이 채널 모르고 투자하면 정보 격차 그대로입니다\n팔로우 → 알림 설정 → 매일 자동 수신\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "📉 지금 시장에서 정보 없이 버티는 건 위험합니다\n팔로우로 매일 시그널 먼저 확인하세요\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "🚨 다음 VIX 급등 때 이 분석 못 보면 늦습니다\n팔로우 → 알림 ON → 먼저 대응\n⚠️ 투자 참고 정보, 투자 권유 아님",
]

# ── DESIRE (욕망 — 수익, 성장 기대) ──────────────────────
_CTA_DESIRE: List[str] = [
    "📈 매일 시장 데이터 분석하는 사람과 뉴스만 보는 사람\n그 차이는 시간이 지나야 납니다\n팔로우로 데이터 기반 투자 시작\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "💡 19개 시그널로 매일 시장을 읽습니다\n완전 무료 — 팔로우만 하면 됩니다\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "🎯 VIX, 공포지수, ETF 배분까지 매일 자동 정리\n지금 팔로우하면 내일 아침부터 바로 수신\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "📊 데이터 기반 시장 분석 매일 무료로\n팔로우 → 알림 설정하면 빠짐없이 받습니다\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "🔭 미장 분석 19개 시그널 — 직접 만든 시스템\n매일 자동 발행 중 → 팔로우로 구독\n⚠️ 투자 참고 정보, 투자 권유 아님",
]

# ── COMPARE (비교 — 남 vs 나) ────────────────────────────
_CTA_COMPARE: List[str] = [
    "🧠 이미 팔로우한 사람들은 오늘 아침 이 분석 보고 시작했습니다\n아직 안 했다면 지금이 기회\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "👤 개인 투자자가 기관보다 늦게 아는 이유는\n정보를 보는 루틴이 없어서입니다\n팔로우 = 매일 아침 루틴 완성\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "📌 이 분석 보는 사람들과 안 보는 사람들\n6개월 뒤 시장 관점이 달라집니다\n팔로우로 매일 데이터 루틴 시작\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "🤝 매일 시장 분석 루틴 가진 투자자 vs 뉴스만 보는 투자자\n전자가 되려면 팔로우부터\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "📰 뉴스는 이미 늦었습니다\n데이터가 먼저 움직입니다\n팔로우 → 매일 데이터 먼저 확인\n⚠️ 투자 참고 정보, 투자 권유 아님",
]

# ── ANGER (분노 — 정보 비대칭, 잘못된 시장) ──────────────
_CTA_ANGER: List[str] = [
    "😤 증권사 리포트는 이미 늦게 나옵니다\n19개 시그널로 먼저 읽는 법 → 팔로우로 매일 무료 수신\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "🤐 이 정보 원래 유료입니다\n지금은 무료로 공개 중 — 팔로우만 하면 됩니다\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "📣 개인 투자자에게 불리한 시장\n데이터로 대응하는 방법이 있습니다\n팔로우 → 매일 시그널 먼저 확인\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "🔍 아무도 이걸 한국어로 정리해주지 않아서 직접 만들었습니다\n팔로우로 무료 구독\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "💢 월가 움직임을 한발 늦게 아는 개인 투자자\n그 격차를 줄이는 가장 쉬운 방법: 팔로우\n⚠️ 투자 참고 정보, 투자 권유 아님",
]

_CTA_BY_EMOTION: Dict[str, List[str]] = {
    "FOMO":    _CTA_FOMO,
    "DESIRE":  _CTA_DESIRE,
    "COMPARE": _CTA_COMPARE,
    "ANGER":   _CTA_ANGER,
}


def _pick_cta(risk_level: str = "MEDIUM") -> str:
    """레짐 → 감정 → CTA 풀에서 랜덤 선택."""
    emotion = _pick_emotion(risk_level)
    pool    = _CTA_BY_EMOTION[emotion]
    chosen  = random.choice(pool)
    logger.debug(f"[ThreadBuilder] CTA 선택: emotion={emotion}")
    return chosen


# ═══════════════════════════════════════════════════════════
# 후킹 문구 풀 — 세션 × 감정 3차원 매핑
# ═══════════════════════════════════════════════════════════
# 구조: {session: {emotion: [문구, ...]}}
# fallback: session 없음 → "default" / emotion 없음 → "FOMO"
#
# 확장 시: 해당 세션 × 감정 리스트에 항목 추가

_HOOK_POOL: Dict[str, Dict[str, List[str]]] = {
    "morning": {
        "FOMO": [
            "⏰ 출근 전 5분 — 오늘 미장 핵심 {n}가지\n이거 모르면 하루 늦습니다",
            "🌅 미장 마감 정리 — 이미 본 사람은 포지션 잡았습니다\n{n}가지 핵심 정리",
            "📛 오늘 놓치면 안 되는 시그널 {n}가지",
            "⚡ 오늘 미장 핵심 {n}가지 — 지금 안 보면 하루 뒤처집니다",
            "🔕 아직 못 봤습니까 — 오늘 미장 핵심 {n}가지",
        ],
        "DESIRE": [
            "☕ 출근길 {n}분 — 오늘 미장 핵심 정리",
            "📈 오늘 시장에서 기회 되는 것 {n}가지 — 데이터로 골랐습니다",
            "🔭 오늘 미장 핵심 {n}가지 — 데이터 루틴의 시작",
            "💡 오늘 알면 유리한 것 {n}가지 — 미장 브리핑",
        ],
        "COMPARE": [
            "🧠 매일 이 분석 보는 사람 vs 안 보는 사람\n오늘 핵심 {n}가지 차이",
            "👤 기관은 이미 알고 있습니다 — 개인이 알아야 할 {n}가지",
            "📌 오늘 아는 사람 vs 모르는 사람 갈리는 포인트 {n}가지",
        ],
        "ANGER": [
            "😤 증권사 리포트에 없는 것 {n}가지 — 직접 정리했습니다",
            "🔍 아무도 이걸 한국어로 안 알려줘서 — 오늘 미장 핵심 {n}가지",
            "📣 개인 투자자가 몰라서 손해 보는 것 {n}가지 — 오늘 공개",
        ],
    },
    "close": {
        "FOMO": [
            "🔔 오늘 종가 — 내일 대응 늦으면 후회합니다\n지금 확인할 {n}가지",
            "🌙 오늘 미장 마감 — 내일 어떻게 됩니까\n핵심 {n}가지 먼저 정리",
            "📉 오늘 종가에서 읽어야 할 경고 {n}가지",
            "⏰ 자기 전에 확인해야 할 것 {n}가지 — 내일이 달라집니다",
        ],
        "DESIRE": [
            "🎬 오늘 미장 마감 — 내일 기회 되는 것 {n}가지",
            "📊 종가 기준 오늘의 전략 {n}가지 — 데이터로 정리",
            "🔑 내일을 위해 오늘 알아야 할 것 {n}가지",
        ],
        "COMPARE": [
            "🤝 오늘 종가 분석 — 아는 투자자는 이미 대응했습니다\n{n}가지 핵심",
            "📰 오늘 마감 뉴스 말고 — 데이터가 말하는 것 {n}가지",
            "👤 종가 보는 투자자 vs 시그널 보는 투자자 — {n}가지 차이",
        ],
        "ANGER": [
            "💢 오늘 미장 — 뉴스는 이렇게 말했지만 데이터는 달랐습니다\n{n}가지",
            "🤐 종가 뒤에 숨은 시그널 {n}가지 — 직접 정리",
        ],
    },
    "full": {
        "FOMO": [
            "🔭 오늘 전체 시장 — 지금 안 보면 내일 대응 늦습니다\n{n}가지 심층 분석",
            "📊 전체 시장 점검 — 이미 본 사람들은 포지션 조정했습니다\n{n}가지",
            "📌 오늘 놓치면 안 되는 전체 시그널 {n}개",
        ],
        "DESIRE": [
            "💎 오늘의 전략 스레드 — ETF부터 매크로까지 {n}가지",
            "🗺 시장 전체 그림 — 데이터 기반 {n}가지 분석",
            "📐 전략가 시각으로 보는 오늘 시장 {n}가지",
        ],
        "COMPARE": [
            "🧠 ETF 전략 아는 투자자 vs 모르는 투자자\n{n}가지 오늘 분석",
            "📊 기관이 보는 것과 개인이 보는 것 — {n}가지 차이",
        ],
        "ANGER": [
            "📣 유료 리포트 수준 분석 — 무료로 공개합니다\n{n}가지 심층 분석",
            "🔍 증권사가 공개 안 하는 것 {n}가지 — 데이터로 직접 봤습니다",
        ],
    },
    "narrative": {
        "FOMO": [
            "📖 숫자 뒤에 진짜 이야기 — 지금 놓치면 맥락을 잃습니다\n{n}가지",
            "🧩 오늘 시장 흐름 — 퍼즐 {n}조각 먼저 공개",
            "🔬 지금 시장에서 가장 중요한 흐름 {n}가지",
        ],
        "DESIRE": [
            "💡 시장을 읽는 눈 — 오늘 {n}가지 관점 정리",
            "🎯 데이터가 가리키는 방향 {n}가지 — 맥락 분석",
        ],
        "COMPARE": [
            "🧠 시장 맥락 읽는 사람 vs 숫자만 보는 사람\n{n}가지 오늘 해석",
            "📰 뉴스 헤드라인 vs 실제 데이터 — {n}가지 차이",
        ],
        "ANGER": [
            "😤 오늘 시장 — 언론이 틀린 것 {n}가지",
            "💢 왜 뉴스만 보면 손해를 봅니까 — 데이터로 반박 {n}가지",
        ],
    },
    "intraday": {
        "FOMO": [
            "⚡ 장중 지금 이 순간 — 놓치면 늦습니다\n{n}가지 확인",
            "📡 실시간 시장 — 지금 움직이는 것 {n}가지",
            "🔔 장중 시그널 변화 — 지금 확인해야 할 {n}가지",
        ],
        "DESIRE": [
            "⏱ 장중 핵심 포인트 {n}가지 — 오늘 전략 업데이트",
            "🌊 장중 흐름 — 오늘 기회 되는 것 {n}가지",
        ],
        "COMPARE": [
            "👤 장중 시그널 보는 투자자 vs 모르는 투자자\n{n}가지 지금 차이",
            "🧠 장중 대응하는 사람들이 보는 것 {n}가지",
        ],
        "ANGER": [
            "💢 장중 언론이 안 다루는 시그널 {n}가지",
            "😤 지금 시장 — 뉴스와 데이터가 다릅니다\n{n}가지 확인",
        ],
    },
    "alert": {
        "FOMO": [
            "🚨 지금 움직여야 합니다 — 확인 {n}가지",
            "⚠️ 시장 이상 신호 — 늦으면 안 됩니다\n{n}개 긴급 체크",
            "🔴 긴급 시그널 — 지금 확인하지 않으면 늦습니다",
        ],
        "ANGER": [
            "😤 시장이 또 흔들립니다 — 이유 {n}가지 데이터로 분석",
            "💢 이런 상황에서 개인 투자자가 당하지 않으려면 {n}가지",
        ],
    },
    "weekly": {
        "FOMO": [
            "📋 이번 주 미장 — 다음 주 대비 안 하면 늦습니다\n{n}가지 정리",
            "📅 주간 리뷰 — 이미 본 사람들은 다음 주 준비 시작했습니다\n{n}가지",
        ],
        "DESIRE": [
            "📊 이번 주 시장 전체 리뷰 — {n}가지 핵심 정리",
            "🔑 다음 주를 위한 이번 주 핵심 {n}가지",
        ],
        "COMPARE": [
            "🧠 주간 데이터 점검하는 투자자 vs 안 하는 투자자\n{n}가지 차이",
        ],
        "ANGER": [
            "😤 이번 주 시장 — 언론이 안 다룬 것 {n}가지 정리",
        ],
    },
    "default": {
        "FOMO": [
            "📊 지금 이 분석 놓치면 늦습니다 — {n}가지 핵심",
            "📌 오늘 알아야 할 것 {n}가지 — 먼저 확인",
        ],
        "DESIRE": [
            "💡 오늘의 핵심 {n}가지 — 데이터 기반 정리",
            "🎯 주목할 포인트 {n}가지",
        ],
        "COMPARE": [
            "🧠 아는 투자자가 먼저 보는 것 {n}가지",
        ],
        "ANGER": [
            "😤 아무도 안 알려주는 것 {n}가지 — 직접 정리",
        ],
    },
}


def _pick_hook(session: str, n_parts: int, risk_level: str = "MEDIUM") -> str:
    """
    레짐 → 감정 선택 → 세션 × 감정 풀 → 랜덤 후킹 문구.

    fallback 체계:
      session 없음 → "default"
      emotion 없음 → "FOMO"
      풀 비어있음  → 기본 문구
    """
    emotion      = _pick_emotion(risk_level)
    session_pool = _HOOK_POOL.get(session) or _HOOK_POOL["default"]
    emotion_pool = (
        session_pool.get(emotion)
        or session_pool.get("FOMO")
        or list(session_pool.values())[0]
    )
    hook = random.choice(emotion_pool).format(n=n_parts)
    logger.debug(
        f"[ThreadBuilder] 후킹: session={session} "
        f"risk={risk_level} emotion={emotion}"
    )
    return hook


# ═══════════════════════════════════════════════════════════
# Alert 전용 감정 suffix
# ═══════════════════════════════════════════════════════════
_ALERT_EMOTION_SUFFIX: Dict[str, List[str]] = {
    "VIX_L2": [
        "\n\n이 시그널 놓치지 않으려면 팔로우 → 알림 ON\n⚠️ 투자 참고 정보, 투자 권유 아님",
        "\n\n미리 알고 대응한 사람들이 있습니다\n팔로우로 다음 Alert 먼저 받으세요\n⚠️ 투자 참고 정보, 투자 권유 아님",
        "\n\n팔로우하면 이런 급변 시그널 실시간 수신됩니다\n⚠️ 투자 참고 정보, 투자 권유 아님",
    ],
    "SPY_L2": [
        "\n\n급락 대응 놓친 사람들이 후회하는 이유\n팔로우 → 다음엔 먼저 받습니다\n⚠️ 투자 참고 정보, 투자 권유 아님",
        "\n\n이런 시그널 먼저 받으려면 팔로우 → 알림 설정\n⚠️ 투자 참고 정보, 투자 권유 아님",
        "\n\n팔로우하면 SPY 급변 시 자동 알림\n⚠️ 투자 참고 정보, 투자 권유 아님",
    ],
    "OIL_L2": [
        "\n\n유가 급등 시 먼저 대응하는 방법\n팔로우 → 자동 수신\n⚠️ 투자 참고 정보, 투자 권유 아님",
        "\n\n이런 매크로 변화 먼저 알고 싶다면 팔로우\n⚠️ 투자 참고 정보, 투자 권유 아님",
    ],
    "CRISIS": [
        "\n\n복합 위기 시그널 — 먼저 알면 대응이 다릅니다\n팔로우 → 알림 ON\n⚠️ 투자 참고 정보, 투자 권유 아님",
        "\n\n이 채널 팔로우하면 위기 시그널 실시간 수신\n⚠️ 투자 참고 정보, 투자 권유 아님",
    ],
}

_DEFAULT_ALERT_SUFFIX: List[str] = [
    "\n\n팔로우하면 다음 Alert 먼저 받습니다\n⚠️ 투자 참고 정보, 투자 권유 아님",
    "\n\n이런 시그널 놓치지 않으려면 팔로우 → 알림 ON\n⚠️ 투자 참고 정보, 투자 권유 아님",
]


def get_alert_emotion_suffix(alert_type: str) -> str:
    """
    Alert 트윗 하단 감정 트리거 suffix.
    run_alert.py의 _format_x_alert_tweet에서 호출.
    """
    pool = _ALERT_EMOTION_SUFFIX.get(alert_type, _DEFAULT_ALERT_SUFFIX)
    return random.choice(pool)


# ═══════════════════════════════════════════════════════════
# 텍스트 분할
# ═══════════════════════════════════════════════════════════

def _split_text_to_chunks(text: str, limit: int = _TWEET_SOFT_LIMIT) -> List[str]:
    """
    텍스트 → X 트윗 단위 분할.
    단락(\\n\\n) → 문장(.) → 강제 자름 순서.
    빈 입력 시 [""] 반환 (크래시 없음).
    """
    if not text or not text.strip():
        return [""]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para).strip() if current else para

        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)

            if len(para) > limit:
                sentences = para.split(". ")
                sub = ""
                for sent in sentences:
                    s = sent if sent.endswith(".") else sent + "."
                    sub_cand = (sub + " " + s).strip() if sub else s
                    if len(sub_cand) <= limit:
                        sub = sub_cand
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = s[:limit]
                if sub:
                    current = sub
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks if chunks else [text[:limit]]


# ═══════════════════════════════════════════════════════════
# 스레드 빌드 — 메인 함수
# ═══════════════════════════════════════════════════════════

def build_thread(
    content: str,
    session: str = "default",
    risk_level: str = "MEDIUM",
    add_hook: bool = True,
    add_cta: bool = True,
    add_counter: bool = True,
) -> List[str]:
    """
    콘텐츠 → X 스레드 리스트.

    Args:
        content:     스레드로 만들 텍스트 (후킹/CTA 미포함)
        session:     "morning" / "close" / "full" / "intraday" / "narrative" / ...
        risk_level:  "HIGH" / "MEDIUM" / "LOW"
        add_hook:    True → 첫 트윗 감정 후킹 삽입
        add_cta:     True → 마지막 트윗 감정 CTA 삽입
        add_counter: True → "1/N" 카운터 삽입

    Returns:
        발행 준비된 트윗 리스트 (index 0 = 첫 트윗)
    """
    # 유효값 검증
    if risk_level not in ("HIGH", "MEDIUM", "LOW"):
        risk_level = "MEDIUM"

    chunks = _split_text_to_chunks(content)

    if len(chunks) == 1 and not add_hook and not add_cta:
        return chunks

    # 카운터 삽입
    if add_counter and len(chunks) > 1:
        total  = len(chunks)
        chunks = [f"{i}/{total}\n\n{c}" for i, c in enumerate(chunks, 1)]

    thread: List[str] = []

    # 감정 후킹 첫 트윗
    if add_hook:
        total_parts = len(chunks) + (1 if add_cta else 0)
        thread.append(_pick_hook(session, total_parts, risk_level))

    thread.extend(chunks)

    # 감정 CTA 마지막 트윗
    if add_cta:
        thread.append(_pick_cta(risk_level))

    logger.info(
        f"[ThreadBuilder v{VERSION}] 완성 "
        f"session={session} risk={risk_level} tweets={len(thread)}"
    )
    return thread


def build_single_tweet(
    content: str,
    session: str = "default",
    risk_level: str = "MEDIUM",
) -> str:
    """
    단일 트윗 — 감정 기반 CTA 삽입.
    800자 이하 콘텐츠 또는 단발 alert에 사용.
    """
    if risk_level not in ("HIGH", "MEDIUM", "LOW"):
        risk_level = "MEDIUM"

    cta   = _pick_cta(risk_level)
    tweet = f"{content}\n\n{cta}"

    if len(tweet) > _TWEET_SOFT_LIMIT:
        avail = _TWEET_SOFT_LIMIT - len(cta) - 4
        tweet = f"{content[:avail]}...\n\n{cta}"

    return tweet
