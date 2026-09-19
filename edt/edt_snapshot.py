"""
============================================================
EDT Market Snapshot Collector v1.2
============================================================
변경 이력:
  v1.2 (2026-09-19) — 파일럿 테스트 반영: 롤오버 오탐 제거
    · 단일 변동률(4%) 판정은 오탐률 67% 확인 → 만기 캘린더 게이트 추가
      실측: 09-10 +6.7% / 09-11 -4.61% (실제 급변, 승격되면 안 됨)
            09-18 -6.3% (롤오버, 승격되어야 함)
      → 변동률만으로는 셋 다 발동. 만기 근접 여부로만 분리 가능.
    · NYMEX WTI 만기(인도월 전월 25일 이전 3영업일) 계산 함수 신설
    · 롤오버 판정 = 만기 근접 구간 AND 변동률 초과 (AND 조건)
    · 만기 구간 밖 급변은 승격하지 않고 플래그만 기록 (실제 급변 정보 보존)
  v1.1 (2026-09-19) — TRACK-A2-32 대응: WTI 선물 롤오버 왜곡 교정
    · FRED DCOILWTICO(WTI Cushing 현물) 주 소스 추가
    · fetch_yf() 롤오버/이상치 감지 플래그 추가 (반환 4→5개)
    · WTI 이중화: 롤오버 의심 또는 yfinance 실패 시 FRED 승격
    · meta.rollover_suspected 필드 신설
    ※ 배경: yfinance CL=F는 연속선물이라 만기 전 계약월이 자동 교체된다.
      2026-09-18 실측에서 latest=11월물($95.47) / prev=10월물($101.91)이
      되어 실제 시장 변동(-1.6%)과 무관한 -$4.8 계단 하락이 발생했다.
      Oil Shock 트리거($100)가 단일 임계라 오판정 직전까지 갔다.
  v1.0 — 최초 작성

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

SCHEMA_VERSION = "edt_snapshot_v1.2"
KST = timezone(timedelta(hours=9))

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_PAGE_ID = os.environ.get("EDT_SNAPSHOT_PAGE_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# v1.1 — 이상치 감지 임계 (12 DAILY_DELTA v1.14 RULE DSG-04와 동일 기준)
ROLLOVER_THRESHOLD = 0.04   # 일간 변동 4% 초과
# v1.2 — 만기 근접 판정 폭. 이 구간 안에서의 급변만 롤오버로 본다.
ROLL_WINDOW_BDAYS = 5       # 만기 5영업일 전 ~ 만기 당일
# 만기 캘린더를 적용할 선물 기반 지표 (현물·지수는 제외)
FUTURES_KEYS = {"wti"}

# FRED 시리즈 정의: key -> (series_id, 표시명, 12 v1.5 등급, 통상 지연일)
FRED_SERIES = {
    "us10y":    ("DGS10",        "10Y Treasury Yield (%)",      "높음", 1),
    "us2y":     ("DGS2",         "2Y Treasury Yield (%)",       "높음", 1),
    "t10y2y":   ("T10Y2Y",       "10Y-2Y Spread (%p)",          "높음", 1),
    "effr":     ("EFFR",         "Effective Fed Funds Rate (%)","높음", 1),
    "sofr":     ("SOFR",         "SOFR (%)",                    "높음", 1),
    "hy_oas":   ("BAMLH0A0HYM2", "HY OAS (ICE BofA, %p)",       "중간", 3),
    "vix_fred": ("VIXCLS",       "VIX Close (FRED)",            "높음", 1),
    # v1.1 신설 — WTI Cushing 현물. 계약월 개념이 없어 롤오버 왜곡이 원천 없음.
    "wti_fred": ("DCOILWTICO",   "WTI Crude (FRED, Cushing 현물)", "높음", 2),
}

# yfinance 티커 정의: key -> (ticker, 표시명, 등급)
YF_TICKERS = {
    "vix": ("^VIX",     "VIX Close (yfinance)", "높음"),
    # CL=F는 연속선물 — 만기 전 계약월 자동 교체로 롤오버 왜곡 발생 가능.
    # v1.1부터 wti_fred(FRED 현물)와 이중화하여 의심 시 승격한다.
    "wti": ("CL=F",     "WTI Crude ($)",        "중간"),
    "dxy": ("DX-Y.NYB", "Dollar Index (DXY)",   "중간"),
}


# ------------------------------------------------------------
# 만기 캘린더 (v1.2 신설)
# ------------------------------------------------------------

def _prev_bday(d):
    """직전 영업일(주말만 고려). 미국 공휴일은 미반영이므로
    ROLL_WINDOW_BDAYS 를 넉넉히 잡아 오차를 흡수한다."""
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def wti_expiry(year: int, month: int):
    """NYMEX WTI(CL) 인도월 {year}-{month} 계약의 최종거래일.
    규칙: 인도월 전월 25일 이전 3영업일.
          25일이 영업일이 아니면 25일 직전 영업일을 기준으로 3영업일 전."""
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    base = datetime(py, pm, 25).date()
    while base.weekday() >= 5:          # 25일이 주말이면 직전 영업일로
        base -= timedelta(days=1)
    for _ in range(3):
        base = _prev_bday(base)
    return base


def bdays_until(target, frm):
    """frm → target 까지의 영업일 수. target 이 과거면 음수."""
    if target == frm:
        return 0
    step = 1 if target > frm else -1
    cnt, cur = 0, frm
    while cur != target:
        cur += timedelta(days=step)
        if cur.weekday() < 5:
            cnt += step
    return cnt


def in_roll_window(as_of_str: str) -> bool:
    """해당 일자가 WTI 롤오버 위험 구간(만기 N영업일 전 ~ 만기)에 있는가."""
    if not as_of_str:
        return False
    try:
        d = datetime.strptime(as_of_str, "%Y-%m-%d").date()
    except ValueError:
        return False
    # 당월·익월 인도 계약의 만기를 모두 검사 (월말 경계 대응)
    cands = [wti_expiry(d.year, d.month)]
    ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    cands.append(wti_expiry(ny, nm))
    for exp in cands:
        gap = bdays_until(exp, d)
        if 0 <= gap <= ROLL_WINDOW_BDAYS:
            return True
    return False


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
    """yfinance 최근 종가 2건 + 롤오버 의심 플래그 반환. 실패 시 None.

    v1.1: 반환값이 4개 → 5개로 변경되었다.
          (latest_val, latest_date, prev_val, prev_date, rollover_suspect)
          호출부 언패킹을 반드시 함께 맞춰야 한다.
    """
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

        # v1.1 신설 — 롤오버/이상치 감지
        # 연속선물의 계약월 교체는 실제 시장 변동과 무관한 계단형 점프로 나타난다.
        rollover_suspect = False
        if prev_val:
            rollover_suspect = abs(latest_val - prev_val) / prev_val > ROLLOVER_THRESHOLD

        return (latest_val, latest_date, prev_val, prev_date, rollover_suspect)
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
    rollover_suspected = []                                   # v1.1 신설
    outlier_only = []                                         # v1.2 신설
    for key, (tk, name, grade) in YF_TICKERS.items():
        res = fetch_yf(tk)
        if res:
            v, d, pv, pd_, suspect = res                      # v1.1 — 5개 언패킹
            metrics[key] = build_metric(name, v, d, pv, pd_, f"yfinance:{tk}", grade)
            if suspect:
                # v1.2 — 변동률 초과만으로는 롤오버로 단정하지 않는다.
                # 선물 지표가 만기 근접 구간에 있을 때만 롤오버로 판정한다.
                if key in FUTURES_KEYS and in_roll_window(d):
                    rollover_suspected.append(key)
                    print(f"[WARN] {key}: 만기 근접 구간에서 일간 변동 "
                          f"{ROLLOVER_THRESHOLD:.0%} 초과 ({pv} -> {v}) "
                          f"— 롤오버 의심", file=sys.stderr)
                else:
                    outlier_only.append(key)
                    print(f"[INFO] {key}: 일간 변동 {ROLLOVER_THRESHOLD:.0%} 초과 "
                          f"({pv} -> {v}) — 만기 구간 밖, 실제 급변으로 간주하여 "
                          f"원본 유지", file=sys.stderr)
        else:
            failures.append(key)
            metrics[key] = build_metric(name, None, None, None, None, f"yfinance:{tk}", "불가")

    # 3) VIX 이중화: yfinance 우선, 실패 시 FRED VIXCLS 승격
    if metrics["vix"]["value"] is None and metrics["vix_fred"]["value"] is not None:
        metrics["vix"] = dict(metrics["vix_fred"])
        metrics["vix"]["name"] = "VIX Close (FRED fallback)"

    # 3-B) WTI 이중화 (v1.1 신설) — 롤오버 의심 또는 yfinance 실패 시 FRED 현물 승격
    #      평시에는 당일성이 좋은 yfinance를 유지하고, 이상 시에만 FRED로 교체한다.
    if metrics.get("wti_fred", {}).get("value") is not None:
        if ("wti" in rollover_suspected) or (metrics["wti"]["value"] is None):
            yf_raw = metrics["wti"]["value"]
            yf_as_of = metrics["wti"]["as_of"]
            reason = "롤오버 의심" if "wti" in rollover_suspected else "yfinance 실패"
            metrics["wti"] = dict(metrics["wti_fred"])
            metrics["wti"]["name"] = f"WTI Crude (FRED 승격 — {reason})"
            metrics["wti"]["yf_raw"] = yf_raw        # 기각된 원본 보존
            metrics["wti"]["yf_as_of"] = yf_as_of
            metrics["wti"]["promoted_reason"] = reason
            print(f"[INFO] WTI: FRED 승격 ({reason}). yfinance 원본 {yf_raw} 기각",
                  file=sys.stderr)

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
            "rollover_suspected": rollover_suspected,   # v1.1 신설
            "outlier_no_promote": outlier_only,         # v1.2 신설 — 급변이나 승격 안 함
            "excluded_by_design": ["fear_greed_index (web_search 유지)"],
            "edt_rule": "12 DAILY_DELTA v1.5 — 본 snapshot은 신뢰도 1순위 소스. "
                        "generated_at 24h 초과 시 EDT 측 기존 절차 폴백. "
                        "v1.2: 선물 지표는 12 v1.14 DSG-04/DSG-05 및 "
                        "21 v1.13 OST-01/OST-02 교차검증을 거쳐 확정한다. "
                        "rollover_suspected=롤오버 판정(FRED 승격) / "
                        "outlier_no_promote=실제 급변(원본 유지, CP-1 교차검증 권고).",
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
