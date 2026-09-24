"""
gen/hero_shorts/caption.py — 발행 캡션 자동 생성
================================================
컷계획(plan)과 승인된 ROLE_LINES 대사에서만 문장을 만든다.
새 사실·수치·권유 문구를 만들어내지 않는다(팩트체크 원칙).
"""
import json
import logging
from pathlib import Path

from .voiceover import line_for_role

log = logging.getLogger("hero_shorts.caption")

DISCLAIMER = "⚠️ 투자 참고 정보, 투자 권유 아님"
HASHTAGS = "#EDT #시장만화 #주식 #매크로 #숏폼"


def build_caption(plan):
    """plan(title/type/outcome/cuts.role) → 발행 캡션 텍스트. 결정론적(같은 plan → 같은 캡션)."""
    title = str(plan.get("title") or "").strip()
    cuts = sorted(plan.get("cuts", []), key=lambda c: c.get("cut_no", 0))
    if not cuts:
        raise RuntimeError("캡션 생성 불가 — plan에 컷이 없음")
    bullets = ["- " + line_for_role(c.get("role", ""), title, plan.get("type", ""), plan.get("outcome", ""))
               for c in cuts]
    body = "\n".join([title, "", "포인트", *bullets, "", DISCLAIMER, "", HASHTAGS])
    return body.strip() + "\n"


def save_caption(plan_file, out_file):
    """plan JSON → 캡션 txt 저장. out_file 기본은 호출부에서 지정."""
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    text = build_caption(plan)
    out_path = Path(out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    log.info("캡션 저장: %s", out_path)
    return str(out_path)
