"""
kr_sleeve/engine.py — KR 슬리브 비중 계산 (DN2026_0108)

  합산 비중: US_i × (100 − k)/100,  KR_j = k × w_j      (정수화: 최대잔여법, 합 100)
  A안(본값): w = INTERNAL_A (50:50)
  B안(기록만): w_161510 = (XLE+ITA+TLT)/100, w_0060H0 = 나머지
  실효 미국 주식 노출 = (QQQM+XLK+SPYM+XLE+ITA) × (100−k)/100 + k × w_0060H0 × 미국 비중 근사치
비중은 상계·차감하지 않는다(3인 합의). 미국 6종 입력은 바꾸지 않는다.
"""
from __future__ import annotations

from kr_sleeve import config


def largest_remainder(raw: dict[str, float], total: int = 100) -> dict[str, int]:
    """실수 비중을 합이 total인 정수로 바꾼다. 잔여가 같으면 입력 순서가 앞선 키에 먼저 준다."""
    floors = {k: int(v) for k, v in raw.items()}
    short = total - sum(floors.values())
    order = sorted(raw, key=lambda k: (-(raw[k] - floors[k]), list(raw).index(k)))
    for k in order[:max(short, 0)]:
        floors[k] += 1
    return floors


def internal_b(us_alloc: dict[str, int]) -> dict[str, int]:
    defensive = sum(us_alloc[e] for e in config.US_DEFENSIVE_REAL_ETFS)
    return {"161510": defensive, "0060H0": 100 - defensive}


def combine(us_alloc: dict[str, int], internal: dict[str, int], k_pct: int) -> dict[str, int]:
    if not 0 <= k_pct <= 100:
        raise ValueError(f"k_pct 범위 오류: {k_pct}")
    if sum(internal.values()) != 100:
        raise ValueError(f"내부 비중 합 {sum(internal.values())} ≠ 100")
    raw: dict[str, float] = {e: us_alloc[e] * (100 - k_pct) / 100 for e in config.US_ETFS}
    for etf in config.KR_ETFS:
        raw[etf.code] = k_pct * internal[etf.code] / 100
    result = largest_remainder(raw)
    assert sum(result.values()) == 100 and min(result.values()) >= 0
    return result


def lookthrough_us_pct(us_alloc: dict[str, int], internal: dict[str, int], k_pct: int) -> float:
    us_equity = sum(us_alloc[e] for e in config.US_EQUITY_ETFS) * (100 - k_pct) / 100
    kr_us = k_pct * internal["0060H0"] / 100 * config.TOTAL_WORLD_US_SHARE
    return round(us_equity + kr_us, 1)


def compute(us_alloc: dict[str, int], k_pct: int = config.K_PCT) -> dict:
    a = dict(config.INTERNAL_A)
    b = internal_b(us_alloc)
    return {
        "k_pct": k_pct,
        "internal_a": a,
        "internal_b": b,
        "total_allocation": combine(us_alloc, a, k_pct),
        "total_allocation_b": combine(us_alloc, b, k_pct),
        "lookthrough_us_pct": lookthrough_us_pct(us_alloc, a, k_pct),
        "lookthrough_us_share": config.TOTAL_WORLD_US_SHARE,
        "lookthrough_us_share_source": config.TOTAL_WORLD_US_SHARE_SOURCE,
    }
