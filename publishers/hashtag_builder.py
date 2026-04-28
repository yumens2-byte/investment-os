"""
publishers/hashtag_builder.py
===============================
레짐 / 리스크 / 시그널 기반 동적 해시태그 생성기.

v1.0.0 (2026-04-29)
  - 고정 태그 + 레짐별 동적 태그 + 리스크별 태그 조합
  - X Premium 25,000자 환경: 태그 길이 영향 미미
  - 단일 진입점: HashtagBuilder.build()
"""

from __future__ import annotations

import logging

VERSION = "1.0.0"

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 1. 고정 태그 (세션 무관, 항상 포함)
# ─────────────────────────────────────────────────────────────

_FIXED_TAGS: list[str] = [
    "#미국증시",
    "#ETF투자",
    "#미국주식",
    "#주식투자",
]

# ─────────────────────────────────────────────────────────────
# 2. 레짐별 동적 태그
# ─────────────────────────────────────────────────────────────

_REGIME_TAGS: dict[str, list[str]] = {
    "Oil Shock":         ["#원유", "#WTI", "#에너지ETF", "#유가급등"],
    "Inflation Surge":   ["#인플레이션", "#금리", "#물가"],
    "Risk-Off":          ["#안전자산", "#채권", "#리스크오프"],
    "Liquidity Crisis":  ["#유동성위기", "#채권", "#안전자산"],
    "Recession Risk":    ["#경기침체", "#리스크관리", "#방어주"],
    "Goldilocks":        ["#상승장", "#매수시점", "#골디락스"],
    "Transition":        ["#전환국면", "#시장전환", "#중립대응"],
    "Normal":            ["#정상시장", "#균형포트폴리오"],
    "Stagflation":       ["#스태그플레이션", "#인플레이션", "#경기침체"],
}

# ─────────────────────────────────────────────────────────────
# 3. 리스크 레벨별 추가 태그
# ─────────────────────────────────────────────────────────────

_RISK_TAGS: dict[str, list[str]] = {
    "HIGH":   ["#급락주의", "#변동성급등", "#하락장"],
    "MEDIUM": ["#변동성주의", "#분산투자"],
    "LOW":    ["#저변동성", "#안정장세"],
}

# ─────────────────────────────────────────────────────────────
# 4. 시그널별 상황 태그
# ─────────────────────────────────────────────────────────────

_SIGNAL_TAGS: dict[str, dict] = {
    # PCR 탐욕 과열 감지
    "pcr_complacency": {
        "condition": lambda s: s.get("pcr_state") == "Bullish (Complacency)",
        "tags": ["#과열경보", "#옵션심리"],
    },
    # VIX 고점 경보
    "vix_high": {
        "condition": lambda s: (s.get("volatility_score") or 0) >= 4,
        "tags": ["#VIX급등", "#공포지수"],
    },
    # 채권 급락 경보
    "tlt_crash": {
        "condition": lambda s: (s.get("tlt_health_score") or 0) >= 4,
        "tags": ["#채권급락", "#금리급등"],
    },
    # 방어 로테이션
    "defensive_rotation": {
        "condition": lambda s: s.get("sector_state") == "Defensive Rotation",
        "tags": ["#방어주", "#섹터로테이션"],
    },
    # 골든크로스
    "golden_cross": {
        "condition": lambda s: s.get("golden_cross") is True,
        "tags": ["#골든크로스", "#상승추세"],
    },
    # 데드크로스
    "death_cross": {
        "condition": lambda s: s.get("death_cross") is True,
        "tags": ["#데드크로스", "#하락추세"],
    },
}

# ─────────────────────────────────────────────────────────────
# 5. 세션별 기본 태그
# ─────────────────────────────────────────────────────────────

_SESSION_TAGS: dict[str, list[str]] = {
    "morning": ["#미장오픈", "#모닝브리핑"],
    "full":    ["#시장분석", "#포트폴리오"],
    "close":   ["#마감정리", "#오늘의증시"],
    "alert":   ["#긴급알림", "#시장경보"],
}


class HashtagBuilder:
    """
    제목: 동적 해시태그 생성기

    내용: 레짐 / 리스크 / 시그널 / 세션 정보를 조합하여
          X 발행에 적합한 해시태그 문자열을 생성합니다.
          중복 제거, 최대 개수 제한(10개)을 적용합니다.

    책임:
      - 고정 태그 + 동적 태그 조합
      - 중복 태그 제거 (순서 유지)
      - 최대 10개 제한 (X 알고리즘 최적화)
    """

    MAX_TAGS: int = 10

    @classmethod
    def build(
        cls,
        regime: str = "",
        risk_level: str = "",
        signals: dict | None = None,
        session: str = "morning",
    ) -> str:
        """
        제목: 해시태그 문자열 생성 (단일 진입점)

        내용: 우선순위 순서로 태그를 수집하고 MAX_TAGS 이내로 반환합니다.
              고정 태그 → 레짐 태그 → 리스크 태그 → 시그널 태그 → 세션 태그

        Args:
            regime:     현재 레짐 (예: "Oil Shock")
            risk_level: 현재 리스크 레벨 (예: "MEDIUM")
            signals:    macro_engine 시그널 dict (None 허용)
            session:    실행 세션 (예: "morning")

        Returns:
            str: 해시태그 문자열 (예: "#미국증시 #ETF투자 #원유 ...")
                 태그 없으면 빈 문자열
        """
        tags: list[str] = []

        # 1. 고정 태그
        tags.extend(_FIXED_TAGS)

        # 2. 레짐별 동적 태그
        regime_tags = _REGIME_TAGS.get(regime, [])
        tags.extend(regime_tags)

        # 3. 리스크 레벨 태그
        risk_tags = _RISK_TAGS.get(risk_level.upper(), [])
        tags.extend(risk_tags)

        # 4. 시그널 상황 태그
        if signals:
            for sig_key, cfg in _SIGNAL_TAGS.items():
                try:
                    if cfg["condition"](signals):
                        tags.extend(cfg["tags"])
                except Exception:
                    continue

        # 5. 세션별 태그
        session_tags = _SESSION_TAGS.get(session, [])
        tags.extend(session_tags)

        # 중복 제거 (순서 유지)
        seen: set[str] = set()
        unique_tags: list[str] = []
        for tag in tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        # 최대 10개 제한
        final_tags = unique_tags[: cls.MAX_TAGS]

        result = " ".join(final_tags)
        logger.info(
            f"[HashtagBuilder] v{VERSION} 생성 완료: "
            f"{len(final_tags)}개 태그 | {result[:60]}"
        )
        return result
