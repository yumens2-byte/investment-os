"""
============================================================
EDT Market Snapshot Collector v1.0
============================================================
목적:
  EDT Universe 파이프라인 Phase A(시장데이터 수집)를 위해
  미국 시장 지표를 수집하고 12 DAILY_DELTA v1.5 신뢰도 등급을
  태깅하여 Notion 고정 페이지(EDT Market Snapshot)에 기록한다.

원칙:
  - 기존 investment-os(ICG) 워크플로/코드 일절 무접촉 (신규 파일만)
  - 소스 이중화: FRED 공식 + yfinance, 한쪽 실패 시 다른 쪽 채택
  - 실패해도 예외로 중단하지 않고 해당 지표만 "불가" 처리
    (EDT 측은 신선도 검증 후 기존 12 v1.5 절차로 폴백)

필요 환경변수 (GitHub Secrets):
  FRED_API_KEY          : 기존 등록분 재사용
  NOTION_API_KEY        : 기존 등록분 재사용
  EDT_SNAPSHOT_PAGE_ID  : 신규 — Notion "EDT Market Snapshot" 페이지 ID
  DRY_RUN (선택)        : "true" 시 Notion 기록 생략, stdout 출력만

의존성: requests, yfinance, pandas  (requirements 참조)
============================================================
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------

SCHEMA_VERSION = "edt_snapshot_v1.0"
KST = timezone(timedelta(hours=9))

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_PAGE_ID = os.environ.get("EDT_SNAPSHOT_PAGE_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# FRED 시리즈 정의: key -> (series_id, 표시명, 12 v1.5 등급, 통상 지연일)
FRED_SERIES = {
    "us10y":    ("DGS10",        "10Y Treasury Yield (%)",      "높음", 1),
    "us2y":     ("DGS2",         "2Y Treasury Yield (%)",       "높음", 1),
    "t10y2y":   ("T10Y2Y",       "10Y-2Y Spread (%p)",          "높음", 1),
    "effr":     ("EFFR",         "Effective Fed Funds Rate (%)","높음", 1),
    "sofr":     ("SOFR",         "SOFR (%)",                    "높음", 1),
    "hy_oas":   ("BAMLH0A0HYM2", "HY OAS (ICE BofA, %p)",       "중간", 3),
    "vix_fred": ("VIXCLS",       "VIX Close (FRED)",            "높음", 1),
}

# yfinance 티커 정의: key -> (ticker, 표시명, 등급)
YF_TICKERS = {
    "vix": ("^VIX",     "VIX Close (yfinance)", "높음"),
    "wti": ("CL=F",     "WTI Crude ($)",        "중간"),
    "dxy": ("DX-Y.NYB", "Dollar Index (DXY)",   "중간"),
}


# ------------------------------------------------------------
# 수집 함수
# ------------------------------------------------------------

def fetch_fred(series_id: str):
    """FRED 최근 관측 2건 반환: (latest_value, latest_date, prev_value, prev_date)
    실패/데이터 없음 시 None 반환."""
    if not FRED_API_KEY:
        return None
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 10,
    }
    try:
        r = requests.get(FRED_BASE, params=params, timeout=30)
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)]
        if not obs:
            return None
        latest = obs[0]
        prev = obs[1] if len(obs) > 1 else None
        return (
            float(latest["value"]), latest["date"],
            float(prev["value"]) if prev else None,
            prev["date"] if prev else None,
        )
    except Exception as e:
        print(f"[WARN] FRED {series_id} 실패: {e}", file=sys.stderr)
        return None


def fetch_yf(ticker: str):
    """yfinance 최근 종가 2건 반환. 실패 시 None."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period="10d", progress=False, auto_adjust=True)
        closes = df["Close"].dropna()
        if closes.empty:
            return None
        # yfinance 멀티인덱스 대응
        if hasattr(closes, "columns"):
            closes = closes.iloc[:, 0].dropna()
        latest_date = closes.index[-1].strftime("%Y-%m-%d")
        latest_val = float(closes.iloc[-1])
        prev_val = float(closes.iloc[-2]) if len(closes) > 1 else None
        prev_date = closes.index[-2].strftime("%Y-%m-%d") if len(closes) > 1 else None
        return (latest_val, latest_date, prev_val, prev_date)
    except Exception as e:
        print(f"[WARN] yfinance {ticker} 실패: {e}", file=sys.stderr)
        return None


# ------------------------------------------------------------
# 메트릭 조립
# ------------------------------------------------------------

def build_metric(name, value, as_of, prev, prev_date, source, grade, lag_days=None):
    delta = round(value - prev, 4) if (value is not None and prev is not None) else None
    return {
        "name": name,
        "value": round(value, 4) if value is not None else None,
        "as_of": as_of,
        "prev": round(prev, 4) if prev is not None else None,
        "prev_date": prev_date,
        "delta": delta,
        "source": source,
        "grade": grade,          # 12 v1.5 신뢰도 등급 (높음/중간/낮음/불가)
        "lag_days_typical": lag_days,
    }


def collect() -> dict:
    metrics = {}
    failures = []

    # 1) FRED 공식 소스
    for key, (sid, name, grade, lag) in FRED_SERIES.items():
        res = fetch_fred(sid)
        if res:
            v, d, pv, pd_ = res
            metrics[key] = build_metric(name, v, d, pv, pd_, f"FRED:{sid}", grade, lag)
        else:
            failures.append(key)
            metrics[key] = build_metric(name, None, None, None, None, f"FRED:{sid}", "불가", lag)

    # 2) yfinance 소스
    for key, (tk, name, grade) in YF_TICKERS.items():
        res = fetch_yf(tk)
        if res:
            v, d, pv, pd_ = res
            metrics[key] = build_metric(name, v, d, pv, pd_, f"yfinance:{tk}", grade)
        else:
            failures.append(key)
            metrics[key] = build_metric(name, None, None, None, None, f"yfinance:{tk}", "불가")

    # 3) VIX 이중화: yfinance 우선, 실패 시 FRED VIXCLS 승격
    if metrics["vix"]["value"] is None and metrics["vix_fred"]["value"] is not None:
        metrics["vix"] = dict(metrics["vix_fred"])
        metrics["vix"]["name"] = "VIX Close (FRED fallback)"

    # 4) 거래일 판정 (DGS10 최신 관측일 기준)
    last_trading_day = metrics["us10y"]["as_of"]
    now_kst = datetime.now(KST)
    is_new_data = False
    if last_trading_day:
        gap = (now_kst.date() - datetime.strptime(last_trading_day, "%Y-%m-%d").date()).days
        is_new_data = gap <= 4  # 주말+지연 허용 범위

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        "meta": {
            "last_us_trading_day": last_trading_day,
            "is_new_data": is_new_data,
            "failed_metrics": failures,
            "excluded_by_design": ["fear_greed_index (web_search 유지)"],
            "edt_rule": "12 DAILY_DELTA v1.5 — 본 snapshot은 신뢰도 1순위 소스. "
                        "generated_at 24h 초과 시 EDT 측 기존 절차 폴백.",
        },
        "metrics": metrics,
    }
    return snapshot


# ------------------------------------------------------------
# Notion 기록 (고정 페이지의 첫 code block 갱신, 없으면 생성)
# ------------------------------------------------------------

NOTION_HEADERS_BASE = {
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def _notion_headers():
    return {**NOTION_HEADERS_BASE, "Authorization": f"Bearer {NOTION_API_KEY}"}


def _chunk_rich_text(text: str, limit: int = 1900):
    return [{"type": "text", "text": {"content": text[i:i + limit]}}
            for i in range(0, len(text), limit)]


def write_to_notion(snapshot: dict):
    if not NOTION_API_KEY or not NOTION_PAGE_ID:
        raise RuntimeError("NOTION_API_KEY 또는 EDT_SNAPSHOT_PAGE_ID 미설정")

    body = json.dumps(snapshot, ensure_ascii=False, indent=2)
    code_payload = {
        "code": {
            "rich_text": _chunk_rich_text(body),
            "language": "json",
        }
    }

    # 기존 첫 code block 탐색
    r = requests.get(
        f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children?page_size=50",
        headers=_notion_headers(), timeout=30)
    r.raise_for_status()
    code_block_id = None
    for blk in r.json().get("results", []):
        if blk.get("type") == "code":
            code_block_id = blk["id"]
            break

    if code_block_id:
        r = requests.patch(
            f"https://api.notion.com/v1/blocks/{code_block_id}",
            headers=_notion_headers(), json=code_payload, timeout=30)
    else:
        r = requests.patch(
            f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children",
            headers=_notion_headers(),
            json={"children": [{"object": "block", "type": "code", **code_payload}]},
            timeout=30)
    r.raise_for_status()
    print(f"[OK] Notion 기록 완료: page={NOTION_PAGE_ID}")


# ------------------------------------------------------------
# main
# ------------------------------------------------------------

def main():
    snapshot = collect()
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))

    # 핵심 지표 전량 실패 시 실패 종료 (Actions 알림 유도)
    core_failed = all(
        snapshot["metrics"][k]["value"] is None for k in ("us10y", "us2y", "vix"))
    if core_failed:
        print("[FATAL] 핵심 지표(10Y/2Y/VIX) 전량 수집 실패", file=sys.stderr)
        sys.exit(1)

    if DRY_RUN:
        print("[DRY_RUN] Notion 기록 생략")
        return
    write_to_notion(snapshot)


if __name__ == "__main__":
    main()
