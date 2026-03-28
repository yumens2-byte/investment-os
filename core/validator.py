"""
core/validator.py
10_output_schema.md + 14_execution_engine.md Hard Gates 구현.
validate_data / validate_output — 하나라도 FAIL이면 발행 차단.
"""
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# 필수 최상위 키 (10_output_schema.md 기준)
_REQUIRED_TOP_KEYS = [
    "market_snapshot",
    "market_regime",
    "market_score",
    "etf_analysis",
    "etf_strategy",
    "etf_allocation",
    "portfolio_risk",
    "trading_signal",
    "output_helpers",
]

# market_snapshot 필수 필드
_REQUIRED_SNAPSHOT_FIELDS = ["sp500", "nasdaq", "vix", "us10y", "oil", "dollar_index"]

# 유효한 Enum 값
_VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}
_VALID_TRADING_SIGNALS = {"BUY", "ADD", "HOLD", "REDUCE", "HEDGE", "SELL"}
_VALID_STANCES = {"Overweight", "Neutral", "Underweight", "Hedge", "Exclude"}


# ──────────────────────────────────────────────────────────────
# 1. validate_data
# ──────────────────────────────────────────────────────────────

def validate_data(data: dict) -> dict:
    """
    JSON Core Data 구조 및 필수 필드 검증.
    Hard Gate #1: FAIL이면 output block.
    """
    errors = []
    warnings = []

    # 1-1. 최상위 키 존재 여부
    for key in _REQUIRED_TOP_KEYS:
        if key not in data:
            errors.append(f"missing_key:{key}")

    if errors:
        # 구조 자체가 없으면 이후 검증 의미 없음
        return _build_result(errors, warnings, confidence="LOW")

    # 1-2. market_snapshot 필드 검증
    snap = data["market_snapshot"]
    for field in _REQUIRED_SNAPSHOT_FIELDS:
        if field not in snap:
            errors.append(f"missing_snapshot_field:{field}")
        elif not isinstance(snap[field], (int, float)):
            errors.append(f"invalid_type:market_snapshot.{field}")

    # 1-3. allocation 합계 100% 강제
    alloc = data["etf_allocation"].get("allocation", {})
    total_weight = data["etf_allocation"].get("total_weight", 0)

    if not alloc:
        errors.append("missing:allocation")
    if total_weight != 100:
        errors.append(f"invalid:total_weight={total_weight} (must be 100)")

    # 1-4. market_risk_level Enum 검증
    risk = data["market_regime"].get("market_risk_level", "")
    if risk not in _VALID_RISK_LEVELS:
        errors.append(f"invalid_enum:market_risk_level={risk}")

    # 1-5. trading_signal Enum 검증
    sig = data["trading_signal"].get("trading_signal", "")
    if sig not in _VALID_TRADING_SIGNALS:
        errors.append(f"invalid_enum:trading_signal={sig}")

    # 경고: VIX 값 범위 체크 (경고는 발행 차단 안 함)
    vix = snap.get("vix", 20)
    if vix <= 0 or vix > 80:
        warnings.append(f"suspicious:vix={vix} (expected 10~80)")

    confidence = "HIGH" if not errors else "LOW"
    return _build_result(errors, warnings, confidence)


# ──────────────────────────────────────────────────────────────
# 2. validate_output
# ──────────────────────────────────────────────────────────────

def validate_output(data: dict) -> dict:
    """
    전략 일관성 검증 (Governance Rule).
    Hard Gate #2: FAIL이면 publish block.

    Rules:
      - market_risk_level: market_regime vs portfolio_risk 일치
      - Underweight: allocation <= 10
      - Overweight: allocation >= 15
      - TLT: allocation < 45 (극단 방어 방지)
    """
    errors = []
    checked = []

    # 2-1. market_risk_level 일치
    regime_risk = data["market_regime"].get("market_risk_level")
    portfolio_risk = data["portfolio_risk"].get("market_risk_level")
    checked.append("market_risk_level_consistency")
    if regime_risk != portfolio_risk:
        errors.append(
            f"mismatch:market_risk_level "
            f"regime={regime_risk} vs portfolio={portfolio_risk}"
        )

    # 2-2. Stance vs Allocation 정합성
    stance = data["etf_strategy"].get("stance", {})
    alloc = data["etf_allocation"].get("allocation", {})
    checked.append("stance_allocation_consistency")

    for etf, s in stance.items():
        weight = alloc.get(etf, 0)
        if s == "Underweight" and weight > 10:
            errors.append(
                f"mismatch:{etf}:stance=Underweight but allocation={weight}%"
            )
        if s == "Overweight" and weight < 15:
            errors.append(
                f"mismatch:{etf}:stance=Overweight but allocation={weight}%"
            )

    # 2-3. TLT 극단 배분 방지
    checked.append("tlt_allocation_guard")
    tlt_weight = alloc.get("TLT", 0)
    if tlt_weight >= 45:
        errors.append(
            f"allocation_guard:TLT={tlt_weight}% — exceeds 45% ceiling"
        )

    # 2-4. 총합 재확인
    checked.append("total_weight_final_check")
    if sum(alloc.values()) != 100:
        errors.append(f"final_weight_mismatch:total={sum(alloc.values())}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "checked_fields": checked,
    }


# ──────────────────────────────────────────────────────────────
# 3. 발행 가능 여부 최종 판단
# ──────────────────────────────────────────────────────────────

def ensure_publish_eligible(data: dict) -> Tuple[dict, dict]:
    """
    validate_data + validate_output 모두 PASS여야 발행 허용.
    하나라도 FAIL이면 ValueError raise.
    Returns: (data_result, output_result)
    """
    data_result = validate_data(data)
    output_result = validate_output(data)

    logger.info(
        f"[Validator] data={data_result['status']} | "
        f"output={output_result['status']}"
    )

    if data_result["status"] != "PASS":
        raise ValueError(f"validate_data FAIL: {data_result['errors']}")
    if output_result["status"] != "PASS":
        raise ValueError(f"validate_output FAIL: {output_result['errors']}")

    return data_result, output_result


# ──────────────────────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────────────────────

def _build_result(errors: list, warnings: list, confidence: str) -> dict:
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "confidence": confidence,
    }
