# gen/nuna_weekly — Iron Nuna 주간 미국시장 뉴스 쇼츠 (v1.0.0)

매주 목요일, 월~수 미국장 요약 영상을 만들어 Instagram Reels · Threads · YouTube Shorts · Facebook Reel 에
Zernio 예약 발행한다. hero_shorts 대체 파이프라인(설계서 12장 이행 계획).

- 설계서: Notion「NUNA 주간 뉴스 쇼츠 — 상세설계서 (2026-10-19)」(Investment OS 허브 하위)
- 원장: Supabase `nuna_*` 4테이블 (적용 완료, `sql/` 참조)

## 실행

```bash
# 생성 (워크플로 A, 목 06:47 KST)
python -m gen.nuna_weekly gen [--step run|preflight|collect|script|tts|images|clips|assemble|qc|stage|notify] [--date YYYY-MM-DD]
# 발행 (생성 워크플로의 publish job 이 NOTIFIED 직후 실행 · 수동 재시도는 워크플로 B dispatch,
#       scheduledFor = max(08:30, 알림+62분)) — DN2026_0079
python -m gen.nuna_weekly publish [--step publish|verify] [--date YYYY-MM-DD]
# Actions Secret으로 DB 접근만 점검 (GET 0행, 유료 호출·원장 쓰기·발행 없음)
python -m gen.nuna_weekly publish --step dbcheck
# 전수테스트 (0원·무네트워크)
python -m pytest gen/nuna_weekly/tests -q && ruff check gen/nuna_weekly
```

재개: 실패하면 상태는 마지막 성공 단계에 머문다(last_error 기록). 비유료 단계는 같은 명령으로 재개한다.
실유료 단계의 오류·중단은 `PAID_RECOVERY_REQUIRED`로 자동 재호출을 차단하며, 공급자 요청·비용 대사 후 복구한다.

## 생성 → 발행 연결과 장애 복구

- (DN2026_0079) 정기 발행은 생성 워크플로(`nuna_weekly_gen.yml`)의 `publish` job 이 맡는다. 생성 실행이
  이번 실행에서 live 로 `NOTIFIED`에 도달하면(`gen` 출력 `chain=publish-live`) 같은 실행 안에서 곧바로 발행한다.
  dry 실행·스킵 회차·이미 알림된 회차 재실행·생성 실패·`vars.NUNA_ENABLED != 1`이면 `publish` job 이 skipped 다.
  job output 은 secret 값을 포함하면 가려지므로 `true`/`false` 값은 output 으로 넘기지 않는다.
  연결 발행은 알림 직후 시작해 보류창 60분을 매번 기다리므로 job timeout 은 120분이다.
  출력 기록이 실패하면 `NOTIFIED` 회차가 남는다 — 로그 안내대로 예약 시각 전에 B 워크플로를 수동 실행한다.
  독립 cron 두 개는 서로 다른 시간만큼 지연될 수 있어(W44 실측 3시간 이상), 발행이 '알림+62분' 이후 시작되면
  예약 시각 경과로 차단되던 구조를 없앤 것이다(STD-SCHED-03).
- `nuna_weekly_publish.yml`은 schedule 이 없다. 수동 재시도(`step=publish`), 예약 후 검증(`step=verify`),
  권한 확인(`step=dbcheck`)에만 쓴다. 두 발행 경로의 env 키 집합은 같게 유지한다.
- 연결 키는 같은 ISO 주차의 `episode_id`이며, 실운영 DB `nuna_episodes`의 `NOTIFIED` 상태와
  `script_json`, `media_json`, `notified_at`를 발행 단계가 읽는다.
- 생성 `dry_run=true`는 원장을 메모리에만 쓴다. 별도 발행 실행으로 결과가 전달되지 않는다.
  무과금 전체 연결 점검은 `tests/test_pipeline.py`의 공유 MemoryStore 통합 테스트로 수행한다.
- 월~수 미국장이 모두 끝나기 전 수집은 `MARKET_NOT_READY`로 중단한다. `--date`는 회차 선택이며
  실제 시각을 변경하지 않는다. 조기 실행에서 현재 주 미래 종가를 채우지 않는다.
- `preflight`는 DB·생성 백엔드를 초기화하지 않고 달력과 장 종료 여부만 확인한다.
- `dbcheck`는 실제 SUPABASE_SERVICE_ROLE_KEY로 7개 테이블을 0행 조회한다. 날짜·회차가 없어도 실행 가능하며
  성공은 조회 권한만 확인한다. INSERT/UPDATE·비용 RPC 권한은 검사하지 않는다.
- DB 키는 두 YAML과 db.py 모두 `SUPABASE_SERVICE_ROLE_KEY`를 사용한다. 기존 JWT 키는 apikey+Bearer,
  신형 sb_secret_/sb_publishable_ 키는 apikey 헤더만 사용한다. 공개용 키를 서버 권한 키로 승격하지 않는다.
- 발행은 생성 API 키를 요구하지 않는다. 기존 `SUPABASE_SERVICE_ROLE_KEY`와 발행·알림 키는 필요하다.
- `SCHEDULED/PARTIAL/FAILED` 회차도 생성 알림 이력이 있으면 `step=publish`로 재시도할 수 있다.
  이미 scheduled/published인 채널은 POST하지 않는다. 결과가 확정된 4xx 실패·미전송 차단 건만 재시도한다.
  post ID가 있는 건, 전송 후 pending, 5xx/408/409/429는 `RECONCILE_REQUIRED`로 신규 POST를 차단한다.
  post ID가 있으면 `step=verify`로 조회·대사한다. ID도 없으면 Zernio 원본 요청·게시 목록부터 확인한다.
  과거 예약 시각과 API 멱등 보존 기간은 재시도 전 확인한다. 오래된 주차를 자동으로 현재 예약으로 바꾸지 않는다.
- 새 러너에서 `ASSEMBLED/QC_PASSED`의 로컬 영상이 사라졌다면 `step=run`으로 재조립·QC 후 진행한다.
  원격 이미지·클립은 재사용하며, 원격 백업이 없는 음성은 기존 정책에 따라 예산 범위 내 재생성한다.
- 생성 원장이 없거나 생성 미완료이면 발행 실행은 exit 1로 표시한다. 정상 발행으로 오인하지 않는다.
- 보류창은 알림부터 60분이며 발행 실행에서 30초 이하 간격으로 확인한다. 창 종료 후 API 호출 여유 2분을 둔다.
  따라서 예약은 `max(목 08:30, 알림+62분)`이다. 발행 job timeout은 120분이다(DN2026_0079, 보류창 60 + 검증 대기 최대 30 + verify 최대 10 + 설치).
  예약 POST 이후의 보류는 자동 취소를 뜻하지 않으며, Zernio 예약을 별도 확인해야 한다.
- 실유료 단계 호출 전에 `PAID_STEP_IN_PROGRESS:<step>`을 남긴다. 실패·강제 중단 후 새 러너에서도
  자동 유료 재제출을 차단한다. 로컬 pending만으로 무중복·무재과금 자동 복구를 보장하지 않는다.
- fal 이미지·클립은 제출 직후 작업 기록(request_id·status_url·response_url)을 `assets_json.fal_pending`에,
  request_id를 비용 원장에 남긴다. 대기 상한 초과는 `FAL_PENDING:<step>:`으로 기록되며, 이 경우에만
  재실행이 원장 기록으로 재제출·재과금 없이 재조회한다. 기록이 없거나 다른 오류면 기존 차단을 유지한다.
  수동 복구로 last_error 를 지울 때는 실패 작업의 `assets_json.fal_pending` 키도 함께 지운다.
  대기 상한 합계(이미지 수×이미지 상한 + 클립 수×클립 상한)는 70분 이하여야 하며 유료 단계 전에 검사한다.

상세 분석·테스트·배포/롤백 절차: `docs/nuna_weekly_predeployment_20261024.md`.

운영 실행 전 `vars.NUNA_ENABLED=1`, `SUPABASE_SERVICE_ROLE_KEY` 및 README의 Secrets 설정을 확인한다.
활성화 스위치와 키는 코드 수정으로 우회하지 않는다. 수동 실생성은 완료된 주차의 목요일을 `date`에 넣고
`step=run`, `dry_run=false`로 실행한다. 생성이 `NOTIFIED`에 도달하면 같은 실행의 `publish` job 이 발행한다
(DN2026_0079). 그 job 이 실패했을 때만 `nuna_weekly_publish.yml`을 동일 `date`로 수동 실행하며,
예약 시각(알림+62분과 발행일 08:30 중 늦은 쪽) 전에 시작해야 한다.

## 실패 대응 런북 (DN2026_0081)

발행 워크플로(B)는 이미 만들어진 영상을 예약 발행할 뿐이다. 생성이 `NOTIFIED`에 도달하지 못한 회차는
B 를 돌려도 「발행 보류 — 생성 미완료」로 끝난다. 생성 실패는 **gen 재실행**으로 복구하고, 재실행이
`NOTIFIED`에 도달하면 같은 실행의 `publish` job 이 자동으로 발행한다(DN2026_0079).

1. 실패 확인 — Watchdog 알림 또는 Actions 실패 표시를 받으면 원인 접두를 확인한다.
   단계 실패는 Telegram 리포트 `[NUNA] <회차> 생성 실패 — <접두>: …`, 실행 로그 `✖ <접두>: …`,
   또는 `nuna_episodes.last_error`에 남는다. 차단으로 멈춘 경우는 로그 `중단: PipelineError: …`에만 남는다.
2. 접두별 조치

| `last_error` 접두 | 뜻 | 조치 |
| --- | --- | --- |
| `FAL_PENDING:images:` · `FAL_PENDING:clips:` | fal 작업이 대기 상한 안에 안 끝남(작업 기록 보존) | gen 을 `dry_run=false`, 같은 `date`로 재실행 → 재제출·재과금 없이 같은 작업을 재조회해 이어간다 |
| `collect:` · `qc:` · `stage:` · `notify:` | 무료 단계 실패 | 원인(시세·네트워크·QC 게이트)을 확인한 뒤 같은 방법으로 재실행 → 실패한 단계부터 이어간다 |
| `assemble:` | 조립 실패. 새 러너는 음성 파일이 없어 **TTS 를 다시 합성(유료, 회당 약 ₩12)** 하며, 길이가 실행마다 달라 `TTSFitError`(목표×1.15 초과)가 날 수 있다(DN2026_0085, W44 베타 실측) | 같은 방법으로 재실행(2회 한도) → 클립·이미지는 재사용하고 TTS 만 다시 합성한다. 회차 비용 상한(2,000원) 여유를 확인한다 |
| `script:` · `tts:` · `images:` · `clips:` (접두에 `FAL_PENDING` 없음), `PAID_STEP_IN_PROGRESS:` | 유료 호출 결과가 불확정 | **재실행 금지**(자동 재과금 차단, `PAID_RECOVERY_REQUIRED`). 비용 원장·공급사 대사 후 원장 정리가 필요하다 — 개발조직에 요청 |
| (없음, 상태 `NOTIFIED`) | 생성은 끝났고 연결 발행만 실패·skipped | 예약 시각 전에 B 워크플로를 `step=publish`, 같은 `date`, `dry_run=false`로 수동 실행 |

3. 재실행 한도 — gen 재실행은 2회까지. 두 번째도 실패하면 더 돌리지 않고 개발조직에 원인 판정을 요청한다.
4. 시간 한도
   - 발행은 예약 시각(알림+62분과 발행일 08:30 중 늦은 쪽)이 지나면 「예약 시각 경과」로 영구 차단된다.
     재실행으로 새로 `NOTIFIED`가 되면 알림 시각이 새로 찍혀 예약도 그 뒤로 잡힌다.
   - 다음 주 회차와 겹치지 않게, 같은 주(ISO 주차) 안에서만 재실행한다.
5. 대기 상한 조정 — 클립·이미지가 반복해서 `FAL_PENDING`이면 `vars.NUNA_FAL_CLIP_MAX_WAIT_SEC`·
   `vars.NUNA_FAL_IMAGE_MAX_WAIT_SEC`(60~1200 정수)를 fal 실측 처리 시간 기준으로 올린다(코드 변경 없음).
   합계(이미지 수×이미지 상한 + 클립 수×클립 상한)는 70분 이하여야 하며, 넘으면 유료 단계 전에 중단된다.
   60초판(이미지 5·클립 2) 기준 클립 상한은 이미지 300초일 때 최대 1200초.

## 운영 스위치 (GitHub vars / secrets)

| 이름 | 기본 | 의미 |
| --- | --- | --- |
| secret `DRY_RUN` | true | 기존 규약. `false` 일 때만 유료 호출·발행. A시스템과 공유 |
| vars `NUNA_ENABLED` | 0 | `1` 일 때만 schedule 실행. (DN2026_0079) 생성 연결 `publish` job 도 `1` 일 때만 실행 — `1`이면 수동 live 생성(`dry_run=false`)도 NOTIFIED 직후 자동 예약 발행된다(보류창 60분 opt-out). `1`이 아니면 자동 발행 없음, 수동 발행은 B 워크플로 |
| vars `NUNA_DURATION_SEC` | 60 | C1 — 60 또는 70 |
| vars `NUNA_FB_CUT` | 0 | C1 ②안 — 70초일 때 Facebook 60초 편집본 |
| vars `NUNA_MARKET_SOURCE` | direct | D1 — direct(Yahoo 직접) / supabase(daily_snapshots) |
| vars `NUNA_TTS_VOICE` | Kore | PoC P3 에서 확정 |
| vars `NUNA_REF_IMAGE_URLS` | — | Iron Nuna 기준 이미지 https URL(콤마). 없으면 `assets/nuna_ref_front.png` |
| vars `NUNA_FAL_IMAGE_MAX_WAIT_SEC` | 300 | fal 이미지 1장 대기 상한(60~1200초) |
| vars `NUNA_FAL_CLIP_MAX_WAIT_SEC` | 900 | fal 클립 1개 대기 상한(60~1200초). 초과 시 작업 기록 보존 후 `FAL_PENDING:clips:` |
| secret `TELEGRAM_PAID_CHANNEL_ID` | — | 알림 대상. 해당 채널의 실제 관리자만 보류 가능. 개인 대화는 상대 사용자만 허용 |

Secrets: `FAL_AI_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_SUB_PAY_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`ZERNIO_API_KEY`(IG·Threads), `ZERNIO_API_SUB_KEY`(YT·FB), `NUNA_{INSTAGRAM,THREADS,YOUTUBE,FACEBOOK}_ACCOUNT_ID`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_PAID_CHANNEL_ID`.

## 미검증 사실 (PoC 에서 확인 — 설계서 11장)

1. Yahoo v8 chart `range=1mo` 응답의 `timestamp`·`meta.exchangeTimezoneName` 파싱 (합성 데이터로만 테스트)
2. fal `nano-banana/edit` 의 data URI 기준 이미지 수용 · 응답 `images[0].url`
3. Kling 2.6 i2v 9:16 출력, `generate_audio`/`negative_prompt` 파라미터, 응답 `video.url`
4. Gemini TTS `gemini-3.8-flash-tts` 실응답 형식(RIFF 여부)·usage 필드·한국어 낭독 속도
5. Zernio 예약 게시 상태값, 409 `idempotency_conflict` 위치, `/accounts/health` 응답 형식,
   Instagram `shareToFeed`/`thumbOffset`, Facebook `contentType: "reel"`
6. `daily_news.top_headlines` 형식(JSON 문자열 배열)
