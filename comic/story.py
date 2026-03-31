"""
comic/story.py
Investment Comic v2.0 — Claude API 스토리라인 생성

변경사항 (v1.x → v2.0):
  - 에피소드 번호 파라미터 추가
  - 이전 에피소드 요약 주입 (연속성 보장)
  - JSON 출력 강제 + 파싱 안정화
  - 리스크 레벨별 캐릭터 분기 명확화
"""

import os
import json
import logging
from datetime import date

import anthropic

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000

SYSTEM_PROMPT = """You are the writer of "Investment Comic", a daily financial webcomic.

## Characters (DO NOT change names or reference real IPs)
- **Max Bullhorn**: Heroic golden bull warrior. Optimistic, inspiring, protects rising markets.
- **Baron Bearsworth**: Villainous dark bear. Sinister, calculating, thrives on market fear.
- **The Volatician**: Chaos wizard. Appears only in HIGH risk. Unpredictable, cryptic.

## Rules
1. Output ONLY valid JSON. No markdown, no explanation, no code blocks.
2. NEVER mention Marvel, DC, Disney, Nintendo, or any real IP.
3. NEVER mention real company names in dialogue.
4. Keep financial terms entertaining and accessible.
5. Each cut must advance the story — no filler.
6. image_prompt must be in English, cinematic comic style, safe for all ages.
7. image_prompt must NOT include character names — describe appearance only.

## Story Structure
- daily (4 cuts): Setup → Conflict → Climax → Resolution
- weekly (8 cuts): Extended arc, subplot, cliffhanger optional

## Output Format
{
  "title": "Episode title",
  "caption": "X post text with 2-3 hashtags (Korean OK, max 200 chars)",
  "context_summary": "1-2 sentence summary for next episode continuity",
  "cuts": [
    {
      "cut_number": 1,
      "scene": "Scene description (Korean, 1-2 sentences)",
      "dialogue": "Character dialogue (Korean)",
      "image_prompt": "English cinematic comic panel prompt, describe character appearance not name",
      "mood": "optimistic|tense|chaotic|warning|triumphant"
    }
  ]
}"""


def _build_user_prompt(
    risk_level: str,
    comic_type: str,
    market_data: dict,
    episode_no: int,
    recent_episodes: list[dict]
) -> str:
    """유저 프롬프트 조립"""

    # 이전 에피소드 요약 (연속성)
    prev_summary = ""
    if recent_episodes:
        lines = []
        for ep in recent_episodes:
            lines.append(
                f"  - Ep.{ep['episode_no']} [{ep['risk_level']}] {ep['title']}: {ep.get('summary', '')}"
            )
        prev_summary = "## Previous Episodes (for continuity)\n" + "\n".join(lines)

    # 리스크별 캐릭터 가이드
    char_guide = {
        "LOW":    "Main: Max Bullhorn (hero). No villain needed. Tone: optimistic, victorious.",
        "MEDIUM": "Main: Max Bullhorn vs Baron Bearsworth. Tense standoff. Uncertain outcome.",
        "HIGH":   "Main: Max + Baron face The Volatician together. Crisis mode. Warning tone.",
    }.get(risk_level, "Main: Max Bullhorn.")

    cut_count = 4 if comic_type == "daily" else 8

    return f"""## Today's Comic Brief
- Date: {date.today()}
- Episode: #{episode_no}
- Type: {comic_type} ({cut_count} cuts)
- Risk Level: {risk_level}
- Character Guide: {char_guide}

## Market Data
- VIX: {market_data.get('vix', 'N/A')}
- S&P500 Change: {market_data.get('sp500', 'N/A')}%
- US10Y: {market_data.get('us10y', 'N/A')}%
- Top ETF move: {market_data.get('top_etf', 'N/A')}

{prev_summary}

Generate a {cut_count}-cut comic story. Output JSON only."""


def generate_story(
    risk_level: str,
    comic_type: str,
    market_data: dict,
    episode_no: int,
    recent_episodes: list[dict]
) -> dict:
    """
    Claude API로 스토리 생성

    Returns:
        {
          "title": str,
          "caption": str,
          "context_summary": str,
          "cuts": [{"cut_number", "scene", "dialogue", "image_prompt", "mood"}]
        }
    Raises:
        ValueError: JSON 파싱 실패 시
        anthropic.APIError: API 오류 시
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_prompt = _build_user_prompt(
        risk_level, comic_type, market_data, episode_no, recent_episodes
    )

    logger.info(f"[Story] Claude 스토리 생성 시작 — Ep.{episode_no}, {risk_level}, {comic_type}")

    # retry 2회
    for attempt in range(1, 3):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}]
            )
            raw = response.content[0].text.strip()

            # JSON 파싱 (마크다운 코드블록 방어)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            story = json.loads(raw)

            # 필수 키 검증
            _validate_story(story, comic_type)

            logger.info(f"[Story] 생성 완료 — '{story['title']}'")
            return story

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[Story] 파싱 실패 (시도 {attempt}/2): {e}")
            if attempt == 2:
                raise ValueError(f"Claude 스토리 JSON 파싱 최종 실패: {e}")

    raise ValueError("generate_story: 예외 흐름 도달 (비정상)")


def _validate_story(story: dict, comic_type: str) -> None:
    """스토리 JSON 구조 검증"""
    required_keys = {"title", "caption", "context_summary", "cuts"}
    missing = required_keys - set(story.keys())
    if missing:
        raise KeyError(f"스토리 필수 키 누락: {missing}")

    expected_cuts = 4 if comic_type == "daily" else 8
    if len(story["cuts"]) < expected_cuts:
        raise ValueError(
            f"컷 수 부족: expected={expected_cuts}, got={len(story['cuts'])}"
        )

    for cut in story["cuts"]:
        cut_required = {"cut_number", "scene", "dialogue", "image_prompt", "mood"}
        cut_missing = cut_required - set(cut.keys())
        if cut_missing:
            raise KeyError(f"컷 필수 키 누락: {cut_missing}")
