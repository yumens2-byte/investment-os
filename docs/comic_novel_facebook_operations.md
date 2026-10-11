# Comic Novel Facebook 개발·운영 가이드

기준일: 2026-10-12 KST. 운영 반영 전 검토용. 기존 원본 DB와 외부 게시를 변경하지 않은 상태에서 작성했다.

## 요구사항

- `public.comic_novels`의 X 전체 발행 확인 데이터를 소비한다.
- 금요일마다 Facebook 미발행 중 `publish_date ASC` 순서로 한 행만 발행한다.
- 성공 후 `publish_fb_data DATE`에 실제 게시 시점의 KST 날짜를 기록한다.
- 데이터가 없으면 정상 스킵한다. 새 소설·시장 분석을 생성하지 않는다.
- 2026-10-16부터 시작할 설정을 준비했다. 시각은 기존 운영 시·분을 유지한 22:17 KST 제안이다.

## 확인된 DB

읽기 조회에서 `public.comic_novels` 23행, 최소 날짜 2026-04-05(EP.01~EP.04), 최대 날짜 2026-10-12(EP.107~EP.108)를 확인했다. `publish_date`는 DATE NOT NULL, id는 integer PK, 날짜 유일 인덱스는 `uix_novel_date`이다. `publish_fb_data`는 조회 시점에 없다. 같은 프로젝트의 `icg` 스키마에도 동명 테이블이 있으므로 운영 원본 `public`을 명시한다.

## 운영 반영 선행 조건

1. `db/comic_novel_fb_schema.sql` 검토 후 운영 마이그레이션을 별도 승인·적용한다. 이 파일은 작성만으로 DB에 적용되지 않는다.
2. 기존 `SUPABASE_KEY`가 신규 제한 RPC를 실행할 서버 권한인지 확인한다. 공개·일반 사용자 권한을 넓히지 않는다.
3. 기존 과거 소설의 X 게시 완료 증거를 확인해 채널 이력에 등록한다. `published`만으로 확정하지 않는다.
4. `FACE_PAGE_ID`와 `FACE_PAGE_TOKEN`이 의도한 Page인지 확인한다.
5. 미리보기→대상 Page 검증 후 `FACE_NOVEL_ENABLED=true`로 활성화한다. 승인된 운영 배포에서는 workflow 기본값 true이며, 저장소 변수 false로 일시중지한다.

## 미리보기

```bash
DRY_RUN=true python -m weekend.comic_novel_fb --stage preview --output output/comic-novel-fb-preview.json
```

미리보기는 DB 읽기만 사용한다. schema 적용 전 실행하면 신규 컬럼/이력/RPC 부재로 실패할 수 있다. DB 장애를 빈 데이터로 바꾸지 않는다.

## 발행

```bash
FACE_NOVEL_ENABLED=true DRY_RUN=false python -m weekend.comic_novel_fb --stage publish --output output/comic-novel-fb-report.json
```

실발행은 KST 금요일·최초일·주차 제한을 적용한다. 정상 1건 후 종료하며 backlog를 한 번에 발행하지 않는다. 이 명령은 Page에 외부 게시하므로 운영 반영 승인 이전에는 실행하지 않는다.

## 게시 확인 복구

실제 Page에 원문이 게시되었는지 확인한 뒤 해당 ID와 실제 게시 시점을 제공한다.

```bash
python -m weekend.comic_novel_fb --stage reconcile --claim-id CLAIM_ID --post-id PAGE_POST_ID --confirmed-at '2026-10-16T22:18:00+09:00' --actor OPERATOR --note 'Page에서 원문·게시시각·ID 확인' --output output/comic-novel-fb-reconcile.json
```

실제로 미게시임을 확인한 경우에만 다음을 실행한다. 이 명령은 이력을 실패 상태로 바꾸며 Facebook 게시 요청은 하지 않는다. actor/note는 확인 증거를 기록하는 필수 입력이다.

```bash
python -m weekend.comic_novel_fb --stage reconcile --not-posted --claim-id CLAIM_ID --actor OPERATOR --note 'Page와 실행 로그에서 미게시 확인' --output output/comic-novel-fb-release.json
```

시간 경과·캐시 삭제·날짜 NULL만으로 재발행하지 않는다. Sending/unknown은 매주 후속 발행도 차단한다. 실제 게시 여부를 모르면 보류를 유지한다.

## 원본 X 경로 보완

- dry-run은 신규 소설·상태·채널 이력을 저장하지 않는다.
- 캐시 published는 재발행하지 않는다. 불확실 상태는 수동 확인이 필요하다.
- `status=generated`일 때만 조건부 UPDATE로 X 실행 선점한다.
- 실제 숫자 ID, 전체 건수, published_count, ID 중복을 검증한다.
- X 실발행 이후 DB 증거 저장 실패는 `HOLD_DB_CONFIRM`이다. 자동 재발행하지 않는다.
- X 확인 기능은 DB 배포 전 운영 코드에 반영하면 실패할 수 있으므로 DB·코드 배포 순서를 준수한다.

## 표지

기존 DB에 X 표지 영속 경로·바이트 해시가 없으므로 초기 구현은 텍스트 발행이 기본이다. storage:// 경로 및 해시가 모두 등록된 이미지에 한해 같은 바이트를 재사용한다. 신규 원본 X 경로에서 표지 업로드를 자동 구현한 상태는 아니다. 표지를 새로 생성하지 않는다.

## 검증 범위

새 소비 모듈·X 성공 판정·저장소 계약 및 기존 Facebook/MVP 관련 테스트를 수행한다. DB RPC는 격리 Postgres 호환 엔진에서 검증하며 운영 DB 마이그레이션·실제 Page 발행 테스트는 별도 단계다. 검증 결과는 PR에 실제 통과 건수와 환경을 기록한다.

## 제한 사항

검증 런타임은 Python 3.12이며 Actions 설정은 기존 Python 3.11을 유지했다. 배포 전 GitHub CI에서 3.11 실행을 확인해야 한다. 기존 공용 X publisher는 전송 후 불확실한 예외에도 자체 재시도·텍스트 폴백을 할 수 있다. 이번 변경은 원본 실행 경합을 막지만 이 공용 publisher 내부의 재시도 정책을 변경하지 않았다. 신규 Facebook 경로는 쓰기 재시도를 하지 않는다.

선점 함수는 consumer가 처음 읽은 원문 해시를 전달받아 DB의 현재 원문·X 증거와 비교한다. Sending 전에도 원문을 재확인한다. 이로써 본문 조회 이후 변경된 데이터를 과거 본문으로 게시하지 않는다.

## 개발 검증 결과 (2026-10-12)

- Python 신규·관련 회귀: 183 passed. 실제 외부 API와 운영 DB 쓰기는 mock 처리했다.
- SQL: PostgreSQL 18.3 / PGlite 0.5.8 격리 엔진에서 22 checks PASS. 운영 DB는 PostgreSQL 17이며 운영 적용은 하지 않았다.
- SQL 스크립트 재적용, DB 해시와 Node SHA-256 동일성, 확인 안 된 선두 데이터 추월 방지, 중복 선점 거절, 활성 주차 unique 제약, unknown 복구, 미래 확인시각 거절, 날짜 기록, 다음 주 처리, 미게시 해제, 원문 변경 차단, anon RPC 권한 차단을 확인했다.
- PGlite는 한 엔진으로 실행했으므로 별도 네트워크 DB 세션의 실제 동시성 스트레스 테스트를 완료했다고 주장하지 않는다.
- YAML 구조 및 기존 일요일 inline Python/shell 구문 확인 PASS.
- 운영 단계에서 필요: PostgreSQL 17 실제 마이그레이션 검증, GitHub Python 3.11 CI, 과거 X 발행 증빙, Page 권한·실발행 파일럿.

격리 SQL 테스트 재현:

```bash
npm install --prefix /tmp/comic-novel-pglite @electric-sql/pglite@0.5.8
PGLITE_MODULE=/tmp/comic-novel-pglite/node_modules/@electric-sql/pglite/dist/index.js node tests/sql/comic_novel_fb.integration.mjs
```

## 2026-10-12 운영 승인 및 기존 원본 확인

사용자가 현재 적재 23행은 전부 X 정상 발행본이며 published 상태로 확인 가능하다고 확인했다. 기존 원문 해시 스냅샷 23행을 legacy 확인 테이블에 등록하고 23행 해시 일치를 검증했다. 가짜 게시 ID는 생성하지 않았다. 신규 원본은 실제 X 전체 ID 검증을 유지한다. 최종 검증은 Python 3.11 196건, 격리 SQL 26건 통과다.
