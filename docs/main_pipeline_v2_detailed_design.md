# Investment OS `main.yml` 상세 설계 v0.1

작성 기준: `main` 2c3f6f7070e16f8c36590b4138980630e3917100 (2026-09-26). 상태: 설계 초안, 코드·DB·워크플로 미적용.

운영 DB와 코드의 교차 대조, 데이터 매핑 오류, 접근 통제 요구사항은 [`main_pipeline_v2_requirements.md`](main_pipeline_v2_requirements.md)를 우선한다. 이 설계서의 DB DDL은 신규 테이블 제안이며 운영 DB에 적용되지 않았다.

## 1. 목적과 범위

`main.yml`의 시장 분석 및 발행(`morning`, `narrative`, `full`, 수동 `intraday`·`close`·`weekly`), 10분 간격 Alert, PR/push 검증을 다룬다. 기존 시장 계산식, 레짐·ETF 판단 규칙, 콘텐츠 포맷은 보존한다. 변경 대상은 실행 경계, 발행 상태, 재처리, 영속 이력, 관측성이다. Comic 등 독립 워크플로는 이 문서의 변경 대상이 아니다.

### 현재 실행 연결

| 작업 | 시간/입력 | Python | 지속 상태 |
|---|---|---|---|
| morning | KST 월~금 06:36 | `main.py run all --session morning --mode tweet` | core_data, weekly_log, rank_history, DLQ, history |
| narrative | KST 월~금 11:36 | `run all --session narrative --mode tweet` | weekly_log, DLQ, history, narrative_visual_history |
| full | KST 월~금 18:36 | `run all --session full --mode tweet` | weekly_log, DLQ, history |
| alert | KST 월~금 22:00~06:50 10분 간격 | `main.py alert` | alert_history, x_alert_history, DLQ, morning core_data |
| intraday/close/weekly | 수동 dispatch | `run all` | 일부 캐시; publish-history restore/save 없음 |
| pilot_test | main push/PR | `main.py test --round all` | 없음 |

`run all`은 `run_market.run()`의 검증 PASS 후 `run_view.run()`을 호출한다. `run_market`은 수집·계산·core_data 저장뿐 아니라 Supabase 일별 적재와 랭킹 Telegram 발송도 한다. `run_view`는 X 발행, 부가 X 게시, Telegram, 다국어 게시 후 X 주 게시물 성공 시 `history.json`을 갱신한다. `run_alert`는 독립 수집·발송 경로다.

## 2. 현행 위험과 코드 근거

1. **이력의 원자성 없음:** `actions/cache/restore`와 `save`는 파일 스냅샷이다. job별 concurrency group이 달라 공통 이력/ DLQ 갱신이 서로 직렬화되지 않는다. 캐시 손실 시 `duplicate_checker._load_history()`는 `[]`로, `dlq._load_queue()`도 `[]`로 시작한다.
2. **불명확한 발행 결과:** X 성공 후 로컬 기록 전 크래시가 나면 중복 검사에 남지 않는다. `publish_thread()`의 부분 성공은 게시 ID 목록을 반환하지만 최종 `pub_result.success=False`라 `run_view`의 주 발행 이력 기록은 생략된다. 외부 API에 멱등 키를 강제할 수 있다고 가정하지 않는다.
3. **DRY_RUN 범위 불일치:** `x_publisher`는 발행을 생략하나 `telegram_publisher`는 DRY_RUN과 무관하게 실제 전송한다. `run_market`의 랭킹 전송도 영향을 받는다. `run_view`는 DRY_RUN의 X 성공 응답을 발행 이력에 기록할 수 있다.
4. **채널 실패 은폐:** Telegram `send_message`는 실패 응답을 반환하거나 설정 누락 시 빈 목록을 반환한다. `run_view`는 다수 호출의 결과를 집계하지 않고 최종 `success`에 X 주 게시 결과만 사용한다. `main.py alert`는 `run_alert.run()` 요약의 부분 실패를 종료 코드로 반영하지 않는다.
5. **상태 저장소 일부 기구축:** `core/alert_state_backend.py`는 `file/dual/supabase` 모드와 append-only `os_alert_history`를 제공한다. 새 설계는 이를 폐기하지 않고 Alert 판정 및 발행 장부와 연결한다. 기존 `db/create_daily_tables.sql`의 날짜별 유일 키 테이블은 실행·채널 단위 발행 장부로 재사용하지 않는다.
6. **시간 기준 혼재:** cron은 UTC, 예약 설명은 KST, Alert 윈도는 ET, 휴장 판단은 KST 날짜다. `target_market_date`를 세션별로 정의해야 과거 시세·휴장일·중복 키를 일관되게 검증할 수 있다.

## 3. 실행 계약

### 3.1 식별자와 시간

* `run_id`: GitHub `run_id` + `run_attempt` + job 이름. 재시도마다 다른 기술 실행 ID.
* `business_key`: `(session, target_market_date, edition)`; `edition` 기본 `regular`. 운영자 수정 발행은 승인된 새 edition만 사용한다.
* `publication_key`: `(business_key, channel, audience, message_role, sequence_no, locale)`. 채널 예: `x`, `telegram`; audience: `public`, `free`, `paid`; message_role: `main`, `snapshot`, `report`, `earnings`, `rank_change`, `alert` 등. 실제 메시지 종류를 구현 시 목록화한다.
* `target_market_date`: 미 동부시간 거래 세션 기준 날짜. `morning`은 실행 KST 날짜 직전 미국 거래일을 명시적으로 계산해야 하는지 실제 데이터 소스 timestamp로 확정한다. `narrative/full`의 당일/전일 기준도 동일하게 확정한다. 확정 전 운영 키를 임의 변경하지 않는다.
* `source_as_of`, `observed_at`, `prepared_at`, `sent_at`: 전부 UTC timestamptz, 표시에서만 KST/ET 변환.
* 동일 날짜라도 데이터·본문이 변경되면 `content_sha256`이 달라진다. 기본 edition 중복은 자동 재발행하지 않으며 수정 발행은 별도 edition과 사유·승인 주체를 기록한다.

### 3.2 실행 상태

`STARTED -> COLLECTED -> VALIDATED -> PREPARED -> DISPATCHING -> COMPLETED | PARTIAL_FAILED | FAILED`; 휴장·윈도 밖은 `SKIPPED(reason)`. 각 단계 시작/종료/오류를 기록한다. `VALIDATED` 이전에 외부 발행은 없다. 부가 게시물이 실패해도 주 게시물 성공 여부와 각 채널 결과를 보존한다. 전체 종료 코드는 필수 대상의 상태로 결정하고, 선택 발행의 실패는 `PARTIAL_FAILED`로 남겨 알림 정책에 반영한다.

### 3.3 게시물 상태

`PENDING -> CLAIMED -> SENDING -> SENT`; 확정 실패는 `RETRYABLE` 또는 `PERMANENT_FAILED`; 요청이 전달되었는지 확인할 수 없는 시간초과·프로세스 종료는 `UNKNOWN`이다. `UNKNOWN -> SENT | RETRYABLE`은 외부 결과 대조 또는 운영자 판정 후에만 허용한다. preview의 결과는 실행 결과와 artifact에만 남기고 실발행용 `os_publications`에는 등록하지 않는다. 따라서 preview가 추후 live 실행의 유일 키를 점유하지 않는다.

## 4. DB 설계 (신규 마이그레이션 초안)

기존 Supabase PostgreSQL 사용을 **가정**한 설계이며 실제 DB 스키마·접근 권한·RLS 정책 확인 후 적용한다. PostgreSQL에서는 상태 갱신과 선점에 트랜잭션/RPC 또는 서버측 실행 계층이 필요하다. 단순 클라이언트 `SELECT` 후 `INSERT`로 원자성을 주장하지 않는다.

```sql
CREATE TABLE os_pipeline_runs (
  run_id text PRIMARY KEY,
  session text NOT NULL,
  target_market_date date NOT NULL,
  edition text NOT NULL DEFAULT 'regular',
  source_as_of timestamptz,
  status text NOT NULL CHECK (status IN
    ('STARTED','COLLECTED','VALIDATED','PREPARED','DISPATCHING','COMPLETED','PARTIAL_FAILED','FAILED','SKIPPED')),
  validation jsonb,
  source_manifest jsonb,
  error_code text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE os_publications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id text NOT NULL REFERENCES os_pipeline_runs(run_id),
  session text NOT NULL,
  target_market_date date NOT NULL,
  edition text NOT NULL,
  channel text NOT NULL,
  audience text NOT NULL,
  message_role text NOT NULL,
  sequence_no integer NOT NULL DEFAULT 0,
  locale text NOT NULL DEFAULT 'ko',
  content_sha256 text NOT NULL,
  status text NOT NULL CHECK (status IN
    ('PENDING','CLAIMED','SENDING','SENT','RETRYABLE','UNKNOWN','PERMANENT_FAILED','SKIPPED')),
  external_id text,
  reply_to_publication_id uuid REFERENCES os_publications(id),
  claim_token uuid,
  lease_until timestamptz,
  attempt_count integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz,
  last_error_code text,
  last_error_detail text,
  prepared_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_os_publication_business UNIQUE
    (session,target_market_date,edition,channel,audience,message_role,sequence_no,locale)
);
CREATE INDEX idx_os_publications_recovery
  ON os_publications(status,next_attempt_at,lease_until);
CREATE INDEX idx_os_publications_run ON os_publications(run_id);

CREATE TABLE os_delivery_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  publication_id uuid NOT NULL REFERENCES os_publications(id),
  attempt_no integer NOT NULL,
  run_id text NOT NULL,
  request_hash text NOT NULL,
  external_id text,
  outcome text NOT NULL CHECK (outcome IN ('STARTED','SENT','FAILED','UNKNOWN')),
  http_status integer,
  error_code text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE(publication_id, attempt_no)
);
```

`os_alert_history`는 현재 판정용 기록으로 유지하고 신규 발송은 `os_publications`에서 채널·대상별 전송 상태를 관리한다. 현재 `x_alert_history.json`의 90분 쿨다운, Alert 4시간/2시간 판정, Countdown 정책은 변경 없이 기존 테스트와 결과 비교 후 이관한다. `dead_letters.json`은 조회 가능하도록 `os_publications.PERMANENT_FAILED` 및 attempt 이력으로 전환한다. 기존 JSON 파일은 전환 동안 백업/병행기록만 한다.

민감한 원문·토큰을 DB 및 로그에 남기지 않는다. 재시도에 필요한 페이로드는 접근 제한된 저장소의 암호화된 참조로 보관하고 해시·채널·오류 코드만 장부에 기록한다. 이미지의 로컬 경로는 러너가 종료되면 사라지므로 재처리 전 보관 위치와 보존 기간이 필요하다.

## 5. 원자적 선점과 발행 절차

1. 분석을 완료하고 `validate_data` 및 `validate_output`가 PASS인지 확인한다. 필수 입력의 `source_as_of`와 대상 거래일도 검증한다.
2. 같은 트랜잭션에서 `run`과 모든 대상별 `publication`을 등록한다. 유일 키 충돌 시 기존 행의 상태를 읽고 신규 X/TG 요청을 만들지 않는다. `content_sha256` 불일치는 `CONTENT_CHANGED` 운영 알림으로 처리한다.
3. DB 함수에서 조건부 `UPDATE ... WHERE status IN ('PENDING','RETRYABLE') AND (next_attempt_at IS NULL OR next_attempt_at <= now()) RETURNING ...`으로 단일 발행자를 선점하고 `claim_token`, lease를 발급한다. `SENDING` 전 attempt STARTED를 커밋한다.
4. 외부 API를 호출한다. 성공 응답의 실제 외부 ID가 확인되면 해당 publication을 SENT로 갱신한다. Telegram `channel='both'`는 각 chat ID별 publication으로 나누어 일부 성공을 보존한다.
5. 명확한 4xx/검증 실패는 영구 실패, 정책상 재시도 가능한 오류는 지수 백오프+상한 적용, 네트워크 타임아웃 및 만료된 SENDING lease는 UNKNOWN으로 둔다. X·Telegram의 확인 수단을 검증하기 전 UNKNOWN의 무조건 자동 재발행을 금지한다.
6. 스레드는 `sequence_no=0`의 외부 ID 확인 후 `sequence_no=1`을 선점한다. 부분 성공 시 첫 게시 ID는 보존하고 남은 항목만 순서대로 처리한다. 재처리 대상의 선행 게시 상태가 불명확하면 중단한다.
7. DB 장애로 선점 불가 시 실발행을 fail-closed하고 run 결과를 FAILED로 남길 수 있으면 남긴다. 기존 파일 캐시로 자동 강등하지 않는다.

외부 게시 API와 DB 사이에 단일 ACID 거래는 불가능하다. 위 방식은 DB 내 **중복 선점 방지**와 상태 추적을 보장하되, 외부 게시 직후 응답 유실의 완전 자동 exactly-once까지 보장하지 않는다. UNKNOWN 조사가 필수다.

## 6. 세션 오케스트레이션

* `main.py`: `RunContext(run_id, session, mode, target_market_date, dry_run_scope)` 생성. 휴장일 및 세션 시간 판정 결과를 SKIPPED로 기록한다. CLI exit 0=COMPLETED/SKIPPED, 2=검증 실패/부분 실패, 1=시스템 실패로 구분한다. 운영 알림은 SKIPPED와 실패를 구분한다.
* `run_market.py`: 수집→분석→검증→불변 snapshot 저장만 담당하도록 분리. `record_daily`, 랭킹 알림, DB 적재는 검증 후 별도 `PostAnalysisTasks`에서 멱등 처리한다. 기존 수식과 출력 schema 비교 테스트를 먼저 만든다.
* `run_view.py`: 모든 출력 텍스트·이미지의 manifest 생성, 채널별 publication 등록, 순차 dispatch, 상태 집계로 분리. `duplicate_checker`는 일시적으로 shadow 모드에서 새 유일 키 판정과 결과 비교.
* `run_alert.py`: ET window 판정, source snapshot, signal 평가, 기존 쿨다운 판정, 대상별 publication을 순서대로 수행. `alerts_detected/alerts_sent`와 별도로 `failed_count`, `unknown_count`, `skipped_reason` 출력. 부분 실패 시 CLI 성공 종료 금지.
* `DLQ`: 실패 내용별 attempt로 통합. 기존 `core/dlq.py`의 파일 재처리는 feature flag 뒤에서만 사용하고, 단계적 전환 시 중복 소비가 없도록 단일 경로만 활성화한다.

## 7. DRY_RUN 및 수동 실행

`execution_mode=preview|live`를 명시한다. preview에서는 X, Telegram, paid/free, 랭킹 알림, 번역 게시, DLQ 재시도 등 **모든 외부 발행을 금지**한다. API 응답을 성공으로 위장하지 않고 run 결과와 artifact에 preview 내용을 기록한다. 외부 데이터 수집은 허용하되 유료 API 호출 여부와 비용을 로그에 기록한다. 기존 `DRY_RUN` 값은 이관 기간 동안 `preview`로 매핑하되 현행 Telegram 실전송에 의존한 운영이 있는지 먼저 확인한다. 기본 수동 실행은 preview, live는 명시 입력 및 GitHub Environment 보호 규칙 적용을 제안한다. 재발행은 edition, 사유, 대상 채널을 명시한다.

## 8. 워크플로 상세 변경안

* workflow 최상위 `permissions: contents: read`; 필요한 별도 작업만 최소 권한을 추가한다. 비밀값을 전체 workflow env에 일괄 주입하지 않고 발행 job/step에 필요한 것만 부여한다. PR의 `pilot_test`에는 발행 자격 증명을 전달하지 않는다.
* cron과 job `if` 매핑을 CI 정적 검사로 검증한다. 시간대 변환은 `zoneinfo` 기반 테스트로 KST/ET 경계와 DST 양쪽을 검증한다. cron 실행 지연 시 `scheduled_at`, `started_at`, `lateness`를 기록하고 Alert는 window 밖이면 SKIPPED로 설명한다.
* `main.yml`의 6개 세션 템플릿은 공통 composite action/reusable workflow로 설치·검증·artifact 절차를 공유한다. 단, Alert와 분석/발행은 서로 다른 실행 계약으로 유지한다.
* 영속 상태는 캐시에서 제거하되 `pip` 및 브라우저 설치 캐시는 의존성 캐시로 유지할 수 있다. artifacts는 데이터·검증 결과·manifest·오류 요약에 사용하며 비밀값과 API 원문은 제외한다.
* concurrency는 세션별 불필요한 중복 실행을 줄이는 보조 장치다. Alert는 10분 주기이므로 작업 대기 중 취소·시각 경과 정책을 정해야 한다. 근본 멱등성은 DB 유일 키와 선점이 담당한다.

## 9. 롤아웃과 검증 게이트

| 단계 | 작업 | 통과 조건 |
|---|---|---|
| S0 | 현재 작업의 세션·채널·게시물 종류, DB 권한, 데이터 source timestamp 조사 | 발행 대상 매트릭스와 날짜 정책 승인 가능한 초안 |
| S1 | DB 마이그레이션 및 shadow 기록 | 기존 출력과 장부의 채널별 건수 대조, 실발행 경로 불변 |
| S2 | preview 통합 및 모든 채널 테스트 더블 | preview 시 외부 게시 API 호출 0건 |
| S3 | 한 세션·한 채널씩 장부 선점 전환 | 동시 두 실행에서 동일 publication의 발행 선점 1건 |
| S4 | 스레드/Alert/DLQ 전환 | 부분 성공, 타임아웃, DB 단절, 누락 캐시, 재시작 복구 통과 |
| S5 | 운영 관찰 후 파일 상태 제거 | 일정 기간 장부/외부 게시 건수 대조 및 UNKNOWN 처리 운영 확인 |

필수 테스트: 발행 성공 직후 크래시; 외부 성공·DB 기록 실패; 스레드 2번째 실패; Telegram free 성공/paid 실패; 채널 설정 누락; Alert 동일 윈도 재실행; 서로 다른 job 동시 실행; 장부 DB 단절; 휴장일/서머타임 경계; preview에서 모든 발행 0건. 실 API 검증은 별도 테스트 채널과 제한된 수량으로 수행한다.

## 10. 구현 전 결정 항목

1. 실제 Supabase 프로젝트의 `os_alert_history` DDL, 테이블 권한, RPC/트랜잭션 배포 권한 확인.
2. 세션별 `target_market_date`와 source timestamp 기준 확정. 현재 KST 휴장 판정과 발행 의미를 비교해야 한다.
3. X/Telegram에서 요청 성공 여부가 UNKNOWN일 때 조회·대조 가능한 범위와 운영자 확인 절차 확정.
4. 현재 `DRY_RUN` 중 Telegram 발송에 의존하는 운영 사용 여부 확인 후 preview 정책 전환.
5. 대상 채널·언어·유료/무료·부가 콘텐츠별 필수/선택 분류와 부분 실패 시 Actions 종료 정책 확정.

이 문서는 구현을 승인하거나 DB에 적용한 기록이 아니다. 코드와 실제 배포 상태에 근거한 상세 설계이며, 결정 항목 검증 후 마이그레이션/변경 PR을 작성한다.
