"""
core/validator.py (v1.5.1)
============================
Hard Gate 검증.
v1.5.1 추가: 시장 데이터 기본값(fallback) 감지 — 수집 실패 시 발행 차단.
"""
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_REQUIRED_TOP_KEYS = [
    "market_snapshot", "market_regime", "market_score",
    "etf_analysis", "etf_strategy", "etf_allocation",
    "portfolio_risk", "trading_signal", "output_helpers",
]
_REQUIRED_SNAPSHOT_FIELDS = ["sp500", "nasdaq", "vix", "us10y", "oil", "dollar_index"]
_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}
_VALID_TRADING_SIGNALS = {"BUY", "ADD", "HOLD", "REDUCE", "HEDGE", "SELL"}
_VALID_STANCES = {"Overweight", "Neutral", "Underweight", "Hedge", "Exclude"}

# ── 수집 실패 감지용 기본값 패턴 ─────────────────────────────────
# yfinance 실패 시 이 값들이 채워짐 — 실제 시장 데이터가 아님
_FALLBACK_PATTERN = {
    "sp500": 0.0,
    "nasdaq": 0.0,
    "vix": 20.0,
    "us10y": 4.0,
    "oil": 75.0,
    "dollar_index": 100.0,
}
# None이 허용되는 필드 (수집 실패 시 직접 None으로 옴)
_CRITICAL_FIELDS = ["vix", "us10y", "oil"]  # 이 3개 중 하나라도 None이면 실패


def validate_data(data: dict) -> dict:
    """JSON Core Data 구조 + 데이터 품질 검증. Hard Gate #1."""
    errors = []
    warnings = []

    # 1. 최상위 키 존재
    for key in _REQUIRED_TOP_KEYS:
        if key not in data:
            errors.append(f"missing_key:{key}")
    if errors:
        return _build_result(errors, warnings, "LOW")

    snap = data["market_snapshot"]

    # 2. 스냅샷 필드 존재 + 타입
    for field in _REQUIRED_SNAPSHOT_FIELDS:
        if field not in snap:
            errors.append(f"missing_snapshot_field:{field}")
        elif snap[field] is None:
            errors.append(f"null_field:market_snapshot.{field} — yfinance 수집 실패")
        elif not isinstance(snap[field], (int, float)):
            errors.append(f"invalid_type:market_snapshot.{field}")

    # 3. 핵심 필드 None 체크 (수집 실패 감지)
    for field in _CRITICAL_FIELDS:
        if snap.get(field) is None:
            errors.append(f"data_collection_failed:{field} — 실시간 데이터 없음, 발행 차단")

    # 4. 기본값(fallback) 전체 일치 감지
    #    sp500=0, nasdaq=0, vix=20, us10y=4, oil=75, dxy=100 이면 수집 실패 확정
    if all(
        snap.get(k) == v
        for k, v in _FALLBACK_PATTERN.items()
    ):
        errors.append(
            "data_quality:all_values_are_fallback — "
            "yfinance 전체 수집 실패. 실제 시장 데이터 없음. 발행 차단."
        )

    # 5. SPY 변동률 이상값 (정상 장 기준 ±10% 이내)
    sp500 = snap.get("sp500", 0)
    if isinstance(sp500, (int, float)) and abs(sp500) > 10:
        warnings.append(f"suspicious:sp500={sp500}% — 10% 초과 (서킷브레이커 또는 수집 오류 확인)")

    # 6. VIX 범위
    vix = snap.get("vix", 0)
    if isinstance(vix, (int, float)) and (vix <= 0 or vix > 80):
        errors.append(f"suspicious:vix={vix} — 유효 범위(1~80) 벗어남")

    # 7. allocation 총합
    alloc = data["etf_allocation"].get("allocation", {})
    total_weight = data["etf_allocation"].get("total_weight", 0)
    if not alloc:
        errors.append("missing:allocation")
    if total_weight != 100:
        errors.append(f"invalid:total_weight={total_weight}")

    # 8. Enum 검증
    risk = data["market_regime"].get("market_risk_level", "")
    if risk not in _VALID_RISK_LEVELS:
        errors.append(f"invalid_enum:market_risk_level={risk}")
    sig = data["trading_signal"].get("trading_signal", "")
    if sig not in _VALID_TRADING_SIGNALS:
        errors.append(f"invalid_enum:trading_signal={sig}")

    # ── 9. Tier 1 확장 시그널 정합성 검증 (2026-04-01 추가) ──────────
    # Market Score 범위 검증 — 모든 Score는 1~5 정수여야 함
    # 비상식적 수치 발생 시 발행 차단
    ms = data.get("market_score", {})
    _SCORE_KEYS = [
        "growth_score", "inflation_score", "liquidity_score",
        "risk_score", "financial_stability_score", "commodity_pressure_score",
    ]
    for sk in _SCORE_KEYS:
        sv = ms.get(sk)
        if sv is not None and (not isinstance(sv, (int, float)) or sv < 1 or sv > 5):
            errors.append(
                f"sanity_fail:market_score.{sk}={sv} — "
                f"유효 범위(1~5) 벗어남. 시그널 계산 오류 의심. 발행 차단."
            )

    # Tier 1 시그널 개별 범위 검증 (signals dict가 core_data에 포함된 경우)
    sigs = data.get("signals", {})
    _SIGNAL_RANGES = {
        "fear_greed_score":       (1, 5),   # T1-1
        "crypto_risk_score":      (1, 4),   # T1-2
        "equity_momentum_score":  (1, 5),   # T1-3
        "xlf_gld_score":          (1, 3),   # T1-4
        "volatility_score":       (1, 5),   # 기존
        "rate_score":             (1, 4),   # 기존
        "commodity_pressure_score": (1, 4), # 기존
        "sentiment_score":        (1, 3),   # 기존
    }
    for sig_key, (lo, hi) in _SIGNAL_RANGES.items():
        sig_val = sigs.get(sig_key)
        if sig_val is not None and (not isinstance(sig_val, (int, float)) or sig_val < lo or sig_val > hi):
            errors.append(
                f"sanity_fail:signal.{sig_key}={sig_val} — "
                f"유효 범위({lo}~{hi}) 벗어남. 발행 차단."
            )

    confidence = "HIGH" if not errors else "LOW"
    return _build_result(errors, warnings, confidence)


def validate_output(data: dict) -> dict:
    """전략 정합성 검증. Hard Gate #2."""
    errors = []
    checked = []

    regime_risk    = data["market_regime"].get("market_risk_level")
    portfolio_risk = data["portfolio_risk"].get("market_risk_level")
    checked.append("market_risk_level_consistency")
    if regime_risk != portfolio_risk:
        errors.append(
            f"mismatch:market_risk_level "
            f"regime={regime_risk} vs portfolio={portfolio_risk}"
        )

    stance = data["etf_strategy"].get("stance", {})
    alloc  = data["etf_allocation"].get("allocation", {})
    checked.append("stance_allocation_consistency")
    for etf, s in stance.items():
        weight = alloc.get(etf, 0)
        if s == "Underweight" and weight > 10:
            errors.append(f"mismatch:{etf}:Underweight but allocation={weight}%")
        if s == "Overweight" and weight < 15:
            errors.append(f"mismatch:{etf}:Overweight but allocation={weight}%")

    checked.append("tlt_allocation_guard")
    if alloc.get("TLT", 0) >= 45:
        errors.append(f"allocation_guard:TLT={alloc.get('TLT')}% — 45% 상한 초과")

    checked.append("total_weight_final_check")
    if sum(alloc.values()) != 100:
        errors.append(f"final_weight_mismatch:total={sum(alloc.values())}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "checked_fields": checked,
    }


def ensure_publish_eligible(data: dict) -> Tuple[dict, dict]:
    """validate_data + validate_output 모두 PASS여야 발행 허용."""
    data_result   = validate_data(data)
    output_result = validate_output(data)
    logger.info(
        f"[Validator] data={data_result['status']} | output={output_result['status']}"
    )
    if data_result["status"] != "PASS":
        raise ValueError(f"validate_data FAIL: {data_result['errors']}")
    if output_result["status"] != "PASS":
        raise ValueError(f"validate_output FAIL: {output_result['errors']}")
    return data_result, output_result


def _build_result(errors, warnings, confidence):
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "confidence": confidence,
    }
