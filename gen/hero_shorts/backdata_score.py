"""
gen/hero_shorts/backdata_score.py — 백데이터 사후 검증 채점기
==============================================================
마스터 승인(2026-10-13). JCU 시장 콜(라벨·위험점수)의 적중 여부를
T+1 / T+5 / T+20 종가로 사후 채점한다.

규율(사전 고정 — 결과 보고 기준 변경 금지):
    - 채점 기준은 발행 시점에 고정한다.
    - 사후 검증 전용 — 미래 예측 표현 금지.
    - 데이터 수집 실패 회차는 unscored 로 기록한다(임의 보정 금지).

지표 정의:
    pct_change = (future_close - base_close) / base_close * 100
    |pct_change| <= FLAT_BAND_PCT  => flat
    라벨 → 기대 방향: risk_on → up / risk_off → down / neutral → flat

주의: FLAT_BAND_PCT 는 설계 파라미터다. 값을 바꾸면 과거 채점을 재계산하지 않는다(기준 고정).
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("hero_shorts.backdata_score")

VERSION = "1.0.0"
HORIZONS = (1, 5, 20)
FLAT_BAND_PCT = 0.5
DEFAULT_SYMBOL = "^GSPC"

LABEL_DIRECTION = {
    "risk_on": "up",
    "neutral": "flat",
    "risk_off": "down",
}

HIT = "hit"
MISS = "miss"
UNSCORED = "unscored"


def record_call(date, label, risk_score, base_close, symbol=DEFAULT_SYMBOL):
    """발행 시점 콜 스냅샷. label 은 risk_on|neutral|risk_off."""
    if label not in LABEL_DIRECTION:
        raise ValueError(f"미정의 라벨 '{label}' — 허용: {sorted(LABEL_DIRECTION)}")
    if base_close is None or base_close <= 0:
        raise ValueError("base_close 는 양수여야 한다")
    return {
        "date": date,
        "label": label,
        "expected_direction": LABEL_DIRECTION[label],
        "risk_score": risk_score,
        "symbol": symbol,
        "base_close": float(base_close),
        "version": VERSION,
    }


def expected_direction(label):
    return LABEL_DIRECTION[label]


def observed_direction(pct_change, flat_band=FLAT_BAND_PCT):
    """실제 방향 판정 — 경계 포함(<= flat, >= -flat)."""
    if pct_change > flat_band:
        return "up"
    if pct_change < -flat_band:
        return "down"
    return "flat"


def score_horizon(call, horizon, future_close, flat_band=FLAT_BAND_PCT):
    """단일 지평 채점. future_close 가 None 이면 unscored."""
    if horizon not in HORIZONS:
        raise ValueError(f"미정의 지평 {horizon} — 허용: {list(HORIZONS)}")
    base = call["base_close"]
    if future_close is None:
        log.warning("T+%d 종가 결손 — unscored 기록(%s)", horizon, call["date"])
        return {"horizon": horizon, "verdict": UNSCORED, "reason": "future_close 없음"}
    pct = (float(future_close) - base) / base * 100.0
    obs = observed_direction(pct, flat_band)
    exp = expected_direction(call["label"])
    return {
        "horizon": horizon,
        "future_close": float(future_close),
        "pct_change": round(pct, 4),
        "expected": exp,
        "observed": obs,
        "verdict": HIT if exp == obs else MISS,
    }


def score_call(call, closes_by_horizon, flat_band=FLAT_BAND_PCT):
    """콜 1건 전체 채점. closes_by_horizon = {1: close, 5: close, 20: close}."""
    verdicts = [score_horizon(call, h, closes_by_horizon.get(h), flat_band) for h in HORIZONS]
    scored = [v for v in verdicts if v["verdict"] != UNSCORED]
    hits = sum(1 for v in scored if v["verdict"] == HIT)
    return {
        "date": call["date"],
        "label": call["label"],
        "risk_score": call.get("risk_score"),
        "verdicts": verdicts,
        "scored_count": len(scored),
        "hit_count": hits,
        "hit_rate": round(hits / len(scored), 4) if scored else None,
        "version": VERSION,
    }


def aggregate(verdict_records):
    """누적 스코어카드. verdict_records = score_call 결과 리스트."""
    per_horizon = {h: {"scored": 0, "hit": 0} for h in HORIZONS}
    unscored = 0
    for rec in verdict_records:
        for v in rec.get("verdicts", []):
            if v["verdict"] == UNSCORED:
                unscored += 1
                continue
            slot = per_horizon[v["horizon"]]
            slot["scored"] += 1
            if v["verdict"] == HIT:
                slot["hit"] += 1
    for s in per_horizon.values():
        s["hit_rate"] = round(s["hit"] / s["scored"], 4) if s["scored"] else None
    total_scored = sum(s["scored"] for s in per_horizon.values())
    total_hit = sum(s["hit"] for s in per_horizon.values())
    card = {
        "call_count": len(verdict_records),
        "per_horizon": per_horizon,
        "overall": {
            "scored": total_scored,
            "hit": total_hit,
            "hit_rate": round(total_hit / total_scored, 4) if total_scored else None,
        },
        "unscored": unscored,
        "version": VERSION,
    }
    log.info(
        "스코어카드 v%s: 콜 %d건 · 채점 %d · 적중 %d (unscored %d)",
        VERSION, card["call_count"], total_scored, total_hit, unscored,
    )
    return card


def save_json(path, payload):
    """JSON 산출물 저장(call_{date}.json / verify_{date}.json / scorecard.json)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
