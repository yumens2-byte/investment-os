"""
collectors/fred_client.py (v1.3.0)
====================================
FRED API에서 거시경제 지표를 수집한다.
공식 무료 API. 데이터 갱신 주기: 일~주 단위.

v1.3.0 (2026-04-11) Priority A — us2y(DGS2), spread_2y10y_bp 추가
v1.1.0 (2026-04-07) — 다중 BUGFIX (운영 사고 위험 6건 수정)
─────────────────────────────────────────────────────────────
BUG-F1 🚨 FEDFUNDS 하드코딩 fallback 5.25% 제거 (HIGH)
BUG-F2 🚨 hy_spread / yield_curve 하드코딩 fallback 제거 (HIGH)
BUG-F3 🚨 ICSA 이중 fetch 제거 (API quota 낭비 + 코드 중복)
BUG-F4 🚨 T5YIFR 이중 fetch 제거 (BUG-F3 동일 패턴)
BUG-F5 🚨 1차 ICSA fetch에 단위 변환 누락 수정
BUG-F6 🚨 detect_macro_changes() false alarm 차단
"""
import logging
from typing import Optional
from config.settings import FRED_API_KEY, FRED_SERIES

VERSION = "1.4.0"

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

def _fetch_yoy_change(series_id: str) -> Optional[float]:
    """YoY 변화율 계산 (%). 최신값과 12개월 전 값 비교."""
    client = _get_client()
    if client is None:
        return None
    try:
        series = client.get_series(series_id)
        if series is None or series.empty:
            return None
        series = series.dropna()
        if len(series) < 13:
            return None
        current  = float(series.iloc[-1])
        prev_12m = float(series.iloc[-13])
        if prev_12m == 0:
            return None
        return round((current - prev_12m) / prev_12m * 100, 2)
    except Exception as e:
        logger.error(f"[FRED] {series_id} YoY 계산 실패: {e}")
        return None


def _fetch_mom_change(series_id: str) -> Optional[float]:
    """MoM 절대 변화량. 최신값 - 직전값 (단위: 시리즈 원단위)."""
    client = _get_client()
    if client is None:
        return None
    try:
        series = client.get_series(series_id)
        if series is None or series.empty:
            return None
        series = series.dropna()
        if len(series) < 2:
            return None
        return round(float(series.iloc[-1]) - float(series.iloc[-2]), 1)
    except Exception as e:
        logger.error(f"[FRED] {series_id} MoM 계산 실패: {e}")
        return None


def collect_macro_data() -> dict:
    """
    FRED 거시경제 데이터 수집.
    수집 실패 시 None 반환 (stale fallback 사용 금지).

    Returns:
        macro_data dict
    """
    logger.info(f"[FRED v{VERSION}] 거시경제 데이터 수집 시작")

    # ── Tier 1: 핵심 거시 지표 ──────────────────────────────
    fed_rate    = _fetch_latest(FRED_SERIES["fed_funds_rate"])
    hy_spread   = _fetch_latest(FRED_SERIES["hy_spread"])
    yield_curve = _fetch_latest(FRED_SERIES["yield_curve"])

    # ── Tier 2: 노동시장 + 인플레이션 기대 ─────────────────
    initial_claims_raw = _fetch_latest(FRED_SERIES.get("initial_claims", "ICSA"))
    if initial_claims_raw is not None and initial_claims_raw > 1000:
        initial_claims = initial_claims_raw / 1000.0
    else:
        initial_claims = initial_claims_raw
    if initial_claims is not None:
        logger.info(f"[FRED] 신규 실업수당 청구: {initial_claims:.0f}K")
    else:
        logger.warning("[FRED] ICSA 수집 실패 → None (엔진에서 중립 처리)")

    inflation_exp = _fetch_latest(FRED_SERIES.get("inflation_exp", "T5YIFR"))
    if inflation_exp is not None:
        logger.info(f"[FRED] 기대 인플레이션: {inflation_exp:.2f}%")
    else:
        logger.warning("[FRED] T5YIFR 수집 실패 → None (엔진에서 중립 처리)")

    # ── Priority A: 2Y Treasury Yield (DGS2) ────────────────
    us2y = _fetch_latest(FRED_SERIES.get("us2y", "DGS2"))
    if us2y is not None:
        logger.info(f"[FRED] 2Y Treasury Yield: {us2y:.3f}%")
    else:
        logger.warning("[FRED] DGS2 수집 실패 → None (엔진에서 중립 처리)")

    # ── 신규 (v1.4.0): 10Y / 30Y Treasury Yield 절대값 ──────
    us10y = _fetch_latest(FRED_SERIES.get("us10y", "DGS10"))
    if us10y is not None:
        logger.info(f"[FRED] 10Y Treasury Yield: {us10y:.3f}%")
    else:
        logger.warning("[FRED] DGS10 수집 실패 → None (엔진에서 중립 처리)")

    us30y = _fetch_latest(FRED_SERIES.get("us30y", "DGS30"))
    if us30y is not None:
        logger.info(f"[FRED] 30Y Treasury Yield: {us30y:.3f}%")
    else:
        logger.warning("[FRED] DGS30 수집 실패 → None (엔진에서 중립 처리)")

    # ── Priority B: CPI + NFP + Fed Balance Sheet + SOFR ────
    cpi_yoy  = _fetch_yoy_change(FRED_SERIES.get("cpi",      "CPIAUCSL"))
    core_cpi_yoy = _fetch_yoy_change(FRED_SERIES.get("core_cpi", "CPILFESL"))
    nfp_change   = _fetch_mom_change(FRED_SERIES.get("nfp",   "PAYEMS"))
    unemployment = _fetch_latest(FRED_SERIES.get("unemployment", "UNRATE"))
    fed_bs_raw   = _fetch_latest(FRED_SERIES.get("fed_balance_sheet", "WALCL"))
    fed_bs_change_bn = _fetch_mom_change(FRED_SERIES.get("fed_balance_sheet", "WALCL"))
    sofr         = _fetch_latest(FRED_SERIES.get("sofr", "SOFR"))

    if cpi_yoy is not None:
        logger.info(f"[FRED] CPI YoY: {cpi_yoy:.2f}% | Core CPI YoY: {_fmt_pct(core_cpi_yoy)}")
    if nfp_change is not None:
        logger.info(f"[FRED] NFP MoM: {nfp_change:+.0f}K | 실업률: {_fmt_pct(unemployment)}")
    if fed_bs_change_bn is not None:
        logger.info(f"[FRED] 연준자산 주간변화: {fed_bs_change_bn/1000:+.1f}B | SOFR: {_fmt_pct(sofr)}")

    # ── 결과 dict 조립 ──────────────────────────────────────
    # ★ stale 하드코딩 fallback 절대 금지 (BUG-F1, F2 fix)
    data = {
        "fed_funds_rate":      fed_rate,
        "hy_spread":           hy_spread,
        "yield_curve":         yield_curve,
        "credit_stress":       _classify_credit_stress(hy_spread),
        "yield_curve_inverted": (yield_curve is not None and yield_curve < 0),
        "initial_claims":      initial_claims,
        "inflation_exp":       inflation_exp,
        "us2y":                us2y,
        # ── Priority B ──────────────────────────────────────
        "cpi_yoy":          cpi_yoy,
        "core_cpi_yoy":     core_cpi_yoy,
        "nfp_change":       nfp_change,        # MoM 변화량 (천 명)
        "unemployment":     unemployment,       # 실업률 (%)
        "fed_balance_bn":   round(fed_bs_raw / 1000, 1) if fed_bs_raw else None,  # 10억 달러
        "fed_bs_change_bn": round(fed_bs_change_bn / 1000, 1) if fed_bs_change_bn else None,
        "sofr":             sofr,
    }

    # ── Priority A: 10Y-2Y 스프레드 bp 계산 ─────────────────
    # yield_curve (T10Y2Y) = 10Y - 2Y (FRED 기본 계산값)
    # bp 단위로 변환하여 콘텐츠/시그널에서 활용
    if data.get("yield_curve") is not None:
        data["spread_2y10y_bp"] = round(data["yield_curve"] * 100, 1)
    else:
        data["spread_2y10y_bp"] = None

    # ── 신규 (v1.4.0): 10Y / 30Y 절대값 + 스프레드 ──────────
    data["us10y"] = us10y
    data["us30y"] = us30y

    # 2Y-30Y 스프레드 (장기 커브): us30y, us2y 모두 있을 때만 계산
    if us30y is not None and us2y is not None:
        data["spread_2y30y_bp"] = round((us30y - us2y) * 100, 1)
    else:
        data["spread_2y30y_bp"] = None

    # 10Y-30Y 스프레드 (장기 기울기): us30y, us10y 모두 있을 때만 계산
    if us30y is not None and us10y is not None:
        data["spread_10y30y_bp"] = round((us30y - us10y) * 100, 1)
    else:
        data["spread_10y30y_bp"] = None

    logger.info(
        f"[FRED v{VERSION}] 수집 완료: 기준금리 {_fmt_pct(fed_rate)} | "
        f"HY {_fmt_pct(hy_spread)} | "
        f"2Y {_fmt_pct(us2y)} | 10Y {_fmt_pct(us10y)} | 30Y {_fmt_pct(us30y)} | "
        f"2Y-10Y {data.get('spread_2y10y_bp')}bp | "
        f"2Y-30Y {data.get('spread_2y30y_bp')}bp | "
        f"10Y-30Y {data.get('spread_10y30y_bp')}bp"
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

    Returns:
        변화 감지된 항목 리스트
        [{"indicator_id": str, "prev": float, "new": float, "change": float}]
    """
    if not current or not prev:
        return []

    DEFAULTS = {
        "fed_funds_rate": 0.25,
        "hy_spread":      0.5,
        "yield_curve":    0.3,
    }
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
