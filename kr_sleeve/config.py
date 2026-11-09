"""
kr_sleeve/config.py — KR 슬리브 설정 (DN2026_0108)

사람이 바꾸는 값(k, 내부 배분, 종목 표시명, 미국 비중 근사치)은 이 파일의 상수로만 둔다.
변경은 DN 승인과 VERSION 갱신을 함께 한다(운영 관점 합의: 비율은 자주 바꾸지 않는다).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

VERSION = "1.1.1"  # 1.1.1: 금 수집 시간 상한·백필 이어 받기(DN2026_0109 보완 B) · 1.1.0: 금 패널

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KrEtf:
    code: str            # KRX 단축코드 (DB·운영자 알림에만 노출)
    display: str         # 공개 발행물 표기 (회장 지시 11-09: 마스킹 표기)
    short: str           # 공개 발행물 짧은 표기
    internal_name: str   # 정식 명칭 (DB·운영자 알림에만 노출)


ETF_HIGH_DIV = KrEtf("161510", "P**S 고배당주", "P**S", "PLUS 고배당주")
ETF_TOTAL_WORLD = KrEtf("0060H0", "Ti**ER 토탈월드스탁액티브", "Ti**ER", "TIGER 토탈월드스탁액티브")
KR_ETFS: tuple[KrEtf, ...] = (ETF_HIGH_DIV, ETF_TOTAL_WORLD)

# 미국 6종 (morning 출력 키와 동일, 순서 고정)
US_ETFS: tuple[str, ...] = ("QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT")
# 실효 미국 주식 노출 계산 시 주식형으로 보는 미국 종목 (TLT는 채권)
US_EQUITY_ETFS: tuple[str, ...] = ("QQQM", "XLK", "SPYM", "XLE", "ITA")
# B안 비교 필드: 161510 비중을 이 3종 합에 연동 (기록만, 본값 아님)
US_DEFENSIVE_REAL_ETFS: tuple[str, ...] = ("XLE", "ITA", "TLT")

REGIMES: frozenset[str] = frozenset({
    "Risk-On", "Risk-Off", "Oil Shock", "Liquidity Crisis",
    "Recession Risk", "Stagflation Risk", "AI Bubble", "Transition",
})
RISK_LEVELS: frozenset[str] = frozenset({"LOW", "MEDIUM", "HIGH"})

# ── 비율 (DN2026_0108, 3인 검토 취합안 · 회장 승인) ──────────────
K_PCT = 20                                   # KR 슬리브가 전체에서 차지하는 비율(%)
INTERNAL_A = {"161510": 50, "0060H0": 50}    # A안 본값: 내부 50:50

# 0060H0의 미국 주식 비중 근사치 — 운용사 공시 "미국:비미국 약 6:4". 월 1회 수동 갱신.
TOTAL_WORLD_US_SHARE = 0.60
TOTAL_WORLD_US_SHARE_SOURCE = "미래에셋 상품 안내(미국:비미국 약 6:4), 확인 2026-11-09"

# ── 시간·신선도 ──────────────────────────────────────────────
INPUT_MAX_AGE_MIN = 180        # core_data timestamp 허용 경과(분). 초과 시 INPUT_STALE
PUBLISH_WINDOW_MIN = 60        # morning 완료(core_data timestamp) 후 이 시간 안에서만 live 발행
SOURCE_MAX_STALE_DAYS = 7      # 마지막 완료 봉이 이보다 오래되면 SOURCE_STALE
KRX_OPEN_CHECK = (9, 10)       # 이 시각(KST) 이후 평일에 오늘 봉이 없으면 국내 휴장
KRX_CLOSE_DONE = (15, 40)      # 이 시각(KST) 이후에만 오늘 봉을 완료 봉으로 본다
PRICE_LIMIT_PCT = 30.0         # KRX 가격제한폭 — 전일 대비 초과 시 무효
MIN_COMPLETED_BARS = 2
FCHART_COUNT = 30
HTTP_TIMEOUT_SEC = 15
X_DELAY_RANGE_SEC = (180, 600)  # X 발행 전 무작위 대기(안티봇, STD-PUB-10)

# ── 금으로 잰 화폐가치 패널 (DN2026_0109, 회장 승인 11-09) ─────────
GOLD_ENABLED = True
GOLD_AMOUNTS = {"krw": 100_000_000, "usd": 10_000, "jpy": 1_000_000}   # 1억 원 · 1만 달러 · 100만 엔
OZ_G = 31.1034768               # 트로이온스 → g
GOLD_LONG_YEARS = 5             # 5년 = 최신 완료 월 vs 60개월 전 같은 월(고정 규칙 G-03)
GOLD_SHORT_DAYS = 30            # 1개월 = 금 마지막 확정일 T vs T−30일 이하 최신일
GOLD_MIN_DAYS_PER_MONTH = 10    # 월평균 최소 거래일
GOLD_FX_MAX_GAP_DAYS = 4        # 기준일 대비 허용 공백(연휴 고려)
GOLD_MAX_STALE_DAYS = 7         # 마지막 확정일이 이보다 오래되면 GOLD_STALE
GOLD_JUMP_REJECT_PCT = 15.0     # 전일 대비 초과 시 의심값으로 제외
GOLD_JUMP_WARN_PCT = 8.0        # 1개월 창 안 급변 경고(롤오버 후보)
GOLD_SIMILAR_PP = 0.2           # 통화 간 차이가 이 미만이면 '비슷'
GOLD_PAGE_SIZE = 60             # 실측 상한(100은 HTTP 400)
GOLD_BACKFILL_MAX_PAGES = 45
GOLD_PAGE_SLEEP_SEC = 0.4
GOLD_RECENT_DAYS = 45           # DB에서 1개월 계산용으로 읽는 최근 일수
GOLD_TIME_BUDGET_SEC = 120      # 금 수집 전체 상한(초). 실측 dry_run 64회 약 60초, shadow 첫날 백필 포함 여유분

# ── 모드 (STD-MODE-01) ───────────────────────────────────────
MODES = ("dry_run", "shadow", "live")


def mask_codes(text: str) -> str:
    """공개 화면(Actions 로그·job summary·artifact)용: 종목코드·정식명을 마스킹 표기로 바꾼다.
    (회장 지시 11-09)"""
    for etf in KR_ETFS:
        text = text.replace(etf.internal_name, etf.display).replace(etf.code, etf.short)
    return text


def parse_mode(raw: str | None) -> str:
    """KR_SLEEVE_MODE 파서. 3값 외(빈 값·오타 포함)는 dry_run으로 강등하고 WARNING."""
    value = (raw or "").strip().lower()
    if value in MODES:
        return value
    if value:
        logger.warning("[KRSleeve] KR_SLEEVE_MODE=%r 은 표준값이 아님 → dry_run 처리", raw)
    return "dry_run"


def current_mode() -> str:
    return parse_mode(os.getenv("KR_SLEEVE_MODE"))
