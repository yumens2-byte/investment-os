"""
collectors/fred_client.py (v1.1.0)
====================================
FRED API에서 거시경제 지표를 수집한다.
공식 무료 API. 데이터 갱신 주기: 일~주 단위.

v1.1.0 (2026-04-07) — 다중 BUGFIX (운영 사고 위험 6건 수정)
─────────────────────────────────────────────────────────────
BUG-F1 🚨 FEDFUNDS 하드코딩 fallback 5.25% 제거 (HIGH)
  - 이전: API 500 에러 → fallback 5.25% (2023-2024 stale 값)
  - 변경: API 실패 시 None 반환. 다운스트림(_fmt_fred, credit_stress)이
    이미 None을 안전 처리하므로 영향 없음.
  - 14:06 morning에서 13:36의 3.64% → 14:06의 5.25%로 1.61%p 점프했던
    silent failure 사고 재발 차단.

BUG-F2 🚨 hy_spread / yield_curve 하드코딩 fallback 제거 (HIGH)
  - hy_spread fallback 4.0 → None
  - yield_curve fallback 0.0 → None
  - _classify_credit_stress는 None을 "Unknown"으로 안전 처리 (변경 없음).
  - yield_curve_inverted는 None일 때 False (변경 없음).

BUG-F3 🚨 ICSA 이중 fetch 제거 (API quota 낭비 + 코드 중복)
  - 이전: line 73 + line 94 두 곳에서 같은 시리즈 호출 → API 2회 호출
  - 변경: 1차 fetch만 남기고 2차 블록 제거. 단위 변환 로직 1차에 통합.

BUG-F4 🚨 T5YIFR 이중 fetch 제거 (BUG-F3 동일 패턴)
  - line 82 + line 104 → 1차만 유지

BUG-F5 🚨 1차 ICSA fetch에 단위 변환 누락 → 1차만 성공 시 220000K 출력 위험
  - 단위 변환을 1차 블록에 합쳐서 안전 보장

BUG-F6 🚨 detect_macro_changes() false alarm 차단
  - BUG-F1 fix로 자동 해결: fed_rate=None이면 기존 line 164의 None 체크
    `if cur_val is None or prev_val is None: continue`로 skip됨
  - 이전 동작: stale 5.25 vs 실제 3.64 → 1.61%p false alarm 발생 가능
  - 새 동작: API 실패 시 None → skip → false alarm 0건

추가 안전 처리:
  - 로그 출력 시 None-safe 포맷터 사용 (`_fmt_pct`, `_fmt_int_k`)
  - VERSION 상수 추가 (마스터 운영 표준 준수)
"""
import logging
from typing import Optional
from config.settings import FRED_API_KEY, FRED_SERIES

VERSION = "1.2.0"

logger = logging.getLogger(__name__)

_fred_client = None


def _get_client():
    """FRED 클라이언트 싱글톤 (lazy import)"""
    global _fred_client
    if _fred_client is None:
        if not FRED_API_KEY:
            logger.warning("[FRED] API 키 미설정. FRED 수집 건너뜀.")
            return None
        try:
            from fredapi import Fred
            _fred_client = Fred(api_key=FRED_API_KEY)
        except Exception as e:
            logger.error(f"[FRED] 클라이언트 초기화 실패: {e}")
            return None
    return _fred_client


def _fetch_latest(series_id: str) -> Optional[float]:
    """특정 시리즈의 최신값 조회. 실패 시 None 반환 (stale fallback 금지)."""
    client = _get_client()
    if client is None:
        return None
    try:
        series = client.get_series(series_id)
        if series is None or series.empty:
            return None
        # NaN 제거 후 마지막 값
        series = series.dropna()
        return float(series.iloc[-1]) if not series.empty else None
    except Exception as e:
        logger.error(f"[FRED] {series_id} 조회 실패: {e}")
        return None


def _fmt_pct(v: Optional[float]) -> str:
    """None-safe 퍼센트 포맷터"""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_int_k(v: Optional[float]) -> str:
    """None-safe 천 단위 포맷터 (실업수당용)"""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.0f}K"
    except (TypeError, ValueError):
        return "N/A"


def collect_macro_data() -> dict:
    """
    FRED 거시경제 데이터 수집.
    수집 실패 시 None 반환 (stale fallback 사용 금지). 다운스트림이 이미 None 처리.

    Returns:
        macro_data dict
    """
    logger.info(f"[FRED v{VERSION}] 거시경제 데이터 수집 시작")

    # ── Tier 1: 핵심 거시 지표 ──────────────────────────────
    fed_rate = _fetch_latest(FRED_SERIES["fed_funds_rate"])
    hy_spread = _fetch_latest(FRED_SERIES["hy_spread"])
    yield_curve = _fetch_latest(FRED_SERIES["yield_curve"])

    # ── Tier 2: 노동시장 + 인플레이션 기대 ─────────────────
    # T2-3: 주간 신규 실업수당 청구건수 (ICSA)
    #   - FRED 단위: 명 (예: 220000) → 천 명으로 변환 (예: 220.0)
    #   - 주간 업데이트, 실물경제 선행지표 중 가장 실시간성 높음
    #   - 수집 실패 시 None → macro_engine에서 중립 처리
    initial_claims_raw = _fetch_latest(FRED_SERIES.get("initial_claims", "ICSA"))
    if initial_claims_raw is not None and initial_claims_raw > 1000:
        initial_claims = initial_claims_raw / 1000.0
    else:
        initial_claims = initial_claims_raw  # None 또는 이미 천 단위
    if initial_claims is not None:
        logger.info(f"[FRED] 신규 실업수당 청구: {initial_claims:.0f}K")
    else:
        logger.warning("[FRED] ICSA 수집 실패 → None (엔진에서 중립 처리)")

    # T2-4: 5년 기대 인플레이션율 (T5YIFR)
    #   - 단위: % (예: 2.35)
    #   - 일간 업데이트
    #   - 수집 실패 시 None → macro_engine에서 중립 처리
    inflation_exp = _fetch_latest(FRED_SERIES.get("inflation_exp", "T5YIFR"))


    # ── Priority A: 2Y Treasury Yield (DGS2) ────────────────
    # 단기 금리 직접값 + 장단기 스프레드 수치 계산
    # FRED DGS2는 전일 기준 (lag 1일) — 실시간 불필요, 방향성 판단용
    us2y = _fetch_latest(FRED_SERIES.get("us2y", "DGS2"))
    if us2y is not None:
        logger.info(f"[FRED] 2Y Treasury Yield: {us2y:.3f}%")
    else:
        logger.warning("[FRED] DGS2 수집 실패 → None (엔진에서 중립 처리)")
  
  
    if inflation_exp is not None:
        logger.info(f"[FRED] 기대 인플레이션: {inflation_exp:.2f}%")
    else:
        logger.warning("[FRED] T5YIFR 수집 실패 → None (엔진에서 중립 처리)")

    # ── 결과 dict 조립 ─────────────────────────────────────
    # ★ stale 하드코딩 fallback 절대 금지 (BUG-F1, F2 fix)
    data = {
        "fed_funds_rate": fed_rate,         # None 가능 — _fmt_fred가 안전 처리
        "hy_spread": hy_spread,             # None 가능
        "yield_curve": yield_curve,         # None 가능
        # 신용 스트레스 판단 (None → "Unknown" 안전 처리)
        "credit_stress": _classify_credit_stress(hy_spread),
        # 장단기 역전 여부 (None → False)
        "yield_curve_inverted": (yield_curve is not None and yield_curve < 0),
        "initial_claims": initial_claims,
        "inflation_exp": inflation_exp,
        "us2y":          us2y,           # Priority A: 2Y Treasury Yield
  
        # ── Priority A: 10Y-2Y 스프레드 bp 계산 ─────────────────
        # yield_curve (T10Y2Y) 이미 수집됨 — 10Y-2Y 기준
        # bp 단위로 별도 저장하여 콘텐츠/시그널에서 활용
        if data.get("yield_curve") is not None:
            data["spread_2y10y_bp"] = round(data["yield_curve"] * 100, 1)
        elif data.get("us2y") is not None:
            # T10Y2Y 실패 시 us10y - us2y 직접 계산 (run_market에서 snapshot의 us10y 활용 불가 → None)
            data["spread_2y10y_bp"] = None
        else:
            data["spread_2y10y_bp"] = None
    }

    logger.info(
        f"[FRED] 수집 완료: 기준금리 {_fmt_pct(fed_rate)} | "
        f"HY 스프레드 {_fmt_pct(hy_spread)} | "
        f"수익률 곡선 {_fmt_pct(yield_curve)} | "
        f"2Y금리 {_fmt_pct(us2y)} | "        # 신규
        f"실업수당 {_fmt_int_k(initial_claims)} | "
        f"기대인플레 {_fmt_pct(inflation_exp)}"
    )
    return data


def _classify_credit_stress(hy_spread: Optional[float]) -> str:
    if hy_spread is None:
        return "Unknown"
    if hy_spread < 3.5:
        return "Low"
    elif hy_spread < 5.5:
        return "Moderate"
    else:
        return "High"


def detect_macro_changes(
    current: dict,
    prev: dict,
    thresholds: dict = None,
) -> list:
    """
    현재 vs 이전 FRED 데이터 비교 → 유의미한 변화 감지

    Args:
        current:    최신 collect_macro_data() 결과
        prev:       직전 저장된 FRED 데이터
        thresholds: 변화 감지 임계값 (기본값 내장)

    Returns:
        변화 감지된 항목 리스트
        [{"indicator_id": str, "prev": float, "new": float, "change": float}]

    Note:
        v1.1.0: cur_val 또는 prev_val이 None이면 skip되므로 stale fallback
        false alarm이 자동으로 차단됨 (BUG-F6 해결).
    """
    if not prev:
        return []

    DEFAULTS = {
        "fed_funds_rate": 0.25,   # 0.25% 이상 변화
        "hy_spread":      0.5,    # 0.5% 이상 변화
        "yield_curve":    0.3,    # 0.3% 이상 변화
    }
    # FRED 필드명 → indicator_id 매핑
    FIELD_TO_ID = {
        "fed_funds_rate": "FEDFUNDS",
        "hy_spread":      "BAMLH0A0HYM2",
        "yield_curve":    "T10Y2Y",
    }
    thresholds = thresholds or DEFAULTS
    changes = []

    for field, indicator_id in FIELD_TO_ID.items():
        cur_val  = current.get(field)
        prev_val = prev.get(field)
        if cur_val is None or prev_val is None:
            continue
        threshold = thresholds.get(field, 0.25)
        change = abs(cur_val - prev_val)
        if change >= threshold:
            changes.append({
                "indicator_id": indicator_id,
                "prev":  round(prev_val, 4),
                "new":   round(cur_val, 4),
                "change": round(cur_val - prev_val, 4),
            })

    return changes
