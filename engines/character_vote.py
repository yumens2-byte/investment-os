"""
engines/character_vote.py
================================
A안: C-19 캐릭터 투표 재활성화 + C-7 소설 연동 루프.

VERSION = "1.0.0"

v1.0.0 (2026-08-10):
  - 신규. 기존 viral_engine._generate_character_vote()의 후보 선정 로직 이식.
  - 투표 방식: 후보별 reply 트윗의 like_count 비교 ("❤️ = 투표").
    → read API는 기존 viral_performance_tracker.fetch_metrics_batch() 재사용.
  - 발행(일): publish_vote(reply_to=소설 첫 트윗) — 소설 스레드 말단에 연결.
  - 정산(토): settle_vote() — like 최다 승자 → character_votes.winner 확정.
  - 소설 반영: get_latest_winner() → comic_novel.novelify_episodes(mvp_winner=...).

테이블: public.character_votes (마이그레이션 create_character_votes 참조)
"""
from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

KST = timezone(timedelta(hours=9))

MAX_CANDIDATES = 4
MAX_SETTLE_RETRY = 2          # 집계 실패 유예 횟수 (초과 시 skipped)
SETTLE_MIN_AGE_DAYS = 5       # 발행 후 최소 경과일 (일→토 = 6일, 여유 5일)

# viral_engine._CHARACTERS 이식 (원본 보존 — viral_engine 무수정)
CHARACTERS: dict[str, tuple[str, str]] = {
    "EDT":              ("🐂",  "골드 링 각성으로 전선 반전"),
    "Leverage Man":     ("🔥",  "화염 주먹으로 빌런 타격"),
    "Iron Nuna":        ("🛡️", "ETF 방패로 금리 압박 흡수"),
    "Futures Girl":     ("⚡",  "선물 시장 신호 감지"),
    "Gold Bond":        ("🏆",  "황금 갑옷으로 방어선 사수"),
    "War Dominion":     ("😈",  "마감일 연장 전략"),
    "Oil Shock Titan":  ("🛢️", "유가 폭등으로 시장 압박"),
    "Algorithm Reaper": ("💻",  "코드 컴파일로 새로운 위협"),
}

_FALLBACK_CANDIDATES: list[tuple[str, str, str]] = [
    ("EDT",          "🐂",  "시장 수호자"),
    ("Iron Nuna",    "🛡️", "ETF 방패 전사"),
    ("Futures Girl", "⚡",  "선물 시장 감지자"),
    ("War Dominion", "😈",  "시장 지배자"),
]

# 안티봇: 헤더/후보 문구 풀
_VOTE_HEADERS = [
    ("🔥 이번 주 EDT Universe MVP는?\n\n아래 후보 트윗에 ❤️를 눌러 투표하세요!\n"
     "최다 득표 캐릭터가 다음 주 소설 주인공이 됩니다 🎨"),
    ("🗳️ 주간 MVP 투표 오픈!\n\n마음에 드는 캐릭터 트윗에 ❤️ 하나면 투표 완료.\n"
     "1위는 다음 화 활약 확정 🎬"),
    ("🏆 이번 화 최고의 캐릭터를 뽑아주세요\n\n아래 후보 중 ❤️로 한 표!\n"
     "결과는 다음 주 소설에 그대로 반영됩니다 📖"),
]
_CANDIDATE_TEMPLATES = [
    "{emoji} {name}\n— {desc}\n\n이 캐릭터에게 투표하려면 ❤️",
    "{emoji} 후보: {name}\n{desc}\n\n❤️ = 한 표!",
    "{emoji} {name}\n\"{desc}\"\n\nMVP라고 생각하면 ❤️ 꾹",
]


def _get_client():
    """Supabase 클라이언트 (마스터 패턴 — 지연 import)."""
    from db.supabase_client import get_client
    return get_client()


def _is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() != "false"


def _is_valid_tweet_id(tweet_id: Any) -> bool:
    return bool(tweet_id) and str(tweet_id) not in ("FAIL", "SKIP", "DRY_RUN", "X_FAIL")


# ─────────────────────────────────────────────────────────────────────────
# 1. 후보 선정 — 당일 소설 텍스트 기반 (viral_engine 로직 이식)
# ─────────────────────────────────────────────────────────────────────────

def select_candidates(today: str | None = None) -> list[tuple[str, str, str]]:
    """소설 등장 캐릭터 최대 4명. 3명 미만이면 고정 fallback 4인."""
    candidates: list[tuple[str, str, str]] = []
    try:
        from db.daily_store import get_novel
        if today is None:
            today = datetime.now(KST).strftime("%Y-%m-%d")
        novel = get_novel(today)
        if novel and novel.get("novel_text"):
            for char, (emoji, desc) in CHARACTERS.items():
                if char in novel["novel_text"]:
                    candidates.append((char, emoji, desc))
    except Exception as e:
        logger.warning(f"[CharVote] 소설 조회 실패 (fallback 사용): {e}")

    if len(candidates) < 3:
        candidates = list(_FALLBACK_CANDIDATES)
    return candidates[:MAX_CANDIDATES]


# ─────────────────────────────────────────────────────────────────────────
# 2. 발행 — publish_vote (일요일, 소설 발행 직후)
# ─────────────────────────────────────────────────────────────────────────

def publish_vote(reply_to: str | None = None) -> dict:
    """
    투표 발행: 헤더 1트윗 + 후보 N트윗 (reply 체인).

    Args:
        reply_to: 소설 첫 트윗 ID (무효값이면 독립 트윗으로 fallback)
    """
    logger.info(f"[CharVote] v{VERSION} publish_vote 시작 (reply_to={reply_to})")
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 멱등: 당일 기발행 확인
    try:
        existing = (
            _get_client().table("character_votes")
            .select("id").eq("vote_date", today).execute()
        )
        if existing.data:
            logger.info(f"[CharVote] {today} 기발행 → skip")
            return {"success": False, "reason": "already_published"}
    except Exception as e:
        logger.warning(f"[CharVote] 기발행 확인 실패 (진행): {e}")

    candidates = select_candidates(today)

    header_text = random.choice(_VOTE_HEADERS)
    if not _is_valid_tweet_id(reply_to):
        reply_to = None

    try:
        from publishers.x_publisher import publish_thread

        # 헤더 (소설 스레드 말단 연결 또는 독립)
        h_result = publish_thread([header_text], reply_to=reply_to)
        h_ids = h_result.get("tweet_ids", [])
        header_id = h_ids[0] if h_ids else None
        if not header_id:
            logger.warning("[CharVote] 헤더 발행 실패 → 중단")
            return {"success": False, "reason": "header_failed"}

        # 후보 트윗 (헤더에 reply 체인)
        candidate_rows: list[dict[str, Any]] = []
        cand_posts = [
            random.choice(_CANDIDATE_TEMPLATES).format(emoji=e, name=n, desc=d)
            for (n, e, d) in candidates
        ]
        c_result = publish_thread(cand_posts, reply_to=str(header_id))
        c_ids = c_result.get("tweet_ids", [])
        for (name, emoji, _desc), tid in zip(candidates, c_ids):
            candidate_rows.append(
                {"name": name, "emoji": emoji, "tweet_id": str(tid), "likes": None}
            )

        if len(candidate_rows) < len(candidates):
            logger.warning(
                f"[CharVote] 후보 일부 발행 실패 "
                f"({len(candidate_rows)}/{len(candidates)}) — 발행분만 집계 대상"
            )

    except Exception as e:
        logger.warning(f"[CharVote] X 발행 실패: {e}")
        return {"success": False, "reason": f"publish_failed: {e}"}

    # DRY_RUN이면 DB 저장 skip (가짜 ID 집계 방지)
    if _is_dry_run() or str(header_id) == "DRY_RUN":
        logger.info("[CharVote] DRY_RUN → character_votes 저장 skip")
        return {
            "success": True, "dry_run": True,
            "candidates": [c[0] for c in candidates],
        }

    try:
        _get_client().table("character_votes").insert({
            "vote_date": today,
            "candidates": candidate_rows,       # supabase-py: list → jsonb
            "header_tweet_id": str(header_id),
            "status": "open",
        }).execute()
        logger.info(
            f"[CharVote] 발행 완료: header={header_id} 후보={len(candidate_rows)}명"
        )
    except Exception as e:
        logger.warning(f"[CharVote] character_votes 저장 실패: {e}")
        return {"success": False, "reason": f"db_failed: {e}"}

    return {
        "success": True,
        "header_tweet_id": str(header_id),
        "candidates": [c["name"] for c in candidate_rows],
    }


# ─────────────────────────────────────────────────────────────────────────
# 3. 정산 — settle_vote (토요일)
# ─────────────────────────────────────────────────────────────────────────

def settle_vote() -> dict:
    """open 상태 투표를 like_count 기준으로 정산."""
    logger.info(f"[CharVote] v{VERSION} settle_vote 시작")

    try:
        cutoff = (
            datetime.now(KST) - timedelta(days=SETTLE_MIN_AGE_DAYS)
        ).strftime("%Y-%m-%d")
        rows = (
            _get_client().table("character_votes")
            .select("id, vote_date, candidates, retry_count")
            .eq("status", "open")
            .lte("vote_date", cutoff)
            .order("vote_date")
            .execute()
        )
    except Exception as e:
        logger.warning(f"[CharVote] open 조회 실패: {e}")
        return {"success": False, "reason": f"query_failed: {e}"}

    if not rows.data:
        logger.info("[CharVote] 정산 대상 없음")
        return {"success": True, "settled": 0}

    settled = 0
    for row in rows.data:
        result = _settle_one(row)
        if result:
            settled += 1
    return {"success": True, "settled": settled}


def _settle_one(row: dict[str, Any]) -> bool:
    """단일 투표 정산. 성공(closed) 시 True."""
    vote_id = row["id"]
    candidates = row.get("candidates") or []
    tweet_ids = [
        c["tweet_id"] for c in candidates if _is_valid_tweet_id(c.get("tweet_id"))
    ]
    if not tweet_ids:
        _mark(vote_id, "skipped", None)
        return False

    try:
        from engines.viral_performance_tracker import fetch_metrics_batch
        metrics = fetch_metrics_batch(tweet_ids)
    except Exception as e:
        logger.warning(f"[CharVote] metrics 조회 예외: {e}")
        metrics = {}

    likes_by_name: dict[str, int] = {}
    fetch_failed = False
    for c in candidates:
        tid = str(c.get("tweet_id", ""))
        m = metrics.get(tid, {})
        if m.get("status") != "success":
            fetch_failed = True
            continue
        raw = m.get("raw", {}) or {}
        public = (raw.get("data", {}) or {}).get("public_metrics", {}) or {}
        c["likes"] = int(public.get("like_count") or 0)
        likes_by_name[c["name"]] = c["likes"]

    if fetch_failed and not likes_by_name:
        # 전건 실패 → 유예 (retry_count 증가, 초과 시 skipped)
        retry = int(row.get("retry_count") or 0) + 1
        if retry > MAX_SETTLE_RETRY:
            logger.warning(f"[CharVote] id={vote_id} 재시도 초과 → skipped")
            _mark(vote_id, "skipped", None, candidates=candidates)
        else:
            try:
                _get_client().table("character_votes").update(
                    {"retry_count": retry}
                ).eq("id", vote_id).execute()
                logger.info(f"[CharVote] id={vote_id} 집계 실패 → 유예({retry})")
            except Exception as e:
                logger.warning(f"[CharVote] 유예 마킹 실패: {e}")
        return False

    winner = _pick_winner(likes_by_name)
    status = "closed" if winner else "skipped"
    _mark(vote_id, status, winner, candidates=candidates)
    logger.info(
        f"[CharVote] id={vote_id} 정산: winner={winner} likes={likes_by_name}"
    )
    return status == "closed"


def _pick_winner(likes_by_name: dict[str, int]) -> str | None:
    """최다 like 승자. 전원 0 또는 최다 동률이면 None."""
    if not likes_by_name:
        return None
    max_likes = max(likes_by_name.values())
    if max_likes <= 0:
        return None
    top = [n for n, v in likes_by_name.items() if v == max_likes]
    if len(top) != 1:
        return None
    return top[0]


def _mark(
    vote_id: Any,
    status: str,
    winner: str | None,
    candidates: list[dict] | None = None,
) -> None:
    try:
        payload: dict[str, Any] = {
            "status": status,
            "winner": winner,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        if candidates is not None:
            payload["candidates"] = candidates
        _get_client().table("character_votes").update(payload).eq(
            "id", vote_id
        ).execute()
    except Exception as e:
        logger.warning(f"[CharVote] 상태 마킹 실패 (id={vote_id}): {e}")


# ─────────────────────────────────────────────────────────────────────────
# 4. 소설 반영 — get_latest_winner
# ─────────────────────────────────────────────────────────────────────────

def get_latest_winner(max_age_days: int = 10) -> str | None:
    """최근 closed 투표 1건의 winner. 없으면 None (소설 프롬프트 무주입)."""
    try:
        cutoff = (
            datetime.now(KST) - timedelta(days=max_age_days)
        ).strftime("%Y-%m-%d")
        rows = (
            _get_client().table("character_votes")
            .select("winner, vote_date")
            .eq("status", "closed")
            .gte("vote_date", cutoff)
            .order("vote_date", desc=True)
            .limit(1)
            .execute()
        )
        if rows.data and rows.data[0].get("winner"):
            return str(rows.data[0]["winner"])
    except Exception as e:
        logger.warning(f"[CharVote] winner 조회 실패 (무시): {e}")
    return None
