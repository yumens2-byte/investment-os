# Comic Novel Facebook 프로세스 설계서 v1.0

기준일: 2026-10-12 KST. 저장소: yumens2-byte/investment-os. 실제 코드 기준 문서이며 배포 결과는 아래 확인 범위와 후속 운영 보고를 구분한다.

## 1. 업무 흐름

기존 일요일 `comic_novel.yml`은 소설 원본을 생성·저장하고 X 전체 발행의 실제 ID·건수·원문 해시를 확인한다. Facebook 금요일 실행은 해당 원본을 소비한다. 소설 재생성, X/TG 콘텐츠 재발행, MVP 투표를 실행하지 않는다. Telegram은 상태 알림에만 사용한다.

1. KST 현재일과 월요일 기준 주차를 계산한다.
2. 실발행은 활성화, 최초일 2026-10-16, 금요일 조건을 순서대로 확인한다.
3. 대상 Page의 미해결 claimed/sending/unknown 이력이 있으면 보류한다. 같은 주 확정 발행이 있으면 스킵한다.
4. `public.comic_novels`에서 `publish_fb_data IS NULL`, `status='published'`, `publish_date <= today_kst`인 가장 오래된 행을 `publish_date ASC, id ASC LIMIT 1`로 읽는다. 없으면 정상 스킵한다.
5. 그 행의 X 전체 발행 증거를 확인한다. 신규 원본은 실제 양수 숫자 ID 목록, 중복 없음, 예상·확인 건수 일치, 원문 SHA-256 일치를 요구한다. 기존 적재분은 사용자가 전체 X 실발행 완료를 확인한 23행에 한해 날짜·원문 해시·확인자·확인 시각을 기록한 1회성 legacy 확인을 허용한다. 미확인 선두를 건너뛰지 않는다.
6. 저장 제목·에피소드 범위·본문을 Facebook 형식으로 구성하고 10,000자 운영 상한을 확인한다. 본문을 절단하지 않는다.
7. preview는 구성 결과만 반환하며 DB 및 Graph 쓰기 없이 종료한다.
8. publish는 DB 원자적 선점 후 표지를 확보하고 sending을 영속 기록한다. 같은 원문·주차·대상에 경합하거나 원문이 변경되면 전송하지 않는다.
9. Facebook 게시 요청을 한 번 수행한다. 실제 post ID 성공이면 이력 ok와 원본 발행일을 하나의 DB 트랜잭션으로 기록한다. 불명확 응답은 unknown으로 유지한다.
10. 결과 JSON을 artifact로 남기고 실발행 성공 또는 보류·실패 상태를 Telegram으로 통지한다. 알림 실패가 게시를 재실행시키지 않는다.

## 2. 상태 전이

```mermaid
stateDiagram-v2
    [*] --> claimed: 원자적 선점
    claimed --> sending: 원문 재검증·전송 전 기록
    sending --> ok: 실제 게시 ID·DB 확정
    sending --> failed: 확정 실패
    sending --> unknown: 응답 불명확
    unknown --> ok: 운영자 게시 증거 확인
    claimed --> failed: 운영자 미게시 확인
    sending --> failed: 운영자 미게시 확인
    unknown --> failed: 운영자 미게시 확인
    failed --> claimed: 적법한 후속 실행
```

sending 상태에서 프로세스가 종료되거나 게시 성공 후 DB 갱신이 실패하면 영속 sending이 남는다. 오래되었다는 이유로 해제하지 않는다. claimed도 자동 만료하지 않는다. unknown은 다음 주 실행도 차단한다. failed는 확정 미게시/실패이며 후속 금요일 실행에서 재선점할 수 있다.

## 3. 스케줄 및 종료 정책

- `.github/workflows/comic_novel_fb.yml`: UTC cron `17 13 * * 5`, 금요일 22:17 KST. 예약 실행에 0~199초 추가 지연이 있다. GitHub 자체 지연도 가능하다.
- 최초 정규일: 2026-10-16. 금요일 조건은 애플리케이션에서도 검사한다.
- workflow 동시 실행 그룹: `comic-novel-facebook`, 실행 중 취소 false, timeout 15분.
- 운영 반영 승인 이후 연결 점검에서 anon 키 권한 오류가 확인되어 workflow 활성 기본값은 false로 보류했다. repository variable `FACE_NOVEL_ENABLED=false`는 명시적인 중지 설정이다. CLI 모듈 자체는 환경변수 없을 때 false다.
- 수동 Actions는 preview/publish와 dry_run을 제공한다. 기본 선택은 preview, dry_run=true다. reconcile은 운영자 CLI로 수행한다.
- exit 0: PREVIEW/PUBLISHED 및 정상 스킵. exit 1: 설정·DB·확정 게시 실패. exit 2: 증거 부족·불확실·내용 보류. Actions에서 보류도 실패 실행으로 드러난다.

## 4. 저장 및 원자성

`public.comic_novels.publish_fb_data DATE NULL`은 실제 확인된 게시 시각을 Asia/Seoul 날짜로 변환해 기록한다. 예약일이나 원본 publish_date를 복사하지 않는다. 보조 `comic_novel_channel_history`는 source_date, channel, target_key, slot_week_start, status, 원문/게시본문 해시, 실제 ID, 건수, run_id, 시각, actor/note를 보관한다.

대상별 advisory transaction lock, 원본·이력 row lock, 활성 주차 unique 제약과 원본·채널·대상 unique 제약을 사용한다. 원본 조회 뒤 변경을 막기 위해 선점 시 원문 해시를 전달하며 sending 전 재검증한다. DB 오류를 빈 큐로 바꾸지 않는다.

## 5. 현재 운영 데이터와 검증 범위

배포 담당자의 운영 PostgreSQL 17.6 확인 결과: 기존 원본 23행 유지, 신규 publish_fb_data는 모두 NULL, 채널 이력 0행, RLS 활성, 6개 RPC의 service_role 실행 허용 및 anon 실행 차단. legacy 확인 테이블은 service_role SELECT만 허용하고 일반 사용자 접근을 제한한다. 사용자가 기존 적재 23행의 X 전체 발행 완료를 확인하여 `comic_novel_legacy_x_confirmations`에 1회성 해시 스냅샷을 기록하는 보완을 적용한다. 이는 실제 post ID를 생성하는 방식이 아니다. 선두 원본은 2026-04-05이며 사용자 확인과 현재 원문 해시가 일치하는 경우 소비할 수 있다. 스냅샷 이후 변경된 원문과 미래 신규 원본에 이 예외를 확장하지 않는다. 운영 backfill은 23행 반영 및 현재 원문 해시 23행 일치 확인 완료. preview 결과는 후속 운영 보고에서 확인한다.

legacy 보완 후 검증: Python 3.11.17 관련 회귀 196건 통과, 격리 PGlite PostgreSQL 18.3 SQL 검증 26건 통과. 운영 Page에 실제 게시한 결과가 아니다. 배포 후 연결 확인은 별도 운영 보고로 확정한다.

## 6. 제한 및 운영 대응

- 최초 텍스트 발행이 기본이다. 표지는 storage:// 경로와 바이트 SHA-256이 모두 확보된 경우만 재사용한다. 불일치·미확보 시 텍스트로 발행한다. 새 표지는 생성하지 않는다.
- GET Graph `/me`의 Page ID 일치는 대상 확인이며 게시 권한의 실검증은 아니다.
- 기존 공용 X publisher의 불명확 예외 재시도/텍스트 폴백 정책은 이번 작업에서 변경하지 않았다.
- 장애 시 Page·실행 로그·이력을 대조하여 게시 확인 또는 미게시 확인을 먼저 수행한다. 데이터 삭제나 날짜 NULL 복원으로 재발행하지 않는다.

## 운영 연결 확인 결과 — 2026-10-12

코드 main 병합과 운영 DB 마이그레이션은 완료했다. 운영 GitHub 연결 점검은 database_history_read에서 APIError 42501: 기존 SUPABASE_KEY의 역할은 anon이다. 따라서 활성 기본값을 false로 보류했다. 새 이력·RPC에는 서버용 service_role 키가 필요하며 anon 발행 권한을 열지 않는다. 기존 SUPABASE_KEY 시크릿 값을 해당 프로젝트 service_role 키로 갱신한 뒤 읽기 전용 점검 및 첫 원본 preview를 다시 확인하고 활성화한다. 새 X 확인 경로도 같은 권한이 필요하다. Facebook 실제 게시와 날짜 갱신은 아직 0건이다.
