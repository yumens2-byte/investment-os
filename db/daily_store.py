"""
db/daily_store.py
================================
일별 시장 데이터 Supabase 적재 모듈

적재 대상:
  - daily_snapshots:  시장 스냅샷 (Yahoo + FRED + F&G)
  - daily_analysis:   분석 결과 (레짐/시그널/ETF)
  - daily_news:       뉴스 분석 (RSS + Gemini)
  - daily_alerts:     Alert 발동 이력

호출 시점:
  - run_market.py Step 8 직후 (snapshots + analysis + news)
  - run_alert.py 발행 완료 시 (alerts)
"""
import json
import logging
from datetime import date, datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _get_client():
    """Supabase 클라이언트 가져오기"""
    from db.supabase_client import get_client
    return get_client()


def _today_kst() -> str:
    """오늘 날짜 (KST) ISO 형식"""
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    return kst.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────────
# 1. daily_snapshots — 시장 스냅샷 적재
# ──────────────────────────────────────────────────────────────

def store_daily_snapshot(data: dict) -> bool:
    """
    core_data에서 시장 스냅샷 추출 → daily_snapshots UPSERT

    Args:
        data: assemble_core_data() 반환값
    """
    try:
        snapshot = data.get("market_snapshot", {})
        macro = data.get("macro_data", {})
        fg = data.get("fear_greed", {})
        fx = data.get("fx_rates", {})
        crypto = data.get("crypto", {})

        row = {
            "snapshot_date": _today_kst(),
            "spy_change": _safe_float(snapshot.get("sp500")),
            "vix": _safe_float(snapshot.get("vix")),
            "oil_wti": _safe_float(snapshot.get("oil")),
            "us10y": _safe_float(snapshot.get("us10y")),
            "nasdaq_change": _safe_float(snapshot.get("nasdaq")),
            "dollar_index": _safe_float(snapshot.get("dollar_index")),
            "usdkrw": _safe_float(fx.get("usdkrw")),
            "fear_greed": _safe_int(fg.get("value")),
            "fear_greed_label": fg.get("label", ""),
            "btc_usd": _safe_float(crypto.get("btc_usd")),
            "fed_funds_rate": _safe_float(macro.get("fed_funds_rate")),
            "hy_spread": _safe_float(macro.get("hy_spread")),
            "yield_curve": _safe_float(macro.get("yield_curve")),
        }

        _get_client().table("daily_snapshots").upsert(
            row, on_conflict="snapshot_date"
        ).execute()

        logger.info(f"[DailyStore] 스냅샷 저장 완료: {row['snapshot_date']}")
        return True

    except Exception as e:
        logger.warning(f"[DailyStore] 스냅샷 저장 실패 (무시): {e}")
        return False


# ──────────────────────────────────────────────────────────────
# 2. daily_analysis — 분석 결과 적재
# ──────────────────────────────────────────────────────────────

def store_daily_analysis(data: dict, regime_score: int = 0) -> bool:
    """
    core_data에서 분석 결과 추출 → daily_analysis UPSERT

    Args:
        data: assemble_core_data() 반환값
        regime_score: RegimeEngine 점수
    """
    try:
        regime = data.get("market_regime", {})
        signal = data.get("trading_signal", {})
        allocation = data.get("etf_allocation", {})
        score = data.get("market_score", {})
        risk_data = data.get("portfolio_risk", {})

        # ETF 순위 추출 (allocation 비율 기준 내림차순)
        etf_rank = {}
        if allocation:
            sorted_etfs = sorted(allocation.items(), key=lambda x: x[1], reverse=True)
            for rank, (etf, _) in enumerate(sorted_etfs, 1):
                etf_rank[etf] = rank

        # BUY/REDUCE 리스트 추출
        buy_watch = []
        reduce_list = []
        helpers = data.get("output_helpers", {})
        if helpers:
            buy_watch = helpers.get("buy_watch", [])
            reduce_list = helpers.get("reduce", [])

        row = {
            "analysis_date": _today_kst(),
            "regime": regime.get("market_regime", "Unknown"),
            "risk_level": regime.get("market_risk_level", "MEDIUM"),
            "trading_signal": signal.get("signal", "HOLD"),
            "regime_score": regime_score,
            "etf_rank": json.dumps(etf_rank, ensure_ascii=False),
            "etf_allocation": json.dumps(allocation, ensure_ascii=False),
            "market_score": json.dumps(score, ensure_ascii=False),
            "buy_watch": buy_watch if buy_watch else [],
            "reduce_list": reduce_list if reduce_list else [],
        }

        _get_client().table("daily_analysis").upsert(
            row, on_conflict="analysis_date"
        ).execute()

        logger.info(
            f"[DailyStore] 분석 저장 완료: {row['analysis_date']} | "
            f"레짐={row['regime']} 시그널={row['trading_signal']}"
        )
        return True

    except Exception as e:
        logger.warning(f"[DailyStore] 분석 저장 실패 (무시): {e}")
        return False


# ──────────────────────────────────────────────────────────────
# 3. daily_news — 뉴스 분석 적재
# ──────────────────────────────────────────────────────────────

def store_daily_news(data: dict, rss_result: dict = None) -> bool:
    """
    core_data + RSS 결과에서 뉴스 분석 추출 → daily_news UPSERT

    Args:
        data: assemble_core_data() 반환값
        rss_result: collect_news_sentiment() 반환값
    """
    try:
        news_summary = data.get("news_summary", {})
        news_analysis = data.get("news_analysis", {})

        # RSS 감성
        rss_sentiment = news_summary.get("sentiment", "Neutral")
        rss_score = _safe_float(news_summary.get("weighted_score", 0))
        rss_count = news_summary.get("headline_count", 0)

        # Gemini 분석
        gemini_sentiment = news_analysis.get("overall_sentiment", "")
        top_issues = news_analysis.get("top_issues", [])
        key_risk = news_analysis.get("key_risk", "")

        # 주요 헤드라인 (상위 10건)
        top_headlines = []
        if rss_result and "headlines" in rss_result:
            top_headlines = rss_result["headlines"][:10]
        elif data.get("output_helpers", {}).get("top_headlines"):
            top_headlines = data["output_helpers"]["top_headlines"]

        row = {
            "news_date": _today_kst(),
            "rss_sentiment": rss_sentiment,
            "rss_score": rss_score,
            "rss_headline_count": rss_count,
            "gemini_sentiment": gemini_sentiment,
            "top_issues": json.dumps(top_issues, ensure_ascii=False),
            "key_risk": key_risk,
            "top_headlines": json.dumps(top_headlines, ensure_ascii=False),
        }

        _get_client().table("daily_news").upsert(
            row, on_conflict="news_date"
        ).execute()

        logger.info(
            f"[DailyStore] 뉴스 저장 완료: {row['news_date']} | "
            f"RSS={rss_sentiment} Gemini={gemini_sentiment}"
        )
        return True

    except Exception as e:
        logger.warning(f"[DailyStore] 뉴스 저장 실패 (무시): {e}")
        return False


# ──────────────────────────────────────────────────────────────
# 4. daily_alerts — Alert 발동 이력 적재
# ──────────────────────────────────────────────────────────────

def store_daily_alert(
    alert_type: str,
    alert_level: str,
    trigger_value: str = "",
    tweet_id: str = "",
) -> bool:
    """
    Alert 발동 시 daily_alerts INSERT

    Args:
        alert_type: VIX/OIL/SPY/CRISIS/REGIME_CHANGE/ETF_RANK
        alert_level: L1/L2/L3
        trigger_value: 트리거 값 ("WTI $111.5")
        tweet_id: X 발행 ID
    """
    try:
        row = {
            "alert_date": _today_kst(),
            "alert_type": alert_type,
            "alert_level": alert_level,
            "trigger_value": trigger_value,
            "tweet_id": tweet_id,
        }

        _get_client().table("daily_alerts").insert(row).execute()

        logger.info(
            f"[DailyStore] Alert 저장 완료: {alert_type}/{alert_level} "
            f"({trigger_value})"
        )
        return True

    except Exception as e:
        logger.warning(f"[DailyStore] Alert 저장 실패 (무시): {e}")
        return False


# ──────────────────────────────────────────────────────────────
# 5. 통합 적재 함수 (run_market.py에서 1회 호출)
# ──────────────────────────────────────────────────────────────

def store_all_daily_data(
    data: dict,
    regime_score: int = 0,
    rss_result: dict = None,
) -> dict:
    """
    run_market.py Step 8 직후 호출 — 3개 테이블 일괄 적재

    Args:
        data: assemble_core_data() 반환값
        regime_score: RegimeEngine 점수
        rss_result: collect_news_sentiment() 반환값

    Returns:
        {"snapshot": bool, "analysis": bool, "news": bool}
    """
    results = {
        "snapshot": store_daily_snapshot(data),
        "analysis": store_daily_analysis(data, regime_score),
        "news": store_daily_news(data, rss_result),
    }

    ok = sum(1 for v in results.values() if v)
    logger.info(f"[DailyStore] 일괄 적재 완료: {ok}/3 성공")
    return results


# ──────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
