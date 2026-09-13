# Investment OS 와치독 구현 현황 및 확장 운영안

> 기준일: 2026-09-13<br>
> 목적: 현재 저장소의 정적 분석 결과를 Notion에 바로 가져올 수 있는 운영 문서로 정리<br>
> 범위: GitHub Actions 와치독, 개별 워크플로, 애플리케이션 복구 장치(DLQ·retry), 테스트<br>
> 주의: 이 문서는 저장소 코드 기준이다. GitHub Actions 실제 실행 이력, Telegram 수신 이력, Repository Secrets/Variables의 실제 설정값은 확인하지 않았다.

---

## 1. 한눈에 보는 결론

현재 와치독은 **“GitHub Actions가 시작한 운영 워크플로가 비정상 종료되면 내부 Telegram 채널에 알리는 1차 실패 감지기”**까지 구현되어 있다.

- 운영 워크플로 10개를 `workflow_run.completed` 이벤트로 감시한다.
- `success`가 아닌 `failure`, `cancelled`, `timed_out` 등의 종료 결과를 알린다.
- 알림에는 저장소, 워크플로 이름, 결론, 트리거, KST 시각, 실행 로그 URL이 포함된다.
- Telegram 전송은 네트워크 재시도, 연결/전체 타임아웃, HTTP 상태, 응답 JSON의 `ok=true`까지 검사한다.
- 최소 권한(`permissions: {}`)이며 감시 대상 코드를 checkout/실행하지 않아 `workflow_run`의 권한 상승 위험을 줄였다.
- 수동 실행(`workflow_dispatch`)으로 Telegram 연결과 시크릿을 smoke test할 수 있다.
- 감시 대상 누락, 최소 권한, 실패 전용 조건, Telegram 검증 규칙을 확인하는 회귀 테스트가 있다.

반면 현재 구조는 **사후 실패 알림**이지 완전한 가용성 감시는 아니다. 다음 상황은 놓칠 수 있다.

1. GitHub가 cron 실행 자체를 시작하지 않은 경우
2. cron/`if` 불일치로 모든 job이 `skipped`되고 workflow가 성공처럼 끝나는 경우
3. 프로세스가 성공 종료했지만 실제 데이터가 오래됐거나 발행 결과가 누락된 경우
4. Telegram 자체 장애 또는 와치독 workflow 자체 실패
5. 반복 장애의 묶음 처리, 담당자 배정, 재알림, 복구 알림, SLO 집계

따라서 다음 확장의 핵심은 **실패 이벤트 감지 → 예정 실행 누락 감지 → 결과물/데이터 신선도 감지 → 다중 채널 및 온콜 운영 → SLO 기반 운영** 순서다.

---

## 2. 시스템 및 감시 범위

Investment OS는 GitHub Actions에서 데이터 수집, 분석, 이미지 생성, X/Telegram 발행을 수행한다. 현재 `.github/workflows`에는 CI와 와치독을 제외하고 다음 10개 운영 workflow가 있으며, 와치독 목록도 이 10개와 일치한다.

| # | Workflow 표시 이름 | 파일 | 주기/역할 | 와치독 |
|---:|---|---|---|---|
| 1 | `Investment OS Auto Publish` | `main.yml` | 모닝·내러티브·대시보드·alert 등 핵심 파이프라인 | 감시 |
| 2 | `🎨 Comic Daily Pipeline` | `comic_daily.yml` | 평일 데일리 코믹 | 감시 |
| 3 | `📚 Comic Weekly Pipeline` | `comic_weekly.yml` | 주간 코믹 | 감시 |
| 4 | `📝 Comic Novel (Sunday)` | `comic_novel.yml` | 일요일 소설형 콘텐츠 | 감시 |
| 5 | `EDT Market Snapshot` | `edt_snapshot.yml` | EDT 시장 스냅샷 | 감시 |
| 6 | `Prediction League & Data Quiz` | `prediction_league.yml` | 예측 리그·데이터 퀴즈 | 감시 |
| 7 | `Viral Content` | `viral_content.yml` | 바이럴 콘텐츠 | 감시 |
| 8 | `📊 Viral Daily Report` | `viral_daily_report.yml` | 일일 성과 리포트 | 감시 |
| 9 | `📊 Viral Performance Tracker` | `viral_performance.yml` | 30분 단위 성과 추적 | 감시 |
| 10 | `Weekend Content` | `weekend_content.yml` | 토·일 콘텐츠 | 감시 |

### 의도적으로 제외된 workflow

| Workflow | 제외 이유 |
|---|---|
| `Alert Regression Tests` | 운영 발행 workflow가 아니라 CI이므로 현재 회귀 테스트에서도 제외 |
| `🐕‍🦺 Watchdog` | 자기 자신을 `workflow_run`으로 감시하지 않음. 무한 알림/재귀 방지에는 유리하지만 별도 외부 감시가 필요 |

### 감시 단위의 의미

현재 감시 단위는 **job이 아니라 workflow run 전체**다. 예를 들어 `main.yml` 안에 여러 세션 job이 있어도 와치독 메시지는 `Investment OS Auto Publish` 한 건으로 발생한다. 실패한 job/step 이름, 실행 attempt, commit SHA, actor는 현재 메시지에 포함되지 않는다.

---

## 3. 현재 구현 상세

### 3.1 트리거 및 판정

```text
운영 workflow 실행
  └─ completed 이벤트
       ├─ conclusion == success  → 와치독 job 미실행(무알림)
       └─ conclusion != success  → Telegram 내부 알림

workflow_dispatch
  └─ conclusion=test로 Telegram smoke test
```

- `workflow_run.types: completed`를 사용하므로 실행이 종료된 뒤 판정한다.
- 수동 smoke test 외에는 `conclusion != 'success'`일 때만 알린다.
- 아이콘은 `failure`, `cancelled`, `timed_out`, `test`, 기타 상태로 구분한다.
- 서버 시각을 `Asia/Seoul`로 변환해 메시지에 기록한다.

### 3.2 Telegram 전달 신뢰성

| 항목 | 현재 설정 | 평가 |
|---|---|---|
| 대상 | `TELEGRAM_PAID_CHANNEL_ID` | 내부 채널 단일 목적지 |
| 인증 | `TELEGRAM_BOT_TOKEN` | Repository Secret 의존 |
| 시크릿 선검증 | 빈 token/chat ID이면 즉시 실패 | 구현됨 |
| 네트워크 재시도 | 3회, 모든 curl 오류, 2초 간격 | 구현됨 |
| 연결 타임아웃 | 5초 | 구현됨 |
| 요청 전체 타임아웃 | 20초 | 구현됨 |
| HTTP 검증 | 200이 아니면 실패 | 구현됨 |
| API 의미 검증 | JSON `ok`가 `true`가 아니면 실패 | 구현됨 |
| 링크 프리뷰 | 비활성 | 운영 메시지 노이즈 감소 |
| 와치독 job 제한 | 5분 | 무한 대기 방지 |

### 3.3 보안 설계

- workflow 권한은 빈 권한 집합(`permissions: {}`)이다.
- `actions/checkout`을 사용하지 않는다.
- 트리거한 branch/ref의 스크립트를 실행하지 않고 고정된 shell 명령만 수행한다.
- Telegram 입력값은 `--data-urlencode`로 전송한다.

이는 secret 접근 권한을 갖는 `workflow_run`이 비신뢰 코드를 실행하는 경로를 차단하는 적절한 기본 설계다. 앞으로 GitHub API 조회가 필요해도 전체 권한을 켜지 말고 필요한 읽기 권한만 job 수준으로 추가해야 한다.

### 3.4 회귀 테스트

`tests/test_watchdog_workflow.py`는 다음을 정적으로 검증한다.

1. CI와 와치독을 제외한 운영 workflow 이름 집합과 감시 목록이 정확히 일치하는지
2. 성공 실행은 무알림이고 수동 smoke test는 허용되는지
3. `permissions: {}`이고 checkout이 없는지
4. Telegram 시크릿, curl retry/timeout, `ok=true` 확인이 유지되는지

**중요한 공백:** 테스트 파일은 존재하지만 `ci_alert_tests.yml`의 path filter와 실행 명령에 와치독 workflow 및 이 테스트가 포함되어 있지 않다. 따라서 개발자가 로컬 또는 전체 `pytest`를 실행하지 않으면 PR에서 자동 수행된다는 보장이 없다.

### 3.5 애플리케이션 계층의 복구 장치

와치독 외에도 장애 완화를 위한 별도 장치가 있다.

| 장치 | 구현 상태 | 역할/한계 |
|---|---|---|
| 외부 API retry | 여러 collector, Gemini, X publisher에 분산 구현 | 일시 장애 흡수. 횟수·backoff 정책은 모듈별로 다름 |
| DLQ | `core/dlq.py` | X tweet, Telegram message/document 재처리. 기본 3회 실패 후 dead letter |
| DLQ 재처리 | `run_view.py`, `run_alert.py` 시작 단계 | 다음 세션이 실제 실행되어야 처리됨 |
| Dead-letter 알림 | Telegram 무료 채널 | 알림 자체 실패는 best-effort로 무시됨 |
| 상태 명령 | `python main.py status` | 로컬 파일의 최근 분석·검증·발행 이력 요약. 중앙 health API는 아님 |
| job timeout | 대부분 workflow에 존재 | hang 상한 제공. `comic_novel.yml`에는 명시적 timeout이 없음 |
| concurrency | 일부 workflow에만 존재 | Prediction/Daily Report/Performance 및 `main.yml` 일부. 전사 공통 정책은 아님 |
| artifact/cache | `main.yml` 중심으로 사용 | 사후 분석 자료 보존. workflow별 적용과 retention이 균일하지 않음 |
| 개별 실패 알림 | Comic 일부, Viral Performance | 중앙 와치독과 중복될 수 있고 채널·검증·best-effort 정책이 서로 다름 |

---

## 4. 현재 잘 되어 있는 점

1. **단순하고 독립적이다.** 애플리케이션 dependency 설치 없이 curl과 기본 Python만 사용해 감시 경로의 실패 면적이 작다.
2. **감시 목록 drift를 테스트한다.** 새 운영 workflow 추가 시 목록 누락을 찾을 수 있는 구조다.
3. **최소 권한 원칙이 명확하다.** 특히 `workflow_run`에서 checkout을 금지한 점이 좋다.
4. **전송 성공을 다층 검증한다.** curl 성공만 믿지 않고 HTTP 200과 Telegram의 논리 응답까지 확인한다.
5. **운영자가 즉시 원인 분석으로 이동할 수 있다.** 알림에 Actions run URL이 포함된다.
6. **수동 점검 경로가 있다.** 실제 장애를 만들지 않고 secret/채널/Telegram API 연결을 확인할 수 있다.
7. **복구 계층이 별도로 존재한다.** publisher 실패를 DLQ로 넘겨 다음 세션에서 재처리하는 기반이 있다.

---

## 5. 한계 및 운영 위험

### 5.1 탐지 사각지대

| 우선도 | 사각지대 | 장애 예시 | 현재 결과 |
|---|---|---|---|
| P0 | 시작되지 않은 schedule | GitHub cron drop/delay, workflow 비활성화 | `workflow_run` 이벤트가 없어 무알림 |
| P0 | 성공으로 끝난 논리 실패 | 발행 함수가 오류를 삼키고 exit 0, 결과물 0건 | 무알림 |
| P0 | cron과 job `if` 불일치 | workflow는 시작했지만 모든 job skipped | 성공/무알림 가능 |
| P1 | stale data | `core_data.json` 또는 DB snapshot이 갱신되지 않음 | 종료 상태만 성공이면 무알림 |
| P1 | 부분 발행 실패 | X 성공, Telegram 실패 또는 반대 | workflow exit code에 반영되지 않으면 무알림 |
| P1 | 와치독/Telegram 장애 | bot 차단, secret 만료, Telegram 장애 | 와치독 run 실패만 남고 2차 통보 없음 |
| P2 | 성능 저하 | 평소 3분 작업이 18분 소요 후 성공 | 무알림 |
| P2 | 데이터 품질 저하 | 핵심 source가 fallback/기본값만 반환 | validation/exit code에 반영되지 않으면 무알림 |

### 5.2 운영 프로세스 공백

- severity, 서비스 owner, 대응 SLA, runbook 링크가 없다.
- 동일 원인의 반복 알림을 묶거나 cooldown하는 정책이 없다.
- 장애 후 정상화된 시점을 알리는 recovery 메시지가 없다.
- acknowledgement와 담당자 지정 방식이 없다.
- 주간 성공률, 누락률, MTTA/MTTR, API 실패율을 계산할 중앙 지표 저장소가 없다.
- planned maintenance/silence가 없어 점검 중 alert flood 가능성이 있다.
- workflow별 기대 실행 시각, 최대 지연, 최대 결과물 age가 기계 판독 가능한 registry로 관리되지 않는다.

### 5.3 저장소에서 즉시 확인된 관리 위험

1. **와치독 회귀 테스트가 CI에 연결되지 않았다.** 테스트의 존재와 자동 실행은 별개다.
2. **`comic_daily.yml` 실행 블록 안에 `- name:`/`run:` 형태가 문자열로 들어가 있다.** YAML step이 아니라 shell 명령으로 해석되므로 해당 단계가 실행되면 `-` 명령 오류로 실패할 가능성이 매우 높다. 와치독은 이를 사후 통지할 뿐 예방하지 못한다.
3. **개별 workflow 알림과 중앙 와치독이 혼재한다.** 같은 장애가 무료/유료 채널에 중복 통지되거나, 반대로 개별 알림의 `continue-on-error`로 전달 실패가 숨겨질 수 있다.
4. **timeout/concurrency/artifact 정책이 균일하지 않다.** workflow가 늘수록 복사·붙여넣기 drift가 커진다.
5. **표시 이름 기반 결합이다.** 운영 workflow의 top-level `name` 변경 시 감시 목록도 함께 변경해야 한다. 테스트를 자동 CI에 연결하지 않으면 누락을 머지 전에 막지 못한다.

---

## 6. 확장 가능한 목표 아키텍처

```text
[GitHub Actions 종료 이벤트] ─┐
[외부 스케줄 하트비트 검사] ──┼─> [관측 이벤트 정규화]
[데이터/발행 결과 검사] ───────┘       │
                                      ▼
                              [상태 저장소 / Incident Key]
                                      │
                 ┌────────────────────┼────────────────────┐
                 ▼                    ▼                    ▼
          [Telegram 1차]       [GitHub Issue/Slack]   [Metrics/SLO]
                 │
                 ▼
       [dedupe → escalation → recovery]
```

### 핵심 원칙

1. **Failure detector와 absence detector를 분리한다.** 종료 실패와 미실행은 서로 다른 신호다.
2. **workflow 정의와 감시 정책을 하나의 registry에서 관리한다.** 이름, owner, schedule, grace period, severity, runbook을 단일 정본으로 둔다.
3. **관측 경로는 업무 경로와 독립시킨다.** Telegram만을 유일한 경보 채널로 두지 않는다.
4. **알림이 아니라 incident lifecycle을 관리한다.** OPEN → ACK → RESOLVED 상태가 필요하다.
5. **성공의 정의를 exit code 이상으로 확장한다.** 실행 여부, 결과물 신선도, 발행 건수, API 품질을 함께 본다.

---

## 7. 단계별 확장 리스트

### Phase 0 — 즉시 안정화 (1~2일)

- [x] `comic_daily.yml`의 잘못 중첩된 anti-bot step 수정 (2026-09-13)
- [x] 모든 workflow를 `actionlint`로 검사하는 CI 추가 (2026-09-13)
- [x] `tests/test_watchdog_workflow.py`를 PR CI에 연결하고 관련 path filter 추가 (2026-09-13)
- [x] `comic_novel.yml` 포함 모든 운영 job에 `timeout-minutes` 명시 (2026-09-13)
- [x] 15분 random delay를 사용하는 Daily Comic·Weekend Content timeout을 30분으로 상향 (2026-09-13)
- [x] Viral Performance의 빈 choice option을 `repository_default` sentinel로 교체해 actionlint 오류 제거 (2026-09-13)
- [ ] 수동 watchdog smoke test 실행 후 내부 채널 수신 증적 남기기
- [ ] 각 workflow의 실제 최근 30일 성공률과 평균/95백분위 실행 시간을 기준선으로 기록

구현 상세와 후속 단계의 인터페이스·상태 모델·테스트 및 배포 계획은
`docs/watchdog_detailed_design_2026-09-13.md`를 정본으로 사용한다.

**완료 조건**

- 잘못된 YAML/shell 구조가 PR에서 차단된다.
- 운영 workflow 추가/이름 변경 시 감시 목록 테스트가 자동 실행된다.
- 모든 job은 유한한 시간 안에 종료된다.

### Phase 1 — 예정 실행 누락 탐지 (3~5일, 최우선)

- [ ] `config/watchdog_jobs.yml` 같은 registry 생성
  - workflow 표시 이름
  - schedule 및 timezone
  - 허용 지연(grace period)
  - expected weekdays/holiday policy
  - owner/severity/runbook
- [ ] 별도 scheduled watchdog를 10~15분 간격으로 실행
- [ ] GitHub Actions API에서 workflow의 마지막 scheduled run을 조회
- [ ] 기대 시각 + grace period를 넘었는데 run이 없으면 `MISSED_SCHEDULE` 발생
- [ ] 휴장일/주말/비활성 세션을 registry 정책으로 제외
- [ ] 마지막 확인 cursor와 incident key를 외부 저장소(Supabase 권장)에 기록

**권장 incident key**

```text
{repository}:{workflow}:{scheduled_slot_kst}:{failure_type}
```

이 키로 동일 실행에 대한 중복 Telegram 메시지를 억제한다.

### Phase 2 — 결과물 및 신선도 감시 (3~7일)

- [ ] 핵심 job 종료 시 공통 `health_event` JSON 기록
- [ ] 필수 필드: `run_id`, `workflow`, `job`, `session`, `started_at`, `finished_at`, `status`, `records`, `publish_targets`, `error_code`, `version`
- [ ] `core_data` timestamp, Supabase 최신 row, 발행 history의 age 검사
- [ ] 세션별 기대 결과물 수 정의(예: 분석 1건, X post N건, Telegram N건)
- [ ] 주요 collector별 `fresh/stale/fallback/error` 상태 표준화
- [ ] “workflow success + 결과물 누락”을 `DEGRADED` 또는 `FAILED`로 승격
- [ ] `python main.py health --json` 형태의 기계 판독 가능한 health command 추가

### Phase 3 — 알림 라우팅과 온콜 (3~5일)

- [ ] P0/P1/P2 severity 매트릭스 도입
- [ ] P0는 Telegram + 보조 채널(GitHub Issue, Slack, PagerDuty 등) 동시 전송
- [ ] P1/P2는 일정 시간 묶어 digest 제공
- [ ] workflow owner와 runbook URL을 메시지에 포함
- [ ] 15분 미확인/미복구 시 escalation
- [ ] 정상 실행 확인 시 `RECOVERED` 메시지 1회 발송
- [ ] maintenance window와 silence 만료 시각 지원
- [ ] bot token/channel 상태를 외부 synthetic check로 하루 1회 검사

### Phase 4 — 공통 실행 템플릿화 (1~2주)

- [ ] reusable workflow로 Python setup/dependency/actionlint/artifact/실패 후처리 표준화
- [ ] 공통 composite action으로 health event와 진단 번들 업로드
- [ ] retry를 공통 라이브러리로 통합하고 exponential backoff + jitter 적용
- [ ] retry 가능한 오류(429/5xx/timeout)와 불가능한 오류(401/403/validation) 분리
- [ ] workflow별 concurrency group 및 `cancel-in-progress` 정책 명시
- [ ] artifact retention 및 개인정보/secret redaction 정책 통일
- [ ] 개별 Telegram 실패 알림을 중앙 라우터로 통합해 중복 제거

### Phase 5 — SLO 및 용량 관리 (지속)

- [ ] 세션별 SLO 정의
  - 시작 적시성: 기대 시각 + N분 내 시작 비율
  - 실행 성공률
  - end-to-end 발행 성공률
  - 데이터 신선도
  - P95 실행 시간
- [ ] 주간 error budget 리포트 자동 발행
- [ ] API별 호출량, 429율, timeout율, fallback율 추적
- [ ] DLQ depth/oldest age/dead-letter 증가량 알림
- [ ] GitHub Actions 사용 시간 및 artifact/storage 증가량 추적
- [ ] 월 1회 장애 회고와 runbook game day 수행

---

## 8. 권장 Severity 및 알림 정책

| 등급 | 기준 | 1차 통지 | 재알림/승격 | 예시 |
|---|---|---|---|---|
| P0 | 핵심 발행 전체 중단, 2회 연속 missed schedule, 감시체계 자체 장애 | 즉시 다중 채널 | 10~15분 미확인 시 온콜 | morning 미실행, watchdog 전송 불가 |
| P1 | 단일 채널 발행 실패, stale 핵심 데이터, DLQ 급증 | 즉시 Telegram | 30분 지속 시 승격 | X만 실패, core data stale |
| P2 | 비핵심 콘텐츠 실패, 성능 저하, fallback 증가 | digest 또는 업무시간 알림 | 2회/2시간 지속 시 P1 | 코믹 1회 실패, P95 증가 |
| INFO | 복구, 수동 점검, planned maintenance | 상태 채널 | 없음 | RECOVERED, smoke test |

### 권장 메시지 포맷

```text
🚨 [P0][OPEN] morning schedule missed
Service: investment-os / Investment OS Auto Publish / morning
Expected: 2026-09-14 06:36 KST (+15m grace)
Last success: 2026-09-13 06:44 KST
Impact: morning 분석·X·Telegram 발행 미확인
Owner: content-platform
Incident: investment-os:main:2026-09-14-morning:MISSED_SCHEDULE
Runbook: <URL>
Actions: <GitHub Actions URL>
```

메시지에는 secret, API 원문 응답, 전체 payload, 사용자 데이터가 포함되지 않도록 한다.

---

## 9. 운영 지표와 대시보드

### 필수 지표

| 분류 | 지표 | 초기 경보 기준 예시 |
|---|---|---|
| 스케줄 | `workflow_start_delay_seconds` | 세션 grace period 초과 |
| 성공 | `workflow_success_total / workflow_run_total` | 24시간 핵심 workflow < 99% |
| 시간 | `workflow_duration_seconds` | 과거 14일 P95의 1.5배 |
| 신선도 | `dataset_age_seconds` | 데이터셋별 TTL 초과 |
| 발행 | `publish_success_total{target}` | 기대 건수 미달 |
| 외부 API | `api_error_total{provider,code}` | 429/5xx 급증 |
| DLQ | `dlq_depth`, `dlq_oldest_age_seconds` | depth > 0 장기화 또는 oldest > 1세션 |
| 감시 | `watchdog_delivery_success`, `watchdog_last_check_age` | 마지막 성공 점검 age 초과 |

### 최소 대시보드 구성

1. 오늘 예정 대비 실행/성공/누락 타임라인
2. workflow별 최근 30일 성공률과 P95 실행 시간
3. X/Telegram 채널별 발행 성공률
4. source별 fresh/fallback/error 비율
5. DLQ depth, oldest item, dead letter 증가 추이
6. 열린 incident, owner, age, acknowledgement 상태

---

## 10. Runbook 기본 템플릿

각 workflow별 runbook은 아래 형식을 복제한다.

```markdown
# <Workflow / Session> 장애 대응

## 영향
- 누락되는 데이터/콘텐츠/채널:
- 사용자 영향:
- 허용 복구 시간:

## 5분 진단
1. Actions run URL에서 실패 job/step 확인
2. secret/권한/429/timeout/validation 분류
3. 최신 데이터 timestamp와 발행 history 확인
4. DLQ depth와 dead letters 확인

## 안전한 복구
- DRY_RUN 재현 명령:
- 재실행 시 중복 발행 방지 확인:
- workflow_dispatch 입력값:
- rollback 기준:

## 완료 확인
- 결과물 timestamp/건수:
- X/Telegram 실제 도착:
- health event success:
- incident RESOLVED 및 회복 알림:
```

### 공통 1차 대응 순서

1. 알림의 run URL을 열고 `failure`/`cancelled`/`timed_out`/`missed`를 구분한다.
2. 인증(401/403), rate limit(429), provider(5xx), 데이터 validation, 코드 오류로 분류한다.
3. 재실행 전에 중복 방지 history와 실제 X/Telegram 도착 여부를 확인한다.
4. 가능하면 먼저 `DRY_RUN=true`로 수동 실행한다.
5. 실발행 재실행은 해당 세션의 idempotency가 확인된 뒤 수행한다.
6. 복구 후 결과물 신선도와 양 채널 발행을 확인하고 incident를 종료한다.

---

## 11. 변경 관리 체크리스트

### 운영 workflow 추가/이름 변경 시

- [ ] 감시 registry/`notify_watchdog.yml` 목록 갱신
- [ ] watchdog regression test 실행
- [ ] actionlint 실행
- [ ] schedule timezone 및 job `if` 문자열 동기화
- [ ] owner, severity, grace period, runbook 지정
- [ ] timeout과 concurrency 정책 지정
- [ ] 성공 결과물과 health contract 정의
- [ ] 수동 dry-run 및 failure injection으로 알림 검증

### Secret/채널 변경 시

- [ ] `TELEGRAM_BOT_TOKEN` 유효성 확인
- [ ] `TELEGRAM_PAID_CHANNEL_ID` 권한 확인
- [ ] 수동 watchdog smoke test 수신 확인
- [ ] token이 로그나 artifact에 출력되지 않았는지 확인
- [ ] 보조 경보 채널도 정상인지 확인
- [ ] 변경 일시·담당자·검증 run URL 기록

### 월간 점검

- [ ] 모든 기대 schedule 실행 여부 표본 확인
- [ ] watchdog smoke test
- [ ] dead letters 수동 처리 및 원인 분류
- [ ] 장기 미사용 secret/권한 정리
- [ ] workflow 평균/P95 실행 시간과 timeout 비교
- [ ] false positive/false negative 검토
- [ ] runbook 링크 및 담당자 최신화

---

## 12. 우선순위 백로그 요약

| 순번 | 항목 | 우선도 | 효과 | 난이도 |
|---:|---|---|---|---|
| 1 | `comic_daily.yml` 구조 오류 수정 + actionlint CI | P0 | 실행 전 오류 차단 | 낮음 |
| 2 | 와치독 테스트를 PR CI에 연결 | P0 | 감시 목록/보안 drift 차단 | 낮음 |
| 3 | schedule absence detector | P0 | 현재 최대 사각지대 제거 | 중간 |
| 4 | health event + 결과물 신선도 검사 | P1 | 논리적 성공/실패 판별 | 중간 |
| 5 | incident dedupe와 recovery 알림 | P1 | 알림 피로 감소, 복구 가시성 | 중간 |
| 6 | 보조 알림 채널 및 watchdog synthetic check | P1 | Telegram/와치독 SPOF 완화 | 중간 |
| 7 | 공통 reusable workflow | P1 | workflow 증가에 따른 drift 감소 | 높음 |
| 8 | DLQ 지표·age 알림 | P1 | 발행 유실 조기 탐지 | 중간 |
| 9 | SLO/error budget 대시보드 | P2 | 운영 품질의 정량 관리 | 높음 |
| 10 | 정기 failure-injection/game day | P2 | 대응 절차 실효성 검증 | 중간 |

---

## 13. 최종 평가

| 영역 | 현재 성숙도 | 판단 |
|---|---:|---|
| 비정상 종료 감지 | 3/5 | 10개 운영 workflow를 중앙 감시하며 기본기가 좋음 |
| 알림 전달 검증 | 4/5 | retry·timeout·HTTP·API 응답 검증까지 구현 |
| 보안 | 4/5 | 최소 권한 및 no-checkout 설계가 적절 |
| 미실행/지연 감지 | 1/5 | 외부 heartbeat/expected-run 비교가 없음 |
| 결과물/데이터 감시 | 1/5 | workflow 결론과 로컬 상태 중심, end-to-end contract 부족 |
| 장애 복구 | 2/5 | DLQ 기반은 있으나 중앙 지표·수동 재처리 운영이 약함 |
| 온콜/incident 운영 | 1/5 | owner·ACK·escalation·recovery·silence 없음 |
| 테스트/변경 관리 | 2/5 | 테스트는 좋지만 현재 전용 CI 연결이 없음 |

**종합:** 현재 구현은 작은 시스템에서 유효한 **Level 1 종료 실패 알림**이다. 시스템 규모가 커진 현재는 가장 먼저 **CI 연결과 workflow 정적 검증**, 이어서 **missed schedule 탐지**, **결과물 신선도/건수 검증**, **incident 상태 관리와 다중 채널화**를 도입하는 것이 비용 대비 효과가 가장 크다.
