# NUNA W43 대본 생성 장애 수정 및 파일럿

## 확인한 장애

- 실행: https://github.com/yumens2-byte/investment-os/actions/runs/40064733963/job/120785483118
- 입력: run / 2026-10-22 / dry=False.
- 운영 DB: nuna-2026-W43, publish_date=2026-10-22, status=COLLECTED.
- 시장 수집 성공 후 Claude 응답의 JSON 문자열 종료 오류(pos 1360)로 script 실패.
- 기존 로그는 stop_reason·사용 토큰·요청 ID를 보존하지 않았다. 출력 한도 초과 여부는 소급 확정 불가.
- 비용 원장 id=3은 예약액 $0.041/54원으로 charged. 실제 청구액 확인 결과가 아니다. 기존 기록을 삭제하거나 실사용량으로 재작성하지 않는다.

## 수정

- Claude Messages 요청에 output_config.format/json_schema를 지정한다. 모든 객체의 필수 필드와 additionalProperties=false를 지정한다.
- stop_reason, 입력/출력 토큰, 모델, 요청 ID, max_tokens를 로그에 남긴다. 응답 본문은 남기지 않는다.
- max_tokens는 첫 호출 8000, 한 번의 재생성은 12000. 예약액도 해당 출력 상한으로 계산한다.
- max_tokens 종료와 JSON 파싱 실패는 응답 사용량을 정산하고, 별도의 예산 예약을 통과한 경우에만 한 번 재생성한다. 내용 검증 재생성과 합쳐 최대 두 번의 호출이다.
- refusal 등 다른 종료 사유와 통신 예외는 자동 재시도하지 않는다. SDK 내부 재시도도 0으로 설정해 호출 횟수를 숨기지 않는다.
- 응답 처리 실패라도 사용량이 확인됐으면 Budget이 실제 토큰 기반 설정 단가로 정산한다. 응답을 못 받은 통신 오류는 예약액으로 보수 정산한다.
- 유료 단계 실패 알림에서 단순 재실행 안내를 제거하고 비용 대사 후 복구가 필요함을 명시한다.
- 기존 PAID_RECOVERY_REQUIRED 및 중복 발행 차단은 유지한다.

## 검증

- test_script.py: 24 passed. 잘린 응답/문법 오류 복구, 실패 응답 사용량, 두 번 실패 후 중단, 거부/통신 예외 재시도 금지, 예산 초과 시 두 번째 API 호출 차단을 포함한다.
- 전체 NUNA 회귀: 181 passed (83.27초). 이후 추가한 SDK 계약 검사 2개를 포함한 test_script.py 24개도 통과했다. 모의 E2E는 60초 영상 생성·QC, 멱등 재실행, 4개 채널 예약·검증과 보류 차단을 포함한다. 반영 후 GitHub CI는 추가 검사까지 포함한 전체 183개를 실행한다.
- 운영 워크플로 가드레일: 14 passed.
- 모의 테스트는 유료 API·실제 텔레그램·SNS 발행 성공의 증거가 아니다.

## 실제 API 파일럿 절차

1. 수정 커밋의 회귀 테스트 성공을 확인한다.
2. 운영 DB 상태가 COLLECTED이고 last_error가 해당 JSON 파싱 오류와 일치하는지 재확인한다. 실행 중인 생성 작업과 미정산 reserved 비용이 없어야 한다.
3. 기존 비용 기록은 보존한다. 해당 오류에 한정해 복구 승인 이력을 nuna_step_runs에 남기고 last_error만 조건부 해제한다. 이 작업은 파일럿을 실제 실행할 때 수행하며, 테스트 준비만으로 해제하지 않는다.
4. 기존 nuna-weekly-gen 워크플로를 main / step=script / date=2026-10-22 / dry_run=false로 수동 실행한다. 이미지·TTS·클립 단계까지 이어지는 run은 사용하지 않는다.
5. SCRIPTED 및 script_json 저장, script 단계 성공, 각 호출의 사용량·request_id 기록, 최대 두 번 호출을 확인한다. 실패 시 보호 상태를 유지하고 새 오류를 분석한다.
6. 대본 검토 후 별도 승인된 생성 run을 진행한다. 실제 SNS 발행은 영상 확인 후 기존 휴먼게이트에서 진행한다.

GitHub 연결 도구는 현재 워크플로 dispatch 기능을 제공하지 않고, 로컬에는 운영 API 키가 없다. 실제 API 파일럿은 GitHub UI 수동 실행 또는 승인된 브라우저 전환이 필요하다. 실행 전에는 실제 API 파일럿 통과로 표기하지 않는다.

## 근거

- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- 공식 문서상 structured outputs도 max_tokens 종료·refusal 시 정상 스키마 결과를 보장하지 않으므로 종료 사유 검사와 별도 처리 경로를 유지한다.
