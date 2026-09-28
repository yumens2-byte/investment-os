# Investment OS main 파이프라인 개발자 매뉴얼 v1.0

기준: `design/main-pipeline-v2` (2026-09-28 KST). 대상은 Python 3.11 및 `.github/workflows/main.yml`; 첨부 Kotlin 5개는 이 저장소의 빌드·실행 경로에 포함되지 않는다.

Notion 정본: [개발자 매뉴얼](https://app.notion.com/p/3e99208cbdc3812ebaa0c9ffb61e5327)

## 1. 코드 지도와 계약

| 진입점 | 책임 | 상태·테스트 |
| --- | --- | --- |
| `main.py` | CLI run/test/alert, 세션 분기 | `pilot_test.py`, `full_test.py` |
| `run_market.py` | 수집, 엔진 실행, validator, core_data 및 일별 저장 | `test_e2e_pipeline.py`, Tier 시그널 테스트 |
| `db/daily_store.py` | KST 날짜별 snapshot/analysis/news upsert | `tests/test_daily_store_contract.py` |
| `db/supabase_query.py` | 과거 string JSONB·신규 객체 동시 조회 | `tests/test_daily_query_mixed_json.py` |
| `run_view.py` | 포맷·X/TG/부가 발행 및 파일 이력 | 포맷·중복·스레드 테스트 |
| `run_alert.py` | ET 윈도, 판정, 경보 발송 | alert 경계·통합·정책 스크립트 |
| `notify_watchdog.yml` | 시작된 workflow_run 실패 알림 | `tests/test_watchdog_workflow.py` |

`store_daily_analysis()`는 `data.trading_signal.trading_signal`을 읽고, 누락/형식 오류면 해당 upsert를 수행하지 않는다. `store_daily_news()`는 `rss_result`의 감성·점수·건수를 읽는다. JSONB에는 `json.dumps()` 문자열 대신 Python dict/list를 전달한다. `store_all_daily_data()`의 부분 실패가 상위 run 종료 코드로 전파되는 기능은 아직 없다.

## 2. 개발·검증 절차

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt 'pytest>=8,<10'
DRY_RUN=true .venv/bin/python -m pytest -q tests/test_daily_store_contract.py tests/test_daily_query_mixed_json.py tests/test_market_holiday_timezone.py tests/test_duplicate_checker_scope.py
DRY_RUN=true .venv/bin/python main.py test --round all
DRY_RUN=true .venv/bin/python -m pytest -q tests --ignore=tests/test_alert_state_backend.py --ignore=tests/test_comic_pipeline.py
PYTHONPATH=. DRY_RUN=true .venv/bin/python tests/test_alert_state_backend.py
PYTHONPATH=. DRY_RUN=true .venv/bin/python tests/test_comic_pipeline.py
```

`pilot_test.py`와 `full_test.py`는 같은 파일 상태를 만질 수 있으므로 별도 순차 실행한다. CI의 `pilot_test`는 main push/PR에서 `main.py test --round all` 후 위 4개 pytest 파일을 실행한다. 다른 자체 테스트 스크립트는 표준 pytest fixture가 아닌 인자를 사용하므로 직접 실행해야 한다. 루트 `etf_engine.py`, `json_builder.py`는 Markdown 내용의 역사적 파일이며 전체 `compileall .`의 실패 원인이다. 패키지 디렉터리 대상 컴파일은 통과했다.

## 3. 데이터 변경 규칙

1. 수집 반환값 → `core_data` → `daily_store` → DB 컬럼 → 조회 소비자까지 하나의 표로 추적한다. `daily_snapshots`는 별도 `prediction_league` 퀴즈의 정답 입력이다.
2. 필수 입력 부재는 기본 HOLD/Neutral/0으로 숨기지 않는다. 전체 RSS 소스 실패와 유효한 0건은 다음 마이그레이션에서 상태값으로 구분한다.
3. KST 적재일을 미국 거래 대상일로 간주하지 않는다. 새 거래일·시각 필드를 추가할 때 ET DST/휴장/주말 케이스와 기존 소비자 호환 테스트를 함께 추가한다.
4. 동일 날짜 upsert는 과거 행을 덮으므로 운영 DB에서 계약 테스트를 실행하지 않는다. fake client 및 격리된 테스트 DB를 사용한다. 기존 135건 백필은 원본이 있는 날짜만 승인된 이관 절차로 처리한다.

## 4. 발행 변경 규칙과 미완료 항목

공통 X/TG 발행 함수는 `DRY_RUN`에서 네트워크 발행을 차단한다. preview 확장 시 랭킹, 번역/부가 게시, DLQ 등 전체 세션의 네트워크 발행 호출 0건을 통합 테스트로 확인한다. 파일 캐시가 발행 장부는 아니므로 신규 DB 테이블은 조건부 원자 선점·외부 ID·attempt·UNKNOWN을 포함해야 한다. 타임아웃을 성공 또는 재시도 가능 실패로 추정하지 않는다.

후속 작업 순서는 DATA-04~06 → PUB-04 → PUB-01~03 → ALERT/OPS → SEC다. 현재 Tier 1 시그널 테스트는 ZIP 원본에서도 144/145(위기 growth_score 기대 불일치), Hero Shorts 자체 테스트는 81개 중 파일 경로 의존 오류 3건이다. 신규 변경과 무관한 기존 실패도 CI 게이트로 승격하기 전에 원인을 수정해야 한다. 상세 실행 결과는 `source_level_impact_review_2026_09_28.md`를 참조한다.

## 5. 코드 리뷰 확인점

- cron 추가·수정 시 `on.schedule`과 job `if` 문자열 일치, watchdog `workflow_run` 표시 이름 일치.
- PR 검증 경로에 실발행 secret을 넘기지 않고 결과 artifact에 토큰·원문 민감값을 기록하지 않음.
- 채널별 일부 성공을 전체 성공으로 합치지 않고 외부 ID를 보존.
- run/채널별 중복 키와 테스트 디렉터리를 격리하고 병렬 실행 시 공유 파일 충돌을 검증.
- RLS/GRANT 변경 전 실제 DB key 소비자와 롤백 절차를 확인.
