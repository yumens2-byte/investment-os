"""
gen/hero_shorts/publish_targets/youtube/gates.py — 유튜브 전용 게이트 (Y-G1~Y-G6)
================================================================================
설계서 §4.3. 통과(ok=True)가 아니면 전송하지 않는다(fail-closed).

    Y-G1 길이 ≤ 180초 · 비율 9:16          (F4)
    Y-G2 containsSyntheticMedia = true      (F5 · T-G2 대응)
    Y-G3 제목 비어있지 않고 ≤100자,
         또한 제목 ≠ 회차 태그 단독          (F6 — "Ep88" 결함 차단)
    Y-G4 visibility ∈ {public,private,unlisted}, 정기 발행은 public (F5)
    Y-G5 태그 합계 ≤ 500자 · 태그당 ≤ 100자  (F4)
    Y-G6 설명 ≤ 5,000자                     (F4)

**Y-G3 이 이 라인의 존재 이유다.** 유튜브는 제목 미지정 시 content 첫 줄을
제목으로 쓰고, 현행 캡션 첫 줄은 회차 태그(`Ep88`)다. 이 게이트가 없으면
공개 제목이 `Ep88` 이 된다 — 실측으로 ep88 레코드의 `title` 이 정확히 `"Ep88"` 이다.
"""
import logging
import re

log = logging.getLogger("hero_shorts.publish_targets.youtube.gates")

VERSION = "1.0.0"

GATE_IDS = ("Y-G1", "Y-G2", "Y-G3", "Y-G4", "Y-G5", "Y-G6")

MAX_DURATION_SEC = 180          # F4 — 쇼츠 3분
REQUIRED_RATIO = "9:16"         # F4
TITLE_LIMIT = 100               # F4
DESC_LIMIT = 5000               # F4
TAG_TOTAL_LIMIT = 500           # F4
TAG_ITEM_LIMIT = 100            # F4

VALID_VISIBILITIES = ("public", "private", "unlisted")
REGULAR_VISIBILITY = "public"   # 정기 발행 기본 — E11 승인값

_NORM_RE = re.compile(r"[\s\-_#:：.·/]+")


def _norm(value) -> str:
    return _NORM_RE.sub("", str(value or "")).lower()


def is_label_only(title, ep_label_value) -> bool:
    """제목이 회차 태그 단독인가. `Ep88` · `ep 88` · `#EP-88` 모두 해당."""
    t, lb = _norm(title), _norm(ep_label_value)
    if not lb:
        return False
    return t == lb


def gate_yg1(duration_sec, ratio=REQUIRED_RATIO):
    problems = []
    if duration_sec is None:
        problems.append("길이 미확정")
    else:
        try:
            d = float(duration_sec)
        except (TypeError, ValueError):
            problems.append(f"길이 값이 수치가 아님: {duration_sec!r}")
            d = None
        if d is not None:
            if d <= 0:
                problems.append("길이 0 이하")
            elif d > MAX_DURATION_SEC:
                problems.append(f"길이 {d}s > 쇼츠 상한 {MAX_DURATION_SEC}s")
    if ratio != REQUIRED_RATIO:
        problems.append(f"비율 {ratio} != {REQUIRED_RATIO}")
    return {"gate": "Y-G1", "ok": not problems, "problems": problems}


def gate_yg2(contains_synthetic_media):
    ok = contains_synthetic_media is True
    return {"gate": "Y-G2", "ok": ok,
            "problems": [] if ok else ["containsSyntheticMedia 미설정 — 합성미디어 고지 누락(필수)"]}


def gate_yg3(title, ep_label_value=None, source_title=None):
    """Y-G3 — 합성 제목과 **원본 회차 제목** 양쪽을 검사한다.

    합성 제목(`f"{라벨} {회차제목}"`)만 보면 이 결함을 놓친다:
    회차 제목이 `Ep88` 이면 합성 결과가 `Ep88 Ep88` 이 되어 '라벨 단독'이 성립하지 않는다.
    실제 결함은 **원본 레코드의 title 이 라벨 그 자체**인 것이므로 원본을 함께 본다.
    """
    t = str(title or "").strip()
    src = str(source_title or "").strip()
    problems = []
    if not t:
        problems.append("제목 없음 — 유튜브가 content 첫 줄을 제목으로 사용하게 된다")
    elif len(t) > TITLE_LIMIT:
        problems.append(f"제목 {len(t)}자 > {TITLE_LIMIT}자")
    if t and is_label_only(t, ep_label_value):
        problems.append(
            f"제목 '{t}' 이(가) 회차 태그 단독 — 공개 제목 결함(F6). 회차 제목 필요")
    if src and is_label_only(src, ep_label_value):
        problems.append(
            f"회차 제목이 '{src}' — 회차 태그 단독(F6 데이터 결함). "
            "원장 레코드의 title 을 실제 회차 제목으로 교정해야 한다")
    return {"gate": "Y-G3", "ok": not problems, "problems": problems}


def gate_yg4(visibility, *, regular_publish=True, expected=REGULAR_VISIBILITY):
    v = str(visibility or "").strip().lower()
    problems = []
    if v not in VALID_VISIBILITIES:
        problems.append(f"visibility '{visibility}' ∉ {list(VALID_VISIBILITIES)}")
    elif regular_publish and expected and v != str(expected).lower():
        problems.append(f"정기 발행 visibility '{v}' != '{expected}'")
    return {"gate": "Y-G4", "ok": not problems, "problems": problems}


def gate_yg5(tags):
    items = [str(t) for t in (tags or [])]
    total = sum(len(t) for t in items)
    problems = []
    over = [t for t in items if len(t) > TAG_ITEM_LIMIT]
    if over:
        problems.append(f"태그 {TAG_ITEM_LIMIT}자 초과: {over}")
    if total > TAG_TOTAL_LIMIT:
        problems.append(f"태그 합계 {total}자 > {TAG_TOTAL_LIMIT}자")
    return {"gate": "Y-G5", "ok": not problems, "problems": problems,
            "total": total, "count": len(items)}


def gate_yg6(description):
    n = len(str(description or ""))
    return {"gate": "Y-G6", "ok": n <= DESC_LIMIT,
            "problems": [] if n <= DESC_LIMIT else [f"설명 {n}자 > {DESC_LIMIT}자"],
            "length": n}


def run_youtube_gates(duration_sec=None, ratio=REQUIRED_RATIO, title=None,
                      ep_label_value=None, visibility=REGULAR_VISIBILITY,
                      tags=None, description=None,
                      contains_synthetic_media=True, regular_publish=True,
                      source_title=None):
    """Y-G1~Y-G6 전건 실행. ok=False 이면 유튜브로 전송하지 않는다."""
    results = [
        gate_yg1(duration_sec, ratio),
        gate_yg2(contains_synthetic_media),
        gate_yg3(title, ep_label_value, source_title=source_title),
        gate_yg4(visibility, regular_publish=regular_publish),
        gate_yg5(tags),
        gate_yg6(description),
    ]
    ok = all(r["ok"] for r in results)
    failed = [r["gate"] for r in results if not r["ok"]]
    log.info("유튜브 게이트 %s%s", "통과" if ok else "실패", "" if ok else f" — {failed}")
    return {"ok": ok, "failed": failed, "results": results, "version": VERSION}
