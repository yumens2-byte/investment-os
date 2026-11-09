"""
kr_sleeve/input_contract.py — morning 결과(core_data.json) 입력 계약 검사 (DN2026_0108)

읽는 키(tests/fixtures/core_data_morning_20261005.json 으로 확인):
  status, command, timestamp,
  data.market_regime.market_regime, data.market_regime.market_risk_level,
  data.etf_allocation.allocation  ← Stance 보정·정규화를 거친 최종 비중
파일은 읽기만 한다. 쓰지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from kr_sleeve import config
from kr_sleeve.errors import INPUT_INVALID, INPUT_MISSING, INPUT_STALE, SleeveSkip


@dataclass(frozen=True)
class MorningInput:
    timestamp: datetime          # tz-aware (UTC)
    regime: str
    risk_level: str
    us_allocation: dict[str, int]


def load_core_data(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise SleeveSkip(INPUT_MISSING, f"파일 없음: {path}") from e
    except (OSError, ValueError) as e:
        raise SleeveSkip(INPUT_MISSING, f"읽기 실패: {type(e).__name__}") from e
    if not isinstance(data, dict):
        raise SleeveSkip(INPUT_MISSING, "최상위가 객체가 아님")
    return data


def _parse_ts(raw) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise SleeveSkip(INPUT_INVALID, "timestamp 없음")
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise SleeveSkip(INPUT_INVALID, f"timestamp 형식 오류: {raw[:40]}") from e
    if ts.tzinfo is None:
        raise SleeveSkip(INPUT_INVALID, "timestamp 시간대 없음")
    return ts.astimezone(UTC)


def validate(envelope: dict, now: datetime) -> MorningInput:
    """계약을 검사하고 MorningInput을 돌려준다. 위반 시 SleeveSkip."""
    if envelope.get("status") != "ok":
        raise SleeveSkip(INPUT_INVALID, f"status={envelope.get('status')!r}")
    command = str(envelope.get("command", ""))
    if "morning" not in command:
        raise SleeveSkip(INPUT_INVALID, f"morning 결과가 아님: command={command[:40]!r}")

    ts = _parse_ts(envelope.get("timestamp"))
    age_min = (now - ts).total_seconds() / 60
    if age_min > config.INPUT_MAX_AGE_MIN:
        raise SleeveSkip(INPUT_STALE, f"경과 {age_min:.0f}분 > {config.INPUT_MAX_AGE_MIN}분")
    if age_min < -10:
        raise SleeveSkip(INPUT_INVALID, f"timestamp가 미래: {age_min:.0f}분")

    data = envelope.get("data")
    if not isinstance(data, dict):
        raise SleeveSkip(INPUT_INVALID, "data 없음")
    regime_block = data.get("market_regime") or {}
    regime = regime_block.get("market_regime")
    risk = regime_block.get("market_risk_level")
    if regime not in config.REGIMES:
        raise SleeveSkip(INPUT_INVALID, f"레짐 값 오류: {regime!r}")
    if risk not in config.RISK_LEVELS:
        raise SleeveSkip(INPUT_INVALID, f"리스크 레벨 값 오류: {risk!r}")

    alloc_block = data.get("etf_allocation") or {}
    alloc = alloc_block.get("allocation") if isinstance(alloc_block, dict) else None
    if not isinstance(alloc, dict):
        raise SleeveSkip(INPUT_INVALID, "etf_allocation.allocation 없음")
    us: dict[str, int] = {}
    for etf in config.US_ETFS:
        v = alloc.get(etf)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v != int(v):
            raise SleeveSkip(INPUT_INVALID, f"{etf} 비중 값 오류: {v!r}")
        us[etf] = int(v)
    extra = set(alloc) - set(config.US_ETFS)
    if extra:
        raise SleeveSkip(INPUT_INVALID, f"알 수 없는 종목: {sorted(extra)}")
    if sum(us.values()) != 100:
        raise SleeveSkip(INPUT_INVALID, f"US 6종 합 {sum(us.values())} ≠ 100")

    return MorningInput(timestamp=ts, regime=regime, risk_level=risk, us_allocation=us)
