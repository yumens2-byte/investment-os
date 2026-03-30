"""
core/rank_tracker.py
======================
ETF 랭킹 변화 감지 및 이력 저장

저장 파일: data/published/rank_history.json
구조:
{
  "last_rank": {"XLE": 1, "TLT": 2, ...},
  "last_updated": "2026-03-30",
  "changes": [
    {"date": "2026-03-30", "from": {...}, "to": {...}}
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

RANK_HISTORY_PATH = Path(
    os.getenv("RANK_HISTORY_PATH", "data/published/rank_history.json")
)


def _load() -> dict:
    if RANK_HISTORY_PATH.exists():
        try:
            return json.loads(RANK_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_rank": {}, "last_updated": "", "changes": []}


def _save(data: dict) -> None:
    RANK_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    RANK_HISTORY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def detect_rank_change(
    new_rank: dict,
    dt_utc: Optional[datetime] = None
) -> Optional[dict]:
    """
    ETF 랭킹 변화 감지

    Args:
        new_rank: {"XLE": 1, "TLT": 2, ...}
        dt_utc:   기준 시각

    Returns:
        변화 있으면 dict, 없으면 None
        {
          "top1_changed": bool,
          "old_top1": str,
          "new_top1": str,
          "moved_up":   [{"etf": "TLT", "from": 3, "to": 1}],
          "moved_down": [{"etf": "XLE", "from": 1, "to": 3}],
          "old_rank": dict,
          "new_rank": dict,
        }
    """
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)
    kst   = dt_utc + timedelta(hours=9)
    today = kst.strftime("%Y-%m-%d")

    history = _load()
    old_rank = history.get("last_rank", {})

    # 첫 실행이면 저장만 하고 변화 없음 반환
    if not old_rank:
        history["last_rank"]    = new_rank
        history["last_updated"] = today
        _save(history)
        logger.info("[RankTracker] 첫 실행 — 기준 랭킹 저장")
        return None

    # 오늘 이미 처리했으면 스킵
    if history.get("last_updated") == today:
        logger.info("[RankTracker] 오늘 이미 처리됨 — 스킵")
        return None

    # 변화 감지
    moved_up   = []
    moved_down = []

    for etf, new_pos in new_rank.items():
        old_pos = old_rank.get(etf, new_pos)
        if old_pos != new_pos:
            if new_pos < old_pos:
                moved_up.append({"etf": etf, "from": old_pos, "to": new_pos})
            else:
                moved_down.append({"etf": etf, "from": old_pos, "to": new_pos})

    # 1위 변화 여부
    old_top1 = min(old_rank, key=old_rank.get) if old_rank else "—"
    new_top1 = min(new_rank, key=new_rank.get) if new_rank else "—"
    top1_changed = old_top1 != new_top1

    if not moved_up and not moved_down:
        # 변화 없음 — 날짜만 업데이트
        history["last_rank"]    = new_rank
        history["last_updated"] = today
        _save(history)
        logger.info("[RankTracker] 랭킹 변화 없음")
        return None

    change = {
        "top1_changed": top1_changed,
        "old_top1":     old_top1,
        "new_top1":     new_top1,
        "moved_up":     sorted(moved_up,   key=lambda x: x["to"]),
        "moved_down":   sorted(moved_down, key=lambda x: x["to"]),
        "old_rank":     old_rank,
        "new_rank":     new_rank,
    }

    # 이력 저장
    history["last_rank"]    = new_rank
    history["last_updated"] = today
    changes = history.get("changes", [])
    changes.append({"date": today, **change})
    history["changes"] = changes[-30:]  # 최근 30일만 유지
    _save(history)

    logger.info(
        f"[RankTracker] 변화 감지 — "
        f"1위:{old_top1}→{new_top1} | "
        f"상승:{[x['etf'] for x in moved_up]} | "
        f"하락:{[x['etf'] for x in moved_down]}"
    )
    return change
