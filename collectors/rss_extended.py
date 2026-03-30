"""
collectors/rss_extended.py
===========================
Reddit 대체 — 다중 무료 RSS 소스 기반 감성 분석.

v1.5.0 설계:
  - 소스별 독립 fetch (한 소스 장애 → 나머지 계속)
  - User-Agent 헤더 설정 (CNBC/MarketWatch 차단 방지)
  - SHA256 헤드라인 dedup (동일 기사 중복 집계 방지)
  - 부정어 처리 ("no rally", "not bullish" → bearish 판단)
  - 소스별 weight 기반 가중 평균 감성 집계
  - 단일 진입점: collect_extended_sentiment()
"""
import hashlib
import logging
import socket
import urllib.request
from typing import List, Dict, Optional

from config.settings import RSS_SOURCES, RSS_USER_AGENT
from config.settings import SENTIMENT_BULLISH_THRESHOLD, SENTIMENT_BEARISH_THRESHOLD

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 1. 감성 키워드 사전 (부정어 포함 설계)
# ──────────────────────────────────────────────────────────────

# 강세 키워드 (가중치: 강도별 분류)
_BULLISH_STRONG = [
    "surges", "soars", "record high", "all-time high", "breakout",
    "blowout earnings", "massive rally", "explosive growth",
]
_BULLISH_NORMAL = [
    "rally", "gain", "gains", "rises", "bullish", "optimism",
    "recovery", "rebound", "upgrade", "outperform", "beat estimates",
    "strong earnings", "better than expected", "growth", "boosts",
    "jumps", "climbs", "advances", "positive outlook",
]

# 약세 키워드
_BEARISH_STRONG = [
    "crash", "collapse", "plummets", "freefall", "meltdown",
    "crisis deepens", "worst since", "historic drop", "panic selling",
]
_BEARISH_NORMAL = [
    "recession", "selloff", "sell-off", "plunge", "fear", "panic",
    "downgrade", "miss estimates", "below expectations", "slump",
    "falls", "drops", "declines", "bearish", "concern", "worry",
    "inflation surge", "rate hike", "tightening", "stagflation",
    "default", "debt ceiling", "warning", "caution", "risk",
]

# 부정어 수식어 (바로 앞에 오면 극성 반전)
_NEGATION_WORDS = [
    "no ", "not ", "never ", "without ", "despite ", "fails to ",
    "unable to ", "no sign of ", "not yet ",
]


def _score_headline(headline: str) -> float:
    """
    헤드라인 감성 점수 산출.
    - 강한 키워드: ±2.0
    - 일반 키워드: ±1.0
    - 부정어 + 강세 키워드: -1.0 (반전)
    - 부정어 + 약세 키워드: +0.5 (약한 반전)
    """
    text = headline.lower()
    score = 0.0

    # 강세 체크 (부정어 선처리)
    for kw in _BULLISH_STRONG:
        if kw in text:
            negated = any(neg + kw in text for neg in _NEGATION_WORDS)
            score += -1.5 if negated else 2.0

    for kw in _BULLISH_NORMAL:
        if kw in text:
            negated = any(neg + kw in text for neg in _NEGATION_WORDS)
            score += -0.5 if negated else 1.0

    # 약세 체크
    for kw in _BEARISH_STRONG:
        if kw in text:
            negated = any(neg + kw in text for neg in _NEGATION_WORDS)
            score += 1.0 if negated else -2.0

    for kw in _BEARISH_NORMAL:
        if kw in text:
            negated = any(neg + kw in text for neg in _NEGATION_WORDS)
            score += 0.5 if negated else -1.0

    # -3 ~ +3 클리핑
    return max(-3.0, min(3.0, score))


# ──────────────────────────────────────────────────────────────
# 2. RSS Fetch (feedparser 없이 stdlib urllib 사용 가능하도록 설계)
# ──────────────────────────────────────────────────────────────

def _fetch_feed_raw(url: str, timeout: int) -> Optional[str]:
    """
    URL에서 RSS XML raw 텍스트 수집.
    feedparser 우선, 없으면 urllib fallback.
    """
    try:
        import feedparser
        _feedparser_available = True
    except ImportError:
        _feedparser_available = False

    if _feedparser_available:
        try:
            import feedparser
            # feedparser에 User-Agent 전달
            feed = feedparser.parse(
                url,
                agent=RSS_USER_AGENT,
                request_headers={"User-Agent": RSS_USER_AGENT},
            )
            if feed and feed.entries:
                return feed  # feedparser 객체 반환
            return None
        except Exception as e:
            logger.debug(f"[RSS-EXT] feedparser 실패 {url[:40]}: {e}")
            return None
    else:
        # stdlib urllib fallback
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RSS_USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug(f"[RSS-EXT] urllib 실패 {url[:40]}: {e}")
            return None


def _extract_titles_from_feed(feed_obj) -> List[str]:
    """feedparser 결과 또는 raw XML에서 제목 추출"""
    titles = []
    # feedparser 객체인 경우
    if hasattr(feed_obj, "entries"):
        for entry in feed_obj.entries:
            title = entry.get("title", "").strip()
            if title:
                titles.append(title)
        return titles

    # raw XML fallback (정규식 없이 간단 파싱)
    if isinstance(feed_obj, str):
        import re
        found = re.findall(r"<title[^>]*>(.*?)</title>", feed_obj, re.DOTALL)
        # 첫 번째는 채널 제목이므로 제외
        for raw in found[1:]:
            # CDATA 제거
            clean = re.sub(r"<!\[CDATA\[|\]\]>", "", raw).strip()
            if clean and len(clean) > 5:
                titles.append(clean)
    return titles


def _fetch_source(source: dict) -> Dict:
    """
    단일 RSS 소스 수집.
    Returns: {name, headlines, count, success}
    """
    name = source["name"]
    url = source["url"]
    max_items = source.get("max_items", 10)
    timeout = source.get("timeout_sec", 8)

    try:
        feed_obj = _fetch_feed_raw(url, timeout)
        if feed_obj is None:
            logger.warning(f"[RSS-EXT] 수집 실패 — {name}")
            return {"name": name, "headlines": [], "count": 0, "success": False}

        titles = _extract_titles_from_feed(feed_obj)[:max_items]
        logger.debug(f"[RSS-EXT] {name}: {len(titles)}건")
        return {"name": name, "headlines": titles, "count": len(titles), "success": True}

    except (socket.timeout, OSError) as e:
        logger.warning(f"[RSS-EXT] 네트워크 오류 — {name}: {e}")
        return {"name": name, "headlines": [], "count": 0, "success": False}
    except Exception as e:
        logger.warning(f"[RSS-EXT] 예외 — {name}: {e}")
        return {"name": name, "headlines": [], "count": 0, "success": False}


# ──────────────────────────────────────────────────────────────
# 3. 중복 제거
# ──────────────────────────────────────────────────────────────

def _dedup_headlines(headlines: List[str]) -> List[str]:
    """
    SHA256 앞 16자 기준 중복 헤드라인 제거.
    동일 기사가 여러 소스에 나오는 경우 첫 번째만 유지.
    """
    seen = set()
    unique = []
    for h in headlines:
        key = hashlib.sha256(h.lower().strip().encode()).hexdigest()[:16]
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


# ──────────────────────────────────────────────────────────────
# 4. 가중 감성 집계
# ──────────────────────────────────────────────────────────────

def _aggregate_sentiment(
    source_results: List[dict],
    source_configs: List[dict],
) -> dict:
    """
    소스별 weight 기반 가중 감성 점수 집계.
    weight_map: {source_name: weight}
    """
    weight_map = {s["name"]: s.get("weight", 1.0) for s in source_configs}

    all_scores = []
    weighted_scores = []
    source_summary = []

    for result in source_results:
        if not result["success"] or not result["headlines"]:
            continue

        name = result["name"]
        weight = weight_map.get(name, 1.0)
        headlines = result["headlines"]

        scores = [_score_headline(h) for h in headlines]
        src_score = sum(scores) / len(scores) if scores else 0.0
        weighted_score = src_score * weight

        all_scores.extend(scores)
        weighted_scores.append(weighted_score)

        bullish_cnt = sum(1 for s in scores if s > 0)
        bearish_cnt = sum(1 for s in scores if s < 0)
        neutral_cnt = sum(1 for s in scores if s == 0)

        source_summary.append({
            "name": name,
            "count": len(headlines),
            "bullish": bullish_cnt,
            "bearish": bearish_cnt,
            "neutral": neutral_cnt,
            "score": round(src_score, 2),
            "weighted_score": round(weighted_score, 2),
        })

        logger.debug(
            f"[RSS-EXT] {name}: score={src_score:.2f} "
            f"(강세{bullish_cnt}/약세{bearish_cnt}/중립{neutral_cnt})"
        )

    # 최종 가중 평균
    net_weighted = sum(weighted_scores) / len(weighted_scores) if weighted_scores else 0.0

    if net_weighted >= SENTIMENT_BULLISH_THRESHOLD:
        sentiment = "Bullish"
    elif net_weighted <= SENTIMENT_BEARISH_THRESHOLD:
        sentiment = "Bearish"
    else:
        sentiment = "Neutral"

    bullish_total = sum(1 for s in all_scores if s > 0)
    bearish_total = sum(1 for s in all_scores if s < 0)
    neutral_total = sum(1 for s in all_scores if s == 0)

    return {
        "news_sentiment": sentiment,
        "net_weighted_score": round(net_weighted, 3),
        "total_headlines": len(all_scores),
        "bullish_count": bullish_total,
        "bearish_count": bearish_total,
        "neutral_count": neutral_total,
        "sources_ok": len(weighted_scores),
        "sources_fail": len(source_results) - len(weighted_scores),
        "source_detail": source_summary,
    }


# ──────────────────────────────────────────────────────────────
# 5. 통합 진입점
# ──────────────────────────────────────────────────────────────

def collect_extended_sentiment() -> dict:
    """
    다중 RSS 소스에서 금융 뉴스 감성 수집 및 집계.
    - 소스 개수: settings.RSS_SOURCES 기준 (현재 9개)
    - 부정어 처리 / dedup / 가중 평균 적용
    - 모든 소스 실패 시 Neutral 반환 (시스템 중단 없음)

    Returns:
        {
            news_sentiment: "Bullish" | "Neutral" | "Bearish",
            net_weighted_score: float,
            total_headlines: int,
            bullish_count: int,
            bearish_count: int,
            neutral_count: int,
            sources_ok: int,
            sources_fail: int,
            source_detail: list,
        }
    """
    logger.info(f"[RSS-EXT] 다중 RSS 수집 시작 ({len(RSS_SOURCES)}개 소스)")

    # 소스별 순차 수집 (네트워크 제한 환경 고려 — 병렬화 불필요)
    results = []
    for source in RSS_SOURCES:
        result = _fetch_source(source)
        results.append(result)

    # 전체 헤드라인 dedup (소스 간 중복 제거)
    all_headlines_flat = []
    for r in results:
        all_headlines_flat.extend(r.get("headlines", []))

    unique_headlines = _dedup_headlines(all_headlines_flat)
    dedup_removed = len(all_headlines_flat) - len(unique_headlines)
    if dedup_removed > 0:
        logger.info(f"[RSS-EXT] 중복 헤드라인 제거: {dedup_removed}건")

    # 감성 집계
    sentiment_result = _aggregate_sentiment(results, RSS_SOURCES)

    ok = sentiment_result["sources_ok"]
    fail = sentiment_result["sources_fail"]
    sentiment = sentiment_result["news_sentiment"]
    score = sentiment_result["net_weighted_score"]

    logger.info(
        f"[RSS-EXT] 완료: {sentiment} (가중점수={score:+.2f}) | "
        f"소스 {ok}성공/{fail}실패 | "
        f"헤드라인 {sentiment_result['total_headlines']}건(중복제거 후)"
    )

    if ok == 0:
        logger.warning("[RSS-EXT] 모든 소스 실패 — Neutral 반환")
        return {
            "news_sentiment": "Neutral",
            "net_weighted_score": 0.0,
            "total_headlines": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "sources_ok": 0,
            "sources_fail": len(RSS_SOURCES),
            "source_detail": [],
            "headlines": [],
        }

    sentiment_result["headlines"] = unique_headlines[:10]  # 상위 10개 보관
    return sentiment_result
