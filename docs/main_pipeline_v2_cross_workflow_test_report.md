# main 파이프라인 1차 구현 검증 및 타 워크플로 데이터 교차 점검

기준일: 2026-09-28 · 기준 브랜치: `design/main-pipeline-v2` · 운영 DB 쓰기 및 외부 게시 없음

## 구현 범위

- `db/daily_store.py`: `data.trading_signal.trading_signal` 및 `rss_result` 실제 반환 계약에 맞춘 신규 저장값, JSONB 객체/배열 전달, 잘못된 입력의 쓰기 방지.
- `.github/workflows/main.yml`: pilot_test에 계약·혼합형 조회·날짜·중복 범위 pytest 13건 실행을 추가.
- `.github/workflows/notify_watchdog.yml`: `workflow_run.workflows`의 EDT 2건과 Hero Shorts 표시 이름을 실제 워크플로 이름과 일치시킴.
- `full_test.py`: 기존 코드 변화와 어긋난 3개 단언을 실제 라우터·호출 인자·세션 라벨 계약에 맞춰 수정.

## 타 워크플로 데이터 의존성

| 워크플로 | 실제 실행 경로·데이터 | 이번 변경과의 관계 |
| --- | --- | --- |
| `main.yml` | `db/daily_store.py`가 `daily_snapshots`, `daily_analysis`, `daily_news`, `daily_alerts` 저장. `db/supabase_query.py`가 snapshot/analysis 조회; `engines/alert_followup.py`가 alerts/snapshots 조회 | 직접 대상. 기존 JSONB 문자열과 새 객체를 조회하는 혼합형 테스트 포함. |
| `prediction_league.yml` | `engines/data_quiz.py`가 전일 `daily_snapshots`의 실제값 조회. `engines/prediction_league.py`는 SPY 절대값을 별도 수집 | snapshot의 날짜·정확성이 퀴즈에 전파됨. 이번 저장 변경은 analysis/news에 국한하며 snapshot 계약은 유지. |
| `comic_daily.yml`, `comic_weekly.yml` | `comic.pipeline`; `core_data.json`은 일부 생성기에서 선택 입력이고, 해당 워크플로에 main 산출물 복원 단계가 없음 | main의 daily_analysis/news 변경과 직접 공유 없음. 선택 입력 누락 시 폴백 동작은 별도 품질 점검 대상. |
| `viral_content.yml` | 활성 C-20은 `engines.viral_engine.run_viral_c20` | 변경된 daily_analysis/news 테이블 직접 참조는 정적 검색에서 확인되지 않음. |
| `edt_snapshot.yml`, `edt_snapshot_watchdog.yml` | EDT 자체 수집·감시 | main 적재 테이블 직접 참조 없음. 알림 watchdog의 감시 대상 이름을 교정. |
| `notify_watchdog.yml` | 모든 지정 워크플로의 `workflow_run` 결과 | 지정 12개 이름 전부 실제 최상위 `name`과 일치함을 YAML 파싱으로 검증. |
| 그 밖의 `.yml` | Hero Shorts, Weekend, Viral Daily/Performance 등 | `daily_analysis`/`daily_news` 직접 참조가 정적 검색에서 발견되지 않음. 독립 실행 시크릿·외부 서비스는 실 호출 검증 대상 아님. |

## 실행 결과

| 검증 | 결과 |
| --- | ---: |
| `DRY_RUN=true LOG_LEVEL=WARNING python main.py test --round all` | Round 1 28/28, Round 2 14/14, 총 42/42 |
| CI pilot pytest 4개 파일 | 13 passed |
| `python full_test.py` | 146 PASS, 0 FAIL |
| `pytest -q tests --ignore=tests/test_alert_state_backend.py --ignore=tests/test_comic_pipeline.py` | 337 passed, 경고 1건 (`tweepy`의 `imghdr` 폐기 예정) |
| 별도 스크립트 `test_alert_state_backend.py`, `test_comic_pipeline.py` | 각각 35/35, 9/9 PASS (`PYTHONPATH=.`) |
| Alert/이미지 경계·통합·정책 스크립트 5개 | 114/114, 45/45, 90/90, 53/53, 55/55 PASS |
| 전체 `.github/workflows/*.yml` PyYAML BaseLoader 파싱 및 watchdog 이름 참조 | 14개 파싱, 감시 대상 12개 중 미해결 0개 |
| `git diff --check` | 통과 |

pytest가 기존 두 스크립트의 사용자 정의 `tmp_dir`/`t` 인자를 fixture로 해석해 직접 전체 수집 시 5건 오류가 발생했다. 각 스크립트의 자체 실행 진입점으로 별도 통과했고, CI에는 pytest 형식인 4개 파일만 지정했다.

## 잔여 게이트

- GitHub Actions 실제 runner, 외부 API, 운영 Supabase write, X·Telegram 발행은 실행하지 않았다. 로컬 테스트 결과는 배포 성공을 뜻하지 않는다.
- 운영 DB의 과거 135건 잘못된 신호·RSS·JSONB 문자열은 변경되지 않았다. 원본 대조 가능한 건만 별도 정정 계획을 수립한다.
- `daily_snapshots` 기준일과 미국 시장 거래일의 차이, 부분 적재 실패·재시도, publication 장부 및 RLS는 요구사항의 후속 단계다.
- 기존 두 자체 테스트 스크립트를 표준 pytest 수집 형태로 바꾸는 것은 별도 정리 작업으로 남긴다.
