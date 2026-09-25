"""
============================================================
EDT Market Snapshot Collector v1.3
============================================================
변경 이력:
  v1.4 (2026-09-26) — ANCHOR-01 교정 (v1.3 미적용 상태에서 실측 검증으로 발견)
    · v1.3 결함: 앵커 관측일을 us10y(FRED) 단일 시리즈에서 취했다.
      FRED 공표 지연이 1영업일이므로 매 실행마다 anchor_ok=false 오탐 발생.
      실측(2026-09-26 07:0x KST): FRED 10Y 최신=09-24 인데,
        같은 시점 스냅샷에 yfinance 09-25 종가가 이미 존재(sp500/WTI/VIX/DXY).
        → 위반이 아님에도 [WARN] ANCHOR 불일치가 발화했다.
    · 교정: 관측 앵커 = **전 소스 중 최신 관측일**(freshest wins).
      meta.anchor 에 freshest_source / fred_last_us_trading_day 를 병기하여
      "소스 자체 지연"과 "실제 데이터 미도달"을 구분한다.
    · meta.observed_last_us_trading_day 신설 (last_us_trading_day 는 v1.2 의미 보존).
    · 전 메트릭(FRED+yfinance)에 age_days 산출 적용.
  v1.3 (2026-09-26) — 수집기 결함 4건 패치 (TRACK-A2-32 후속 / D-1·D-2·D-6 대응)
    [A] FRED_SERIES 에 us30y(DGS30) 신설
        ※ 실측 결함: v1.2 스냅샷에 30Y 필드 자체가 없어
          21 v1.14 TH-IMM-05 이중선(경보 5.375/확정 5.40/트리거 5.55) 판정이
          구조적으로 불가능했다. CP-1 에서 [DATA GAP] 으로 반복 노출된 원인.
    [B] 신선도 게이트(staleness gate) 신설
        · 시리즈별 최대 허용 관측 경과일 STALE_MAX_DAYS
        · 관측 지연 시 metric.stale=True / metric.age_days / meta.stale_series
        ※ 실측 결함: fetch_fred 는 관측 최신값을 무조건 채택한다.
          DCOILWTICO 가 2026-09-15 자($107.02)에 정체된 상태에서도
          "최신값"으로 채택되어 Ep91~Ep94 파이프라인에 주입되었다.
    [C] WTI 승격 경로 강화 (RULE WTI-PROMO-01)
        · 롤오버 판정 시 승격 후보(FRED)가 stale 이면 승격 금지
        · 승격 불가 시 yfinance 직전 종가로 롤백(rollback) — 3분기 처리
        ※ 실측 결함: v1.2 는 stale FRED 도 무조건 승격하여
          "최신 실측"을 "7일 전 값"으로 덮어썼다.
    [D] meta.anchor 신설 — 앵커 계약 검증
        · expected_last_us_trading_day / anchor_lag_days / anchor_ok
        ※ 실측 결함: 17 v1.11 RULE PUB-01 은 앵커를 "직전 미국 종가"로 규정하나
          스냅샷은 그 검증 신호를 출력하지 않아 위반이 조용히 통과했다.
          실측: 2026-09-26 06:43 KST 조회분 last_us_trading_day=09-23 (기대 09-25, lag 2영업일)
    [E] YF_TICKERS 에 sp500(^GSPC) 신설 — arc_state.market_snapshot 필수 필드 대응
    [F] meta.schedule_slot 신설 — 회차 슬롯 식별(월·수·금 발행 캘린더 대조용)
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

⚠️ v1.3 은 **가산(additive) 패치**다. 기존 필드 삭제·의미 변경 0건.
   추가 필드: metric.stale / metric.age_days / meta.stale_series /
              meta.anchor / meta.schedule_slot / metrics.us30y / metrics.sp500
   → 하위 소비자(12 v1.14 / 21 v1.13 / 21 v1.14)는 무수정으로 동작한다.

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
from datetime import date, datetime, timedelta, timezone

import requests

# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------

SCHEMA_VERSION = "edt_snapshot_v1.4"
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

# v1.3 [B] — 시리즈별 최대 허용 관측 경과일(달력일).
#   lag_days_typical(FRED 통상 지연) + 주말 흡수 여유.
#   초과 시 stale=True 로 기록하고, 승격 후보 자격을 박탈한다.
STALE_MAX_DAYS = {
    "us10y": 4, "us2y": 4, "us30y": 4, "t10y2y": 4,
    "effr": 4, "sofr": 4, "vix_fred": 4,
    "hy_oas": 6,           # 등급 중간 / 통상 lag 3일
    "wti_fred": 5,         # 등급 높음 / 통상 lag 2일
}
STALE_MAX_DEFAULT = 4

# FRED 시리즈 정의: key -> (series_id, 표시명, 12 v1.5 등급, 통상 지연일)
FRED_SERIES = {
    "us10y":    ("DGS10",        "10Y Treasury Yield (%)",      "높음", 1),
    "us2y":     ("DGS2",         "2Y Treasury Yield (%)",       "높음", 1),
    # v1.3 [A] 신설 — 21 v1.14 TH-IMM-05 이중선 판정 필수 지표.
    #   v1.2 까지 30Y 가 스냅샷에 부재하여 CP-1 에서 [DATA GAP] 이 반복 발생했다.
    "us30y":    ("DGS30",        "30Y Treasury Yield (%)",      "높음", 1),
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
    # v1.3 [E] 신설 — arc_state.market_snapshot 의 sp500 필드 대응.
    "sp500": ("^GSPC",  "S&P 500 Close",        "높음"),
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
# 신선도 / 앵커 판정 (v1.3 신설)
# ------------------------------------------------------------

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def age_days(as_of_str: str, ref: date):
    """관측일 → 기준일 경과 달력일. 파싱 불가 시 None."""
    d = parse_date(as_of_str)
    if d is None:
        return None
    return (ref - d).days


def is_fresh(key: str, as_of_str: str, ref: date) -> bool:
    """RULE STALE-01 — 시리즈별 최대 허용 경과일 이내인가."""
    age = age_days(as_of_str, ref)
    if age is None:
        return False
    return age <= STALE_MAX_DAYS.get(key, STALE_MAX_DEFAULT)


def expected_us_trading_day(now_kst: datetime) -> date:
    """RULE ANCHOR-01 — 해당 실행 시점에 '직전 미국 종가'가 존재해야 하는 날짜.

    미국 정규장은 16:00 ET 마감 = KST 익일 05:00(서머타임)/06:00(표준시).
    따라서 KST 실행일 D 의 직전 완료 세션은 미국일 기준 D-1 이며,
    D-1 이 주말이면 직전 금요일로 후퇴한다.
    """
    d = now_kst.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def schedule_slot(now_kst: datetime) -> str:
    """v1.3 [F] — 17 v1.11 발행 캘린더(월·수·금) 대조용 슬롯 라벨."""
    wd = now_kst.weekday()          # 0=월 … 6=일
    return {
        0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI", 5: "SAT", 6: "SUN",
    }.get(wd, "UNKNOWN")


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
        "stale": False,          # v1.3 [B] 신설
        "age_days": None,        # v1.3 [B] 신설
    }


def resolve_wti(wti: dict, wti_fred: dict, rollover_suspected: bool):
    """RULE WTI-PROMO-01 (v1.3 [C]) — WTI 소스 확정.

    반환: (확정 metric, action) — action ∈ {keep, promote, rollback, gap}

    v1.2 결함: 승격 후보(FRED)의 신선도를 검증하지 않고 무조건 승격했다.
      → DCOILWTICO 가 2026-09-15 자($107.02)에 정체된 상태에서도
        "최신값"으로 채택되어 Ep91~Ep94 파이프라인에 주입되었다.
    """
    fred_ok = wti_fred.get("value") is not None and not wti_fred.get("stale")

    if rollover_suspected:
        if fred_ok:
            m = dict(wti_fred)
            m["name"] = "WTI Crude (FRED 승격 — 롤오버 의심)"
            m["yf_raw"] = wti.get("value")          # 기각된 원본 보존
            m["yf_as_of"] = wti.get("as_of")
            m["promoted_reason"] = "롤오버 의심"
            return m, "promote"
        # 승격 후보가 stale → yfinance 직전 종가로 롤백 (오염점 회피)
        if wti.get("prev") is not None:
            m = build_metric(
                "WTI Crude (yfinance 직전 종가 롤백 — 롤오버 회피)",
                wti["prev"], wti.get("prev_date"), None, None,
                "yfinance:CL=F(prev)", "중간",
            )
            m["yf_raw"] = wti.get("value")
            m["yf_as_of"] = wti.get("as_of")
            m["promoted_reason"] = "롤오버 의심 + FRED stale → 직전 종가 롤백"
            m["stale"] = True
            return m, "rollback"
        return wti, "gap"

    if wti.get("value") is None:
        if fred_ok:
            m = dict(wti_fred)
            m["name"] = "WTI Crude (FRED 승격 — yfinance 실패)"
            m["promoted_reason"] = "yfinance 실패"
            return m, "promote"
        return wti, "gap"

    return wti, "keep"


def resolve_anchor(metrics: dict, now_kst: datetime) -> dict:
    """RULE ANCHOR-01 (v1.4 교정) — 앵커 계약 검증.

    v1.3 결함: 관측 앵커를 us10y(FRED) 단일 시리즈로 삼았다.
      FRED 공표 지연이 1영업일이므로, 매 실행마다 anchor_ok=False 오탐이 발생한다.
      실제로는 yfinance 계열 지표가 당일 종가를 이미 보유하고 있는 경우가 대부분이다.
    v1.4: 관측 앵커 = 전 소스 중 최신 관측일(freshest wins).
      fred_last_us_trading_day 를 병기해 "소스 자체 지연"과
      "실제 데이터 미도달"을 구분한다.
    """
    fred_day = (metrics.get("us10y") or {}).get("as_of")
    observed, src = None, None
    for key, m in metrics.items():
        a = m.get("as_of")
        if m.get("value") is None or not a:
            continue
        if observed is None or a > observed:
            observed, src = a, m.get("source")

    exp = expected_us_trading_day(now_kst)
    out = {
        "expected_last_us_trading_day": exp.isoformat(),
        "observed_last_us_trading_day": observed,
        "fred_last_us_trading_day": fred_day,
        "freshest_source": src,
        "freshest_sources": [],
        "anchor_lag_bdays": None,
        "anchor_ok": False,
        "anchor_note": "",
    }
    if observed is None:
        out["anchor_note"] = "[DATA GAP] 관측일 산출 불가 — 전 메트릭 수집 실패"
        return out
    freshest = sorted({m.get("source") for m in metrics.values()
                       if m.get("value") is not None and m.get("as_of") == observed})
    out["freshest_sources"] = freshest
    out["anchor_lag_bdays"] = bdays_until(exp, parse_date(observed))
    out["anchor_ok"] = (parse_date(observed) == exp)
    if out["anchor_ok"]:
        out["anchor_note"] = (
            f"앵커 계약 충족 — 기대 {exp.isoformat()} = 관측 {observed} "
            f"({len(freshest)}개 소스). "
            f"FRED 10Y 최신 {fred_day} (소스 자체 지연, 위반 아님).")
    else:
        out["anchor_note"] = (
            f"17 v1.11 RULE PUB-01 위반 — 기대 앵커 {exp.isoformat()}, "
            f"관측 {observed} (lag {out['anchor_lag_bdays']}영업일). "
            f"FRED 공표 지연 또는 수집기 스케줄 지연.")
    return out


def collect() -> dict:
    metrics = {}
    failures = []
    now_kst = datetime.now(KST)
    ref_date = now_kst.date()

    # 1) FRED 공식 소스
    for key, (sid, name, grade, lag) in FRED_SERIES.items():
        res = fetch_fred(sid)
        if res:
            v, d, pv, pd_ = res
            metrics[key] = build_metric(name, v, d, pv, pd_, f"FRED:{sid}", grade, lag)
        else:
            failures.append(key)
            metrics[key] = build_metric(name, None, None, None, None, f"FRED:{sid}", "불가", lag)

    # 1-B) v1.3 [B] 신선도 게이트 — 관측 지연 시 stale 마킹
    stale_series = []
    for key in FRED_SERIES:
        m = metrics.get(key)
        if not m or m.get("value") is None:
            continue
        m["age_days"] = age_days(m.get("as_of"), ref_date)
        if not is_fresh(key, m.get("as_of"), ref_date):
            m["stale"] = True
            stale_series.append(key)
            print(f"[WARN] {key}: 관측 {m.get('as_of')} — 경과 {m['age_days']}일 "
                  f"(허용 {STALE_MAX_DAYS.get(key, STALE_MAX_DEFAULT)}일) 초과 → stale "
                  f"※ 승격 후보 자격 박탈", file=sys.stderr)

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

    # 3) VIX 이중화: yfinance 우선, 실패 시 FRED VIXCLS 승격 (stale 게이트 적용)
    if metrics["vix"]["value"] is None and metrics["vix_fred"].get("value") is not None:
        if not metrics["vix_fred"].get("stale"):
            metrics["vix"] = dict(metrics["vix_fred"])
            metrics["vix"]["name"] = "VIX Close (FRED fallback)"
        else:
            print("[WARN] VIX: yfinance 실패 + FRED stale → 승격 보류", file=sys.stderr)

    # 3-B) WTI 이중화 (v1.1 신설 / v1.3 [C] 강화)
    #      평시에는 당일성이 좋은 yfinance를 유지하고,
    #      롤오버 의심 또는 yfinance 실패 시에만 FRED를 승격 후보로 검토한다.
    wti_action = "keep"
    if metrics.get("wti") is not None:
        resolved, wti_action = resolve_wti(
            metrics["wti"], metrics.get("wti_fred", {}) or {},
            "wti" in rollover_suspected,
        )
        if wti_action in ("promote", "rollback"):
            metrics["wti"] = resolved
            print(f"[INFO] WTI: {wti_action} — value={resolved.get('value')} "
                  f"as_of={resolved.get('as_of')}", file=sys.stderr)
        elif wti_action == "gap":
            print("[WARN] WTI: 승격 후보 stale + 직전값 부재 → [DATA GAP]", file=sys.stderr)

    # 3-C) v1.4 — 전 메트릭(FRED + yfinance) 관측 경과일 산출 (앵커 판정 입력)
    for key, m in metrics.items():
        if m.get("value") is not None and m.get("as_of"):
            m["age_days"] = age_days(m.get("as_of"), ref_date)

    # 4) 거래일 판정 + 앵커 계약 검증 (v1.3 [D] 신설 / v1.4 교정)
    anchor = resolve_anchor(metrics, now_kst)
    last_trading_day = anchor["fred_last_us_trading_day"]     # v1.2 의미 보존
    observed_last_day = anchor["observed_last_us_trading_day"]
    is_new_data = False
    if observed_last_day:
        gap = (now_kst.date() - parse_date(observed_last_day)).days
        is_new_data = gap <= 4      # 주말+지연 허용 범위
    if not anchor["anchor_ok"]:
        print(f"[WARN] ANCHOR 불일치: {anchor['anchor_note']}", file=sys.stderr)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        "meta": {
            "last_us_trading_day": last_trading_day,          # FRED 10Y 기준 (v1.2 의미 보존)
            "observed_last_us_trading_day": observed_last_day,  # v1.4 — 전 소스 최신 관측일
            "is_new_data": is_new_data,
            "failed_metrics": failures,
            "rollover_suspected": rollover_suspected,   # v1.1 신설
            "outlier_no_promote": outlier_only,         # v1.2 신설 — 급변이나 승격 안 함
            "stale_series": stale_series,               # v1.3 [B] 신설
            "wti_action": wti_action,                   # v1.3 [C] 신설
            "anchor": anchor,                           # v1.3 [D] 신설
            "schedule_slot": schedule_slot(now_kst),    # v1.3 [F] 신설
            "excluded_by_design": ["fear_greed_index (web_search 유지)"],
            "edt_rule": "12 DAILY_DELTA v1.5 — 본 snapshot은 신뢰도 1순위 소스. "
                        "generated_at 24h 초과 시 EDT 측 기존 절차 폴백. "
                        "v1.3: 시리즈별 신선도 게이트(STALE-01)와 앵커 계약 검증(ANCHOR-01)을 "
                        "적용한다. stale_series=승격·채택 부적격 지표 / "
                        "anchor.anchor_ok=false 시 CP-1 은 앵커 불일치를 명시하고 "
                        "17 v1.11 RULE PUB-02·PUB-04 절차를 발동해야 한다. "
                        "rollover_suspected=롤오버 판정 / "
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
