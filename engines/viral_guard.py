"""
engines/viral_guard.py
===================================================
C-20 바이럴 콘텐츠 L1 보안 필터.

VERSION = "1.0.0"

역할:
  1. 실존 인물 감지 → 이미지 생성 거부 (safe_to_generate=False)
  2. IP/브랜드 감지 → 프롬프트 내 일반명사 치환 (lamborghini → luxury sports car)

설계 출처:
  Notion "🎨 C-20 바이럴 고도화 상세 설계서 v2.0 (2026-04-21)"

v1.0.0 (2026-04-21):
  - 최초 작성
  - _BLOCKED_IP 50+ 개, _BLOCKED_PERSON 40+ 명
  - sanitize_image_prompt() 단일 진입점
"""
import logging
import re

logger = logging.getLogger(__name__)

VERSION = "1.0.0"


# ──────────────────────────────────────────────────────────────
# 블랙리스트 — IP/브랜드 (대소문자 무관, 소문자로 통일 저장)
# ──────────────────────────────────────────────────────────────

_BLOCKED_IP = {
    # 엔터테인먼트 IP/캐릭터
    "marvel", "disney", "pixar", "nintendo", "pokemon", "pokémon",
    "harry potter", "star wars", "avengers", "mickey", "mickey mouse",
    "superman", "batman", "spider-man", "spiderman", "iron man", "ironman",
    "hello kitty", "sanrio", "doraemon", "one piece", "naruto",
    "dragon ball", "hogwarts",

    # 자동차 브랜드
    "lamborghini", "ferrari", "porsche", "bugatti", "maserati",
    "rolls-royce", "rolls royce", "bentley", "aston martin",
    "mercedes", "mercedes-benz", "bmw", "audi", "tesla",

    # 명품 브랜드
    "rolex", "patek philippe", "audemars piguet", "hermes", "hermès",
    "louis vuitton", "chanel", "gucci", "prada", "dior", "fendi",
    "cartier", "tiffany", "bulgari", "bvlgari", "versace", "balenciaga",

    # IT/테크/의류 브랜드
    "apple", "iphone", "macbook", "nike", "adidas", "samsung", "galaxy",
    "google", "amazon", "meta", "facebook", "netflix", "spotify",

    # 한글 브랜드 (한글 원어로만 등장 시 대비)
    "람보르기니", "페라리", "포르쉐", "롤스로이스", "벤츠", "아우디",
    "롤렉스", "에르메스", "루이비통", "샤넬", "구찌", "프라다",
    "나이키", "아디다스",
}


# ──────────────────────────────────────────────────────────────
# 블랙리스트 — 실존 인물
# ──────────────────────────────────────────────────────────────

_BLOCKED_PERSON = {
    # 정치인
    "donald trump", "trump", "joe biden", "biden", "obama",
    "xi jinping", "putin", "zelensky",
    "yoon", "yoon suk-yeol", "이재명", "윤석열",

    # 연준/중앙은행
    "jerome powell", "powell", "janet yellen", "yellen",
    "christine lagarde", "lagarde",

    # 기업인/투자자
    "elon musk", "musk", "mark zuckerberg", "zuckerberg",
    "jeff bezos", "bezos", "bill gates", "gates",
    "warren buffett", "buffett", "charlie munger",
    "sam altman", "altman", "tim cook", "cook",
    "jensen huang", "huang", "cathie wood",

    # 연예인 (한/영)
    "bts", "blackpink", "twice", "newjeans",
    "taylor swift", "kim kardashian",
    "아이유", "김태희", "송혜교", "손흥민", "이강인",
}


# ──────────────────────────────────────────────────────────────
# IP → 일반명사 치환 맵
# ──────────────────────────────────────────────────────────────

_REPLACEMENTS = {
    # 자동차
    "lamborghini":  "luxury sports car",
    "ferrari":      "luxury sports car",
    "porsche":      "luxury sports car",
    "bugatti":      "luxury sports car",
    "maserati":     "luxury sports car",
    "rolls-royce":  "luxury sedan",
    "rolls royce":  "luxury sedan",
    "bentley":      "luxury sedan",
    "aston martin": "luxury sports car",
    "mercedes":     "premium sedan",
    "mercedes-benz":"premium sedan",
    "bmw":          "premium sedan",
    "audi":         "premium sedan",
    "tesla":        "electric vehicle",

    # 명품
    "rolex":           "luxury watch",
    "patek philippe":  "luxury watch",
    "audemars piguet": "luxury watch",
    "hermes":          "luxury handbag",
    "hermès":          "luxury handbag",
    "louis vuitton":   "luxury handbag",
    "chanel":          "luxury handbag",
    "gucci":           "luxury handbag",
    "prada":           "luxury handbag",
    "dior":            "luxury handbag",
    "cartier":         "luxury jewelry",
    "tiffany":         "luxury jewelry",
    "bulgari":         "luxury jewelry",
    "bvlgari":         "luxury jewelry",

    # 테크/의류
    "iphone":  "premium smartphone",
    "macbook": "premium laptop",
    "apple":   "premium tech device",
    "nike":    "premium sneakers",
    "adidas":  "premium sneakers",

    # 한글 원어
    "람보르기니": "luxury sports car",
    "페라리":     "luxury sports car",
    "포르쉐":     "luxury sports car",
    "벤츠":       "premium sedan",
    "롤렉스":     "luxury watch",
    "에르메스":   "luxury handbag",
    "루이비통":   "luxury handbag",
    "샤넬":       "luxury handbag",
    "구찌":       "luxury handbag",
    "나이키":     "premium sneakers",
    "아디다스":   "premium sneakers",
}


# ──────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────

def sanitize_image_prompt(
    prompt: str,
    opt_a_en: str,
    opt_b_en: str,
    condition_en: str = "",
) -> tuple[str, bool, list[str]]:
    """
    이미지 프롬프트 L1 보안 필터.

    처리 순서:
      1. 실존 인물 감지 → 즉시 safe_to_generate=False 반환 (이미지 생성 거부)
      2. IP/브랜드 감지 → 프롬프트 내 일반명사 치환
      3. 경고 리스트와 함께 반환

    Args:
        prompt:       원본 이미지 프롬프트
        opt_a_en:     영어 선택지 A
        opt_b_en:     영어 선택지 B
        condition_en: 영어 조건 (선택)

    Returns:
        (sanitized_prompt, safe_to_generate, warnings)

        - sanitized_prompt: IP 치환이 완료된 프롬프트
        - safe_to_generate: False면 호출자는 이미지 생성을 거부해야 함
        - warnings: 로그 기록용 감지 이력 (예: ["blocked_person:trump"])
    """
    warnings: list[str] = []

    # 검사 대상을 합쳐서 소문자화 (감지용)
    haystack = f"{opt_a_en} {opt_b_en} {condition_en} {prompt}".lower()

    # ── Step 1: 실존 인물 감지 → 즉시 거부 ──────────────────
    for person in _BLOCKED_PERSON:
        if person.lower() in haystack:
            warnings.append(f"blocked_person:{person}")
            logger.warning(
                f"[ViralGuard] 실존 인물 감지 → 이미지 생성 거부: {person}"
            )
            return (prompt, False, warnings)

    # ── Step 2: IP/브랜드 치환 ───────────────────────────────
    sanitized = prompt
    for ip, replacement in _REPLACEMENTS.items():
        pattern = re.compile(re.escape(ip), re.IGNORECASE)
        if pattern.search(sanitized):
            warnings.append(f"blocked_ip:{ip}→{replacement}")
            sanitized = pattern.sub(replacement, sanitized)

    # ── Step 3: 치환 맵에 없는 IP도 감지만 로그 (방어적) ────
    for ip in _BLOCKED_IP:
        ip_lower = ip.lower()
        if ip_lower in haystack and ip_lower not in _REPLACEMENTS:
            warnings.append(f"detected_ip_no_replacement:{ip}")

    if warnings:
        logger.info(f"[ViralGuard] 필터 동작: {len(warnings)}건 감지")
        logger.debug(f"[ViralGuard] 상세: {warnings}")

    return (sanitized, True, warnings)


def is_person_blocked(text: str) -> bool:
    """
    편의 함수. 텍스트 내 실존 인물 포함 여부만 확인.
    테스트 및 외부 사전 체크용.
    """
    haystack = text.lower()
    return any(p.lower() in haystack for p in _BLOCKED_PERSON)


def is_ip_blocked(text: str) -> bool:
    """
    편의 함수. 텍스트 내 IP/브랜드 포함 여부만 확인.
    """
    haystack = text.lower()
    return any(ip.lower() in haystack for ip in _BLOCKED_IP)
