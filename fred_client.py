"""
collectors/fred_client.py
FRED API에서 거시경제 지표를 수집한다.
공식 무료 API. 데이터 갱신 주기: 일~주 단위.
"""
import logging
from typing import Optional
from config.settings import FRED_API_KEY, FRED_SERIES

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
    """특정 시리즈의 최신값 조회"""
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


def collect_macro_data() -> dict:
    """
    FRED 거시경제 데이터 수집
    Returns: macro_data dict
    """
    logger.info("[FRED] 거시경제 데이터 수집 시작")

    fed_rate = _fetch_latest(FRED_SERIES["fed_funds_rate"])
    hy_spread = _fetch_latest(FRED_SERIES["hy_spread"])
    yield_curve = _fetch_latest(FRED_SERIES["yield_curve"])

    data = {
        "fed_funds_rate": fed_rate if fed_rate is not None else 5.25,
        "hy_spread": hy_spread if hy_spread is not None else 4.0,
        "yield_curve": yield_curve if yield_curve is not None else 0.0,
        # 신용 스트레스 판단 (HY 스프레드 기준)
        # < 3.5% → Low / 3.5~5.5% → Moderate / > 5.5% → High
        "credit_stress": _classify_credit_stress(hy_spread),
        # 장단기 역전 여부
        "yield_curve_inverted": (yield_curve is not None and yield_curve < 0),
    }

    # ── Tier 2 FRED 시리즈 수집 (2026-04-01 추가) ────────────
    # T2-3: 주간 신규 실업수당 청구건수 (천 명 단위)
    #   노동시장 실시간 건강도 — 주간 업데이트, 선행성 높음
    initial_claims = _fetch_latest(FRED_SERIES.get("initial_claims", "ICSA"))
    data["initial_claims"] = initial_claims
    if initial_claims is not None:
        logger.info(f"[FRED] 신규 실업수당 청구: {initial_claims:.0f}K")
    else:
        logger.warning("[FRED] ICSA 수집 실패 → None (엔진에서 중립 처리)")

    # T2-4: 5년 기대 인플레이션율 (%)
    #   시장이 향후 5년간 기대하는 인플레이션 수준
    inflation_exp = _fetch_latest(FRED_SERIES.get("inflation_exp", "T5YIFR"))
    data["inflation_exp"] = inflation_exp
    if inflation_exp is not None:
        logger.info(f"[FRED] 기대 인플레이션: {inflation_exp:.2f}%")
    else:
        logger.warning("[FRED] T5YIFR 수집 실패 → None (엔진에서 중립 처리)")

    # ── Tier 2 확장 FRED 시리즈 (2026-04-01 추가) ──────────────
    # T2-3: 주간 신규 실업수당 청구건수 (ICSA)
    #   - 단위: 천 명 (예: 220 = 220,000명)
    #   - 주간 업데이트 — 실물경제 선행지표 중 가장 실시간성 높음
    #   - 수집 실패 시 None → macro_engine에서 중립 처리
    initial_claims = _fetch_latest(FRED_SERIES.get("initial_claims", "ICSA"))
    # FRED ICSA 단위: 명 (예: 220000) → 천 명으로 변환
    if initial_claims is not None and initial_claims > 1000:
        initial_claims = initial_claims / 1000.0
    data["initial_claims"] = initial_claims

    # T2-4: 5년 기대 인플레이션율 (T5YIFR, 5-Year Breakeven)
    #   - 단위: % (예: 2.35)
    #   - 일간 업데이트 — 시장의 인플레이션 기대를 반영
    #   - 수집 실패 시 None → macro_engine에서 중립 처리
    data["inflation_exp"] = _fetch_latest(FRED_SERIES.get("inflation_exp", "T5YIFR"))

    logger.info(
        f"[FRED] 수집 완료: 기준금리 {data['fed_funds_rate']:.2f}% | "
        f"HY 스프레드 {data['hy_spread']:.2f}% | "
        f"수익률 곡선 {data['yield_curve']:.2f}% | "
        f"실업수당 {data.get('initial_claims', 'N/A')}K | "
        f"기대인플레 {data.get('inflation_exp', 'N/A')}%"
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
