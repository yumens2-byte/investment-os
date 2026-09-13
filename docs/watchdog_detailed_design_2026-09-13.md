# Investment OS Watchdog 상세설계서

> 문서 상태: Phase 0 구현 완료 / Phase 1 개발 준비<br>
> 기준일: 2026-09-13<br>
> 관련 분석: `docs/watchdog_operations_analysis_2026-09-13.md`<br>
> 대상 독자: 운영 담당자, 개발자, 리뷰어

---

## 1. 목적과 범위

이 문서는 기존 종료 실패 알림을 유지하면서 Investment OS의 감시 체계를 단계적으로 확장하기 위한 구현 정본이다. 이번 개발 범위는 즉시 장애 위험을 제거하는 Phase 0이며, 다음 개발 단위인 missed-schedule detector와 health event의 상세 계약까지 확정한다.

### 이번 릴리스에 포함

1. Daily Comic의 anti-bot delay를 올바른 GitHub Actions step으로 분리
2. Comic Novel에 15분 상한을 추가하고, 15분 random delay를 사용하는 Daily Comic과 Weekend Content의 상한을 30분으로 조정
3. workflow 또는 watchdog 테스트 변경 시 자동 실행되는 CI guardrail 추가
4. 모든 workflow에 대한 `actionlint` CI 추가
5. 모든 운영 job의 timeout 존재 여부를 검증하는 회귀 테스트 추가
6. 상세설계, 검증 기준, 배포·rollback 절차 작성

### 이번 릴리스에 포함하지 않음

- GitHub API 기반 missed schedule 탐지 실행 코드
- Supabase incident/health-event 테이블 및 migration
- Telegram 외 보조 채널
- 운영 환경 smoke test와 최근 30일 SLO baseline

위 항목은 secret, 실제 GitHub 실행 이력, 운영 데이터베이스 정책과 함께 배포해야 하므로 Phase 1 이후로 분리한다.

---

## 2. 요구사항

### 기능 요구사항

| ID | 요구사항 | 수용 기준 | 상태 |
|---|---|---|---|
| WD-F-001 | 운영 workflow 종료 실패를 중앙 Telegram으로 통지 | 기존 watchdog 회귀 테스트 통과 | 기존 구현 |
| WD-F-002 | Daily Comic 지연을 실제 독립 step으로 실행 | pipeline shell 본문에 `- name:`이 없고 schedule에서만 delay | 완료 |
| WD-F-003 | 모든 운영 job에 실행 상한과 의도적 delay 이상의 여유를 둔다 | 모든 job에 timeout 존재, 15분 delay job은 30분 이상 | 완료 |
| WD-F-004 | workflow 변경 시 정적 검사를 수행한다 | `.github/workflows/*.yml` 변경이 CI를 trigger하고 actionlint 실행 | 완료 |
| WD-F-005 | watchdog 정책 변경 시 회귀 테스트를 수행한다 | 두 guardrail 테스트 파일이 CI에서 실행 | 완료 |
| WD-F-101 | 예정 실행 누락을 감지한다 | 기대 시각+grace 이후 run 부재 시 incident 1건 생성 | 설계 완료 |
| WD-F-102 | 결과물 신선도와 발행 건수를 감시한다 | health contract 위반 시 DEGRADED/FAILED 생성 | 설계 완료 |
| WD-F-103 | 동일 장애를 중복 통지하지 않는다 | incident key당 OPEN 알림 1회 | 설계 완료 |
| WD-F-104 | 정상화 시 회복을 알린다 | OPEN incident가 정상 신호 수신 후 RESOLVED 1회 | 설계 완료 |

### 비기능 요구사항

| ID | 요구사항 | 목표 |
|---|---|---|
| WD-N-001 | 보안 | watchdog은 기본 `permissions: {}` 유지; API 사용 job만 최소 read 권한 |
| WD-N-002 | 장애 격리 | 감시 실패가 발행 pipeline 실행을 차단하지 않음 |
| WD-N-003 | 멱등성 | 동일 slot/run에 동일 incident key 사용 |
| WD-N-004 | 시간 기준 | 저장은 UTC, 표시와 업무 캘린더 판정은 Asia/Seoul |
| WD-N-005 | 관측성 | 모든 판정에 reason code와 source timestamp 기록 |
| WD-N-006 | 비용 | Phase 1 detector는 10~15분 주기, API pagination 최소화 |
| WD-N-007 | 개인정보 | secret, 원문 payload, Telegram 응답 전문을 incident에 저장하지 않음 |

---

## 3. Phase 0 구현설계

### 3.1 Daily Comic step 교정

#### 기존 문제

`Run Daily Comic Pipeline`의 multiline shell 안에 YAML처럼 보이는 `- name:`과 `run:`이 포함되어 있었다. 이는 Actions step이 아니라 shell 입력이므로 실행 즉시 명령 오류를 만들 수 있다.

#### 변경 구조

```text
Install Playwright
  → Anti-bot random delay (schedule only)
  → Run Daily Comic Pipeline
  → Notify Failure (실패 시)
```

- delay step은 schedule에서만 실행한다.
- 수동 dispatch는 운영자 검증 시간을 줄이기 위해 delay를 건너뛴다.
- `sleep "$DELAY"`로 변수 확장을 안전하게 인용한다.
- 최대 delay 899초와 기존 job timeout 15분의 경계가 매우 가깝다. 후속 작업에서 설치/실행 P95를 측정한 뒤 timeout을 재산정해야 한다.

### 3.2 Timeout 표준

모든 운영 job은 `timeout-minutes`를 명시해야 한다. 이번 변경에서는 유일하게 누락된 Comic Novel에 15분을 적용했다. 또한 최대 899초 delay가 기존 timeout과 같거나 더 길었던 Daily Comic(15분)과 Weekend Content(10분)는 setup과 발행 시간을 흡수하도록 30분으로 상향했다.

향후 timeout은 다음 공식으로 분기별 재산정한다.

```text
timeout = ceil(P95 setup + 최대 의도적 delay + P95 business runtime + 20% buffer)
```

`test_every_production_job_has_a_timeout`은 명시 여부를 검사하고, `test_long_random_delays_have_runtime_headroom`은 현재 15분 delay를 사용하는 두 job이 30분 이상의 상한을 갖는지 검사한다. 그 밖의 시간 값은 실제 실행 지표를 수집한 뒤 정책 테스트로 확장한다.

### 3.3 CI Guardrail

`Alert Regression Tests` workflow에 독립 job `workflow-guardrails`를 추가한다. Alert 테스트 dependency 설치/실행과 결합하지 않으므로 빠르게 실패하고 원인을 분리할 수 있다.

```text
workflow/test file 변경
  → checkout
  → Python 3.11
  → pytest watchdog + operations
  → actionlint 1.7.7
```

#### Trigger 경로

- `.github/workflows/*.yml`
- `tests/test_watchdog_workflow.py`
- `tests/test_workflow_operations.py`

PR과 main push 모두 동일하게 적용한다.

#### actionlint 공급망 정책

- Docker image tag는 `rhysd/actionlint:1.7.7`로 버전을 고정한다.
- `latest`는 사용하지 않는다.
- 다음 보안 hardening에서 image digest까지 고정하고 월 1회 Dependabot/Renovate 방식으로 갱신한다.

### 3.4 회귀 테스트 설계

| 테스트 | 보호 대상 | 실패 의미 |
|---|---|---|
| `test_daily_comic_delay_is_a_real_step` | YAML처럼 보이는 shell 오삽입 재발 | Daily Comic 실행 불가 가능성 |
| `test_every_production_job_has_a_timeout` | hang 무제한 대기 | 실패 감지 지연/Actions 비용 증가 |
| `test_long_random_delays_have_runtime_headroom` | 의도적 delay가 timeout을 소진하는 구성 | 정상 실행의 무작위 timeout |
| `test_watchdog_guardrails_are_connected_to_ci` | 테스트 파일만 존재하고 자동 실행되지 않는 상태 | 정책 drift가 PR에서 미탐지 |
| 기존 watchdog 3 tests | 감시 목록, 최소 권한, Telegram 계약 | 중앙 실패 감지 약화 |

---

## 4. Phase 1 Missed-schedule Detector 상세설계

### 4.1 구성요소

| 컴포넌트 | 예정 경로 | 책임 |
|---|---|---|
| 정책 registry | `config/watchdog_jobs.yml` | workflow/session별 schedule, grace, owner, severity, runbook |
| detector | `core/watchdog_detector.py` | 기대 slot 계산, GitHub run과 대조, 판정 생성 |
| GitHub client | `core/github_actions_client.py` | Actions run 조회, pagination/timeout/error 표준화 |
| incident store | `db/watchdog_incident_store.py` | open/dedupe/resolve 상태 전이 |
| notifier | `notifier/watchdog_router.py` | severity별 채널 라우팅과 redaction |
| entrypoint | `run_watchdog.py` | 설정 로드, 검사 orchestration, exit code |
| workflow | `.github/workflows/watchdog_schedule.yml` | 10~15분 주기 실행 및 수동 dry-run |

### 4.2 Registry 계약

```yaml
version: 1
timezone: Asia/Seoul
workflows:
  - id: main-morning
    workflow_file: main.yml
    workflow_name: Investment OS Auto Publish
    schedules:
      - cron_utc: "36 21 * * 0-4"
        session: morning
    grace_minutes: 20
    start_tolerance_minutes: 5
    severity: P0
    owner: content-platform
    runbook: docs/runbooks/watchdog_main.md
    calendar: us-market-kst
    enabled: true
```

#### 검증 규칙

1. `id`는 registry 내 유일해야 한다.
2. `workflow_name`은 대상 workflow의 top-level name과 일치해야 한다.
3. `cron_utc`는 저장소에서 지원하는 5-field cron subset이어야 한다.
4. `grace_minutes`는 5~180 범위여야 한다.
5. `severity`는 P0/P1/P2 중 하나여야 한다.
6. `runbook` 파일이 저장소에 존재해야 한다.
7. 비활성 schedule도 삭제하지 않고 `enabled: false`와 사유를 기록한다.

### 4.3 판정 알고리즘

검사 시각을 `now_utc`라 할 때 각 활성 schedule에 대해 다음을 수행한다.

1. `now_utc - lookback`부터 현재까지의 cron slot을 계산한다.
2. KST 캘린더 정책으로 휴장/주말 제외 여부를 판정한다.
3. `slot + grace <= now_utc`인 가장 최근 slot을 검사 대상으로 선택한다.
4. GitHub Actions API에서 해당 workflow의 `event=schedule` run을 조회한다.
5. `created_at >= slot - start_tolerance`인 run이 있으면 존재로 판정한다.
6. run이 없으면 `MISSED_SCHEDULE`, grace 이후 시작했으면 `LATE_START`를 생성한다.
7. incident key로 store를 조회해 신규 OPEN일 때만 통지한다.
8. 이후 정상 slot/run을 확인하면 기존 incident를 RESOLVED하고 회복 메시지를 한 번 보낸다.

### 4.4 판정 결과 모델

```json
{
  "schema_version": 1,
  "check_id": "uuid",
  "checked_at": "2026-09-13T22:00:00Z",
  "policy_id": "main-morning",
  "scheduled_slot": "2026-09-13T21:36:00Z",
  "status": "MISSED_SCHEDULE",
  "severity": "P0",
  "reason_code": "NO_RUN_AFTER_GRACE",
  "workflow_run_id": null,
  "incident_key": "investment-os:main-morning:2026-09-13T21:36Z:MISSED_SCHEDULE"
}
```

### 4.5 GitHub API 오류 정책

| 조건 | 처리 |
|---|---|
| 401/403 | detector 자체 P0 `AUTH_ERROR`; schedule 누락으로 오분류하지 않음 |
| 429 | `Retry-After` 존중, 최대 3회; 지속 시 P1 `RATE_LIMITED` |
| 5xx/timeout | exponential backoff+jitter 최대 3회; UNKNOWN 판정 |
| 응답 schema 오류 | P1 `UPSTREAM_SCHEMA_ERROR` |
| pagination 초과 | lookback 제한 후 P1 `INSUFFICIENT_EVIDENCE` |

GitHub API가 불확실한 상태에서는 대상 workflow를 실패로 단정하지 않고 detector 상태를 `UNKNOWN`으로 둔다.

---

## 5. Phase 2 Health Event 상세설계

### 5.1 공통 이벤트 계약

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "repository": "owner/investment-os",
  "workflow": "Investment OS Auto Publish",
  "run_id": "123456",
  "run_attempt": 1,
  "job": "morning",
  "session": "morning",
  "started_at": "2026-09-13T21:36:10Z",
  "finished_at": "2026-09-13T21:43:20Z",
  "status": "SUCCESS",
  "data_freshness": {"core_data_age_seconds": 42},
  "records": {"collected": 12, "validated": 12},
  "publish_targets": {
    "x": {"expected": 1, "succeeded": 1},
    "telegram": {"expected": 1, "succeeded": 1}
  },
  "fallbacks": [],
  "error_code": null,
  "system_version": "from config/settings.py"
}
```

### 5.2 상태 정의

| 상태 | 조건 | workflow exit |
|---|---|---|
| SUCCESS | 필수 데이터 fresh, validation pass, 기대 발행 건수 충족 | 0 |
| DEGRADED | 허용 fallback 사용 또는 비핵심 target 일부 실패 | 정책별 0/1 |
| FAILED | validation 실패, 핵심 target 누락, 결과물 미생성 | 1 |
| SKIPPED | 휴장일/중복 방지 등 의도된 skip | 0, reason 필수 |
| UNKNOWN | 관측 저장 실패 또는 근거 부족 | 업무 결과와 별도 watchdog 경보 |

성공 여부와 관측 이벤트 저장 성공 여부를 분리한다. health store 장애가 실제 발행을 중복 재시도하게 해서는 안 된다.

### 5.3 멱등성과 중복 발행

- event idempotency key: `{run_id}:{run_attempt}:{job}:{session}`
- publish idempotency key: `{channel}:{session}:{kst_business_date}:{content_hash}`
- incident key: `{policy_id}:{scheduled_slot}:{reason_code}`

재실행 전 publish history와 실제 target 응답을 확인한다. health-event 저장 실패만으로 업무 pipeline을 자동 재실행하지 않는다.

---

## 6. Incident 상태 모델

```text
                  ┌─────────────┐
new signal ──────>│ OPEN        │
                  └──────┬──────┘
                         │ operator ack
                         ▼
                  ┌─────────────┐
                  │ ACKNOWLEDGED│
                  └──────┬──────┘
                         │ healthy evidence
                         ▼
                  ┌─────────────┐
                  │ RESOLVED    │
                  └─────────────┘

OPEN/ACKNOWLEDGED ── maintenance policy ──> SUPPRESSED ── expiry ──> OPEN
```

### 상태 전이 규칙

- 동일 incident key의 반복 신호는 `occurrence_count`와 `last_seen_at`만 갱신한다.
- OPEN 최초 1회, escalation 조건 충족 1회, RESOLVED 1회만 통지한다.
- SUPPRESSED에도 이벤트는 저장하되 외부 통지만 생략한다.
- healthy evidence는 같은 policy의 **해당 slot 또는 이후 slot**이어야 한다.
- 운영자 ACK만으로 장애를 RESOLVED하지 않는다.

---

## 7. 보안 상세

1. 종료 알림 watchdog은 현재처럼 checkout 없이 `permissions: {}`를 유지한다.
2. schedule detector는 `actions: read`, `contents: read`만 job 수준으로 부여한다.
3. incident를 GitHub Issue로 저장할 경우에만 별도 job에 `issues: write`를 부여한다. Supabase 사용 시 불필요하다.
4. fork PR의 코드가 secret을 사용하는 `workflow_run` 경로에서 실행되지 않게 한다.
5. 로그 redactor는 token, Authorization header, Telegram bot URL, channel id를 마스킹한다.
6. artifact에는 원문 API 응답 대신 status code, request id, reason code만 저장한다.
7. third-party action/image는 버전 또는 digest를 고정한다.

---

## 8. 테스트 전략

### 단위 테스트

- cron: weekday shift, range/list/step, month/year boundary
- timezone: UTC↔KST 날짜 전환 및 DST 비적용 확인
- calendar: 평일, 주말, 미국 휴장일, `FORCE_RUN`
- detector: on-time, late, missed, in-progress, API unknown
- incident: dedupe, ACK, recovery, suppression expiry
- health contract: SUCCESS/DEGRADED/FAILED/SKIPPED 경계
- redaction: token/API URL/channel id 비노출

### 통합 테스트

- fixture GitHub API 응답으로 pagination/retry/429/5xx 검증
- fake incident store로 OPEN→ACK→RESOLVED 전이 검증
- fake Telegram endpoint로 HTTP 200 + `ok=false` 검증
- registry의 workflow name과 실제 YAML name 일치 검증

### 운영 검증

1. 수동 smoke test: Telegram 테스트 메시지 수신
2. shadow mode: 7일간 incident 저장만 하고 알림하지 않음
3. fault injection: 비핵심 테스트 workflow 실패/누락으로 1회 검증
4. P1만 활성화 후 false-positive 관찰
5. P0 활성화 및 보조 채널 추가

---

## 9. 배포 계획

### Phase 0 배포

1. PR에서 `workflow-guardrails`와 기존 alert regression 통과 확인
2. Daily Comic을 `workflow_dispatch` + dry-run으로 실행
3. Comic Novel을 dry-run으로 실행하고 15분 timeout 내 종료 확인
4. watchdog 수동 dispatch 후 내부 paid channel 수신 확인
5. 다음 scheduled Daily Comic run의 delay/pipeline step 경계 확인

### Phase 1 배포

1. registry와 detector를 unit test와 함께 merge
2. `DRY_RUN=true`, notification disabled로 7일 shadow 실행
3. 실제 run history와 missed/late 판정을 매일 대조
4. grace period를 workflow P95 기준으로 조정
5. P1 알림부터 활성화하고 3일 뒤 P0 활성화
6. 2주간 false-positive가 목표치 이하이면 정식 운영 전환

### Rollback

- Phase 0: 문제가 생기면 guardrail job 또는 개별 workflow 변경 commit을 revert한다.
- timeout 롤백보다 측정 근거에 따른 상향을 우선한다.
- Phase 1: detector workflow를 disable하고 업무 workflow는 그대로 유지한다.
- 알림 flood 시 notification router만 `monitor` 모드로 전환하며 이벤트 저장은 유지한다.

---

## 10. 운영 Runbook

### 중앙 watchdog 알림 발생

1. 메시지의 Actions URL에서 conclusion과 실패 step을 확인한다.
2. `failure`, `cancelled`, `timed_out`, `MISSED_SCHEDULE`, `DEGRADED`로 분류한다.
3. 인증, 429, 외부 5xx, validation, 코드 오류 중 reason code를 결정한다.
4. 재실행 전에 X/Telegram 실제 발행 여부와 publish history를 확인한다.
5. 가능한 경우 동일 입력으로 dry-run 후 실발행한다.
6. 결과물 timestamp/건수와 양 채널 도착을 확인한 뒤 incident를 종료한다.

### Daily Comic 실패

1. `Anti-bot random delay`인지 `Run Daily Comic Pipeline`인지 실패 step을 구분한다.
2. timeout이면 setup + delay + pipeline 실제 소요 시간을 기록한다.
3. 수동 dispatch는 delay 없이 실행되므로 dry-run 진단에 사용한다.
4. 이미지 artifact와 Telegram/X 도착을 확인한 뒤 실발행 여부를 결정한다.

### Watchdog 자체 실패

1. Actions UI에서 watchdog 최근 run을 직접 확인한다.
2. `TELEGRAM_BOT_TOKEN`과 `TELEGRAM_PAID_CHANNEL_ID` 설정/권한을 확인한다.
3. Telegram API 상태 및 bot의 채널 membership을 확인한다.
4. 보조 채널 도입 전까지는 GitHub Actions notification을 임시 2차 채널로 사용한다.
5. 복구 후 수동 smoke test run URL과 수신 시각을 운영 일지에 남긴다.

---

## 11. 개발 산출물 목록

| 산출물 | 경로 | 상태 |
|---|---|---|
| 현황/로드맵 | `docs/watchdog_operations_analysis_2026-09-13.md` | 갱신 |
| 상세설계/Runbook | `docs/watchdog_detailed_design_2026-09-13.md` | 신규 |
| Daily Comic workflow 교정 | `.github/workflows/comic_daily.yml` | 구현 |
| Comic Novel timeout | `.github/workflows/comic_novel.yml` | 구현 |
| 장시간 delay job timeout 보정 | `.github/workflows/comic_daily.yml`, `.github/workflows/weekend_content.yml` | 구현 |
| CI workflow guardrail | `.github/workflows/ci_alert_tests.yml` | 구현 |
| 기존 watchdog 정책 테스트 | `tests/test_watchdog_workflow.py` | 기존/CI 연결 |
| 공통 workflow 운영 테스트 | `tests/test_workflow_operations.py` | 신규 |

---

## 12. Definition of Done

- [x] Daily Comic delay가 독립 step이고 dispatch에서는 skip된다.
- [x] 모든 운영 job에 timeout이 명시되어 있다.
- [x] 최대 15분 delay를 쓰는 job에 setup/실행용 timeout 여유가 있다.
- [x] workflow 변경이 watchdog/운영 테스트와 actionlint를 trigger한다.
- [x] guardrail 테스트가 로컬에서 통과한다.
- [x] 상세설계와 rollback/runbook이 저장소에 있다.
- [ ] GitHub-hosted CI에서 actionlint image 실행이 확인되었다.
- [ ] Daily Comic/Comic Novel dry-run 실행이 확인되었다.
- [ ] Telegram 수동 smoke test 수신 증적이 남았다.
- [ ] 최근 30일 실행 시간 baseline이 기록되었다.

미완료 항목은 실제 GitHub Actions 및 운영 secret 접근이 필요한 배포 검증으로, PR merge 전 또는 직후 운영자가 수행한다.
