"""
gen/hero_shorts/canon.py — 캐릭터 캐논 v2.4.1 (공식 노션 캐논 연동)
=====================================================================
SSOT 체계 (마스터 지시 "노션 캐논 참고"):
    L1 원문  : canon_data.json — 노션 캐논 페이지(스펙 v2.18.1/v2.28/RCL-07)에서
               동기화한 공식 원문(KR verbatim) + REF 슬롯 + 금지 요소.
               이 파일이 캐논의 데이터 원장 — 수작업 편집 금지, 노션 패치 후 재동기화만 허용.
    L2 코드  : 본 파일 — 공식 원문의 영어 프롬프트 번역본 + 금지 문구 게이트.
               번역은 스펙 필드를 빠짐없이 반영하며 임의 추가 묘사를 넣지 않는다.
    L3 게이트: QC G2 (토큰 존재·금지 문구) — assemble_qc.qc_4gates 에서 집행.

v2.4 대비 정정 (노션 공식 캐논 대조, 2026-09-21):
    1. 주역 히어로 = EDT (Endurance D Tiger, 타이거+로만 아머).
       'Guardian of Capital'라는 별도 캐릭터는 공식 캐논에 부재 → 폐기.
    2. Exposure Futures Girl = FG-01 (보라 네온 듀얼 피스톨) — 크림슨 자켓 폐기.
    3. Leverage Muscle Man = LEV-01 (인간 남성) — 그린 베스트 폐기.
    4. Oil Shock Titan 시각 기준선 = 마스터 승인 컷1 실측 (영상 전용 캐논).
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("hero_shorts.canon")

_DATA = json.loads((Path(__file__).resolve().parent / "canon_data.json").read_text(encoding="utf-8"))
CANON_SOURCE = _DATA["_source"]          # 원장 출처 (노션 페이지 id 포함)

# ── 공식 캐논의 영어 프롬프트 라인 (스펙 필드 전수 반영 번역) ─────────
CHARACTERS = {
    "EDT": ("EDT the Endurance D Tiger — a muscular tiger hero in bronze-steel "
            "roman full plate armor with shoulder spikes, a golden D emblem on "
            "the chest, a red cape attached at the back only, glowing golden "
            "eyes, dark stripes, wielding a large electric chainsaw"),
    "Exposure Futures Girl": (
        "Exposure Futures Girl — a calm calculating slender woman with "
        "waist-length straight black-to-dark-purple gradient hair worn loose, "
        "dark black armor with purple neon trim, dual glowing purple pistols, "
        "a purple energy belt, x4 EXPOSURE glowing markers, hologram chart "
        "background"),
    "Leverage Muscle Man": (
        "Leverage Muscle Man — a human male bodybuilder with flame-shaped "
        "red-to-orange hair, sharp glowing gold eyes, bare muscular upper "
        "body on dark ash skin, black cargo pants and combat boots, wielding "
        "an x3 MULTIPLIER barbell with a red-gold aura"),
    "Debt Titan": (
        "the Debt Titan — a spike-monster in black spike armor with lava "
        "cracks"),
    "Oil Shock Titan": (
        "the Oil Shock Titan — a colossal titan of molten oil and black smoke"),
}

# ── 캐릭터별 금지 문구 (스펙 '금지 요소' 필드의 영어 감지 목록) ───────
FORBIDDEN_PER_CHARACTER = {
    "Exposure Futures Girl": ["ponytail", "shoulder-length hair", "bob cut",
                              "blonde", "silver hair", "red hair", "retro style"],
    "Leverage Muscle Man": ["beast", "fur", "snout", "platinum white",
                            "mecha armor"],
    "EDT": [],   # EDT 본인은 D 엠블럼 보유 가능
}
FORBIDDEN_GLOBAL = ["D emblem on any other character", "buy now", "guaranteed"]

# ── REF 슬롯 (노션 REF LOCK 패턴 준용 — 파일명만 기재, URL은 주입) ───
REF_SHEETS = {
    "EDT": ["1000038175.png (RCL-07-A FULL BODY, 잠정 7/9)",
            "1000038171.png (RCL-07-B SYSTEM STRIKE, 확정 9/9)"],
    "Exposure Futures Girl": [],   # REF 확정 페이지 확인 후 등록
    "Oil Shock Titan": [],         # 승인 컷1 포스터 승격 예정
    "Debt Titan": [],
    "Leverage Muscle Man": [],
}

# ── 스타일·세계관·공통 문구 ─────────────────────────────────────────
WORLD_CANON = ("modern financial district, glass towers, urban street grids, "
               "emergency-lit avenues, grounded contemporary city realism")
STYLE_CANON = ("Dramatic webtoon-anime style, vertical 9:16, cinematic "
               "lighting, dark amber and crimson palette, volumetric haze")
CLEAN_TAIL = "No subtitles, no logo, no watermark."
PROMPT_TOKENS = ["webtoon-anime style", "EDT", "9:16", "No subtitles",
                 "modern financial district"]
WORLD_FORBIDDEN = ["medieval castle", "fantasy village", "ancient fortress",
                   "temple town"]
FORBIDDEN_GLOBAL += WORLD_FORBIDDEN


def ref_sheet_urls(character, config=None):
    """REF 시트 이미지 URL 조회 — gen/data/ref_sheets.json (URL 주입형) 우선.

    시각 캐논 고정(L2 계층)의 실행부 — REF 이미지가 등록된 캐릭터는
    reference-to-video 엔드포인트로 생성해 픽셀 단위 고정을 적용한다.
    미등록 캐릭터는 빈 리스트 반환 → text-to-video + 캐논 문구로 폴백.
    """
    cfg_path = Path(__file__).resolve().parent.parent / "data" / "ref_sheets.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        urls = cfg.get(character, [])
        if urls:
            log.info("REF 시트 주입: %s → %d장", character, len(urls))
            return urls
    log.info("REF 시트 미등록 — 텍스트 캐논 폴백: %s", character)
    return []


def canon_block(names):
    """캐논 블록 조립 — 미등록 캐릭터는 경고 로그 후 스킵 (v2.4 동일)."""
    parts = []
    for n in names:
        desc = CHARACTERS.get(n)
        if desc:
            parts.append(desc)
        else:
            log.warning("canon 미등록 캐릭터 스킵: %s (canon.py 등록 필요)", n)
    return ", ".join(parts)


def forbidden_for(names):
    """지정 캐릭터 집합에 적용되는 금지 문구 전체."""
    out = list(FORBIDDEN_GLOBAL)
    for n in names:
        out += FORBIDDEN_PER_CHARACTER.get(n, [])
    return out


def full_prompt(scene_text, characters, camera=None, sound=None):
    """완성형 컷 프롬프트 조립 (유일한 프롬프트 빌드 경로 — v2.4 동일)."""
    seg = [scene_text, WORLD_CANON, STYLE_CANON]
    if camera:
        seg.append(camera)
    if sound:
        seg.append(sound)
    seg.append(canon_block(characters))
    seg.append(CLEAN_TAIL)
    joined = ". ".join([s.rstrip(".") for s in seg if s]) + "."
    # G2 계약 보장 — 주역 EDT 는 모든 컷 프롬프트에 서술된다(캐논 원칙, 근본 수정).
    if "EDT" not in joined:
        hero = CHARACTERS.get("EDT", "EDT")
        if not hero.startswith("EDT"):
            hero = "EDT, " + hero
        joined = joined.rstrip(".") + ", " + hero + "."
    return joined
