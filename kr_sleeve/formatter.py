"""
kr_sleeve/formatter.py — KR 슬리브 발행 문구 (DN2026_0108, 회장 승인 템플릿 11-09)

  X        : 국내 2종 + k 한 줄, 기준일. 첫 줄 5종·해시태그 풀 무작위(STD-PUB-10).
             X 계정은 프리미엄(장문 허용)이라 길이 차단·자르기 없음(회장 지시 11-09).
             가중 길이는 참고값으로만 기록
  Telegram : HTML. 슬리브 구성 · 합산 8종 · 실효 미국 노출 · 기준일 2개 · 통화 주석
  Facebook : 평문 별도 요약
  운영자    : skipped·발행 실패 알림(종목코드·정식명 포함 — 비공개 방)
공개 문구에는 마스킹 표기(P**S, Ti**ER)만 쓰고 종목코드·정식 브랜드명은 넣지 않는다(회장 지시 11-09).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from kr_sleeve import config

KST = timezone(timedelta(hours=9))
_ET = ZoneInfo("America/New_York")
DISCLAIMER = "⚠️ 모델 비중 · 투자 참고 정보, 투자 권유 아님"

_X_OPENERS = (
    "🇰🇷 KR Sleeve",
    "🧭 국내 ETF 배분",
    "📌 오늘의 국내 슬리브",
    "🇰🇷 국내 ETF 슬리브",
    "🧩 KR 슬리브 업데이트",
)
_TAG_POOL = (
    "#국내ETF #자산배분",
    "#ETF투자 #포트폴리오",
    "#국내ETF #투자메모",
    "#자산배분 #ETF전략",
    "#고배당 #전세계주식",
)
_REGIME_TAG = {
    "Risk-On": "#RiskOn", "Risk-Off": "#RiskOff", "Oil Shock": "#OilShock",
    "Liquidity Crisis": "#유동성위기", "Recession Risk": "#경기침체",
    "Stagflation Risk": "#스태그플레이션", "AI Bubble": "#AI버블", "Transition": "#전환구간",
}
_RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}


# ── 공통 ────────────────────────────────────────────────────
def x_weighted_length(text: str) -> int:
    """X 가중 글자수(보수적): 라틴·일반 구두점 1, 그 외(한글·이모지 등) 코드포인트당 2."""
    n = 0
    for ch in text:
        cp = ord(ch)
        light = cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F or 0x2032 <= cp <= 0x2037
        n += 1 if light else 2
    return n


def us_session_label(morning_ts: datetime) -> str:
    """morning 시각 기준 직전 미국 정규장 날짜(MM/DD). 미국 공휴일은 반영하지 않는다(주말만 보정)."""
    et = morning_ts.astimezone(_ET)
    d = et.date() if (et.hour, et.minute) >= (16, 0) else et.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%m/%d")


def _mmdd(iso_day: str) -> str:
    return f"{iso_day[5:7]}/{iso_day[8:10]}"


def _kr_total(result: dict, etf: config.KrEtf) -> int:
    return result["total_allocation"][etf.code]


def _kr_date(quotes: dict) -> str:
    days = sorted({q["as_of"] for q in quotes.values()})
    return " / ".join(_mmdd(d) for d in days)


# ── X ───────────────────────────────────────────────────────
def format_x(ctx: dict, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    res, regime = ctx["result"], ctx["regime"]
    tags = " ".join(t for t in (rng.choice(_TAG_POOL), _REGIME_TAG.get(regime, "")) if t)
    lines = [
        f"{rng.choice(_X_OPENERS)}  |  {regime}",
        "",
        f"국내 ETF 슬리브 {res['k_pct']}% 기준 모델 비중",
    ]
    for etf in config.KR_ETFS:
        lines.append(f"▪ {etf.display} {_kr_total(res, etf)}%")
    lines += [
        "",
        f"🗓 레짐: 미국 {ctx['us_session']} 마감 · 가격: 국내 {_kr_date(ctx['quotes'])} 종가",
        DISCLAIMER,
        "",
        tags,
    ]
    return "\n".join(lines)


# ── Telegram (HTML) ─────────────────────────────────────────
def format_telegram(ctx: dict) -> str:
    res, quotes = ctx["result"], ctx["quotes"]
    total = res["total_allocation"]
    lines = [
        "🇰🇷 <b>KR Sleeve</b>  |  국내 ETF 2종 서브 배분",
        "",
        f"{_RISK_EMOJI.get(ctx['risk_level'], '⚪')} 레짐 <b>{ctx['regime']}</b>"
        f"  |  리스크 {ctx['risk_level']}",
        "<i>레짐·미국 비중은 오늘 morning 분석 결과를 그대로 사용</i>",
        "",
        f"📐 <b>슬리브 구성</b> (KR 비중 k = {res['k_pct']}%)",
    ]
    for etf in config.KR_ETFS:
        q = quotes[etf.code]
        lines.append(f"▪ {etf.display}  <b>{res['internal_a'][etf.code]}%</b>  →  전체 {total[etf.code]}%")
        lines.append(f"   ₩{q['close']:,.0f}  (전일 대비 {q['change_pct']:+.1f}%)")
    us_line = " · ".join(f"{e} {total[e]}" for e in config.US_ETFS)
    kr_line = " · ".join(f"{etf.short} {total[etf.code]}" for etf in config.KR_ETFS)
    share = round(config.TOTAL_WORLD_US_SHARE * 100)
    lines += [
        "",
        "📊 <b>합산 8종 모델 비중</b> (원화 평가 기준)",
        us_line,
        kr_line,
        "",
        f"🔎 <b>실효 미국 주식 노출 ≈ {res['lookthrough_us_pct']:.0f}%</b>",
        f"<i>{config.ETF_TOTAL_WORLD.short} 토탈월드의 미국 비중 약 {share}% 포함(운용사 공시 근사치)</i>",
        "",
        f"🗓 레짐 기준: 미국 {ctx['us_session']} 마감  |  국내 가격: {_kr_date(quotes)} 종가",
        f"ℹ️ morning 미국 6종 비중과 다른 이유: KR 슬리브 {res['k_pct']}%를 더한 합산 모델",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


# ── Facebook (평문 요약) ─────────────────────────────────────
def format_facebook(ctx: dict) -> str:
    res = ctx["result"]
    total = res["total_allocation"]
    us_sum = sum(total[e] for e in config.US_ETFS)
    us_detail = " · ".join(f"{e} {total[e]}" for e in config.US_ETFS)
    share = round(config.TOTAL_WORLD_US_SHARE * 100)
    lines = [
        "🇰🇷 KR Sleeve | 국내 ETF 2종 서브 배분",
        "",
        f"오늘 레짐: {ctx['regime']} (미국 {ctx['us_session']} 마감 기준)",
        f"국내 ETF 슬리브 {res['k_pct']}%를 더한 모델 비중입니다.",
        "",
    ]
    for etf in config.KR_ETFS:
        lines.append(f"▪ {etf.display} {total[etf.code]}%")
    lines += [
        f"▪ 미국 ETF 6종 합계 {us_sum}% ({us_detail})",
        "",
        f"{config.ETF_TOTAL_WORLD.short} 토탈월드는 미국 주식 비중이 약 {share}%라, "
        f"이를 포함한 실효 미국 주식 노출은 약 {res['lookthrough_us_pct']:.0f}%입니다.",
        f"국내 가격은 {_kr_date(ctx['quotes'])} 종가, 원화 평가 기준입니다.",
        "",
        DISCLAIMER,
        "#국내ETF #자산배분",
    ]
    return "\n".join(lines)


# ── 운영자 방 (비공개) ───────────────────────────────────────
def format_operator(status: str, code: str, detail: str, meta: dict) -> str:
    names = ", ".join(f"{e.internal_name}({e.code})" for e in config.KR_ETFS)
    return "\n".join([
        f"⚠️ <b>[KRSleeve] v{meta.get('version')} {status.upper()}</b> | mode={meta.get('mode')} "
        f"| run_id={meta.get('run_id') or '-'}",
        f"사유: {code}" + (f" — {detail[:200]}" if detail else ""),
        f"입력: core_data {meta.get('input_timestamp') or '-'} / 레짐 {meta.get('regime') or '-'}",
        f"대상: {names}",
        "조치: 오늘 KR 발행 없음 또는 해당 채널 확인 필요. 미국 6종 morning 발행은 영향 없음",
    ])
