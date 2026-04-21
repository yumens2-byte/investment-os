"""
engines/viral_engine.py (C-6 / C-18 / C-19 / C-20 통합)
===================================================
바이럴 콘텐츠 통합 엔진.

VERSION = "1.6.0"

v1.6.0 (2026-04-21):
  - [신규] L1 프롬프트 필터 — engines/viral_guard.py 연동
    - IP/브랜드 → 일반명사 치환 (lamborghini → luxury sports car)
    - 실존 인물 감지 → 이미지 생성 거부 (텍스트만 발행)
  - [신규] 이미지 시각화 모드 3종 — engines/viral_prompts.py 연동
    - object: 카 10, 11 (플렉스/소비)
    - situation: 카 7, 9 (외모/배우자) — 실루엣 전용
    - lifestyle: 예비 매핑
  - [Feature Flag] VIRAL_GUARD_L1 (기본 true), VIRAL_IMAGE_MODE_V2 (기본 true)
  - [보존] run_viral_c20() 큰 흐름, _generate_dilemma_viral(), _should_generate_image()
  - 설계서: Notion "🎨 C-20 바이럴 고도화 상세 설계서 v2.0"

v1.5.6 (2026-04-16):

v1.5.6 (2026-04-16):
  - needs_image 항상 True 반환 버그 수정
    - 원인: JSON 예시에 "needs_image": true 하드코딩 → Gemini가 복사
    - 수정1: JSON 예시값 true → false (기본값 보수적으로)
    - 수정2: needs_image 판단 기준 프롬프트 강화 + 명시적 예시 추가
    - 수정3: _C20_CATEGORIES 모듈 레벨 상수로 분리
    - 수정4: _should_generate_image() 추가
        1차: 카테고리 키워드 기반 Python 결정 (확실한 경우)
        2차: Gemini 응답 fallback (1차 키워드 미매칭 시)
        결과: 12개 카테고리 중 4개만 True (외모/람보르기니/럭셔리/배우자외모)

v1.5.5 (2026-04-16):
  - _is_korean(): 루프 내부 중복 정의 → 모듈 레벨 함수로 이동
    (for 루프 3회 iteration마다 재정의되던 문제 해결)
  - C-6 _generate_quiz(): 원본 함수 전체 주석 코드 복원
    (v1.5.4에서 1줄로 축약된 것 → v1.5.0 설계 준수: 주석 보존)

v1.5.4 (2026-04-16):
  - _generate_dilemma_viral(): Gemini JSON 파싱 실패 대응
    - _repair_json() 헬퍼 추가 (trailing comma, 제어문자, 코드블록 제거)
    - 재시도 3회 + temperature 단계 하향 (1.0 → 0.9 → 0.8)
    - response_json=False → 원문 수신 후 직접 파싱 (gateway JSON 파싱 우회)
  - 파일 로그 구조 추가
    - 경로: logs/viral/viral_YYYYMMDD.log
    - 모듈 로드 시 FileHandler 자동 등록

v1.5.3 (2026-04-16):
  - IndentationError 수정 (오염 로그 4줄 삭제)
  - content 할당 누락 수정
  - content_type NameError 수정

v1.5.2 (2026-04-14):
  - 한/영 혼입 버그 수정, max_tokens 600

v1.5.1 (2026-04-14):
  - 프롬프트 전면 교체, 카테고리 12개, temperature 1.0

v1.5.0 (2026-04-14):
  - C-6 금융퀴즈 중단

v1.4.0 (2026-04-14):
  - C-20 이미지 고도화
"""
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

X_TWEET_MAX = 4000
X_REPLY_MAX = 4000


# ──────────────────────────────────────────────────────────────
# 파일 로그 초기화
# ──────────────────────────────────────────────────────────────

def _setup_file_logger() -> None:
    """
    viral_engine 전용 파일 로거 등록.
    경로: logs/viral/viral_YYYYMMDD.log
    중복 등록 방지 포함.
    """
    try:
        log_dir  = os.path.join("logs", "viral")
        os.makedirs(log_dir, exist_ok=True)

        date_str = datetime.now(KST).strftime("%Y%m%d")
        log_path = os.path.join(log_dir, f"viral_{date_str}.log")

        for h in logger.handlers:
            if isinstance(h, logging.FileHandler) and log_path in h.baseFilename:
                return

        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)
        logger.info(f"[Viral] 파일 로그 등록: {log_path}")
    except Exception as e:
        logger.warning(f"[Viral] 파일 로그 등록 실패 (영향 없음): {e}")


_setup_file_logger()


# ──────────────────────────────────────────────────────────────
# JSON 복구 헬퍼
# ──────────────────────────────────────────────────────────────

def _repair_json(text: str) -> str:
    """
    Gemini 응답 JSON 복구.
    처리:
      1. 마크다운 코드블록 제거 (```json ... ```)
      2. 제어문자 제거 (탭/개행 제외)
      3. trailing comma 제거 (, 뒤 } 또는 ])
    """
    if not text:
        return text

    # 1. 마크다운 코드블록
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "").strip()

    # 2. 제어문자 제거
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # 3. trailing comma
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text.strip()


# ──────────────────────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────────────────────

def _is_korean(text: str) -> bool:
    """한글 포함 여부 확인 (유니코드 AC00-D7A3). 한/영 혼입 방어용."""
    return any("\uAC00" <= c <= "\uD7A3" for c in text)


def _is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() != "false"


def _today_hash() -> int:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return int(hashlib.md5(f"viral_{today}".encode()).hexdigest(), 16)


def _is_sunday() -> bool:
    return datetime.now(KST).weekday() == 6


def should_run(session: str) -> bool:
    force = os.environ.get("FORCE_RUN", "false").lower() == "true"
    if force:
        logger.info("[Viral] FORCE_RUN=true → 강제 실행")
        return True
    if session in ("viral_afternoon", "viral_c20", "viral_c20_morning", "viral_c20_evening"):
        return True
    logger.info(f"[Viral] 지원하지 않는 세션 → 스킵 (session={session})")
    return False


# ──────────────────────────────────────────────────────────────
# 콘텐츠 선택
# ──────────────────────────────────────────────────────────────

def _select_content_type() -> str:
    h     = _today_hash()
    types = ["money", "dilemma", "vote"] if _is_sunday() else ["money", "dilemma"]
    return types[h % len(types)]


# ──────────────────────────────────────────────────────────────
# C-6: 금융 퀴즈 — ⛔ 중단 (v1.5.0)
# 함수 코드 보존. _select_content_type()에서 제외됨.
# 재활성화 시: _select_content_type() types 리스트에 "quiz" 추가
# ──────────────────────────────────────────────────────────────

# def _generate_quiz() -> dict:
#     """
#     Gemini로 금융 퀴즈 1개 생성.
#     Returns: {success, tweet, reply, tg_text, category}
#     """
#     try:
#         from core.gemini_gateway import call, is_available
#         if not is_available():
#             return {"success": False, "error": "Gemini 미사용"}
#
#         prompt = (
#             "투자/금융 퀴즈 1개를 JSON으로 생성해줘.\n\n"
#             "조건:\n"
#             "- 4지선다 객관식 (A/B/C/D)\n"
#             "- 카테고리: 투자 역사, 금융 상식, ETF 지식, 경제 용어, 유명 투자자 중 랜덤\n"
#             "- 난이도: 초급~중급 (일반인도 참여 가능)\n"
#             "- 정답 해설 2~3문장\n"
#             "- 재미있는 사실(fun fact) 1개 포함\n\n"
#             "JSON 형식:\n"
#             "{\n"
#             '  "question": "질문",\n'
#             '  "options": {"A": "...", "B": "...", "C": "...", "D": "..."},\n'
#             '  "answer": "D",\n'
#             '  "explanation": "해설",\n'
#             '  "fun_fact": "재미있는 사실",\n'
#             '  "category": "카테고리"\n'
#             "}\n"
#         )
#
#         result = call(
#             prompt=prompt,
#             model="flash-lite",
#             max_tokens=500,
#             temperature=0.9,
#             response_json=True,
#         )
#
#         if not result.get("success") or not result.get("data"):
#             return {"success": False, "error": "Gemini 퀴즈 생성 실패"}
#
#         quiz        = result["data"]
#         q           = quiz.get("question", "")
#         opts        = quiz.get("options", {})
#         answer      = quiz.get("answer", "")
#         explanation = quiz.get("explanation", "")
#         fun_fact    = quiz.get("fun_fact", "")
#         category    = quiz.get("category", "금융상식")
#
#         quiz_no = int(datetime.now(KST).strftime("%j"))
#
#         tweet = (
#             f"🧠 금융 퀴즈 #{quiz_no}\n\n"
#             f"Q. {q}\n\n"
#             f"A) {opts.get('A', '')}\n"
#             f"B) {opts.get('B', '')}\n"
#             f"C) {opts.get('C', '')}\n"
#             f"D) {opts.get('D', '')}\n\n"
#             f"정답은 다음 트윗에서! 👇\n"
#             f"댓글로 예상 정답 남겨주세요 🔥\n\n"
#             f"#금융퀴즈 #투자상식 #ETF투자"
#         )
#
#         reply = (
#             f"✅ 정답: {answer}) {opts.get(answer, '')}\n\n"
#             f"{explanation}\n\n"
#             f"💡 {fun_fact}\n\n"
#             f"내일 퀴즈도 기대해주세요! 🧠"
#         )
#
#         tg_text = (
#             f"🧠 <b>금융 퀴즈 #{quiz_no}</b>\n\n"
#             f"Q. {q}\n\n"
#             f"A) {opts.get('A', '')}\n"
#             f"B) {opts.get('B', '')}\n"
#             f"C) {opts.get('C', '')}\n"
#             f"D) {opts.get('D', '')}\n\n"
#             f"✅ 정답: {answer}) {opts.get(answer, '')}\n"
#             f"{explanation}\n\n"
#             f"💡 {fun_fact}"
#         )
#
#         logger.info(f"[Viral] C-6 퀴즈 생성: {category} | 정답={answer}")
#
#         return {
#             "success":  True,
#             "type":     "quiz",
#             "tweet":    tweet[:X_TWEET_MAX],
#             "reply":    reply[:X_REPLY_MAX],
#             "tg_text":  tg_text,
#             "category": category,
#             "has_reply": True,
#         }
#
#     except Exception as e:
#         logger.warning(f"[Viral] C-6 퀴즈 생성 실패: {e}")
#         return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# C-18: 물가/자산 비교
# ──────────────────────────────────────────────────────────────

def _generate_money_compare() -> dict:
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return {"success": False, "error": "Gemini 미사용"}

        types = [
            "시간 여행형: 특정 금액($100 또는 $1,000)으로 과거 vs 현재 살 수 있는 것 비교",
            "물가 비교형: 2006 vs 2026 주요 물가 비교 (빅맥, 집값, 휘발유, S&P 500)",
            "놀라운 숫자형: 유명 투자자나 기업의 놀라운 자산 성장 스토리",
        ]
        selected_type = random.choice(types)

        prompt = (
            f"투자/금융 관련 '놀라운 숫자' 콘텐츠를 트윗 형식으로 작성해줘.\n\n"
            f"타입: {selected_type}\n\n"
            "조건:\n"
            "- 300~500자 한국어\n"
            "- 숫자 비교가 핵심 (충격적인 대비)\n"
            "- 이모지 3~4개\n"
            "- 마지막에 '여러분은 어떻게 생각하세요? 👇' 같은 CTA\n"
            "- 해시태그 3개 (#투자 #복리 #자산 등)\n"
            "- 근사치는 '약' 또는 '~' 표시\n"
            "- 투자 권유 아님 — 교양/재미 콘텐츠\n"
            "- 트윗 본문만 출력. 설명/부연 없이.\n"
        )

        result = call(prompt=prompt, model="flash-lite", max_tokens=600, temperature=0.9)

        if not result.get("success") or not result.get("text"):
            return {"success": False, "error": "Gemini 물가 비교 생성 실패"}

        tweet = result["text"].strip().strip('"').strip("'")
        if len(tweet) > X_TWEET_MAX:
            tweet = tweet[:X_TWEET_MAX - 3] + "..."

        tg_text = tweet.replace("💰", "💰 <b>").replace("\n\n#", "</b>\n\n#")
        logger.info(f"[Viral] C-18 물가비교 생성 ({len(tweet)}자)")

        return {
            "success":  True,
            "type":     "money",
            "tweet":    tweet,
            "tg_text":  tg_text,
            "has_reply": False,
        }
    except Exception as e:
        logger.warning(f"[Viral] C-18 물가비교 생성 실패: {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# C-20: 극단적 선택 (기본 버전)
# ──────────────────────────────────────────────────────────────

def _generate_dilemma() -> dict:
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return {"success": False, "error": "Gemini 미사용"}

        prompt = (
            "투자/돈/자산 관련 '극단적 선택' 콘텐츠를 JSON으로 생성해줘.\n\n"
            "형식: 두 가지 극단적 선택지 A vs B\n"
            "주제: 배당 vs 성장, 부동산 vs 주식, 높은 연봉 vs 투자 자유,\n"
            "      특정 주식 vs 특정 자산, 복리 vs 일시불 등\n\n"
            "조건:\n"
            "- 두 선택지가 모두 매력적이어야 함 (정답 없음)\n"
            "- 구체적인 숫자 포함 (금액, 수량, 기간)\n"
            "- 현실적이면서 고민되는 상황\n"
            "- 재미있고 논쟁적 (댓글 유도)\n"
            "- 투자 권유 아님\n\n"
            "JSON 문법 규칙:\n"
            "- 문자열 값 안에 큰따옴표 사용 금지 → 작은따옴표 사용\n"
            "- trailing comma 금지\n\n"
            "JSON 형식:\n"
            "{\n"
            '  "option_a": "선택지 A",\n'
            '  "option_b": "선택지 B",\n'
            '  "condition": "조건",\n'
            '  "category": "카테고리"\n'
            "}\n"
        )

        result = call(
            prompt=prompt, model="flash-lite",
            max_tokens=300, temperature=0.95, response_json=True,
        )

        if not result.get("success") or not result.get("data"):
            return {"success": False, "error": "Gemini 극단적 선택 생성 실패"}

        d         = result["data"]
        opt_a     = d.get("option_a", "")
        opt_b     = d.get("option_b", "")
        condition = d.get("condition", "")
        category  = d.get("category", "투자")

        tweet_lines = ["🔥 극단적 선택\n", f"A) {opt_a}", f"B) {opt_b}"]
        if condition:
            tweet_lines.append(f"\n⚠️ {condition}")
        tweet_lines.append("\n어느 쪽? 댓글로! A 또는 B 👇")
        tweet_lines.append("\n#극단적선택 #투자 #돈")

        tweet = "\n".join(tweet_lines)
        if len(tweet) > X_TWEET_MAX:
            tweet = tweet[:X_TWEET_MAX - 3] + "..."

        tg_text = f"🔥 <b>극단적 선택</b>\n\nA) {opt_a}\nB) {opt_b}\n"
        if condition:
            tg_text += f"\n⚠️ {condition}\n"
        tg_text += "\n어느 쪽? 댓글로! 👇"

        logger.info(f"[Viral] C-20 극단적 선택 생성: {category}")

        return {
            "success":  True,
            "type":     "dilemma",
            "tweet":    tweet,
            "tg_text":  tg_text,
            "has_reply": False,
            "category": category,
        }
    except Exception as e:
        logger.warning(f"[Viral] C-20 극단적 선택 생성 실패: {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# C-20 v1.4.0: 자극적 버전 + 이미지 전용 (v1.5.4: 재시도 추가)
# ──────────────────────────────────────────────────────────────

def _generate_dilemma_viral() -> dict:
    """
    C-20 극단적 선택 — 자극적/논쟁적 강화 버전 (v1.5.4).

    v1.5.4 변경:
      - response_json=False → 원문 수신 후 _repair_json() + 직접 파싱
        (gemini_gateway의 JSON 파싱 오류 우회)
      - 재시도 3회 + temperature 단계 하향 (1.0 → 0.9 → 0.8)
      - 시도별 실패 사유 로그 기록 (파일 로그에도 남음)
    """
    from core.gemini_gateway import call, is_available
    if not is_available():
        return {"success": False, "error": "Gemini 미사용"}

    h             = _today_hash()
    category_hint = _C20_CATEGORIES[h % len(_C20_CATEGORIES)]

    prompt_template = (
        "20~30대 직장인/투자 입문자 타겟 '극단적 선택' SNS 콘텐츠를 JSON으로 생성해줘.\n\n"
        f"오늘 카테고리: {category_hint}\n\n"
        "핵심 조건:\n"
        "- 읽자마자 '헐', '와 이거 진짜 고민되는데' 반응 나와야 함\n"
        "- A 선택하면 B가 아깝고, B 선택하면 A가 아쉬운 진짜 딜레마\n"
        "- 구체적 숫자 필수 (예: 월 300만원, 자산 50억)\n"
        "- 2030 현실에 맞는 구어체\n"
        "- 투자 권유 절대 아님\n\n"
        "예시:\n"
        "- '쭉쭉빵빵인데 평생 월급 200만원 vs 외모 평범한데 현금 자산 50억'\n"
        "- '연봉 1억인데 꼰대 상사+매일 야근 vs 연봉 3천인데 풀리모트+자유'\n\n"
        "needs_image 판단 기준 (중요 — 반드시 직접 판단):\n"
        "- true: 시각적으로 표현 가능한 대비 (외모/차/집/명품/라이프스타일 등)\n"
        "  예시 true: '쭉쭉빵빵 vs 외모 평범', '람보르기니 vs 국민차', '펜트하우스 vs 반지하'\n"
        "- false: 숫자/금액/기간이 핵심인 추상 비교 (연봉, 복리, 자산 수치 등)\n"
        "  예시 false: '연봉 1억 vs 배당 월 300', '복리 20년 vs 일시불 10억', '코인 vs 주식'\n"
        "⚠️ 기본값은 false. 명확히 시각적인 경우에만 true.\n\n"
        "⚠️ JSON 문법 규칙 (반드시 준수):\n"
        "- 문자열 값 안에 큰따옴표(\") 사용 금지 → 작은따옴표(') 사용\n"
        "- 각 필드 끝 쉼표 누락 금지, 마지막 필드 뒤 쉼표 금지\n"
        "- option_a, option_b, condition: 한국어만\n"
        "- option_a_en, option_b_en, condition_en: 영어만\n\n"
        "JSON 형식:\n"
        "{\n"
        '  "option_a": "한국어 선택지 A",\n'
        '  "option_b": "한국어 선택지 B",\n'
        '  "condition": "한국어 조건",\n'
        '  "category": "카테고리명",\n'
        '  "needs_image": false,\n'
        '  "hashtags": "#극단적선택 #태그2 #태그3 #태그4 #태그5",\n'
        '  "option_a_en": "English Option A",\n'
        '  "option_b_en": "English Option B",\n'
        '  "condition_en": "English condition"\n'
        "}\n\n"
        "hashtags 기준:\n"
        "- 투자/자산: #재테크 #주식 #ETF #경제 #돈버는법\n"
        "- 직장/커리어: #직장인 #N잡러 #FIRE족 #경제적자유 #퇴사\n"
        "- 외모/연애: #연애현실 #돈이최고 #결혼 #현실연애 #부자\n"
        "- 소비/라이프: #플렉스 #부자되기 #인생선택 #돈관리 #재테크\n"
        "반드시 #극단적선택 포함. 총 5~6개.\n"
    )

    # ── 재시도 루프 (최대 3회) ────────────────────────────────
    temperatures = [1.0, 0.9, 0.8]
    last_error   = "알 수 없는 오류"

    for attempt, temp in enumerate(temperatures, start=1):
        try:
            logger.info(f"[Viral-C20] JSON 생성 시도 {attempt}/3 (temperature={temp})")

            # response_json=False → 원문 텍스트 수신 후 직접 파싱
            result = call(
                prompt=prompt_template,
                model="flash-lite",
                max_tokens=600,
                temperature=temp,
                response_json=False,
            )

            raw_text = result.get("text", "") or ""
            if not raw_text:
                last_error = f"시도 {attempt}: Gemini 빈 응답"
                logger.warning(f"[Viral-C20] {last_error}")
                continue

            repaired = _repair_json(raw_text)
            logger.debug(f"[Viral-C20] 복구 후 텍스트 (앞 200자): {repaired[:200]}")

            try:
                d = json.loads(repaired)
            except json.JSONDecodeError as je:
                last_error = (
                    f"시도 {attempt}: JSON 파싱 실패 — {je} | "
                    f"원문(앞 120자)={raw_text[:120]}"
                )
                logger.warning(f"[Viral-C20] {last_error}")
                continue

            opt_a = d.get("option_a", "")
            opt_b = d.get("option_b", "")
            if not opt_a or not opt_b:
                last_error = f"시도 {attempt}: 필수 필드 누락 (option_a/b)"
                logger.warning(f"[Viral-C20] {last_error}")
                continue

            opt_a_en     = d.get("option_a_en", "")
            opt_b_en     = d.get("option_b_en", "")
            condition    = d.get("condition", "")
            condition_en = d.get("condition_en", condition)
            category     = d.get("category", "투자")
            needs_image  = bool(d.get("needs_image", False))
            hashtags     = d.get("hashtags", "#극단적선택 #재테크 #돈 #주식 #경제")

            # ── 한/영 혼입 방어 (_is_korean: 모듈 레벨 함수) ──────
            if opt_a and not _is_korean(opt_a):
                logger.warning("[Viral-C20] option_a 영어 감지 → en 필드와 교정")
                opt_a, opt_a_en = opt_a_en, opt_a

            if opt_b and not _is_korean(opt_b):
                logger.warning("[Viral-C20] option_b 영어 감지 → en 필드와 교정")
                opt_b, opt_b_en = opt_b_en, opt_b

            if condition and not _is_korean(condition):
                logger.warning("[Viral-C20] condition 영어 감지 → en 필드와 교정")
                condition, condition_en = condition_en, condition

            if not _is_korean(opt_a) or not _is_korean(opt_b):
                last_error = (
                    f"시도 {attempt}: 한국어 교정 실패 "
                    f"opt_a={opt_a[:30]} opt_b={opt_b[:30]}"
                )
                logger.warning(f"[Viral-C20] {last_error}")
                continue

            if not opt_a_en:
                opt_a_en = opt_a
            if not opt_b_en:
                opt_b_en = opt_b

            tweet_lines = ["🔥 극단적 선택\n", f"A) {opt_a}", f"B) {opt_b}"]
            if condition:
                tweet_lines.append(f"\n⚠️ {condition}")
            tweet_lines.append("\n어느 쪽? 댓글로! A 또는 B 👇")
            tweet_lines.append(f"\n{hashtags}")

            tweet = "\n".join(tweet_lines)
            if len(tweet) > 4000:
                tweet = tweet[:3997] + "..."

            tg_text = f"🔥 <b>극단적 선택</b>\n\nA) {opt_a}\nB) {opt_b}\n"
            if condition:
                tg_text += f"\n⚠️ {condition}\n"
            tg_text += "\n어느 쪽? 댓글로! 👇"

            logger.info(
                f"[Viral-C20] 자극적 선택 생성 완료 "
                f"(시도={attempt}, category={category}, needs_image={needs_image})"
            )

            return {
                "success":      True,
                "type":         "dilemma",
                "tweet":        tweet,
                "tg_text":      tg_text,
                "has_reply":    False,
                "opt_a":        opt_a,
                "opt_b":        opt_b,
                "opt_a_en":     opt_a_en,
                "opt_b_en":     opt_b_en,
                "condition":    condition,
                "condition_en": condition_en,
                "category":     category,
                "needs_image":  needs_image,
                "hashtags":     hashtags,
            }

        except Exception as e:
            last_error = f"시도 {attempt}: 예외 발생 — {e}"
            logger.warning(f"[Viral-C20] {last_error}")

    logger.error(
        f"[Viral-C20] _generate_dilemma_viral() 3회 모두 실패. "
        f"마지막 오류: {last_error}"
    )
    return {"success": False, "error": f"3회 재시도 실패 — {last_error}"}


# ──────────────────────────────────────────────────────────────
# C-20 카테고리 상수 (모듈 레벨)
# _generate_dilemma_viral() + _should_generate_image() 공유 사용
# ──────────────────────────────────────────────────────────────

_C20_CATEGORIES = [
    "극단적 수익 vs 안정 (연복리/배당/일시불, 숫자 크게)",      # 0  abstract → False
    "인생 한 방 vs 평생 안정 수입 (극단적 금액)",              # 1  abstract → False
    "자산 선택 (부동산/주식/코인/금 극단 비교)",               # 2  abstract → False
    "복리 vs 일시불 (시간 가치 논쟁)",                       # 3  abstract → False
    "직장 현실 (연봉 vs 자유, 꼰대 vs 리모트 등 2030 공감)",  # 4  abstract → False
    "SNS/크리에이터 vs 안정 직장 (팔로워/구독자 vs 월급)",     # 5  abstract → False
    "N잡러/FIRE 족 vs 대기업 정규직",                       # 6  abstract → False
    "외모/매력 vs 돈 (이성 현실, 구체적 극단 상황)",           # 7  visual  → True
    "연애 vs 재테크 (2030 현실 고민)",                       # 8  abstract → False
    "배우자/파트너 선택 — 외모 극단 vs 자산 극단",            # 9  visual  → True
    "플렉스 소비 극단 (람보르기니/명품 vs 절약 부자)",         # 10 visual  → True
    "소비 스타일 (럭셔리 거지 vs 검소 부자 라이프)",          # 11 visual  → True
]


def _should_generate_image(category_hint: str, gemini_flag: bool) -> bool:
    """
    이미지 생성 여부 최종 결정.

    전략 (2단계):
      1차: category_hint 키워드 기반 Python 결정 (확실한 경우)
      2차: Gemini 응답(gemini_flag) 사용 (1차에서 결정 불가 시)

    이미지 생성 기준 — 시각적으로 표현 가능한 대비:
      True:  외모, 차/자동차, 집/부동산 외형, 명품, 라이프스타일 비교
      False: 숫자/연봉/복리/자산 수치, 직장환경(추상), 연애감정(추상)

    Args:
        category_hint: 오늘의 카테고리 문자열 (프롬프트에 주입된 값)
        gemini_flag:   Gemini가 반환한 needs_image 값

    Returns:
        bool — 이미지 생성 여부
    """
    # 확실히 이미지 필요한 키워드
    visual_keywords = ["외모", "배우자", "람보르기니", "명품", "럭셔리", "소비 스타일"]
    # 확실히 이미지 불필요한 키워드
    abstract_keywords = ["복리", "일시불", "연복리", "배당", "코인", "주식", "자산 선택",
                         "연봉", "FIRE", "N잡", "재테크", "직장 현실", "SNS"]

    for kw in visual_keywords:
        if kw in category_hint:
            logger.info(f"[Viral-C20] needs_image=True (키워드 매칭: '{kw}')")
            return True

    for kw in abstract_keywords:
        if kw in category_hint:
            logger.info(f"[Viral-C20] needs_image=False (키워드 매칭: '{kw}')")
            return False

    # 키워드 미매칭 → Gemini 응답 사용
    logger.info(f"[Viral-C20] needs_image={gemini_flag} (Gemini 응답 사용, 키워드 미매칭)")
    return gemini_flag


def _generate_dilemma_image_only(
    opt_a_en: str,
    opt_b_en: str,
    condition_en: str = "",
    category_index: int = -1,
) -> str | None:
    """
    이미지 생성 (v1.6.0).

    변경점 (v1.5.6 → v1.6.0):
      1. category_index 인자 추가 — 시각화 모드 결정용
      2. VIRAL_IMAGE_MODE_V2=true 시 3모드 분기 (object/situation/lifestyle)
      3. VIRAL_GUARD_L1=true 시 L1 필터 적용
         - IP/브랜드 → 일반명사 치환
         - 실존 인물 감지 → None 반환 (이미지 생성 거부)

    Feature Flag (모두 true 기본):
      VIRAL_IMAGE_MODE_V2: 3모드 이미지
      VIRAL_GUARD_L1:      L1 보안 필터

    Returns:
        image_path (str) | None (생성 실패 또는 L1 거부)
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    os.makedirs("data/images", exist_ok=True)
    out_path = f"data/images/c20_{date_str}.png"

    use_mode_v2 = os.environ.get("VIRAL_IMAGE_MODE_V2", "true").lower() == "true"
    use_guard_l1 = os.environ.get("VIRAL_GUARD_L1", "true").lower() == "true"

    # ── 프롬프트 구성 ────────────────────────────────────────
    if use_mode_v2 and category_index >= 0:
        try:
            from engines.viral_prompts import get_visual_mode, build_image_prompt
            mode = get_visual_mode(category_index)
            if mode is None:
                logger.info(
                    f"[Viral-C20] 카테고리 {category_index} 시각화 모드 없음 → 이미지 스킵"
                )
                return None
            prompt = build_image_prompt(mode, opt_a_en, opt_b_en, condition_en)
            logger.info(
                f"[Viral-C20] 시각화 모드 적용: {mode} (카테고리 {category_index})"
            )
        except ImportError as ie:
            logger.warning(
                f"[Viral-C20] viral_prompts import 실패 → 기존 프롬프트 fallback: {ie}"
            )
            prompt = _build_legacy_prompt(opt_a_en, opt_b_en, condition_en)
    else:
        # v1.5.6 기존 프롬프트 (VIRAL_IMAGE_MODE_V2=false 또는 category_index=-1)
        prompt = _build_legacy_prompt(opt_a_en, opt_b_en, condition_en)

    # ── L1 보안 필터 ─────────────────────────────────────────
    if use_guard_l1:
        try:
            from engines.viral_guard import sanitize_image_prompt
            sanitized, safe, warnings = sanitize_image_prompt(
                prompt, opt_a_en, opt_b_en, condition_en,
            )
            if warnings:
                logger.warning(f"[Viral-C20][L1] 필터 동작: {warnings}")
            if not safe:
                logger.warning(
                    "[Viral-C20][L1] 실존 인물 감지 → 이미지 생성 거부 (텍스트만 발행)"
                )
                return None
            prompt = sanitized
        except ImportError as ie:
            logger.warning(f"[Viral-C20] viral_guard import 실패 → 필터 미적용: {ie}")

    # ── 이미지 생성 (v1.5.6 동일 로직) ───────────────────────
    try:
        from core.gemini_gateway import generate_image
        result = generate_image(prompt=prompt, output_path=out_path)
        if result.get("success") and result.get("image_path"):
            logger.info(
                f"[Viral-C20] 이미지 생성 완료: {out_path} "
                f"(key={result.get('key_used', '?')} paid={result.get('paid')})"
            )
            return out_path
        logger.warning(
            f"[Viral-C20] Gemini 이미지 실패: {result.get('error', 'unknown')}"
        )
        return None
    except Exception as e:
        logger.warning(f"[Viral-C20] 이미지 생성 예외: {e}")
        return None


def _build_legacy_prompt(opt_a_en: str, opt_b_en: str, condition_en: str = "") -> str:
    """v1.5.6 기존 split-screen battle card 프롬프트. Fallback 전용."""
    return (
        f"Dark cinematic split-screen battle card, 1080x1080 square. "
        f"LEFT half: deep blue gradient background, huge bold letter 'A' at top, "
        f"centered white bold text: '{opt_a_en[:80]}'. "
        f"RIGHT half: deep orange gradient background, huge bold letter 'B' at top, "
        f"centered white bold text: '{opt_b_en[:80]}'. "
        "Center divider: glowing white 'VS' in a circle with dramatic light effect. "
        "Top banner: fire emoji + 'EXTREME CHOICE — WHICH ONE?' in bold caps. "
        + (f"Bottom center: '⚠️ {condition_en[:60]}' in yellow. " if condition_en else "")
        + "Bottom right: 'EDT Investment 🐂' small text. "
        "Style: high contrast, dramatic, cinematic financial poster. "
        "CRITICAL: ALL text MUST be in English. No Korean characters."
    )


# ──────────────────────────────────────────────────────────────
# C-20 전용 파이프라인
# ──────────────────────────────────────────────────────────────

def run_viral_c20(session: str = "viral_c20") -> dict:
    """
    C-20 극단적 선택 전용 파이프라인 (v1.5.4).
    KST 08:00 / 18:00 평일 하루 2회.
    """
    logger.info(f"[Viral-C20] v1.6.0 파이프라인 시작 (session={session})")

    if not should_run(session):
        return {"success": False, "reason": "not_my_slot"}

    slot = "오전" if "morning" in session else "퇴근"

    def _tg_notify(msg: str):
        try:
            from publishers.telegram_publisher import send_message
            send_message(msg, channel="free")
        except Exception as e:
            logger.warning(f"[Viral-C20] TG 안내 실패: {e}")

    # ── Step 1: 텍스트 생성 (3회 재시도 내장) ───────────────
    content = _generate_dilemma_viral()
    if not content.get("success"):
        logger.error(f"[Viral-C20] 텍스트 생성 최종 실패: {content.get('error')}")
        _tg_notify(
            f"⚠️ [C-20 {slot}] 콘텐츠 생성 실패\n"
            f"Gemini 텍스트 생성 오류로 발행을 건너뜁니다.\n"
            f"오류: {content.get('error', 'unknown')}"
        )
        return content

    opt_a_en     = content["opt_a_en"]
    opt_b_en     = content["opt_b_en"]
    condition_en = content["condition_en"]
    hook_tweet   = content["tweet"]
    tg_text      = content["tg_text"]
    category     = content.get("category", "투자")
    hashtags     = content.get("hashtags", "#극단적선택 #재테크 #돈 #주식 #경제")

    # ── Step 2: 이미지 생성 여부 최종 결정 ───────────────────
    # Gemini 응답(needs_image)만 믿지 않고 카테고리 기반으로 재확인
    # _should_generate_image(): 1차 키워드 판단 → 2차 Gemini 응답 fallback
    gemini_needs_image = content.get("needs_image", False)
    needs_image = _should_generate_image(
        category_hint=_C20_CATEGORIES[_today_hash() % len(_C20_CATEGORIES)],
        gemini_flag=gemini_needs_image,
    )
    logger.info(
        f"[Viral-C20] 이미지 생성 결정: needs_image={needs_image} "
        f"(gemini={gemini_needs_image}, category={category})"
    )

    image_path = None
    if needs_image:
        # v1.6.0: 카테고리 인덱스 전달 (시각화 모드 분기용)
        category_index = _today_hash() % len(_C20_CATEGORIES)
        image_path = _generate_dilemma_image_only(
            opt_a_en, opt_b_en, condition_en,
            category_index=category_index,
        )
        if not image_path:
            logger.warning("[Viral-C20] 이미지 생성 전부 실패 → 발행 중단")
            _tg_notify(
                f"⚠️ [C-20 {slot}] 이미지 생성 실패 — 발행 중단\n"
                "Gemini API 일시 장애로 오늘 극단적 선택 콘텐츠를 발행하지 못했습니다.\n"
                "다음 발행 시간에 다시 시도합니다."
            )
            return {"success": False, "reason": "image_failed"}
    else:
        logger.info("[Viral-C20] needs_image=False → 이미지 생성 스킵 (텍스트 발행)")

    # ── Step 3: X 발행 ────────────────────────────────────────
    opt_a     = content["opt_a"]
    opt_b     = content["opt_b"]
    condition = content["condition"]

    body_tweet = f"A) {opt_a}\n\nB) {opt_b}"
    if condition:
        body_tweet += f"\n\n⚠️ {condition}"
    body_tweet += f"\n\n{hashtags}"

    cta_tweet = (
        "댓글로 A 또는 B 알려주세요 👇\n\n"
        "정답은 없습니다 — 여러분의 선택이 답!\n\n"
        "💡 매일 출근/퇴근 시간 투자 콘텐츠\n"
        "⚠️ 투자 참고 정보, 투자 권유 아님"
    )

    tweet_id  = "FAIL"
    x_success = False
    try:
        from publishers.x_publisher import publish_tweet_with_image, publish_thread, publish_tweet

        if image_path:
            first_result = publish_tweet_with_image(hook_tweet, image_path)
            first_id     = first_result.get("tweet_id")
            if first_result.get("success") and first_id:
                tweet_id  = first_id
                x_success = True
                publish_thread([body_tweet, cta_tweet], reply_to=str(first_id))
                logger.info(
                    f"[Viral-C20] X 이미지+스레드 발행 완료: "
                    f"첫트윗={first_id} | category={category}"
                )
            else:
                raise RuntimeError("이미지 트윗 실패")
        else:
            posts     = [hook_tweet, body_tweet, cta_tweet]
            pt_result = publish_thread(posts)
            ids       = pt_result.get("tweet_ids", [])
            if ids:
                tweet_id  = ids[0]
                x_success = True
                logger.info(
                    f"[Viral-C20] X 텍스트 스레드 발행 완료: "
                    f"첫트윗={tweet_id} | category={category}"
                )
            else:
                raise RuntimeError("텍스트 스레드 실패")

    except Exception as e:
        logger.warning(f"[Viral-C20] X 발행 실패: {e}")
        _tg_notify(
            f"⚠️ [C-20 {slot}] X 발행 실패\n"
            f"{'이미지는 생성됐으나 ' if image_path else ''}X API 오류로 발행 실패.\n"
            f"(오류: {str(e)[:100]})"
        )

    # ── Step 4: TG 발행 (X 성공 여부 무관) ──────────────────
    try:
        from publishers.telegram_publisher import send_photo, send_message
        if image_path:
            send_photo(image_path, caption=tg_text, channel="free")
        else:
            send_message(tg_text, channel="free")
        logger.info("[Viral-C20] TG 무료 채널 발행 완료")
    except Exception as e:
        logger.warning(f"[Viral-C20] TG 발행 실패: {e}")

    logger.info(
        f"[Viral-C20] 완료 | x_success={x_success} | "
        f"has_image={bool(image_path)} | tweet_id={tweet_id} | category={category}"
    )

    return {
        "success":   x_success,
        "type":      "dilemma",
        "tweet_id":  tweet_id,
        "session":   session,
        "category":  category,
        "has_image": bool(image_path),
    }


# ──────────────────────────────────────────────────────────────
# C-19: 캐릭터 투표 (일요일만)
# ──────────────────────────────────────────────────────────────

_CHARACTERS = {
    "EDT":              ("🐂",  "골드 링 각성으로 전선 반전"),
    "Leverage Man":     ("🔥",  "화염 주먹으로 빌런 타격"),
    "Iron Nuna":        ("🛡️", "ETF 방패로 금리 압박 흡수"),
    "Futures Girl":     ("⚡",  "선물 시장 신호 감지"),
    "Gold Bond":        ("🏆",  "황금 갑옷으로 방어선 사수"),
    "War Dominion":     ("😈",  "마감일 연장 전략"),
    "Oil Shock Titan":  ("🛢️", "유가 폭등으로 시장 압박"),
    "Algorithm Reaper": ("💻",  "코드 컴파일로 새로운 위협"),
}


def _generate_character_vote() -> dict:
    try:
        candidates = []
        try:
            from db.daily_store import get_novel
            today = datetime.now(KST).strftime("%Y-%m-%d")
            novel = get_novel(today)
            if novel and novel.get("novel_text"):
                for char, (emoji, desc) in _CHARACTERS.items():
                    if char in novel["novel_text"]:
                        candidates.append((char, emoji, desc))
        except Exception:
            pass

        if len(candidates) < 3:
            candidates = [
                ("EDT",          "🐂",  "시장 수호자"),
                ("Iron Nuna",    "🛡️", "ETF 방패 전사"),
                ("Futures Girl", "⚡",  "선물 시장 감지자"),
                ("War Dominion", "😈",  "시장 지배자"),
            ]

        candidates = candidates[:4]

        lines = ["🔥 이번 주 EDT Universe MVP는?\n"]
        for char, emoji, desc in candidates:
            lines.append(f"{emoji} {char} — {desc}")
        lines += [
            "",
            "댓글로 투표! 이모지 하나면 OK 👇",
            "가장 많은 표 받은 캐릭터가 다음 주 표지에! 🎨",
            "",
            "#EDT #투자코믹스 #투자소설 #캐릭터투표",
        ]

        tweet = "\n".join(lines)
        if len(tweet) > X_TWEET_MAX:
            tweet = tweet[:X_TWEET_MAX - 3] + "..."

        tg_lines = ["🔥 <b>이번 주 EDT Universe MVP는?</b>\n"]
        for char, emoji, desc in candidates:
            tg_lines.append(f"{emoji} <b>{char}</b> — {desc}")
        tg_lines += ["", "댓글로 투표해주세요! 👇"]
        tg_text = "\n".join(tg_lines)

        logger.info(f"[Viral] C-19 캐릭터투표 생성 ({len(candidates)}명)")

        return {
            "success":  True,
            "type":     "vote",
            "tweet":    tweet,
            "tg_text":  tg_text,
            "has_reply": False,
        }
    except Exception as e:
        logger.warning(f"[Viral] C-19 캐릭터투표 생성 실패: {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────────────────────

def run_viral(session: str = "viral_afternoon") -> dict:
    """바이럴 콘텐츠 통합 파이프라인. 현재 dilemma 고정 운영."""
    logger.info(f"[Viral] 파이프라인 시작 (session={session})")

    if not should_run(session):
        logger.info(f"[Viral] 이 시간대 아님 → 스킵 (session={session})")
        return {"success": False, "reason": "not_my_slot"}

    content = _generate_dilemma()

    if not content.get("success"):
        logger.warning(f"[Viral] 콘텐츠 생성 실패: {content.get('error')}")
        return content

    tweet_id = "SKIP"
    try:
        from publishers.x_publisher import publish_tweet
        pub_result = publish_tweet(content["tweet"])
        tweet_id   = pub_result.get("tweet_id", "FAIL")
        logger.info(f"[Viral] X 발행: {tweet_id} | type=dilemma")

        if content.get("has_reply") and content.get("reply"):
            if tweet_id and tweet_id not in ("FAIL", "SKIP", "DRY_RUN"):
                if _is_dry_run():
                    logger.info("[Viral] DRY_RUN → reply 즉시 발행 (대기 스킵)")
                else:
                    logger.info("[Viral] 정답 reply 30분 대기...")
                    time.sleep(1800)
                try:
                    from publishers.x_publisher import publish_tweet as _pub_reply
                    try:
                        _pub_reply(content["reply"], reply_to=tweet_id)
                    except TypeError:
                        _pub_reply(content["reply"])
                    logger.info("[Viral] reply 발행 완료")
                except Exception as re:
                    logger.warning(f"[Viral] reply 실패 (무시): {re}")
            else:
                logger.info("[Viral] DRY_RUN — reply 스킵")

    except Exception as e:
        logger.warning(f"[Viral] X 발행 실패: {e}")

    try:
        from publishers.telegram_publisher import send_message
        send_message(content.get("tg_text", content["tweet"]), channel="free")
        logger.info("[Viral] TG 발행 완료")
    except Exception as e:
        logger.warning(f"[Viral] TG 발행 실패: {e}")

    return {
        "success":  True,
        "type":     "dilemma",
        "tweet_id": tweet_id,
        "session":  session,
    }


# CLI 지원
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    session = sys.argv[1] if len(sys.argv) > 1 else "viral_afternoon"
    result  = run_viral(session)
    print(result)
