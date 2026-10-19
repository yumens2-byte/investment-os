# gen/nuna_weekly — Iron Nuna 주간 미국시장 뉴스 쇼츠 (v1.0.0)

매주 목요일, 월~수 미국장 요약 영상을 만들어 Instagram Reels · Threads · YouTube Shorts · Facebook Reel 에
Zernio 예약 발행한다. hero_shorts 대체 파이프라인(설계서 12장 이행 계획).

- 설계서: Notion「NUNA 주간 뉴스 쇼츠 — 상세설계서 (2026-10-19)」(Investment OS 허브 하위)
- 원장: Supabase `nuna_*` 4테이블 (적용 완료, `sql/` 참조)

## 실행

```bash
# 생성 (워크플로 A, 목 06:47 KST)
python -m gen.nuna_weekly gen [--step run|preflight|collect|script|tts|images|clips|assemble|qc|stage|notify] [--date YYYY-MM-DD]
# 발행 (워크플로 B, 목 08:12 KST → scheduledFor = max(08:30, 알림+60분))
python -m gen.nuna_weekly publish [--step publish|verify] [--date YYYY-MM-DD]
# 전수테스트 (0원·무네트워크)
python -m pytest gen/nuna_weekly/tests -q && ruff check gen/nuna_weekly
```

재개: 실패하면 상태는 마지막 성공 단계에 머문다(last_error 기록). 같은 명령 재실행 시 다음 단계부터 진행.

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
| secret `NUNA_TELEGRAM_ADMIN_IDS` | — | S-04 — 보류 권한 Telegram user id(콤마). 없으면 보류 버튼 비활성 |

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
