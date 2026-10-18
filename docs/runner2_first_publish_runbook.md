# Runner2 첫 실발행 입회 런북 (하드 스톱 해제 절차)

상태: **대기** — 마스터 입회 시 아래 절차만 수행한다. 본 문서는 절차서이지 실행 기록이 아니다.
작성: 2026-10-18 (KST). 원장 정본: `state/publish_state.json`.

## 0. 원칙

- 첫 실발행은 **마스터 입회 하에 1회만**. cron 무인화(`RUNNER2_ENABLED`)는 입회 실발행의 성공 관측 이후에만 설정한다.
- 승인 범위 외 Zernio 호출 금지. 본 절차가 거치는 Zernio 쓰기는 stage(PUT 2회) + publish(POST 1회)뿐이다.
- 실패·보류·게이트 미통과 시 발행하지 않고 보고만 한다(fail-closed).

## 1. 사전 조건

- [x] ep87 치유본 릴리즈 asset 준비: tag `ep87-threads-republish` / 파일 `ep87_v273.mp4` (18,997,176B)
      sha256 `af652be56d0eb3b09355e29ca5a794b217d63025e9cac585b336ee2246581c48` — AI Drive `/hero-shorts/ep87/ep87_final_voiced_v2.7.3.mp4`와 동일 실물
- [x] TH 게이트 재판정(2026-10-18 로컬 실측): 치유본을 원장 기록 경로에 배치 시 `failed=[]` — TH-G4 포함 전부 통과. mp4/h264/aac/60.87s 실측.
- [x] 러너2 워크플로우에 게이트 단계 `HERO_THREADS_ACCOUNT_ID` secret 주입 + 릴리즈 asset 사전 확보(sha 재검증) 배치 반영
- [x] 모니터 가동 중: `zernio_publish_watch` 30분 폴링(텔레그램 알림) — ep88 게시물 2건 failed 감시 확정
- [ ] **구 post 상태 확인**: 9·26 생성 Zernio post `6ab6ad110908be0a3604b286`(ep87) — GET 정본 조회로 pending/scheduled이면 **입회 전 Delete로 취소**(이중 게시 방지). failed/expired면 그대로 진행.
- [ ] ep89 post_id 정본 확인 — GET 조회 후 watch 슬롯 `enabled=true` 전환

## 2. 입회 실발행 (수동, shadow=false)

```bash
gh workflow run zernio_publish_runner2.yml \
  -f shadow=false \
  -f release_tag=ep87-threads-republish \
  -f media_name=ep87_v273.mp4 \
  -f media_sha256=af652be56d0eb3b09355e29ca5a794b217d63025e9cac585b336ee2246581c48
```

기대 흐름: select(ep87, threads) → gates A-G2(전부 통과 기대) → media(sha 재검증) → stage(PUT 200×2) → publish(POST 1회) → 감시 대상 등록 → 원장 커밋.

주의: publish 응답이 **207 수용**이면 `post.status=scheduled`(Zernio 자동 재시도 예약) — 실패가 아니라 정상 수용이다. 최종 성공 판정은 platformPostUrl 관측으로만 한다(A-G5).

## 3. 성공 판정

1. publish POST 수용(201/207) + run green.
2. 30분 이내 watch 폴링에서 `platformPostUrl` 관측 → 원장 threads 승격.
3. 관측된 스레드 게시물 URL을 마스터에게 보고.

## 4. 중단·롤백

- pending/scheduled 중단: **DELETE** `/v1/posts/{postId}` 가 공식 취소(단, 전송이 시작된 entry는 나갈 수 있음 — Zernio 문서).
- published 이후 철회: **Unpublish**는 published/partial 전용.
- Meta `is_transient` 거절 재발: 시도 소진 시 원장 failed 확정 → 헬스+retry 워크플로우(`zernio_health_retry_ep88.yml`)의 ep87 전용 사본으로 retry(헬스 healthy일 때만).

## 5. 성공 후 무인화 개시

```bash
gh variable set RUNNER2_ENABLED --body "1"
```

- 이후 cron(짝수 시각 23분, KST)이 게이트 A-G0~G5를 통과한 회차만 자동 발행. 즉시 중단: `gh variable set RUNNER2_ENABLED --body "0"`.
- 무인화 개시 후 첫 3회	run은 watch 로그로 감시 권장.
