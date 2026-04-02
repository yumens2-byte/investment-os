"""
comic/story.py (B-18 + B-19)
================================
Investment Comic v3.0 — 스토리 생성 엔진

v3.0 변경사항:
  - LLM 우선순위: Gemini Main → Gemini Sub → Claude fallback
  - 19개 시그널 + B-16 뉴스 분석 + 레짐 정보를 프롬프트에 반영 (B-19)
  - 기존 캐릭터/포맷/출력 구조 완전 호환
"""

import os
import json
import logging
from datetime import date

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────

CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_TOKENS_DAILY  = 2000
MAX_TOKENS_WEEKLY = 4000

SYSTEM_PROMPT = """You are the writer of "Investment Comic", a daily financial webcomic.

## Characters (DO NOT change names or reference real IPs)
- **Max Bullhorn**: Heroic golden bull warrior. Optimistic, inspiring, protects rising markets.
- **Baron Bearsworth**: Villainous dark bear. Sinister, calculating, thrives on market fear.
- **The Volatician**: Chaos wizard. Appears only in HIGH risk. Unpredictable, cryptic.

## Rules
1. Output ONLY valid JSON. No markdown, no explanation, no code blocks.
1-a. caption MUST follow this 4-part structure for BOTH daily and weekly:
     Line1: Hook (emoji + title)
     Line2-3: Story summary 1~2 lines
     Line4: 🐂BUY→하트 / ⚖️HOLD→댓글 / 🐻SELL→리트윗
     Line5: #InvestmentComic #투자코믹 #미국증시 #미국주식 #ETF투자 #지금팔아야할까 #폭락오나 #주식전쟁 #버틸까팔까 #개미투자자
     Total MUST be under 280 characters.
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
  "caption": "X post caption — 4단 구조",
  "context_summary": "1-2 sentence summary for next episode continuity",
  "cuts": [
    {
      "cut_number": 1,
      "scene": "Scene description",
      "dialogue": "Character dialogue",
      "image_prompt": "English cinematic prompt (no character names)",
      "mood": "tense | hopeful | dramatic | chaotic | calm"
    }
  ]
}"""


# ──────────────────────────────────────────────────────────────
# 1. 프롬프트 빌더 (B-19: Investment OS 데이터 연동)
# ──────────────────────────────────────────────────────────────

def _build_user_prompt(
    risk_level: str,
    comic_type: str,
    market_data: dict,
    episode_no: int,
    recent_episodes: list[dict],
    core_data: dict = None,
) -> str:
    """유저 프롬프트 조립 — B-19: core_data 시그널/뉴스 반영"""

    prev_summary = ""
    if recent_episodes:
        lines = []
        for ep in recent_episodes:
            lines.append(
                f"  - Ep.{ep['episode_no']} [{ep['risk_level']}] {ep['title']}: {ep.get('summary', '')}"
            )
        prev_summary = "## Previous Episodes (for continuity)\n" + "\n".join(lines)

    cut_count = 4 if comic_type == "daily" else 8
    market_context = _build_market_context(market_data, core_data)
    char_guide = _build_character_guide(risk_level, core_data)

    return f"""## Today's Comic Brief
- Date: {date.today()}
- Episode: #{episode_no}
- Type: {comic_type} ({cut_count} cuts)
- Risk Level: {risk_level}
- Character Guide: {char_guide}

{market_context}

{prev_summary}

Generate a {cut_count}-cut comic story. Output JSON only."""


def _build_market_context(market_data: dict, core_data: dict = None) -> str:
    """B-19: core_data에서 시장 컨텍스트 추출"""

    basic = f"""## Market Data
- VIX: {market_data.get('vix', 'N/A')}
- S&P500 Change: {market_data.get('sp500', 'N/A')}%
- US10Y: {market_data.get('us10y', 'N/A')}%
- Top ETF move: {market_data.get('top_etf', 'N/A')}"""

    if not core_data:
        return basic

    regime = core_data.get("market_regime", {})
    signals = core_data.get("signals", {})
    ts = core_data.get("trading_signal", {})
    alloc = core_data.get("etf_allocation", {}).get("allocation", {})
    snapshot = core_data.get("market_snapshot", {})
    news = core_data.get("news_analysis", {})

    rank = core_data.get("etf_analysis", {}).get("etf_rank", {})
    top_etf = min(rank, key=rank.get) if rank else "N/A"
    worst_etf = max(rank, key=rank.get) if rank else "N/A"

    sp500_val = snapshot.get('sp500', market_data.get('sp500', 0))
    try:
        sp500_str = f"{float(sp500_val):+.1f}%"
    except (TypeError, ValueError):
        sp500_str = str(sp500_val)

    context = f"""## Market Data (Investment OS — 19 Signals)
- Regime: {regime.get('market_regime', 'N/A')}
- Risk Level: {regime.get('market_risk_level', 'N/A')}
- Regime Reason: {regime.get('regime_reason', 'N/A')}
- VIX: {snapshot.get('vix', market_data.get('vix', 'N/A'))}
- S&P500: {sp500_str}
- Oil: ${snapshot.get('oil', 'N/A')}
- Dollar: {snapshot.get('dollar_index', 'N/A')}
- F&G Index: {signals.get('fear_greed_state', 'N/A')} (score={signals.get('fear_greed_score', '?')})
- Trading Signal: {ts.get('trading_signal', 'N/A')}
- Top ETF: {top_etf} ({alloc.get(top_etf, '?')}%)
- Worst ETF: {worst_etf} ({alloc.get(worst_etf, '?')}%)
- BUY Watch: {', '.join(ts.get('signal_matrix', {}).get('buy_watch', []))}
- Reduce: {', '.join(ts.get('signal_matrix', {}).get('reduce', []))}"""

    top_issues = news.get("top_issues", [])
    if top_issues:
        issues_text = "\n".join(
            f"  - {iss.get('topic', '?')} ({iss.get('impact', '?')}): {iss.get('summary', '')}"
            for iss in top_issues[:3]
        )
        key_risk = news.get("key_risk", "")
        context += f"""

## Key News Issues (AI Analysis)
{issues_text}
  Key Risk: {key_risk}

Use these news issues as story material — weave them into the character conflict."""

    return context


def _build_character_guide(risk_level: str, core_data: dict = None) -> str:
    """B-19: 레짐 기반 캐릭터 가이드 확장"""

    base_guide = {
        "LOW":    "Main: Max Bullhorn (hero). No villain needed. Tone: optimistic, victorious.",
        "MEDIUM": "Main: Max Bullhorn vs Baron Bearsworth. Tense standoff. Uncertain outcome.",
        "HIGH":   "Main: Max + Baron face The Volatician together. Crisis mode. Warning tone.",
    }.get(risk_level, "Main: Max Bullhorn.")

    if not core_data:
        return base_guide

    regime = core_data.get("market_regime", {}).get("market_regime", "")

    regime_flavor = {
        "Oil Shock": "Baron uses oil surge as a weapon. Dark energy from commodity markets.",
        "Risk-Off": "Baron dominates the scene. Max is on the defensive. Safety assets glow.",
        "Transition": "Both Max and Baron circle each other warily. Neither has the upper hand.",
        "Risk-On": "Max charges forward with momentum. Baron retreats to shadows.",
        "Stagflation Risk": "The Volatician corrupts both Bull and Bear. Everything is uncertain.",
    }.get(regime, "")

    if regime_flavor:
        return f"{base_guide}\n  Regime flavor: {regime_flavor}"
    return base_guide


# ──────────────────────────────────────────────────────────────
# 2. 스토리 생성 (B-18: Gemini → Gemini Sub → Claude)
# ──────────────────────────────────────────────────────────────

def generate_story(
    risk_level: str,
    comic_type: str,
    market_data: dict,
    episode_no: int,
    recent_episodes: list[dict],
    core_data: dict = None,
) -> dict:
    """
    스토리 생성 — Gemini Main → Gemini Sub → Claude fallback

    Returns:
        {title, caption, context_summary, cuts: [{cut_number, scene, dialogue, image_prompt, mood}]}
    """
    user_prompt = _build_user_prompt(
        risk_level, comic_type, market_data,
        episode_no, recent_episodes, core_data,
    )

    max_tokens = MAX_TOKENS_DAILY if comic_type == "daily" else MAX_TOKENS_WEEKLY

    # ── 1차: Gemini 시도 (Main → Sub 자동 전환) ──
    story = _try_gemini(user_prompt, max_tokens, comic_type, episode_no)
    if story:
        return story

    # ── 2차: Claude fallback ──
    story = _try_claude(user_prompt, max_tokens, comic_type, episode_no)
    if story:
        return story

    raise ValueError(f"generate_story: Gemini + Claude 모두 실패 — Ep.{episode_no}")


def _try_gemini(user_prompt, max_tokens, comic_type, episode_no):
    """Gemini로 스토리 생성 시도 (Main → Sub 자동 전환)"""
    try:
        from core.gemini_gateway import call, is_available
        if not is_available():
            logger.info("[Story] Gemini 미설정 → Claude fallback")
            return None

        logger.info(f"[Story] Gemini 스토리 생성 시도 — Ep.{episode_no}")

        result = call(
            prompt=user_prompt,
            model="flash",
            system_instruction=SYSTEM_PROMPT,
            max_tokens=max_tokens,
            temperature=0.8,
            response_json=True,
        )

        if result["success"] and result.get("data"):
            story = result["data"]
            _validate_story(story, comic_type)
            logger.info(
                f"[Story] Gemini 생성 완료 — '{story['title']}' "
                f"(key={result['key_used']})"
            )
            return story

        if result["success"] and result.get("text"):
            story = _parse_raw_json(result["text"])
            if story:
                _validate_story(story, comic_type)
                logger.info(f"[Story] Gemini 텍스트 파싱 성공 — '{story['title']}'")
                return story

        logger.warning(f"[Story] Gemini 실패: {result.get('error', '?')[:80]}")
        return None

    except Exception as e:
        logger.warning(f"[Story] Gemini 예외: {e}")
        return None


def _try_claude(user_prompt, max_tokens, comic_type, episode_no):
    """Claude API로 스토리 생성 (최종 fallback)"""
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("[Story] ANTHROPIC_API_KEY 미설정 — Claude 불가")
            return None

        client = anthropic.Anthropic(api_key=api_key)
        logger.info(f"[Story] Claude fallback 시도 — Ep.{episode_no}")

        for attempt in range(1, 3):
            try:
                response = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                raw = response.content[0].text.strip()
                story = _parse_raw_json(raw)
                if story:
                    _validate_story(story, comic_type)
                    logger.info(f"[Story] Claude 생성 완료 — '{story['title']}'")
                    return story
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"[Story] Claude 파싱 실패 (시도 {attempt}/2): {e}")

        return None

    except Exception as e:
        logger.warning(f"[Story] Claude 예외: {e}")
        return None


def _parse_raw_json(raw):
    """LLM 응답에서 JSON 추출"""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        return None


# ──────────────────────────────────────────────────────────────
# 3. 검증
# ──────────────────────────────────────────────────────────────

def _validate_story(story, comic_type):
    """스토리 필수 키 + 구조 검증"""
    required = {"title", "caption", "context_summary", "cuts"}
    missing = required - set(story.keys())
    if missing:
        raise ValueError(f"필수 키 누락: {missing}")

    expected_cuts = 4 if comic_type == "daily" else 8
    actual_cuts = len(story.get("cuts", []))
    if actual_cuts < expected_cuts:
        raise ValueError(f"컷 부족: {actual_cuts}/{expected_cuts}")

    for i, cut in enumerate(story["cuts"], 1):
        for key in ["cut_number", "scene", "dialogue", "image_prompt"]:
            if key not in cut:
                raise ValueError(f"Cut #{i} 키 누락: {key}")
