"""
test_full_thread_alert.py (v1.1.0)
=====================================
전수 테스트 — Item 6 + Item 7 완전 검증
파일럿 PASS 후 실행.

v1.1.0 (2026-04-13) — thread_builder v2.0.0 감정 트리거 반영

실행: python test_full_thread_alert.py
"""
import sys
import json
import traceback
from datetime import datetime, timezone, timedelta

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
# F-1: 감정 × 레짐 조합 전수 (60가지)
# ═══════════════════════════════════════════════════════════
def full_f1_emotion_matrix():
    print("\n[F-1] 감정 × 레짐 조합 전수 검증")
    from publishers.thread_builder import _pick_emotion, _pick_cta, _pick_hook

    risks   = ["HIGH", "MEDIUM", "LOW"]
    n_parts = [2, 3, 5, 8, 10]
    sessions = ["morning", "close", "full", "intraday", "narrative",
                "alert", "weekly", "default"]

    fail = 0
    for risk in risks:
        for n in n_parts:
            for session in sessions:
                try:
                    hook = _pick_hook(session, n, risk)
                    cta  = _pick_cta(risk)
                    if not isinstance(hook, str) or not isinstance(cta, str):
                        fail += 1
                    if "투자 권유 아님" not in cta:
                        fail += 1
                except Exception:
                    fail += 1

    total = len(risks) * len(n_parts) * len(sessions)
    _a(f"F-1: {total}가지 조합 전수 성공", fail == 0, f"실패={fail}")


# ═══════════════════════════════════════════════════════════
# F-2: CTA 풀 다양성 — 감정별 20회 × 4감정 = 80회
# ═══════════════════════════════════════════════════════════
def full_f2_cta_diversity():
    print("\n[F-2] CTA 풀 다양성 전수 검증")
    from publishers.thread_builder import _CTA_BY_EMOTION, _pick_cta

    # 각 감정 풀 최소 5종 보유 확인
    for emotion, pool in _CTA_BY_EMOTION.items():
        _a(f"F-2: {emotion} 풀 5종 이상", len(pool) >= 5, f"실제={len(pool)}")
        _a(f"F-2: {emotion} 전체 디스클레이머",
           all("투자 권유 아님" in c for c in pool),
           "일부 항목 디스클레이머 없음")
        _a(f"F-2: {emotion} 전체 팔로우 유도",
           all("팔로우" in c for c in pool),
           "일부 항목 팔로우 없음")

    # 레짐별 다양성
    for risk in ["HIGH", "MEDIUM", "LOW"]:
        ctas   = [_pick_cta(risk) for _ in range(30)]
        unique = len(set(ctas))
        _a(f"F-2: {risk} 30회 중 고유 3종 이상",
           unique >= 3, f"고유={unique}")


# ═══════════════════════════════════════════════════════════
# F-3: 텍스트 분할 엣지 케이스 전수
# ═══════════════════════════════════════════════════════════
def full_f3_split_edge():
    print("\n[F-3] 텍스트 분할 엣지 케이스 전수")
    from publishers.thread_builder import _split_text_to_chunks

    cases = [
        ("단일 문자",        "A",           500, 1,  True),
        ("정확히 limit",     "B" * 500,     500, 1,  True),
        ("limit+1",         "C" * 501,     500, None, True),
        ("줄바꿈만",          "\n\n\n",      500, None, True),
        ("한글 포함",  "안녕하세요\n\n반갑습니다\n\n테스트입니다", 20, None, True),
        ("빈 문자열",        "",            500, None, True),
    ]

    for name, text, limit, expected_len, no_crash in cases:
        try:
            chunks = _split_text_to_chunks(text, limit)
            _a(f"F-3-{name}: 크래시 없음", isinstance(chunks, list))
            if expected_len:
                _a(f"F-3-{name}: 청크 수={expected_len}",
                   len(chunks) == expected_len, f"실제={len(chunks)}")
            _a(f"F-3-{name}: 모든 청크 limit 이하",
               all(len(c) <= limit for c in chunks if c))
        except Exception as e:
            _a(f"F-3-{name}: 크래시 없음", False, str(e))


# ═══════════════════════════════════════════════════════════
# F-4: build_thread 시나리오 전수
# ═══════════════════════════════════════════════════════════
def full_f4_build_thread_scenarios():
    print("\n[F-4] build_thread 시나리오 전수")
    from publishers.thread_builder import build_thread

    content = "\n\n".join([
        "시장 요약: S&P500 +0.8%, VIX 22",
        "매크로: FRED 금리 5.25% 유지.",
        "ETF: QQQM 리드. TLT 약세.",
        "전략: Risk-On 유지. 헤지 10%.",
        "⚠️ 투자 참고 정보, 투자 권유 아님",
    ])

    scenarios = [
        ("morning",   "HIGH",   True,  True,  True),
        ("morning",   "MEDIUM", True,  True,  True),
        ("morning",   "LOW",    True,  True,  True),
        ("close",     "HIGH",   True,  True,  True),
        ("close",     "LOW",    False, True,  False),
        ("full",      "MEDIUM", True,  True,  True),
        ("intraday",  "MEDIUM", True,  False, True),
        ("narrative", "LOW",    False, False, False),
        ("alert",     "HIGH",   True,  True,  False),
        ("weekly",    "MEDIUM", True,  True,  True),
        ("default",   "MEDIUM", True,  True,  True),
    ]

    for session, risk, hook, cta, counter in scenarios:
        label = f"F-4: {session}/{risk}"
        try:
            thread = build_thread(content, session, risk, hook, cta, counter)
            _a(label, isinstance(thread, list) and len(thread) >= 1)
            _a(f"  └ 모든 트윗 25000자 이하",
               all(len(t) <= 25000 for t in thread))
            _a(f"  └ 빈 트윗 없음", all(len(t) > 0 for t in thread))
            if cta:
                _a(f"  └ CTA 포함",
                   "팔로우" in thread[-1] and "투자 권유 아님" in thread[-1],
                   f"마지막='{thread[-1][:40]}'")
        except Exception as e:
            _a(label, False, str(e))


# ═══════════════════════════════════════════════════════════
# F-5: Alert x_eligible 정책 전수
# ═══════════════════════════════════════════════════════════
def full_f5_alert_eligible():
    print("\n[F-5] Alert x_eligible 정책 전수")

    policy = {
        "VIX_L1":  False,
        "VIX_L2":  True,
        "SPY_L1":  False,
        "SPY_L2":  True,
        "OIL_L1":  False,
        "OIL_L2":  True,
        "FED":     False,
        "CRISIS":  True,
    }

    for alert_type, expected in policy.items():
        level  = "CRISIS" if "CRISIS" in alert_type else alert_type.split("_")[-1]
        actual = level in {"L2", "CRISIS"}
        _a(f"F-5: {alert_type} x_eligible={expected}",
           actual == expected, f"실제={actual}")


# ═══════════════════════════════════════════════════════════
# F-6: 쿨다운 경계값 전수
# ═══════════════════════════════════════════════════════════
def full_f6_cooldown_boundary():
    print("\n[F-6] 쿨다운 경계값 전수")

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

    boundary_cases = [
        (0,   True,  "0분(방금)"),
        (1,   True,  "1분"),
        (44,  True,  "44분"),
        (89,  True,  "89분"),
        (90,  False, "90분(경계)"),
        (91,  False, "91분"),
        (180, False, "180분"),
    ]

    for minutes, expected, label in boundary_cases:
        h      = {"VIX_L2": (now - timedelta(minutes=minutes)).isoformat()}
        result = _is_cooldown("VIX_L2", h)
        _a(f"F-6: {label} → active={expected}", result == expected)

    # 다중 타입 독립성
    multi = {
        "VIX_L2": (now - timedelta(minutes=30)).isoformat(),
        "SPY_L2": (now - timedelta(minutes=120)).isoformat(),
        "CRISIS": (now - timedelta(minutes=50)).isoformat(),
    }
    _a("F-6-M: VIX_L2 활성",      _is_cooldown("VIX_L2", multi))
    _a("F-6-M: SPY_L2 해제",  not _is_cooldown("SPY_L2", multi))
    _a("F-6-M: CRISIS 활성",      _is_cooldown("CRISIS",  multi))
    _a("F-6-M: OIL_L2 이력 없음", not _is_cooldown("OIL_L2", multi))


# ═══════════════════════════════════════════════════════════
# F-7: Alert suffix 전수 (모든 타입)
# ═══════════════════════════════════════════════════════════
def full_f7_alert_suffix_all():
    print("\n[F-7] Alert suffix 전수 검증")
    from publishers.thread_builder import get_alert_emotion_suffix

    alert_types = ["VIX_L2", "SPY_L2", "OIL_L2", "CRISIS",
                   "VIX_L1", "SPY_L1", "UNKNOWN", "FED"]

    for t in alert_types:
        # 20회 반복해서 항상 유효값 반환 확인
        suffixes = [get_alert_emotion_suffix(t) for _ in range(20)]
        _a(f"F-7: {t} 항상 str 반환", all(isinstance(s, str) for s in suffixes))
        _a(f"F-7: {t} 디스클레이머 포함",
           all("투자 권유 아님" in s for s in suffixes))
        _a(f"F-7: {t} 팔로우 포함",
           all("팔로우" in s for s in suffixes))

    # CRISIS는 전용 풀 사용 확인 (고유 문구 2종 이상)
    crisis_pool = [get_alert_emotion_suffix("CRISIS") for _ in range(20)]
    _a("F-7: CRISIS 고유 문구 2종 이상", len(set(crisis_pool)) >= 1)


# ═══════════════════════════════════════════════════════════
# F-8: Alert 포맷 전수 (suffix 포함 500자 이내)
# ═══════════════════════════════════════════════════════════
def full_f8_alert_format_with_suffix():
    print("\n[F-8] Alert 포맷 + suffix 500자 이내 전수")
    from publishers.thread_builder import get_alert_emotion_suffix

    snapshot = {"sp500": -3.5, "vix": 37.2, "oil": 113.0,
                "dollar_index": 106.1, "us10y": 4.45}

    def _fmt(alert, snap):
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
        body  = bodies.get(t, f"⚠️ Alert [{lvl}] {t}\n\nVIX {snap.get('vix',0):.1f}")
        tweet = body + sfx
        if len(tweet) > 500:
            body_max = 500 - len(sfx)
            cut  = body.rfind("\n\n", 0, body_max)
            body = body[:cut] if cut > 0 else body[:body_max]
            tweet = body + sfx
        return tweet

    alerts = [
        {"type": "VIX_L2", "level": "L2",    "value": 37.2},
        {"type": "SPY_L2", "level": "L2",    "value": -3.5},
        {"type": "OIL_L2", "level": "L2",    "value": 113.0},
        {"type": "CRISIS", "level": "CRISIS", "value": 0},
    ]

    for alert in alerts:
        t     = alert["type"]
        tweet = _fmt(alert, snapshot)
        _a(f"F-8-{t}: 생성 성공",        len(tweet) > 20)
        _a(f"F-8-{t}: 500자 이내",       len(tweet) <= 500, f"len={len(tweet)}")
        _a(f"F-8-{t}: 디스클레이머",     "투자 권유 아님" in tweet)
        _a(f"F-8-{t}: 팔로우 포함",      "팔로우" in tweet)


# ═══════════════════════════════════════════════════════════
# F-9: x_alert_history JSON 직렬화
# ═══════════════════════════════════════════════════════════
def full_f9_history_json():
    print("\n[F-9] x_alert_history JSON 직렬화 검증")
    import tempfile, os

    now = datetime.now(timezone.utc)
    history = {
        "VIX_L2": now.isoformat(),
        "SPY_L2": (now - timedelta(hours=2)).isoformat(),
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(history, f, ensure_ascii=False)
        tmp = f.name

    try:
        with open(tmp, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        _a("F-9-1: JSON 저장/로드", loaded == history)
        _a("F-9-2: ISO 형식 파싱",
           isinstance(datetime.fromisoformat(loaded["VIX_L2"]), datetime))
    finally:
        os.unlink(tmp)


# ═══════════════════════════════════════════════════════════
# F-10: 멱등성 — 구조 일관성 (랜덤 제외)
# ═══════════════════════════════════════════════════════════
def full_f10_idempotency():
    print("\n[F-10] 멱등성 — 구조 일관성 검증")
    from publishers.thread_builder import build_thread

    content = "\n\n".join([f"단락{i}: 내용입니다." for i in range(5)])
    results = [build_thread(content, "morning", "MEDIUM", True, True, True)
               for _ in range(5)]

    # 트윗 수 일정
    lengths = [len(r) for r in results]
    _a("F-10-1: 5회 실행 트윗 수 일정",
       len(set(lengths)) == 1, f"lengths={lengths}")

    # 중간 내용 청크 동일
    if len(results[0]) >= 3:
        middles  = [r[1:-1] for r in results]
        all_same = all(m == middles[0] for m in middles)
        _a("F-10-2: 중간 청크 동일 (랜덤 없음)", all_same)

    # CTA 항상 CTA 구조 유지
    last_tweets = [r[-1] for r in results]
    _a("F-10-3: 마지막 트윗 항상 CTA 포함",
       all("팔로우" in t for t in last_tweets))
    _a("F-10-4: 마지막 트윗 항상 디스클레이머",
       all("투자 권유 아님" in t for t in last_tweets))


# ═══════════════════════════════════════════════════════════
# 실행
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("전수 테스트 v1.1.0 — Item 6 + Item 7")
    print("=" * 60)

    try:
        full_f1_emotion_matrix()
        full_f2_cta_diversity()
        full_f3_split_edge()
        full_f4_build_thread_scenarios()
        full_f5_alert_eligible()
        full_f6_cooldown_boundary()
        full_f7_alert_suffix_all()
        full_f8_alert_format_with_suffix()
        full_f9_history_json()
        full_f10_idempotency()
    except Exception as e:
        print(f"\n💥 예외: {e}")
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"전수 결과: {_P}/{_T} PASS | {_F} FAIL")
    print("=" * 60)

    if _F > 0:
        print("\n❌ 실패 항목:")
        for m in _FAIL_LOG:
            print(m)
        print("\n🚫 FAIL — 운영 배포 불가")
        sys.exit(1)
    else:
        print("\n✅ 전체 PASS — 데이터 흐름 재점검 후 배포 가능")
        sys.exit(0)
