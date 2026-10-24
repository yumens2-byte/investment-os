# NUNA 운영 반영 및 휴먼게이트 베타 가이드 — 2026-10-24

## 운영 반영 기록

- PR #22 병합 완료: https://github.com/yumens2-byte/investment-os/pull/22
- 운영 코드 커밋: `3fd7f9f86707c7357170af6b4441de923db75d2e`
- 승인된 변경 head: `bcbb5bc10f68f8c5ac3b37bc6968444491d4b303`
- 반영 전 GitHub Python 3.11 NUNA 158건 통과, 로컬 158건 + 운영 가드레일 14건 통과.
- 배포 직후 W43/W44 회차 없음, W43 발행 대상·비용 원장 0건을 읽기 전용 확인.
- 키/운영 변수는 변경하지 않았다. 실생성·SNS 예약·게시 워크플로는 실행하지 않았다.
- 이 문서는 사용자 수동 실행으로 검수와 발행 승인을 분리하는 베타 절차다.
- 배포 후 검사: https://github.com/yumens2-byte/investment-os/actions/runs/40059975544

## 0. 최초 설정 확인

Repository → Settings → Secrets and variables → Actions에서 확인한다. 키 값은 대화나 로그에 복사하지 않는다.

| 종류 | 이름 | 베타 기준 |
|---|---|---|
| Variables | NUNA_ENABLED | 0 — 정기 생성·발행 중단, 수동 workflow_dispatch는 실행 가능 |
| Variables | NUNA_DURATION_SEC | 60 |
| Variables | NUNA_FB_CUT | 0 |
| Variables | NUNA_MARKET_SOURCE | direct |
| Variables | NUNA_TTS_VOICE | Kore |
| Secrets | SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY | 둘 다 필요. 최초 실패 로그의 SERVICE_ROLE_KEY가 비어 있었으므로 우선 확인 |
| Secrets | ANTHROPIC_API_KEY, GEMINI_API_SUB_PAY_KEY, FAL_AI_KEY | 실생성에 필요 |
| Secrets | ZERNIO_API_KEY, ZERNIO_API_SUB_KEY | 생성 결과 스테이징과 예약에 필요 |
| Secrets | NUNA_INSTAGRAM_ACCOUNT_ID, NUNA_THREADS_ACCOUNT_ID | main 키에 연결된 계정 |
| Secrets | NUNA_YOUTUBE_ACCOUNT_ID, NUNA_FACEBOOK_ACCOUNT_ID | sub 키에 연결된 계정 |
| Secrets | TELEGRAM_BOT_TOKEN, TELEGRAM_PAID_CHANNEL_ID | 생성 결과 검수 알림 |
| Secrets | NUNA_TELEGRAM_ADMIN_IDS | 보류 권한 Telegram 사용자 ID 목록 |

NUNA_ENABLED의 현재 값은 API로 확인하지 못했다. 자동 예약과 수동 검수를 분리하려면 베타 시작 전 0임을 확인한다.
사전에 종료된 미국 월~수 거래일을 대상으로만 테스트한다.

## 1. 오늘 테스트의 공통 입력

오늘은 2026-10-24(토)다. 테스트 회차 선택일을 `2026-10-22`로 고정한다.
대상 미국장은 10-19~21, 회차 ID는 `nuna-2026-W43`이다.

- 모든 Run workflow의 Branch는 `main`.
- 모든 단계에서 같은 date `2026-10-22` 사용.
- date를 비우면 오늘 토요일로 판정돼 생성이 SKIPPED가 될 수 있다.
- `2026-10-29`를 넣으면 다음 주 미국장이 아직 종료되지 않아 MARKET_NOT_READY 대상이다.

| 순서 | 워크플로 | step | date | dry_run | 의미 |
|---|---|---|---|---|---|
| A | nuna-weekly-gen | preflight | 2026-10-22 | true | 달력·장 종료 판정 |
| B | nuna-weekly-gen | run | 2026-10-22 | false | 유료 실생성 → QC → 스테이징 → Telegram 알림 |
| C | nuna-weekly-publish | publish | 2026-10-22 | false | 검수 완료 후 실행하는 실제 예약·발행 승인 |
| D | nuna-weekly-publish | verify | 2026-10-22 | false | 외부 게시물 상태·URL 조회 및 원장 갱신 |

gen: https://github.com/yumens2-byte/investment-os/actions/workflows/nuna_weekly_gen.yml
publish: https://github.com/yumens2-byte/investment-os/actions/workflows/nuna_weekly_publish.yml

## 2. 휴먼게이트 A — 사전 판정

gen → Run workflow → A 입력값으로 실행한다.
성공 로그에서 회차 nuna-2026-W43, 발행 기준일 2026-10-22, 거래일 10-19~21을 확인한다.

preflight 성공은 키·잔액·계정 연결 성공을 의미하지 않는다. DB·유료 SDK를 초기화하지 않는 달력 검사다.
실행 URL로 점검을 요청한 뒤 다음 단계로 진행할 수 있다.

## 3. 휴먼게이트 B — 실생성 및 검수

A 통과와 설정 확인 후 B 입력값으로 gen을 한 번 실행한다. dry_run=false이므로 생성 비용이 발생한다.

완료 기준:
- collect → script → tts → images → clips → assemble → qc → stage → notify 순서 성공.
- 회차 상태 NOTIFIED, QC 통과, main/sub 스테이징 URL 존재.
- Telegram에서 생성 영상과 발행 예정 시각 확인.
- Actions의 QC artifact 확인. artifact에는 영상·대본이 포함되지 않는다.

영상은 60초 길이, 한국어 음성·수치·주차, 캐릭터 일관성, 자막 잘림, 면책 문구를 확인한다.
캡션·대본은 원장의 script_json과 실제 영상으로 확인한다. 영상 알림이 없거나 검수할 수 없으면 발행하지 않는다.
알림은 예약 계획을 보여줄 뿐이다. NUNA_ENABLED=0에서 publish를 실행하지 않으면 SNS 예약은 만들어지지 않는다.

이 단계에서 중단하고 생성 실행 URL로 점검을 요청한다. 검수에 합격한 경우에만 C를 실행한다.
별도의 [승인] 버튼은 없다. **C의 실발행 워크플로 실행이 승인 행위**다.

주의:
- gen dry_run=true 결과는 메모리 원장이어서 별도 publish 실행으로 전달되지 않는다.
- gen을 script/tts/images 단위로 쪼개 실행하면 러너 간 로컬 자산 유실·재합성 문제가 생길 수 있으므로 첫 베타는 step=run을 사용한다.
- 유료 오류는 PAID_RECOVERY_REQUIRED로 보호될 수 있다. 실패 URL을 먼저 점검하고 무조건 재실행하지 않는다.

## 4. 휴먼게이트 C — 예약 승인 및 보류 시험

검수 합격 후 C 입력값으로 publish를 실행한다. 실제 4채널 예약/발행을 승인하는 단계다.
예약은 Instagram·Threads·YouTube·Facebook 4채널을 대상으로 하며 3개 Zernio 요청으로 처리한다.

보류창은 생성 알림 후 60분, 예약은 max(기준일 08:30, 실제 생성 알림+62분)이다.
오늘 새로 생성한 W43 회차는 오늘의 알림 시각을 사용하므로 미래 예약이 가능하다.
예: 오늘 알림 14:00 → 보류창 종료 15:00 → 예약 15:02. 이는 설명용 시각이며 실제 알림을 기준으로 판단한다.

검수 후 예약 승인하려면 보류 버튼을 누르지 않고 C를 실행한다.
- 보류창이 남아 있으면 30초 이하 간격으로 보류 콜백을 확인하면서 기다린다.
- 창이 끝난 후 예약 요청을 보낸다.
- 알림+62분이 이미 지났으면 과거 예약 차단이 발생할 수 있다. 시각·원장을 먼저 확인한다.

보류 기능 자체를 시험하려면:
1. C 실행 전 또는 보류창 대기 중, 권한 있는 사용자로 Telegram [보류]를 누른다.
2. 원장 HELD, 예약 POST 없음, 4채널 게시 없음이 합격 기준이다.
3. HELD가 되면 같은 회차를 publish 재실행해도 발행하지 않는다.
4. 이 시험은 발행 전 단계에서 끝난다. HELD를 임의 해제해 실발행 시험으로 이어가지 않는다.

첫 회차에서 보류 시험과 실제 발행 시험을 모두 끝낼 수 있다고 가정하지 않는다.
먼저 '보류 차단 시험' 또는 '검수 후 실제 발행 시험' 중 이번 회차의 목적을 선택한다.
예약 POST가 이미 만들어진 뒤의 보류 버튼은 자동 취소 기능이 아니다.

## 5. 휴먼게이트 D — 실제 발행 확인

예약 시각 이후 workflow의 자동 verify 결과를 확인한다. 별도 확인이 필요하면 D 입력값으로 실행한다.
dry_run=true로 별도 verify를 실행하면 운영 회차를 검증하는 절차가 되지 않으므로 false를 사용한다.

합격 기준:
- Instagram·Threads·YouTube·Facebook 모두 플랫폼 상태 published.
- 회차 전체 상태 PUBLISHED.
- 실제 플랫폼 URL 4건을 열어 영상과 계정 확인.
- 중복 게시 없음, 예약 시각·실제 발행 결과·실패 사유 확인.

SCHEDULED는 예약 접수 상태다. 워크플로 초록색만으로 실제 발행 성공을 판정하지 않는다.
일부 실패/PARTIAL/pending/RECONCILE_REQUIRED면 성공 보고를 중단하고 실행 URL과 회차로 점검한다.

## 6. 중단·복구 기준

| 상태/오류 | 다음 행동 |
|---|---|
| SERVICE_ROLE_KEY 미설정 | Secrets 설정 확인 후 재판정 |
| MARKET_NOT_READY | date/대상 거래일 확인, 미래 데이터 요구 금지 |
| PAID_RECOVERY_REQUIRED | 공급자 요청·비용·부분 자산 대사 후 복구 |
| RECONCILE_REQUIRED 또는 pending | 신규 발행 재실행 금지, 기존 post ID/요청 조회 |
| HELD | 의도된 중단, 임의 원장 변경 금지 |
| 예약 시각 경과 | 날짜·시각을 임의 수정하지 않고 점검 |
| 영상/음성/QC/캡션 검수 실패 | publish 실행 금지 |
| 이미 외부 예약 존재 | 중복 생성보다 기존 예약 대사 우선 |

베타 통과 전 NUNA_ENABLED를 1로 올리지 않는다. 중단 시 기존 외부 예약은 자동 취소되지 않는다.
