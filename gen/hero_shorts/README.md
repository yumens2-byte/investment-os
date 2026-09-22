# gen/ — 히어로 쇼츠 파이프라인 v2.4 (캐논 유지·비용 최적화)

- 원장: EDT Arc State Ledger (노션) — 파서가 인덱스 코드블록 → 정본 page_id → arc_state
- 영상: fal H3 (768P $0.06/s) → 720p 트랜스코드 / 유형별 길이: BATTLE 60s·AFTERMATH 30s·FLASHBACK 40~50s
- 크레딧: 젠스파크 오미니 사용 금지(테스트 종료) — 크레딧 소모 0
- 캐논: `canon.py` 단일 출처 — 모든 프롬프트 자동 삽입, QC G2 검증
- 예산: 월 상한 $30 도달 시 자동 중단 (`gen/data/costs.json` 누적)

명령: python3 -m gen.hero_shorts.pipeline {plan|generate|assemble|qc|publish|run|test}
단건 생성: python3 -m gen.hero_shorts.pipeline generate --plan gen/data/out/ep86_plan.json --only 2 --backend fal  (키는 gen/.env 에 FAL_AI_KEY=... 형태로 주입 — 로그에 값 미출력)
전수테스트: python3 gen/hero_shorts/tests/test_all.py
상세설계: gen/docs/design_v2_4.md
