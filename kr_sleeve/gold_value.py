"""
kr_sleeve/gold_value.py — 금으로 잰 화폐가치 패널 (DN2026_0109)

지표: 같은 돈(1억 원 · 1만 달러 · 100만 엔)으로 살 수 있는 금(g)의 변화. 보유 비율 제안이 아니다.
  5년 헤드라인 : KRX 금시장 국내 금(원/g) 단일 소스. 최신 완료 월 월평균 vs 60개월 전 같은 월 월평균(고정 규칙)
  1개월 보조   : COMEX 금 근월물(달러/oz) × 환율. 금 마지막 확정일 T vs T−30일 이하 최신일
경로: 네이버 marketIndex prices (비공식). PoC 2026-11-09 Actions 42574453271·42574892694·42575779387·42577605104.
  M04020000 은 productDetail 응답 stockExchangeType=KRX(한국거래소), 단위 원/g 으로 확인.
규칙:
  - 확정값만 쓴다. KST 오늘 날짜 행(장중)은 제외. 국제 금은 시카고 날짜 기준 오늘 이전 행만.
  - 요청 1회당 재시도 없음. 실패·형식 변경은 GoldSkip(사유 코드) → 패널만 생략(DN2026_0109 G-09).
  - 이력은 Supabase gold_value_daily에 쌓는다(shadow·live). dry_run은 메모리에서만 계산한다.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from kr_sleeve import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
_CT = ZoneInfo("America/Chicago")
PRICES_URL = "https://m.stock.naver.com/front-api/marketIndex/prices"
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# 사유 코드 (STD-PROC-15: 사유를 한 문구로 묶지 않는다)
GOLD_SOURCE_HTTP = "GOLD_SOURCE_HTTP"
GOLD_SOURCE_FORMAT = "GOLD_SOURCE_FORMAT"
GOLD_INSUFFICIENT = "GOLD_INSUFFICIENT"
GOLD_STALE = "GOLD_STALE"
GOLD_DB = "GOLD_DB"


class GoldSkip(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Series:
    key: str        # DB series 값
    category: str
    code: str
    lo: float       # 허용 범위(하한)
    hi: float       # 허용 범위(상한)
    tz: str         # 날짜 기준: "KST" 또는 "CT"


KRX_GOLD = Series("krx_gold_krw_g", "metals", "M04020000", 10_000, 1_000_000, "KST")
GC_GOLD = Series("gc_gold_usd_oz", "metals", "GCcv1", 500, 20_000, "CT")
USDKRW = Series("usdkrw", "exchange", "FX_USDKRW", 800, 2_500, "KST")
JPYKRW = Series("jpykrw100", "exchange", "FX_JPYKRW", 500, 1_600, "KST")
ALL_SERIES = (KRX_GOLD, GC_GOLD, USDKRW, JPYKRW)
LONG_SERIES = (KRX_GOLD, USDKRW, JPYKRW)   # 5년 비교에 쓰는 시리즈


# ── 수집 ────────────────────────────────────────────────────
def _confirmed(series: Series, traded_at: str, now_utc: datetime) -> str | None:
    """확정 행이면 날짜(YYYY-MM-DD), 아니면 None."""
    day = traded_at[:10]
    if series.tz == "CT":
        return day if day < now_utc.astimezone(_CT).date().isoformat() else None
    return day if day < now_utc.astimezone(KST).date().isoformat() else None


def fetch_page(series: Series, page: int, now_utc: datetime, http_get: Callable) -> list[tuple[str, float]]:
    """1페이지(최신순) → 확정 (day, close) 목록. 빈 결과는 []."""
    params = {"category": series.category, "reutersCode": series.code,
              "page": page, "pageSize": config.GOLD_PAGE_SIZE}
    try:
        resp = http_get(PRICES_URL, params=params, headers=_UA, timeout=config.HTTP_TIMEOUT_SEC)
    except requests.RequestException as e:
        raise GoldSkip(GOLD_SOURCE_HTTP, f"{series.key} {type(e).__name__}") from e
    if resp.status_code != 200:
        raise GoldSkip(GOLD_SOURCE_HTTP, f"{series.key} HTTP {resp.status_code}")
    try:
        body = json.loads(resp.text)
        if not body.get("isSuccess"):
            if page > 1:            # 이력 끝을 넘긴 페이지는 형식 오류가 아니라 '없음'으로 본다
                return []
            raise ValueError("isSuccess=false")
        items = body.get("result") or []
        rows = []
        for x in items:
            day = _confirmed(series, str(x["localTradedAt"]), now_utc)
            if day is None:
                continue
            date.fromisoformat(day)
            rows.append((day, float(str(x["closePrice"]).replace(",", ""))))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise GoldSkip(GOLD_SOURCE_FORMAT, f"{series.key} {type(e).__name__}") from e
    return rows


def clean(series: Series, rows: dict[str, float]) -> dict[str, float]:
    """범위 밖 행과 단발 이상치를 뺀다.
    단발 이상치 = 7일 이내 앞 행 대비 ±GOLD_JUMP_REJECT_PCT 초과이면서, 7일 이내 뒤 행이 다시 반대로 되돌아오는 행
    (뒤 행이 없으면 최신값이므로 앞 행 대비 초과만으로 제외). 수준 변화(되돌아오지 않음)와
    멀리 떨어진 행(5년 전 월 vs 최신 월) 사이는 비교하지 않는다 — 연쇄 제외 방지."""
    in_range = {}
    for day in sorted(rows):
        v = rows[day]
        if series.lo <= v <= series.hi:
            in_range[day] = v
        else:
            logger.warning("[GoldValue] %s %s 범위 밖 %s → 제외", series.key, day, v)
    days = sorted(in_range)
    lim = config.GOLD_JUMP_REJECT_PCT

    def near(a: str, b: str) -> bool:
        return abs((date.fromisoformat(b) - date.fromisoformat(a)).days) <= 7

    out: dict[str, float] = {}
    last_ok: str | None = None          # 앞 행 = 7일 이내 마지막 통과 행
    for i, day in enumerate(days):
        v = in_range[day]
        prev = last_ok if last_ok is not None and near(last_ok, day) else None
        nxt = days[i + 1] if i + 1 < len(days) and near(day, days[i + 1]) else None
        jump_in = prev is not None and abs(v / out[prev] - 1) * 100 > lim
        if jump_in and (nxt is None or abs(in_range[nxt] / v - 1) * 100 > lim):
            logger.warning("[GoldValue] %s %s 단발 이상치 %+.1f%% → 제외", series.key, day,
                           (v / out[prev] - 1) * 100)
            continue
        out[day] = v
        last_ok = day
    return out


def month_window(now_utc: datetime) -> tuple[str, str]:
    """(최신 완료 월, 60개월 전 같은 월) YYYY-MM."""
    today = now_utc.astimezone(KST).date()
    first = today.replace(day=1)
    last_done = first - timedelta(days=1)
    m1 = last_done.strftime("%Y-%m")
    m0 = f"{last_done.year - config.GOLD_LONG_YEARS:04d}-{last_done.month:02d}"
    return m1, m0


def sync_history(now_utc: datetime, http_get: Callable, store=None, sleep=time.sleep) -> dict[str, dict]:
    """시리즈별 확정 이력. store가 있으면 최근 1페이지만 받아 쌓고, 60개월 전 월이 비어 있으면 백필한다.
    store가 없으면(dry_run) 필요한 구간까지 페이지를 받아 메모리에서만 쓴다."""
    m1, m0 = month_window(now_utc)
    need_from = f"{m0}-01"
    hist: dict[str, dict] = {}
    calls = 0
    for s in ALL_SERIES:
        long_needed = s in LONG_SERIES
        rows = dict(fetch_page(s, 1, now_utc, http_get))
        calls += 1
        if store is not None:
            try:
                store.upsert_rows(s.key, rows)
                have = store.count_month(s.key, m0) if long_needed else config.GOLD_MIN_DAYS_PER_MONTH
            except Exception as e:  # noqa: BLE001
                raise GoldSkip(GOLD_DB, f"{s.key} {type(e).__name__}") from e
        else:
            have = 0 if long_needed else config.GOLD_MIN_DAYS_PER_MONTH
        if long_needed and have < config.GOLD_MIN_DAYS_PER_MONTH:
            page = 2
            while page <= config.GOLD_BACKFILL_MAX_PAGES and (not rows or min(rows) >= need_from):
                sleep(config.GOLD_PAGE_SLEEP_SEC)
                got = fetch_page(s, page, now_utc, http_get)
                calls += 1
                if not got:
                    break
                rows.update(got)
                if store is not None:
                    try:
                        store.upsert_rows(s.key, dict(got))
                    except Exception as e:  # noqa: BLE001
                        raise GoldSkip(GOLD_DB, f"{s.key} {type(e).__name__}") from e
                page += 1
            logger.info("[GoldValue] %s 백필 %d페이지, 최오래 %s", s.key, page - 1, min(rows) if rows else "-")
        if store is not None:
            try:
                since = need_from if long_needed else None
                rows = {**store.load(s.key, since, m1), **store.load_recent(s.key, config.GOLD_RECENT_DAYS),
                        **rows}
            except Exception as e:  # noqa: BLE001
                raise GoldSkip(GOLD_DB, f"{s.key} {type(e).__name__}") from e
        hist[s.key] = clean(s, rows)
    logger.info("[GoldValue] 수집 완료 호출 %d회", calls)
    return hist


# ── 계산 ────────────────────────────────────────────────────
def _at_or_before(series: dict[str, float], day: str, max_age: int) -> tuple[float, str] | None:
    keys = [k for k in series if k <= day]
    if not keys:
        return None
    k = max(keys)
    if (date.fromisoformat(day) - date.fromisoformat(k)).days > max_age:
        return None
    return series[k], k


def _month_avg(series: dict[str, float], month: str) -> float | None:
    vals = [v for k, v in series.items() if k.startswith(month)]
    if len(vals) < config.GOLD_MIN_DAYS_PER_MONTH:
        return None
    return sum(vals) / len(vals)


def _pct(a: float, b: float) -> float:
    return (b / a - 1) * 100


def grams_domestic(krx_krw_g: float, usdkrw: float, jpykrw100: float) -> dict[str, float]:
    a = config.GOLD_AMOUNTS
    return {"krw": a["krw"] / krx_krw_g, "usd": a["usd"] * usdkrw / krx_krw_g,
            "jpy": a["jpy"] * (jpykrw100 / 100) / krx_krw_g}


def grams_intl(gold_usd_oz: float, usdkrw: float, jpykrw100: float) -> dict[str, float]:
    a = config.GOLD_AMOUNTS
    usdjpy = usdkrw / (jpykrw100 / 100)
    return {"krw": a["krw"] / (gold_usd_oz * usdkrw) * config.OZ_G,
            "usd": a["usd"] / gold_usd_oz * config.OZ_G,
            "jpy": a["jpy"] / (gold_usd_oz * usdjpy) * config.OZ_G}


def compute(hist: dict[str, dict], now_utc: datetime) -> dict:
    """패널 값. 부족·지연이면 GoldSkip."""
    k, g, u, j = (hist.get(s.key) or {} for s in (KRX_GOLD, GC_GOLD, USDKRW, JPYKRW))
    today = now_utc.astimezone(KST).date()
    for s, rows in ((KRX_GOLD, k), (GC_GOLD, g), (USDKRW, u), (JPYKRW, j)):
        if not rows:
            raise GoldSkip(GOLD_INSUFFICIENT, f"{s.key} 0행")
        if (today - date.fromisoformat(max(rows))).days > config.GOLD_MAX_STALE_DAYS:
            raise GoldSkip(GOLD_STALE, f"{s.key} 마지막 {max(rows)}")

    # 5년 (월평균)
    m1, m0 = month_window(now_utc)
    avgs = {}
    for name, rows in (("krx", k), ("usdkrw", u), ("jpykrw", j)):
        for m in (m0, m1):
            v = _month_avg(rows, m)
            if v is None:
                raise GoldSkip(GOLD_INSUFFICIENT, f"{name} {m} 거래일 부족")
            avgs[(name, m)] = v
    then = grams_domestic(avgs[("krx", m0)], avgs[("usdkrw", m0)], avgs[("jpykrw", m0)])
    now = grams_domestic(avgs[("krx", m1)], avgs[("usdkrw", m1)], avgs[("jpykrw", m1)])
    long = {"from_month": m0, "to_month": m1,
            "gold_krw_per_g": {"then": round(avgs[("krx", m0)], 2), "now": round(avgs[("krx", m1)], 2)},
            "grams": {c: {"then": round(then[c], 2), "now": round(now[c], 2),
                          "pct": round(_pct(then[c], now[c]), 2)} for c in ("krw", "usd", "jpy")}}

    # 1개월 (국제 금 × 환율)
    t = max(g)
    t0 = (date.fromisoformat(t) - timedelta(days=config.GOLD_SHORT_DAYS)).isoformat()
    g1, g0 = _at_or_before(g, t, 0), _at_or_before(g, t0, config.GOLD_FX_MAX_GAP_DAYS)
    u1, j1 = _at_or_before(u, t, config.GOLD_FX_MAX_GAP_DAYS), _at_or_before(j, t, config.GOLD_FX_MAX_GAP_DAYS)
    u0, j0 = _at_or_before(u, t0, config.GOLD_FX_MAX_GAP_DAYS), _at_or_before(j, t0, config.GOLD_FX_MAX_GAP_DAYS)
    if None in (g1, g0, u1, j1, u0, j0):
        raise GoldSkip(GOLD_INSUFFICIENT, f"1개월 기준값 없음 {t0}~{t}")
    s0 = grams_intl(g0[0], u0[0], j0[0])
    s1 = grams_intl(g1[0], u1[0], j1[0])
    window = sorted(d for d in g if g0[1] <= d <= t)
    jumps = [d for a, d in zip(window, window[1:], strict=False)
             if abs(_pct(g[a], g[d])) >= config.GOLD_JUMP_WARN_PCT]
    if jumps:
        logger.warning("[GoldValue] 1개월 창 국제 금 급변(롤오버 후보) %s", jumps)
    short = {"from": g0[1], "to": t, "gold_usd_oz": {"then": g0[0], "now": g1[0]},
             "gold_usd_pct": round(_pct(g0[0], g1[0]), 2),
             "value_pct": {c: round(_pct(s0[c], s1[c]), 2) for c in ("usd", "jpy", "krw")},
             "jump_warning": jumps}
    return {"status": "ok", "version": config.VERSION, "long": long, "short": short}


def build_panel(now_utc: datetime, http_get: Callable | None = None, store=None, sleep=time.sleep) -> dict:
    """패널 dict. 실패는 {'status': 'skipped', 'reason', 'detail'} — 예외를 밖으로 내지 않는다(G-09)."""
    logger.info("[GoldValue] v%s 시작 | store=%s", config.VERSION, "on" if store is not None else "off")
    try:
        hist = sync_history(now_utc, http_get or requests.get, store, sleep)
        panel = compute(hist, now_utc)
        lg = panel["long"]["grams"]["krw"]
        logger.info("[GoldValue] 5년 1억 원 %.0fg → %.0fg (%+.2f%%)", lg["then"], lg["now"], lg["pct"])
        return panel
    except GoldSkip as s:
        logger.warning("[GoldValue] 패널 생략: %s %s", s.code, s.detail)
        return {"status": "skipped", "reason": s.code, "detail": s.detail}
    except Exception as e:  # noqa: BLE001 — 패널은 본 발행을 막지 않는다
        logger.exception("[GoldValue] 예상하지 못한 오류 — 패널 생략")
        return {"status": "skipped", "reason": type(e).__name__, "detail": str(e)[:200]}


# ── 표기 ────────────────────────────────────────────────────
CUR_LABEL = {"usd": "달러", "jpy": "엔", "krw": "원"}


def fmt_pct(x: float) -> str:
    """공개 표기 소수 1자리. 마이너스는 슬리브 본문과 같은 ASCII '-'(R5, 회장 승인 11-09 16:39)."""
    return f"{x:+.1f}%"


def won_position(value_pct: dict) -> str:
    """원의 위치만 한 문장으로 말한다(순위 나열 없음). 차이 0.2%p 미만은 '비슷'."""
    w = value_pct["krw"]
    verdict = {}
    for c in ("usd", "jpy"):
        d = w - value_pct[c]
        verdict[c] = "비슷" if abs(d) < config.GOLD_SIMILAR_PP else ("강" if d > 0 else "약")
    if verdict["usd"] == verdict["jpy"]:
        v = verdict["usd"]
        return "원은 달러·엔과 비슷했습니다" if v == "비슷" else f"원은 달러·엔보다 {v}했습니다"

    def part(c):
        v = verdict[c]
        return f"{CUR_LABEL[c]}와 비슷했고" if v == "비슷" else f"{CUR_LABEL[c]}보다 {v}했고"
    first, second = part("usd"), verdict["jpy"]
    tail = "엔과 비슷했습니다" if second == "비슷" else f"엔보다 {second}했습니다"
    return f"원은 {first} {tail}"


def _month_label(m: str) -> str:
    return f"{m[2:4]}년 {int(m[5:])}월"


def _month_short(m: str) -> str:
    return f"{m[2:4]}.{m[5:7]}"


def short_line(p: dict) -> str:
    v = p["short"]["value_pct"]
    return " · ".join(f"{CUR_LABEL[c]} {fmt_pct(v[c])}" for c in ("usd", "jpy", "krw"))


SOURCE_NOTE = "국내 금 KRX 금시장(원/g) 월평균 · 1개월은 국제 금 선물(근월물)×환율 · 세금·수수료 제외"
PLUS_NOTE = "※ +는 같은 돈으로 살 수 있는 금이 늘었다는 뜻"


def panel_telegram(p: dict) -> list[str]:
    """가독성 최종 검토 반영(R1~R6): 공백 정렬 제거, 기준 월 명시, '금 기준' 명시, 줄 분리."""
    lg, sh = p["long"], p["short"]
    g = lg["grams"]
    d0, d1 = sh["from"], sh["to"]
    return [
        "🪙 <b>금으로 잰 화폐가치</b>",
        "1억 원으로 살 수 있는 금",
        f"5년 전({_month_short(lg['from_month'])} 평균) {g['krw']['then']:,.0f}g → "
        f"최근({_month_short(lg['to_month'])} 평균) {g['krw']['now']:,.0f}g ({fmt_pct(g['krw']['pct'])})",
        f"· 1만 달러  {g['usd']['then']:,.1f}g → {g['usd']['now']:,.1f}g ({fmt_pct(g['usd']['pct'])})",
        f"· 100만 엔  {g['jpy']['then']:,.1f}g → {g['jpy']['now']:,.1f}g ({fmt_pct(g['jpy']['pct'])})",
        "",
        f"최근 1개월 금 기준 가치 ({d0[5:7]}/{d0[8:]} → {d1[5:7]}/{d1[8:]})",
        f"{short_line(p)} — {won_position(sh['value_pct'])}",
        f"{PLUS_NOTE} · 같은 기간 금값(달러) {fmt_pct(sh['gold_usd_pct'])}",
        f"<i>{SOURCE_NOTE}</i>",
    ]


def panel_facebook(p: dict) -> list[str]:
    lg, sh = p["long"], p["short"]
    g = lg["grams"]
    return [
        "🪙 금으로 잰 화폐가치",
        f"1억 원으로 살 수 있는 금이 5년 새 {g['krw']['then']:,.0f}g({_month_label(lg['from_month'])} 평균)에서 "
        f"{g['krw']['now']:,.0f}g({_month_label(lg['to_month'])} 평균)으로 줄었습니다({fmt_pct(g['krw']['pct'])}).",
        f"같은 기간 1만 달러는 {fmt_pct(g['usd']['pct'])}, 100만 엔은 {fmt_pct(g['jpy']['pct'])}였습니다.",
        f"최근 1개월 금 기준 가치: {short_line(p)} (같은 기간 금값(달러) {fmt_pct(sh['gold_usd_pct'])})",
        f"※ {SOURCE_NOTE}",
    ]


def panel_x(p: dict) -> list[str]:
    lg = p["long"]
    g = lg["grams"]["krw"]
    return [
        f"🪙 금으로 재면 1억 원: {g['then']:,.0f}g({_month_short(lg['from_month'])}) → "
        f"{g['now']:,.0f}g({_month_short(lg['to_month'])}), {fmt_pct(g['pct'])}",
        f"1개월 금 기준 가치: {short_line(p)}",
    ]


# ── 이력 저장 ────────────────────────────────────────────────
class GoldStore:
    """Supabase gold_value_daily (db/gold_value_schema.sql). PK(day, series)."""
    TABLE = "gold_value_daily"

    def __init__(self, client=None):
        if client is None:
            from db.supabase_client import get_client
            client = get_client()
        self.client = client

    def upsert_rows(self, series: str, rows: dict[str, float]) -> None:
        if not rows:
            return
        now = datetime.now(UTC).isoformat()
        payload = [{"day": d, "series": series, "close": v, "source": "naver_marketindex", "fetched_at": now}
                   for d, v in sorted(rows.items())]
        self.client.table(self.TABLE).upsert(payload, on_conflict="day,series").execute()

    def count_month(self, series: str, month: str) -> int:
        start, end = _month_bounds(month)
        res = (self.client.table(self.TABLE).select("day").eq("series", series)
               .gte("day", start).lt("day", end).execute())
        return len(res.data or [])

    def _select(self, series: str, start: str, end: str | None) -> dict[str, float]:
        q = self.client.table(self.TABLE).select("day,close").eq("series", series).gte("day", start)
        if end:
            q = q.lt("day", end)
        res = q.order("day").limit(1000).execute()
        return {str(r["day"])[:10]: float(r["close"]) for r in (res.data or [])}

    def load(self, series: str, since: str | None, m1: str) -> dict[str, float]:
        """60개월 전 월과 최신 완료 월만 읽는다(행 수 1,000 제한 회피)."""
        if since is None:
            return {}
        out = {}
        for m in (since[:7], m1):
            out.update(self._select(series, *_month_bounds(m)))
        return out

    def load_recent(self, series: str, days: int) -> dict[str, float]:
        start = (datetime.now(KST).date() - timedelta(days=days)).isoformat()
        return self._select(series, start, None)


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return f"{month}-01", nxt.isoformat()


def history_by_month(rows: dict[str, float]) -> dict[str, int]:
    """진단용: 월별 행 수."""
    acc: dict[str, int] = defaultdict(int)
    for d in rows:
        acc[d[:7]] += 1
    return dict(acc)
