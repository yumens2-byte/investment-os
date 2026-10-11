# Comic Novel Facebook 연재 요구사항 정의서 v1.0

- 기준일: 2026-10-12 KST
- 저장소: yumens2-byte/investment-os
- 요청 범위: 기존 comic_novel 운영을 바탕으로 요구사항 정의, 상세설계 및 개발 착수
- 문서 등록 대상: 기존 Notion C-7 EDT Universe 소설형 에피소드 발행 설계 영역. 등록 완료 여부는 작업 결과에서 별도로 확인한다.
- 운영 반영 상태: 운영 DB 마이그레이션 및 기존 23행 확인 완료. 기존 SUPABASE_KEY는 변경하지 않는다. GitHub OIDC 제한 gateway 보완 후 실제 무쓰기 smoke 결과 확인까지 활성화 보류. 실제 Facebook 게시 미수행.

## 1. 목적 및 범위

기존 X comic_novel이 저장한 주간 합본 소설을 Facebook에 매주 금요일 한 편씩 연재한다. Facebook은 X의 후속 소비 채널이며 소설을 독립 생성하지 않는다. 기존 제목, 에피소드 범위와 novel_text를 재사용한다. FB 실행은 X/TG 발행이나 MVP 투표를 실행하지 않는다.

사용자 표현의 publish_data는 기존 코드와 운영 DB의 publish_date에 대응한다. 기존 컬럼명은 유지하며 신규 컬럼은 요청대로 publish_fb_data를 사용한다.

## 2. 확인된 기준 사실

| 항목 | 확인 내용 |
|---|---|
| 원본 | public.comic_novels |
| 운영 DB 읽기 확인 | 기존 데이터 23행, 가장 오래된 날짜 2026-04-05, 최신 날짜 2026-10-12 |
| 에피소드 범위 | 가장 오래된 행 EP01~04, 최신 행 EP107~108 |
| 원본 날짜 | publish_date DATE NOT NULL |
| 기본키 | id INT |
| 날짜 유일성 | uix_novel_date 유일 인덱스 |
| FB 날짜 | 운영 DB에 publish_fb_data DATE 적용 완료, 기존 23행 모두 NULL |
| 기존 X 결함 | X 실패 및 부분 발행을 충분히 검증하지 않고 published 상태를 기록할 수 있음 |

운영 DB 읽기 확인 결과는 작업 중 조회 시점의 스냅샷이다. 사용자가 기존 23행의 X 전체 실발행 완료 및 published 상태의 신뢰성을 확인했다. 표지 자산 확보 여부는 별도 확인한다.

## 3. 기능 요구사항

| ID | 요구사항 | 수용 기준 |
|---|---|---|
| FR-01 | X에 종속된 Facebook 발행 | 기존 23개 적재본은 사용자 확인에 따라 published 상태와 원문 해시 스냅샷으로 인정. 신규 원본은 X 전체 실제 ID·건수 검증 |
| FR-02 | 매주 금요일 한 편 | KST 금요일에 실행하고 동일 KST 주차의 신규 성공 발행은 최대 1건 |
| FR-03 | 이번 주부터 시작 | 최초 정규 슬롯은 2026-10-16 금요일. 배포·검증 완료 전 실제 활성화하지 않음 |
| FR-04 | 오래된 원본부터 | 미발행 대상 중 publish_date ASC 순으로 한 행 선정. 동률 보조 정렬은 실제 PK id 사용 |
| FR-05 | FB 발행일 컬럼 | publish_fb_data DATE NULL 추가. 기존 데이터는 NULL 유지 |
| FR-06 | 성공 후 날짜 저장 | Facebook 성공 및 실제 post ID 확인 후 실제 KST 발행일을 날짜 컬럼에 기록 |
| FR-07 | 대상 없으면 스킵 | 빈 테이블, 전부 발행 완료, 적격 원본 없음은 정상 스킵. 신규 소설 생성 또는 외부 발행 없음 |
| FR-08 | 본문 재사용 | 저장 제목·본문·문단·대사를 유지. X 스레드 유도 문구를 Facebook 형식에 맞게 제거 |
| FR-09 | 표지 재사용 | 확보한 X 표지를 재사용. 과거 표지 미확보 시 기존 publisher의 텍스트 경로 사용. 이미지 신규 생성 금지 |
| FR-10 | 순서 유지 | 가장 오래된 대상이 X 미확인 또는 본문 오류이면 보류. 더 최신 행으로 건너뛰지 않음 |
| FR-11 | 미래 원본 제외 | publish_date가 현재 KST 날짜보다 미래이면 선정하지 않음 |
| FR-12 | 기존 경로 보완 | X 전체 성공·부분 실패·dry-run 판정을 분리하고 실패 상태를 성공 원본으로 취급하지 않음 |

## 4. 오류 및 운영 요구사항

| ID | 요구사항 | 수용 기준 |
|---|---|---|
| NFR-01 | 원본·주차 중복 방지 | DB 원자적 선점과 유일 제약으로 동시 실행에서도 발행 요청은 최대 한 번 |
| NFR-02 | DB 오류와 빈 큐 구분 | 읽기 오류는 실패로 보고하며 SKIPPED_NO_DATA로 변환하지 않음 |
| NFR-03 | dry-run 무변경 | preview는 Graph 요청, 발행일 변경, 이력 쓰기를 수행하지 않음 |
| NFR-04 | 불명확 응답 보호 | timeout·성공 여부 불명·성공 무ID는 보류하고 자동 재게시하지 않음 |
| NFR-05 | 성공 후 DB 실패 복구 | 전송 상태를 발행 전에 영속 기록. 성공 후 DB 실패는 실제 게시 확인 및 DB 보정으로 복구하며 재게시 금지 |
| NFR-06 | 비밀정보 보호 | 기존 secrets 사용. 토큰이나 인증 정보를 본문·로그·결과 artifact에 출력하지 않음 |
| NFR-07 | 원본 보호 | 이미 발행한 원본 재실행이 본문·FB 날짜·발행 이력을 덮어쓰지 않음 |
| NFR-08 | 길이·내용 검증 | 빈 본문·길이 초과·선점 이후 해시 변경은 보류. 임의 절단이나 추가 게시 금지 |
| NFR-09 | 결과 추적 | run ID, 원본 날짜, 슬롯 주차, 결과 상태, post ID, 오류 유형 기록 |
| NFR-10 | 알림 독립성 | 운영 알림 실패가 확정된 게시 성공을 취소하거나 재게시시키지 않음 |

## 5. 데이터 및 처리 계약

대상은 publish_fb_data IS NULL, status='published', publish_date <= today_kst 조건을 만족하는 가장 오래된 한 행이다. 해당 행의 신규 X 성공 증거 또는 사용자 확인 기존 23행의 원문 해시 스냅샷을 검증한다. generated, DRY_RUN ID, 빈 ID, 부분 발행, 실패 결과는 X 완료 증거로 인정하지 않는다.

발행 전 주차와 미해결 sending/unknown 이력을 확인한다. 같은 주 이미 성공하면 SKIPPED_WEEK_DONE, 대상이 없으면 SKIPPED_NO_DATA로 정상 종료한다. X 미확인은 HOLD_X_UNCONFIRMED로 종료한다.

성공 응답의 실제 post ID 확보 후 채널 이력 ok와 publish_fb_data를 하나의 DB 트랜잭션으로 반영한다. 날짜는 워크플로 실행 예정일이 아니라 확인된 실제 KST 발행일이다. 갱신 행수는 정확히 한 행이어야 한다.

원본·주차 선점, 전송 전 sending, 성공 후 ok, 불명확 unknown 및 reconcile을 담는 보조 채널 이력은 중복 방지를 위한 구현 설계이다. 요청한 publish_fb_data 컬럼을 대체하지 않는다.

## 6. 스케줄 및 제안 구분

사용자 확정 요구는 매주 금요일 한 편, 이번 주 시작, 오래된 순서, 빈 큐 스킵, 발행 후 날짜 기록이다. 금요일 22:17 KST 및 UTC cron `17 13 * * 5`는 기존 운영 시·분을 유지하는 설계 선택이다. 실제 GitHub Actions 실행 시각은 지연될 수 있다.

별도 workflow comic_novel_fb.yml을 사용하며 preview/publish/reconcile을 분리한다. 운영 활성화 플래그 기본값은 비활성으로 두고 실발행 전 설정·권한·마이그레이션·Page 대상과 검증 결과를 확인한다.

## 7. 검수 및 완료 기준

1. 빈 큐·전부 완료·미래 데이터·generated만 존재할 때 발행 요청 없이 스킵한다.
2. 적격 여러 행에서는 가장 오래된 한 행만 선정한다.
3. X 실패·부분 성공·dry-run 증거는 Facebook에서 발행하지 않는다.
4. dry-run은 DB 및 외부 발행에 쓰기 0회를 보장한다.
5. 정상 성공 시 실제 post ID 및 KST 날짜를 한 행에 기록하고 같은 주 재실행을 스킵한다.
6. 동시 실행은 DB 선점 하나만 성공한다.
7. 응답 불명확·전송 중 runner 종료·성공 후 DB 실패는 자동 재게시 없이 보류 및 reconcile로 복구한다.
8. 원본 본문·FB 날짜와 확정 이력은 X 재실행에도 보존된다.
9. KST 금요일 경계·최초일·연말 주차·다음 주 슬롯을 검증한다.
10. 운영 DB 및 실제 Facebook에 테스트 데이터를 쓰지 않고 단위·격리 통합 검증을 완료한다.

## 8. 상세설계·개발 착수 상태

상세설계와 구현을 완료했고 운영 DB 마이그레이션·23행 legacy 확인을 반영했다. OIDC gateway 배포와 실제 smoke 확인 전 활성화는 보류한다. 현재 개발 검증 결과는 Python 3.11 테스트 196건 및 SQL 격리 검증 26건 통과이다. 실제 Facebook 발행 완료를 의미하지 않는다. 테스트 최종 근거와 변경 파일은 PR 및 테스트 보고서에서 관리한다.

잔여 확인: OIDC gateway 배포·실제 smoke, 대상 Page와 최초 원본 preview, 운영 활성화 및 첫 실제 금요일 게시 결과. 기존 23행 X 확인과 운영 DB 마이그레이션은 완료했다.

## 9. 근거

- 분석 기준 문서: Comic_Novel_Facebook_Analysis_Detail_Design_20261012.md
- 저장소 기준 커밋: f038cfd67645b204b4100749a52cd14874153e12
- 기준 모듈: .github/workflows/comic_novel.yml, weekend/comic_novel.py, db/daily_store.py, publishers/x_publisher.py, publishers/facebook_publisher.py
- 사용자 지시: Facebook은 X 종속, 매주 금요일 한 편, 없으면 스킵, publish_fb_data 추가, 오래된 원본부터 발행 후 날짜 갱신

## 10. 인증 보완 요구사항

사용자 지시: 별도 로그인과 기존 SUPABASE_KEY 변경 없이 운영한다. NOVEL_USE_GITHUB_OIDC=true에서 GitHub Actions의 단기 OIDC 토큰으로 comic-novel-gateway를 호출한다. 공개 anon 역할에 DB 권한을 확대하지 않고 신규 GitHub secret도 추가하지 않는다. 서버 내부의 기본 service_role 키는 Edge 함수 안에서만 사용한다. Sunday 워크플로는 X 확인 쓰기, Friday 워크플로는 Facebook 제한 RPC 쓰기, CI main push는 읽기만 허용한다. 실제 OIDC smoke 확인 전 활성화하지 않는다.
