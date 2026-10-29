# Comic Novel Facebook 운영자 매뉴얼 — Actions 입력 안내

기준일: 2026-10-30 KST (최초 2026-10-12). 대상: 📝 Comic Novel (Sunday) 워크플로우의 facebook-novel 작업.

> 2026-10-30 변경: Facebook 연재는 금요일 단독 워크플로우에서 **comic_novel.yml 일요일 실행의 병렬 작업**으로 이동했습니다. X(x-novel)와 Facebook(facebook-novel)이 같은 실행에서 동시에 시작합니다. comic_novel_fb.yml은 직접 실행할 수 없는 재사용 워크플로우가 되었습니다. 발행 여부는 publish_fb_yn(Y/N, publish_fb_data에서 자동 계산)으로 확인합니다.

## 1. 평소에는 수동 실행이 필요 없습니다

매주 일요일 10:17 KST에 자동 실행합니다(cron `17 1 * * 0`). 최초 예약일은 2026-11-01입니다. AI 호출 없이 publish_fb_yn='N' 중 publish_date가 가장 오래된 원본 한 편을 처리하고, 데이터가 없으면 정상적으로 건너뜁니다. 성공한 뒤 publish_fb_data에 실제 KST 게시 날짜를 기록하며 publish_fb_yn은 자동으로 Y가 됩니다. GitHub 예약 실행은 지연될 수 있습니다.

자동 실행에는 운영자가 입력할 항목이 없습니다. 아래 Run workflow는 미리보기, 필요한 수동 발행, 장애 복구에 사용합니다.

## 2. Run workflow 여는 방법

1. [Comic Novel Actions](https://github.com/yumens2-byte/investment-os/actions/workflows/comic_novel.yml)를 엽니다. Facebook만 실행하려면 반드시 target=facebook을 선택합니다(all은 X도 실행합니다).
2. Run workflow를 누릅니다.
3. Use workflow from / Branch는 main을 선택합니다. 다른 브랜치는 운영 인증 대상이 아닙니다.
4. 아래 표에서 목적에 맞는 값을 입력하고 Run workflow를 누릅니다.
5. 생성된 실행을 열어 결과를 확인합니다.

기존 GitHub 이용 권한으로 실행합니다. 이 작업 때문에 새 서비스 로그인이나 SUPABASE_KEY 변경을 할 필요는 없습니다.

## 3. 입력값 한눈에 보기

체크함 = true, 체크 해제 = false. 빈칸은 아무 값도 입력하지 않는다는 뜻입니다.

| 입력 항목 | 미리보기 — 평소 점검 | 수동 실제 발행 | 이미 게시된 건의 DB 복구 | 미게시가 확인된 건의 해제 |
|---|---|---|---|---|
| Branch | main | main | main | main |
| target | facebook | facebook | facebook | facebook |
| dry_run | true | false | false | false |
| fb_stage | preview | publish | reconcile | reconcile |
| fb_claim_id | 빈칸 | 빈칸 | 해당 선점 ID | 해당 선점 ID |
| fb_post_id | 빈칸 | 빈칸 | 실제 Facebook 게시 ID | 빈칸 |
| fb_confirmed_at | 빈칸 | 빈칸 | 실제 게시 시각 | 빈칸 |
| fb_note | 빈칸 | 빈칸 | 게시 확인 근거 | 미게시 확인 근거 |
| fb_not_posted | 체크 해제 | 체크 해제 | 체크 해제 | 체크함 |

**평소 점검은 target=facebook, fb_stage=preview로 실행하고 fb_ 입력 다섯 항목은 비워 두세요.** fb_stage 기본값은 preview입니다. dry_run은 X와 공용 문자열 입력(기본 false)이며, fb_stage=preview이면 dry_run 값과 무관하게 미리보기로 실행됩니다. 아래 본문의 stage·claim_id·post_id·confirmed_at·note·not_posted는 각각 fb_ 접두 입력을 뜻합니다.

## 4. 가장 많이 쓰는 실행: 미리보기

Branch=main, target=facebook, fb_stage=preview, dry_run=true. claim_id, post_id, confirmed_at, note는 빈칸이며 not_posted는 체크 해제입니다.

미리보기는 후보 원본과 완성된 게시 본문을 확인합니다. Facebook 게시나 DB 갱신을 하지 않습니다. 일요일이 아니어도 사용할 수 있습니다. stage=preview이면 dry_run을 실수로 해제해도 미리보기로 실행됩니다.

결과 status가 PREVIEW이면 source_publish_date와 preview를 확인합니다. 현재 확인된 최초 원본은 2026-04-05 EP.01~EP.04입니다. 이후 데이터 상태에 따라 후보는 달라집니다.

## 5. 수동으로 실제 한 편 발행하기

Branch=main, target=facebook, fb_stage=publish, dry_run=false. 나머지 입력은 미리보기와 동일하게 비웁니다.

실제 발행 조건은 활성화 상태, KST 일요일, 2026-11-01 이후, 같은 주(월~일) 미발행, 원본의 X 발행 확인입니다. 일요일이 아니면 SKIPPED_NOT_SUNDAY이며, 이미 그 주에 발행했으면 SKIPPED_WEEK_DONE입니다.

자동 발행이 정상적으로 완료됐다면 수동 발행을 추가로 실행할 필요가 없습니다. 이 실행은 가장 오래된 후보 한 편을 처리하며 운영자가 날짜나 원본을 직접 지정하지 않습니다.

fb_stage=publish여도 dry_run=true이면 실제 게시하지 않고 미리보기로 동작합니다.

## 6. 결과 확인 방법

1. Actions의 해당 실행을 열고 facebook-novel / comic-novel-facebook 작업을 선택합니다.
2. Consume or reconcile one stored novel 단계의 마지막 JSON을 확인합니다.
3. 실행 요약의 Artifacts에서 comic-novel-facebook-실행번호를 내려받습니다.
4. 안에 있는 comic-novel-fb-report.json을 엽니다. 실행이 중간에 끝나면 artifact가 없을 수 있습니다.

| 결과 status | 의미 | 운영자 조치 |
|---|---|---|
| PREVIEW | 미리보기 완료, 게시 없음 | source_publish_date와 preview 확인 |
| PUBLISHED | 실제 게시 및 DB 확정 완료 | Facebook 원문과 게시 ID 확인 |
| SKIPPED_NO_DATA | 발행 후보 없음 | 정상 종료, 입력 변경 불필요 |
| SKIPPED_WEEK_DONE | 이번 주 발행 완료 | 추가 발행 불필요 |
| SKIPPED_NOT_FRIDAY / SKIPPED_BEFORE_FIRST_DATE | 실행 요일 또는 최초일 조건 미충족 | 다음 정규 예약 실행 대기 |
| SKIPPED_DISABLED | 운영 중지 상태 | 의도한 중지인지 담당자 확인 |
| SKIPPED_CLAIM_BLOCKED | DB 선점이 허용되지 않음 | 기존 실행·이력 확인 |
| HOLD_UNRESOLVED / HOLD_UNKNOWN / HOLD_DB_COMMIT / 기타 HOLD | 미해결 게시 또는 증거 부족 | 아래 복구 절차에 따라 사실 확인 |
| FAILED_* | 설정·연결·게시·복구 실패 | JSON의 status/error_type과 실행 링크를 담당자에게 전달 |
| RECONCILED | 이미 게시된 사실을 DB에 반영 | 추가 게시 불필요 |
| RECONCILED_NOT_POSTED | 미게시 확인으로 선점 해제 | 새 게시를 수행한 결과가 아님 |

초록색 성공 실행도 PREVIEW 또는 SKIPPED일 수 있습니다. 실제 게시 여부는 PUBLISHED와 실제 Facebook 게시물을 함께 확인합니다. HOLD는 Actions에서 실패로 표시될 수 있습니다.

## 7. 장애 복구 입력은 언제 쓰나요?

Facebook 게시가 이미 존재하지만 DB 확정이 실패했거나, 전송 결과가 불확실해 다음 연재가 막힌 경우에만 stage=reconcile을 사용합니다. 이 기능 자체는 새 Facebook 게시를 하지 않고 DB 상태를 보정합니다.

### A. 실제 게시물이 존재하는 경우

Branch=main, target=facebook, fb_stage=reconcile, dry_run=false, fb_not_posted=체크 해제로 설정합니다. 다음 네 값을 입력합니다.

- claim_id: 실패 실행 결과의 claim_id. DB 발행 선점 번호이며 GitHub 실행번호나 comic_novels의 원본 ID가 아닙니다.
- post_id: 결과의 실제 post_id 또는 담당자가 확인한 실제 Facebook 게시 ID. 게시물 URL을 그대로 입력하지 않습니다. 숫자 또는 PAGE_ID_POST_ID 형식입니다.
- confirmed_at: 실제 게시 시각에 시간대를 붙입니다. 형식 예시는 2026-10-16T22:18:00+09:00입니다. 이 값은 입력 형식 예시이므로 실제 시각으로 바꿔야 합니다. 실행 시작 시각이나 현재 시각을 대신 넣지 않습니다.
- note: 어떤 게시물·원문·시각·로그를 대조했는지 기록합니다. 예: “해당 Page 게시물 URL과 원문, 게시 ID, 게시 시각 확인; 실패 실행 링크 …”.

actor는 실행한 GitHub 사용자로 자동 기록되므로 별도 입력칸이 없습니다. 실제 게시 ID나 시각을 확보하지 못했다면 복구 값을 추정해서 입력하지 않습니다.

### B. 실제 미게시를 확인한 경우

Branch=main, target=facebook, fb_stage=reconcile, dry_run=false, fb_not_posted=체크함으로 설정합니다.

claim_id와 note만 입력하고 post_id/confirmed_at은 반드시 비웁니다. note에 Page 활동 기록, 실행 로그 등 미게시 확인 근거를 적습니다. 이 실행은 미해결 선점을 해제할 뿐 즉시 발행하지 않습니다. 다음 적법한 금요일 실행에서 재선정될 수 있습니다.

게시 여부를 모르면 not_posted를 체크하지 않고 보류를 유지합니다. 시간이 지났거나 오류가 났다는 사실만으로 미게시가 확인되는 것은 아닙니다.

## 8. 값이 없거나 실패할 때

- claim_id가 결과에 없으면 임의의 번호를 넣지 않습니다. 실패 실행 링크와 결과 파일을 담당자에게 전달해 이력을 확인합니다.
- FAILED_RECONCILE이면 입력 누락, ID·시각 형식, 실제 이력 상태를 확인합니다. 게시 날짜나 이력을 직접 삭제해서 해결하지 않습니다.
- 인증·DB 연결 오류가 나면 기존 SUPABASE_KEY는 그대로 두고 실행 링크와 error_type을 담당자에게 전달합니다. 운영 경로는 기존 키와 GitHub 실행 인증을 함께 사용합니다.
- 토큰·키는 note, 문서, 로그에 붙여 넣지 않습니다.
- 게시 결과가 불확실한 상태에서 publish 재실행으로 복구하지 않습니다.

## 9. 운영 중지와 재개

저장소 변수 FACE_NOVEL_ENABLED=false는 신규 발행 중지 설정입니다. 저장소 변수 관리 권한이 있는 담당자가 변경합니다. workflow 기본값은 true입니다. 중지는 이미 게시된 콘텐츠를 삭제하지 않습니다. 미리보기와 증거가 확인된 reconcile은 별도 실행 목적이며 사용할 수 있습니다.

예약 실행 시간이나 최초일을 바꿔서 조건을 우회하지 않습니다.

## 10. 현재 확인된 운영 상태

2026-10-12 기준 main 코드·gateway 배포 및 운영 연결 미리보기 검증 완료. 기존 원본 23행과 기존 SUPABASE_KEY를 유지했습니다. 최초 정규 실행은 2026-10-16 금요일 22:17 KST입니다. 이 문서 작성 시 실제 Facebook 게시와 publish_fb_data 갱신은 0건입니다.

[운영 연결 점검](https://github.com/yumens2-byte/investment-os/actions/runs/38178676459/job/114586388853)은 Page 대상 일치 및 첫 원본 PREVIEW를 확인한 결과입니다. 실제 게시 권한과 첫 게시 성공은 정규 발행 결과에서 확인합니다.
