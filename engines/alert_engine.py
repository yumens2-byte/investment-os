"""
engines/alert_engine.py
========================
Alert 감지 엔진 — 2번(SPY 급락) + 5번(Fed RSS) 조합 구현.

감지 항목:
  1. VIX 급등 Alert         (단독 L1~L2)
  2. SPY 급락 Alert          (단독 L1~L3)
  3. Oil Shock Alert          (단독 L1~L2)
  4. Fed 충격 Alert           (SPY 급락 + Fed RSS 키워드 AND 조합 → L2~L3)
  5. 복합 위기 Alert          (VIX + SPY + US10Y 동시 악화 → L2~L3)
  6. PCR 극단값 Alert         (옵션 시장 극단 심리 → L1)      ← v1.0.0 신규
  7. Crypto Basis Alert       (BTC 백워데이션 극단 → L1)      ← v1.0.0 신규
  8. VIX 카운트다운 Alert     (25/27/29 단계별 사전 경고)
  9. ETF 랭킹 변화 Alert      (Top1 변경 → L2, Top3 변동 → L1) ← B-5
 10. 레짐 전환 Alert          (Shock 진입/danger 방향 → L2)    ← B-6
 11. MOVE Index 급등 Alert    (채권 변동성 위기 → L1)          ← v1.1.0
 12. SPY SMA200 이탈 Alert    (200일선 이탈/데스크로스)        ← v1.1.0
 13. 스태그플레이션 공포 Alert (SPY↓+TLT↓ 동반 → L2)         ← v1.1.0
 14. 금리역전 심화 Alert       (10Y-2Y < -50bp → L1)          ← v1.1.0
 15. CPI 과열 Alert           (CPI YoY > 임계값 → L1)         ← v1.1.0
 16. SOFR 스트레스 Alert       (단기자금 스프레드 급등 → L1)   ← v1.1.0

등급:
  L1 — 주의    (단일 지표 임계값 돌파)
  L2 — 경고    (복합 조건 또는 심각 단일 지표)
  L3 — 위기    (다중 지표 동시 악화)

출력: List[AlertSignal] — 발송 대상 Alert 목록

x_eligible 정책 (run_alert.py Step X 연동):
  True  → L2/L3 등급의 시장 직접 지표 Alert만 X 즉시 발행
           VIX_L2, SPY_L2/L3, OIL(가격충격), FED_SHOCK, CRISIS, STAGFLATION, SMA200_BREAK_L2
  False → L1 등급, 내부 분석용 지표, 주간 알림 등 TG만 발행
           VIX_L1, SPY_L1, PCR, CRYPTO_BASIS, VIX_COUNTDOWN, ETF_RANK, REGIME_CHANGE,
           MOVE_SPIKE, YIELD_SPREAD_DEEP, CPI_HOT, SOFR_STRESS, SMA200_BREAK_L1

⚠️  run_alert.py Step X 주의사항:
    AlertSignal은 dataclass이므로 .get() 메서드가 없음.
    Step X에서 반드시 getattr(_alert, "x_eligible", False) 로 접근할 것.
    _alert.get("x_eligible", False) → AttributeError 발생.
"""
import logging
from typing import List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

VERSION = "1.1.1"  # BUG-1/2/3/4 수정

# ─── Alert 임계값 ───────────────────────────────────────────
# VIX
VIX_L1 = 28.0         # VIX 28 이상 → L1
VIX_L2 = 35.0         # VIX 35 이상 → L2
VIX_SURGE_PCT = 15.0  # 전일 대비 15% 급등 시 L1→L2 격상

# VIX 프리미엄 레벨 (유료 채널 세분화)
VIX_PREMIUM_LEVELS = [20, 25, 30, 35]

# VIX 카운트다운 레벨 (하루 1회, 공포구간 진입 전 단계별 경고)
VIX_COUNTDOWN_LEVELS = [25, 27, 29]

# SPY
SPY_L1 = -2.5   # SPY -2.5% 이하 → L1
SPY_L2 = -4.0   # SPY -4.0% 이하 → L2
SPY_L3 = -6.0   # SPY -6.0% 이하 → L3 (서킷브레이커 근접)

# Oil
OIL_SURGE_PCT   = 4.0    # 하루 4% 이상 급등
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
CRYPTO_BASIS_BACKWARDATION = -1.0  # basis_spread < -1.0% → 극단 백워데이션 → L1

# ── v1.1.0: Priority A 임계값 import ──────────────────────────
from config.settings import (
    MOVE_STRESSED,
    STAGFLATION_SPY_THR, STAGFLATION_TLT_THR,
    YIELD_SPREAD_DEEP_BP,
    CPI_HOT,
    SOFR_STRESS_THR,
)


# ─────────────────────────────────────────────────────────────
# AlertSignal dataclass
# ─────────────────────────────────────────────────────────────
# FIX BUG-1: x_eligible 필드 추가
#   → run_alert.py Step X에서 getattr(_alert, "x_eligible", False) 로 접근
#
# FIX BUG-3: vix_premium_level / vix_premium_crossed / prev_vix 필드 선언
#   → 기존 동적 속성 추가(a.vix_premium_level = ...) 방식 제거
#     dataclass에 명시적으로 선언하여 타입 안전성 확보
#     __slots__ 추가 시에도 안전하게 동작
# ─────────────────────────────────────────────────────────────
@dataclass
class AlertSignal:
    # ── 필수 필드 ────────────────────────────────────────────
    alert_type: str          # "VIX" / "SPY" / "OIL" / "FED_SHOCK" / "CRISIS" / ...
    level: str               # "L1" / "L2" / "L3"
    reason: str              # 발생 이유 (트윗 본문용)
    snapshot: dict           # 현재 시장 스냅샷

    # ── 선택 필드 (기본값 있음) ──────────────────────────────
    etf_hints:    List[str] = field(default_factory=list)  # 주목 ETF
    avoid_etfs:   List[str] = field(default_factory=list)  # 회피 ETF
    fed_detected: bool = False   # Fed 키워드 감지 여부

    # FIX BUG-1/BUG-4: X 즉시 발행 자격 (기본값 False)
    # 각 AlertSignal 생성 시 명시적으로 지정 (run_alert.py Step X 연동)
    # 접근 방법: getattr(_alert, "x_eligible", False)   ← .get() 사용 금지
    x_eligible: bool = False

    # FIX BUG-3: 프리미엄 메타 필드 dataclass에 선언
    # run_alert.py에서 동적으로 주입하던 속성을 명시적 필드로 전환
    vix_premium_level:   Optional[int] = None   # 현재 VIX가 속하는 프리미엄 레벨
    vix_premium_crossed: bool          = False   # 이번 실행에서 레벨 돌파 여부
    prev_vix:            float         = 0.0     # 직전 VIX 값 (급등 비교용)


# ─────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────
def _detect_fed_keywords(news_result: dict) -> bool:
    """
    RSS 수집 결과에서 Fed 관련 키워드 감지.
    source_detail에서 헤드라인 직접 확인.
    키워드 2개 이상 감지 시 True 반환.
    """
    details = news_result.get("source_detail", [])
    all_headlines: List[str] = []
    for d in details:
        all_headlines.extend(d.get("headlines", []))

    text  = " ".join(all_headlines).lower()
    count = sum(1 for kw in FED_KEYWORDS if kw in text)

    if count >= 2:
        logger.info(f"[AlertEngine] Fed 키워드 감지: {count}건")
        return True
    return False


# ─────────────────────────────────────────────────────────────
# 개별 Alert 감지 함수
# ─────────────────────────────────────────────────────────────

def _vix_alert(snapshot: dict, prev_snapshot: Optional[dict]) -> Optional[AlertSignal]:
    """
    VIX 급등 감지.

    등급:
      VIX >= L2(35) → L2, x_eligible=True
      VIX >= L1(28) → L1, x_eligible=False
      전일 대비 15%+ 급등 시 L1 → L2 격상 (격상 후 x_eligible=True)
    """
    vix = snapshot.get("vix", 0)
    if vix <= 0:
        return None

    # 기본 등급 판정
    if vix >= VIX_L2:
        level  = "L2"
        reason = f"VIX {vix:.1f} — 극단적 공포 구간 진입"
        hints  = ["TLT", "SPYM"]
        avoids = ["QQQM", "XLK"]
    elif vix >= VIX_L1:
        level  = "L1"
        reason = f"VIX {vix:.1f} — 공포 지수 경계 구간"
        hints  = ["TLT", "ITA"]
        avoids = ["QQQM"]
    else:
        return None

    # 전일 대비 급등 체크 → L1이면 L2로 격상
    if prev_snapshot:
        prev_vix = prev_snapshot.get("vix", vix)
        if prev_vix > 0:
            surge_pct = (vix - prev_vix) / prev_vix * 100
            if surge_pct >= VIX_SURGE_PCT and level == "L1":
                level  = "L2"
                reason = f"VIX {vix:.1f} (+{surge_pct:.0f}%) — 공포 지수 급등"

    # FIX BUG-4: x_eligible — L2 이상만 X 발행 대상
    return AlertSignal(
        alert_type="VIX",
        level=level,
        reason=reason,
        snapshot=snapshot,
        etf_hints=hints,
        avoid_etfs=avoids,
        x_eligible=(level == "L2"),  # L2 → True, L1 → False
    )


def _spy_alert(snapshot: dict) -> Optional[AlertSignal]:
    """
    SPY 급락 감지.

    등급:
      SPY <= L3(-6%) → L3, x_eligible=True
      SPY <= L2(-4%) → L2, x_eligible=True
      SPY <= L1(-2.5%) → L1, x_eligible=False
    """
    spy = snapshot.get("sp500", 0)

    if spy <= SPY_L3:
        # FIX BUG-4: x_eligible 명시
        return AlertSignal(
            alert_type="SPY",
            level="L3",
            reason=f"SPY {spy:.1f}% — 서킷브레이커 근접 급락",
            snapshot=snapshot,
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK", "XLE"],
            x_eligible=True,
        )
    elif spy <= SPY_L2:
        return AlertSignal(
            alert_type="SPY",
            level="L2",
            reason=f"SPY {spy:.1f}% — 시장 급락 경보",
            snapshot=snapshot,
            etf_hints=["TLT", "ITA"],
            avoid_etfs=["QQQM", "XLK"],
            x_eligible=True,
        )
    elif spy <= SPY_L1:
        return AlertSignal(
            alert_type="SPY",
            level="L1",
            reason=f"SPY {spy:.1f}% — 주의 급락",
            snapshot=snapshot,
            etf_hints=["SPYM", "TLT"],
            avoid_etfs=["QQQM"],
            x_eligible=False,  # L1 → TG만
        )
    return None


def _oil_alert(snapshot: dict, prev_snapshot: Optional[dict]) -> Optional[AlertSignal]:
    """
    Oil Shock 감지.

    등급:
      가격 >= $100 → L2, x_eligible=True  (절대 가격 충격)
      전일 대비 +4% 급등 → L1, x_eligible=False (급등이지만 가격은 $100 미만)
      두 조건 동시 → L2, x_eligible=True
    """
    oil = snapshot.get("oil", 0)
    if oil <= 0:
        return None

    price_shock = oil >= OIL_SHOCK_PRICE
    surge_shock = False
    surge_pct   = 0.0

    if prev_snapshot:
        prev_oil = prev_snapshot.get("oil", oil)
        if prev_oil > 0:
            surge_pct = (oil - prev_oil) / prev_oil * 100
            if surge_pct >= OIL_SURGE_PCT:
                surge_shock = True

    if not (price_shock or surge_shock):
        return None

    # reason 조립
    reason = f"WTI ${oil:.1f}"
    if price_shock:
        reason += " — $100 돌파"
    if surge_shock:
        reason += f" (+{surge_pct:.1f}% 급등)"

    # FIX BUG-4: x_eligible — 가격충격($100 돌파)만 X 발행
    return AlertSignal(
        alert_type="OIL",
        level="L2" if price_shock else "L1",
        reason=reason,
        snapshot=snapshot,
        etf_hints=["XLE", "ITA"],
        avoid_etfs=["QQQM", "TLT"],
        x_eligible=price_shock,  # 가격 충격만 True, 급등률만이면 False
    )


def _fed_shock_alert(
    spy_alert: Optional[AlertSignal],
    fed_detected: bool,
    snapshot: dict,
) -> Optional[AlertSignal]:
    """
    Fed 충격 Alert — SPY 급락 + Fed RSS 키워드 AND 조건.
    SPY만 있으면 일반 급락, Fed 키워드까지 있으면 Fed 충격으로 격상.

    SPY L2 이상 + Fed 키워드 → L3, x_eligible=True
    SPY L1 + Fed 키워드      → L2, x_eligible=True
    """
    if not fed_detected or spy_alert is None:
        return None

    spy = snapshot.get("sp500", 0)
    vix = snapshot.get("vix", 0)

    if spy <= SPY_L2 or (spy <= SPY_L1 and vix >= VIX_L1):
        level  = "L3"
        reason = f"SPY {spy:.1f}% + Fed 뉴스 급증 — Fed 발표 충격 의심"
    else:
        level  = "L2"
        reason = f"SPY {spy:.1f}% + Fed 관련 키워드 감지 — 금리 관련 시장 충격"

    # FIX BUG-4: FED_SHOCK는 모든 레벨에서 X 발행 (Fed 이벤트는 즉시성 중요)
    return AlertSignal(
        alert_type="FED_SHOCK",
        level=level,
        reason=reason,
        snapshot=snapshot,
        etf_hints=["TLT", "SPYM"],
        avoid_etfs=["QQQM", "XLK"],
        fed_detected=True,
        x_eligible=True,
    )


def _crisis_alert(snapshot: dict) -> Optional[AlertSignal]:
    """
    복합 위기 Alert — VIX + SPY + US10Y 동시 악화.

    3개 동시 → L3, x_eligible=True
    2개 동시 → L2, x_eligible=True
    """
    vix   = snapshot.get("vix", 0)
    spy   = snapshot.get("sp500", 0)
    us10y = snapshot.get("us10y", 4.0)

    crisis_count = 0
    if vix   >= VIX_L2: crisis_count += 1
    if spy   <= SPY_L2: crisis_count += 1
    if us10y >= 4.8:    crisis_count += 1

    if crisis_count >= 3:
        # FIX BUG-4: CRISIS → x_eligible=True
        return AlertSignal(
            alert_type="CRISIS",
            level="L3",
            reason=f"SPY {spy:.1f}% | VIX {vix:.1f} | US10Y {us10y:.2f}% — 복합 위기 동시 발생",
            snapshot=snapshot,
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK", "XLE"],
            x_eligible=True,
        )
    elif crisis_count == 2:
        return AlertSignal(
            alert_type="CRISIS",
            level="L2",
            reason=f"SPY {spy:.1f}% | VIX {vix:.1f} — 복합 위험 신호 감지",
            snapshot=snapshot,
            etf_hints=["TLT", "ITA"],
            avoid_etfs=["QQQM", "XLK"],
            x_eligible=True,
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
    [B-5] ETF 랭킹 변화 Alert.

    등급:
      Top1 변경 → L2, x_eligible=False (분석 정보, TG 유료 채널)
      Top3 내 ETF 교체 → L1, x_eligible=False
      Top1 유지 + 하위만 변동 → 발송 안함

    x_eligible=False 이유: ETF 랭킹은 실시간 급변보다 전략 정보
                           TG 유료 채널 전용으로 충분
    """
    if rank_change is None:
        return None

    top1_changed = rank_change.get("top1_changed", False)
    moved_up     = rank_change.get("moved_up", [])
    moved_down   = rank_change.get("moved_down", [])
    old_top1     = rank_change.get("old_top1", "—")
    new_top1     = rank_change.get("new_top1", "—")

    # ── Top1 변경 → L2 ──
    if top1_changed:
        cause = ""
        if signal_diff_result and signal_diff_result.get("summary"):
            cause = f" | 원인: {signal_diff_result['summary']}"

        return AlertSignal(
            alert_type="ETF_RANK",
            level="L2",
            reason=f"ETF 1위 전환: {old_top1} → {new_top1}{cause}",
            snapshot=snapshot,
            etf_hints=[new_top1],
            avoid_etfs=[old_top1] if old_top1 != "—" else [],
            x_eligible=False,  # 전략 정보 → TG만
        )

    # ── Top3 내 교체 있는지 확인 → L1 ──
    top3_moved = any(
        m["to"] <= 3 or m["from"] <= 3
        for m in moved_up + moved_down
    )

    if top3_moved:
        up_names   = [m["etf"] for m in moved_up   if m["to"]   <= 3]
        down_names = [m["etf"] for m in moved_down if m["from"] <= 3]

        cause = ""
        if signal_diff_result and signal_diff_result.get("summary"):
            cause = f" | 원인: {signal_diff_result['summary']}"

        parts = []
        if up_names:   parts.append(f"상승: {','.join(up_names)}")
        if down_names: parts.append(f"하락: {','.join(down_names)}")

        return AlertSignal(
            alert_type="ETF_RANK",
            level="L1",
            reason=f"ETF Top3 변화 — {' / '.join(parts)}{cause}",
            snapshot=snapshot,
            etf_hints=up_names,
            avoid_etfs=down_names,
            x_eligible=False,
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
    [B-6] 레짐 전환 Alert.

    등급:
      Shock 레짐 진입 → L2, x_eligible=False
      danger 방향 전환 → L2, x_eligible=False
      recovery 방향 전환 → L1, x_eligible=False
      Risk Level만 변경 → L1, x_eligible=False

    x_eligible=False 이유: 레짐 전환은 분석 정보로 TG 채널 충분
                           X 즉시 발행보다 TG 해설이 더 적합
    """
    if regime_change is None:
        return None

    old_regime     = regime_change.get("old_regime",      "—")
    new_regime     = regime_change.get("new_regime",      "—")
    old_risk       = regime_change.get("old_risk_level",  "—")
    new_risk       = regime_change.get("new_risk_level",  "—")
    direction      = regime_change.get("direction",       "danger")
    regime_changed = regime_change.get("regime_changed",  False)
    risk_changed   = regime_change.get("risk_changed",    False)

    cause = ""
    if signal_diff_result and signal_diff_result.get("summary"):
        cause = f" | 원인: {signal_diff_result['summary']}"

    # ── Shock 레짐 진입 → L2 ──
    shock_regimes = {"Oil Shock", "Liquidity Crisis", "Crisis Regime"}
    if regime_changed and new_regime in shock_regimes:
        hints, avoids = _regime_etf_hints(new_regime)
        return AlertSignal(
            alert_type="REGIME_CHANGE",
            level="L2",
            reason=f"Shock 레짐 진입: {old_regime} → {new_regime}{cause}",
            snapshot=snapshot,
            etf_hints=hints,
            avoid_etfs=avoids,
            x_eligible=False,
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
            x_eligible=False,
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
            x_eligible=False,
        )

    # ── 레짐 동일 + Risk Level만 변경 → L1 ──
    if not regime_changed and risk_changed:
        return AlertSignal(
            alert_type="REGIME_CHANGE",
            level="L1",
            reason=f"Risk Level 변경: {old_risk} → {new_risk} (레짐 {new_regime} 유지){cause}",
            snapshot=snapshot,
            x_eligible=False,
        )

    return None


def _regime_etf_hints(regime: str):
    """레짐별 주목/회피 ETF 반환"""
    _HINTS = {
        "Risk-On":          (["QQQM", "XLK"], []),
        "Risk-Off":         (["TLT", "ITA"],  ["QQQM", "XLK"]),
        "Oil Shock":        (["XLE", "ITA"],  ["QQQM", "TLT"]),
        "Liquidity Crisis": (["TLT", "SPYM"], ["QQQM", "XLK"]),
        "Recession Risk":   (["TLT", "SPYM"], ["QQQM", "XLE"]),
        "Stagflation Risk": (["XLE", "ITA"],  ["QQQM", "TLT"]),
        "Crisis Regime":    (["TLT", "SPYM"], ["QQQM", "XLK", "XLE"]),
        "Transition":       ([], []),
    }
    return _HINTS.get(regime, ([], []))


# ──────────────────────────────────────────────────────────────
# v1.0.0: PCR + Crypto Basis Alert
# ──────────────────────────────────────────────────────────────

def _pcr_alert(signals: dict, snapshot: dict) -> Optional[AlertSignal]:
    """
    [v1.0.0] PCR 극단값 Alert — 옵션 시장 심리 극단 감지.

    PCR > 1.5 : 극단 공포 — 풋 옵션 폭증, 패닉 헤징 신호
    PCR < 0.5 : 극단 탐욕 — 콜 옵션 폭증, 과열 신호

    과거 사례: PCR > 1.5는 2020-03, 2022-09~10 저점권과 일치
               PCR < 0.5는 2021-11 고점권과 일치

    x_eligible=False: 옵션 시장 내부 지표, 단독으로 X 즉시 발행 불필요
    """
    pcr   = signals.get("pcr_value", 0) or 0
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
            x_eligible=False,
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
            x_eligible=False,
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

    x_eligible=False: 파생 지표, 단독 X 즉시 발행 불필요
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
            x_eligible=False,
        )

    return None


# ──────────────────────────────────────────────────────────────
# v1.1.0: Priority A Alert 감지 함수 (2026-04-11 신규)
# ──────────────────────────────────────────────────────────────

def _detect_move_spike(tier2_data: dict) -> Optional[AlertSignal]:
    """
    [A-3] MOVE Index 급등 Alert.
    MOVE > 140 → 채권 변동성 위기 수준 → L1, x_eligible=False

    x_eligible=False: 채권 파생 지표, TG 분석 정보로 충분
    """
    move = tier2_data.get("move_index") if tier2_data else None
    if move is None:
        return None
    if move >= MOVE_STRESSED:
        return AlertSignal(
            alert_type="MOVE_SPIKE",
            level="L1",
            reason=(
                f"MOVE Index {move:.1f} — 채권 변동성 위기 수준 "
                f"(임계값 {MOVE_STRESSED}). 금리 불확실성 급등"
            ),
            snapshot={},
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK"],
            x_eligible=False,
        )
    return None


def _detect_sma200_break(spy_sma_data: dict) -> Optional[AlertSignal]:
    """
    [A-6] SPY SMA200 이탈 / 데스크로스 Alert.

    데스크로스 + 200선 이탈 동시 → L2, x_eligible=True  (기술적 약세장 진입)
    200선 이탈 단독 → L1, x_eligible=False
    """
    if not spy_sma_data:
        return None

    price  = spy_sma_data.get("spy_price")
    sma50  = spy_sma_data.get("spy_sma50")
    sma200 = spy_sma_data.get("spy_sma200")

    if not all([price, sma50, sma200]):
        return None

    death_cross = sma50  < sma200
    below_200   = price  < sma200
    pct         = round((price - sma200) / sma200 * 100, 2)

    if death_cross and below_200:
        return AlertSignal(
            alert_type="SMA200_BREAK",
            level="L2",
            reason=(
                f"SPY 데스크로스 + 200일선 이탈 "
                f"(${price} | SMA200 ${sma200} | {pct:+.1f}%) "
                f"— 기술적 약세장 진입 신호"
            ),
            snapshot={},
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK"],
            x_eligible=True,   # 데스크로스 + 이탈 동시 → 중요 신호
        )
    elif below_200:
        return AlertSignal(
            alert_type="SMA200_BREAK",
            level="L1",
            reason=(
                f"SPY 200일선 이탈 "
                f"(${price} | SMA200 ${sma200} | {pct:+.1f}%) "
                f"— 추세 전환 경고"
            ),
            snapshot={},
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK"],
            x_eligible=False,
        )
    return None


def _detect_stagflation(tier2_data: dict, snapshot: dict) -> Optional[AlertSignal]:
    """
    [A-4] 스태그플레이션 공포 Alert.
    SPY < -1.0% AND TLT < -1.0% 동반 하락 → L2, x_eligible=True
    (주식·채권 동반 약세 = 금리 상승 + 경기 둔화)
    """
    tlt_chg = (tier2_data or {}).get("tlt_change")
    spy_chg = (snapshot    or {}).get("sp500")

    if tlt_chg is None or spy_chg is None:
        return None

    if spy_chg < STAGFLATION_SPY_THR and tlt_chg < STAGFLATION_TLT_THR:
        return AlertSignal(
            alert_type="STAGFLATION",
            level="L2",
            reason=(
                f"스태그플레이션 공포 — "
                f"SPY {spy_chg:+.2f}% + TLT {tlt_chg:+.2f}% 동반 하락 "
                f"(금리 상승 + 경기 둔화 동시 신호)"
            ),
            snapshot=snapshot or {},
            etf_hints=["XLE", "ITA"],
            avoid_etfs=["QQQM", "TLT"],
            x_eligible=True,   # L2 복합 신호 → X 즉시 발행
        )
    return None


def _detect_yield_spread_deep(fred_data: dict) -> Optional[AlertSignal]:
    """
    [A-5] 금리역전 심화 Alert.
    10Y-2Y 스프레드 < -50bp → L1, x_eligible=False
    경기침체 선행 지표 (월간 변화 속도가 느려 즉시 X 발행 불필요)
    """
    spread_bp = (fred_data or {}).get("spread_2y10y_bp")
    if spread_bp is None:
        return None

    if spread_bp < YIELD_SPREAD_DEEP_BP:
        us2y     = (fred_data or {}).get("us2y")
        us2y_str = f" | 2Y {us2y:.2f}%" if us2y else ""
        return AlertSignal(
            alert_type="YIELD_SPREAD_DEEP",
            level="L1",
            reason=(
                f"금리역전 심화 — 10Y-2Y {spread_bp:.1f}bp"
                f"{us2y_str} "
                f"(임계값 {YIELD_SPREAD_DEEP_BP}bp)"
            ),
            snapshot={},
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=[],
            x_eligible=False,
        )
    return None


# ──────────────────────────────────────────────────────────────
# v1.1.0 Priority B: CPI + SOFR Alert
# ──────────────────────────────────────────────────────────────

def _detect_cpi_surprise(fred_data: dict) -> Optional[AlertSignal]:
    """
    [B-1] CPI 과열 Alert.
    CPI YoY > 3.5% → 연준 긴축 재점화 리스크 → L1, x_eligible=False

    월간 데이터이므로 alert_history 쿨다운 7일 적용 필수 (run_alert.py에서 처리).
    x_eligible=False: 월간 지표, 실시간 즉각 X 발행 불필요
    """
    cpi_yoy = (fred_data or {}).get("cpi_yoy")
    if cpi_yoy is None:
        return None

    if cpi_yoy > CPI_HOT:
        core     = (fred_data or {}).get("core_cpi_yoy")
        core_str = f" (Core {core:.2f}%)" if core else ""
        return AlertSignal(
            alert_type="CPI_HOT",
            level="L1",
            reason=(
                f"CPI {cpi_yoy:.2f}% YoY{core_str} — "
                f"임계값 {CPI_HOT}% 초과. 연준 금리 인하 기대 약화 리스크"
            ),
            snapshot={},
            etf_hints=["TLT", "XLU", "XLP"],
            avoid_etfs=["QQQM", "XLRE"],
            x_eligible=False,
        )
    return None


def _detect_sofr_stress(fred_data: dict) -> Optional[AlertSignal]:
    """
    [B-8] SOFR 단기자금 스트레스 Alert.
    SOFR - Fed Funds Rate 차이 > 0.5%p → L1, x_eligible=False
    2008/2020 위기 선행 지표

    x_eligible=False: 단기자금 내부 지표, TG 분석으로 충분
    """
    sofr_spread = (fred_data or {}).get("sofr_spread")
    sofr        = (fred_data or {}).get("sofr")

    if sofr_spread is None:
        return None

    if sofr_spread >= SOFR_STRESS_THR:
        sofr_val = f"{sofr:.3f}%" if sofr is not None else "N/A"
        return AlertSignal(
            alert_type="SOFR_STRESS",
            level="L1",
            reason=(
                f"SOFR 스트레스 — 단기자금 스프레드 {sofr_spread:.3f}%p "
                f"(SOFR {sofr_val}). 은행간 신용 경색 경고"
            ),
            snapshot={},
            etf_hints=["TLT", "SPYM"],
            avoid_etfs=["QQQM", "XLK"],
            x_eligible=False,
        )
    return None


# ──────────────────────────────────────────────────────────────
# VIX 카운트다운
# ──────────────────────────────────────────────────────────────

def _vix_countdown_alert(
    snapshot: dict,
    prev_snapshot: Optional[dict] = None,
) -> Optional[AlertSignal]:
    """
    VIX 카운트다운 — 25/27/29 도달 시 경고.
    하루 1회 제한은 alert_history에서 처리.
    공포구간(VIX 30) 진입 전 단계별 사전 경고.

    x_eligible=False: 사전 경고 목적, 즉시 X 발행 불필요
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
    reason   = (
        f"VIX {vix:.1f} — {triggered_level} 돌파 "
        f"(공포구간까지 {distance:.1f}pt)"
    )

    return AlertSignal(
        alert_type="VIX_COUNTDOWN",
        level="L1",
        reason=reason,
        snapshot=snapshot,
        x_eligible=False,  # 사전 경고 → TG만
    )


# ──────────────────────────────────────────────────────────────
# 메인 실행 함수
# ──────────────────────────────────────────────────────────────

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
        snapshot:          현재 시장 스냅샷
        news_result:       collect_news_sentiment() 결과 (RSS 포함)
        prev_snapshot:     직전 실행 스냅샷 (급변 감지용, 없으면 None)
        rank_change:       rank_tracker.detect_rank_change() 결과 (B-5)
        regime_change:     regime_tracker.detect_regime_change() 결과 (B-6)
        signal_diff_result: signal_diff.compute_signal_diff() 결과 (B-5/B-6)
        score_diff_result:  signal_diff.compute_score_diff() 결과 (B-6)
        signals:           core_data signals dict (PCR/Basis Alert용)  ← v1.0.0
        tier2_data:        Tier2 시장 데이터 (MOVE/TLT 등)            ← v1.1.0
        fred_data:         FRED 거시 데이터 (spread_bp/us2y 등)       ← v1.1.0
        spy_sma_data:      SPY SMA 데이터 (price/sma50/sma200)        ← v1.1.0

    Returns:
        발송 대상 AlertSignal 리스트 (등급 내림차순 정렬)

    ⚠️  run_alert.py Step X 주의:
        x_eligible 접근 시 반드시 getattr(_alert, "x_eligible", False) 사용.
        AlertSignal은 dataclass이므로 _alert.get("x_eligible") → AttributeError.
    """
    logger.info(f"[AlertEngine] v{VERSION} Alert 감지 시작")
    alerts: List[AlertSignal] = []
    _signals = signals     or {}
    _tier2   = tier2_data  or {}
    _fred    = fred_data   or {}
    _sma     = spy_sma_data or {}

    # ── 1. Fed 키워드 감지 (공통) ─────────────────────────────
    fed_detected = _detect_fed_keywords(news_result)

    # ── 2. 개별 Alert 감지 ────────────────────────────────────
    vix_sig = _vix_alert(snapshot, prev_snapshot)
    spy_sig = _spy_alert(snapshot)
    oil_sig = _oil_alert(snapshot, prev_snapshot)

    # ── 3. 복합 위기 (가장 먼저 — 최고 등급) ──────────────────
    crisis_sig = _crisis_alert(snapshot)
    if crisis_sig:
        alerts.append(crisis_sig)

    # ── 4. Fed 충격 (SPY + Fed AND 조건) ──────────────────────
    fed_sig = _fed_shock_alert(spy_sig, fed_detected, snapshot)
    if fed_sig:
        alerts.append(fed_sig)
    elif spy_sig:
        # Fed 미감지 시 일반 SPY Alert
        alerts.append(spy_sig)

    # ── 5. VIX, Oil (독립 Alert) ──────────────────────────────
    if vix_sig and not crisis_sig:  # 복합위기에 이미 포함된 경우 중복 방지
        alerts.append(vix_sig)
    if oil_sig:
        alerts.append(oil_sig)

    # ── 6-A. VIX 카운트다운 (하루 1회, 25/27/29 단계별) ───────
    countdown_sig = _vix_countdown_alert(snapshot, prev_snapshot)
    if countdown_sig:
        alerts.append(countdown_sig)

    # ── B-5: ETF 랭킹 변화 Alert ──────────────────────────────
    etf_rank_sig = _etf_rank_alert(rank_change, signal_diff_result, snapshot)
    if etf_rank_sig:
        alerts.append(etf_rank_sig)

    # ── B-6: 레짐 전환 Alert ──────────────────────────────────
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

    # ── v1.1.0: Priority A Alert ──────────────────────────────
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

    # ── v1.1.0 Priority B: CPI 과열 ───────────────────────────
    cpi_sig = _detect_cpi_surprise(_fred)
    if cpi_sig:
        alerts.append(cpi_sig)
        logger.info(f"[AlertEngine] CPI Alert: {cpi_sig.reason}")

    # ── v1.1.0 Priority B: SOFR 스트레스 ──────────────────────
    sofr_sig = _detect_sofr_stress(_fred)
    if sofr_sig:
        alerts.append(sofr_sig)
        logger.info(f"[AlertEngine] SOFR Alert: {sofr_sig.reason}")

    # ── v1.0.0: Crypto Basis 극단 백워데이션 ──────────────────
    if _signals:
        basis_sig = _crypto_basis_alert(_signals, snapshot)
        if basis_sig:
            alerts.append(basis_sig)
            logger.info(f"[AlertEngine] Crypto Basis Alert: {basis_sig.reason}")

    # ── 프리미엄 메타 정보 부착 ───────────────────────────────
    # FIX BUG-3: 동적 속성 추가(a.vix_premium_level = ...) 방식 제거.
    #             AlertSignal dataclass에 필드가 선언되어 있으므로
    #             직접 속성 할당으로 타입 안전하게 처리.
    vix_now    = snapshot.get("vix", 0)
    vix_before = (prev_snapshot or {}).get("vix", vix_now)

    # 현재 VIX가 속하는 프리미엄 레벨 판별
    premium_vix_level: Optional[int] = None
    for lvl in sorted(VIX_PREMIUM_LEVELS, reverse=True):
        if vix_now >= lvl:
            premium_vix_level = lvl
            break

    # 프리미엄 레벨 신규 돌파 여부 (이전 VIX가 해당 레벨 미만이었는지)
    premium_triggered = (
        premium_vix_level is not None and vix_before < premium_vix_level
    )

    # dataclass에 선언된 필드에 직접 할당 (타입 안전)
    for a in alerts:
        a.vix_premium_level   = premium_vix_level
        a.vix_premium_crossed = premium_triggered
        a.prev_vix            = vix_before

    # ── 등급 내림차순 정렬 (L3 > L2 > L1) ────────────────────
    level_order = {"L3": 0, "L2": 1, "L1": 2}
    alerts.sort(key=lambda a: level_order.get(a.level, 9))

    if alerts:
        logger.info(
            f"[AlertEngine] {len(alerts)}개 Alert 감지: "
            f"{[(a.alert_type, a.level, a.x_eligible) for a in alerts]}"
        )
    else:
        logger.info("[AlertEngine] 이상 없음 — Alert 없음")

    return alerts
