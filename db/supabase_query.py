"""
db/supabase_query.py (G-1)
===================================
Supabase 히스토리 조회 모듈 — 축적 데이터 활용.

조회 함수 6개:
  1. get_regime_streak()    — 현재 레짐 연속 일수
  2. get_snapshot_delta()   — N일 전 대비 시장 데이터 변화
  3. get_etf_trend()        — 최근 N일 ETF 배분 추이
  4. get_score_trend()      — 최근 N일 Market Score 추이
  5. get_recent_alerts()    — 최근 N일 Alert 이력
  6. get_regime_history()   — 최근 N일 레짐 전환 이력

VERSION = "1.0.0"
RPD: +0 (SQL만, AI 미사용)
"""
import json
import logging
from datetime import date, datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def _get_client():
    """Supabase 클라이언트"""
    from db.supabase_client import get_client
    return get_client()


def _today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────────
# 1. get_regime_streak — 현재 레짐 연속 일수
# ──────────────────────────────────────────────────────────────

def get_regime_streak() -> dict:
    """
    현재 레짐이 며칠째 지속 중인지 조회.
    daily_analysis에서 최신부터 역순으로 같은 regime 유지 일수 계산.

    Returns:
        {
          "regime": "Oil Shock",
          "streak_days": 3,
          "since_date": "2026-04-02",
          "prev_regime": "Risk-Off",
        }
    """
    try:
        client = _get_client()
        result = client.table("daily_analysis") \
            .select("analysis_date, regime") \
            .order("analysis_date", desc=True) \
            .limit(30) \
            .execute()

        if not result.data or len(result.data) == 0:
            return {"regime": "Unknown", "streak_days": 0, "since_date": "", "prev_regime": ""}

        rows = result.data
        current_regime = rows[0].get("regime", "Unknown")
        streak = 1
        since_date = rows[0].get("analysis_date", "")
        prev_regime = ""

        for i in range(1, len(rows)):
            if rows[i].get("regime") == current_regime:
                streak += 1
                since_date = rows[i].get("analysis_date", since_date)
            else:
                prev_regime = rows[i].get("regime", "")
                break

        logger.info(f"[SupaQuery] 레짐 streak: {current_regime} {streak}일 (since {since_date})")
        return {
            "regime": current_regime,
            "streak_days": streak,
            "since_date": since_date,
            "prev_regime": prev_regime,
        }

    except Exception as e:
        logger.warning(f"[SupaQuery] regime streak 조회 실패: {e}")
        return {"regime": "Unknown", "streak_days": 0, "since_date": "", "prev_regime": ""}


# ──────────────────────────────────────────────────────────────
# 2. get_snapshot_delta — N일 전 대비 변화
# ──────────────────────────────────────────────────────────────

def get_snapshot_delta(days: int = 1) -> dict:
    """
    N일 전 대비 시장 데이터 변화량 계산.

    Returns:
        {
          "vix": {"current": 23.87, "prev": 31.05, "delta": -7.18},
          "oil_wti": {"current": 111.54, "prev": 99.64, "delta": 11.90},
          "fear_greed": {"current": 11, "prev": 9, "delta": 2},
          "spy_change": {"current": 0.11, "prev": -1.67, "delta": 1.78},
          "usdkrw": {"current": 1514.0, "prev": 1480.0, "delta": 34.0},
          "has_data": True,
        }
    """
    try:
        client = _get_client()
        result = client.table("daily_snapshots") \
            .select("*") \
            .order("snapshot_date", desc=True) \
            .limit(days + 1) \
            .execute()

        if not result.data or len(result.data) < 2:
            return {"has_data": False}

        current = result.data[0]
        prev = result.data[min(days, len(result.data) - 1)]

        fields = ["vix", "oil_wti", "fear_greed", "spy_change", "usdkrw",
                   "us10y", "dollar_index", "btc_usd", "nasdaq_change"]
        delta = {"has_data": True}

        for field in fields:
            c_val = current.get(field)
            p_val = prev.get(field)
            if c_val is not None and p_val is not None:
                try:
                    c = float(c_val)
                    p = float(p_val)
                    delta[field] = {
                        "current": c,
                        "prev": p,
                        "delta": round(c - p, 4),
                    }
                except (ValueError, TypeError):
                    pass

        logger.info(f"[SupaQuery] snapshot delta: {len(delta)-1}개 필드 ({days}일 전 대비)")
        return delta

    except Exception as e:
        logger.warning(f"[SupaQuery] snapshot delta 조회 실패: {e}")
        return {"has_data": False}


# ──────────────────────────────────────────────────────────────
# 3. get_etf_trend — 최근 N일 ETF 배분 추이
# ──────────────────────────────────────────────────────────────

def get_etf_trend(days: int = 5) -> dict:
    """
    최근 N일 ETF 배분 비중 추이.

    Returns:
        {
          "XLE": [25, 28, 30, 30, 30],
          "QQQM": [15, 10, 5, 5, 5],
          "dates": ["04-01", "04-02", "04-03", "04-04", "04-05"],
          "has_data": True,
        }
    """
    try:
        client = _get_client()
        result = client.table("daily_analysis") \
            .select("analysis_date, etf_allocation") \
            .order("analysis_date", desc=True) \
            .limit(days) \
            .execute()

        if not result.data or len(result.data) == 0:
            return {"has_data": False}

        # 역순 → 시간순
        rows = list(reversed(result.data))
        dates = []
        etf_data = {}

        for row in rows:
            dates.append(row.get("analysis_date", "")[-5:])  # "04-04"
            alloc_raw = row.get("etf_allocation", "{}")
            if isinstance(alloc_raw, str):
                alloc = json.loads(alloc_raw)
            else:
                alloc = alloc_raw or {}

            for etf, pct in alloc.items():
                if etf not in etf_data:
                    etf_data[etf] = []
                try:
                    etf_data[etf].append(int(pct))
                except (ValueError, TypeError):
                    etf_data[etf].append(0)

        etf_data["dates"] = dates
        etf_data["has_data"] = True

        logger.info(f"[SupaQuery] ETF trend: {len(dates)}일 데이터")
        return etf_data

    except Exception as e:
        logger.warning(f"[SupaQuery] ETF trend 조회 실패: {e}")
        return {"has_data": False}


# ──────────────────────────────────────────────────────────────
# 4. get_score_trend — 최근 N일 Market Score 추이
# ──────────────────────────────────────────────────────────────

def get_score_trend(days: int = 5) -> dict:
    """
    최근 N일 Market Score 6축 추이.

    Returns:
        {
          "growth": [2, 2, 2, 3, 2],
          "risk": [3, 3, 2, 2, 2],
          "commodity": [2, 3, 4, 4, 4],
          "dates": ["04-01", "04-02", "04-03", "04-04", "04-05"],
          "has_data": True,
        }
    """
    try:
        client = _get_client()
        result = client.table("daily_analysis") \
            .select("analysis_date, market_score") \
            .order("analysis_date", desc=True) \
            .limit(days) \
            .execute()

        if not result.data or len(result.data) == 0:
            return {"has_data": False}

        rows = list(reversed(result.data))
        dates = []
        score_keys = ["growth_score", "inflation_score", "liquidity_score",
                       "risk_score", "financial_stability_score", "commodity_pressure_score"]
        short_keys = ["growth", "inflation", "liquidity", "risk", "stability", "commodity"]
        scores = {k: [] for k in short_keys}

        for row in rows:
            dates.append(row.get("analysis_date", "")[-5:])
            ms_raw = row.get("market_score", "{}")
            if isinstance(ms_raw, str):
                ms = json.loads(ms_raw)
            else:
                ms = ms_raw or {}

            for sk, fk in zip(short_keys, score_keys):
                try:
                    scores[sk].append(int(ms.get(fk, 0)))
                except (ValueError, TypeError):
                    scores[sk].append(0)

        scores["dates"] = dates
        scores["has_data"] = True

        logger.info(f"[SupaQuery] Score trend: {len(dates)}일 데이터")
        return scores

    except Exception as e:
        logger.warning(f"[SupaQuery] Score trend 조회 실패: {e}")
        return {"has_data": False}


# ──────────────────────────────────────────────────────────────
# 5. get_recent_alerts — 최근 Alert 이력
# ──────────────────────────────────────────────────────────────

def get_recent_alerts(days: int = 7) -> list:
    """
    최근 N일간 Alert 발동 이력.

    Returns:
        [
          {"date": "2026-04-04", "type": "OIL", "level": "L2", "value": "WTI $111.5"},
          {"date": "2026-03-28", "type": "VIX", "level": "L1", "value": "VIX 31.1"},
        ]
    """
    try:
        client = _get_client()
        cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")

        result = client.table("daily_alerts") \
            .select("alert_date, alert_type, alert_level, trigger_value") \
            .gte("alert_date", cutoff) \
            .order("alert_date", desc=True) \
            .limit(20) \
            .execute()

        alerts = []
        for row in (result.data or []):
            alerts.append({
                "date": row.get("alert_date", ""),
                "type": row.get("alert_type", ""),
                "level": row.get("alert_level", ""),
                "value": row.get("trigger_value", ""),
            })

        logger.info(f"[SupaQuery] 최근 Alert: {len(alerts)}건 ({days}일)")
        return alerts

    except Exception as e:
        logger.warning(f"[SupaQuery] Alert 이력 조회 실패: {e}")
        return []


# ──────────────────────────────────────────────────────────────
# 6. get_regime_history — 최근 레짐 전환 이력
# ──────────────────────────────────────────────────────────────

def get_regime_history(days: int = 14) -> list:
    """
    최근 N일간 레짐 전환 이력 (변경된 날짜만).

    Returns:
        [
          {"date": "2026-04-04", "regime": "Oil Shock", "risk": "MEDIUM"},
          {"date": "2026-04-02", "regime": "Risk-Off", "risk": "HIGH"},
          {"date": "2026-03-30", "regime": "Transition", "risk": "MEDIUM"},
        ]
    """
    try:
        client = _get_client()
        result = client.table("daily_analysis") \
            .select("analysis_date, regime, risk_level") \
            .order("analysis_date", desc=True) \
            .limit(days) \
            .execute()

        if not result.data or len(result.data) == 0:
            return []

        rows = result.data
        history = [{"date": rows[0].get("analysis_date", ""),
                     "regime": rows[0].get("regime", ""),
                     "risk": rows[0].get("risk_level", "")}]

        for i in range(1, len(rows)):
            if rows[i].get("regime") != rows[i-1].get("regime"):
                history.append({
                    "date": rows[i].get("analysis_date", ""),
                    "regime": rows[i].get("regime", ""),
                    "risk": rows[i].get("risk_level", ""),
                })

        logger.info(f"[SupaQuery] 레짐 이력: {len(history)}건 전환 ({days}일)")
        return history

    except Exception as e:
        logger.warning(f"[SupaQuery] 레짐 이력 조회 실패: {e}")
        return []
