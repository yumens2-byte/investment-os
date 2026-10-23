# NUNA 생성·발행 파이프라인 점검 — 2026-10-24

## 확인한 근거

- 기준 main: `27c6f8a75b855cddf7c21abe90437ffcc6d254e8`.
- [지정 생성 실패](https://github.com/yumens2-byte/investment-os/actions/runs/39321179527/job/118295342492):
  2026-10-20 05:40 KST, `workflow_dispatch`, `dry=True`, `DummyLLM`, 시장 소스 `direct`.
  월~수(미국 10-19/20/21) 중 10-20의 sp500/nasdaq/vix가 없어 collect에서 exit 1.
  실행 시점의 뉴욕 날짜는 10-19이며, 10-20/21 종가는 아직 존재할 수 없다.
  필수 데이터 검증을 완화하거나 미래 값을 만드는 방식으로 복구하지 않는다.
- 같은 로그의 `SUPABASE_SERVICE_ROLE_KEY`는 빈 값이다. 나머지 키의 유효성은 마스킹 로그로 판단 불가.
- [10-22 생성 예약 실행](https://github.com/yumens2-byte/investment-os/actions/runs/39698626769) 및
  [발행 예약 실행](https://github.com/yumens2-byte/investment-os/actions/runs/39705825325)은 `skipped`.
  발행 job에는 실행 step이 0개다. YAML job 조건은 수동 실행 또는 `vars.NUNA_ENABLED == '1'`이다.
  따라서 해당 예약 실행에서는 활성화 조건이 충족되지 않았다. 현재 변수 값은 직접 읽지 않았다.
- 연결된 Supabase `ccomoimhhttaklfadaos`에 읽기 전용 count 조회:
  `nuna_episodes=0`, `nuna_publish_targets=0`, `nuna_cost_ledger=0`.
  원장·발행 성공을 입증할 운영 데이터가 없다. SQL 변경은 수행하지 않았다.

## 동작 연결

생성(목 06:47 KST)과 발행(목 08:12 KST)은 독립 cron이다. workflow_run 연결은 없다.
생성은 collect → script → tts → images → clips → assemble → qc → stage → notify 순서다.
실운영에서 같은 ISO 주차 `episode_id`의 DB 원장이 `NOTIFIED`가 되면 발행이
`script_json/media_json/notified_at`을 읽어 4개 채널을 3개 Zernio 요청으로 예약한다.
발행 시각은 max(목 08:30, 생성 알림+60분)이다. GitHub cron 실제 시작은 지연될 수 있다.
DRY_RUN 원장은 메모리이므로 서로 다른 Actions 실행 사이에는 전달되지 않는다.

## 코드 수정

1. 실제 시각 기준 마지막 대상 미국장이 종료되기 전 `MARKET_NOT_READY`를 반환한다.
   뉴욕 시간대를 사용해 DST 전환을 반영하고 외부 수집 전에 차단한다. preflight도 같은 조건을 확인한다.
2. 생성 백엔드를 Ctx 생성 시 일괄 초기화하지 않고 생성 단계에서 지연 초기화한다.
   기존 발행 워크플로에는 생성 API 키를 주입하지 않으므로, 종전 실발행은 Claude 초기화부터 실패하는 구조였다.
3. preflight는 DB·SDK·생성 키 없이 달력과 종료 시각을 확인한다. 잘못된 단계는 성공으로 처리하지 않는다.
4. 생성 알림 이력이 있는 SCHEDULED/PARTIAL/FAILED 회차를 재시도한다.
   scheduled/published 채널은 건너뛰고 기존 발행 모듈의 멱등 키를 유지한다.
5. 원장 없음·생성 미완료는 exit 1로 노출한다. 상태 전이 실패도 확인한다.
6. 새 러너의 ASSEMBLED/QC_PASSED 상태에서 로컬 영상이 없으면 step=run으로
   CLIPPED에서 재조립하고 QC를 다시 실행한다. 이미지·클립은 원격 자산을 재사용한다.
   음성의 원격 백업은 기존에 없으므로 필요하면 예산 제한 안에서 TTS가 다시 생성된다.
7. 예약 시각이 지난 미발행 대상은 자동 발행하지 않는다. 과거 뉴스의 무단 재예약을 방지한다.

## 검증과 한계

- 새 회귀 테스트 13건: 미종료 주차, DST 종료 전후, 생성 키 없는 발행,
  부분 실패 재시도/중복 방지, 원장 없음·미완료, preflight, 새 러너 재조립/QC, 과거 예약 차단.
- 기존 통합 테스트의 시각을 2026-10-22 06:50 KST로 고정해 실제 실행일에 따른 변동을 제거했다.
- 실제 ffmpeg 조립·QC, dummy 생성 → 예약 → 검증을 포함한 전체 테스트를 실행한다.
  유료 생성·외부 게시·Telegram 메시지 전송은 수행하지 않는다.
- 외부 모델 ID/유료 API 실응답, Zernio 4채널 계정 연결 상태, 실운영 키는 이번 무과금 검증으로 입증하지 못한다.
- 보류 콜백은 발행 시작 시 조회하는 기존 구조다. 예약 생성 후 눌린 보류는 자동 예약 취소로 연결되지 않는다.
  완전한 보류창 보장을 위한 지속 폴링/예약 취소 설계는 별도 보완 항목이다.
- fal pending 파일은 로컬에만 있어 유료 요청 진행 중 러너가 사라지면 요청 복구를 보장하지 못한다.
  이미지·클립 완료 후 원장에 저장된 원격 자산의 재사용과 구분한다.

## 실운영 재개 조건

1. 수정 코드 반영 후 Repository Variables의 `NUNA_ENABLED=1`을 확인한다.
2. GitHub Actions Secrets에 `SUPABASE_SERVICE_ROLE_KEY`와 README의 생성·발행·계정 키를 설정한다.
   키를 대화나 코드에 복사하지 않는다. 기존 SUPABASE_KEY를 권한 확인 없이 대체 사용하지 않는다.
3. 완료된 주차 목요일을 date로 지정하고 gen의 preflight를 확인한다.
4. gen `step=run`, `dry_run=false`는 유료 생성이다. 정상 완료 후 DB `NOTIFIED`, QC 결과,
   media_json, 생성 알림을 확인한다. dry_run=true 생성은 발행용 운영 원장을 만들지 않는다.
5. publish는 같은 date를 사용한다. 과거 예약 시각이면 자동 중단한다. 현재 주차의 정상 예약 시간에 실행한다.
6. `step=verify`로 채널별 실제 URL과 원장 상태를 확인한다. 일부 실패는 예약 시각·원장 확인 후 publish 재시도한다.

현재 판정: 코드 수정·무과금 검증 단계. 운영 활성화/키 설정/유료 생성/실발행 성공 판정은 별개다.

최종 검증 결과: `python -m pytest gen/nuna_weekly/tests -q` — 137 passed in 78.52s.
`python -m ruff check gen/nuna_weekly` 및 `git diff --check` 통과.
