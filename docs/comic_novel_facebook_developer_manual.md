# Comic Novel Facebook 개발자 매뉴얼 v1.0

기준일: 2026-10-12 KST. 코드와 운영 반영 승인에 따른 설정을 기준으로 작성했다.

## 1. 파일 및 책임

| 파일 | 책임 |
|---|---|
| `.github/workflows/comic_novel_fb.yml` | 금요일 실행, secrets 전달, 결과 artifact |
| `weekend/comic_novel_fb.py` | 대상 선택·X 증거·본문·주차 게이트·게시·복구 CLI |
| `db/comic_novel_channel_store.py` | 읽기와 제한 RPC 호출 계약 |
| `db/comic_novel_gateway.py` | GitHub 단기 OIDC 토큰 발급·단일 요청 transport |
| `db/comic_novel_fb_schema.sql` | 신규 컬럼·이력·권한·6개 RPC |
| `db/comic_novel_legacy_x_status_backfill.sql` | 사용자 확인 기존 23행의 1회성 원문 해시 스냅샷 |
| `weekend/comic_novel.py` | X 전체 성공 확인 및 증거 저장 |
| `db/daily_store.py` | INSERT 원본 저장, 조건부 X 선점 |
| `scripts/verify_comic_novel_deployment.py` | 운영 연결·Page ID·preview 무쓰기 점검 |
| `tests/test_comic_novel_fb.py`, `tests/test_comic_novel_channel_store.py`, `tests/test_comic_novel_x_confirmation.py` | 핵심 소비·계약·원본 성공 회귀 |
| `tests/sql/comic_novel_fb.integration.mjs` | 격리 SQL 재현 |

## 2. 실행 환경 및 설정

Actions는 Python 3.11, `pip install -r requirements.txt`를 사용한다. 기존 secrets `SUPABASE_URL`, `SUPABASE_KEY`, `FACE_PAGE_ID`, `FACE_PAGE_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_FREE_CHANNEL_ID`를 사용한다. 키 값은 문서·명령·로그에 출력하지 않는다. SUPABASE_KEY는 기존 anon 값을 그대로 유지한다. 제한 작업은 NOVEL_USE_GITHUB_OIDC=true에서 전용 Edge gateway를 사용한다. 로그인이나 신규 GitHub secret, 키 교체를 요구하지 않는다.

- `FACE_NOVEL_ENABLED`: 운영 연결 확인 보류 중 workflow 기본 false. repository variable false로 중지한다. 로컬 모듈 기본 false.
- `NOVEL_USE_GITHUB_OIDC`: Actions에서는 true. GitHub OIDC 토큰으로 제한 gateway를 호출한다.
- `FACE_NOVEL_FIRST_DATE`: 2026-10-16.
- `FACE_NOVEL_MAX_CHARS`: 10000. Meta 최대 길이라고 주장하는 값이 아닌 운영 상한.
- `DRY_RUN`: 모듈 기본 true. stage preview는 항상 true. 실발행은 publish와 false 모두 필요하다.
- `FACE_PAGE_ID`: 선점 target_key이며 실제 publisher 설정과 같아야 한다.
- `FACE_GRAPH_BASE`: 기존 config.settings의 Graph 기본 경로를 사용한다.

## 3. DB/RPC 계약

모든 함수는 SECURITY INVOKER이며 public/anon/authenticated 실행 권한을 제한하고 service_role에 허용한다. public 스키마를 사용한다. 같은 프로젝트의 icg 동명 테이블을 변경하지 않는다.

| RPC | 인자 | 성공 계약 |
|---|---|---|
| `novel_fb_claim` | p_source_date date, p_target text, p_slot date, p_run_id text, p_content_hash text, p_source_hash text | id와 status=claimed. 슬롯은 월요일 날짜 |
| `novel_fb_mark_sending` | p_claim_id bigint, p_content_hash text | status=sending. 원본/X 증거/선두/주차 재검증 |
| `novel_fb_finalize` | p_claim_id bigint, p_post_id text, p_confirmed_at timestamptz, p_actor text 기본 빈값, p_note text 기본 빈값 | status=ok. 날짜와 이력 동시 갱신 |
| `novel_fb_mark_result` | p_claim_id bigint, p_status text, p_error_type text | sending에서 failed 또는 unknown |
| `novel_fb_release` | p_claim_id bigint, p_actor text, p_note text | 미게시 증거가 있는 claimed/sending/unknown을 failed로 변경 |
| `novel_x_confirm` | p_source_date date, p_post_ids jsonb, p_expected_count int, p_source_hash text, p_cover_uri text 기본 NULL | 전체 X 실제 ID 검증 후 이력 ok·원본 published |

source hash는 UTF-8 `title + '\n' + episode_range + '\n' + novel_text`의 SHA-256이다. content_hash는 Facebook 완성 본문의 SHA-256이다. X ID는 양수 숫자 문자열, Facebook ID는 양수 숫자 또는 `PAGE_ID_POST_ID` 형식이다. 숫자 형식 확인만으로 외부 게시 존재가 증명되는 것은 아니므로 수동 보정 전 실제 Page/X와 대조한다.

## 4. preview 및 발행

다음 명령은 실행 형식 예시다. 운영 OIDC 경로는 허용된 GitHub Actions 런타임의 id-token: write 권한과 ACTIONS_ID_TOKEN_REQUEST_TOKEN/URL이 필요하다. 로컬 쉘에서 기존 anon 키만으로 제한 읽기·쓰기를 실행할 수 없다. preview는 새 컬럼·이력 존재와 gateway 읽기 권한이 필요하다.

```bash
DRY_RUN=true python -m weekend.comic_novel_fb --stage preview --output output/comic-novel-fb-preview.json
```

HOLD_X_UNCONFIRMED이면 가장 오래된 행의 X 증거가 없거나 불완전/해시 불일치다. 해당 원본을 그대로 보류하며 최신 행을 선택하지 않는다. 사용자가 현재 적재된 기존 23행의 X 전체 발행 완료를 확인하여 2026-10-12까지 published 원본의 1회성 legacy 해시 스냅샷을 허용했다. 이 스냅샷이 적용되고 원문 해시가 같으면 2026-04-05 선두 원본부터 소비할 수 있다. 신규 원본은 전체 실제 X ID 검증을 유지한다.

실발행 명령은 외부 게시를 발생시킨다. KST 금요일 및 최초일 조건을 통과한 경우만 진행한다.

```bash
FACE_NOVEL_ENABLED=true DRY_RUN=false python -m weekend.comic_novel_fb --stage publish --output output/comic-novel-fb-report.json
```

Actions 수동 실행은 stage=publish, dry_run=false를 함께 선택한다. 진단 목적으로 최초일 또는 시스템 시간을 변경해 강제 게시하지 않는다. 같은 주 성공 시 SKIPPED_WEEK_DONE이다.

## 5. 장애 및 운영자 복구

HOLD_DB_COMMIT이면 실제 게시 ID가 결과에 있을 수 있다. HOLD_UNKNOWN/HOLD_DB_RESULT 또는 runner 종료라면 Page와 로그를 대조한다. sending/unknown이 유지되면 다음 주도 자동 게시하지 않는다. 캐시 삭제나 이력 삭제로 복구하지 않는다.

게시된 사실과 실제 시각을 확인한 경우 다음 명령으로 날짜·이력만 보정한다. 시간은 timezone offset이 필수이며 미래 시각은 거절한다. 아래 시각은 명령 형식 예시이며 그 시각에 실제 게시했다고 확인한 기록이 아니다.

```bash
python -m weekend.comic_novel_fb --stage reconcile --claim-id CLAIM_ID --post-id PAGE_POST_ID --confirmed-at '2026-10-16T22:18:00+09:00' --actor OPERATOR --note '실제 Page 원문·ID·게시시각 확인 근거' --output output/comic-novel-fb-reconcile.json
```

미게시임을 실제 확인했을 때만 해제한다. actor/note 필수이며 post-id/confirmed-at과 같이 사용할 수 없다. 이 명령은 Graph 게시를 호출하지 않는다.

```bash
python -m weekend.comic_novel_fb --stage reconcile --not-posted --claim-id CLAIM_ID --actor OPERATOR --note 'Page 및 실행 로그에서 미게시 확인 근거' --output output/comic-novel-fb-release.json
```

원본 X는 dry-run 저장 금지, INSERT-only 캐시, generated 상태 조건부 선점으로 중복 실행을 막는다. X 전체 ID·건수 검증 뒤 novel_x_confirm을 호출한다. X 성공 이후 증거 저장 실패는 HOLD_DB_CONFIRM으로 자동 재게시하지 않는다. 기존 23행은 사용자의 전체 X 발행 완료 확인에 따라 `comic_novel_legacy_x_confirmations(source_date PK/FK, source_content_hash, confirmed_by, note, confirmed_at)`에 1회성 스냅샷을 기록한다. get_x_evidence는 실제 X 이력을 우선하고 없을 때 legacy 스냅샷을 조회하여 verification_basis=legacy_published_status를 반환한다. 검증은 published 상태·날짜·해시·확인자·시각을 요구한다. SQL claim/sending도 두 경로를 동일한 해시로 검증한다. 이 경로에는 가짜 X ID나 건수를 기록하지 않는다. 테이블은 RLS와 service_role SELECT 제한을 사용한다. 이후 신규 원본의 status만으로 완료를 인정하지 않는다.

## 6. 배포 후 무쓰기 점검

```bash
python -m scripts.verify_comic_novel_deployment
```

이 스크립트는 예상 Supabase 프로젝트 `ccomoimhhttaklfadaos` 호스트, 이력 읽기, Graph GET `/me` ID와 FACE_PAGE_ID 일치, preview 결과를 검사한다. VERIFIED는 연결 및 대상 일치 확인이다. 게시 권한 또는 실발행 성공 의미가 아니다. 결과에 database_writes=0/public_posts=0을 기록한다. HOLD_X_UNCONFIRMED는 연결 점검에서 허용하나 연재 준비 완료는 아니다. 사용자 확인 legacy 스냅샷 반영 후에는 선두 preview의 실제 결과를 다시 확인한다.

운영 스키마 적용 확인: PostgreSQL 17.6, 23개 원본 유지, publish_fb_data 모두 NULL, 이력 0행, RLS 활성, service_role RPC 실행 허용/anon 차단. 코드 merge·Actions 점검 결과는 실제 완료 후 운영 보고에 기록한다.

## 7. 검증 재현 및 제한

legacy 보완 후 Python 3.11.17 관련 회귀 196건, 격리 SQL 26건 통과를 확인했다. 실외부 발행 테스트와 별도 네트워크 세션 동시성 부하 완료를 의미하지 않는다.

```bash
pytest -q tests/test_comic_novel_fb.py tests/test_facebook_publisher.py tests/test_comic_novel_channel_store.py tests/test_weekend_comic_novel.py tests/test_comic_novel_x_confirmation.py tests/test_character_vote.py tests/test_daily_store_contract.py tests/test_workflow_operations.py tests/test_publish_channel.py tests/test_morning_watchdog.py tests/test_watchdog_workflow.py
npm install --prefix /tmp/comic-novel-pglite @electric-sql/pglite@0.5.8
PGLITE_MODULE=/tmp/comic-novel-pglite/node_modules/@electric-sql/pglite/dist/index.js node tests/sql/comic_novel_fb.integration.mjs
```

기존 공용 X publisher 내부의 불명확 예외 자체 재시도·텍스트 폴백은 잔여 위험이다. 신규 Facebook consumer는 전송 쓰기를 재시도하지 않는다. 표지 자동 영속 업로드는 구현하지 않았고 storage:// 경로와 해시가 모두 확인된 표지만 재사용한다.

## 운영 연결 확인 결과 — 2026-10-12

운영 DB 마이그레이션 및 기존 23행 legacy 확인은 완료했다. 최초 직접 연결 점검의 42501은 기존 SUPABASE_KEY가 anon이어서 발생했다. 사용자의 기존 키 유지·로그인 없는 처리 지시에 따라 SUPABASE_KEY를 변경하지 않고 GitHub OIDC 인증 Edge gateway를 사용한다. 신규 GitHub secret을 추가하거나 anon DB 읽기·발행 권한을 열지 않는다. NOVEL_USE_GITHUB_OIDC=true일 때 제한 작업은 gateway를 통한다. 게이트웨이 배포 및 실제 GitHub OIDC 무쓰기 smoke 결과를 확인할 때까지 Facebook 활성 기본값은 false다. Facebook 실제 게시와 날짜 갱신은 아직 0건이다.

## 9. OIDC gateway 인증·권한

comic-novel-gateway는 GitHub 공개키로 RSA 서명, issuer, audience comic-novel:ccomoimhhttaklfadaos, 정확한 yumens2-byte/investment-os 저장소와 고정 repository/owner ID, refs/heads/main, 허용된 workflow 식별자를 검증한다. Sunday comic_novel workflow는 novel_x_confirm 쓰기만, Friday comic_novel_fb workflow는 Facebook 5개 RPC 쓰기만, CI push main은 읽기만 허용한다. Edge 함수의 verify_jwt=false는 GitHub OIDC 직접 검증을 사용하기 위한 설정이다. 서버 기본 service_role 키는 함수 내부에만 있으며 GitHub·클라이언트에 반환하지 않는다.

reconcile CLI는 허용된 Friday Actions 런타임에서 증거 인자를 제공하거나 이미 승인된 서버 운영 환경에서만 수행한다. workflow_dispatch는 stage=reconcile과 claim_id, post_id, confirmed_at, note, not_posted 입력을 제공하며 actor는 github.actor를 사용한다. 실제 게시/미게시 증거를 운영자가 확인한 뒤 실행하며 Graph POST를 하지 않는다. 로컬 사용을 위해 로그인하거나 SUPABASE_KEY를 교체하지 않는다. cover loader는 OIDC store에 직접 storage client가 없으면 텍스트 발행으로 폴백한다. 실제 OIDC smoke를 통과하기 전 workflow 활성 기본값 false를 유지한다.

최신 개발 검증 결과: Python 3.11 관련 회귀 230건, 격리 SQL 26건 통과. Edge 인증 테스트와 실제 GitHub OIDC smoke 결과는 아직 완료 확인 전이며 후속 운영 보고에서 확정한다.
