"""
gen/hero_shorts/tiktok/ — 틱톡 진입 라인 (분할 발행 + 합본 발행)

설계 정본: tiktok_design_v1.md v1.0 (마스터 승인 2026-10-13)
하위 모듈:
    gates.py    — T-G1/T-G2/T-G3 + 캡션 규율(G-4)
    caption.py  — 편별 캡션 생성(문구 창작 없음)
    manifest.py — 7컷 → 3편 발행 매니페스트 · from_combined 합본 1편
    publish.py  — Zernio 틱톡 발행(fail-closed)
    account.py  — 계정 토큰 preflight(만료·재연결 감지)
    runner.py   — CLI
"""
# =============================================================================
# ❄️ 동결(Freeze) — 2026-10-18 (마스터 결정 A안)
#
#   상태: **삭제하지 않는다. 기능을 확장하지도 않는다.**
#
#   삭제하지 않는 이유 3가지
#     1. watchdog.py 가 살아있는 감시 경로다 — .github/workflows/hero_shorts_watchdog.yml
#        이 `python -m gen.hero_shorts.tiktok.watchdog --expect actions` 를 실행 중이다.
#        삭제하면 감시가 끊긴다.
#     2. 레거시 원장 `tiktok{}` 블록을 **발행 활동 증거로 계속 집계**해야 한다
#        (LEDGER_BLOCK_NAMES). 블록을 지우면 과거 발행 이력이 감시에서 사라져
#        "활동 없음"으로 오판한다 — 무손실 보존 원칙.
#     3. 테스트 3파일이 이 패키지를 import 한다
#        (test_publish_fallback_d14 · test_timestamps · test_tiktok_suspension).
#
#   안전 근거 (발행 실행 경로는 이미 끊겨 있다)
#     · publish_runtime.TIKTOK_PUBLISH_SUSPENDED = True — 환경변수 오버라이드 불가
#     · assert_publish_allowed() 가 4개 지점에서 틱톡을 외부 호출 전에 차단
#     · tiktok.runner `ready` 는 status="suspended" 만 반환
#     · 스킬 라인은 tombstone(hero-shorts-tiktok-publish) · 스케줄은 취소됨
#
#   잠금 회귀 테스트: gen/hero_shorts/tests/test_tiktok_freeze.py
#   해제 조건: 마스터의 명시적 틱톡 라인 재개 지시. 그 전까지 수정 금지.
# =============================================================================
VERSION = "1.4.0"
