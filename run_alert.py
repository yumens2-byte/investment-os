"""
run_alert.py
=============
역할: Alert 감지 + 발송
실행: python run_alert.py
     python main.py alert

파이프라인:
  시장 데이터 수집 (yfinance)
  RSS 뉴스 수집 (rss_extended)
  → Alert 엔진 (조건 판정)
  → 쿨다운 체크 (1시간)
  → Alert 포맷 생성
  → TG 발행 (무료/유료 채널)
  → Step X: x_eligible=True Alert X 발행 (감정 트리거 또는 기본 포맷)
  → 이력 기록

이상 없으면 조용히 종료 (Alert 없음 = 정상)

Step X 설계 원칙:
  - x_eligible=True + 쿨다운 없음  → 감정 트리거 포맷 X 발행 (post_alert_tweet)
  - x_eligible=True + 쿨다운 중    → 기본 포맷 X 발행 (publish_tweet)
  - x_eligible=False               → X 발행 완전 스킵 (TG만 발행)
  - TG 발행은 항상 tweet_for_tg(기본 포맷) 사용 — X 포맷과 완전 분리
  - AlertSignal은 dataclass → getattr() 로 x_eligible 접근 (필수)
    _alert.get("x_eligible", False) 는 AttributeError 발생 — 사용 금지
  - x_alert_history.json: GitHub Actions cache로 run 간 유지

v1.1.0 변경사항:
  [패치1] VIX_COUNTDOWN X 발행 제거 — x_eligible=False 설계 원칙 적용
  [패치2] X 발행 3-way 분기 재설계 — tweet_for_tg 변수 분리
  [패치3] store_daily_alert 단일 위치 통합 — 중복/누락 방지
  [패치4] TG 무료 채널 tweet_for_tg 사용 — X 감정 포맷 유입 차단
  [패치5] B-21A 밈 선생성 + TG 통합 발행 — TG free 중복 발행 제거
"""
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import os

from config.settings import LOG_LEVEL, DRY_RUN

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_alert")

VERSION = "1.1.0"

# ─────────────────────────────────────────────────────────────
# Step X 전용 상수 + 유틸 함수
# ─────────────────────────────────────────────────────────────
# ⚠️  AlertSignal x_eligible 접근 규칙:
#       getattr(_alert, "x_eligible", False)   ← 올바름
#       _alert.get("x_eligible", False)        ← AttributeError (dataclass는 .get() 없음)

_X_ALERT_COOLDOWN_MIN = 90                              # 동일 타입 X 재발행 최소 대기 (분)
_X_ALERT_HISTORY_FILE = Path("x_alert_history.json")   # GitHub Actions cache로 유지


def _load_x_alert_history() -> dict:
    """
    X Alert 발행 이력 로드.
    파일 없으면 {} 반환 (FileNotFoundError 안전 처리).
    """
    if not _X_ALERT_HISTORY_FILE.exists():
        return {}
    try:
        with open(_X_ALERT_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[AlertX] 이력 로드 실패 → 빈 dict 사용: {e}")
        return {}


def _save_x_alert_history(history: dict) -> None:
    """
    X Alert 발행 이력 저장.
    실패 시 로그만 출력 (쿨다운 소실 허용 — 하루 최대 1회 중복 가능성).
    """
    try:
        with open(_X_ALERT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[AlertX] 이력 저장 실패 (쿨다운 다음 실행 초기화): {e}")


def _is_x_cooldown_active(alert_type: str, history: dict) -> bool:
    """
    쿨다운 체크: 동일 타입의 X 발행이 쿨다운 이내 있으면 True.
    잘못된 날짜 형식은 False 반환 (크래시 없음).
    """
    last_str = history.get(alert_type)
    if not last_str:
        return False
    try:
        last_dt = datetime.fromisoformat(last_str)
        return datetime.now(timezone.utc) - last_dt < timedelta(minutes=_X_ALERT_COOLDOWN_MIN)
    except Exception:
        return False


def _format_x_alert_tweet(alert, snapshot: dict) -> str:
    """
    X 전용 Alert 포맷 생성 — 감정 트리거 suffix 포함.
    500자 초과 시 본문 자름 (suffix 유지).

    Args:
        alert:    AlertSignal 객체 (dataclass)
        snapshot: 현재 시장 스냅샷
    """
    from publishers.thread_builder import get_alert_emotion_suffix

    alert_type = alert.alert_type
    level      = alert.level
    suffix     = get_alert_emotion_suffix(alert_type)

    bodies = {
        "VIX_L2": (
            f"🚨 VIX {snapshot.get('vix', 0):.1f} 돌파 — 공포지수 극단 구간 진입\n\n"
            f"현재 SPY {snapshot.get('sp500', 0):+.1f}%"
            f" | Oil ${snapshot.get('oil', 0):.0f}\n\n"
            f"변동성 35+ 구간은 역사적으로 저가 매수 기회이기도 하지만 "
            f"추가 하락 리스크도 공존합니다."
        ),
        "SPY_L2": (
            f"🔴 S&P500 {snapshot.get('sp500', 0):+.1f}% — 장중 급락 감지\n\n"
            f"현재 VIX {snapshot.get('vix', 0):.1f}"
            f" | Oil ${snapshot.get('oil', 0):.0f}"
            f" | 10Y {snapshot.get('us10y', 0):.2f}%\n\n"
            f"-3% 이상 하락은 알고리즘 매도 가속 구간입니다. "
            f"포지션 점검이 필요한 시점."
        ),
        "SPY_L3": (
            f"🆘 S&P500 {snapshot.get('sp500', 0):+.1f}% — 서킷브레이커 근접 급락\n\n"
            f"현재 VIX {snapshot.get('vix', 0):.1f}"
            f" | Oil ${snapshot.get('oil', 0):.0f}\n\n"
            f"역사적 급락 구간 진입. 리스크 관리 최우선."
        ),
        "OIL": (
            f"⛽ WTI 유가 ${snapshot.get('oil', 0):.0f} 돌파 — 인플레이션 재점화 신호\n\n"
            f"현재 SPY {snapshot.get('sp500', 0):+.1f}%"
            f" | DXY {snapshot.get('dollar_index', 0):.1f}\n\n"
            f"유가 $100 이상은 Fed 긴축 장기화 압박으로 이어질 수 있습니다."
        ),
        "FED_SHOCK": (
            f"🏦 Fed 충격 감지 — SPY {snapshot.get('sp500', 0):+.1f}%\n\n"
            f"VIX {snapshot.get('vix', 0):.1f}"
            f" | Fed 관련 뉴스 급증\n\n"
            f"금리 관련 시장 충격 의심. 포지션 점검 필요."
        ),
        "CRISIS": (
            f"🆘 복합 위기 시그널 감지\n\n"
            f"VIX {snapshot.get('vix', 0):.1f}"
            f" | SPY {snapshot.get('sp500', 0):+.1f}%"
            f" | Oil ${snapshot.get('oil', 0):.0f}\n\n"
            f"다중 지표 동시 경보 — 리스크 관리 최우선 구간입니다."
        ),
        "STAGFLATION": (
            f"📉 스태그플레이션 공포 — SPY {snapshot.get('sp500', 0):+.1f}%\n\n"
            f"주식·채권 동반 약세 감지 (금리 상승 + 경기 둔화)\n\n"
            f"에너지·방산 비중 주목. 성장주 주의."
        ),
        "SMA200_BREAK": (
            f"📊 SPY 200일 이동평균선 이탈 감지\n\n"
            f"현재 SPY {snapshot.get('sp500', 0):+.1f}%"
            f" | 기술적 약세장 진입 신호\n\n"
            f"추세 전환 경고 — 포지션 재점검 시점."
        ),
    }

    body  = bodies.get(
        alert_type,
        f"⚠️ 시장 Alert [{level}] — {alert_type}\n\n"
        f"VIX {snapshot.get('vix', 0):.1f}"
        f" | SPY {snapshot.get('sp500', 0):+.1f}%"
    )
    tweet = body + suffix

    # 500자 초과 → 본문 자름, suffix 유지
    MAX = 500
    if len(tweet) > MAX:
        body_max = MAX - len(suffix)
        cut   = body.rfind("\n\n", 0, body_max)
        body  = body[:cut] if cut > 0 else body[:body_max]
        tweet = body + suffix

    return tweet


# ─────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────

def _is_alert_window() -> tuple[bool, str]:
    """
    미국 동부시간 기준 Alert 실행 허용 시간인지 판정.
    exact match 대신 grace window를 둔다.
    예: 09:30 target, grace=12이면 09:30:00 ~ 09:41:59 허용
    """
    tz_name       = os.getenv("ALERT_TZ",             "America/New_York")
    windows_raw   = os.getenv("ALERT_WINDOWS",         "09:30,10:30,15:30")
    grace_minutes = int(os.getenv("ALERT_GRACE_MINUTES", "12"))

    now_et  = datetime.now(ZoneInfo(tz_name))
    weekday = now_et.weekday()  # Mon=0 ... Sun=6

    if weekday >= 5:
        return False, f"weekend_et:{now_et.isoformat()}"

    allowed       = [x.strip() for x in windows_raw.split(",") if x.strip()]
    now_total_min = now_et.hour * 60 + now_et.minute

    for hhmm in allowed:
        hh, mm           = map(int, hhmm.split(":"))
        target_total_min = hh * 60 + mm
        diff             = now_total_min - target_total_min

        if -grace_minutes <= diff <= grace_minutes:
            return True, (
                f"inside_window_et:{now_et.isoformat()} "
                f"target={hhmm} grace={grace_minutes}m diff={diff}m"
            )

    return False, (
        f"outside_window_et:{now_et.isoformat()} "
        f"allowed={allowed} grace={grace_minutes}m"
    )


def _load_prev_snapshot() -> dict:
    """직전 core_data.json에서 이전 스냅샷 로드 (급변 감지용)"""
    from config.settings import CORE_DATA_FILE
    try:
        if CORE_DATA_FILE.exists():
            with open(CORE_DATA_FILE, encoding="utf-8") as f:
                envelope = json.load(f)
            return envelope.get("data", {}).get("market_snapshot", {})
    except Exception:
        pass
    return {}


# ─────────────────────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────────────────────

def run() -> dict:
    logger.info("=" * 50)
    logger.info(f"[run_alert] v{VERSION} 시작 | DRY_RUN={DRY_RUN}")
    logger.info("=" * 50)

    # ── Pre-Step: 미국 동부시간 Alert 윈도우 체크 ─────────────
    if DRY_RUN:
        logger.info("[run_alert] DRY_RUN — 시간 윈도우 체크 생략, 강제 실행")
    else:
        should_run, reason = _is_alert_window()
        if not should_run:
            logger.info(f"[run_alert] 시간 윈도우 아님 — 스킵: {reason}")
            return {"alerts_detected": 0, "alerts_sent": 0, "reason": "outside_window"}
        logger.info(f"[run_alert] 시간 윈도우 통과: {reason}")

    # ── Step 0: DLQ 재처리 (B-17) ────────────────────────────
    try:
        from core.dlq import process_queue, get_queue_size
        q_size = get_queue_size()
        if q_size > 0:
            logger.info(f"[Step 0] DLQ 재처리 시작: {q_size}건")
            dlq_result = process_queue()
            logger.info(f"[Step 0] DLQ 완료: {dlq_result}")
        else:
            logger.info("[Step 0] DLQ 비어있음 — 스킵")
    except Exception as e:
        logger.warning(f"[Step 0] DLQ 처리 실패 (영향 없음): {e}")

    # ── Step 1: 데이터 수집 ────────────────────────────────────
    logger.info("[Step 1] 시장 데이터 + RSS 수집")
    from collectors.yahoo_finance import collect_market_snapshot
    from collectors.news_rss import collect_news_sentiment

    prev_snapshot = _load_prev_snapshot()
    snapshot      = collect_market_snapshot()
    news_result   = collect_news_sentiment()

    # ── Step 1-T2: Priority A — Tier2 + SPY SMA + FRED (v1.6.0 신규) ──
    tier2_data = {}
    try:
        from collectors.yahoo_finance import collect_tier2_market_data
        tier2_data = collect_tier2_market_data()
        logger.info(
            f"[Step 1-T2] Tier2 수집 완료 | "
            f"IWM={tier2_data.get('iwm_change')} "
            f"TLT={tier2_data.get('tlt_change')} "
            f"MOVE={tier2_data.get('move_index')}"
        )
    except Exception as e:
        logger.warning(f"[Step 1-T2] Tier2 수집 실패 (Alert 영향 없음): {e}")

    spy_sma_data = {}
    try:
        from collectors.yahoo_finance import collect_spy_sma
        spy_sma_data = collect_spy_sma()
        logger.info(
            f"[Step 1-SMA] SPY SMA 수집 완료 | "
            f"Price=${spy_sma_data.get('spy_price')} "
            f"SMA50=${spy_sma_data.get('spy_sma50')} "
            f"SMA200=${spy_sma_data.get('spy_sma200')}"
        )
    except Exception as e:
        logger.warning(f"[Step 1-SMA] SPY SMA 수집 실패 (Alert 영향 없음): {e}")

    fred_data_alert = {}
    try:
        from collectors.fred_client import collect_macro_data
        fred_data_alert = collect_macro_data()
        logger.info(
            f"[Step 1-FRED] FRED 수집 완료 | "
            f"spread={fred_data_alert.get('spread_2y10y_bp')}bp "
            f"us2y={fred_data_alert.get('us2y')}%"
        )
    except Exception as e:
        logger.warning(f"[Step 1-FRED] FRED 수집 실패 (Alert 영향 없음): {e}")

    # ── Step 1-M: FRED 경제지표 변화 감지 ─────────────────────
    try:
        from collectors.fred_client import collect_macro_data, detect_macro_changes
        from core.alert_history import should_send as _should_send, record_alert as _rec
        from publishers.econ_event_formatter import format_econ_event, format_econ_event_telegram
        from publishers.telegram_publisher import send_message as tg_send
        from publishers.x_publisher import publish_tweet as x_pub

        prev_macro = {}
        try:
            from core.json_builder import load_core_data
            prev_data  = load_core_data()
            prev_macro = prev_data.get("macro_data", {})
        except Exception:
            pass

        cur_macro     = collect_macro_data()
        macro_changes = detect_macro_changes(cur_macro, prev_macro)

        try:
            from core.json_builder import load_core_data as _lcd
            _cd     = _lcd()
            _regime = _cd.get("market_regime", {}).get("market_regime",   "—")
            _signal = _cd.get("trading_signal", {}).get("trading_signal", "HOLD")
        except Exception:
            _regime, _signal = "—", "HOLD"

        for mc in macro_changes:
            iid = mc["indicator_id"]
            _send, _reason = _should_send(f"ECON_{iid}", "L1")
            if not _send:
                logger.info(f"[Step 1-M] 경제지표 차단: {iid} — {_reason}")
                continue

            logger.info(f"[Step 1-M] 경제지표 변화: {iid} {mc['prev']} → {mc['new']}")

            tweet  = format_econ_event(iid, mc["prev"], mc["new"], _regime, _signal)
            tg_txt = format_econ_event_telegram(iid, mc["prev"], mc["new"], _regime, _signal)

            res = x_pub(tweet)
            _rec(f"ECON_{iid}", "L1", str(res.get("tweet_id", "FAIL")), tweet)
            tg_send(tg_txt, channel="free")
            logger.info(f"[Step 1-M] 경제지표 발행 완료: {iid}")
    except Exception as e:
        logger.warning(f"[Step 1-M] 경제지표 감지 실패 (영향 없음): {e}")

    # ── Step 2: Alert 엔진 실행 ────────────────────────────────
    logger.info("[Step 2] Alert 엔진 실행")

    # ── Step 2-B5: ETF 랭킹 변화 감지 ─────────────────────────
    rank_change        = None
    signal_diff_result = None
    signals_for_alert  = {}
    try:
        from core.rank_tracker import detect_rank_change
        from core.signal_diff import compute_signal_diff
        from core.json_builder import load_core_data as _lcd_rank

        _cd         = _lcd_rank()
        _data       = _cd.get("data", {})
        new_rank    = _data.get("etf_analysis", {}).get("etf_rank", {})
        new_signals = _data.get("signals", {})
        signals_for_alert = new_signals

        if new_rank:
            rank_change = detect_rank_change(new_rank)

            if rank_change:
                _prev_signals = {}
                try:
                    from core.regime_tracker import _load as _rt_load
                    _rt_data      = _rt_load()
                    _prev_signals = _rt_data.get("last_signals", {})
                except Exception:
                    pass

                if _prev_signals:
                    signal_diff_result = compute_signal_diff(_prev_signals, new_signals)
                    logger.info(
                        f"[Step 2-B5] 랭킹 변화 원인: "
                        f"{signal_diff_result.get('summary', 'N/A')}"
                    )
    except Exception as e:
        logger.warning(f"[Step 2-B5] ETF 랭킹 감지 실패 (영향 없음): {e}")

    # ── Step 2-B6: 레짐 전환 감지 ──────────────────────────────
    regime_change     = None
    score_diff_result = None
    try:
        from core.regime_tracker import detect_regime_change
        from core.signal_diff import compute_signal_diff as _sd2, compute_score_diff
        from core.json_builder import load_core_data as _lcd_regime

        _cd2           = _lcd_regime()
        _data2         = _cd2.get("data", {})
        new_regime     = _data2.get("market_regime", {}).get("market_regime",     "")
        new_risk_level = _data2.get("market_regime", {}).get("market_risk_level", "")
        new_mkt_score  = _data2.get("market_score", {})
        new_signals_r  = _data2.get("signals", {})

        if new_regime:
            regime_change = detect_regime_change(
                new_regime, new_risk_level, new_mkt_score, new_signals_r
            )

            if regime_change:
                old_score         = regime_change.get("old_market_score", {})
                new_score         = regime_change.get("new_market_score", {})
                score_diff_result = compute_score_diff(old_score, new_score)

                if signal_diff_result is None:
                    old_sigs           = regime_change.get("old_signals", {})
                    signal_diff_result = _sd2(old_sigs, new_signals_r)

                logger.info(
                    f"[Step 2-B6] 레짐 전환: {regime_change.get('old_regime')} → "
                    f"{regime_change.get('new_regime')} | "
                    f"원인: {signal_diff_result.get('summary', 'N/A')}"
                )
    except Exception as e:
        logger.warning(f"[Step 2-B6] 레짐 전환 감지 실패 (영향 없음): {e}")

    from engines.alert_engine import run_alert_engine
    alerts = run_alert_engine(
        snapshot, news_result, prev_snapshot or None,
        rank_change=rank_change,
        regime_change=regime_change,
        signal_diff_result=signal_diff_result,
        score_diff_result=score_diff_result,
        signals=signals_for_alert,
        tier2_data=tier2_data,
        fred_data=fred_data_alert,
        spy_sma_data=spy_sma_data,
    )

    if not alerts:
        logger.info("[run_alert] Alert 없음 — 정상 종료")
        return {"alerts_detected": 0, "alerts_sent": 0}

    # ── Step 2.5: 발행 직전 Validation Gate ───────────────────
    logger.info("[Step 2.5] Alert 발행 전 Validation Gate")

    _snap_valid  = True
    _snap_errors = []
    vix_val      = snapshot.get("vix",   0)
    spy_val      = snapshot.get("sp500", 0)

    if vix_val <= 0:
        _snap_errors.append(f"VIX 비정상: {vix_val}")
        _snap_valid = False
    if spy_val == 0 and vix_val == 0:
        _snap_errors.append("SPY/VIX 모두 0 — 수집 실패 의심")
        _snap_valid = False

    if not _snap_valid:
        logger.error(
            f"[Step 2.5] 스냅샷 Validation FAIL — Alert 발행 차단: {_snap_errors}"
        )
        return {
            "alerts_detected": len(alerts),
            "alerts_sent":     0,
            "reason":          "snapshot_validation_fail",
            "errors":          _snap_errors,
        }

    _core_valid = True
    try:
        from core.json_builder import load_core_data as _lcd_val
        from core.validator    import validate_data   as _vd

        _cd_val   = _lcd_val()
        _data_val = _cd_val.get("data", {})

        if _data_val:
            _vr = _vd(_data_val)
            if _vr["status"] != "PASS":
                logger.warning(
                    f"[Step 2.5] core_data Validation FAIL: {_vr.get('errors')}"
                    " — B-5/B-6 Alert 차단, 기존 Alert는 허용"
                )
                _core_valid = False
    except Exception as e:
        logger.info(f"[Step 2.5] core_data 로드 불가 — B-5/B-6 미동작 (정상): {e}")
        _core_valid = False

    if not _core_valid:
        before  = len(alerts)
        alerts  = [a for a in alerts if a.alert_type not in ("ETF_RANK", "REGIME_CHANGE")]
        removed = before - len(alerts)
        if removed > 0:
            logger.warning(
                f"[Step 2.5] core_data FAIL → B-5/B-6 Alert {removed}건 제거"
            )

        if not alerts:
            logger.info("[run_alert] Validation 후 발행 가능 Alert 없음 — 종료")
            return {
                "alerts_detected": before,
                "alerts_sent":     0,
                "reason":          "validation_filtered",
            }

    logger.info(f"[Step 2.5] Validation PASS — {len(alerts)}건 발행 진행")

    # ── Step 3: 쿨다운 체크 + 발송 ────────────────────────────
    from core.alert_history         import should_send, record_alert
    from publishers.alert_formatter import format_alert_tweet
    from publishers.x_publisher     import publish_tweet

    sent_count = 0
    results    = []
    x_history  = _load_x_alert_history()

    # core_data 로드 (유료 채널 + B-21A 밈에서 사용)
    data = {}
    try:
        from core.json_builder import load_core_data as _lcd_step3
        _cd_step3 = _lcd_step3()
        data      = _cd_step3.get("data", {})
    except Exception:
        pass

    for signal in alerts:

        # ── [패치1] VIX_COUNTDOWN: X 발행 제거 — x_eligible=False 원칙 적용 ──
        # 설계: VIX_COUNTDOWN은 사전 경고 정보성 알람 → TG 전용 발행
        if signal.alert_type == "VIX_COUNTDOWN":
            from core.alert_history         import should_send_countdown, record_countdown
            from publishers.alert_formatter import format_countdown_tweet
            vix_now   = signal.snapshot.get("vix", 0)
            from engines.alert_engine import VIX_COUNTDOWN_LEVELS
            triggered = max(
                (lvl for lvl in VIX_COUNTDOWN_LEVELS if vix_now >= lvl), default=None
            )
            if triggered is None:
                continue
            send, reason = should_send_countdown(triggered)
            if not send:
                logger.info(f"[run_alert] VIX 카운트다운 차단: {reason}")
                continue

            logger.info(f"[run_alert] VIX 카운트다운 TG 발행: VIX {triggered} — {reason}")
            tweet_cd = format_countdown_tweet(signal)
            # X 발행 없음 — x_eligible=False
            try:
                from publishers.telegram_publisher import send_message
                send_message(f"⚠️ <b>VIX 카운트다운</b>\n\n{tweet_cd}", channel="free")
                record_countdown(triggered, "TG_ONLY")
                sent_count += 1
                logger.info(f"[run_alert] VIX 카운트다운 TG 발행 완료: VIX {triggered}")
            except Exception as e:
                logger.warning(f"[run_alert] TG 카운트다운 발송 실패: {e}")

            results.append({
                "type": "VIX_COUNTDOWN", "level": triggered,
                "tweet_id": "TG_ONLY", "x_skipped": True,
            })
            continue

        # ── 쿨다운 체크 ───────────────────────────────────────
        send, reason = should_send(signal.alert_type, signal.level)
        if not send:
            logger.info(
                f"[run_alert] 발송 차단: {signal.alert_type}/{signal.level} — {reason}"
            )
            continue
        logger.info(
            f"[run_alert] 발송 결정: {signal.alert_type}/{signal.level} — {reason}"
        )

        # ── [패치2] X 발행 3-way 분기 + tweet_for_tg 완전 분리 ──
        # tweet_for_tg: TG 발행에 사용하는 기본 포맷 (X 발행 경로와 독립)
        # X 발행 경로에 따라 tweet_for_tg 값이 변경되지 않음을 보장
        _x_elig    = getattr(signal, "x_eligible", False)
        _x_cool    = _is_x_cooldown_active(signal.alert_type, x_history)
        tweet_for_tg = format_alert_tweet(signal)   # TG용 — 항상 기본 포맷
        tweet_id     = "SKIP_X"                     # X 발행 없는 경우 기본값

        if _x_elig and not _x_cool:
            # 케이스 A: x_eligible=True, 쿨다운 없음 → 감정 트리거 포맷 X 발행
            from publishers.x_publisher import post_alert_tweet
            tweet_for_x = _format_x_alert_tweet(signal, snapshot)
            logger.info(
                f"[run_alert] X 감정 발행 예정 [{signal.alert_type}/{signal.level}] "
                f"({len(tweet_for_x)}자)"
            )
            _x_ok    = post_alert_tweet(tweet_for_x, dry_run=DRY_RUN)
            tweet_id = "EMOTION" if _x_ok else "FAIL"
            if _x_ok:
                x_history[signal.alert_type] = datetime.now(timezone.utc).isoformat()
                record_alert(signal.alert_type, signal.level, tweet_id, tweet_for_x)
                sent_count += 1
                logger.info(
                    f"[run_alert] X 감정 발행 완료: {signal.alert_type}/{signal.level}"
                )
            else:
                logger.error(
                    f"[run_alert] X 감정 발행 실패: {signal.alert_type}/{signal.level}"
                )

        elif _x_elig and _x_cool:
            # 케이스 B: x_eligible=True, 쿨다운 중 → 기본 포맷 X 발행 (감정 포맷 중복 방지)
            logger.info(
                f"[run_alert] X 쿨다운 중 — 기본 포맷으로 발행: "
                f"{signal.alert_type}/{signal.level}"
            )
            logger.info(
                f"[run_alert] 발행 예정 [{signal.alert_type}/{signal.level}] "
                f"({len(tweet_for_tg)}자)\n{tweet_for_tg}"
            )
            result   = publish_tweet(tweet_for_tg)
            tweet_id = result.get("tweet_id", "FAIL")
            if result.get("success"):
                record_alert(signal.alert_type, signal.level, str(tweet_id), tweet_for_tg)
                sent_count += 1
                logger.info(
                    f"[run_alert] X 기본 발행 완료: "
                    f"{signal.alert_type}/{signal.level} → {tweet_id}"
                )
            else:
                logger.error(
                    f"[run_alert] X 기본 발행 실패: {signal.alert_type}/{signal.level}"
                )

        else:
            # 케이스 C: x_eligible=False → X 발행 완전 스킵 (TG만 진행)
            logger.info(
                f"[run_alert] x_eligible=False — X 발행 스킵: "
                f"{signal.alert_type}/{signal.level}"
            )

        # ── [패치3] Supabase store_daily_alert 단일 위치 통합 ──
        # X 발행 성공한 경우만 적재 (SKIP_X, FAIL 제외)
        if tweet_id not in ("SKIP_X", "FAIL"):
            try:
                from db.daily_store import store_daily_alert
                store_daily_alert(
                    alert_type=signal.alert_type,
                    alert_level=signal.level,
                    trigger_value=signal.reason[:100] if signal.reason else "",
                    tweet_id=str(tweet_id),
                )
            except Exception as db_err:
                logger.warning(f"[run_alert] Alert DB 적재 실패 (무시): {db_err}")

        # ── [패치5] B-21A 밈 이미지 선생성 ───────────────────
        # TG 발행 전에 미리 생성하여 TG free 통합 발행에 활용
        meme_path = None
        try:
            from comic.meme_generator import generate_meme
            meme_path = generate_meme(
                alert_type=signal.alert_type,
                alert_level=signal.level,
                snapshot=snapshot,
                core_data=data,
            )
        except Exception as e:
            logger.warning(f"[run_alert] B-21A 밈 생성 실패 (영향 없음): {e}")

        # ── [패치4] TG 무료 채널 — tweet_for_tg 사용, B-5/B-6 전용 포맷 우선 ──
        # tweet_for_tg는 항상 기본 포맷 — X 감정 포맷 유입 차단
        # 밈 이미지 있으면 TG 텍스트+이미지 통합 단일 발행 (중복 발행 제거)
        try:
            from publishers.telegram_publisher import send_message, send_photo

            if signal.alert_type == "ETF_RANK" and rank_change:
                # B-5: ETF 랭킹 상세 포맷 (원인 분석 포함)
                from publishers.alert_formatter import format_etf_rank_telegram
                _tg_regime = ""
                try:
                    from core.json_builder import load_core_data as _lcd_tg
                    _tg_regime = (
                        _lcd_tg().get("data", {})
                                 .get("market_regime", {})
                                 .get("market_regime", "")
                    )
                except Exception:
                    pass
                tg_text = format_etf_rank_telegram(
                    rank_change, signal_diff_result, _tg_regime
                )
                send_message(tg_text, channel="free")
                logger.info("[run_alert] TG 무료 B-5 상세 포맷 발송 완료")

            elif signal.alert_type == "REGIME_CHANGE" and regime_change:
                # B-6: 레짐 전환 상세 포맷 (Score + 시그널 원인)
                from publishers.alert_formatter import format_regime_change_telegram
                _tg_signal = ""
                _tg_top1   = ""
                try:
                    from core.json_builder import load_core_data as _lcd_tg2
                    _tg_d2     = _lcd_tg2().get("data", {})
                    _tg_signal = _tg_d2.get("trading_signal", {}).get("trading_signal", "")
                    _tg_rank   = _tg_d2.get("etf_analysis",  {}).get("etf_rank", {})
                    if _tg_rank:
                        _tg_top1 = min(_tg_rank, key=_tg_rank.get)
                except Exception:
                    pass
                tg_text = format_regime_change_telegram(
                    regime_change, signal_diff_result, score_diff_result,
                    _tg_signal, _tg_top1,
                )
                send_message(tg_text, channel="free")
                logger.info("[run_alert] TG 무료 B-6 상세 포맷 발송 완료")

            else:
                # 일반 Alert: tweet_for_tg(기본 포맷) 사용
                tg_text = f"🚨 <b>Investment OS Alert</b>\n\n{tweet_for_tg}"
                if meme_path:
                    # [패치5] 이미지 있으면 캡션+이미지 통합 단일 발행 (텍스트 별도 없음)
                    send_photo(meme_path, caption=tg_text, channel="free")
                    logger.info(
                        f"[run_alert] TG 무료 이미지+캡션 통합 발행: "
                        f"{signal.alert_type}/{signal.level}"
                    )
                else:
                    send_message(tg_text, channel="free")
                    logger.info(
                        f"[run_alert] TG 무료 텍스트 발행: "
                        f"{signal.alert_type}/{signal.level}"
                    )

            logger.info(
                f"[run_alert] TG 무료 채널 발송 완료: "
                f"{signal.alert_type}/{signal.level}"
            )
        except Exception as e:
            logger.warning(f"[run_alert] TG 무료 발송 예외 (X 발행 영향 없음): {e}")

        # ── TG 유료 채널 ───────────────────────────────────────
        try:
            vix_crossed = getattr(signal, "vix_premium_crossed", False)
            vix_level   = getattr(signal, "vix_premium_level",   None)
            prev_vix    = getattr(signal, "prev_vix",            0)

            snap    = data.get("market_snapshot", {})
            regime  = data.get("market_regime",  {}).get("market_regime",    "—")
            risk    = data.get("market_regime",  {}).get("market_risk_level", "—")
            matrix  = data.get("trading_signal", {}).get("signal_matrix", {})
            buy     = matrix.get("buy_watch", [])
            sig_val = data.get("trading_signal", {}).get("trading_signal", "HOLD")

            from publishers.telegram_publisher import send_message as tg_send
            from publishers.premium_alert_formatter import (
                format_vix_premium, format_regime_change_premium
            )

            if vix_crossed and vix_level:
                vix_now = snap.get("vix", 0)
                pm_text = format_vix_premium(vix_now, prev_vix, regime, risk)
                tg_send(pm_text, channel="paid")
                logger.info(f"[run_alert] 유료 채널 VIX {vix_level} 알람 발송")

            if signal.alert_type in ("CRISIS", "FED_SHOCK"):
                pm_text = format_regime_change_premium(
                    "—", regime, sig_val, risk, buy
                )
                tg_send(pm_text, channel="paid")
                logger.info("[run_alert] 유료 채널 레짐 전환 알람 발송")

            # B-6: 레짐 전환 L2 → 프리미엄 Score + 전략
            if (
                signal.alert_type == "REGIME_CHANGE"
                and signal.level   == "L2"
                and regime_change
            ):
                from publishers.premium_alert_formatter import format_regime_change_premium_v2
                _etf_rank = data.get("etf_analysis", {}).get("etf_rank", {})
                _etf_top1 = (
                    min(_etf_rank, key=_etf_rank.get) if _etf_rank else ""
                )
                pm_text = format_regime_change_premium_v2(
                    regime_change,
                    signal_diff_result=signal_diff_result,
                    score_diff_result=score_diff_result,
                    trading_signal=sig_val,
                    etf_top1=_etf_top1,
                    etf_hints=signal.etf_hints,
                    avoid_etfs=signal.avoid_etfs,
                )
                tg_send(pm_text, channel="paid")
                logger.info("[run_alert] 유료 채널 B-6 레짐 전환 프리미엄 발송")

        except Exception as e:
            logger.warning(f"[run_alert] 유료 채널 알람 예외 (영향 없음): {e}")

        # ── [패치5] B-21A: X 이미지 발행 (TG와 독립, meme_path 재사용) ──
        # TG 통합 발행은 위에서 완료. 여기서는 X 이미지만 발행.
        if meme_path:
            try:
                from publishers.x_publisher import publish_tweet_with_image
                _em_map = {
                    "VIX":         "📊",
                    "OIL":         "⛽",
                    "SPY":         "📉",
                    "CRISIS":      "🚨",
                    "FED_SHOCK":   "🏦",
                    "STAGFLATION": "🔥",
                    "SMA200_BREAK": "📉",
                    "PCR":         "📊",
                    "CRYPTO_BASIS": "₿",
                }
                _em   = _em_map.get(signal.alert_type, "⚡")
                _hint = ""
                if signal.alert_type == "OIL":
                    _p = snapshot.get("oil")
                    if _p:
                        _hint = f" WTI ${float(_p):.0f}"
                elif signal.alert_type == "VIX":
                    _p = snapshot.get("vix")
                    if _p:
                        _hint = f" VIX {float(_p):.1f}"
                elif "SPY" in signal.alert_type:
                    _p = snapshot.get("sp500")
                    if _p:
                        _hint = f" SPY {float(_p):+.1f}%"
                elif signal.alert_type == "CRISIS":
                    _hint = " 복합위기"

                meme_tweet = (
                    f"{_em} {signal.alert_type} {signal.level}{_hint}\n"
                    f"⚠️ 투자 참고 정보, 투자 권유 아님"
                )
                pub_r = publish_tweet_with_image(meme_tweet, meme_path)
                if pub_r.get("success"):
                    logger.info(f"[run_alert] B-21A X 이미지 발행 완료: {signal.alert_type}")
            except Exception as e:
                logger.warning(f"[run_alert] B-21A X 이미지 발행 예외 (영향 없음): {e}")

        results.append({
            "type":       signal.alert_type,
            "level":      signal.level,
            "tweet_id":   tweet_id,
            "x_eligible": _x_elig,
            "success":    tweet_id not in ("SKIP_X", "FAIL"),
        })

    # x_eligible Alert 쿨다운 이력 저장 (루프 후 1회)
    _save_x_alert_history(x_history)

    # ── 완료 ────────────────────────────────────────────────────
    summary = {
        "alerts_detected": len(alerts),
        "alerts_sent":     sent_count,
        "results":         results,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }

    logger.info("=" * 50)
    logger.info(f"[run_alert] 완료 — 감지:{len(alerts)}개 발송:{sent_count}개")
    logger.info("=" * 50)

    return summary


def main():
    try:
        run()
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[run_alert] 예외: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
