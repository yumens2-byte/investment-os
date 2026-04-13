"""
test_pilot_thread_alert.py (v1.1.0)
=====================================
파일럿 테스트 — Item 6 (thread_builder v2.0.0) + Item 7 (alert X 발행)
네트워크 없이 순수 로직만 검증.

v1.1.0 (2026-04-13) — thread_builder v2.0.0 감정 트리거 시스템 반영
  - _pick_cta(risk_level) 시그니처 변경 반영
  - 감정 트리거 선택 로직 검증 추가

실행: python test_pilot_thread_alert.py
기준: 전체 PASS 시에만 전수 테스트 진행
"""
import sys
import traceback

_T = _P = _F = 0
_FAIL_LOG = []


def _a(name: str, cond: bool, detail: str = ""):
    global _T, _P, _F
    _T += 1
    if cond:
        _P += 1
        print(f"  ✅ {name}")
    else:
        _F += 1
        msg = f"  ❌ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        _FAIL_LOG.append(msg)


# ═══════════════════════════════════════════════════════════
# P-1: import + 기본 구조
# ═══════════════════════════════════════════════════════════
def pilot_p1_import():
    print("\n[P-1] thread_builder v2.0.0 import + 기본 구조 확인")
    try:
        from publishers.thread_builder import (
            build_thread,
            build_single_tweet,
            get_alert_emotion_suffix,
            _pick_hook,
            _pick_cta,
            _pick_emotion,
            _split_text_to_chunks,
            VERSION,
        )
        _a("import OK", True)
        _a("VERSION == 2.0.0", VERSION == "2.0.0", f"실제={VERSION}")

        import inspect
        sig_bt  = inspect.signature(build_thread)
        sig_cta = inspect.signature(_pick_cta)
        _a("build_thread 파라미터 확인",
           all(p in sig_bt.parameters for p in ["content", "session", "risk_level"]))
        _a("_pick_cta risk_level 파라미터 확인",
           "risk_level" in sig_cta.parameters)

    except ImportError as e:
        _a("import OK", False, str(e))
        print("  ⚠️  thread_builder.py 파일 없음 — P-1 이후 스킵")
        return False
    return True


# ═══════════════════════════════════════════════════════════
# P-2: 감정 트리거 선택 로직
# ═══════════════════════════════════════════════════════════
def pilot_p2_emotion():
    print("\n[P-2] 감정 트리거 선택 로직 검증")
    from publishers.thread_builder import _pick_emotion

    valid_emotions = {"FOMO", "DESIRE", "COMPARE", "ANGER"}

    for risk in ["HIGH", "MEDIUM", "LOW"]:
        results = [_pick_emotion(risk) for _ in range(20)]
        _a(f"P-2: {risk} 감정 모두 유효값",
           all(r in valid_emotions for r in results),
           f"이상값={[r for r in results if r not in valid_emotions]}")

    # HIGH → FOMO 우세 (20회 중 8회 이상)
    high_results = [_pick_emotion("HIGH") for _ in range(20)]
    fomo_cnt = high_results.count("FOMO")
    _a("P-2: HIGH → FOMO 우세 (20회 중 8회+)",
       fomo_cnt >= 8, f"FOMO={fomo_cnt}/20")

    # LOW → DESIRE 우세
    low_results = [_pick_emotion("LOW") for _ in range(20)]
    desire_cnt = low_results.count("DESIRE")
    _a("P-2: LOW → DESIRE 우세 (20회 중 8회+)",
       desire_cnt >= 8, f"DESIRE={desire_cnt}/20")

    # 잘못된 risk_level → 크래시 없음
    try:
        result = _pick_emotion("UNKNOWN")
        _a("P-2: 잘못된 risk_level → 크래시 없음", result in valid_emotions)
    except Exception as e:
        _a("P-2: 잘못된 risk_level 처리", False, str(e))


# ═══════════════════════════════════════════════════════════
# P-3: CTA 감정별 검증
# ═══════════════════════════════════════════════════════════
def pilot_p3_cta():
    print("\n[P-3] CTA 감정 기반 검증")
    from publishers.thread_builder import _pick_cta

    for risk in ["HIGH", "MEDIUM", "LOW"]:
        ctas = [_pick_cta(risk) for _ in range(20)]

        # 디스클레이머 전수 포함
        _a(f"P-3: {risk} 디스클레이머 전수 포함",
           all("투자 권유 아님" in c for c in ctas),
           "일부 CTA 디스클레이머 없음")

        # 팔로우 유도 전수 포함
        _a(f"P-3: {risk} 팔로우 유도 전수 포함",
           all("팔로우" in c for c in ctas),
           "일부 CTA 팔로우 없음")

        # 고유값 다양성
        unique = len(set(ctas))
        _a(f"P-3: {risk} 고유 CTA 2종 이상",
           unique >= 2, f"고유={unique}/20")

    # HIGH → FOMO/ANGER 계열 우세 (손실 프레임 키워드)
    high_ctas = [_pick_cta("HIGH") for _ in range(30)]
    loss_keywords = ["늦습니다", "위험", "못 보면", "당하지", "격차"]
    loss_cnt = sum(1 for c in high_ctas if any(kw in c for kw in loss_keywords))
    _a("P-3: HIGH → 손실 프레임 키워드 우세 (30회 중 12회+)",
       loss_cnt >= 12, f"손실 프레임={loss_cnt}/30")


# ═══════════════════════════════════════════════════════════
# P-4: 후킹 문구 검증
# ═══════════════════════════════════════════════════════════
def pilot_p4_hook():
    print("\n[P-4] 후킹 문구 감정 기반 검증")
    from publishers.thread_builder import _pick_hook

    sessions = ["morning", "close", "full", "intraday", "narrative", "alert", "default"]

    for session in sessions:
        for risk in ["HIGH", "MEDIUM", "LOW"]:
            try:
                hook = _pick_hook(session, 5, risk)
                _a(f"P-4: {session}/{risk} 생성 성공",
                   isinstance(hook, str) and len(hook) > 5)
            except Exception as e:
                _a(f"P-4: {session}/{risk} 생성 성공", False, str(e))

    # HIGH → 손실/긴급 키워드 우세
    high_hooks = [_pick_hook("morning", 5, "HIGH") for _ in range(20)]
    urgent_kw  = ["늦습니다", "잡았습니다", "놓치면", "뒤처집니다", "못 봤습니까"]
    urgent_cnt = sum(1 for h in high_hooks if any(kw in h for kw in urgent_kw))
    _a("P-4: HIGH morning → 긴급 키워드 우세 (20회 중 8회+)",
       urgent_cnt >= 8, f"긴급={urgent_cnt}/20")

    # LOW → 기회/데이터 키워드
    low_hooks  = [_pick_hook("morning", 5, "LOW") for _ in range(20)]
    opport_kw  = ["기회", "데이터", "정리", "분석", "핵심"]
    opport_cnt = sum(1 for h in low_hooks if any(kw in h for kw in opport_kw))
    _a("P-4: LOW morning → 기회 키워드 포함 (20회 중 5회+)",
       opport_cnt >= 5, f"기회={opport_cnt}/20")


# ═══════════════════════════════════════════════════════════
# P-5: 텍스트 분할
# ═══════════════════════════════════════════════════════════
def pilot_p5_split():
    print("\n[P-5] 텍스트 분할 로직 검증")
    from publishers.thread_builder import _split_text_to_chunks

    # 짧은 텍스트 → 1청크
    c = _split_text_to_chunks("VIX 22, SPY +0.5%.", limit=500)
    _a("P-5-1: 짧은 텍스트 1청크", len(c) == 1)

    # 5단락 → 복수 청크
    long = "\n\n".join([f"단락{i}: " + "X" * 200 for i in range(5)])
    c2   = _split_text_to_chunks(long, limit=500)
    _a("P-5-2: 5단락 → 복수 청크", len(c2) >= 2)
    _a("P-5-3: 모든 청크 limit 이하", all(len(x) <= 500 for x in c2))

    # 빈 텍스트 → 크래시 없음
    try:
        c3 = _split_text_to_chunks("", limit=500)
        _a("P-5-4: 빈 텍스트 처리 OK", isinstance(c3, list))
    except Exception as e:
        _a("P-5-4: 빈 텍스트 처리 OK", False, str(e))


# ═══════════════════════════════════════════════════════════
# P-6: build_thread 통합
# ═══════════════════════════════════════════════════════════
def pilot_p6_build_thread():
    print("\n[P-6] build_thread 통합 검증")
    from publishers.thread_builder import build_thread

    sample = "\n\n".join([
        "📊 [Morning Brief] 2026-04-13\n\nS&P500 +0.8% | VIX 22.1 | Oil $78",
        "🔍 주요 시그널\n\nFear&Greed: 45 (Neutral) | Credit Spread 3.2%",
        "💡 오늘 주목 포인트\n\nFed 발언 예정. 금리 경로 주목 필요.",
        "📈 ETF 전략\n\nQQQM 25% | XLK 20% | TLT 15% 비중 권장.",
        "⚠️ 투자 참고 정보, 투자 권유 아님",
    ])

    thread = build_thread(sample, "morning", "MEDIUM", True, True, True)

    _a("P-6-1: 리스트 반환", isinstance(thread, list))
    _a("P-6-2: 최소 3개 트윗 (hook+내용+cta)", len(thread) >= 3, f"len={len(thread)}")
    _a("P-6-3: 마지막 트윗 CTA 포함",
       "팔로우" in thread[-1] and "투자 권유 아님" in thread[-1])
    _a("P-6-4: 모든 트윗 25000자 이하",
       all(len(t) <= 25000 for t in thread))

    # HIGH 레짐
    thread_h = build_thread(sample, "morning", "HIGH", True, True, True)
    _a("P-6-5: HIGH 레짐 정상 생성",
       isinstance(thread_h, list) and len(thread_h) >= 3)

    # hook=False, cta=False
    thread_n = build_thread(sample, "close", "LOW", False, False, False)
    _a("P-6-6: hook=False cta=False 정상 처리", isinstance(thread_n, list))


# ═══════════════════════════════════════════════════════════
# P-7: Alert suffix
# ═══════════════════════════════════════════════════════════
def pilot_p7_alert_suffix():
    print("\n[P-7] Alert 감정 suffix 검증")
    from publishers.thread_builder import get_alert_emotion_suffix

    for alert_type in ["VIX_L2", "SPY_L2", "OIL_L2", "CRISIS", "UNKNOWN_TYPE"]:
        suffix = get_alert_emotion_suffix(alert_type)
        _a(f"P-7: {alert_type} suffix 생성", isinstance(suffix, str) and len(suffix) > 5)
        _a(f"P-7: {alert_type} 디스클레이머 포함", "투자 권유 아님" in suffix)
        _a(f"P-7: {alert_type} 팔로우 포함", "팔로우" in suffix)


# ═══════════════════════════════════════════════════════════
# P-8: Alert 포맷 로직
# ═══════════════════════════════════════════════════════════
def pilot_p8_alert_format():
    print("\n[P-8] Alert X 포맷 로직 검증")
    from publishers.thread_builder import get_alert_emotion_suffix

    snapshot = {"sp500": -3.2, "vix": 36.5, "oil": 82.0,
                "dollar_index": 104.2, "us10y": 4.35}

    test_cases = [
        {"type": "VIX_L2",  "level": "L2",     "value": 36.5,  "x_eligible": True},
        {"type": "SPY_L2",  "level": "L2",     "value": -3.2,  "x_eligible": True},
        {"type": "OIL_L2",  "level": "L2",     "value": 112.0, "x_eligible": True},
        {"type": "CRISIS",  "level": "CRISIS",  "value": 0,     "x_eligible": True},
        {"type": "VIX_L1",  "level": "L1",     "value": 31.0,  "x_eligible": False},
        {"type": "SPY_L1",  "level": "L1",     "value": -2.1,  "x_eligible": False},
    ]

    def _fmt_local(alert, snap):
        t   = alert["type"]
        v   = alert["value"]
        lvl = alert["level"]
        sfx = get_alert_emotion_suffix(t)
        bodies = {
            "VIX_L2": f"🚨 VIX {v:.1f} 돌파 — 공포지수 극단 구간 진입\n\n현재 SPY {snap.get('sp500',0):+.1f}% | Oil ${snap.get('oil',0):.0f}\n\n변동성 35+ 구간은 역사적으로 저가 매수 기회이기도 하지만 추가 하락 리스크도 공존합니다.",
            "SPY_L2": f"🔴 S&P500 {v:+.1f}% — 장중 급락 감지\n\n현재 VIX {snap.get('vix',0):.1f} | Oil ${snap.get('oil',0):.0f} | 10Y {snap.get('us10y',0):.2f}%\n\n-3% 이상 하락은 알고리즘 매도 가속 구간입니다.",
            "OIL_L2": f"⛽ WTI 유가 ${v:.0f} 돌파 — 인플레이션 재점화 신호\n\n현재 SPY {snap.get('sp500',0):+.1f}% | DXY {snap.get('dollar_index',0):.1f}\n\n유가 $110 이상은 Fed 긴축 장기화 압박으로 이어질 수 있습니다.",
            "CRISIS":  f"🆘 복합 위기 시그널 감지\n\nVIX {snap.get('vix',0):.1f} | SPY {snap.get('sp500',0):+.1f}% | Oil ${snap.get('oil',0):.0f}\n\n다중 지표 동시 경보 — 리스크 관리 최우선 구간입니다.",
        }
        body  = bodies.get(t, f"⚠️ Alert [{lvl}] {t}")
        tweet = body + sfx
        if len(tweet) > 500:
            body_max = 500 - len(sfx)
            tweet = body[:body_max] + sfx
        return tweet

    for tc in test_cases:
        t     = tc["type"]
        tweet = _fmt_local(tc, snapshot)
        _a(f"P-8: {t} 생성 성공", isinstance(tweet, str) and len(tweet) > 20)
        _a(f"P-8: {t} 디스클레이머", "투자 권유 아님" in tweet)
        _a(f"P-8: {t} 500자 이내", len(tweet) <= 500, f"len={len(tweet)}")

    eligible     = [tc for tc in test_cases if tc["x_eligible"]]
    not_eligible = [tc for tc in test_cases if not tc["x_eligible"]]
    _a("P-8-E: x_eligible=True 4건", len(eligible) == 4)
    _a("P-8-N: x_eligible=False 2건", len(not_eligible) == 2)


# ═══════════════════════════════════════════════════════════
# P-9: 쿨다운 로직
# ═══════════════════════════════════════════════════════════
def pilot_p9_cooldown():
    print("\n[P-9] X Alert 쿨다운 로직 검증")
    from datetime import datetime, timezone, timedelta

    def _is_cooldown(alert_type, history, cooldown_min=90):
        last_str = history.get(alert_type)
        if not last_str:
            return False
        try:
            last_dt = datetime.fromisoformat(last_str)
            return datetime.now(timezone.utc) - last_dt < timedelta(minutes=cooldown_min)
        except Exception:
            return False

    now = datetime.now(timezone.utc)

    _a("P-9-1: 이력 없음 → False",  not _is_cooldown("VIX_L2", {}))
    _a("P-9-2: 30분 전 → True",
       _is_cooldown("VIX_L2", {"VIX_L2": (now - timedelta(minutes=30)).isoformat()}))
    _a("P-9-3: 120분 전 → False",
       not _is_cooldown("VIX_L2", {"VIX_L2": (now - timedelta(minutes=120)).isoformat()}))
    _a("P-9-4: 타입 다름 독립",
       not _is_cooldown("SPY_L2", {"VIX_L2": (now - timedelta(minutes=30)).isoformat()}))
    _a("P-9-5: 잘못된 날짜 → False",
       not _is_cooldown("VIX_L2", {"VIX_L2": "NOT_A_DATE"}))


# ═══════════════════════════════════════════════════════════
# P-10: x_formatter 연동
# ═══════════════════════════════════════════════════════════
def pilot_p10_formatter():
    print("\n[P-10] x_formatter format_thread_auto 연동")
    try:
        from publishers.x_formatter import format_thread_auto
        _a("P-10-1: format_thread_auto import OK", True)

        mock_core = {"market_regime": {"market_risk_level": "MEDIUM"}}
        result    = format_thread_auto("VIX 22, SPY +0.5%. 시장 안정.", "morning", mock_core)

        _a("P-10-2: 리스트 반환", isinstance(result, list))
        _a("P-10-3: 최소 1개", len(result) >= 1)
        _a("P-10-4: 디스클레이머 포함",
           any("투자 권유 아님" in t for t in result))

    except ImportError:
        _a("P-10-1: format_thread_auto import OK", False,
           "x_formatter.py에 format_thread_auto 미추가")
    except Exception as e:
        _a("P-10: 실행 오류", False, str(e))


# ═══════════════════════════════════════════════════════════
# 실행
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("파일럿 테스트 v1.1.0")
    print("Item 6 (thread_builder v2.0.0) + Item 7 (Alert X 발행)")
    print("=" * 60)

    try:
        ok = pilot_p1_import()
        if ok:
            pilot_p2_emotion()
            pilot_p3_cta()
            pilot_p4_hook()
            pilot_p5_split()
            pilot_p6_build_thread()
            pilot_p7_alert_suffix()
        pilot_p8_alert_format()
        pilot_p9_cooldown()
        if ok:
            pilot_p10_formatter()
    except Exception as e:
        print(f"\n💥 예외: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"파일럿 결과: {_P}/{_T} PASS | {_F} FAIL")
    print("=" * 60)

    if _F > 0:
        print("\n❌ 실패 항목:")
        for m in _FAIL_LOG:
            print(m)
        print("\n🚫 FAIL — 전수 테스트 진행 불가")
        sys.exit(1)
    else:
        print("\n✅ 파일럿 PASS — 전수 테스트 진행 가능")
        sys.exit(0)
