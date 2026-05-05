"""Streamer consensus deduplication helpers.

Step 6-YT (run_view morning) 발행 전에 호출해 동일/유사 내용 재발행을 차단한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = REPO_ROOT / "data" / "streamer_publish_log.json"
LOG_PATH = Path("data/streamer_publish_log.json")


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def build_fingerprints(consensus: dict[str, Any]) -> dict[str, str]:
    tweet = _normalize(consensus.get("tweet", ""))
    points = consensus.get("summary_points", []) or []
    points_text = "|".join(_normalize(str(p)) for p in points)
    tags = consensus.get("topic_tags", []) or []
    direction = _normalize(consensus.get("direction", "neutral"))

    raw_base = f"{tweet}||{points_text}"
    topic_base = f"{direction}||{'|'.join(sorted(map(str, tags)))}"

    return {
        "raw_hash": hashlib.sha256(raw_base.encode("utf-8")).hexdigest(),
        "topic_hash": hashlib.sha256(topic_base.encode("utf-8")).hexdigest(),
    }


def _load_logs() -> list[dict[str, Any]]:
    try:
        if not LOG_PATH.exists():
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text("[]", encoding="utf-8")
            logger.info(f"[StreamerDedupe] 로그 파일 초기 생성: {LOG_PATH}")
            return []
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[StreamerDedupe] 로그 로드 실패: {e}")
        return []


def _save_logs(logs: list[dict[str, Any]]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[StreamerDedupe] 로그 저장 실패: {e}")


def check_streamer_duplicate(consensus: dict[str, Any], lookback_hours: int = 48) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    fp = build_fingerprints(consensus)
    direction = (consensus.get("direction") or "neutral").lower()
    since = now - timedelta(hours=lookback_hours)

    logs = _load_logs()
    scanned = 0
    recent = 0
    for row in reversed(logs):
        scanned += 1
    for row in reversed(logs):
        try:
            ts = datetime.fromisoformat(row.get("published_at_utc", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if ts < since:
            continue
        recent += 1

        if row.get("raw_hash") == fp["raw_hash"]:
            return {
                "allow": False,
                "reason": "raw_hash_match",
                "fingerprints": fp,
                "scanned": scanned,
                "recent": recent,
                "matched_at": row.get("published_at_utc"),
            }

        if row.get("topic_hash") == fp["topic_hash"] and row.get("direction", "").lower() == direction:
            return {
                "allow": False,
                "reason": "topic_hash_match",
                "fingerprints": fp,
                "scanned": scanned,
                "recent": recent,
                "matched_at": row.get("published_at_utc"),
            }

    return {
        "allow": True,
        "reason": "no_recent_match",
        "fingerprints": fp,
        "scanned": scanned,
        "recent": recent,
        "matched_at": None,
    }

        if row.get("raw_hash") == fp["raw_hash"]:
            return {"allow": False, "reason": "raw_hash_match", "fingerprints": fp}

        if row.get("topic_hash") == fp["topic_hash"] and row.get("direction", "").lower() == direction:
            return {"allow": False, "reason": "topic_hash_match", "fingerprints": fp}

    return {"allow": True, "reason": "no_recent_match", "fingerprints": fp}


def record_streamer_publish(consensus: dict[str, Any], decision: dict[str, Any], tweet_id: str | None) -> None:
    logs = _load_logs()
    fp = decision.get("fingerprints") or build_fingerprints(consensus)
    logs.append(
        {
            "published_at_utc": datetime.now(timezone.utc).isoformat(),
            "tweet_id": tweet_id,
            "tweet": consensus.get("tweet", "")[:300],
            "direction": consensus.get("direction", "neutral"),
            "raw_hash": fp.get("raw_hash"),
            "topic_hash": fp.get("topic_hash"),
            "reason": decision.get("reason", "unknown"),
            "allow": bool(decision.get("allow", False)),
            "lookback_recent": decision.get("recent", 0),
            "lookback_scanned": decision.get("scanned", 0),
            "matched_at": decision.get("matched_at"),
        }
    )
    _save_logs(logs)
