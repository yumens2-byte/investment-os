# gen/nuna_weekly — Iron Nuna 주간 미국시장 뉴스 쇼츠 (v1.0.0)

매주 목요일, 월~수 미국장 요약 영상을 만들어 Instagram Reels · Threads · YouTube Shorts · Facebook Reel 에
Zernio 예약 발행한다. hero_shorts 대체 파이프라인(설계서 12장 이행 계획).

- 설계서: Notion「NUNA 주간 뉴스 쇼츠 — 상세설계서 (2026-10-19)」(Investment OS 허브 하위)
- 원장: Supabase `nuna_*` 4테이블 (적용 완료, `sql/` 참조)

## 실행

```bash
# 생성 (워크플로 A, 목 06:47 KST)
python -m gen.nuna_weekly gen [--step run|preflight|collect|script|tts|images|clips|assemble|qc|stage|notify] [--date YYYY-MM-DD]
# 발행 (워크플로 B, 목 08:12 KST → scheduledFor = max(08:30, 알림+62분))
python -m gen.nuna_weekly publish [--step publish|verify] [--date YYYY-MM-DD]
# Actions Secret으로 DB 접근만 점검 (GET 0행, 유료 호출·원장 쓰기·발행 없음)
python -m gen.nuna_weekly publish --step dbcheck
# 전수테스트 (0원·무네트워크)
python -m pytest gen/nuna_weekly/tests -q && ruff check gen/nuna_weekly
```

재개: 실패하면 상태는 마지막 성공 단계에 머문다(last_error 기록). 비유료 단계는 같은 명령으로 재개한다.
실유료 단계의 오류·중단은 `PAID_RECOVERY_REQUIRED`로 자동 재호출을 차단하며, 공급자 요청·비용 대사 후 복구한다.

## 생성 → 발행 연결과 장애 복구

- 두 워크플로는 독립 cron 이다. 생성 성공이 발행 워크플로를 자동 호출하지 않는다.
- 연결 키는 같은 ISO 주차의 `episode_id`이며, 실운영 DB `nuna_episodes`의 `NOTIFIED` 상태와
  `script_json`, `media_json`, `notified_at`를 발행 워크플로가 읽는다.
- 생성 `dry_run=true`는 원장을 메모리에만 쓴다. 별도 발행 실행으로 결과가 전달되지 않는다.
  무과금 전체 연결 점검은 `tests/test_pipeline.py`의 공유 MemoryStore 통합 테스트로 수행한다.
- 월~수 미국장이 모두 끝나기 전 수집은 `MARKET_NOT_READY`로 중단한다. `--date`는 회차 선택이며
  실제 시각을 변경하지 않는다. 조기 실행에서 현재 주 미래 종가를 채우지 않는다.
- `preflight`는 DB·생성 백엔드를 초기화하지 않고 달력과 장 종료 여부만 확인한다.
- `dbcheck`는 실제 SUPABASE_KEY로 7개 테이블을 0행 조회한다. 날짜·회차가 없어도 실행 가능하며
  성공은 조회 권한만 확인한다. INSERT/UPDATE·비용 RPC 권한은 검사하지 않는다.
- DB 키는 두 YAML과 db.py 모두 `SUPABASE_KEY`를 사용한다. 기존 JWT 키는 apikey+Bearer,
  신형 sb_secret_/sb_publishable_ 키는 apikey 헤더만 사용한다. 공개용 키를 서버 권한 키로 승격하지 않는다.
- 발행은 생성 API 키를 요구하지 않는다. 기존 `SUPABASE_KEY`와 발행·알림 키는 필요하다.
- `SCHEDULED/PARTIAL/FAILED` 회차도 생성 알림 이력이 있으면 `step=publish`로 재시도할 수 있다.
  이미 scheduled/published인 채널은 POST하지 않는다. 결과가 확정된 4xx 실패·미전송 차단 건만 재시도한다.
  post ID가 있는 건, 전송 후 pending, 5xx/408/409/429는 `RECONCILE_REQUIRED`로 신규 POST를 차단한다.
  post ID가 있으면 `step=verify`로 조회·대사한다. ID도 없으면 Zernio 원본 요청·게시 목록부터 확인한다.
  과거 예약 시각과 API 멱등 보존 기간은 재시도 전 확인한다. 오래된 주차를 자동으로 현재 예약으로 바꾸지 않는다.
- 새 러너에서 `ASSEMBLED/QC_PASSED`의 로컬 영상이 사라졌다면 `step=run`으로 재조립·QC 후 진행한다.
  원격 이미지·클립은 재사용하며, 원격 백업이 없는 음성은 기존 정책에 따라 예산 범위 내 재생성한다.
- 생성 원장이 없거나 생성 미완료이면 발행 실행은 exit 1로 표시한다. 정상 발행으로 오인하지 않는다.
- 보류창은 알림부터 60분이며 발행 실행에서 30초 이하 간격으로 확인한다. 창 종료 후 API 호출 여유 2분을 둔다.
  따라서 예약은 `max(목 08:30, 알림+62분)`이다. 발행 job timeout은 90분이다.
  예약 POST 이후의 보류는 자동 취소를 뜻하지 않으며, Zernio 예약을 별도 확인해야 한다.
- 실유료 단계 호출 전에 `PAID_STEP_IN_PROGRESS:<step>`을 남긴다. 실패·강제 중단 후 새 러너에서도
  자동 유료 재제출을 차단한다. 로컬 pending만으로 무중복·무재과금 자동 복구를 보장하지 않는다.

상세 분석·테스트·배포/롤백 절차: `docs/nuna_weekly_predeployment_20261024.md`.

운영 실행 전 `vars.NUNA_ENABLED=1`, `SUPABASE_KEY` 및 README의 Secrets 설정을 확인한다.
활성화 스위치와 키는 코드 수정으로 우회하지 않는다. 수동 실생성은 완료된 주차의 목요일을 `date`에 넣고
`step=run`, `dry_run=false`로 실행한다. `NOTIFIED`와 QC·스테이징을 확인한 뒤 동일 `date`로 발행한다.

## 운영 스위치 (GitHub vars / secrets)

| 이름 | 기본 | 의미 |
| --- | --- | --- |
| secret `DRY_RUN` | true | 기존 규약. `false` 일 때만 유료 호출·발행. A시스템과 공유 |
| vars `NUNA_ENABLED` | 0 | `1` 일 때만 schedule 실행 |
| vars `NUNA_DURATION_SEC` | 60 | C1 — 60 또는 70 |
| vars `NUNA_FB_CUT` | 0 | C1 ②안 — 70초일 때 Facebook 60초 편집본 |
| vars `NUNA_MARKET_SOURCE` | direct | D1 — direct(Yahoo 직접) / supabase(daily_snapshots) |
| vars `NUNA_TTS_VOICE` | Kore | PoC P3 에서 확정 |
| vars `NUNA_REF_IMAGE_URLS` | — | Iron Nuna 기준 이미지 https URL(콤마). 없으면 `assets/nuna_ref_front.png` |
| secret `TELEGRAM_PAID_CHANNEL_ID` | — | 알림 대상. 해당 채널의 실제 관리자만 보류 가능. 개인 대화는 상대 사용자만 허용 |

Secrets: `FAL_AI_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_SUB_PAY_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`,
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
