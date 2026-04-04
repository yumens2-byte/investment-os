"""
weekend/finance_basics.py (C-4B)
=================================
매주 일요일 금융 기본 상식 시리즈

Gemini로 금융 기본 상식 콘텐츠 자동 생성.
일요일 다음 주 프리뷰 발행 후 추가 발행.

Gemini 실패 시 스킵 (다음 주 프리뷰 발행에 영향 없음).
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# 금융 기본 상식 주제 풀 (25개 → 약 6개월 순환)
TOPICS = [
    # ── 돈의 기초 (5개) ──
    {"topic": "복리의 마법 — 72법칙으로 자산 2배 기간 계산", "category": "돈의 기초"},
    {"topic": "인플레이션이란? — 돈의 가치가 줄어드는 이유", "category": "돈의 기초"},
    {"topic": "디플레이션과 스태그플레이션 — 물가의 두 가지 악몽", "category": "돈의 기초"},
    {"topic": "환율이란? — 원/달러/엔 환율이 내 자산에 미치는 영향", "category": "돈의 기초"},
    {"topic": "금리란? — 기준금리가 경제 전체에 미치는 파급력", "category": "돈의 기초"},
    # ── 시장 구조 (5개) ──
    {"topic": "주식 시장의 구조 — NYSE/나스닥/장외시장", "category": "시장 구조"},
    {"topic": "시가총액이란? — 대형주/중형주/소형주 분류법", "category": "시장 구조"},
    {"topic": "거래량과 유동성 — 왜 거래량이 중요한가", "category": "시장 구조"},
    {"topic": "IPO란? — 기업 상장의 과정과 투자 포인트", "category": "시장 구조"},
    {"topic": "서킷브레이커와 거래정지 — 시장 안전장치의 원리", "category": "시장 구조"},
    # ── 투자 개념 (5개) ──
    {"topic": "배당이란? — 배당금/배당률/배당성장의 차이", "category": "투자 개념"},
    {"topic": "공매도란? — 하락에 베팅하는 전략의 원리", "category": "투자 개념"},
    {"topic": "마진과 레버리지 — 양날의 검, 수익과 위험 동시 확대", "category": "투자 개념"},
    {"topic": "액면분할과 무상증자 — 주가가 갑자기 변하는 이유", "category": "투자 개념"},
    {"topic": "자사주 매입이란? — 기업이 자기 주식을 사는 이유", "category": "투자 개념"},
    # ── 경제 지표 (5개) ──
    {"topic": "중앙은행(연준)이란? — Fed의 역할과 구조", "category": "경제 지표"},
    {"topic": "신용등급과 채권 등급 — AAA부터 정크본드까지", "category": "경제 지표"},
    {"topic": "GDP란? — 국내총생산이 투자에 중요한 이유", "category": "경제 지표"},
    {"topic": "실업률과 고용 — 경제 건강의 온도계", "category": "경제 지표"},
    {"topic": "PMI란? — 제조업/서비스업 경기 선행 지표", "category": "경제 지표"},
    # ── 리스크와 심리 (5개) ──
    {"topic": "리스크와 리턴 — 수익률이 높으면 위험도 높다", "category": "리스크와 심리"},
    {"topic": "손절과 익절 — 감정이 아닌 원칙으로 매매하기", "category": "리스크와 심리"},
    {"topic": "FOMO와 패닉셀 — 투자자 심리의 함정", "category": "리스크와 심리"},
    {"topic": "분산투자 vs 집중투자 — 워렌 버핏도 고민한 선택", "category": "리스크와 심리"},
    {"topic": "세금과 투자 — 양도소득세/배당소득세 기본 상식", "category": "리스크와 심리"},
]


def _get_topic_index() -> int:
    """현재 주차 기준 주제 인덱스 (매주 순환)"""
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    week_num = kst.isocalendar()[1]
    return week_num % len(TOPICS)


def generate_finance_basics() -> dict:
    """
    금융 기본 상식 콘텐츠 생성.

    Returns:
        {
          "success": True/False,
          "topic": "주제명",
          "category": "카테고리",
          "episode": 7,
          "tweet": "트윗 포맷",
          "telegram": "TG 포맷",
        }
    """
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            return {"success": False, "reason": "Gemini 미설정"}

        idx = _get_topic_index()
        topic_info = TOPICS[idx]
        topic = topic_info["topic"]
        category = topic_info["category"]
        episode = idx + 1

        prompt = (
            f"금융 기본 상식 콘텐츠를 생성해줘.\n"
            f"주제: {topic}\n"
            f"카테고리: {category}\n\n"
            f"JSON으로 응답:\n"
            f'{{"title": "주제 제목",\n'
            f' "intro": "1~2줄 도입부",\n'
            f' "key_points": ["핵심 1", "핵심 2", "핵심 3"],\n'
            f' "real_example": "실제 숫자가 포함된 생활 속 예시",\n'
            f' "one_line": "한 줄 요약"}}\n\n'
            f"조건:\n"
            f"- 금융 초보자도 바로 이해할 수 있는 수준\n"
            f"- 일상생활 비유 활용\n"
            f"- 실제 숫자/사례 반드시 포함\n"
            f"- 한국어로\n"
            f"- JSON만 출력"
        )

        result = call(
            prompt=prompt,
            model="flash-lite",
            max_tokens=500,
            temperature=0.5,
            response_json=True,
        )

        if result.get("success") and result.get("data"):
            d = result["data"]
            title = str(d.get("title", topic))
            intro = str(d.get("intro", ""))
            points = d.get("key_points", [])
            example = str(d.get("real_example", ""))
            one_line = str(d.get("one_line", ""))

            if not intro or not points:
                logger.warning("[FinanceBasics] Gemini 응답 불완전 → 스킵")
                return {"success": False, "reason": "응답 불완전"}

            # 핵심 포인트 포맷
            emoji_nums = ["1️⃣", "2️⃣", "3️⃣"]
            points_fmt = "\n".join(
                f"{emoji_nums[i]} {p}" for i, p in enumerate(points[:3])
            )

            # 트윗 포맷 (X Premium — 제한 없음)
            tweet = (
                f"💰 금융 상식 #{episode} | {title}\n\n"
                f"{intro}\n\n"
                f"📌 핵심:\n{points_fmt}\n\n"
                f"💡 예시: {example}\n\n"
                f"🎯 한 줄 정리: {one_line}\n\n"
                f"#금융상식 #경제공부 #투자기초"
            )

            # TG 포맷
            telegram = (
                f"💰 <b>금융 상식 #{episode}</b> | {title}\n\n"
                f"{intro}\n\n"
                f"📌 <b>핵심 포인트</b>\n{points_fmt}\n\n"
                f"💡 <b>예시</b>: {example}\n\n"
                f"🎯 <b>한 줄 정리</b>: {one_line}"
            )

            logger.info(
                f"[FinanceBasics] 생성 완료 | #{episode} {title} | {category}"
            )

            return {
                "success": True,
                "topic": title,
                "category": category,
                "episode": episode,
                "tweet": tweet,
                "telegram": telegram,
            }

        logger.warning(f"[FinanceBasics] Gemini 실패: {result.get('error', '?')[:50]}")

    except Exception as e:
        logger.warning(f"[FinanceBasics] 생성 실패 (무시): {e}")

    return {"success": False, "reason": "Gemini 실패"}
