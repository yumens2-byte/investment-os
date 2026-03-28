"""
collectors/fred_client.py
FRED API에서 거시경제 지표를 수집한다.
공식 무료 API. 데이터 갱신 주기: 일~주 단위.
"""
import logging
from typing import Optional
from fredapi import Fred
from config.settings import FRED_API_KEY, FRED_SERIES

logger = logging.getLogger(__name__)

_fred_client: Optional[Fred] = None


def _get_client() -> Optional[Fred]:
    """FRED 클라이언트 싱글턴"""
    global _fred_client
    if _fred_client is None:
        if not FRED_API_KEY:
            logger.warning("[FRED] API 키 미설정. FRED 수집 건너뜀.")
            return None
        try:
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

    logger.info(
        f"[FRED] 수집 완료: 기준금리 {data['fed_funds_rate']:.2f}% | "
        f"HY 스프레드 {data['hy_spread']:.2f}% | "
        f"수익률 곡선 {data['yield_curve']:.2f}%"
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
