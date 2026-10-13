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
VERSION = "1.4.0"
