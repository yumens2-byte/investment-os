"""
engines/viral_engine.py (C-6 / C-18 / C-19 / C-20 통합)
===================================================
바이럴 콘텐츠 통합 엔진.

하루 1회, 4개 콘텐츠 중 1개를 랜덤 선택하여 발행.
시간대: 아침(06~08 KST) 또는 오후(16~19 KST) 중 랜덤.

yml cron 2개 (아침 06:00 + 오후 16:00)로 트리거.
날짜 해시 기반으로 아침/오후 결정 → 해당 시간대에만 실행.

콘텐츠:
  C-6:  금융 퀴즈 (4지선다, 30분 후 정답 reply)
  C-18: 물가/자산 비교 ($100의 시간 여행)
  C-19: 캐릭터 투표 (일요일만, C-7 소설 연동)
  C-20: 극단적 선택 (A vs B, 투자/돈 관련)

VERSION = "1.2.0"  # X 프리미엄 4000자 + DRY_RUN 즉시 실행 + FORCE_RUN
RPD: +1/일 (Gemini flash-lite)
"""
import hashlib
import logging
import os
import random
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ── X 프리미엄 글자 제한 ──
# 프리미엄: 25,000자 (무료: 280자)
# 바이럴 본문은 짧을수록 참여율 ↑ → 소프트 타겟 500자
# 해설/정답은 길게 허용 → 하드 맥스 4,000자
X_TWEET_MAX = 4000       # 본문 하드 맥스 (프리미엄)
X_REPLY_MAX = 4000       # reply 하드 맥스 (프리미엄)

def _is_dry_run() -> bool:
    """DRY_RUN 환경변수 확인 — "false" 문자열만 실 발행"""
    return os.environ.get("DRY_RUN", "true").lower() != "false"

# ──────────────────────────────────────────────────────────────
# 시간대 결정 (날짜 해시 기반)
# ──────────────────────────────────────────────────────────────

def _today_hash() -> int:
    """오늘 날짜의 결정적 해시 (같은 날 = 같은 결과)"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return int(hashlib.md5(f"viral_{today}".encode()).hexdigest(), 16)


def _is_sunday() -> bool:
    """오늘이 일요일인지"""
    return datetime.now(KST).weekday() == 6


def should_run(session: str) -> bool:
    """
    이 시간대에 실행해야 하는지 결정.
    날짜 해시로 아침/오후를 결정 → 하루 1회만 실행.
    FORCE_RUN=true 시 무조건 실행 (수동 테스트용).

    Args:
        session: "viral_morning" 또는 "viral_afternoon"
    """
    # FORCE_RUN=true → 무조건 실행 (workflow_dispatch 수동 테스트)
    force = os.environ.get("FORCE_RUN", "false").lower() == "true"
    if force:
        logger.info(f"[Viral] FORCE_RUN=true → 슬롯 무시, 강제 실행")
        return True

    h = _today_hash()
    is_morning_day = (h % 2 == 0)  # 짝수 날 = 아침, 홀수 날 = 오후

    if session == "viral_morning" and is_morning_day:
        return True
    if session == "viral_afternoon" and not is_morning_day:
        return True

    return False


def _random_delay(session: str) -> int:
    """
    시간대 내 랜덤 딜레이 (초).
    아침: 0~120분 (06:00~08:00)
    오후: 0~180분 (16:00~19:00)
    """
    if session == "viral_morning":
        return random.randint(0, 200)   # 7200 0~120분 -> 200
    else:
        return random.randint(0, 200)  # 10800 0~180분 -> 200


# ──────────────────────────────────────────────────────────────
# 콘텐츠 선택
# ──────────────────────────────────────────────────────────────

def _select_content_type() -> str:
    """
    4개 콘텐츠 중 1개 랜덤 선택.
    일요일: quiz / money / dilemma / vote (4개 중 1개)
    평일: quiz / money / dilemma (3개 중 1개, 투표는 일요일만)
    """
    h = _today_hash()

    if _is_sunday():
        types = ["quiz", "money", "dilemma", "vote"]
    else:
        types = ["quiz", "money", "dilemma"]

    return types[h % len(types)]


# ──────────────────────────────────────────────────────────────
# C-6: 금융 퀴즈
# ──────────────────────────────────────────────────────────────

def _generate_quiz() -> dict:
    """
    Gemini로 금융 퀴즈 1개 생성.
    Returns: {success, tweet, reply, tg_text, category}
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return {"success": False, "error": "Gemini 미사용"}

        prompt = (
            "투자/금융 퀴즈 1개를 JSON으로 생성해줘.\n\n"
            "조건:\n"
            "- 4지선다 객관식 (A/B/C/D)\n"
            "- 카테고리: 투자 역사, 금융 상식, ETF 지식, 경제 용어, 유명 투자자 중 랜덤\n"
            "- 난이도: 초급~중급 (일반인도 참여 가능)\n"
            "- 정답 해설 2~3문장\n"
            "- 재미있는 사실(fun fact) 1개 포함\n\n"
            "JSON 형식:\n"
            "{\n"
            '  "question": "질문",\n'
            '  "options": {"A": "...", "B": "...", "C": "...", "D": "..."},\n'
            '  "answer": "D",\n'
            '  "explanation": "해설",\n'
            '  "fun_fact": "재미있는 사실",\n'
            '  "category": "카테고리"\n'
            "}\n"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=500,
            temperature=0.9,
            response_json=True,
        )

        if not result.get("success") or not result.get("data"):
            return {"success": False, "error": "Gemini 퀴즈 생성 실패"}

        quiz = result["data"]
        q = quiz.get("question", "")
        opts = quiz.get("options", {})
        answer = quiz.get("answer", "")
        explanation = quiz.get("explanation", "")
        fun_fact = quiz.get("fun_fact", "")
        category = quiz.get("category", "금융상식")

        # 퀴즈 번호 (날짜 기반)
        quiz_no = int(datetime.now(KST).strftime("%j"))  # 1~366

        # 퀴즈 트윗
        tweet = (
            f"🧠 금융 퀴즈 #{quiz_no}\n\n"
            f"Q. {q}\n\n"
            f"A) {opts.get('A', '')}\n"
            f"B) {opts.get('B', '')}\n"
            f"C) {opts.get('C', '')}\n"
            f"D) {opts.get('D', '')}\n\n"
            f"정답은 다음 트윗에서! 👇\n"
            f"댓글로 예상 정답 남겨주세요 🔥\n\n"
            f"#금융퀴즈 #투자상식 #ETF투자"
        )

        # 정답 reply
        reply = (
            f"✅ 정답: {answer}) {opts.get(answer, '')}\n\n"
            f"{explanation}\n\n"
            f"💡 {fun_fact}\n\n"
            f"내일 퀴즈도 기대해주세요! 🧠"
        )

        # TG 포맷
        tg_text = (
            f"🧠 <b>금융 퀴즈 #{quiz_no}</b>\n\n"
            f"Q. {q}\n\n"
            f"A) {opts.get('A', '')}\n"
            f"B) {opts.get('B', '')}\n"
            f"C) {opts.get('C', '')}\n"
            f"D) {opts.get('D', '')}\n\n"
            f"✅ 정답: {answer}) {opts.get(answer, '')}\n"
            f"{explanation}\n\n"
            f"💡 {fun_fact}"
        )

        logger.info(f"[Viral] C-6 퀴즈 생성: {category} | 정답={answer}")

        return {
            "success": True,
            "type": "quiz",
            "tweet": tweet[:X_TWEET_MAX],
            "reply": reply[:X_REPLY_MAX],
            "tg_text": tg_text,
            "category": category,
            "has_reply": True,  # 30분 후 정답 reply 발행 필요
        }

    except Exception as e:
        logger.warning(f"[Viral] C-6 퀴즈 생성 실패: {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# C-18: 물가/자산 비교
# ──────────────────────────────────────────────────────────────

def _generate_money_compare() -> dict:
    """
    Gemini로 "이 금액이면?" 물가/자산 비교 콘텐츠 생성.
    Returns: {success, tweet, tg_text}
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return {"success": False, "error": "Gemini 미사용"}

        # 3가지 타입 중 랜덤 선택
        types = [
            "시간 여행형: 특정 금액($100 또는 $1,000)으로 과거 vs 현재 살 수 있는 것 비교",
            "물가 비교형: 2006 vs 2026 주요 물가 비교 (빅맥, 집값, 휘발유, S&P 500)",
            "놀라운 숫자형: 유명 투자자나 기업의 놀라운 자산 성장 스토리",
        ]
        selected_type = random.choice(types)

        prompt = (
            f"투자/금융 관련 '놀라운 숫자' 콘텐츠를 트윗 형식으로 작성해줘.\n\n"
            f"타입: {selected_type}\n\n"
            f"조건:\n"
            f"- 300~500자 한국어\n"
            f"- 숫자 비교가 핵심 (충격적인 대비)\n"
            f"- 이모지 3~4개\n"
            f"- 마지막에 '여러분은 어떻게 생각하세요? 👇' 같은 CTA\n"
            f"- 해시태그 3개 (#투자 #복리 #자산 등)\n"
            f"- 근사치는 '약' 또는 '~' 표시\n"
            f"- 투자 권유 아님 — 교양/재미 콘텐츠\n"
            f"- 트윗 본문만 출력. 설명/부연 없이.\n"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=600,
            temperature=0.9,
        )

        if not result.get("success") or not result.get("text"):
            return {"success": False, "error": "Gemini 물가 비교 생성 실패"}

        tweet = result["text"].strip().strip('"').strip("'")
        if len(tweet) > X_TWEET_MAX:
            tweet = tweet[:X_TWEET_MAX - 3] + "..."

        # TG 포맷 (동일 텍스트, 볼드 추가)
        tg_text = tweet.replace("💰", "💰 <b>").replace("\n\n#", "</b>\n\n#")

        logger.info(f"[Viral] C-18 물가비교 생성 ({len(tweet)}자)")

        return {
            "success": True,
            "type": "money",
            "tweet": tweet,
            "tg_text": tg_text,
            "has_reply": False,
        }

    except Exception as e:
        logger.warning(f"[Viral] C-18 물가비교 생성 실패: {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# C-19: 캐릭터 투표 (일요일만)
# ──────────────────────────────────────────────────────────────

# EDT Universe 캐릭터 목록
# ──────────────────────────────────────────────────────────────
# C-20: 극단적 선택 (Would You Rather — 투자/돈 버전)
# ──────────────────────────────────────────────────────────────

def _generate_dilemma() -> dict:
    """
    Gemini로 투자/돈 관련 극단적 선택 콘텐츠 생성.
    "A vs B 어느 쪽?" 형식 — 댓글 폭발 유도.
    Returns: {success, tweet, tg_text}
    """
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
            "- 투자 권유 아님 — 재미/토론 콘텐츠\n\n"
            "JSON 형식:\n"
            "{\n"
            '  "option_a": "선택지 A 설명",\n'
            '  "option_b": "선택지 B 설명",\n'
            '  "condition": "조건 (예: 20년 보유 필수)",\n'
            '  "category": "카테고리 (배당vs성장, 연봉vs투자, 자산비교 등)"\n'
            "}\n"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=300,
            temperature=0.95,
            response_json=True,
        )

        if not result.get("success") or not result.get("data"):
            return {"success": False, "error": "Gemini 극단적 선택 생성 실패"}

        dilemma = result["data"]
        opt_a = dilemma.get("option_a", "")
        opt_b = dilemma.get("option_b", "")
        condition = dilemma.get("condition", "")
        category = dilemma.get("category", "투자")

        # 극단적 선택 트윗
        tweet_lines = [
            "🔥 극단적 선택\n",
            f"A) {opt_a}",
            f"B) {opt_b}",
        ]
        if condition:
            tweet_lines.append(f"\n⚠️ {condition}")
        tweet_lines.append("\n어느 쪽? 댓글로! A 또는 B 👇")
        tweet_lines.append("\n#극단적선택 #투자 #돈")

        tweet = "\n".join(tweet_lines)
        if len(tweet) > X_TWEET_MAX:
            tweet = tweet[:X_TWEET_MAX - 3] + "..."

        # TG 포맷
        tg_text = (
            f"🔥 <b>극단적 선택</b>\n\n"
            f"A) {opt_a}\n"
            f"B) {opt_b}\n"
        )
        if condition:
            tg_text += f"\n⚠️ {condition}\n"
        tg_text += "\n어느 쪽? 댓글로! 👇"

        logger.info(f"[Viral] C-20 극단적 선택 생성: {category}")

        return {
            "success": True,
            "type": "dilemma",
            "tweet": tweet,
            "tg_text": tg_text,
            "has_reply": False,
            "category": category,
        }

    except Exception as e:
        logger.warning(f"[Viral] C-20 극단적 선택 생성 실패: {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# C-19: 캐릭터 투표 (일요일만)
# ──────────────────────────────────────────────────────────────

# EDT Universe 캐릭터 목록
_CHARACTERS = {
    "EDT": ("🐂", "골드 링 각성으로 전선 반전"),
    "Leverage Man": ("🔥", "화염 주먹으로 빌런 타격"),
    "Iron Nuna": ("🛡️", "ETF 방패로 금리 압박 흡수"),
    "Futures Girl": ("⚡", "선물 시장 신호 감지"),
    "Gold Bond": ("🏆", "황금 갑옷으로 방어선 사수"),
    "War Dominion": ("😈", "마감일 연장 전략"),
    "Oil Shock Titan": ("🛢️", "유가 폭등으로 시장 압박"),
    "Algorithm Reaper": ("💻", "코드 컴파일로 새로운 위협"),
}


def _generate_character_vote() -> dict:
    """
    C-7 소설 기반 캐릭터 MVP 투표 트윗 생성.
    일요일에만 실행 (C-7 소설 발행 후).
    Returns: {success, tweet, tg_text}
    """
    try:
        # 최근 소설에서 등장 캐릭터 확인
        candidates = []
        try:
            from db.daily_store import get_novel
            today = datetime.now(KST).strftime("%Y-%m-%d")
            novel = get_novel(today)
            if novel and novel.get("novel_text"):
                novel_text = novel["novel_text"]
                for char, (emoji, desc) in _CHARACTERS.items():
                    if char in novel_text:
                        candidates.append((char, emoji, desc))
        except Exception:
            pass

        # 소설 없거나 캐릭터 추출 실패 시 기본 4명
        if len(candidates) < 3:
            candidates = [
                ("EDT", "🐂", "시장 수호자"),
                ("Iron Nuna", "🛡️", "ETF 방패 전사"),
                ("Futures Girl", "⚡", "선물 시장 감지자"),
                ("War Dominion", "😈", "시장 지배자"),
            ]

        # 최대 4명
        candidates = candidates[:4]

        # 투표 트윗
        lines = ["🔥 이번 주 EDT Universe MVP는?\n"]
        for char, emoji, desc in candidates:
            lines.append(f"{emoji} {char} — {desc}")

        lines.append("")
        lines.append("댓글로 투표! 이모지 하나면 OK 👇")
        lines.append("가장 많은 표 받은 캐릭터가 다음 주 표지에! 🎨")
        lines.append("")
        lines.append("#EDT #투자코믹스 #투자소설 #캐릭터투표")

        tweet = "\n".join(lines)
        if len(tweet) > X_TWEET_MAX:
            tweet = tweet[:X_TWEET_MAX - 3] + "..."

        # TG 포맷
        tg_lines = ["🔥 <b>이번 주 EDT Universe MVP는?</b>\n"]
        for char, emoji, desc in candidates:
            tg_lines.append(f"{emoji} <b>{char}</b> — {desc}")
        tg_lines.append("")
        tg_lines.append("댓글로 투표해주세요! 👇")
        tg_text = "\n".join(tg_lines)

        logger.info(f"[Viral] C-19 캐릭터투표 생성 ({len(candidates)}명)")

        return {
            "success": True,
            "type": "vote",
            "tweet": tweet,
            "tg_text": tg_text,
            "has_reply": False,
        }

    except Exception as e:
        logger.warning(f"[Viral] C-19 캐릭터투표 생성 실패: {e}")
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────────────────────

def run_viral(session: str = "viral_morning") -> dict:
    """
    바이럴 콘텐츠 통합 파이프라인.

    Args:
        session: "viral_morning" 또는 "viral_afternoon"

    Returns:
        {"success": True, "type": "quiz|money|vote", ...}
    """
    logger.info(f"[Viral] 파이프라인 시작 (session={session})")

    # ── Step 1: 이 시간대에 실행할지 결정 ──
    if not should_run(session):
        logger.info(f"[Viral] 이 시간대 아님 → 스킵 (session={session})")
        return {"success": False, "reason": "not_my_slot"}

    # ── Step 2: 랜덤 딜레이 (DRY_RUN 시 즉시 실행) ──
    if _is_dry_run():
        logger.info("[Viral] DRY_RUN → 딜레이 스킵, 즉시 실행")
    else:
        delay = _random_delay(session)
        logger.info(f"[Viral] 랜덤 딜레이: {delay}초 ({delay//60}분 {delay%60}초)")
        time.sleep(delay)

    # ── Step 3: 콘텐츠 타입 선택 ──
    content_type = _select_content_type()
    logger.info(f"[Viral] 콘텐츠 선택: {content_type}")

    # ── Step 4: 콘텐츠 생성 ──
    if content_type == "quiz":
        content = _generate_quiz()
    elif content_type == "money":
        content = _generate_money_compare()
    elif content_type == "dilemma":
        content = _generate_dilemma()
    elif content_type == "vote":
        content = _generate_character_vote()
    else:
        return {"success": False, "error": f"알 수 없는 타입: {content_type}"}

    if not content.get("success"):
        logger.warning(f"[Viral] 콘텐츠 생성 실패: {content.get('error')}")
        return content

    # ── Step 5: X 발행 ──
    tweet_id = "SKIP"
    try:
        from publishers.x_publisher import publish_tweet
        pub_result = publish_tweet(content["tweet"])
        tweet_id = pub_result.get("tweet_id", "FAIL")
        logger.info(f"[Viral] X 발행: {tweet_id} | type={content_type}")

        # 퀴즈 정답 reply (DRY_RUN 시 즉시, 실 발행 시 30분 후)
        if content.get("has_reply") and content.get("reply"):
            if tweet_id and tweet_id not in ("FAIL", "SKIP", "DRY_RUN"):
                if _is_dry_run():
                    logger.info("[Viral] DRY_RUN → 퀴즈 reply 즉시 발행 (30분 대기 스킵)")
                else:
                    logger.info("[Viral] 퀴즈 정답 reply 30분 대기...")
                    time.sleep(1800)  # 30분
                try:
                    from publishers.x_publisher import publish_tweet as _pub_reply
                    # reply_to가 지원되면 사용, 아니면 일반 트윗
                    try:
                        _pub_reply(content["reply"], reply_to=tweet_id)
                    except TypeError:
                        _pub_reply(content["reply"])
                    logger.info("[Viral] 퀴즈 정답 reply 발행 완료")
                except Exception as re:
                    logger.warning(f"[Viral] 퀴즈 정답 reply 실패 (무시): {re}")
            else:
                logger.info("[Viral] DRY_RUN — 퀴즈 reply 스킵")

    except Exception as e:
        logger.warning(f"[Viral] X 발행 실패: {e}")

    # ── Step 6: TG 발행 ──
    try:
        from publishers.telegram_publisher import send_message
        send_message(content.get("tg_text", content["tweet"]), channel="free")
        logger.info("[Viral] TG 발행 완료")
    except Exception as e:
        logger.warning(f"[Viral] TG 발행 실패: {e}")

    return {
        "success": True,
        "type": content_type,
        "tweet_id": tweet_id,
        "session": session,
    }


# CLI 지원
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    session = sys.argv[1] if len(sys.argv) > 1 else "viral_morning"
    result = run_viral(session)
    print(result)
