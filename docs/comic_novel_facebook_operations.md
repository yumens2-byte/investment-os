# Comic Novel Facebook 운영 가이드

기준일: 2026-10-12 KST. 사용자가 운영 반영을 승인했다. 운영 DB의 기본 스키마 및 기존 원본 확인 마이그레이션은 완료했으며, GitHub OIDC gateway v1 ACTIVE 및 main 무쓰기 smoke VERIFIED를 확인하고 Facebook workflow 활성 기본값을 true로 반영한다. 저장소 변수 FACE_NOVEL_ENABLED=false로 중지할 수 있다. 실제 Facebook 게시와 publish_fb_data 갱신은 아직 0건이다.

## 1. 현재 운영 데이터

운영 프로젝트 ccomoimhhttaklfadaos, public 스키마, PostgreSQL 17.6에서 comic_novels 기존 23행을 유지했다. publish_fb_data DATE 컬럼은 적용되어 모두 NULL이다. 채널 이력은 0행이다. 사용자가 기존 23행의 X 전체 발행을 확인하여 comic_novel_legacy_x_confirmations에 날짜·원문 해시·확인자·시각을 기록했고 현재 원문 23행과 해시 일치를 확인했다. 가짜 X ID를 만들지 않았다. 이후 신규 원본은 전체 실제 X ID·건수·해시 검증을 유지한다.

최초 소비 원본은 2026-04-05 EP.01~EP.04다. 미발행 중 publish_date ASC 순서로 한 행만 선택하며 가장 오래된 원본이 미확인이면 후속 원본을 추월하지 않는다. 빈 큐는 SKIPPED_NO_DATA로 정상 종료한다.

## 2. 인증과 활성화

기존 SUPABASE_KEY는 anon 값을 그대로 유지한다. 로그인·신규 GitHub secret·키 교체·anon DB 권한 확대를 요구하지 않는다. NOVEL_USE_GITHUB_OIDC=true에서 GitHub 단기 OIDC 토큰으로 comic-novel-gateway를 호출한다. DB의 제한 이력·RPC는 Edge 함수 내부 기본 service_role 권한으로 수행한다.

gateway는 GitHub RSA 서명, issuer, audience comic-novel:ccomoimhhttaklfadaos, 정확한 저장소·repository/owner ID·main ref·workflow를 검증한다. Sunday는 X 확인 쓰기, Friday는 Facebook 제한 RPC 쓰기, CI main push는 읽기만 허용한다. verify_jwt=false는 Supabase 기본 JWT 대신 이 전용 OIDC 검사를 사용하는 설정이다.

FACE_NOVEL_ENABLED workflow 기본값은 true다. gateway v1 ACTIVE 및 실제 main OIDC smoke에서 DB 읽기·Page ID·최초 원본 PREVIEW를 확인했다. repository variable FACE_NOVEL_ENABLED=false는 중지 설정이다. 사용자 운영 반영 승인은 이미 제공되어 있어 이 문서가 추가 승인을 요구하지 않는다.

## 3. 금요일 연재

workflow comic_novel_fb.yml은 UTC cron 17 13 * * 5, 금요일 22:17 KST다. 최초 정규일 2026-10-16이며 예약 지연이 가능하다. 활성화·최초일·금요일·주차 제한은 애플리케이션에서도 확인한다. 동일 KST 주차의 성공 발행은 최대 1건이다.

원본 제목·에피소드 범위·novel_text를 재사용하고 새 소설이나 표지를 생성하지 않는다. storage:// 표지 경로와 바이트 해시를 모두 확인한 경우에만 이미지를 재사용한다. OIDC 경로의 직접 storage client 부재 또는 표지 미확보 시 텍스트로 발행한다.

## 4. preview와 연결 점검

GitHub Actions 수동 실행에서 stage=preview, dry_run=true를 선택한다. preview는 제한 읽기만 수행하며 DB 발행 이력과 Graph 게시를 쓰지 않는다. 정상 원본이면 PREVIEW와 발행 본문을 반환한다. HOLD_X_UNCONFIRMED이면 legacy/실제 X 증거와 현재 원문 해시를 점검한다.

배포 점검 스크립트 실행 형식은 다음과 같다. 운영 OIDC 실행에는 허용된 main Actions 런타임과 id-token: write가 필요하다. 로컬 쉘의 기존 anon 키만으로 제한 읽기·쓰기를 실행할 수 없다.

```bash
NOVEL_USE_GITHUB_OIDC=true python scripts/verify_comic_novel_deployment.py
DRY_RUN=true NOVEL_USE_GITHUB_OIDC=true python -m weekend.comic_novel_fb --stage preview --output output/comic-novel-fb-preview.json
```

검증은 예상 DB 프로젝트, 이력 읽기, Graph GET /me의 Page ID 일치, preview를 확인한다. GET Page ID 확인은 실제 게시 권한 검증을 의미하지 않는다. VERIFIED도 실제 Facebook 게시 완료를 의미하지 않는다.

## 5. 실발행

활성화 후 Actions stage=publish, dry_run=false를 함께 선택한다. KST 금요일·최초일 조건을 통과한 경우에만 한 편을 발행한다. 아래 명령은 허용된 Actions 런타임의 실행 형식이다.

```bash
FACE_NOVEL_ENABLED=true DRY_RUN=false NOVEL_USE_GITHUB_OIDC=true python -m weekend.comic_novel_fb --stage publish --output output/comic-novel-fb-report.json
```

선점 후 sending을 영속 기록하고 게시를 한 번 요청한다. 실제 post ID 성공이면 이력 ok와 publish_fb_data를 하나의 트랜잭션으로 기록한다. 날짜는 확인된 게시 시각의 KST 날짜다. 같은 주 성공 후 재실행은 SKIPPED_WEEK_DONE이다. 시간 변경이나 최초일 변경으로 강제 게시하지 않는다.

## 6. 보류와 복구

claimed/sending/unknown은 시간 경과로 해제하지 않고 다음 주 발행도 차단한다. 게시 성공 후 DB 오류 또는 응답 불명확이면 Page·원문·게시 ID·시각·실행 로그를 대조한다. 이력 삭제나 날짜 NULL 복원으로 재발행하지 않는다.

실제 게시를 확인했다면 Actions stage=reconcile, dry_run=false에 claim_id, post_id, confirmed_at, note를 입력한다. confirmed_at에는 timezone offset이 있는 실제 과거 게시 시각을 입력한다. actor는 github.actor다. 미게시를 확인했다면 not_posted=true와 claim_id/note만 제공하며 post_id/confirmed_at을 섞지 않는다. 두 복구 모두 Graph 게시 요청 없이 DB 상태만 보정한다.

허용된 런타임의 CLI 실행 형식:

```bash
NOVEL_USE_GITHUB_OIDC=true python -m weekend.comic_novel_fb --stage reconcile --claim-id CLAIM_ID --post-id PAGE_POST_ID --confirmed-at ACTUAL_TIMESTAMP_WITH_OFFSET --actor OPERATOR --note '실제 Page 원문·ID·게시시각 확인 근거' --output output/comic-novel-fb-reconcile.json
NOVEL_USE_GITHUB_OIDC=true python -m weekend.comic_novel_fb --stage reconcile --not-posted --claim-id CLAIM_ID --actor OPERATOR --note 'Page 및 실행 로그에서 미게시 확인 근거' --output output/comic-novel-fb-release.json
```

X 성공 후 증거 저장 실패는 HOLD_DB_CONFIRM이며 자동 재게시하지 않는다. 기존 공용 X publisher 내부의 불명확 예외 재시도·텍스트 폴백 정책은 이번 변경에서 수정하지 않은 잔여 위험이다. 신규 Facebook/gateway 쓰기 요청은 자동 재시도하지 않는다.

## 7. 최신 검증과 잔여 확인

Python 3.11 관련 회귀 230건, 격리 SQL 검증 26건 통과. 운영 DB 스키마·원본 보존·legacy 해시를 확인했다. Edge RSA 인증 테스트 22건도 통과했다. SQL 격리 엔진 결과는 별도 네트워크 DB 세션 동시성 부하 테스트 완료를 의미하지 않는다.

gateway 운영 배포·main OIDC 무쓰기 smoke·최초 원본 PREVIEW를 확인하고 workflow 활성 기본값 true를 반영한다. Page 게시 권한과 첫 실제 게시 성공·발행일 갱신은 실제 금요일 실행에서 별도로 확인한다. 이 문서의 작성 시점에는 아직 게시하지 않았다.

## 운영 연결 확인 결과 — 2026-10-12

[PR #20](https://github.com/yumens2-byte/investment-os/pull/20) main 병합 커밋 60a50c5d, Edge gateway v1 ACTIVE. [main 무쓰기 smoke](https://github.com/yumens2-byte/investment-os/actions/runs/38178503564/job/114585877383)는 VERIFIED이며 기존 database_key_role=anon, 예상 DB 프로젝트, page_identity_verified=true, preview_status=PREVIEW, source_publish_date=2026-04-05, database_writes=0, public_posts=0을 확인했다. 검증은 Python 230건·Edge RSA 22건·격리 SQL 26건 통과다.

첫 정규 발행은 2026-10-16 금요일 22:17 KST다. 연결·Page ID·preview 확인은 실제 게시 권한 또는 실제 Facebook 게시 성공의 증명이 아니다. 실제 게시 및 publish_fb_data 기록은 아직 0건이며 첫 금요일 실행에서 확인한다.
