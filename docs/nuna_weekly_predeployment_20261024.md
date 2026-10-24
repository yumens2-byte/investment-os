# NUNA 주간 생성·발행 상세 분석 및 운영 반영 전 검증

기준일: 2026-10-24 KST. 기준 PR: [#22](https://github.com/yumens2-byte/investment-os/pull/22).
분석 시작 기준: main `27c6f8a`, PR head `3d8320f`.
사용자 승인 범위: 상세 분석·수정·시험·운영 반영 직전 준비. main 병합, 운영 스위치·키 변경,
DB 변경, 유료 생성, Telegram 전송, SNS 게시 실행은 포함하지 않는다.

## 1. 판정

코드 수정과 무과금 검증을 완료했다. 전체 NUNA 테스트 158건, 기존 워크플로 가드레일 14건이 통과했다.
추가 장애 주입 테스트는 21건이다. PR 브랜치에 반영하며 운영 브랜치는 변경하지 않는다.
실운영 키·계정·유료 응답 검증은 남아 있으므로 현재 판정은 **반영 후보 준비 완료 / 운영 개방 보류**이다.
실제 게시 성공이나 과금 없는 자동 복구를 보장한다는 뜻이 아니다.

## 2. 기존 장애와 증거

| 항목 | 확인한 사실 | 영향 |
|---|---|---|
| 지정 실행 실패 | 10-20 05:40 KST에 미국 10-19~21 전체 종가 요청, direct·dry=True | 미래 거래일 데이터 부재로 collect 실패 |
| 운영 DB 키 | 지정 실행 환경의 SUPABASE_SERVICE_ROLE_KEY 빈 값 | 실모드 DB 연결 전 설정 확인 필요 |
| 예약 실행 | 10-22 gen/publish 모두 skipped, 발행 job step 0건 | 당시 NUNA_ENABLED 활성화 조건 미충족 |
| 원장 | 최초 점검 때 episodes/targets/costs 각각 0건 | 실운영 성공 이력 확인 불가 |
| 생성·발행 연결 | 독립 cron + ISO 주차별 DB 원장 | gen 성공만으로 publish를 즉시 호출하지 않음 |
| DRY_RUN | Dummy 생성, 쓰기는 MemoryStore | 별도 Actions 실행 사이 결과 전달 안 됨 |

원본 실행 링크·로그 근거는 [최초 점검 기록](nuna_weekly_pipeline_review_20261024.md)을 참조한다.
키의 현재 값·유효성은 읽거나 추정하지 않았다. 이전 실행 로그의 빈 값과 현재 설정 상태를 구분한다.

## 3. 단계별 처리와 책임

| 구간 | 입력·완료 기준 | 장애 처리 |
|---|---|---|
| 달력/preflight | 같은 ISO 주 목요일·월~수 미국 휴장 제외·실제 장 종료 | 미래 종가 수집 차단, 키·DB·SDK 초기화 없이 판정 |
| collect | 필수 S&P500·나스닥 변화율·VIX, 선택 지표·뉴스·레짐 | 필수 누락·NaN·Infinity 차단, 선택 지표는 None 가능 |
| script/TTS | 사실 근거·길이·정책 검증 후 대본/구간 음성 | 실유료 호출 전 원장에 진행 의도 저장 |
| images/clips | 기준 이미지·캐논·세로 규격·원격 URL | 오류/중단 후 유료 자동 재제출 금지, 대사 후 복구 |
| assemble/QC | 영상 variant 완전성, Q1~Q8 | 빈 영상 목록은 통과하지 않음, 새 러너 영상 유실은 재조립/QC |
| stage/notify | 모든 variant의 QC 통과·스테이징·생성 알림 | QC 근거 누락 시 업로드 차단, NOTIFIED 이전 발행 금지 |
| publish | 같은 episode의 대본·media·알림 시각 | 보류창 확인, 예약 시각 경과 차단, 전송 의도 선기록 |
| verify | 플랫폼별 post ID 조회·URL·상태 | pending/failed도 ID가 있으면 조회, 4채널 누락 시 전체 성공 금지 |

생성 백엔드는 생성 단계에서만 초기화한다. 발행에서 Claude/Gemini/fal 키를 요구하지 않는다.
회차의 duration_target_sec와 현재 NUNA_DURATION_SEC가 다르면 생성 중 규격 변경을 차단한다.

## 4. 보류창과 일정

- gen cron: 매주 목요일 06:47 KST. publish cron: 목요일 08:12 KST.
- 보류창: 생성 알림 시각부터 60분.
- 예약 시각: `max(목요일 08:30 KST, 생성 알림+62분)`.
- 보류창이 남아 있으면 POST 전에 30초 이하 간격으로 확인한다. 폴링 HTTP 경과도 실제 시각에 반영한다.
- 예: 알림 07:20 → 창 종료 08:20 → 예약 08:30. 알림 08:00 → 창 종료 09:00 → 예약 09:02.
- 발행 job timeout: 45분에서 90분으로 확대. 창 종료 후 예약+5분 대기·조회 검증을 포함한다.
- 검증 대기는 30분 이내만 같은 실행에서 진행한다. 더 멀면 예약 상태로 종료하고 verify를 별도 실행한다.
- GitHub cron 실제 시작 시각은 보장되지 않는다. 늦게 시작해 예약이 과거이면 발행을 차단한다.
- 예약 POST 이후의 보류 입력은 자동 취소가 아니다. 외부 예약이 있으면 운영자가 Zernio 원본 예약을 확인한다.

## 5. 중복 발행 방지 상세

발행 대상은 Instagram·Threads·YouTube·Facebook이며 요청은 main(IG+Threads), sub(YT), sub(FB)의 3건이다.
POST 전에 대상별 키·마스킹 요청·시도 횟수·SUBMISSION_STARTED를 기록한다.
외부 응답 후 DB 저장이 실패해도 다음 실행에서 이미 전송을 시도한 사실을 확인할 수 있다.

| 채널 상태/응답 | 자동 처리 | 금지 사항 |
|---|---|---|
| scheduled/published | 신규 POST 생략 | 완료 채널 재발행 |
| 전송 전 health blocked | 나중에 상태 확인 후 재시도 가능 | 차단 채널 포함해 무조건 발행 |
| 확정 4xx 실패, post ID 없음 | 기존 실패 재시도 규칙, 5회 상한 | 시도 횟수 DB 제약 초과 |
| 5xx/408/409/429 | pending·RECONCILE_REQUIRED | 새 키로 자동 신규 발행 |
| timeout/연결 단절 | pending·전송 기록 유지 | 결과 불명 요청의 무조건 재전송 |
| 기존 post ID 있음 | verify로 조회·대사 | 실패라는 이유만으로 신규 게시물 생성 |
| 예약 응답에 post ID 없음 | pending·대사 필요 | 추적 불가 응답을 예약 성공으로 집계 |
| 일부 플랫폼 원장 없음 | 전체 성공 판정 차단 | 3/4 발행을 PUBLISHED로 집계 |

공식 Zernio 문서는 같은 Idempotency-Key를 24시간 내 재사용하면 원래 게시물을 반환하며,
5xx·타임아웃이어도 게시물이 저장돼 있을 수 있다고 명시한다. 또한 과거 scheduledFor는 즉시 발행으로 처리한다.
이 때문에 로컬 partial 상태·계정 헬스 변화로 새로운 키를 생성하는 대신 결과 불명 건을 대사 대상으로 남긴다.
미래에 안전한 자동 재전송을 확장하려면 원본 요청 그룹·키·계정·유효기간을 복원하는 별도 계약이 필요하다.
Zernio retry-post는 즉시 발행 API이므로 이번 예약 전용 자동 복구에는 연결하지 않았다.

## 6. 유료 생성 중단·복구

문제: fal pending은 로컬 파일이며 TTS도 원격 백업이 없다. 단계 중 러너가 사라지면 원격 생성 여부,
부분 자산, 비용을 새 러너가 완전히 복원하지 못한다. 기존 비용 원장은 예외 시 추정 비용을 보수적으로 확정한다.

처리:

1. 실유료 script/tts/images/clips 시작 전에 `PAID_STEP_IN_PROGRESS:<step>`을 episode.last_error에 저장한다.
2. 성공 전이에만 marker를 지운다. 예외면 기존 step 오류를 남긴다.
3. 같은 단계의 marker 또는 오류가 남은 재실행은 PAID_RECOVERY_REQUIRED로 API 호출 전에 차단한다.
4. 공급자 요청 상태·실제 비용·완료 자산을 대사한 뒤 복구한다. marker를 지우고 무조건 재실행하지 않는다.
5. 원격 URL이 이미 원장에 있는 완료 단계 자산은 재사용할 수 있다.

자동 복구되지 않는 경우를 실패로 명확히 노출하는 보호 조치다. 공급자 작업 조회/부분 자산의 영속 체크포인트,
동일 비용 예약의 재사용까지 구현한 완전 자동 복구는 이번 범위에 포함하지 않는다.
예산 gate는 추정 예약액 기반이며, 실제 사용량·공급자 청구액의 절대 상한 보장이 아니다.

## 7. 외부 계약 대조

2026-10-24 공식 문서로 모델 ID와 입력 형식을 확인했다. 실제 계정의 모델 접근권·잔액·출력 품질 검증과 구분한다.

| 연계 | 확인 내용 | 근거 |
|---|---|---|
| Claude | claude-sonnet-5-5 모델 ID·Messages 사용 | [공식 모델 문서](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) |
| Gemini TTS | gemini-3.8-flash-tts, Interactions input/annotations/speech_config, output_audio.data | [공식 음성 생성](https://ai.google.dev/gemini-api/docs/speech-generation) |
| fal 이미지 | nano-banana/edit, image_urls·aspect_ratio | [공식 API](https://fal.ai/models/fal-ai/nano-banana/edit/api) |
| fal 클립 | Kling 2.6 pro i2v, start_image_url·generate_audio | [공식 API](https://fal.ai/models/fal-ai/kling-video/v2.6/pro/image-to-video/api) |
| Zernio | 예약·멱등·과거 예약 시각·조회/재시도 구분 | [공식 문서 인덱스](https://docs.zernio.com/llms.txt) |

## 8. 시험 증거

| 검사 | 결과 |
|---|---|
| python -m pytest gen/nuna_weekly/tests -q | 158 passed, 74.83s |
| 기존 workflow/watchdog/morning 운영 가드레일 | 14 passed |
| 신규 장애 주입 | 21건, 위 전체 158건에 포함 |
| Ruff / git diff --check / compileall | 통과 |
| actionlint 1.7.7 | gen/publish/CI 3개 YAML 통과, shellcheck·pyflakes 연동은 비활성 |
| CLI preflight | DRY_RUN=false, date=2026-10-22에서 키 없이 exit 0 |

시험 환경은 로컬 Python 3.12.14, ffmpeg/ffprobe, Noto CJK다. 유료 공급자 호출과 실운영 게시물 생성은 없다.
GitHub Actions의 운영 Python 3.11 차이를 확인하기 위해 기존 ci_alert_tests.yml에 NUNA 전용 job을 추가했다.
같은 pinned requirements, ffmpeg·폰트, 실제 전체 테스트를 사용하며 운영 secrets는 주입하지 않는다.
PR synchronize 후 원격 job 결과를 확인한다. 기존 CI의 다른 job 실패는 NUNA 결과와 분리해 기록한다.

장애 주입 범위: 창 중간/마지막 보류·미보류, 408/409/429/5xx, 응답 후 원장 저장 실패,
pending+post ID 조회, 채널 누락, 빈 QC/스테이징, NaN/Infinity, 시도 상한, 유료 의도 저장·중단 후 차단.

## 9. 운영 반영 순서 및 중단 기준

| 순서 | 실행 주체 | 완료 기준 / 중단 기준 |
|---|---|---|
| 1. 변경 검토 | 개발/승인자 | PR diff·시험·90분 timeout·예약+2분 변경 확인 |
| 2. 원격 CI 확인 | 개발 | NUNA Python 3.11 job 통과. 미통과면 병합 보류 |
| 3. 설정 확인 | 운영 | NUNA_ENABLED 및 키/4채널 계정의 설정·권한 확인. 키 값은 보고서에 기재하지 않음 |
| 4. 코드 반영 | 운영 승인 후 | 승인된 SHA만 main에 반영, 운영 활성화 전 SHA 확인 |
| 5. 생성 점검 | 운영 승인 후 | preflight → 실생성, 비용/품질/원장 NOTIFIED 확인. 실패 시 publish 금지 |
| 6. 예약 점검 | 운영 승인 후 | 보류창·미래 scheduledFor·3요청/4채널 원장 확인 |
| 7. 발행 확인 | 운영 | verify, 4채널 상태·실제 URL·오류 확인. 일부 누락이면 전체 성공 보고 금지 |

기본 날짜는 다음 정상 주간 실행일인 2026-10-29(목)이며, 이번 승인만으로 예약 실행·활성화하지 않는다.
오래된 10-22 회차를 지금 publish하면 과거 예약 차단 대상이다. 복구를 위해 날짜만 바꾸거나 marker를 임의 삭제하지 않는다.

운영 반영 후 회귀 시: NUNA_ENABLED를 끄고 신규 생성/발행을 중단한다. 기존 외부 예약은 자동 취소되지 않으므로
Zernio 게시물 ID와 채널 상태부터 대사한다. 외부 예약이 있으면 코드 롤백보다 취소/보류 절차를 우선 확인한다.
승인된 revert commit으로 코드만 되돌리며 원장·비용·게시물을 삭제하지 않는다. rollback 자체가 외부 발행 취소는 아니다.

## 10. 최종 인계 항목

- PR 코드와 이 문서를 승인 후보로 인계한다. main·운영 설정·DB는 변경하지 않았다.
- 실제 키/계정/모델 접근권, 유료 생성 출력·실제 청구액, Zernio 4채널 실발행은 미검증 항목이다.
- 자동화의 안전 중단과 운영자 대사는 이번 복구 정책의 일부다. 결과 불명 상황의 무조건 재실행을 제공하지 않는다.
