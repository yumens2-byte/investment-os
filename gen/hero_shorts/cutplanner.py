"""
gen/hero_shorts/cutplanner.py — 유형별 컷구성·프롬프트 빌더
=============================================================
역할:
    원장 arc_state → 유형별 컷수/역할 배정 → 캐논 삽입 완성 프롬프트 +
    fal H3 요청 JSON 을 산출한다. 화면 텍스트·수치 표기는 금지(캐논 규칙)이며,
    원장 수치는 서사 근거로만 사용한다.

길이 설계 (마스터 지시 "30초~1분, 기존 스토리에 최적"):
    BATTLE     6컷 × 10s = 60s  — 도입→위협→교전→위기→전환→마무리
    AFTERMATH  3컷 × 10s = 30s  — 여파→수습→다음 조짐
    FLASHBACK  4컷 × 10s = 40s  — 회상 톤(필름 그레인) 4컷

비용: fal 768P $0.06/s — 30s=$1.80, 40s=$2.40, 60s=$3.60.
"""
import logging

from . import canon

log = logging.getLogger("hero_shorts.cutplanner")

# ── 유형별 표준 컷구성 ─────────────────────────────────────────────
# 각 컷 = 단일 동작 + 단일 카메라 무브 (영상 모델 안정 구간).
TYPE_PLAN = {
    "BATTLE": [
        "ESTABLISH_THREAT",   # 도입 — 빌런 위협 전경
        "ESCALATE",           # 위협 확대 — 도시·투자자 혼란
        "ENGAGE",             # 교전 — 수호자 진입
        "CRISIS",             # 위기 — 열세 (Ep91 같은 패배 구간에서는 여기서 끊김)
        "TURN",               # 전환 — 재배분·전술
        "RESOLVE",            # 마무리 — 결과 암시
    ],
    "AFTERMATH": [
        "AFTERMATH_CALM",     # 여파 — 잔해·고요
        "REGROUP",            # 수습 — 히어로 정리
        "NEXT_OMEN",          # 다음 조짐 — 새 빌런 그림자
    ],
    "FLASHBACK": [
        "FB_OPEN",            # 회상 개시 — 과거 톤
        "FB_BODY",            # 회상 본문 — 사건 재현
        "FB_PEAK",            # 충격점 — 트라우마 원인
        "FB_RETURN",          # 현재 복귀 — 결의
    ],
}

# arc_state 에서 서사에 쓸 핵심 필드 (화면 표기 없음 — 근거로만 사용)
NARRATIVE_FIELDS = ["type", "outcome", "title", "date"]


def cuts_for_type(ep_type):
    """유형 → 컷 역할 리스트. 미정의 유형은 안전 기본값(BATTLE) 대신
    AFTERMATH 3컷을 택해 최소 비용으로 폴백한다(추측 확장 금지)."""
    plan = TYPE_PLAN.get(ep_type)
    if plan is None:
        log.warning("미정의 유형 '%s' — AFTERMATH 3컷 폴백", ep_type)
        plan = TYPE_PLAN["AFTERMATH"]
    return plan


def build_cut_prompt(cut_role, ep_state):
    """컷 역할 + 원장 arc_state → 캐논 삽입 완성 프롬프트.

    캐논 유지 원칙: 캐릭터 기술은 canon.full_prompt 를 통해서만 가능하므로,
    이 함수가 프롬프트 빌드의 유일한 경로다.

    Args:
        cut_role: TYPE_PLAN 의 역할 상수.
        ep_state: 원장 정본 JSON(dict) — episode/title/type/villain 등.

    Returns:
        영어 최종 프롬프트(str).
    """
    a = ep_state.get("arc_state", {})
    villains = a.get("active_villains") or []
    hero = "EDT"
    # 원장에 따라 조연 배치 (Ep90+ 팀 전개 반영)
    if a.get("arc_day", 0) >= 8:
        hero = None  # 팀 전개 — 조립부에서 2인 지정
    title = ep_state.get("title", "")

    SCENES = {
        "ESTABLISH_THREAT": (
            f"A colossal villain {canon.CHARACTERS.get('Oil Shock Titan','')} rises over a night city, "
            "slamming the ground as red heat waves cross glowing ticker lights",
            villains[:1], "low-angle slow push-in", "deep rumbling bass"),
        "ESCALATE": (
            "Panic spreads across the city as market lights flicker and fall, "
            "crowds flee in long shadows",
            [], "slow lateral tracking shot", "rising sirens and drums"),
        "ENGAGE": (
            "Two heroes land on a rooftop and exchange a nod before leaping toward "
            "the giant villain",
            [hero or "Exposure Futures Girl"] if not hero else
            ["EDT", "Exposure Futures Girl"],
            "dynamic orbit camera", "heroic brass stab"),
        "CRISIS": (
            "The heroes are knocked back by a shockwave, armor scratched, "
            "the sky darkens as tension peaks",
            ["EDT", "Exposure Futures Girl"],
            "shaky handheld close-up", "low ominous drone"),
        "TURN": (
            "The Guardian of Capital redirects streams of golden light from a "
            "burning district to a dark district that flickers back to life",
            ["EDT"], "slow crane-up", "rising orchestral swell"),
        "RESOLVE": (
            "The villain staggers back as dawn breaks over the skyline and "
            "golden particles settle over the city",
            ["EDT"], "wide hopeful shot", "single resolve note"),
        "AFTERMATH_CALM": (
            "Quiet ruins of a financial district at dawn, embers drifting, "
            "the Guardian surveys the damage",
            ["EDT"], "slow high-angle pan", "soft wind, distant chimes"),
        "REGROUP": (
            "The hero team stands together on a rooftop, planning the next move",
            ["EDT", "Exposure Futures Girl"],
            "medium two-shot", "calm strings"),
        "NEXT_OMEN": (
            "A new shadow of stacked ledgers and chains moves behind distant "
            "clouds as lightning flickers",
            ["Debt Titan"], "slow zoom-in", "low sub-bass pulse"),
        "FB_OPEN": (
            "Grainy sepia-toned memory of a city in crisis years ago, "
            "old charts crumbling",
            [], "slow push through drifting papers", "aged film crackle"),
        "FB_BODY": (
            "A younger analyst watches collapsing charts in a dark room, "
            "red light washing over his face",
            ["EDT"], "over-the-shoulder shot", "muffled heartbeat"),
        "FB_PEAK": (
            "A giant hammer of falling prices shatters a glass floor in memory-space",
            [], "dutch-angle close-up", "sharp glass shatter"),
        "FB_RETURN": (
            "Back in the present, the analyst closes his fist with quiet resolve",
            ["EDT"], "tight close-up rack focus", "single piano note"),
    }
    entry = SCENES.get(cut_role)
    if entry is None:
        log.error("미정의 컷 역할: %s", cut_role)
        raise KeyError(cut_role)
    scene, chars, camera, sound = entry
    prompt = canon.full_prompt(scene, [c for c in chars if c], camera, sound)
    log.debug("컷 프롬프트 완성[%s] %s %s — %d자", cut_role, ep_state.get("episode"), title, len(prompt))
    return prompt


def build_fal_request(prompt, duration=10):
    """fal H3 요청 JSON 빌더.

    스키마 실측(2026-09-20): aspect_ratio '9:16' 열거형, resolution '768P'
    네이티브, duration 정수형, prompt_expansion_mode 'disabled' 권장.
    """
    return {
        "prompt": prompt,
        "duration": duration,                      # 10s 상한 실측은 승인 후 1회
        "resolution": "768P",
        "aspect_ratio": "9:16",
        "prompt_expansion_mode": "disabled",
    }


def plan_episode(ep_state):
    """회차 전체 컷계획 산출.

    Returns:
        [{"cut_no":1, "role":..., "prompt":..., "request":{...}}, ...]
    """
    ep_type = ep_state.get("type", "")
    roles = cuts_for_type(ep_type)
    log.info("컷계획: %s (%s) → %d컷", ep_state.get("episode"), ep_type, len(roles))
    return [
        {"cut_no": i + 1, "role": r,
         "prompt": build_cut_prompt(r, ep_state),
         "request": build_fal_request(build_cut_prompt(r, ep_state))}
        for i, r in enumerate(roles)
    ]
