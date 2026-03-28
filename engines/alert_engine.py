"""
engines/alert_engine.py
========================
Alert 감지 엔진 — 2번(SPY 급락) + 5번(Fed RSS) 조합 구현.

감지 항목:
  1. VIX 급등 Alert         (단독 L1~L2)
  2. SPY 급락 Alert          (단독 L1~L2)
  3. Oil Shock Alert          (단독 L1)
  4. Fed 충격 Alert           (SPY 급락 + Fed RSS 키워드 조합 → L2~L3)
  5. 복합 위기 Alert          (VIX + SPY + US10Y 동시 악화 → L3)

등급:
  L1 — 주의    (단일 지표 임계값 돌파)
  L2 — 경고    (복합 조건 또는 심각 단일 지표)
  L3 — 위기    (다중 지표 동시 악화)

출력: List[AlertSignal] — 발송 대상 Alert 목록
"""
import logging
from typing import List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ─── Alert 임계값 ───────────────────────────────────────────
# VIX
VIX_L1 = 28.0   # VIX 28 이상 → L1
VIX_L2 = 35.0   # VIX 35 이상 → L2
VIX_SURGE_PCT = 15.0  # 전일 대비 15% 급등

# SPY
SPY_L1 = -2.5   # SPY -2.5% 이하 → L1
SPY_L2 = -4.0   # SPY -4.0% 이하 → L2
SPY_L3 = -6.0   # SPY -6.0% 이하 → L3 (서킷브레이커 근접)

# Oil
OIL_SURGE_PCT = 4.0   # 하루 4% 이상 급등
OIL_SHOCK_PRICE = 100.0  # $100 돌파

# US10Y 급변
US10Y_SURGE = 0.15  # 하루 0.15% 이상 변동

# Fed RSS 키워드
FED_KEYWORDS = [
    "federal reserve", "fed rate", "fomc",
    "rate hike", "rate cut", "powell",
    "emergency rate", "basis points",
    "interest rate decision", "rate decision",
    "fed raises", "fed cuts", "bps",
]


@dataclass
class AlertSignal:
    alert_type: str          # "VIX" / "SPY" / "OIL" / "FED_SHOCK" / "CRISIS"
    level: str               # "L1" / "L2" / "L3"
    reason: str              # 발생 이유 (트윗 본문용)
    snapshot: dict           # 현재 시장 스냅샷
    etf_hints: List[str] = field(default_factory=list)  # 주목 ETF
    avoid_etfs: List[str] = field(default_factory=list)  # 회피 ETF
    fed_detected: bool = False  # Fed 키워드 감지 여부


def _detect_fed_keywords(news_result: dict) -> bool:
    """
    RSS 수집 결과에서 Fed 관련 키워드 감지.
    source_detail에서 헤드라인 직접 확인.
    """
    # news_result의 source_detail에서 헤드라인 재확인
    details = news_result.get("source_detail", [])
    all_headlines = []
    for d in details:
        all_headlines.extend(d.get("headlines", []))

    text = " ".join(all_headlines).lower()
    count = sum(1 for kw in FED_KEYWORDS if kw in text)

    if count >= 2:
        logger.info(f"[AlertEngine] Fed 키워드 감지: {count}건")
        return True
    return False


def _vix_alert(snapshot: dict, prev_snapshot: Optional[dict]) -> Optional[AlertSignal]:
    """VIX 급등 감지"""
    vix = snapshot.get("vix", 0)
    if vix <= 0:
        return None

    # 등급 판정
    if vix >= VIX_L2:
        level = "L2"
        reason = f"VIX {vix:.1f} — 극단적 공포 구간 진입"
        hints = ["TLT", "SPYM"]
        avoids = ["QQQM", "XLK"]
    elif vix >= VIX_L1:
        level = "L1"
        reason = f"VIX {vix:.1f} — 공포 지수 경계 구간"
        hints = ["TLT", "ITA"]
        avoids = ["QQQM"]
    else:
        return None

    # 전일 대비 급등 체크
    if prev_snapshot:
        prev_vix = prev_snapshot.get("vix", vix)
        if prev_vix > 0:
            surge_pct = (vix - prev_vix) / prev_vix * 100
            if surge_pct >= VIX_SURGE_PCT and level == "L1":
                level = "L2"
                reason = f"VIX {vix:.1f} (+{surge_pct:.0f}%) — 공포 지수 급등"

    return AlertSignal(
        alert_type="VIX",
        level=level,
        reason=reason,
        snapshot=snapshot,
        etf_hints=hints,
        avoid_etfs=avoids,
    )


def _spy_alert(snapshot: dict) -> Optional[AlertSignal]:
    """SPY 급락 감지"""
    spy = snapshot.get("sp500", 0)

    if spy <= SPY_L3:
        return AlertSignal(
            alert_type="SPY",
            level="L3",
            reason=f"SPY {spy:.1f}% — 서킷브레이커 근접 급락",
            snapshot=snapshot,
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK", "XLE"],
        )
    elif spy <= SPY_L2:
        return AlertSignal(
            alert_type="SPY",
            level="L2",
            reason=f"SPY {spy:.1f}% — 시장 급락 경보",
            snapshot=snapshot,
            etf_hints=["TLT", "ITA"],
            avoid_etfs=["QQQM", "XLK"],
        )
    elif spy <= SPY_L1:
        return AlertSignal(
            alert_type="SPY",
            level="L1",
            reason=f"SPY {spy:.1f}% — 주의 급락",
            snapshot=snapshot,
            etf_hints=["SPYM", "TLT"],
            avoid_etfs=["QQQM"],
        )
    return None


def _oil_alert(snapshot: dict, prev_snapshot: Optional[dict]) -> Optional[AlertSignal]:
    """Oil Shock 감지"""
    oil = snapshot.get("oil", 0)
    if oil <= 0:
        return None

    price_shock = oil >= OIL_SHOCK_PRICE
    surge_shock = False

    if prev_snapshot:
        prev_oil = prev_snapshot.get("oil", oil)
        if prev_oil > 0:
            surge_pct = (oil - prev_oil) / prev_oil * 100
            if surge_pct >= OIL_SURGE_PCT:
                surge_shock = True

    if price_shock or surge_shock:
        reason = f"WTI ${oil:.1f}"
        if price_shock:
            reason += " — $100 돌파"
        if surge_shock:
            prev_oil = prev_snapshot.get("oil", oil) if prev_snapshot else oil
            pct = (oil - prev_oil) / prev_oil * 100 if prev_oil else 0
            reason += f" (+{pct:.1f}% 급등)"

        return AlertSignal(
            alert_type="OIL",
            level="L2" if price_shock else "L1",
            reason=reason,
            snapshot=snapshot,
            etf_hints=["XLE", "ITA"],
            avoid_etfs=["QQQM", "TLT"],
        )
    return None


def _fed_shock_alert(
    spy_alert: Optional[AlertSignal],
    fed_detected: bool,
    snapshot: dict,
) -> Optional[AlertSignal]:
    """
    Fed 충격 Alert — SPY 급락 + Fed RSS 키워드 AND 조건.
    SPY만 있으면 일반 급락, Fed 키워드까지 있으면 Fed 충격으로 격상.
    """
    if not fed_detected:
        return None
    if spy_alert is None:
        return None

    # SPY 등급을 한 단계 올려서 Fed 충격으로 처리
    spy = snapshot.get("sp500", 0)
    vix = snapshot.get("vix", 0)

    if spy <= SPY_L2 or (spy <= SPY_L1 and vix >= VIX_L1):
        level = "L3"
        reason = f"SPY {spy:.1f}% + Fed 뉴스 급증 — Fed 발표 충격 의심"
    else:
        level = "L2"
        reason = f"SPY {spy:.1f}% + Fed 관련 키워드 감지 — 금리 관련 시장 충격"

    return AlertSignal(
        alert_type="FED_SHOCK",
        level=level,
        reason=reason,
        snapshot=snapshot,
        etf_hints=["TLT", "SPYM"],
        avoid_etfs=["QQQM", "XLK"],
        fed_detected=True,
    )


def _crisis_alert(snapshot: dict) -> Optional[AlertSignal]:
    """
    복합 위기 Alert — VIX + SPY + US10Y 동시 악화.
    """
    vix = snapshot.get("vix", 0)
    spy = snapshot.get("sp500", 0)
    us10y = snapshot.get("us10y", 4.0)

    crisis_count = 0
    if vix >= VIX_L2:
        crisis_count += 1
    if spy <= SPY_L2:
        crisis_count += 1
    if us10y >= 4.8:
        crisis_count += 1

    if crisis_count >= 3:
        return AlertSignal(
            alert_type="CRISIS",
            level="L3",
            reason=f"SPY {spy:.1f}% | VIX {vix:.1f} | US10Y {us10y:.2f}% — 복합 위기 동시 발생",
            snapshot=snapshot,
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK", "XLE"],
        )
    elif crisis_count == 2:
        return AlertSignal(
            alert_type="CRISIS",
            level="L2",
            reason=f"SPY {spy:.1f}% | VIX {vix:.1f} — 복합 위험 신호 감지",
            snapshot=snapshot,
            etf_hints=["TLT", "ITA"],
            avoid_etfs=["QQQM", "XLK"],
        )
    return None


def run_alert_engine(
    snapshot: dict,
    news_result: dict,
    prev_snapshot: Optional[dict] = None,
) -> List[AlertSignal]:
    """
    전체 Alert 감지 실행.

    Args:
        snapshot: 현재 시장 스냅샷
        news_result: collect_news_sentiment() 결과 (RSS 포함)
        prev_snapshot: 직전 실행 스냅샷 (급변 감지용, 없으면 None)

    Returns:
        발송 대상 AlertSignal 리스트 (우선순위 정렬)
    """
    logger.info("[AlertEngine] Alert 감지 시작")
    alerts: List[AlertSignal] = []

    # 1. Fed 키워드 감지 (공통)
    fed_detected = _detect_fed_keywords(news_result)

    # 2. 개별 Alert 감지
    vix_sig   = _vix_alert(snapshot, prev_snapshot)
    spy_sig   = _spy_alert(snapshot)
    oil_sig   = _oil_alert(snapshot, prev_snapshot)

    # 3. 복합 위기 (가장 먼저 체크 — 최고 등급)
    crisis_sig = _crisis_alert(snapshot)
    if crisis_sig:
        alerts.append(crisis_sig)

    # 4. Fed 충격 (SPY + Fed AND 조건)
    fed_sig = _fed_shock_alert(spy_sig, fed_detected, snapshot)
    if fed_sig:
        alerts.append(fed_sig)
    elif spy_sig:
        # Fed 미감지 시 일반 SPY Alert
        alerts.append(spy_sig)

    # 5. VIX, Oil (독립 Alert)
    if vix_sig and not crisis_sig:  # 복합위기에 포함되지 않은 경우
        alerts.append(vix_sig)
    if oil_sig:
        alerts.append(oil_sig)

    # 등급 내림차순 정렬 (L3 > L2 > L1)
    level_order = {"L3": 0, "L2": 1, "L1": 2}
    alerts.sort(key=lambda a: level_order.get(a.level, 9))

    if alerts:
        logger.info(f"[AlertEngine] {len(alerts)}개 Alert 감지: "
                    f"{[(a.alert_type, a.level) for a in alerts]}")
    else:
        logger.info("[AlertEngine] 이상 없음 — Alert 없음")

    return alerts
