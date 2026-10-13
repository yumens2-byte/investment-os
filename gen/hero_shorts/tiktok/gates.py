"""
gen/hero_shorts/tiktok/gates.py — 틱톡 발행 게이트 (T-G1/T-G2/T-G3 + 캡션 규율)
=============================================================================
설계서 v1.0 §2.4 준수. 통과(ok=True)가 아니면 발행하지 않는다(fail-closed).

    T-G1 길이·비율 : 세그먼트 길이 <= 계정 max duration · 9:16
    T-G2 AIGC 고지 : ai_generated 플래그 True 필수
    T-G3 텍스트    : 영상 내 문자 0건 (D5)
    CAPTION 규율   : 면책문 포함 · 금지어 0 · 해시태그 한도 · 세계관 태그 (JCU G-4)
"""
import logging
import re

log = logging.getLogger("tiktok.gates")

VERSION = "1.0.0"

REQUIRED_RATIO = "9:16"
# TikTok 공개 레벨 — creator_info 실측값 범위(설계서 §2.4 T-G4)
VALID_PRIVACY_LEVELS = (
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
)
DEFAULT_PRIVACY_LEVEL = "PUBLIC_TO_EVERYONE"
BUSINESS_LANE = "business"
# 아래 수치는 설계 파라미터 — 계정 실측(TikTok creator_info)으로 확정 필요.
# 확정 후에는 값을 바꾸더라도 과거 판정을 재계산하지 않는다(기준 고정).
TIKTOK_CAPTION_LIMIT = 2200
MAIN_HASHTAG_LIMIT = 8
WORLD_TAG = "#JCUniverse"

# JCU G-4 기본 금지어(마스터 제공 목록이 있으면 그것을 우선한다).
FORBIDDEN_PHRASES = (
    "반드시", "무조건", "보장", "확실히", "내일 오른다",
    "추천", "사라", "팔아라",
)

DISCLAIMER_MARKS = ("투자 참고", "투자 권유 아님", "not investment advice")

_HASHTAG_RE = re.compile(r"#[0-9A-Za-z가-힣_]+")


def gate_tg1(duration_sec, max_duration_sec, ratio=REQUIRED_RATIO):
    problems = []
    if duration_sec is None or duration_sec <= 0:
        problems.append("길이 미확정")
    elif duration_sec > max_duration_sec:
        problems.append(f"길이 {duration_sec}s > 계정 상한 {max_duration_sec}s")
    if ratio != REQUIRED_RATIO:
        problems.append(f"비율 {ratio} != {REQUIRED_RATIO}")
    return {"gate": "T-G1", "ok": not problems, "problems": problems}


def gate_tg2(ai_generated):
    ok = ai_generated is True
    return {
        "gate": "T-G2",
        "ok": ok,
        "problems": [] if ok else ["AIGC 고지 플래그(ai_generated=True) 미적용"],
    }


def gate_tg3(text_hits):
    hits = [h for h in (text_hits or []) if str(h).strip()]
    ok = not hits
    return {
        "gate": "T-G3",
        "ok": ok,
        "problems": [] if ok else [f"영상 내 문자 감지(D5 위반): {hits}"],
    }


def gate_caption(caption, hashtag_limit=MAIN_HASHTAG_LIMIT, char_limit=TIKTOK_CAPTION_LIMIT):
    text = caption or ""
    problems = []
    if not any(m in text for m in DISCLAIMER_MARKS):
        problems.append("면책문 미포함 — 발행 금지(JCU G-4)")
    banned = sorted({p for p in FORBIDDEN_PHRASES if p in text})
    if banned:
        problems.append(f"금지어 감지(JCU G-4): {banned}")
    tags = _HASHTAG_RE.findall(text)
    if len(tags) > hashtag_limit:
        problems.append(f"해시태그 {len(tags)}개 > 한도 {hashtag_limit}")
    if WORLD_TAG not in text:
        problems.append(f"세계관 태그 {WORLD_TAG} 누락")
    if len(text) > char_limit:
        problems.append(f"캡션 {len(text)}자 > {char_limit} 제한")
    return {"gate": "CAPTION", "ok": not problems, "problems": problems, "hashtags": tags}


def gate_tg4(privacy_level, draft=False, lane=BUSINESS_LANE):
    """T-G4 공개 레벨 — TikTok API 필수 필드 + Business 앱 레인 규칙.

    - draft=True: TikTok 앱에서 공개 레벨을 고르므로 검증 대상 아님(통과).
    - Business 앱 연결 계정: 영상 직접 발행은 PUBLIC_TO_EVERYONE 만 허용.
      (비공개 레벨은 draft 경유만 가능)
    """
    if draft:
        return {"gate": "T-G4", "ok": True, "problems": [], "lane": lane, "draft": True}
    problems = []
    if not privacy_level:
        problems.append("privacy_level 미지정 — TikTok API 필수 필드")
    elif privacy_level not in VALID_PRIVACY_LEVELS:
        problems.append(
            f"미정의 privacy_level '{privacy_level}' — 허용: {list(VALID_PRIVACY_LEVELS)}"
        )
    elif lane == BUSINESS_LANE and privacy_level != DEFAULT_PRIVACY_LEVEL:
        problems.append(
            f"Business 앱 계정은 영상 직접 발행 시 {DEFAULT_PRIVACY_LEVEL} 만 허용 "
            f"('{privacy_level}' 은 draft 경유 필요)"
        )
    return {"gate": "T-G4", "ok": not problems, "problems": problems,
            "lane": lane, "draft": False}


def run_gates(caption, duration_sec, max_duration_sec, ai_generated=True,
              ratio=REQUIRED_RATIO, text_hits=None,
              privacy_level=None, draft=False, lane=BUSINESS_LANE):
    """전 게이트 실행. ok=False 이면 발행 금지."""
    results = [
        gate_tg1(duration_sec, max_duration_sec, ratio),
        gate_tg2(ai_generated),
        gate_tg3(text_hits),
        gate_tg4(privacy_level, draft, lane),
        gate_caption(caption),
    ]
    ok = all(r["ok"] for r in results)
    failed = [r["gate"] for r in results if not r["ok"]]
    log.info("틱톡 게이트 %s%s", "통과" if ok else "실패", "" if ok else f" — {failed}")
    return {"ok": ok, "failed": failed, "results": results, "version": VERSION}
