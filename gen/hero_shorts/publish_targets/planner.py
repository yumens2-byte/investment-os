"""
gen/hero_shorts/publish_targets/planner.py — 발행 계획 · 실행 글루 (W8)
======================================================================
설계서 §6.1 · §6.2 · §6.3.

흐름(고정):
    승인 확인(C-G5) → 공통 규율(C-G1~C-G4) → 타깃 게이트(Y-G*/TH-G*) → 전송

**단일 호출 원칙**: 정상 경로는 유튜브·쓰레드를 `platforms[]` 한 번으로 보낸다
(미디어 인게스트 1회 · 원자성 · post_id 1개). 부분실패 후 재시도만 타깃별 단일 호출이다
(성공한 채널을 다시 올리지 않는다 — G2).

fail-closed: 게이트 미통과 타깃은 페이로드가 비어 있고 `transport` 로 넘어가지 않는다.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import caption_plan, common_gates, ledger_v2
from .base import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    TargetPlan,
    TargetResult,
    approval_status,
    ep_label,
    media_facts,
)
from .transport import build_request, collect_errors, decompose_response

log = logging.getLogger("hero_shorts.publish_targets.planner")

VERSION = "1.0.0"

DEFAULT_MAX_ATTEMPTS = 3


# --------------------------------------------------------------------------- #
# 계획
# --------------------------------------------------------------------------- #
def build_projections(record: Dict[str, Any], ep_key: str, *,
                      hook: Optional[str] = None,
                      topic_tag: str = caption_plan.DEFAULT_TOPIC_TAG,
                      tags: Sequence[str] = caption_plan.HASHTAG_LIST) -> Dict[str, Any]:
    """회차 → 캡션 3투영. 회차 제목이 없으면 ValueError(추측 생성 금지)."""
    return caption_plan.build_projections(
        record, ep_label=ep_label(ep_key), hook=hook,
        topic_tag=topic_tag, tags=tags)


def common_gate_for(record: Dict[str, Any], projections: Dict[str, Any], *,
                    text_hits: Optional[Iterable[str]] = None,
                    require_disclaimer: bool = True) -> Dict[str, Any]:
    """C-G1~C-G5. 모든 타깃에 공통 적용된다."""
    facts = media_facts(record)
    texts = [projections.get("youtube", {}).get("content"),
             projections.get("threads", {}).get("content")]
    if not require_disclaimer:
        texts = []
    return common_gates.run_common_gates(
        media_url=facts.get("media_url"),
        duration_sec=facts.get("duration_sec"),
        ratio=facts.get("ratio"),
        media_kind=facts.get("media_kind"),
        texts=[t for t in texts if t],
        text_hits=list(text_hits or []),
        approval_status=approval_status(record),
    )


def make_targets(platforms: Sequence[str], **kwargs) -> Dict[str, Any]:
    """플랫폼명 → 타깃 인스턴스. REGISTRY 지연 import(순환 회피)."""
    from . import get_target

    return {p: get_target(p, **kwargs.pop(p, {})) for p in platforms}


def plan_episode(record: Dict[str, Any], ep_key: str, *,
                 targets: Dict[str, Any],
                 platforms: Sequence[str],
                 hooks: Optional[Dict[str, str]] = None,
                 topic_tag: str = caption_plan.DEFAULT_TOPIC_TAG,
                 tags: Sequence[str] = caption_plan.HASHTAG_LIST,
                 text_hits: Optional[Iterable[str]] = None,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> Dict[str, Any]:
    """회차 1건의 게이트·페이로드까지 완성한다(전송 없음).

    Returns: {"ep","projections","common","plans":{plat: TargetPlan},
              "pending":[...],"blocked":[...],"skipped":[...]}
    """
    status = ledger_v2.target_status
    projections = build_projections(record, ep_key,
                                    hook=(hooks or {}).get(ep_key),
                                    topic_tag=topic_tag, tags=tags)
    common = common_gate_for(record, projections, text_hits=text_hits)

    plans: Dict[str, TargetPlan] = {}
    pending: List[str] = []
    blocked: List[str] = []
    skipped: List[str] = []

    for plat in platforms:
        cur = status(record, plat)
        if cur in (STATUS_PUBLISHED, STATUS_BLOCKED, "skipped"):
            skipped.append(plat)
            continue
        used = ledger_v2.attempts_used(record, plat)
        if used >= max_attempts:
            log.warning("재시도 상한 도달 — 제외: %s:%s (%d회)", ep_key, plat, used)
            skipped.append(plat)
            continue
        target = targets[plat]
        plan = target.build_plan(dict(record, _ep_key=ep_key), projections[plat],
                                 ep_key=ep_key, common_gate=common)
        plans[plat] = plan
        if plan.gate.get("blocked"):
            blocked.append(plat)
        else:
            pending.append(plat)

    return {
        "ep": ep_key,
        "projections": projections,
        "common": common,
        "plans": plans,
        "pending": pending,
        "blocked": blocked,
        "skipped": skipped,
        "facts": media_facts(record),
    }


def plan_ledger(doc: Dict[str, Any], *, targets: Dict[str, Any],
                platforms: Sequence[str] = ledger_v2.TARGET_PLATFORMS,
                eps: Optional[Sequence[str]] = None,
                **kwargs) -> List[Dict[str, Any]]:
    """원장 전체 계획. 발행 대상(pending)이 없는 회차도 결과에 담는다."""
    keys = list(eps) if eps else ledger_v2.episode_keys(doc)
    out: List[Dict[str, Any]] = []
    for ep in keys:
        record = doc.get(ep)
        if not isinstance(record, dict):
            continue
        try:
            out.append(plan_episode(record, ep, targets=targets,
                                    platforms=platforms, **kwargs))
        except ValueError as exc:                     # 제목 등 필수값 부재
            log.error("%s 계획 불가: %s", ep, exc)
            out.append({"ep": ep, "error": str(exc), "plans": {},
                        "pending": [], "blocked": list(platforms), "skipped": []})
    return out


# --------------------------------------------------------------------------- #
# 실행 (전송)
# --------------------------------------------------------------------------- #
def results_from_response(plans: Dict[str, TargetPlan], response,
                          *, platforms: Sequence[str]) -> Dict[str, TargetResult]:
    """전송 응답 → 타깃별 TargetResult.

    설계서 §6.2: HTTP 207 · 엔트리 부재는 **published 로 승격하지 않는다**.
    """
    decomp = decompose_response(response, platforms)
    errors = collect_errors(response)
    out: Dict[str, TargetResult] = {}
    for plat in platforms:
        plan = plans[plat]
        info = decomp.get(plat) or {"outcome": STATUS_PENDING, "url": None,
                                    "error": None, "post_id": None, "explicit": False}
        outcome = info["outcome"]
        if response.partial_failure and outcome == STATUS_PUBLISHED and not info.get("explicit"):
            outcome = STATUS_FAILED              # 207 에서 비명시 성공은 인정하지 않는다
        err = info.get("error")
        if outcome == STATUS_FAILED and not err and errors:
            err = " ; ".join(errors)[:500]
        out[plat] = TargetResult(
            platform=plat,
            outcome=outcome,
            post_id=info.get("post_id"),
            platform_post_id=info.get("platform_post_id"),
            platform_post_url=info.get("url"),
            published_at=ledger_v2.now_utc() if outcome == STATUS_PUBLISHED else None,
            published_at_source="platform" if outcome == STATUS_PUBLISHED else None,
            recorded_at=ledger_v2.now_utc(),
            error=err,
            attempts=[{
                "at": ledger_v2.now_utc(),
                "outcome": outcome,
                "note": ("HTTP 207" if response.partial_failure else None),
            }],
        )
    return out


def publish_batch(plans: Dict[str, TargetPlan], *, transport,
                  media_url: str, platforms: Sequence[str],
                  publish_now: bool = True,
                  scheduled_at: Optional[str] = None,
                  timezone: Optional[str] = None,
                  content: Optional[str] = None,
                  tags: Optional[Sequence[str]] = None,
                  idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    """정상 경로 — 유튜브·쓰레드를 **1회 호출**로 함께 보낸다.

    게이트 미통과 타깃은 전송 대상에서 제외하고 blocked 결과로 기록한다.
    `content`/`tags` 를 주지 않으면 각 엔트리의 `customContent` 에서 승계한다
    (단일 타깃 재시도 경로에서 최상위 content 가 비지 않게 한다).
    """
    send_plats = [p for p in platforms if p in plans and not plans[p].gate.get("blocked")]
    results: Dict[str, TargetResult] = {}
    for plat in platforms:
        if plat in send_plats:
            continue
        plan = plans.get(plat)
        results[plat] = TargetResult(
            platform=plat, outcome=STATUS_BLOCKED,
            error="; ".join((plan.gate.get("problems") if plan else ["계획 없음"]) or []),
            recorded_at=ledger_v2.now_utc(),
            attempts=[{"at": ledger_v2.now_utc(), "outcome": STATUS_BLOCKED, "note": "게이트"}],
        )

    response = None
    if send_plats:
        entries = [plans[p].payload for p in send_plats]
        if content is None:                       # 엔트리 customContent 승계
            for p in send_plats:
                cc = (plans[p].payload or {}).get("customContent")
                if cc:
                    content = cc
                    break
        body = build_request(media_url, entries,
                             publish_now=publish_now, scheduled_for=scheduled_at,
                             timezone=timezone, content=content, tags=tags)
        try:
            response = transport.send(body, platforms=send_plats,
                                      idempotency_key=idempotency_key)
            results.update(results_from_response(plans, response,
                                                platforms=send_plats))
        except Exception as exc:                      # 전송 계층 예외 → 타깃별 failed
            log.error("전송 실패(%s): %s", type(exc).__name__, exc)
            for p in send_plats:
                results[p] = TargetResult(
                    platform=p, outcome=STATUS_FAILED,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                    recorded_at=ledger_v2.now_utc(),
                    attempts=[{"at": ledger_v2.now_utc(), "outcome": STATUS_FAILED,
                               "note": "전송 예외"}],
                )

    return {"results": results, "sent": send_plats,
            "response": response, "platforms": list(platforms)}


def publish_single(plan: TargetPlan, *, transport, media_url: str,
                   publish_now: bool = True,
                   content: Optional[str] = None,
                   tags: Optional[Sequence[str]] = None,
                   idempotency_key: Optional[str] = None) -> TargetResult:
    """재시도 경로 — **성공한 채널을 다시 올리지 않는다**(G2). 타깃 1건만 보낸다."""
    body = build_request(media_url, [plan.payload], publish_now=publish_now,
                         content=content or (plan.payload or {}).get("customContent"),
                         tags=tags)
    try:
        response = transport.send(body, platforms=[plan.platform],
                                  idempotency_key=idempotency_key)
    except Exception as exc:
        return TargetResult(
            platform=plan.platform, outcome=STATUS_FAILED,
            error=f"{type(exc).__name__}: {exc}"[:500],
            recorded_at=ledger_v2.now_utc(),
            attempts=[{"at": ledger_v2.now_utc(), "outcome": STATUS_FAILED,
                       "note": "전송 예외"}],
        )
    return results_from_response({plan.platform: plan}, response,
                                 platforms=[plan.platform])[plan.platform]


# --------------------------------------------------------------------------- #
# 원장 반영
# --------------------------------------------------------------------------- #
def apply_results(record: Dict[str, Any], results: Dict[str, TargetResult],
                  platform: Optional[str] = None) -> Dict[str, Any]:
    """결과를 회차 레코드에 기록. `tiktok{}` 은 손대지 않는다(무손실 보존)."""
    written = {}
    for plat, res in results.items():
        if platform and plat != platform:
            continue
        written[plat] = ledger_v2.set_target(record, plat, res)
    return written


def summary(plans: Dict[str, TargetPlan], results: Dict[str, TargetResult]) -> Dict[str, Any]:
    return {
        "planned": sorted(plans.keys()),
        "outcomes": {p: r.outcome for p, r in sorted(results.items())},
        "published": sorted([p for p, r in results.items() if r.outcome == STATUS_PUBLISHED]),
        "failed": sorted([p for p, r in results.items() if r.outcome == STATUS_FAILED]),
        "blocked": sorted([p for p, r in results.items() if r.outcome == STATUS_BLOCKED]),
        "pending": sorted([p for p, r in results.items() if r.outcome == STATUS_PENDING]),
    }
