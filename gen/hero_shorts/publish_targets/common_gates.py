"""
gen/hero_shorts/publish_targets/common_gates.py — 플랫폼 무관 공통 게이트
==========================================================================
설계서 §4.2. C-G1~C-G5 는 모든 타깃에 공통 적용된다.
통과(ok=True)가 아니면 전송하지 않는다(fail-closed).

    C-G1 미디어   : 존재 · 비율 9:16 · 길이 > 0
    C-G2 면책문   : 투자 참고/권유 아님 문구 포함
    C-G3 금지어   : JCU G-4 금지어 0건
    C-G4 문자     : 영상 내 문자 0건 (D5)
    C-G5 승인     : approval.status == "APPROVED"
"""
import logging

log = logging.getLogger("hero_shorts.publish_targets.common_gates")

VERSION = "1.0.0"

REQUIRED_RATIO = "9:16"

DISCLAIMER_MARKS = ("투자 참고", "투자 권유 아님", "not investment advice")

FORBIDDEN_PHRASES = (
    "반드시", "무조건", "보장", "확실히", "내일 오른다",
    "추천", "사라", "팔아라",
)

APPROVED = "APPROVED"


def gate_cg1(media_url=None, duration_sec=None, ratio=REQUIRED_RATIO, media_kind="video"):
    """C-G1 — 미디어 존재·비율·길이."""
    problems = []
    if not media_url:
        problems.append("media_url 없음 — 업로드 단계 선행 필요")
    if duration_sec is None or duration_sec <= 0:
        problems.append("길이 미확정")
    if ratio != REQUIRED_RATIO:
        problems.append(f"비율 {ratio} != {REQUIRED_RATIO}")
    if media_kind != "video":
        problems.append(f"미디어 종류 '{media_kind}' — 영상만 허용")
    return {"gate": "C-G1", "ok": not problems, "problems": problems}


def gate_cg2(text):
    """C-G2 — 면책문 포함."""
    t = text or ""
    ok = any(m in t for m in DISCLAIMER_MARKS)
    return {"gate": "C-G2", "ok": ok,
            "problems": [] if ok else ["면책문 미포함 — 발행 금지(JCU G-4)"]}


def gate_cg3(text):
    """C-G3 — JCU G-4 금지어 0건."""
    t = text or ""
    banned = sorted({p for p in FORBIDDEN_PHRASES if p in t})
    return {"gate": "C-G3", "ok": not banned,
            "problems": [] if not banned else [f"금지어 감지(JCU G-4): {banned}"]}


def gate_cg4(text_hits):
    """C-G4 — 영상 내 문자 0건(D5)."""
    hits = [h for h in (text_hits or []) if str(h).strip()]
    return {"gate": "C-G4", "ok": not hits,
            "problems": [] if not hits else [f"영상 내 문자 감지(D5 위반): {hits}"]}


def gate_cg5(approval_status):
    """C-G5 — 승인 완료."""
    ok = str(approval_status or "").upper() == APPROVED
    return {"gate": "C-G5", "ok": ok,
            "problems": [] if ok else [f"승인 미완료(status={approval_status!r}) — 발행 금지"]}


def run_common_gates(media_url=None, duration_sec=None, ratio=REQUIRED_RATIO,
                     media_kind="video", texts=None, text_hits=None,
                     approval_status=None):
    """공통 게이트 전건 실행. texts = 검사할 본문 문자열들의 목록(면책문·금지어 대상).

    ok=False 이면 어떤 타깃에도 전송하지 않는다.
    """
    joined = "\n".join([t for t in (texts or []) if t])
    results = [
        gate_cg1(media_url, duration_sec, ratio, media_kind),
        gate_cg2(joined),
        gate_cg3(joined),
        gate_cg4(text_hits),
        gate_cg5(approval_status),
    ]
    ok = all(r["ok"] for r in results)
    failed = [r["gate"] for r in results if not r["ok"]]
    log.info("공통 게이트 %s%s", "통과" if ok else "실패", "" if ok else f" — {failed}")
    return {"ok": ok, "failed": failed, "results": results, "version": VERSION}
