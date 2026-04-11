"""
core/json_builder.py (v1.1.0)
14_execution_engine.md Single Source of Truth 구현.
모든 엔진 결과를 JSON Core Data로 조립한다.

변경이력:
  v1.1.0 (2026-04-07) Phase 1A — crypto_basis, btc_sentiment 파라미터 추가
  v1.0.x 기존 로직
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

# 변경
VERSION = "1.2.0"


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
    macro_data: dict = None,
    signals: dict = None,
    news_analysis: dict = None,
    crypto_basis: dict = None,
    btc_sentiment: dict = None,
    spy_sma_data: dict = None,  # ← 신규 추가 (Priority A)
) -> dict:

  
    """
    각 엔진 출력을 단일 data dict로 조립.
    모든 Output 엔진(run_view 등)은 이 dict만 참조한다.

    2026-04-01 추가: signals (19개 시그널 dict)
    2026-04-02 추가: news_analysis (B-16 Gemini 뉴스 심층 분석)
    2026-04-07 추가: crypto_basis, btc_sentiment (Phase 1A T4-1, T4-4)
                    → signals dict에 병합되어 저장
    """
    # ── Phase 1A: 신규 시그널을 signals dict에 병합 ──
    merged_signals = dict(signals or {})

    # T4-1 Crypto Basis Spread
    if crypto_basis is None:
        crypto_basis = {
            "basis_spread": None,
            "state": "Unknown",
            "score": 2,
            "mark": None,
            "index": None,
        }
    merged_signals.update({
        "crypto_basis_spread": crypto_basis.get("basis_spread"),
        "crypto_basis_state":  crypto_basis.get("state", "Unknown"),
        "crypto_basis_score":  crypto_basis.get("score", 2),
        "crypto_basis_mark":   crypto_basis.get("mark"),
        "crypto_basis_index":  crypto_basis.get("index"),
    })

    # T4-4 BTC Social Sentiment
    if btc_sentiment is None:
        btc_sentiment = {
            "sentiment": None,
            "state": "Unknown",
            "score": 2,
            "themes_supportive": "",
            "themes_critical": "",
        }
    merged_signals.update({
        "btc_social_sentiment":            btc_sentiment.get("sentiment"),
        "btc_sentiment_state":             btc_sentiment.get("state", "Unknown"),
        "btc_sentiment_score":             btc_sentiment.get("score", 2),
        "btc_sentiment_themes_supportive": btc_sentiment.get("themes_supportive", ""),
        "btc_sentiment_themes_critical":   btc_sentiment.get("themes_critical", ""),
    })

    logger.info(
        f"[Builder v{VERSION}] signals 병합 완료 "
        f"(기존 {len(signals or {})}개 + Phase 1A 10개 = {len(merged_signals)}개)"
    )

    return {
        "fx_rates":       fx_rates or {},
        "fear_greed":     fear_greed or {},
        "crypto":         crypto or {},
        "news_summary":   news_summary or {},
        "news_analysis":  news_analysis or {},
        "macro_data":     macro_data or {},
        "market_snapshot": snapshot,
        "market_regime":  market_regime,
        "market_score":   market_score,
        "signals":        merged_signals,
        "etf_analysis":   etf_analysis,
        "etf_strategy":   etf_strategy,
        "etf_allocation": etf_allocation,
        "portfolio_risk": portfolio_risk,
        "trading_signal": trading_signal,
        "output_helpers": output_helpers,
        "spy_sma":        spy_sma_data or {},   # ← return dict에 추가
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
