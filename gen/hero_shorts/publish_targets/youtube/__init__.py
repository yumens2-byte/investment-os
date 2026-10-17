"""
gen/hero_shorts/publish_targets/youtube/ — 유튜브 쇼츠 타깃 (W5)
================================================================
설계서 §2.2 · §4.3. 공통 계층은 플랫폼을 모르고, 이 모듈만 유튜브를 안다.

`YoutubeTarget` 은 `base.Target` 계약을 만족한다:
    build_plan(record, projection) → TargetPlan (게이트 실행 포함)
    verify(record, result)         → 검증 dict
    extract_id(url)                → videoId
"""
from typing import Any, Dict, Optional

from ..base import (
    STATUS_BLOCKED,
    TargetPlan,
    ep_label as _ep_label,
    media_facts,
    resolve_account_id,
)
from . import gates as gates_mod
from . import payload as payload_mod
from . import verify as verify_mod

VERSION = "1.0.0"

ENV_ACCOUNT_KEY = "HERO_YOUTUBE_ACCOUNT_ID"


class YoutubeTarget:
    """유튜브 쇼츠 타깃."""

    platform = "youtube"
    env_account_key = ENV_ACCOUNT_KEY

    def __init__(self, account_id: Optional[str] = None,
                 *, visibility: str = payload_mod.DEFAULT_VISIBILITY,
                 category_id: str = payload_mod.CATEGORY_EDUCATION,
                 made_for_kids: bool = payload_mod.DEFAULT_MADE_FOR_KIDS,
                 contains_synthetic_media: bool = True,
                 regular_publish: bool = True) -> None:
        self.account_id = account_id
        self.visibility = visibility
        self.category_id = category_id
        self.made_for_kids = made_for_kids
        self.contains_synthetic_media = contains_synthetic_media
        self.regular_publish = regular_publish

    # --- 계획 --------------------------------------------------------------- #
    def build_plan(self, record: Dict[str, Any], projection: Dict[str, Any],
                   *, ep_key: Optional[str] = None,
                   common_gate: Optional[Dict[str, Any]] = None) -> TargetPlan:
        ep_key = ep_key or record.get("_ep_key") or ""
        label = _ep_label(ep_key) or record.get("_ep_label") or ""
        facts = media_facts(record)
        proj = projection or {}

        gate = gates_mod.run_youtube_gates(
            duration_sec=facts.get("duration_sec"),
            ratio=facts.get("ratio"),
            title=proj.get("title"),
            ep_label_value=label,
            source_title=record.get("title"),
            visibility=self.visibility,
            tags=proj.get("tags"),
            description=proj.get("content"),
            contains_synthetic_media=self.contains_synthetic_media,
            regular_publish=self.regular_publish,
        )
        merged = _merge_gates(common_gate, gate, own_ids=gates_mod.GATE_IDS)

        account_id = resolve_account_id(self.env_account_key, self.account_id)
        problems = []
        if not account_id:
            problems.append(
                f"{self.env_account_key} 미설정 — 유튜브 계정 ID 주입 필요(fail-closed)")
        if not merged["ok"]:
            problems.append(f"게이트 미통과: {merged['failed']}")

        payload = {}
        if not problems:
            payload = payload_mod.platform_entry(
                account_id, proj,
                visibility=self.visibility,
                category_id=self.category_id,
                made_for_kids=self.made_for_kids,
                contains_synthetic_media=self.contains_synthetic_media,
            )

        return TargetPlan(
            platform=self.platform,
            account_id=account_id,
            # `ok` = 전송 가능. 게이트 실패든 계정 미설정이든 하나라도 있으면 False.
            gate={**merged, "ok": merged["ok"] and not problems,
                  "blocked": bool(problems), "problems": problems},
            payload=payload,
            idempotency_key=f"{ep_key}:{self.platform}",
        )

    # --- 검증 --------------------------------------------------------------- #
    def verify(self, record: Dict[str, Any], result) -> Dict[str, Any]:
        return verify_mod.verify(record, result)

    def extract_id(self, url: str) -> Optional[str]:
        return verify_mod.extract_video_id(url)


def _merge_gates(common_gate, own_gate, *, own_ids) -> Dict[str, Any]:
    """공통 게이트 + 타깃 게이트 병합. 하나라도 실패면 ok=False(fail-closed)."""
    common_ok = True if common_gate is None else bool(common_gate.get("ok"))
    common_failed = [] if common_gate is None else list(common_gate.get("failed") or [])
    results = [] if common_gate is None else list(common_gate.get("results") or [])
    ok = common_ok and bool(own_gate.get("ok"))
    return {
        "ok": ok,
        "failed": common_failed + list(own_gate.get("failed") or []),
        "results": results + list(own_gate.get("results") or []),
        "own_gate_ids": list(own_ids),
        "common_ok": common_ok,
    }


def blocked_result(plan: TargetPlan, note: Optional[str] = None):
    """게이트 미통과/계정 미설정 → 원장 기록용 blocked 결과."""
    from ..base import TargetResult
    from ..ledger_v2 import now_utc

    return TargetResult(
        platform=plan.platform,
        outcome=STATUS_BLOCKED,
        error="; ".join(plan.gate.get("problems") or []) or note,
        recorded_at=now_utc(),
        attempts=[{"at": now_utc(), "outcome": STATUS_BLOCKED,
                   "note": ",".join(plan.gate.get("failed") or []) or None}],
    )
