"""
weekend/comic_novel.py (C-7)
===================================
EDT Universe 주간 소설형 에피소드 발행.

매주 일요일 22:00 KST:
  1. Supabase 캐시 확인 → HIT이면 DB에서 로드 (Claude 호출 0)
  2. MISS → episode_context DB에서 에피소드 조회 (우선)
  3. DB 없으면 → Notion API에서 Properties 기반 조회 (fallback)
  4. Claude로 주간 합본 소설 생성 (2500~3500자)
  5. Supabase 캐시 저장
  6. Gemini 표지 이미지 생성 (영어 프롬프트, 텍스트 없음)
  7. X 이미지 트윗(표지) + 스레드 + TG 장문 발행

VERSION = "1.2.0"  # Properties 기반 스크립트 + episode_context DB 우선 조회
RPD: +0 Claude API + 1 Gemini (표지 이미지)
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

# ── EDT Universe 캐릭터 정보 (표지 프롬프트용) ──
CHARACTER_VISUAL = {
    "EDT": "young hero with blue energy aura and golden ring on right hand, dual-color chainsaw weapon",
    "Leverage Man": "muscular man with fire gauntlets and leverage amplifier belt",
    "Iron Nuna": "woman in silver ETF shield armor with data visor",
    "Futures Girl": "woman with golden eyes and hologram futures chart projection",
    "Gold Bond": "knight in golden armor with heavy golden shield",
    "War Dominion": "dark villain with red eyes and missile launchers, military commander",
    "Oil Shock Titan": "giant fire-crowned monster made of oil barrels",
    "Debt Titan": "skeletal giant with bond chain whips, cracks in ground",
    "Algorithm Reaper": "hooded figure with digital code streams and glowing circuit patterns",
    "Baron Bearsworth": "dark bear villain in business suit with market crash charts",
}


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
    Notion 허브 하위 페이지 중 에피소드 페이지 검색.
    Supabase에 마지막 적재된 에피소드 번호 이후만 필터링.
    제목 형식: "[에피소드N]" 또는 "Ep N" 모두 지원.
    """
    if not NOTION_API_KEY:
        logger.warning("[ComicNovel] NOTION_API_KEY 미설정")
        return []

    # ── 마지막 적재 에피소드 번호 조회 ──
    last_ep = 0
    try:
        from db.daily_store import get_last_novel_episode_no
        last_ep = get_last_novel_episode_no()
        logger.info(f"[ComicNovel] 마지막 적재 에피소드: EP.{last_ep:02d}")
    except Exception as e:
        logger.warning(f"[ComicNovel] 마지막 에피소드 조회 실패 (전체 조회): {e}")

    try:
        import requests
        # Notion Search API로 에피소드 검색 (두 형식 모두 커버)
        all_episodes = []
        for query_keyword in ["에피소드", "Ep"]:
            resp = requests.post(
                "https://api.notion.com/v1/search",
                headers=_notion_headers(),
                json={
                    "query": query_keyword,
                    "filter": {"property": "object", "value": "page"},
                    "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                    "page_size": 20,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            results = resp.json().get("results", [])
            for page in results:
                title_parts = page.get("properties", {}).get("title", {}).get("title", [])
                title = "".join(t.get("plain_text", "") for t in title_parts) if title_parts else ""

                # 에피소드 번호 추출 (두 형식 지원)
                # "[에피소드10]" 또는 "Ep 01" 또는 "Ep.10"
                ep_match = re.search(r'(?:에피소드|Ep\s*\.?\s*)(\d+)', title)
                if not ep_match:
                    continue

                ep_no = int(ep_match.group(1))

                # 이미 적재된 에피소드는 스킵
                if ep_no <= last_ep:
                    continue

                # 중복 제거 (page_id 기준)
                if any(e["page_id"] == page["id"] for e in all_episodes):
                    continue

                all_episodes.append({
                    "page_id": page["id"],
                    "episode_no": ep_no,
                    "title": title,
                    "last_edited": page.get("last_edited_time", ""),
                })

        all_episodes.sort(key=lambda x: x["episode_no"])
        logger.info(f"[ComicNovel] Notion에서 {len(all_episodes)}개 신규 에피소드 발견 (EP.{last_ep:02d} 이후)")
        return all_episodes

    except Exception as e:
        logger.warning(f"[ComicNovel] Notion 조회 실패: {e}")
        return []


def _fetch_episode_content(page_id: str) -> str:
    """
    Notion 페이지에서 에피소드 스크립트 추출.
    1순위: 본문 블록 (rich_text)
    2순위: Properties 기반 스크립트 조합 (blank page 대응)
    """
    try:
        import requests

        # ── 1순위: 본문 블록 추출 ──
        resp = requests.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100",
            headers=_notion_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            blocks = resp.json().get("results", [])
            texts = []
            for block in blocks:
                btype = block.get("type", "")
                content = block.get(btype, {})
                for rt_key in ["rich_text", "text"]:
                    if rt_key in content:
                        for rt in content[rt_key]:
                            texts.append(rt.get("plain_text", ""))
                        break
                if btype == "code" and "rich_text" in content:
                    for rt in content["rich_text"]:
                        texts.append(rt.get("plain_text", ""))

            block_text = "\n".join(texts).strip()
            if len(block_text) > 50:  # 의미 있는 본문이 있으면 사용
                return block_text

        # ── 2순위: Properties 기반 스크립트 조합 ──
        logger.info(f"[ComicNovel] 본문 블록 없음 → Properties 기반 추출: {page_id}")
        resp_page = requests.get(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_notion_headers(),
            timeout=15,
        )
        if resp_page.status_code != 200:
            return ""

        props = resp_page.json().get("properties", {})
        script_parts = []

        # 에피소드 제목
        title_prop = props.get("에피소드", {})
        title_rt = title_prop.get("title", []) if title_prop.get("type") == "title" else []
        ep_title = "".join(t.get("plain_text", "") for t in title_rt)
        if ep_title:
            script_parts.append(f"제목: {ep_title}")

        # 에피소드 타입
        ep_type = _extract_select(props, "에피소드 타입")
        if ep_type:
            script_parts.append(f"타입: {ep_type}")

        # 메인 히어로
        hero = _extract_select(props, "메인 히어로")
        if hero:
            script_parts.append(f"메인 히어로: {hero}")

        # 활성 빌런
        villain = _extract_select(props, "활성 빌런")
        if villain:
            script_parts.append(f"활성 빌런: {villain}")

        # 전투 결과
        battle_result = _extract_select(props, "전투 결과")
        if battle_result:
            script_parts.append(f"전투 결과: {battle_result}")

        # Battle Balance
        balance = props.get("Battle Balance", {})
        if balance.get("type") == "number" and balance.get("number") is not None:
            script_parts.append(f"Battle Balance: {balance['number']}")

        # Arc Day
        arc_day = props.get("Arc Day", {})
        if arc_day.get("type") == "number" and arc_day.get("number") is not None:
            script_parts.append(f"Arc Day: {arc_day['number']}")

        # 특이사항 (가장 중요 — 스토리 핵심)
        notes = _extract_rich_text(props, "특이사항")
        if notes:
            script_parts.append(f"특이사항: {notes}")

        result = "\n".join(script_parts)
        if result:
            logger.info(f"[ComicNovel] Properties 추출 성공: {len(result)}자")
        return result

    except Exception as e:
        logger.warning(f"[ComicNovel] 에피소드 추출 실패: {e}")
        return ""


def _extract_select(props: dict, key: str) -> str:
    """Notion Properties에서 select/status 타입 값 추출"""
    prop = props.get(key, {})
    ptype = prop.get("type", "")
    if ptype == "select" and prop.get("select"):
        return prop["select"].get("name", "")
    if ptype == "status" and prop.get("status"):
        return prop["status"].get("name", "")
    return ""


def _extract_rich_text(props: dict, key: str) -> str:
    """Notion Properties에서 rich_text 타입 값 추출"""
    prop = props.get(key, {})
    if prop.get("type") == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    return ""


def get_episodes_from_db() -> list:
    """
    episode_context 테이블에서 아직 소설화되지 않은 에피소드 조회.
    comic_novels 테이블의 마지막 에피소드 이후만 반환.
    """
    try:
        from db.daily_store import get_last_novel_episode_no
        last_ep = get_last_novel_episode_no()
        logger.info(f"[ComicNovel] 마지막 적재 에피소드: EP.{last_ep:02d}")
    except Exception:
        last_ep = 0

    try:
        from db.supabase_client import get_client
        sb = get_client()
        resp = sb.table("episode_context") \
            .select("episode_no, comic_type, risk_level, title, summary") \
            .gt("episode_no", last_ep) \
            .order("episode_no") \
            .execute()

        if not resp.data:
            logger.info("[ComicNovel] episode_context에 신규 에피소드 없음")
            return []

        episodes = []
        for row in resp.data:
            # Properties 형식과 동일하게 스크립트 조합
            script = (
                f"제목: {row.get('title', '')}\n"
                f"타입: {row.get('comic_type', '')}\n"
                f"Risk Level: {row.get('risk_level', '')}\n"
                f"요약: {row.get('summary', '')}"
            )
            episodes.append({
                "page_id": None,  # DB 소스 → Notion page_id 없음
                "episode_no": row["episode_no"],
                "title": row.get("title", f"EP.{row['episode_no']}"),
                "content": script,  # 미리 추출된 스크립트
            })

        logger.info(f"[ComicNovel] episode_context에서 {len(episodes)}개 신규 에피소드 (EP.{last_ep:02d} 이후)")
        return episodes

    except Exception as e:
        logger.warning(f"[ComicNovel] episode_context 조회 실패: {e}")
        return []


def novelify_episodes(episodes: list) -> dict:
    """
    Claude로 주간 에피소드 합본 소설 생성.
    에피소드 소스: episode_context DB (content 필드) 또는 Notion (page_id로 조회).
    Returns: {success, novel_text, x_thread, tg_html, episode_range, title}
    """
    if not episodes:
        return {"success": False, "error": "에피소드 없음"}

    # 에피소드 스크립트 수집
    scripts = []
    for ep in episodes:
        # DB 소스: content 필드가 이미 있음
        if ep.get("content"):
            content = ep["content"]
        # Notion 소스: page_id로 블록+Properties 추출
        elif ep.get("page_id"):
            content = _fetch_episode_content(ep["page_id"])
        else:
            content = ""

        if content:
            scripts.append(f"--- EP.{ep['episode_no']:02d}: {ep['title']} ---\n{content[:3000]}")

    if not scripts:
        return {"success": False, "error": "스크립트 추출 실패"}

    ep_range = f"EP.{episodes[0]['episode_no']:02d}~EP.{episodes[-1]['episode_no']:02d}"
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

        # 최소 길이 검증 (1500자 미만 시 1회 재시도)
        if len(novel_text) < 1500:
            logger.warning(f"[ComicNovel] 소설 {len(novel_text)}자 → 짧음, 재시도")
            retry_prompt = prompt + "\n\n주의: 반드시 2500자 이상 작성해주세요. 짧으면 안 됩니다."
            retry_resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            retry_text = retry_resp.content[0].text.strip()
            if len(retry_text) > len(novel_text):
                novel_text = retry_text
                logger.info(f"[ComicNovel] 재시도 성공: {len(novel_text)}자")

        if len(novel_text) < 500:
            return {"success": False, "error": f"소설 생성 결과 너무 짧음 ({len(novel_text)}자)"}

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
        f"#EDT #투자코믹스 #미국증시 #ETF투자 #투자소설\n"
        f"#주식전쟁 #금융스릴러 #InvestmentComic\n\n"
        f"⚠️ 본 콘텐츠는 투자 참고 정보이며, 투자 권유가 아닙니다."
    )


# ──────────────────────────────────────────────────────────────
# 표지 이미지 생성 (Gemini, 영어 프롬프트)
# ──────────────────────────────────────────────────────────────

def _generate_cover_image(novel_text: str, ep_range: str) -> str | None:
    """
    소설 내용 기반 Gemini 표지 이미지 생성.
    전체 영어 프롬프트 — 이미지 내 텍스트 없음.
    실패 시 None 반환 (텍스트만 발행 계속).

    Returns: 이미지 파일 경로 또는 None
    """
    try:
        from core.gemini_gateway import generate_image, is_available
        if not is_available():
            logger.info("[ComicNovel] Gemini 미사용 → 표지 생성 스킵")
            return None

        # ── 소설에서 등장 캐릭터 추출 ──
        characters_in_story = []
        for char_name, visual in CHARACTER_VISUAL.items():
            if char_name in novel_text:
                characters_in_story.append(f"{char_name}: {visual}")

        # 최대 3캐릭터만 표지에 포함
        char_desc = "; ".join(characters_in_story[:3]) if characters_in_story else \
            "EDT: young hero with blue energy aura and golden ring"

        # ── 소설 분위기 추출 (키워드 기반) ──
        mood = "intense battle"
        if any(w in novel_text for w in ["교착", "멈춰", "정적"]):
            mood = "tense standoff, calm before storm"
        elif any(w in novel_text for w in ["폭발", "충돌", "전쟁"]):
            mood = "explosive battle, energy clash"
        elif any(w in novel_text for w in ["각성", "골드", "진화"]):
            mood = "awakening, golden transformation, power surge"
        elif any(w in novel_text for w in ["침묵", "어둠", "코드"]):
            mood = "dark mystery, digital shadows, code streams"

        # ── 영어 프롬프트 생성 ──
        prompt = (
            f"Anime-style dramatic cover art for a financial market battle story.\n"
            f"Episode: {ep_range}\n"
            f"Scene mood: {mood}\n"
            f"Characters: {char_desc}\n"
            f"Background: futuristic financial city with stock chart hologram skyline, "
            f"glowing market data streams in the sky.\n"
            f"Style: cinematic lighting, dynamic manga cover composition, "
            f"vibrant colors, dramatic shadows, high contrast.\n"
            f"Aspect ratio: 16:9 landscape.\n"
            f"CRITICAL: Absolutely NO text, NO letters, NO words, NO numbers, "
            f"NO title, NO watermark anywhere in the image. "
            f"Pure visual artwork only."
        )

        # ── Gemini 이미지 생성 ──
        output_path = f"data/images/novel_cover_{ep_range.replace('~', '_')}.png"
        os.makedirs("data/images", exist_ok=True)

        result = generate_image(prompt=prompt, output_path=output_path)

        if result.get("success") and os.path.exists(output_path):
            logger.info(f"[ComicNovel] 표지 생성 완료: {output_path}")
            return output_path
        else:
            logger.warning(f"[ComicNovel] 표지 생성 실패: {result.get('error', '?')}")
            return None

    except Exception as e:
        logger.warning(f"[ComicNovel] 표지 생성 예외 (텍스트만 발행): {e}")
        return None


def publish_novel_episode() -> dict:
    """
    C-7 전체 파이프라인:
    1. Supabase 캐시 확인
    2. MISS → Notion 조회 + Claude 소설화 + 캐시 저장
    3. 표지 이미지 생성 (Gemini)
    4. X 이미지 트윗(표지) + 스레드 + TG 장문 발행
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
        # ── Step 1: episode_context DB 우선 조회 ──
        episodes = get_episodes_from_db()
        if episodes:
            logger.info(f"[ComicNovel] episode_context DB에서 {len(episodes)}개 에피소드 로드")
        else:
            # ── Step 1b: Notion fallback ──
            logger.info("[ComicNovel] DB 없음 → Notion 조회 fallback")
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

    # ── Step 2.7: 표지 이미지 생성 (Gemini) ──
    cover_path = None
    try:
        novel_text = novel_data.get("novel_text", "")
        ep_range = novel_data.get("episode_range", "")
        cover_path = _generate_cover_image(novel_text, ep_range)
    except Exception as e:
        logger.warning(f"[ComicNovel] 표지 생성 예외 (텍스트만 발행): {e}")

    # ── Step 3: X 발행 (표지 있으면 이미지 트윗 → 스레드) ──
    x_thread = novel_data.get("x_thread", [])
    tweet_id = "SKIP"
    try:
        if cover_path and len(x_thread) > 0:
            # 표지 이미지 + 헤더 텍스트를 이미지 트윗으로 발행
            from publishers.x_publisher import publish_tweet_with_image, publish_thread
            header_text = x_thread[0]  # 헤더 포스트
            img_result = publish_tweet_with_image(header_text, cover_path)
            first_tweet_id = img_result.get("tweet_id", "FAIL")

            # 나머지 스레드를 reply로 발행
            if len(x_thread) > 1 and first_tweet_id and first_tweet_id != "FAIL":
                remaining = x_thread[1:]
                thread_result = publish_thread(remaining, reply_to=first_tweet_id)
                tweet_id = first_tweet_id
                logger.info(
                    f"[ComicNovel] X 표지+스레드 발행: {tweet_id} "
                    f"(표지 1장 + 스레드 {len(remaining)}개)"
                )
            else:
                tweet_id = first_tweet_id
                logger.info(f"[ComicNovel] X 표지 트윗만 발행: {tweet_id}")
        else:
            # 표지 없으면 기존 방식 (텍스트 스레드)
            from publishers.x_publisher import publish_thread
            x_result = publish_thread(x_thread)
            tweet_id = x_result.get("tweet_id", "FAIL")
            logger.info(f"[ComicNovel] X 스레드 발행 (표지 없음): {tweet_id} ({len(x_thread)}개)")
    except Exception as e:
        logger.warning(f"[ComicNovel] X 발행 실패: {e}")

    # ── Step 4: TG 장문 발행 ──
    tg_html = novel_data.get("tg_html", "")
    try:
        from publishers.telegram_publisher import send_message, send_photo

        # TG에도 표지 이미지 먼저 발행 (있으면)
        if cover_path:
            try:
                send_photo(cover_path, caption=f"📝 EDT Universe 『{ep_range}』", channel="free")
                logger.info("[ComicNovel] TG 표지 이미지 발행 완료")
            except Exception as pe:
                logger.warning(f"[ComicNovel] TG 표지 발행 실패 (무시): {pe}")

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
        "cover": bool(cover_path),
    }
