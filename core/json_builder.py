"""
core/json_builder.py
14_execution_engine.md Single Source of Truth 구현.
모든 엔진 결과를 JSON Core Data로 조립한다.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from config.settings import (
    SYSTEM_NAME, SYSTEM_VERSION,
    CORE_DATA_FILE, VALIDATION_FILE, PUBLISH_PAYLOAD_FILE,
)

logger = logging.getLogger(__name__)


def build_envelope(command: str, data: dict) -> dict:
    """JSON Core Data Envelope 생성 (10_output_schema.md 기준)"""
    return {
        "system": SYSTEM_NAME,
        "command": command,
        "version": SYSTEM_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "data": data,
    }


def assemble_core_data(
    snapshot: dict,
    market_regime: dict,
    market_score: dict,
    etf_analysis: dict,
    etf_strategy: dict,
    etf_allocation: dict,
    portfolio_risk: dict,
    trading_signal: dict,
    output_helpers: dict,
    fx_rates: dict = None,
    fear_greed: dict = None,
    crypto: dict = None,
    news_summary: dict = None,
) -> dict:
    """
    각 엔진 출력을 단일 data dict로 조립.
    모든 Output 엔진(run_view 등)은 이 dict만 참조한다.
    """
    return {
        "fx_rates":       fx_rates or {},
        "fear_greed":     fear_greed or {},
        "crypto":         crypto or {},
        "news_summary":   news_summary or {},
        "market_snapshot": snapshot,
        "market_regime":  market_regime,
        "market_score":   market_score,
        "etf_analysis":   etf_analysis,
        "etf_strategy":   etf_strategy,
        "etf_allocation": etf_allocation,
        "portfolio_risk": portfolio_risk,
        "trading_signal": trading_signal,
        "output_helpers": output_helpers,
    }


def save_core_data(envelope: dict, data_validation: dict, output_validation: dict) -> None:
    """
    JSON Core Data + 검증 결과를 파일로 저장.
    run_view.py가 이 파일을 읽어 발행한다.
    """
    # core_data.json
    with open(CORE_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2)
    logger.info(f"[Builder] core_data.json 저장: {CORE_DATA_FILE}")

    # validation_result.json
    with open(VALIDATION_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"data_validation": data_validation, "output_validation": output_validation},
            f, ensure_ascii=False, indent=2,
        )

    # publish_payload.json
    publish_ready = (
        data_validation.get("status") == "PASS"
        and output_validation.get("status") == "PASS"
    )
    with open(PUBLISH_PAYLOAD_FILE, "w", encoding="utf-8") as f:
        json.dump({"publish_ready": publish_ready}, f, ensure_ascii=False, indent=2)

    logger.info(f"[Builder] publish_ready={publish_ready}")


def load_core_data() -> dict:
    """
    run_view.py 전용.
    저장된 core_data.json을 로드하여 data dict를 반환한다.
    """
    if not CORE_DATA_FILE.exists():
        raise FileNotFoundError(
            f"core_data.json 없음: {CORE_DATA_FILE}\n"
            "run_market.py를 먼저 실행하세요."
        )
    with open(CORE_DATA_FILE, "r", encoding="utf-8") as f:
        envelope = json.load(f)

    logger.info(
        f"[Builder] core_data.json 로드 완료 "
        f"(timestamp={envelope.get('timestamp', 'unknown')})"
    )
    return envelope
