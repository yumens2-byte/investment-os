# 외부 발행 검증 1차 결과 (2026-09-28 KST)

범위: `main.yml`에서 호출하는 공통 X·Telegram 발행 경계 및 preview 상태 오염. 운영 X·Telegram·Supabase에는 쓰지 않았다. 실제 테스트 채널 자격 증명과 대상 식별자가 제공되지 않아 실전송/수신 확인은 아직 수행하지 않았다.

| 경로 | 확인한 문제 | 조치·검증 |
| --- | --- | --- |
| X 텍스트·이미지·스레드 | DRY_RUN은 API를 차단하지만 스레드 시작 전 15~30초 대기 | 대기를 실발행 분기로 이동. fake X client/media로 호출 0건 확인 |
| TG 텍스트·사진·문서 | DRY_RUN과 무관하게 `requests.post` 실행 | `DRY_RUN`에서 `skipped` 결과를 반환하고 파일 열기·HTTP 호출 전에 종료. 토큰·채널 설정 모의 시 0건 확인 |
| TG free/paid 부분 실패 | 두 대상에 순차 전송, 한 대상 실패가 다른 대상 성공을 숨길 수 있음 | fake HTTP의 성공/실패 2건을 별도 반환하는 계약 확인. 상위 `run_view`의 집계는 아직 미구현 |
| DLQ | preview의 X 모의 성공이 기존 재처리 항목을 성공 처리·삭제할 수 있음 | `process_queue`가 preview에서 읽기만 하고 재시도·저장하지 않음. 원본 항목 불변 테스트 |
| X 발행 이력 | preview 성공 응답이 `history.json`에 기록됨 | `run_view`에서 실발행 성공만 기록 |
| 일별 DB | `run_market`이 preview에서도 `daily_*` upsert | `DRY_RUN`에서 3개 일별 DB 적재 생략 |

검증: 전체 일반 pytest 341 passed, `full_test.py` 146/146, main 파일럿 42/42. 신규 preview 테스트는 X client/media 미호출, TG message/photo/document 미호출, TG 채널별 부분 결과, DLQ 기존 항목 보존을 검사한다. `main.yml` Pilot Test에 신규 테스트를 포함했다. 공통 경로 외 `notifier/telegram.py`, 독립 워크플로의 직접 HTTP 및 다른 서비스 게시 경로는 본 변경의 범위가 아니다.

## 남은 실제 발행 검증 게이트

1. 운영 채널과 분리된 테스트 X 계정 및 Telegram 테스트 채팅의 대상 ID를 확인한다. 운영 채널에 모의 데이터를 게시하지 않는다.
2. 게시할 문구·이미지, 대상·횟수, 삭제/회수 절차를 사전 확정한다. 샘플: `[Investment OS 검증] 2026-09-28 발행 경로 점검 — 투자 신호가 아닙니다.`
3. 테스트 계정만 주입한 격리 실행에서 X 단건·이미지·2개 스레드, TG free/paid 텍스트·사진·문서 순서로 실행하고 외부 ID·HTTP 응답·수신 채널을 대조한다.
4. free 성공/paid 실패, X 2번째 스레드 실패, 요청 타임아웃의 부분 결과와 UNKNOWN 재전송 차단을 확인한다. 현행 `run_view`는 채널별 결과 집계가 없으므로 실운영 승격 전 보강해야 한다.
5. 이 단계가 완료되기 전까지 **실전송 성공·모든 세션의 preview 0건·운영 배포 적합**으로 판정하지 않는다.
