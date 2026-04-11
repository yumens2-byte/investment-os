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
  → X 발행 (DRY_RUN or 실제)
  → 이력 기록

이상 없으면 조용히 종료 (Alert 없음 = 정상)
"""
import json
import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os

from config.settings import LOG_LEVEL, DRY_RUN

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_alert")

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os


def _is_alert_window() -> tuple[bool, str]:
    """
    미국 동부시간 기준 Alert 실행 허용 시간인지 판정.
    exact match 대신 grace window를 둔다.
    예: 09:30 target, grace=5 이면 09:30:00 ~ 09:35:59 허용
    """
    tz_name = os.getenv("ALERT_TZ", "America/New_York")
    windows_raw = os.getenv("ALERT_WINDOWS", "09:30,10:30,15:30")
    grace_minutes = int(os.getenv("ALERT_GRACE_MINUTES", "5"))

    now_et = datetime.now(ZoneInfo(tz_name))
    weekday = now_et.weekday()  # Mon=0 ... Sun=6

    if weekday >= 5:
        return False, f"weekend_et:{now_et.isoformat()}"

    allowed = [x.strip() for x in windows_raw.split(",") if x.strip()]

    now_total_min = now_et.hour * 60 + now_et.minute

    for hhmm in allowed:
        hh, mm = map(int, hhmm.split(":"))
        target_total_min = hh * 60 + mm
        diff = now_total_min - target_total_min

        # target 시각 이후 ~ grace 분 이내만 허용
        if 0 <= diff <= grace_minutes:
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


def run() -> dict:
    logger.info("=" * 50)
    logger.info(f"[run_alert] 시작 | DRY_RUN={DRY_RUN}")
    logger.info("=" * 50)

    # ── Pre-Step: 미국 동부시간 Alert 윈도우 체크 ─────────────
    # DRY_RUN=True이면 시간/주말 체크 완전 생략 (테스트 모드)
    if DRY_RUN:
        logger.info("[run_alert] DRY_RUN — 시간 윈도우 체크 생략, 강제 실행")
    else:
        should_run, reason = _is_alert_window()
        if not should_run:
            logger.info(f"[run_alert] 시간 윈도우 아님 — 스킵: {reason}")
            return {
                "alerts_detected": 0,
                "alerts_sent": 0,
                "reason": "outside_alert_window",
                "detail": reason,
            }
        logger.info(f"[run_alert] 시간 윈도우 통과: {reason}")  # ← 4칸 추가

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

    # ── Step 1: 데이터 수집 ────────────────────────────────
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

     

    # ── Step 1-M: FRED 경제지표 변화 감지 ────────────────
    try:
        from collectors.fred_client import collect_macro_data, detect_macro_changes
        from core.alert_history import should_send as _should_send, record_alert as _rec
        from publishers.econ_event_formatter import format_econ_event, format_econ_event_telegram
        from publishers.telegram_publisher import send_message as tg_send
        from publishers.x_publisher import publish_tweet as x_pub

        # 이전 FRED 데이터 로드 (core_data.json 활용)
        prev_macro = {}
        try:
            from core.json_builder import load_core_data
            prev_data = load_core_data()
            prev_macro = prev_data.get("macro_data", {})
        except Exception:
            pass

        cur_macro = collect_macro_data()
        macro_changes = detect_macro_changes(cur_macro, prev_macro)

        # 레짐/시그널 정보
        try:
            from core.json_builder import load_core_data as _lcd
            _cd = _lcd()
            _regime = _cd.get("market_regime", {}).get("market_regime", "—")
            _signal = _cd.get("trading_signal", {}).get("trading_signal", "HOLD")
        except Exception:
            _regime, _signal = "—", "HOLD"

        for mc in macro_changes:
            iid = mc["indicator_id"]
            # 하루 1회 중복 방지 (alert_history 활용)
            _send, _reason = _should_send(f"ECON_{iid}", "L1")
            if not _send:
                logger.info(f"[Step 1-M] 경제지표 차단: {iid} — {_reason}")
                continue

            logger.info(f"[Step 1-M] 경제지표 변화: {iid} {mc['prev']} → {mc['new']}")

            tweet = format_econ_event(iid, mc["prev"], mc["new"], _regime, _signal)
            tg_txt = format_econ_event_telegram(iid, mc["prev"], mc["new"], _regime, _signal)

            res = x_pub(tweet)
            _rec(f"ECON_{iid}", "L1", str(res.get("tweet_id","FAIL")), tweet)
            tg_send(tg_txt, channel="free")
            logger.info(f"[Step 1-M] 경제지표 발행 완료: {iid}")
    except Exception as e:
        logger.warning(f"[Step 1-M] 경제지표 감지 실패 (영향 없음): {e}")

    # ── Step 2: Alert 엔진 실행 ───────────────────────────
    logger.info("[Step 2] Alert 엔진 실행")

    # ── Step 2-B5: ETF 랭킹 변화 감지 (B-5, 2026-04-01 추가) ──
    rank_change = None
    signal_diff_result = None
    signals_for_alert  = {}   # v1.0.0: PCR/Basis Alert용 signals
    try:
        from core.rank_tracker import detect_rank_change
        from core.signal_diff import compute_signal_diff
        from core.json_builder import load_core_data as _lcd_rank

        # 현재 core_data에서 ETF 랭킹 + signals 로드
        _cd = _lcd_rank()
        _data = _cd.get("data", {})
        new_rank = _data.get("etf_analysis", {}).get("etf_rank", {})
        new_signals = _data.get("signals", {})
        signals_for_alert = new_signals  # PCR/Basis Alert에 전달

        if new_rank:
            rank_change = detect_rank_change(new_rank)

            # 랭킹 변화가 있으면 → 시그널 변화량 분석
            if rank_change:
                # 이전 signals는 regime_tracker에서도 쓸 수 있으므로 여기서 로드
                _prev_signals = {}
                try:
                    from core.regime_tracker import _load as _rt_load
                    _rt_data = _rt_load()
                    _prev_signals = _rt_data.get("last_signals", {})
                except Exception:
                    pass

                if _prev_signals:
                    signal_diff_result = compute_signal_diff(_prev_signals, new_signals)
                    logger.info(
                        f"[Step 2-B5] 랭킹 변화 원인: {signal_diff_result.get('summary', 'N/A')}"
                    )
    except Exception as e:
        logger.warning(f"[Step 2-B5] ETF 랭킹 감지 실패 (영향 없음): {e}")

    # ── Step 2-B6: 레짐 전환 감지 (B-6, 2026-04-01 추가) ──
    regime_change = None
    score_diff_result = None
    try:
        from core.regime_tracker import detect_regime_change
        from core.signal_diff import compute_signal_diff as _sd2, compute_score_diff
        from core.json_builder import load_core_data as _lcd_regime

        _cd2 = _lcd_regime()
        _data2 = _cd2.get("data", {})
        new_regime = _data2.get("market_regime", {}).get("market_regime", "")
        new_risk_level = _data2.get("market_regime", {}).get("market_risk_level", "")
        new_market_score = _data2.get("market_score", {})
        new_signals_r = _data2.get("signals", {})

        if new_regime:
            regime_change = detect_regime_change(
                new_regime, new_risk_level, new_market_score, new_signals_r
            )

            # 레짐 전환이 있으면 → Score/시그널 변화량 분석
            if regime_change:
                old_score = regime_change.get("old_market_score", {})
                new_score = regime_change.get("new_market_score", {})
                score_diff_result = compute_score_diff(old_score, new_score)

                # signal_diff는 B-5에서 이미 계산했을 수 있으므로 없으면 계산
                if signal_diff_result is None:
                    old_sigs = regime_change.get("old_signals", {})
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
        signals=signals_for_alert,          # v1.0.0: PCR/Basis Alert
         # ── Priority A (v1.6.0 신규) ──────────────────────────
        tier2_data=tier2_data,              # IWM/TLT/MOVE → A-2/A-3/A-4 Alert
        fred_data=fred_data_alert,          # us2y/spread → A-5 Alert
        spy_sma_data=spy_sma_data,          # SMA50/200 → A-6 Alert
    )

    if not alerts:
        logger.info("[run_alert] Alert 없음 — 정상 종료")
        return {"alerts_detected": 0, "alerts_sent": 0}

    # ── Step 2.5: 발행 직전 Validation Gate (v1.16.0 추가) ──
    # run_view.py와 동일하게 발행 전 데이터 정합성 검증.
    # FAIL 시 Alert 발행 차단 → 비정상 데이터로 잘못된 Alert 방지.
    logger.info("[Step 2.5] Alert 발행 전 Validation Gate")

    # (1) 스냅샷 기본 sanity check — 수집 데이터 자체가 이상한 경우 차단
    _snap_valid = True
    _snap_errors = []
    vix_val = snapshot.get("vix", 0)
    spy_val = snapshot.get("sp500", 0)

    if vix_val <= 0:
        _snap_errors.append(f"VIX 비정상: {vix_val}")
        _snap_valid = False
    if spy_val == 0 and vix_val == 0:
        _snap_errors.append("SPY/VIX 모두 0 — 수집 실패 의심")
        _snap_valid = False

    if not _snap_valid:
        logger.error(f"[Step 2.5] 스냅샷 Validation FAIL — Alert 발행 차단: {_snap_errors}")
        return {
            "alerts_detected": len(alerts),
            "alerts_sent": 0,
            "reason": "snapshot_validation_fail",
            "errors": _snap_errors,
        }

    # (2) core_data.json Validation — B-5/B-6가 의존하는 데이터 검증
    _core_valid = True
    try:
        from core.json_builder import load_core_data as _lcd_val
        from core.validator import validate_data as _vd

        _cd_val = _lcd_val()
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
        # core_data 로드 실패 = B-5/B-6 동작 불가 (이미 try/except로 처리됨)
        logger.info(f"[Step 2.5] core_data 로드 불가 — B-5/B-6 미동작 (정상): {e}")
        _core_valid = False

    # core_data 검증 실패 시 B-5/B-6 Alert 제거
    if not _core_valid:
        before = len(alerts)
        alerts = [a for a in alerts if a.alert_type not in ("ETF_RANK", "REGIME_CHANGE")]
        removed = before - len(alerts)
        if removed > 0:
            logger.warning(f"[Step 2.5] core_data FAIL → B-5/B-6 Alert {removed}건 제거")

        if not alerts:
            logger.info("[run_alert] Validation 후 발행 가능 Alert 없음 — 종료")
            return {"alerts_detected": before, "alerts_sent": 0, "reason": "validation_filtered"}

    logger.info(f"[Step 2.5] Validation PASS — {len(alerts)}건 발행 진행")

    # ── Step 3: 쿨다운 체크 + 발송 ───────────────────────
    from core.alert_history import should_send, record_alert
    from publishers.alert_formatter import format_alert_tweet
    from publishers.x_publisher import publish_tweet

    sent_count = 0
    results = []

    # core_data 로드 (유료 채널 + B-21A 밈에서 사용)
    data = {}
    try:
        from core.json_builder import load_core_data as _lcd_step3
        _cd_step3 = _lcd_step3()
        data = _cd_step3.get("data", {})
    except Exception:
        pass  # core_data 없어도 기본 Alert 발행에 영향 없음

    for signal in alerts:
        # ── VIX 카운트다운 — 하루 1회 전용 처리 ─────────────────
        if signal.alert_type == "VIX_COUNTDOWN":
            from core.alert_history import should_send_countdown, record_countdown
            from publishers.alert_formatter import format_countdown_tweet
            vix_now = signal.snapshot.get("vix", 0)
            # 가장 가까운 카운트다운 레벨 찾기
            from engines.alert_engine import VIX_COUNTDOWN_LEVELS
            triggered = max((l for l in VIX_COUNTDOWN_LEVELS if vix_now >= l), default=None)
            if triggered is None:
                continue
            send, reason = should_send_countdown(triggered)
            if not send:
                logger.info(f"[run_alert] VIX 카운트다운 차단: {reason}")
                continue
            logger.info(f"[run_alert] VIX 카운트다운 발행: VIX {triggered} — {reason}")
            tweet = format_countdown_tweet(signal)
            result = publish_tweet(tweet)
            tweet_id = result.get("tweet_id", "FAIL")
            if result.get("success"):
                record_countdown(triggered, str(tweet_id))
                sent_count += 1
            # 텔레그램 무료 채널
            try:
                from publishers.telegram_publisher import send_message
                send_message(f"⚠️ <b>VIX 카운트다운</b>\n\n{tweet}", channel="free")
            except Exception as e:
                logger.warning(f"[run_alert] TG 카운트다운 발송 실패: {e}")
            results.append({"type": "VIX_COUNTDOWN", "level": triggered, "tweet_id": tweet_id})
            continue

        # 발송 여부 판단 (등급 변화 + 쿨다운)
        send, reason = should_send(signal.alert_type, signal.level)
        if not send:
            logger.info(f"[run_alert] 발송 차단: {signal.alert_type}/{signal.level} — {reason}")
            continue
        logger.info(f"[run_alert] 발송 결정: {signal.alert_type}/{signal.level} — {reason}")

        # 트윗 포맷 생성
        tweet = format_alert_tweet(signal)

        # 발행 직전 로그
        logger.info(
            f"[run_alert] 발행 예정 [{signal.alert_type}/{signal.level}] "
            f"({len(tweet)}자)\n{tweet}"
        )

        # X 발행
        result = publish_tweet(tweet)

        tweet_id = result.get("tweet_id", "FAIL")
        if result.get("success"):
            record_alert(signal.alert_type, signal.level, str(tweet_id), tweet)
            sent_count += 1
            logger.info(f"[run_alert] 발행 완료: {signal.alert_type}/{signal.level} → {tweet_id}")

            # ── Supabase Alert 적재 ──
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

        else:
            logger.error(f"[run_alert] 발행 실패: {signal.alert_type}/{signal.level}")

        # ── 텔레그램 무료 채널 — B-5/B-6는 전용 상세 포맷 사용 ──
        try:
            from publishers.telegram_publisher import send_message

            if signal.alert_type == "ETF_RANK" and rank_change:
                # B-5: ETF 랭킹 상세 포맷 (원인 분석 포함)
                from publishers.alert_formatter import format_etf_rank_telegram
                # 현재 레짐 정보 로드
                _tg_regime = ""
                try:
                    from core.json_builder import load_core_data as _lcd_tg
                    _tg_cd = _lcd_tg()
                    _tg_regime = _tg_cd.get("data", {}).get("market_regime", {}).get("market_regime", "")
                except Exception:
                    pass
                tg_text = format_etf_rank_telegram(rank_change, signal_diff_result, _tg_regime)
                send_message(tg_text, channel="free")
                logger.info(f"[run_alert] TG 무료 B-5 상세 포맷 발송 완료")

            elif signal.alert_type == "REGIME_CHANGE" and regime_change:
                # B-6: 레짐 전환 상세 포맷 (Score + 시그널 원인)
                from publishers.alert_formatter import format_regime_change_telegram
                # 현재 trading signal + ETF 1위 로드
                _tg_signal = ""
                _tg_top1 = ""
                try:
                    from core.json_builder import load_core_data as _lcd_tg2
                    _tg_cd2 = _lcd_tg2()
                    _tg_d2 = _tg_cd2.get("data", {})
                    _tg_signal = _tg_d2.get("trading_signal", {}).get("trading_signal", "")
                    _tg_rank = _tg_d2.get("etf_analysis", {}).get("etf_rank", {})
                    if _tg_rank:
                        _tg_top1 = min(_tg_rank, key=_tg_rank.get)
                except Exception:
                    pass
                tg_text = format_regime_change_telegram(
                    regime_change, signal_diff_result, score_diff_result,
                    _tg_signal, _tg_top1,
                )
                send_message(tg_text, channel="free")
                logger.info(f"[run_alert] TG 무료 B-6 상세 포맷 발송 완료")

            else:
                # 기존 Alert: X 트윗 텍스트 그대로
                tg_text = f"🚨 <b>Investment OS Alert</b>\n\n{tweet}"
                send_message(tg_text, channel="free")

            logger.info(f"[run_alert] 텔레그램 무료 채널 발송 완료: {signal.alert_type}/{signal.level}")
        except Exception as e:
            logger.warning(f"[run_alert] 텔레그램 발송 예외 (X 발행 영향 없음): {e}")

        # 텔레그램 유료 채널 — VIX 레벨 돌파 또는 레짐 전환 시 프리미엄 알람
        try:
            vix_crossed = getattr(signal, "vix_premium_crossed", False)
            vix_level   = getattr(signal, "vix_premium_level", None)
            prev_vix    = getattr(signal, "prev_vix", 0)

            snap   = data.get("market_snapshot", {})
            regime = data.get("market_regime", {}).get("market_regime", "—")
            risk   = data.get("market_regime", {}).get("market_risk_level", "—")
            matrix = data.get("trading_signal", {}).get("signal_matrix", {})
            buy    = matrix.get("buy_watch", [])
            sig_val = data.get("trading_signal", {}).get("trading_signal", "HOLD")

            from publishers.telegram_publisher import send_message as tg_send
            from publishers.premium_alert_formatter import (
                format_vix_premium, format_regime_change_premium
            )

            # VIX 프리미엄 레벨 돌파 알람
            if vix_crossed and vix_level:
                vix_now = snap.get("vix", 0)
                pm_text = format_vix_premium(vix_now, prev_vix, regime, risk)
                tg_send(pm_text, channel="paid")
                logger.info(f"[run_alert] 유료 채널 VIX {vix_level} 알람 발송")

            # 레짐 전환 알람 (alert_type이 CRISIS 또는 FED_SHOCK 시)
            if signal.alert_type in ("CRISIS", "FED_SHOCK"):
                pm_text = format_regime_change_premium(
                    "—", regime, sig_val, risk, buy
                )
                tg_send(pm_text, channel="paid")
                logger.info(f"[run_alert] 유료 채널 레짐 전환 알람 발송")

            # ── B-5/B-6 유료 채널 (L2 이상만) ─────────────────
            # # B-5: ETF 랭킹 Top1 변경 (L2) → 프리미엄 전체 랭킹 + 원인
            # if (signal.alert_type == "ETF_RANK"
            #         and signal.level == "L2"
            #         and rank_change):
            #     from publishers.premium_alert_formatter import format_etf_rank_premium
            #     pm_text = format_etf_rank_premium(
            #         rank_change,
            #         signal_diff_result=signal_diff_result,
            #         regime=regime,
            #         risk_level=risk,
            #         trading_signal=sig_val,
            #     )
            #     tg_send(pm_text, channel="paid")
            #     logger.info("[run_alert] 유료 채널 B-5 ETF 랭킹 프리미엄 발송")

            # B-6: 레짐 전환 danger/Shock (L2) → 프리미엄 Score + 전략
            if (signal.alert_type == "REGIME_CHANGE"
                    and signal.level == "L2"
                    and regime_change):
                from publishers.premium_alert_formatter import format_regime_change_premium_v2
                pm_text = format_regime_change_premium_v2(
                    regime_change,
                    signal_diff_result=signal_diff_result,
                    score_diff_result=score_diff_result,
                    trading_signal=sig_val,
                    etf_top1=min(
                        data.get("etf_analysis", {}).get("etf_rank", {"—": 1}),
                        key=data.get("etf_analysis", {}).get("etf_rank", {"—": 1}).get,
                        default="—"
                    ) if data.get("etf_analysis", {}).get("etf_rank") else "",
                    etf_hints=signal.etf_hints,
                    avoid_etfs=signal.avoid_etfs,
                )
                tg_send(pm_text, channel="paid")
                logger.info("[run_alert] 유료 채널 B-6 레짐 전환 프리미엄 발송")

        except Exception as e:
            logger.warning(f"[run_alert] 유료 채널 알람 예외 (영향 없음): {e}")

        # ── B-21A: 1컷 시장 밈 이미지 생성 + 발행 ────────────
        try:
            from comic.meme_generator import generate_meme
            meme_path = generate_meme(
                alert_type=signal.alert_type,
                alert_level=signal.level,
                snapshot=snapshot,
                core_data=data,
            )
            if meme_path:
                from publishers.x_publisher import publish_tweet_with_image
                meme_tweet = f"{tweet[:200]}"  # 기존 Alert 텍스트 축약
                pub_r = publish_tweet_with_image(meme_tweet, meme_path)
                if pub_r.get("success"):
                    logger.info(f"[run_alert] B-21A 밈 발행 완료: {signal.alert_type}")
                # TG 무료 채널에도 밈 이미지 발송
                try:
                    from publishers.telegram_publisher import send_photo
                    send_photo(meme_path, caption=f"⚡ {signal.alert_type} Alert", channel="free")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[run_alert] B-21A 밈 생성 예외 (영향 없음): {e}")

        results.append({
            "type": signal.alert_type,
            "level": signal.level,
            "tweet_id": tweet_id,
            "success": result.get("success"),
        })

    summary = {
        "alerts_detected": len(alerts),
        "alerts_sent": sent_count,
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("=" * 50)
    logger.info(f"[run_alert] 완료 — 감지:{len(alerts)}개 발송:{sent_count}개")
    logger.info("=" * 50)

    return summary


def main():
    try:
        result = run()
        # Alert 발송 성공이든 없든 정상 종료
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[run_alert] 예외: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
