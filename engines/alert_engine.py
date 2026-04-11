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
  6. PCR 극단값 Alert         (옵션 시장 극단 심리 → L1)  ← v1.0.0 신규
  7. Crypto Basis Alert       (BTC 백워데이션 극단 → L1)  ← v1.0.0 신규

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

VERSION = "1.0.0"

# ─── Alert 임계값 ───────────────────────────────────────────
# VIX
VIX_L1 = 28.0   # VIX 28 이상 → L1
VIX_L2 = 35.0   # VIX 35 이상 → L2
VIX_SURGE_PCT = 15.0  # 전일 대비 15% 급등

# VIX 프리미엄 레벨 (유료 채널 세분화)
VIX_PREMIUM_LEVELS = [20, 25, 30, 35]

# VIX 카운트다운 레벨 (하루 1회, 공포구간 진입 전 단계별 경고)
VIX_COUNTDOWN_LEVELS = [25, 27, 29]

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

# ── v1.0.0 신규: PCR (Put/Call Ratio) 임계값 ────────────────
PCR_EXTREME_FEAR  = 1.5   # PCR > 1.5 → 극단 공포 (헤지 폭증) → L1
PCR_EXTREME_GREED = 0.5   # PCR < 0.5 → 극단 탐욕 (콜 폭증)  → L1

# ── v1.0.0 신규: Crypto Basis 임계값 ─────────────────────────
CRYPTO_BASIS_BACKWARDATION = -1.0   # basis_spread < -1.0% → 극단 백워데이션 → L1


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


# ──────────────────────────────────────────────────────────────
# B-5: ETF 랭킹 변화 Alert (2026-04-01 추가)
# ──────────────────────────────────────────────────────────────

def _etf_rank_alert(
    rank_change: Optional[dict],
    signal_diff_result: Optional[dict],
    snapshot: dict,
) -> Optional[AlertSignal]:
    """
    [B-5] ETF 랭킹 변화 Alert

    등급 분류:
      - Top1 변경 → L2 (전략 전환 필요)
      - Top3 내 ETF 교체 → L1 (주의 관찰)
      - Top1 유지 + 하위만 변동 → 발송 안함

    Args:
        rank_change:      rank_tracker.detect_rank_change() 결과
        signal_diff_result: signal_diff.compute_signal_diff() 결과
        snapshot:         현재 시장 스냅샷
    """
    if rank_change is None:
        return None

    top1_changed = rank_change.get("top1_changed", False)
    moved_up = rank_change.get("moved_up", [])
    moved_down = rank_change.get("moved_down", [])
    old_top1 = rank_change.get("old_top1", "—")
    new_top1 = rank_change.get("new_top1", "—")

    # ── Top1 변경 → L2 ──
    if top1_changed:
        # 원인 요약 (signal_diff 결과 활용)
        cause = ""
        if signal_diff_result and signal_diff_result.get("summary"):
            cause = f" | 원인: {signal_diff_result['summary']}"

        return AlertSignal(
            alert_type="ETF_RANK",
            level="L2",
            reason=(
                f"ETF 1위 전환: {old_top1} → {new_top1}{cause}"
            ),
            snapshot=snapshot,
            etf_hints=[new_top1],
            avoid_etfs=[old_top1] if old_top1 != "—" else [],
        )

    # ── Top3 내 교체 있는지 확인 → L1 ──
    top3_moved = any(
        m["to"] <= 3 or m["from"] <= 3
        for m in moved_up + moved_down
    )

    if top3_moved:
        up_names = [m["etf"] for m in moved_up if m["to"] <= 3]
        down_names = [m["etf"] for m in moved_down if m["from"] <= 3]

        cause = ""
        if signal_diff_result and signal_diff_result.get("summary"):
            cause = f" | 원인: {signal_diff_result['summary']}"

        parts = []
        if up_names:
            parts.append(f"상승: {','.join(up_names)}")
        if down_names:
            parts.append(f"하락: {','.join(down_names)}")

        return AlertSignal(
            alert_type="ETF_RANK",
            level="L1",
            reason=f"ETF Top3 변화 — {' / '.join(parts)}{cause}",
            snapshot=snapshot,
            etf_hints=up_names,
            avoid_etfs=down_names,
        )

    # 하위(4~6위)만 변동 → 발송 안함
    return None


# ──────────────────────────────────────────────────────────────
# B-6: 레짐 전환 Alert (2026-04-01 추가)
# ──────────────────────────────────────────────────────────────

def _regime_change_alert(
    regime_change: Optional[dict],
    signal_diff_result: Optional[dict],
    score_diff_result: Optional[dict],
    snapshot: dict,
) -> Optional[AlertSignal]:
    """
    [B-6] 레짐 전환 Alert

    등급 분류:
      - 안전→위험 방향 (direction=danger) → L2
      - 위험→안전 방향 (direction=recovery) → L1
      - Shock 레짐 진입 → L2
      - Risk Level만 변경 (레짐 동일) → L1

    Args:
        regime_change:     regime_tracker.detect_regime_change() 결과
        signal_diff_result: signal_diff.compute_signal_diff() 결과
        score_diff_result:  signal_diff.compute_score_diff() 결과
        snapshot:          현재 시장 스냅샷
    """
    if regime_change is None:
        return None

    old_regime = regime_change.get("old_regime", "—")
    new_regime = regime_change.get("new_regime", "—")
    old_risk = regime_change.get("old_risk_level", "—")
    new_risk = regime_change.get("new_risk_level", "—")
    direction = regime_change.get("direction", "danger")
    regime_changed = regime_change.get("regime_changed", False)
    risk_changed = regime_change.get("risk_changed", False)

    # ── 원인 요약 ──
    cause = ""
    if signal_diff_result and signal_diff_result.get("summary"):
        cause = f" | 원인: {signal_diff_result['summary']}"

    # ── Shock 레짐 진입 → L2 ──
    shock_regimes = {"Oil Shock", "Liquidity Crisis", "Crisis Regime"}
    if regime_changed and new_regime in shock_regimes:
        # 어떤 ETF를 주목/회피할지 레짐 기반 판단
        hints, avoids = _regime_etf_hints(new_regime)
        return AlertSignal(
            alert_type="REGIME_CHANGE",
            level="L2",
            reason=f"Shock 레짐 진입: {old_regime} → {new_regime}{cause}",
            snapshot=snapshot,
            etf_hints=hints,
            avoid_etfs=avoids,
        )

    # ── 레짐 전환 (danger 방향) → L2 ──
    if regime_changed and direction == "danger":
        hints, avoids = _regime_etf_hints(new_regime)
        risk_str = f" | Risk: {old_risk}→{new_risk}" if risk_changed else ""
        return AlertSignal(
            alert_type="REGIME_CHANGE",
            level="L2",
            reason=f"레짐 전환: {old_regime} → {new_regime}{risk_str}{cause}",
            snapshot=snapshot,
            etf_hints=hints,
            avoid_etfs=avoids,
        )

    # ── 레짐 전환 (recovery 방향) → L1 ──
    if regime_changed and direction == "recovery":
        hints, avoids = _regime_etf_hints(new_regime)
        risk_str = f" | Risk: {old_risk}→{new_risk}" if risk_changed else ""
        return AlertSignal(
            alert_type="REGIME_CHANGE",
            level="L1",
            reason=f"레짐 회복: {old_regime} → {new_regime}{risk_str}{cause}",
            snapshot=snapshot,
            etf_hints=hints,
            avoid_etfs=avoids,
        )

    # ── 레짐 동일 + Risk Level만 변경 → L1 ──
    if not regime_changed and risk_changed:
        return AlertSignal(
            alert_type="REGIME_CHANGE",
            level="L1",
            reason=f"Risk Level 변경: {old_risk} → {new_risk} (레짐 {new_regime} 유지){cause}",
            snapshot=snapshot,
        )

    return None


def _regime_etf_hints(regime: str):
    """레짐별 주목/회피 ETF 반환"""
    _HINTS = {
        "Risk-On":          (["QQQM", "XLK"], []),
        "Risk-Off":         (["TLT", "ITA"], ["QQQM", "XLK"]),
        "Oil Shock":        (["XLE", "ITA"], ["QQQM", "TLT"]),
        "Liquidity Crisis": (["TLT", "SPYM"], ["QQQM", "XLK"]),
        "Recession Risk":   (["TLT", "SPYM"], ["QQQM", "XLE"]),
        "Stagflation Risk": (["XLE", "ITA"], ["QQQM", "TLT"]),
        "Crisis Regime":    (["TLT", "SPYM"], ["QQQM", "XLK", "XLE"]),
        "Transition":       ([], []),
    }
    return _HINTS.get(regime, ([], []))


def _pcr_alert(signals: dict, snapshot: dict) -> Optional[AlertSignal]:
    """
    [v1.0.0] PCR 극단값 Alert — 옵션 시장 심리 극단 감지.

    PCR > 1.5 : 극단 공포 — 풋 옵션 폭증, 패닉 헤징 신호
    PCR < 0.5 : 극단 탐욕 — 콜 옵션 폭증, 과열 신호

    과거 사례: PCR > 1.5는 2020-03, 2022-09~10 저점권과 일치
               PCR < 0.5는 2021-11 고점권과 일치
    """
    pcr = signals.get("pcr_value", 0) or 0
    state = signals.get("pcr_state", "") or ""

    if pcr <= 0:
        return None

    if pcr > PCR_EXTREME_FEAR:
        return AlertSignal(
            alert_type="PCR_EXTREME",
            level="L1",
            reason=(
                f"PCR {pcr:.2f} ({state}) — 극단 공포. "
                f"풋 옵션 폭증, 시장 패닉 헤징 구간"
            ),
            snapshot=snapshot,
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK"],
        )

    if 0 < pcr < PCR_EXTREME_GREED:
        return AlertSignal(
            alert_type="PCR_EXTREME",
            level="L1",
            reason=(
                f"PCR {pcr:.2f} ({state}) — 극단 탐욕. "
                f"콜 옵션 폭증, 단기 과열 주의"
            ),
            snapshot=snapshot,
            etf_hints=[],
            avoid_etfs=[],
        )

    return None


def _crypto_basis_alert(signals: dict, snapshot: dict) -> Optional[AlertSignal]:
    """
    [v1.0.0] Crypto Basis 극단 백워데이션 Alert.

    basis_spread < -1.0% : BTC 현물 수요가 선물을 압도하는 극단 상황.
    현물 패닉 매도 또는 선물 롤오버 위기를 의미.
    2022-11 FTX 붕괴, 2020-03 COVID 급락 직전에 관측된 패턴.

    단, 백워데이션이 항상 하락 신호는 아님 (강세장에서 현물 수급이 강할 때도 발생).
    참고 정보로만 제공.
    """
    spread = signals.get("crypto_basis_spread")
    state  = signals.get("crypto_basis_state", "") or ""

    if spread is None:
        return None

    if spread < CRYPTO_BASIS_BACKWARDATION:
        return AlertSignal(
            alert_type="CRYPTO_BASIS",
            level="L1",
            reason=(
                f"BTC Basis {spread:+.3f}% ({state}) — "
                f"극단 백워데이션. 현물·선물 괴리 확대 주의"
            ),
            snapshot=snapshot,
            etf_hints=[],
            avoid_etfs=[],
        )

    return None


def _vix_countdown_alert(
    snapshot: dict,
    prev_snapshot: Optional[dict] = None,
) -> Optional[AlertSignal]:
    """
    VIX 카운트다운 — 25/27/29 도달 시 경고 (하루 1회 제한은 alert_history에서 처리)
    공포구간(VIX 30) 진입 전 단계별 사전 경고
    """
    vix = snapshot.get("vix", 0)
    if vix <= 0:
        return None

    # 현재 VIX가 속하는 카운트다운 레벨 확인
    triggered_level = None
    for lvl in sorted(VIX_COUNTDOWN_LEVELS, reverse=True):
        if vix >= lvl:
            triggered_level = lvl
            break

    if triggered_level is None:
        return None

    # 이전 VIX가 해당 레벨 미만이었는지 확인 (신규 돌파 여부)
    prev_vix = (prev_snapshot or {}).get("vix", 0)
    if prev_vix >= triggered_level:
        return None  # 이미 이 레벨 이상이었음 — 신규 돌파 아님

    distance = 30 - vix  # VIX 30까지 남은 거리
    reason = (
        f"VIX {vix:.1f} — {triggered_level} 돌파 "
        f"(공포구간까지 {distance:.1f}pt)"
    )

    return AlertSignal(
        alert_type="VIX_COUNTDOWN",
        level="L1",
        reason=reason,
        snapshot=snapshot,
    )


def run_alert_engine(
    snapshot: dict,
    news_result: dict,
    prev_snapshot: Optional[dict] = None,
    rank_change=None,
    regime_change=None,
    signal_diff_result=None,
    score_diff_result=None,
    signals: dict = None,
    # ── Priority A (v1.1.0 신규) ──────────────────────────────
    tier2_data: dict = None,
    fred_data: dict = None,
    spy_sma_data: dict = None,
) -> List[AlertSignal]:
    """
    전체 Alert 감지 실행.

    Args:
        snapshot:     현재 시장 스냅샷
        news_result:  collect_news_sentiment() 결과 (RSS 포함)
        prev_snapshot: 직전 실행 스냅샷 (급변 감지용, 없으면 None)
        rank_change:  rank_tracker.detect_rank_change() 결과 (B-5)
        regime_change: regime_tracker.detect_regime_change() 결과 (B-6)
        signal_diff_result: signal_diff.compute_signal_diff() 결과 (B-5/B-6)
        score_diff_result:  signal_diff.compute_score_diff() 결과 (B-6)
        signals:      core_data signals dict (PCR/Basis Alert용)  ← v1.0.0

    Returns:
        발송 대상 AlertSignal 리스트 (우선순위 정렬)
    """
    logger.info(f"[AlertEngine] v{VERSION} Alert 감지 시작")
    alerts: List[AlertSignal] = []
    _signals = signals or {}

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

    # 6-A. VIX 카운트다운 (하루 1회, 25/27/29 단계별 사전 경고)
    countdown_sig = _vix_countdown_alert(snapshot, prev_snapshot)
    if countdown_sig:
        alerts.append(countdown_sig)

    # ── B-5: ETF 랭킹 변화 Alert (2026-04-01 추가) ──
    etf_rank_sig = _etf_rank_alert(rank_change, signal_diff_result, snapshot)
    if etf_rank_sig:
        alerts.append(etf_rank_sig)

    # ── B-6: 레짐 전환 Alert (2026-04-01 추가) ──
    regime_sig = _regime_change_alert(
        regime_change, signal_diff_result, score_diff_result, snapshot
    )
    if regime_sig:
        alerts.append(regime_sig)

    # ── v1.0.0: PCR 극단값 Alert ──────────────────────────────
    if _signals:
        pcr_sig = _pcr_alert(_signals, snapshot)
        if pcr_sig:
            alerts.append(pcr_sig)
            logger.info(f"[AlertEngine] PCR Alert: {pcr_sig.reason}")

    # ── v1.1.0: Priority A Alert (2026-04-11 신규) ─────────────
    # PCR과 독립적으로 실행 (if pcr_sig 블록 밖으로 이동)
    _tier2 = tier2_data or {}
    _fred  = fred_data or {}
    _sma   = spy_sma_data or {}

    # A-3: MOVE Index 급등
    move_sig = _detect_move_spike(_tier2)
    if move_sig:
        alerts.append(move_sig)
        logger.info(f"[AlertEngine] MOVE Alert: {move_sig.reason}")

    # A-6: SPY 200일선 이탈 / 데스크로스
    sma_sig = _detect_sma200_break(_sma)
    if sma_sig:
        alerts.append(sma_sig)
        logger.info(f"[AlertEngine] SMA Alert: {sma_sig.reason}")

    # A-4: 스태그플레이션 공포 (SPY↓ + TLT↓)
    stag_sig = _detect_stagflation(_tier2, snapshot)
    if stag_sig:
        alerts.append(stag_sig)
        logger.info(f"[AlertEngine] Stagflation Alert: {stag_sig.reason}")

    # A-5: 금리역전 심화
    spread_sig = _detect_yield_spread_deep(_fred)
    if spread_sig:
        alerts.append(spread_sig)
        logger.info(f"[AlertEngine] Yield Spread Alert: {spread_sig.reason}")

    # ── v1.0.0: Crypto Basis 극단 백워데이션 Alert ──────────────

    # ── v1.0.0: Crypto Basis 극단 백워데이션 Alert ──────────────
    if _signals:
        basis_sig = _crypto_basis_alert(_signals, snapshot)
        if basis_sig:
            alerts.append(basis_sig)
            logger.info(f"[AlertEngine] Crypto Basis Alert: {basis_sig.reason}")

    # 6. 프리미엄 알람 정보 — AlertSignal에 vix_level, regime_changed 부착
    vix_now = snapshot.get("vix", 0)
    vix_before = (prev_snapshot or {}).get("vix", vix_now)
    # 현재 VIX가 속하는 프리미엄 레벨 판별
    premium_vix_level = None
    for lvl in sorted(VIX_PREMIUM_LEVELS, reverse=True):
        if vix_now >= lvl:
            premium_vix_level = lvl
            break
    # 프리미엄 레벨 돌파 여부 (이전 VIX가 해당 레벨 미만이었는지)
    premium_triggered = (
        premium_vix_level is not None and vix_before < premium_vix_level
    )
    # 알람 객체에 메타 정보 첨부
    for a in alerts:
        a.vix_premium_level  = premium_vix_level
        a.vix_premium_crossed = premium_triggered
        a.prev_vix           = vix_before

    # 등급 내림차순 정렬 (L3 > L2 > L1)
    level_order = {"L3": 0, "L2": 1, "L1": 2}
    alerts.sort(key=lambda a: level_order.get(a.level, 9))

    if alerts:
        logger.info(f"[AlertEngine] {len(alerts)}개 Alert 감지: "
                    f"{[(a.alert_type, a.level) for a in alerts]}")
    else:
        logger.info("[AlertEngine] 이상 없음 — Alert 없음")

    return alerts
