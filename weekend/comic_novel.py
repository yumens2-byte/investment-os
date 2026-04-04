"""
weekend/comic_novel.py (C-7)
===================================
EDT Universe 주간 소설형 에피소드 발행.

매주 일요일 22:00 KST:
  1. Supabase 캐시 확인 → HIT이면 DB에서 로드 (Claude 호출 0)
  2. MISS이면 Notion API로 이번 주 에피소드 조회
  3. Claude로 주간 합본 소설 생성 (2000~3000자)
  4. Supabase 캐시 저장
  5. X 스레드 + TG 장문 발행

VERSION = "1.0.0"
RPD: +0 (Claude API, Gemini 미사용)
"""
import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
HUB_PAGE_ID = "3299208cbdc38183814fdb7cfb1908e9"


def _notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _get_week_label() -> str:
    """현재 주 라벨 (예: 2026-W14)"""
    now = datetime.now(KST)
    return now.strftime("%G-W%V")


def get_episodes_from_notion() -> list:
    """
    Notion 허브 하위 페이지 중 '에피소드'가 포함된 페이지 검색.
    최근 7일 이내 수정된 에피소드만 필터링.
    """
    if not NOTION_API_KEY:
        logger.warning("[ComicNovel] NOTION_API_KEY 미설정")
        return []

    try:
        import requests
        # Notion Search API로 에피소드 검색
        resp = requests.post(
            "https://api.notion.com/v1/search",
            headers=_notion_headers(),
            json={
                "query": "에피소드",
                "filter": {"property": "object", "value": "page"},
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                "page_size": 10,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"[ComicNovel] Notion 검색 실패: {resp.status_code}")
            return []

        results = resp.json().get("results", [])
        episodes = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=8)

        for page in results:
            title_parts = page.get("properties", {}).get("title", {}).get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts) if title_parts else ""

            if "에피소드" not in title:
                continue

            # 에피소드 번호 추출
            ep_match = re.search(r'에피소드\s*(\d+)', title)
            if not ep_match:
                continue

            last_edited = page.get("last_edited_time", "")
            if last_edited:
                edit_dt = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
                if edit_dt < cutoff:
                    continue

            episodes.append({
                "page_id": page["id"],
                "episode_no": int(ep_match.group(1)),
                "title": title,
                "last_edited": last_edited,
            })

        episodes.sort(key=lambda x: x["episode_no"])
        logger.info(f"[ComicNovel] Notion에서 {len(episodes)}개 에피소드 발견")
        return episodes

    except Exception as e:
        logger.warning(f"[ComicNovel] Notion 조회 실패: {e}")
        return []


def _fetch_episode_content(page_id: str) -> str:
    """Notion 페이지의 블록 내용을 텍스트로 추출"""
    try:
        import requests
        resp = requests.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
            headers=_notion_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            return ""

        blocks = resp.json().get("results", [])
        texts = []
        for block in blocks:
            btype = block.get("type", "")
            content = block.get(btype, {})
            # rich_text 추출
            for rt_key in ["rich_text", "text"]:
                if rt_key in content:
                    for rt in content[rt_key]:
                        texts.append(rt.get("plain_text", ""))
                    break
            # code 블록
            if btype == "code" and "rich_text" in content:
                for rt in content["rich_text"]:
                    texts.append(rt.get("plain_text", ""))

        return "\n".join(texts)
    except Exception as e:
        logger.warning(f"[ComicNovel] 블록 조회 실패: {e}")
        return ""


def novelify_episodes(episodes: list) -> dict:
    """
    Claude로 주간 에피소드 합본 소설 생성.
    Returns: {success, novel_text, x_thread, tg_html, episode_range, title}
    """
    if not episodes:
        return {"success": False, "error": "에피소드 없음"}

    # 에피소드 스크립트 수집
    scripts = []
    for ep in episodes:
        content = _fetch_episode_content(ep["page_id"])
        if content:
            scripts.append(f"--- EP.{ep['episode_no']}: {ep['title']} ---\n{content[:3000]}")

    if not scripts:
        return {"success": False, "error": "스크립트 추출 실패"}

    ep_range = f"EP.{episodes[0]['episode_no']}~EP.{episodes[-1]['episode_no']}"
    combined_script = "\n\n".join(scripts)

    # Claude 소설화
    try:
        import anthropic
        client = anthropic.Anthropic()

        prompt = (
            f"다음은 투자 코믹스 '{ep_range}' 에피소드 스크립트입니다.\n"
            f"이것을 한국어 주간 합본 소설로 리라이팅해주세요.\n\n"
            f"스크립트:\n{combined_script[:6000]}\n\n"
            f"조건:\n"
            f"1. 3인칭 소설 형식 (\"그녀는... 하였다\" 스타일)\n"
            f"2. 2500~3500자 내외\n"
            f"3. 대사는 「」 사용\n"
            f"4. 에피소드 간 전환은 *** 구분선\n"
            f"5. 마지막 단락에 투자 인사이트 자연스럽게 삽입\n"
            f"6. 문체: 긴장감 있는 경제/금융 스릴러 톤\n"
            f"7. 캐릭터명 그대로 사용 (EDT, Futures Girl 등)\n"
            f"8. 다음 에피소드 예고로 끝내기\n"
            f"9. 소설 본문만 출력. 제목/설명/부연 없이.\n"
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )

        novel_text = response.content[0].text.strip()

        if len(novel_text) < 500:
            return {"success": False, "error": "소설 생성 결과 너무 짧음"}

        # X 스레드 분할 (프리미엄 25,000자 → 2~3개 스레드)
        x_thread = _split_to_thread(novel_text, ep_range)

        # TG HTML 생성
        tg_html = _format_tg_html(novel_text, ep_range)

        # 제목 생성
        title = f"EDT Universe 주간 소설 — {ep_range}"

        logger.info(f"[ComicNovel] 소설 생성 완료: {len(novel_text)}자, 스레드 {len(x_thread)}개")

        return {
            "success": True,
            "novel_text": novel_text,
            "x_thread": x_thread,
            "tg_html": tg_html,
            "episode_range": ep_range,
            "title": title,
        }

    except Exception as e:
        logger.error(f"[ComicNovel] Claude 소설화 실패: {e}")
        return {"success": False, "error": str(e)}


def _split_to_thread(novel_text: str, ep_range: str) -> list:
    """소설을 X 스레드로 분할 (프리미엄 25,000자 활용)"""
    # Thread 1: 헤더
    header = (
        f"📝 EDT Universe 주간 소설\n"
        f"『{ep_range}』\n\n"
        f"이번 주 EDT Universe를 소설로 만나보세요.\n"
        f"🧵👇"
    )

    # 본문을 *** 기준으로 분할, 없으면 1500자 단위
    sections = novel_text.split("***")
    threads = [header]

    for section in sections:
        section = section.strip()
        if not section:
            continue
        # 2500자 초과 시 분할
        while len(section) > 2500:
            # 마침표 기준 분할
            cut_pos = section[:2500].rfind(".")
            if cut_pos < 500:
                cut_pos = 2500
            threads.append(section[:cut_pos + 1])
            section = section[cut_pos + 1:].strip()
        if section:
            threads.append(section)

    # 마지막: 해시태그 + 면책
    footer = (
        "📊 투자 인사이트는 본문 참고\n\n"
        "#EDT #투자코믹스 #미국증시 #ETF투자 #투자소설\n"
        "#주식전쟁 #금융스릴러 #InvestmentComic\n\n"
        "⚠️ 본 콘텐츠는 투자 참고 정보이며, 투자 권유가 아닙니다."
    )
    threads.append(footer)

    return threads


def _format_tg_html(novel_text: str, ep_range: str) -> str:
    """소설을 TG HTML 포맷으로 변환"""
    # 「」 대사를 이탈릭으로
    html = novel_text.replace("「", "<i>「").replace("」", "」</i>")
    # *** 구분선
    html = html.replace("***", "\n━━━\n")

    return (
        f"📝 <b>EDT Universe 주간 소설</b>\n"
        f"<b>『{ep_range}』</b>\n\n"
        f"{html}\n\n"
        f"━━━\n"
        f"⚠️ 본 콘텐츠는 투자 참고 정보이며, 투자 권유가 아닙니다."
    )


def publish_novel_episode() -> dict:
    """
    C-7 전체 파이프라인:
    1. Supabase 캐시 확인
    2. MISS → Notion 조회 + Claude 소설화 + 캐시 저장
    3. X 스레드 + TG 장문 발행
    """
    logger.info("[ComicNovel] C-7 소설형 에피소드 파이프라인 시작")
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # ── Step 0: Supabase 캐시 확인 ──
    cached = None
    try:
        from db.daily_store import get_novel
        cached = get_novel(today)
    except Exception as e:
        logger.warning(f"[ComicNovel] 캐시 조회 실패 (무시): {e}")

    if cached:
        logger.info(f"[ComicNovel] 캐시 HIT — {cached.get('episode_range', '?')} (Claude 호출 0)")
        novel_data = cached
    else:
        # ── Step 1: Notion 조회 ──
        episodes = get_episodes_from_notion()
        if not episodes:
            logger.warning("[ComicNovel] 이번 주 에피소드 없음 → 스킵")
            return {"success": False, "error": "이번 주 에피소드 없음"}

        # ── Step 2: Claude 소설화 ──
        novel_data = novelify_episodes(episodes)
        if not novel_data.get("success"):
            logger.warning(f"[ComicNovel] 소설화 실패: {novel_data.get('error')}")
            return novel_data

        # ── Step 2.5: Supabase 캐시 저장 ──
        try:
            from db.daily_store import save_novel
            save_novel(
                publish_date=today,
                week_label=_get_week_label(),
                episode_range=novel_data["episode_range"],
                title=novel_data["title"],
                novel_text=novel_data["novel_text"],
                x_thread=novel_data["x_thread"],
                tg_html=novel_data["tg_html"],
                source_pages=[ep["page_id"] for ep in episodes],
            )
            logger.info("[ComicNovel] Supabase 캐시 저장 완료")
        except Exception as e:
            logger.warning(f"[ComicNovel] 캐시 저장 실패 (발행은 계속): {e}")

    # ── Step 3: X 스레드 발행 ──
    x_thread = novel_data.get("x_thread", [])
    tweet_id = "SKIP"
    try:
        from publishers.x_publisher import publish_thread
        x_result = publish_thread(x_thread)
        tweet_id = x_result.get("tweet_id", "FAIL")
        logger.info(f"[ComicNovel] X 스레드 발행: {tweet_id} ({len(x_thread)}개)")
    except Exception as e:
        logger.warning(f"[ComicNovel] X 발행 실패: {e}")

    # ── Step 4: TG 장문 발행 ──
    tg_html = novel_data.get("tg_html", "")
    try:
        from publishers.telegram_publisher import send_message
        send_message(tg_html, channel="free")
        logger.info("[ComicNovel] TG 발행 완료")
    except Exception as e:
        logger.warning(f"[ComicNovel] TG 발행 실패: {e}")

    # ── Step 4.5: Supabase status 업데이트 ──
    try:
        from db.daily_store import update_novel_status
        update_novel_status(today, "published")
    except Exception:
        pass

    return {
        "success": True,
        "type": "comic_novel",
        "episode_range": novel_data.get("episode_range", ""),
        "novel_length": len(novel_data.get("novel_text", "")),
        "thread_count": len(x_thread),
        "tweet_id": tweet_id,
    }
