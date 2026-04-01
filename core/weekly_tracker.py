"""
core/weekly_tracker.py
========================
주간 성적표용 일별 시그널 + ETF 수익률 누적 저장

저장 파일: data/published/weekly_log.json
구조:
{
  "week": "2026-W13",
  "entries": [
    {
      "date": "2026-03-25",
      "regime": "Risk-Off",
      "risk": "MEDIUM",
      "signal": "HOLD",
      "buy_watch": ["XLE","TLT"],
      "hold": ["SPYM","ITA"],
      "reduce": ["QQQM","XLK"],
      "etf_returns": {"XLE": 3.2, "TLT": 0.8, ...}  ← 당일 수익률
    },
    ...
  ]
}
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WEEKLY_LOG_PATH = Path(
    os.getenv("WEEKLY_LOG_PATH", "data/published/weekly_log.json")
)


def _iso_week(dt: datetime) -> str:
    """ISO 주차 문자열 반환 (예: 2026-W13)"""
    return dt.strftime("%Y-W%V")


def _load() -> dict:
    """weekly_log.json 로드 (없으면 빈 구조 반환)"""
    if WEEKLY_LOG_PATH.exists():
        try:
            return json.loads(WEEKLY_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"week": "", "entries": []}


def _save(log: dict) -> None:
    """weekly_log.json 저장"""
    WEEKLY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEEKLY_LOG_PATH.write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def record_daily(data: dict, dt_utc: Optional[datetime] = None) -> None:
    """
    매일 run_market 완료 후 호출 — 시그널 + ETF 수익률 기록

    Args:
        data:   core_data.json의 data 필드
        dt_utc: 기준 시각 (None이면 현재)
    """
    try:
        if dt_utc is None:
            dt_utc = datetime.now(timezone.utc)

        kst   = dt_utc + timedelta(hours=9)
        today = kst.strftime("%Y-%m-%d")
        week  = _iso_week(kst)

        log = _load()

        # 새 주차 시작 시 리셋
        if log.get("week") != week:
            log = {"week": week, "entries": []}

        # 이미 오늘 기록 있으면 업데이트
        entries = log.get("entries", [])
        existing = next((e for e in entries if e.get("date") == today), None)

        regime  = data.get("market_regime", {}).get("market_regime", "—")
        risk    = data.get("market_regime", {}).get("market_risk_level", "—")
        signal  = data.get("trading_signal", {}).get("trading_signal", "—")
        matrix  = data.get("trading_signal", {}).get("signal_matrix", {})

        # ETF 일간 수익률 추출 (yfinance 제공 시)
        etf_prices = data.get("etf_prices", {})
        etf_returns = {}
        for etf, prices in etf_prices.items():
            prev = prices.get("prev_close")
            curr = prices.get("current")
            if prev and curr and prev != 0:
                etf_returns[etf] = round((curr - prev) / prev * 100, 2)

        # ── B-8 확장: market_score + allocation + top_signals 저장 (2026-04-01) ──
        market_score = data.get("market_score", {})
        allocation = data.get("etf_allocation", {}).get("allocation", {})
        signals = data.get("signals", {})

        # 주요 시그널 Top 3 추출 (극단값 우선)
        top_signals = []
        if signals:
            _score_keys = [k for k in signals if k.endswith("_score")]
            # 중립(2)에서 먼 순서로 정렬 → 극단값 우선
            extremes = sorted(
                [(k, signals[k]) for k in _score_keys if isinstance(signals.get(k), (int, float))],
                key=lambda x: abs(x[1] - 2.5),
                reverse=True,
            )
            for sig_key, val in extremes[:3]:
                # _score → _state 매핑
                state_key = sig_key.replace("_score", "_state")
                # 일부는 다른 패턴: volatility_score → vix_state 등
                _STATE_MAP = {
                    "volatility_score": "vix_state",
                    "rate_score": "rate_environment",
                    "commodity_pressure_score": "oil_state",
                    "financial_stability_score": "credit_stress_signal",
                    "sentiment_score": "sentiment_state",
                }
                state_key = _STATE_MAP.get(sig_key, state_key)
                state = signals.get(state_key, "")
                top_signals.append({
                    "signal": sig_key,
                    "value": val,
                    "state": state,
                })

        entry = {
            "date":         today,
            "regime":       regime,
            "risk":         risk,
            "signal":       signal,
            "buy_watch":    matrix.get("buy_watch", []),
            "hold":         matrix.get("hold", []),
            "reduce":       matrix.get("reduce", []),
            "etf_returns":  etf_returns,
            # B-8 확장 필드 (2026-04-01 추가)
            "market_score": market_score,
            "allocation":   allocation,
            "top_signals":  top_signals,
        }

        if existing:
            idx = entries.index(existing)
            entries[idx] = entry
        else:
            entries.append(entry)

        log["entries"] = entries
        _save(log)
        logger.info(f"[WeeklyTracker] 기록 완료: {today} | {regime} | {signal}")

    except Exception as e:
        logger.warning(f"[WeeklyTracker] 기록 실패 (영향 없음): {e}")


def _detect_weekly_regime_changes(entries: list) -> list:
    """주간 중 레짐이 변한 날 감지"""
    changes = []
    for i in range(1, len(entries)):
        prev_r = entries[i - 1].get("regime", "")
        curr_r = entries[i].get("regime", "")
        if prev_r and curr_r and prev_r != curr_r:
            changes.append({
                "date": entries[i].get("date", ""),
                "from": prev_r,
                "to": curr_r,
            })
    return changes


def _aggregate_top_signals(entries: list) -> list:
    """모든 날의 top_signals를 병합 → 빈도 + 최대값 기준 Top 5"""
    sig_freq = {}  # signal_key → {"count": N, "max_value": V, "state": "..."}
    for e in entries:
        for ts in e.get("top_signals", []):
            key = ts.get("signal", "")
            if not key:
                continue
            if key not in sig_freq:
                sig_freq[key] = {"count": 0, "max_value": 2.5, "state": ""}
            sig_freq[key]["count"] += 1
            val = ts.get("value", 0)
            if abs(val - 2.5) >= abs(sig_freq[key]["max_value"] - 2.5):
                sig_freq[key]["max_value"] = val
                sig_freq[key]["state"] = ts.get("state", "")

    # 빈도 내림차순 → Top 5
    sorted_sigs = sorted(sig_freq.items(), key=lambda x: -x[1]["count"])
    return [
        {"signal": k, "count": v["count"], "max_value": v["max_value"], "state": v["state"]}
        for k, v in sorted_sigs[:5]
    ]


def get_weekly_summary() -> dict:
    """
    주간 성적표 집계 반환

    Returns:
        {
          "week": "2026-W13",
          "days": 5,
          "dominant_signal": "HOLD",
          "dominant_regime": "Risk-Off",
          "buy_etfs":    {"XLE": [3.2, 1.1, ...], ...},
          "hold_etfs":   {"SPYM": [...], ...},
          "reduce_etfs": {"QQQM": [...], ...},
          "etf_week_return": {"XLE": 4.5, ...},
          "signal_counts": {"BUY": 1, "HOLD": 4, "REDUCE": 0},
          "entries": [...]
        }
    """
    log     = _load()
    entries = log.get("entries", [])

    if not entries:
        return {"week": log.get("week", ""), "days": 0, "entries": []}

    # 시그널 집계
    sig_counts = {}
    regime_counts = {}
    for e in entries:
        sig = e.get("signal", "HOLD")
        reg = e.get("regime", "—")
        sig_counts[sig]    = sig_counts.get(sig, 0) + 1
        regime_counts[reg] = regime_counts.get(reg, 0) + 1

    dominant_signal = max(sig_counts, key=sig_counts.get) if sig_counts else "HOLD"
    dominant_regime = max(regime_counts, key=regime_counts.get) if regime_counts else "—"

    # ETF 수익률 집계
    ETFS = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]
    etf_week_return = {}
    for etf in ETFS:
        returns = [
            e.get("etf_returns", {}).get(etf, 0)
            for e in entries
            if etf in e.get("etf_returns", {})
        ]
        if returns:
            etf_week_return[etf] = round(sum(returns), 2)

    # BUY/HOLD/REDUCE 빈도
    buy_count    = {e: 0 for e in ETFS}
    hold_count   = {e: 0 for e in ETFS}
    reduce_count = {e: 0 for e in ETFS}
    for e in entries:
        for etf in e.get("buy_watch", []):
            buy_count[etf] = buy_count.get(etf, 0) + 1
        for etf in e.get("hold", []):
            hold_count[etf] = hold_count.get(etf, 0) + 1
        for etf in e.get("reduce", []):
            reduce_count[etf] = reduce_count.get(etf, 0) + 1

    return {
        "week":             log.get("week", ""),
        "days":             len(entries),
        "dominant_signal":  dominant_signal,
        "dominant_regime":  dominant_regime,
        "signal_counts":    sig_counts,
        "buy_count":        {k: v for k, v in buy_count.items() if v > 0},
        "hold_count":       {k: v for k, v in hold_count.items() if v > 0},
        "reduce_count":     {k: v for k, v in reduce_count.items() if v > 0},
        "etf_week_return":  etf_week_return,
        "entries":          entries,
        # ── B-8 확장 (2026-04-01 추가) ──
        # 일별 Market Score 이력 (Score 추이 테이블용)
        "daily_scores":     [
            {"date": e.get("date", ""), **e.get("market_score", {})}
            for e in entries if e.get("market_score")
        ],
        # 주초/주말 allocation 비교
        "allocation_start": entries[0].get("allocation", {}) if entries else {},
        "allocation_end":   entries[-1].get("allocation", {}) if entries else {},
        # 주간 레짐 전환 이벤트 (레짐이 변한 날 목록)
        "regime_changes":   _detect_weekly_regime_changes(entries),
        # 주간 주요 시그널 (모든 날의 top_signals 병합 → 빈도순)
        "weekly_top_signals": _aggregate_top_signals(entries),
    }


def get_ai_scorecard(summary: dict) -> dict:
    """
    AI 예측 vs 실제 수익률 비교 성적표 생성

    Returns:
        {
          "correct":   [{"etf": "XLE", "signal": "BUY", "return": 3.2}],
          "incorrect": [{"etf": "QQQM", "signal": "REDUCE", "return": 1.1, "reason": "관세완화 미반영"}],
          "hit_rate":  0.67,    # 적중률
          "total":     3,
        }
    """
    returns  = summary.get("etf_week_return", {})
    buy_c    = summary.get("buy_count", {})
    reduce_c = summary.get("reduce_count", {})

    correct   = []
    incorrect = []

    # BUY 시그널 — 수익률 양수면 적중
    for etf, days in buy_c.items():
        ret = returns.get(etf)
        if ret is None:
            continue
        if ret > 0:
            correct.append({"etf": etf, "signal": "BUY", "return": ret, "days": days})
        else:
            incorrect.append({
                "etf": etf, "signal": "BUY", "return": ret, "days": days,
                "reason": "예상과 달리 하락"
            })

    # REDUCE 시그널 — 수익률 음수면 적중
    for etf, days in reduce_c.items():
        ret = returns.get(etf)
        if ret is None:
            continue
        if ret < 0:
            correct.append({"etf": etf, "signal": "REDUCE", "return": ret, "days": days})
        else:
            incorrect.append({
                "etf": etf, "signal": "REDUCE", "return": ret, "days": days,
                "reason": "예상과 달리 상승"
            })

    total    = len(correct) + len(incorrect)
    hit_rate = round(len(correct) / total, 2) if total > 0 else 0.0

    # 수익률 기준 정렬
    correct   = sorted(correct,   key=lambda x: -abs(x["return"]))
    incorrect = sorted(incorrect, key=lambda x:  abs(x["return"]))

    return {
        "correct":   correct,
        "incorrect": incorrect,
        "hit_rate":  hit_rate,
        "total":     total,
    }
