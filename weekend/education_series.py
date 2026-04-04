"""
weekend/education_series.py (C-4)
==================================
격주 토요일 투자 교육 시리즈

Gemini로 투자 교육 콘텐츠 자동 생성.
주간 리뷰 발행 후 격주(홀수 주)에만 추가 발행.

Gemini 실패 시 스킵 (주간 리뷰 발행에 영향 없음).
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# 주제 풀 (15개 → 약 30주 순환)
TOPICS = [
    {"topic": "ETF란 무엇인가? — 주식 vs ETF vs 펀드 비교", "level": "입문"},
    {"topic": "VIX 지수 완벽 가이드 — 공포 지수의 모든 것", "level": "입문"},
    {"topic": "레짐이란? — 강세/약세/변동성/위기 구분법", "level": "중급"},
    {"topic": "Fear & Greed Index 읽는 법", "level": "입문"},
    {"topic": "매크로 지표 읽기 — 금리/유가/달러", "level": "중급"},
    {"topic": "수익률 곡선과 경기침체 신호", "level": "중급"},
    {"topic": "포지션 사이징 — 리스크에 따른 투자 비율", "level": "중급"},
    {"topic": "섹터 로테이션 전략", "level": "중급"},
    {"topic": "배당 ETF vs 성장 ETF", "level": "입문"},
    {"topic": "환율이 미국 주식에 미치는 영향", "level": "중급"},
    {"topic": "채권 TLT/TIPS 투자 가이드", "level": "중급"},
    {"topic": "실적 시즌의 ETF 전략", "level": "고급"},
    {"topic": "트레일링 스톱 vs 지정가 주문", "level": "입문"},
    {"topic": "문화/유행에 따른 섹터 투자", "level": "고급"},
    {"topic": "위기 시 현금 확보 전략", "level": "중급"},
]


def is_education_week() -> bool:
    return True  // 매주 실행


def _get_topic_index() -> int:
    """현재 주차 기준 주제 인덱스 (순환)"""
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    week_num = kst.isocalendar()[1]
    # 홀수 주만 실행하므로 week_num // 2 로 순번 결정
    return (week_num // 2) % len(TOPICS)


def generate_education_content() -> dict:
    """
    투자 교육 콘텐츠 생성.

    Returns:
        {
          "success": True/False,
          "topic": "주제명",
          "level": "입문/중급/고급",
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
        level = topic_info["level"]
        episode = idx + 1

        prompt = (
            f"투자 교육 콘텐츠를 생성해줘.\n"
            f"주제: {topic}\n"
            f"난이도: {level}\n\n"
            f"JSON으로 응답:\n"
            f'{{"title": "주제 제목",\n'
            f' "intro": "1~2줄 도입부",\n'
            f' "key_points": ["핵심 1", "핵심 2", "핵심 3"],\n'
            f' "example": "실제 예시 (숫자 포함)",\n'
            f' "takeaway": "핵심 요약 1줄"}}\n\n'
            f"조건:\n"
            f"- 초보자도 이해할 수 있게\n"
            f"- 실제 수치/예시 포함\n"
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
            example = str(d.get("example", ""))
            takeaway = str(d.get("takeaway", ""))

            if not intro or not points:
                logger.warning("[Education] Gemini 응답 불완전 → 스킵")
                return {"success": False, "reason": "응답 불완전"}

            # 핵심 포인트 포맷
            emoji_nums = ["1️⃣", "2️⃣", "3️⃣"]
            points_tweet = "\n".join(
                f"{emoji_nums[i]} {p}" for i, p in enumerate(points[:3])
            )
            points_tg = "\n".join(
                f"{emoji_nums[i]} {p}" for i, p in enumerate(points[:3])
            )

            # 트윗 포맷
            tweet = (
                f"🎓 투자 교실 #{episode} | {title}\n\n"
                f"{intro}\n\n"
                f"📌 핵심:\n{points_tweet}\n\n"
                f"💡 요약: {takeaway}\n\n"
                f"#투자교실 #ETF교육 #투자공부"
            )

            # 280자 초과 시 축약
            if len(tweet) > 280:
                tweet = (
                    f"🎓 투자 교실 #{episode} | {title}\n\n"
                    f"📌 핵심:\n{points_tweet}\n\n"
                    f"💡 {takeaway}\n\n"
                    f"#투자교실 #ETF교육"
                )

            # TG 포맷
            telegram = (
                f"🎓 <b>투자 교실 #{episode}</b> | {title}\n\n"
                f"{intro}\n\n"
                f"📌 <b>핵심 포인트</b>\n{points_tg}\n\n"
                f"💡 <b>예시</b>: {example}\n\n"
                f"🎯 <b>요약</b>: {takeaway}"
            )

            logger.info(
                f"[Education] 생성 완료 | #{episode} {title} | {level}"
            )

            return {
                "success": True,
                "topic": title,
                "level": level,
                "episode": episode,
                "tweet": tweet,
                "telegram": telegram,
            }

        logger.warning(f"[Education] Gemini 실패: {result.get('error', '?')[:50]}")

    except Exception as e:
        logger.warning(f"[Education] 생성 실패 (무시): {e}")

    return {"success": False, "reason": "Gemini 실패"}
