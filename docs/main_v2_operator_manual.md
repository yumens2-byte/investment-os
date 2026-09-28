# Investment OS main 파이프라인 운영자 매뉴얼 v1.0

기준: 2026-09-28 KST, 현행 `main.yml`. 이 문서는 현재 가능한 조작과 장애 판정을 설명한다. 목표 기능의 적용 상태는 `main_v2_process_design.md`에서 확인한다.

Notion 정본: [운영자 매뉴얼](https://app.notion.com/p/3e99208cbdc38184aaa7e2aa6959bf2c)

## 1. 시작 전 확인

1. GitHub Actions의 **Investment OS Auto Publish**에서 세션·트리거·브랜치·run URL·예약 시각과 실제 시작 시각을 확인한다. PR/push의 Pilot Test는 게시 작업이 아니다.
2. 수동 실행은 `workflow_dispatch`의 `session`, `mode`, `dry_run`을 확인한다. `alert`와 `full`에서는 mode 입력이 무시된다. 기본 `dry_run` 선택지는 `false`이므로 실행 전 값을 직접 확인한다.
3. **`DRY_RUN=true`에서 X와 공통 Telegram 발행 함수의 외부 요청은 차단된다.** 그러나 전체 세션의 모든 발행 경로·DLQ·기록 오염에 대한 통합 검증은 끝나지 않았다. 수동 검증에 실제 채널 자격 증명을 공급하지 않는다. 스케줄은 `secrets.DRY_RUN`, 수동은 입력값 우선이다.
4. 실행 중인 동일 세션은 job concurrency로 직렬화되지만 다른 세션의 파일 캐시 전체가 공통 잠금으로 보호되지는 않는다. 테스트와 운영을 동시에 공유 작업 디렉터리에서 실행하지 않는다.

## 2. 일상 점검

| 시각/주기 | 점검 대상 | 이상 판단 |
| --- | --- | --- |
| KST 평일 06:36 이후 | morning run, `core_data.json`, validation artifact, X·TG 개별 결과 | 미시작·지연·검증 FAIL·실제 채널 누락 |
| KST 평일 11:36 이후 | narrative run, 이미지·발행 기록 | 생성 실패, 본문만 성공 등 부분 실패 |
| KST 평일 18:36 이후 | full_dashboard run, 발행 이력 | 최신 데이터가 아닌 캐시 사용, 중복 게시 |
| ET 장중 09:30~15:30 | Alert Check 및 윈도·휴장 판정 | 예상 실행 미시작, 검출과 발송 건수 불일치 |
| 매일 | `daily_snapshots`, `daily_analysis`, `daily_news` 같은 KST 적재일 행과 원본 시각 | 빈 값·오래된 소스·JSONB string 재유입 |

GitHub Actions 성공만으로 X/TG 대상별 전송 성공을 확정하지 않는다. 게시물 URL·ID와 채널 로그를 대조한다. watchdog은 시작된 작업의 비정상 종료만 알리며 cron 자체 미시작은 별도 일정 대조가 필요하다.

## 3. 장애별 대응

| 증상 | 먼저 확인할 곳 | 안전한 조치 |
| --- | --- | --- |
| 스케줄 미발행, 수동은 발행 | `secrets.DRY_RUN`, cron과 job `if` 문자열, run 존재 여부 | 비밀값 자체를 기록하지 말고 설정 상태·미시작 여부 확인 |
| 검증 실패 | `validation_result.json`, `core_data.json` artifact | 소스 관측 시각과 누락 필드 점검, 실발행 재실행 보류 |
| DB 일부 행 누락 | 일별 세 테이블의 적재 결과·로그 | 실패 테이블과 KST 날짜 기록. 무분별한 재실행은 같은 날짜 UPSERT 위험 |
| X 성공/TG 실패 또는 스레드 일부 성공 | 외부 게시 ID와 `history.json`, DLQ | 성공분 ID 보존, 누락분만 수동 대조. 전체 재실행 금지 |
| Alert 미검출·중복 | ET 윈도·grace·휴장, alert_history와 x_alert_history 캐시 | 캐시 복원 키, 입력 데이터, 기존 발행 ID 순서대로 확인 |
| 결과 불명확·타임아웃 | run URL, API 응답, 채널 실제 게시 내역 | 외부 게시 여부 확인 전 재전송 금지; 담당자가 UNKNOWN으로 기록 |
| watchdog 알림 없음 | 원 워크플로가 실제 시작했는지, `notify_watchdog.yml`의 이름 | 미시작이면 watchdog 범위 밖; 예약 누락 별도 조사 |

기존 135건의 과거 `daily_analysis` 신호와 `daily_news` RSS 메타데이터는 신뢰 가능한 소급 원본으로 취급하지 않는다. 운영 DB 권한·RLS 변경은 소비자 키 조사와 테스트 환경 검증을 거쳐 별도 이관한다.

## 4. 재처리·종료 기록

사고 기록에는 세션, GitHub run ID/attempt, KST 및 UTC/ET 시각, 대상 거래일(알 수 없으면 미확정), source 관측 시각, 검증 결과, X/TG 각 ID, 실패 단계, 재처리 판단자와 사유를 남긴다. 캐시를 복원하더라도 이전 게시 ID 확인 전 실발행하지 않는다. 상태가 불명확하면 UNKNOWN으로 유지한다.

현행 코드에는 원자적 publication 장부와 전체 세션의 완전한 preview 게이트가 아직 없다. 따라서 이 문서의 수동 대조는 그 기능이 구현·검증될 때까지 필요한 운영 통제다.
