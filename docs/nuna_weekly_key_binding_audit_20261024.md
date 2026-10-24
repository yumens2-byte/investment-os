# NUNA 파이프라인 키 참조 대조 및 DB 점검 보완 — 2026-10-24

기준 운영 SHA: aa96dbea781c14e6a02cec755c0b4706d494f7e8.
대상: nuna_weekly_gen.yml, nuna_weekly_publish.yml, gen/nuna_weekly 런타임 코드.

## 대조 결과

| 용도 | GitHub Secret → 실행 환경변수 → 코드 참조 | 결과 |
|---|---|---|
| DB URL | SUPABASE_URL | 일치 |
| DB 키 | SUPABASE_KEY | 양쪽 YAML과 db.py 일치 |
| 대본 | ANTHROPIC_API_KEY | 생성 YAML·ClaudeBackend 일치 |
| 음성 | GEMINI_API_SUB_PAY_KEY | 생성 YAML·GeminiTTSBackend 일치 |
| 이미지·클립 | FAL_AI_KEY | 생성 YAML·FalQueueBackend 일치 |
| Zernio main/sub | ZERNIO_API_KEY / ZERNIO_API_SUB_KEY | 양쪽 YAML·config.ZERNIO_KEY_ENV 일치 |
| Telegram | TELEGRAM_BOT_TOKEN / TELEGRAM_PAID_CHANNEL_ID | 양쪽 YAML·TelegramClient 일치 |
| Instagram·Threads | NUNA_INSTAGRAM_ACCOUNT_ID / NUNA_THREADS_ACCOUNT_ID | 발행 YAML·config.ACCOUNT_ENV 일치 |
| YouTube·Facebook | NUNA_YOUTUBE_ACCOUNT_ID / NUNA_FACEBOOK_ACCOUNT_ID | 발행 YAML·config.ACCOUNT_ENV 일치 |

생성 YAML에 4개 SNS account ID가 없는 것은 스테이징 단계에서 필요하지 않기 때문이다.
발행 YAML에 생성 API 키가 없는 것은 기존 최소 권한 설계다.
이름 불일치는 확인되지 않았다. GitHub Secret의 실제 값·권한을 읽어 확인한 결과가 아니다.

## 오류와 구분할 점

실행 40062165941의 42501 permission denied는 nuna_episodes의 GET 접근 거부다.
키 이름은 이미 SUPABASE_KEY로 맞아 있으므로 동일 이름으로 재치환해도 권한은 바뀌지 않는다.
키 값이 어느 프로젝트·권한에 해당하는지는 실제 Actions 실행의 DB 요청으로 확인해야 한다.

## 보완

1. DB 키 이름을 db.SUPABASE_KEY_ENV 상수로 통일했다. YAML은 secrets.SUPABASE_KEY를 주입한다.
2. 기존 JWT는 apikey와 Authorization Bearer로 전달하고, 신형 sb_secret_/sb_publishable_ 키는
   JWT가 아니므로 apikey에만 전달한다. 공개 키에 서버 권한을 부여하는 수정은 하지 않는다.
3. SQLSTATE 42501은 DB_PERMISSION_DENIED로 구분하고 키 원본 변수명·형식·대상 호스트를 표시한다.
   키 값·토큰·해시·JWT payload는 출력하지 않는다.
4. 기존 두 워크플로에 dbcheck 선택지를 추가했다. 별도 일회성 워크플로는 만들지 않았다.
5. dbcheck는 실제 주입 키로 7개 테이블에 GET select=* limit=0을 실행한다.
   데이터 행을 읽거나 쓰지 않고, episode·비용·SNS·Telegram·유료 API를 변경하지 않는다.
6. 결과의 write_permission은 not_tested다. 조회 성공으로 쓰기·비용 함수 실행 권한을 단정하지 않는다.

공식 키 형식 근거:
https://supabase.com/docs/guides/getting-started/migrating-to-new-api-keys

## 실행 가이드

GitHub Actions → nuna-weekly-publish → Run workflow:

| 항목 | 값 |
|---|---|
| Branch | main |
| step | dbcheck |
| date | 2026-10-22 (이 단계에서는 회차 생성 안 함) |
| dry_run | false |

성공: DB_CHECK_PASSED, key_source=SUPABASE_KEY, readable_tables 7개.
실패: DB_PERMISSION_DENIED 또는 기존 인증·접속 오류. 키 값·프로젝트·권한은 이 결과로 추가 대사한다.
이 단계는 실제 키를 사용하지만 유료 생성·실발행은 하지 않는다.

## 검증

환경변수 주입부터 실모드·DRY_RUN reader까지의 연결, 키 형식별 헤더,
0행 조회만 수행하는 계약, 권한 오류의 비밀값 비노출, CLI가 생성/발행 컨텍스트를 만들지 않는 경우를 시험한다.
실제 GitHub Secret 값의 권한은 모의 테스트 결과로 입증하지 않는다.

운영 반영 후 기존 CI의 NUNA configured DB key (read-only) job이 main push에서만 실행된다.
먼저 NUNA Python 3.11 회귀 job이 통과해야 실행하며, Supabase URL/키는 조회 step에만 주입한다.
PR에서는 이 실제 키 검사 job을 실행하지 않는다. Telegram·SNS·유료 생성은 호출하지 않는다.

로컬 검증: NUNA 전체 173 passed (82.24s), 기존 운영 가드레일 14 passed.
Ruff·actionlint 1.7.7(관련 YAML 3개)·git diff --check 통과. 실제 키의 권한 결과는 원격 job으로 구분한다.
