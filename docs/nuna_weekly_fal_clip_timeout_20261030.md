# NUNA W44 clips 타임아웃 장애 분석 · 상세설계 · 수정 기록 (2026-10-30)

## 1. 장애 사실 (실행 로그 기준)

- 실행: https://github.com/yumens2-byte/investment-os/actions/runs/40799442842 (nuna-weekly-gen #7, schedule, `cd2a3fa`)
- 회차 `nuna-2026-W44`, `dry=False`, 길이 60s, 시장소스 direct.
- collect ✔ 2.7s → script ✔ 69.7s(1차 검증 실패 "근거 없는 숫자 10" S3·S4·S5, 2차 통과) → tts ✔ 34.8s → images ✔ 59.0s(5장, 장당 약 10s).
- clips: S1 1건 제출(`request_id=01a1853e-6481-7811-bbfe-703d03a5feea`) 후 300s 폴링 종료 시점 `last_status=IN_PROGRESS`.
  `FalError: fal 작업 타임아웃(300s) … — pending 유지(재실행 시 재개)` → exit 1. S8 은 제출되지 않음.
- 비용: clips 행이 예약액 $0.35 로 `charged`, request_id 없음.
- 정기 실행 시작이 cron(21:47 UTC) 대비 01:16 UTC 로 약 3.5시간 지연 — 본 장애와 무관.

## 2. 원인 (코드)

| # | 위치(수정 전) | 내용 |
|---|---|---|
| R1 | `backends/fal.py:73-74`, `backends/__init__.py:86` | 이미지·클립 공통 대기 상한 3s×100=300s. 클립 처리 시간에 비해 짧음 |
| R2 | `backends/fal.py:149`, 워크플로 upload-artifact(qc/ 만) | pending 파일이 러너 로컬에만 존재 → 러너 종료 시 소실 |
| R3 | `pipeline.py:329-331, 340-341` | last_error 가 `clips:` 로 기록되어 재실행은 `PAID_RECOVERY_REQUIRED` 차단 — 메시지("재실행 시 재개")와 반대 |
| R4 | `budget.py` 예외 경로, `clips.py:91-92` | 결과 불명 정산 시 request_id 미기록 → DB 에 대사 근거 없음 |
| R5 | `sql/nuna_001_tables.sql:59` | `nuna_cost_ledger.request_id UNIQUE` — 같은 작업을 재개하며 새 비용 행에 같은 request_id 를 쓰면 DB 오류. 재개는 비용 예약 없이 해야 함 |

## 3. 상세설계

### A. 작업 종류별 대기 상한
- `config.fal_image_max_wait_sec()` = `NUNA_FAL_IMAGE_MAX_WAIT_SEC`, 기본 300.
- `config.fal_clip_max_wait_sec()` = `NUNA_FAL_CLIP_MAX_WAIT_SEC`, 기본 900.
- 허용 범위 60~1200초 정수, 밖이거나 정수가 아니면 `ValueError`(조용한 폴백 금지).
- 회차 합계 상한 `FAL_WAIT_TOTAL_MAX_SEC` = 70분. `run_gen` 이 유료 단계 전(PAID_STEP_IN_PROGRESS 기록 전)에
  `이미지 수×이미지 상한 + 클립 수×클립 상한` 을 검사한다 — 잘못된 vars 가 무과금 영구 차단을 만들지 않게 한다.
- `FalQueueBackend.run(max_wait_sec=)` → 폴링 횟수 `ceil(max_wait_sec / poll_interval)`.
- 워크플로 `timeout-minutes` 40 → 90: fal 대기 합계 상한 70분(기본값 60초판 55분, 70초판 6×300+2×900=60분)
  + 설치·대본·TTS·조립 여유.
  폴링 HTTP 소요시간은 상한에 포함되지 않으므로 실제 경과는 상한보다 길 수 있다.
- 900s 는 설계값이다. 실측 처리시간은 fal 대시보드에서 위 request_id 로 확인 후 vars 로 조정한다.

### B. 작업 기록 원장 보존 · 새 러너 재개
- 제출 직후(폴링 전) `on_submit(job)` 콜백:
  1. `_Charge.submitted(request_id)` → 결과 불명 정산 시에도 비용 원장에 request_id 기록.
  2. `assets_json.fal_pending[<img_Sx|clip_Sx>] = {endpoint, signature, request_id, status_url, response_url, created_at}` 즉시 PATCH.
- 신규 모듈 `fal_jobs.run_paid()` (images·clips 공용):
  - 원장 또는 로컬 pending 에 같은 endpoint·payload signature 기록 → `submit_allowed=False` 로 재조회만, **비용 예약 없음**(R5).
  - 원장 기록과 signature 불일치 → `FAL_JOB_MISMATCH` 로 차단(진행 중 작업 폐기·재과금 방지). 대사 후 해당 키 삭제 필요.
  - 기록 없음 → `budget.charge` 안에서 제출.
- 작업 기록은 단계 성공 시에만 `_FalJobs.clear_prefix()` 로 제거(전이 필드와 함께 저장).
  일부 구간만 완료된 뒤 실패해도 새 러너에서 완료 작업 결과를 재과금 없이 다시 받는다.

### C. 실패 분류
- `FalPendingTimeout(FalError)`: request_id·last_status·persisted.
- persisted(원장 기록 확인) 타임아웃만 last_error `FAL_PENDING:<step>: …` — 기존 차단 접두(`<step>:`, `PAID_STEP_IN_PROGRESS:<step>`)와 겹치지 않음.
- 재실행 시 `FAL_PENDING:<step>:` 인데 해당 접두 기록이 없으면 `PAID_RECOVERY_REQUIRED` 유지.
- 그 외 모든 오류·강제 중단은 기존 차단 그대로. 재개 중 러너가 죽으면 `PAID_STEP_IN_PROGRESS` 로 다시 차단(보수적).
- 텔레그램 실패 알림: FAL_PENDING 은 "재제출 없이 재조회(추가 과금 없음)" 안내.

### D. 테스트 (`tests/test_fal_pending_recovery.py`, 18건)
대기 상한 기본값·범위, 폴링 횟수, 폴링 전 on_submit, 원장 기록 재개(POST 0), 재개 전용 시 불일치 재제출 차단,
타임아웃 시 원장 request_id·작업 기록 보존, 재개 시 비용 행 0, payload 변경 시 차단(POST 0·비용 0),
대기 합계 초과 vars 는 유료 표식 전 중단, 기본값은 60·70초판 모두 합계 상한 이내,
W44 재현(1차 FAL_PENDING → 새 러너 S1 재제출 없음·S8 만 신규·비용 2행·fal_pending 제거),
부분 성공 재사용, 기록 없는 FAL_PENDING 차단, 기록 미보존 타임아웃 기존 차단.

### E. 독립 리뷰 반영 (2차 커밋)
| 지적 | 조치 |
|---|---|
| M1 원장 기록과 payload 불일치 시 조용히 재과금 제출 | `FAL_JOB_MISMATCH` 차단으로 변경 |
| M2 70초판 이미지 6장 누락, 상한 범위가 job timeout 초과 가능 | 합계 상한 검사 + timeout 90분 |
| M3 잘못된 vars 가 PAID_STEP_IN_PROGRESS 뒤 ValueError → 영구 차단, 비정수는 조용히 기본값 | 유료 단계 전 검사, 비정수 거부 |
| L6 MemoryStore 가 assets_json 객체를 공유해 테스트가 약함 | `_FalJobs.save` 가 deepcopy 로 기록 |

### F. 알려진 한계 (미조치, 보수적 동작)
- 강제 종료(SIGKILL·timeout-minutes) 시 비용 원장 행은 `reserved`·request_id 없음으로 남는다(정산이 실행되지 않음).
  request_id 는 제출 직후 `assets_json.fal_pending` 에 기록되므로 대사 근거는 남는다. 상태는 `PAID_STEP_IN_PROGRESS` 차단.
- 제출 후 폴링·결과조회의 일시 오류(타임아웃 아님)는 기존대로 `<step>:` 차단 — 재개 대상 아님.
- 수동 복구로 last_error 를 지울 때는 실패한 작업의 `assets_json.fal_pending` 키도 함께 지워야 한다
  (fal FAILED 작업 기록이 남으면 재실행마다 같은 실패).

## 4. 검증
- `python -m pytest gen/nuna_weekly/tests -q`: 202 passed (기존 184 + 신규 18).
- `tests/test_watchdog_workflow.py tests/test_workflow_operations.py tests/test_morning_watchdog.py`: 14 passed.
- `ruff check gen/nuna_weekly`: 신규 위반 0 (기존 4건 I001 — script.py, tests/test_pipeline.py, tests/test_script.py — 본 변경 범위 밖).
- 모의 테스트는 fal 실제 응답·처리시간의 증거가 아니다. 실제 API 검증은 미실행.

## 5. 범위 밖 · 운영 조치
- W44: 상태 IMAGED, last_error `clips: FalError…` (구 형식) → 신규 코드에서도 `PAID_RECOVERY_REQUIRED` 차단 유지(의도).
  발행일 2026-10-29 경과로 발행 단계도 "예약 시각 경과" 차단. 원장 정리 여부는 운영 판단.
- fal 대시보드에서 `01a1853e-6481-7811-bbfe-703d03a5feea` 의 최종 상태·과금·처리시간 확인 필요(미확인).
- MemoryStore 는 request_id UNIQUE 를 정산 시 재현하지 않는다. 재현 시 dummy 백엔드의 request_id 중복으로
  기존 테스트 7건이 실패함을 확인했다(별도 과제).
- hero_shorts 의 동일 300s 폴링 구조는 변경하지 않았다.
