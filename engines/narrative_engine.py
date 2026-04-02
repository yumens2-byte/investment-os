"""
engines/narrative_engine.py (B-15)
====================================
Gemini 기반 시장 내러티브 자동 생성

19개 시그널 + Regime + Market Score → Gemini Flash-Lite로
한국어 시장 해설 3~5줄 자동 생성.

발행 채널: X + TG 무료/유료
발행 시점: 11:30 KST (UTC 02:30)
Fallback:  Gemini 실패 시 기존 rule-based summary 유지
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 시그널 한국어 라벨
_SIGNAL_LABEL = {
    "volatility_score": "VIX", "rate_score": "금리",
    "commodity_pressure_score": "유가", "financial_stability_score": "금융안정",
    "sentiment_score": "시장심리", "fear_greed_score": "공포탐욕",
    "crypto_risk_score": "BTC", "equity_momentum_score": "주가모멘텀",
    "xlf_gld_score": "금융/금", "breadth_score": "시장참여도",
    "vol_term_score": "변동성구조", "claims_score": "실업수당",
    "infl_exp_score": "기대인플레", "em_stress_score": "신흥국",
    "ai_momentum_score": "AI모멘텀", "nasdaq_rel_score": "나스닥상대",
    "banking_stress_score": "은행스트레스",
}

SYSTEM_INSTRUCTION = """당신은 미국 금융시장 전문 애널리스트입니다.
규칙:
1. 투자 권유 금지 — 정보 제공만
2. 팩트 기반 분석만 작성
3. 한국어 3~5줄로 작성
4. 전문 용어 사용 가능 (일반 투자자 대상)
5. 이모지 사용 금지
6. 시장 레짐 전환 근거를 명확히 설명"""


def _build_prompt(data: dict) -> str:
    """core_data에서 프롬프트 생성"""
    snapshot = data.get("market_snapshot", {})
    regime_info = data.get("market_regime", {})
    ms = data.get("market_score", {})
    signals = data.get("signals", {})
    ts = data.get("trading_signal", {})
    alloc = data.get("etf_allocation", {}).get("allocation", {})

    # 극단값 시그널 추출 (|v-2.5| 기준 Top 5)
    score_keys = [k for k in signals if k.endswith("_score") and isinstance(signals.get(k), (int, float))]
    extremes = sorted(score_keys, key=lambda k: abs(signals[k] - 2.5), reverse=True)[:5]
    extreme_lines = []
    for k in extremes:
        label = _SIGNAL_LABEL.get(k, k)
        state_map = {
            "volatility_score": "vix_state", "rate_score": "rate_environment",
            "commodity_pressure_score": "oil_state", "financial_stability_score": "credit_stress_signal",
            "sentiment_score": "sentiment_state",
        }
        sk = state_map.get(k, k.replace("_score", "_state"))
        state = signals.get(sk, "")
        extreme_lines.append(f"  - {label}: {signals[k]} ({state})")

    prompt = f"""다음 미국 금융시장 데이터를 분석하여 한국어 시장 해설을 작성하세요.

[시장 스냅샷]
  SPY: {snapshot.get('sp500', 0):+.2f}% | VIX: {snapshot.get('vix', 0)} | US10Y: {snapshot.get('us10y', 0)}%
  WTI: ${snapshot.get('oil', 0)} | DXY: {snapshot.get('dollar_index', 0)}

[시장 레짐]
  레짐: {regime_info.get('market_regime', '?')}
  Risk Level: {regime_info.get('market_risk_level', '?')}
  근거: {regime_info.get('regime_reason', '?')}

[Market Score (1~5, 높을수록 위험)]
  Growth: {ms.get('growth_score', '?')} | Inflation: {ms.get('inflation_score', '?')}
  Liquidity: {ms.get('liquidity_score', '?')} | Risk: {ms.get('risk_score', '?')}
  Stability: {ms.get('financial_stability_score', '?')} | Commodity: {ms.get('commodity_pressure_score', '?')}

[주요 시그널 (극단값 Top 5)]
{chr(10).join(extreme_lines)}

[트레이딩 시그널]
  Signal: {ts.get('trading_signal', '?')}
  BUY Watch: {', '.join(ts.get('signal_matrix', {}).get('buy_watch', []))}
  Reduce: {', '.join(ts.get('signal_matrix', {}).get('reduce', []))}

[ETF 배분]
  {' | '.join(f'{e} {v}%' for e, v in sorted(alloc.items(), key=lambda x: -x[1]))}

위 데이터를 종합하여 한국어 3~5줄 시장 해설을 작성하세요.
조건: 투자 권유 금지, 팩트 기반, 레짐 전환 근거 명확히."""

    return prompt


def generate_narrative(data: dict) -> dict:
    """
    Gemini로 시장 내러티브 생성.

    Args:
        data: core_data.json의 data 필드

    Returns:
        {
          "success": True/False,
          "narrative": "한국어 시장 해설 3~5줄",
          "source": "gemini" | "fallback",
        }
    """
    from core.gemini_gateway import call, is_available

    # Gemini 미설정 시 fallback
    if not is_available():
        logger.info("[Narrative] Gemini 미설정 → fallback")
        return _fallback_narrative(data)

    prompt = _build_prompt(data)

    result = call(
        prompt=prompt,
        model="flash-lite",
        system_instruction=SYSTEM_INSTRUCTION,
        max_tokens=512,
        temperature=0.7,
        response_json=False,
        fallback_value=None,
    )

    if result["success"] and result["text"]:
        narrative = result["text"].strip()
        # 너무 길면 자르기 (5줄 이내)
        lines = narrative.split("\n")
        if len(lines) > 7:
            narrative = "\n".join(lines[:5])

        logger.info(f"[Narrative] Gemini 생성 완료 ({len(narrative)}자)")
        return {
            "success": True,
            "narrative": narrative,
            "source": "gemini",
        }

    # Gemini 실패 → fallback
    logger.warning(f"[Narrative] Gemini 실패 → fallback: {result.get('error', '?')[:80]}")
    return _fallback_narrative(data)


def _fallback_narrative(data: dict) -> dict:
    """Gemini 실패 시 기존 rule-based summary 사용"""
    summary = data.get("output_helpers", {}).get("one_line_summary", "")
    regime = data.get("market_regime", {}).get("market_regime", "")
    risk = data.get("market_regime", {}).get("market_risk_level", "")
    signal = data.get("trading_signal", {}).get("trading_signal", "")

    narrative = f"{regime} 국면 | Risk {risk} | Signal {signal}\n{summary}"

    return {
        "success": True,
        "narrative": narrative,
        "source": "fallback",
    }


def format_narrative_tweet(narrative: str) -> str:
    """내러티브를 X 트윗용 280자로 포맷"""
    tweet = f"📝 AI 시장 해설\n\n{narrative}\n\n#ETF #투자 #미국증시 #AI분석"
    if len(tweet) > 280:
        # 초과 시 내러티브 자르기
        max_narr = 280 - len("📝 AI 시장 해설\n\n\n\n#ETF #투자 #미국증시 #AI분석") - 3
        tweet = f"📝 AI 시장 해설\n\n{narrative[:max_narr]}...\n\n#ETF #투자 #미국증시 #AI분석"
    return tweet


def format_narrative_telegram(narrative: str, data: dict) -> str:
    """내러티브를 TG 포맷으로 확장"""
    regime = data.get("market_regime", {}).get("market_regime", "")
    risk = data.get("market_regime", {}).get("market_risk_level", "")
    signal = data.get("trading_signal", {}).get("trading_signal", "")
    snapshot = data.get("market_snapshot", {})

    return (
        f"📝 <b>AI 시장 해설</b>\n\n"
        f"📊 SPY {snapshot.get('sp500', 0):+.1f}% | VIX {snapshot.get('vix', 0)} | "
        f"WTI ${snapshot.get('oil', 0):.0f}\n"
        f"🔄 {regime} | {risk} | {signal}\n\n"
        f"─────────────────\n"
        f"{narrative}\n"
        f"─────────────────\n\n"
        f"<i>Powered by Investment OS + Gemini AI</i>"
    )
