# main 파이프라인 1차 분석설계·상세설계 — 일별 DB 적재

기준: 요구사항 DATA-01~03. 기준 코드 `2c3f6f7`, 운영 DB 조회 2026-09-28 KST. 이 설계는 신규 적재분의 값·타입 정정만 다룬다. 발행, cron, 계산 엔진, 운영 DB 스키마·과거 자료를 변경하지 않는다.

## 분석설계: 계약과 원인

| 소스 필드 | 실제 생성자 | 현재 저장 오류 | 신규 적재 계약 |
|---|---|---|---|
| `data.trading_signal.trading_signal` | `engines/risk_engine._determine_trading_signal` | `signal` 키 조회→기본 HOLD | 문자열 검증 후 그대로 저장; 부재 시 적재 실패를 반환 |
| `rss_result.news_sentiment` | `collectors/news_rss.collect_news_sentiment` | `data.news_summary.sentiment` 조회 | 수집 결과의 Bullish/Neutral/Bearish 저장 |
| `rss_result.sentiment_score` | 동일 wrapper | `data.news_summary.weighted_score` 조회 | 숫자로 변환해 저장 |
| `rss_result.total_headlines` | 동일 wrapper | `data.news_summary.headline_count` 조회 | 비음수 정수 그대로 저장 |
| `etf_rank`, `etf_allocation`, `market_score`, `top_issues`, `top_headlines` | 분석/수집 결과 | `json.dumps`를 JSONB 컬럼에 전달 | Python dict/list를 전달하여 JSONB object/array 저장 |

`rss_result` 누락은 오류로 기록한다. 수집은 성공했으나 모든 원본이 0건이면 `sources_ok=0`, `sources_fail>0`인 결과가 반환되며 이번 1차 범위에서는 기존의 Neutral/0 저장을 유지한다. 정상 0건과 전체 실패를 영속 구분하는 새 컬럼은 DATA-02 후속 마이그레이션으로 남긴다.

`daily_*`는 날짜별 UPSERT이므로 동일 KST 날짜의 재실행이 이전 행을 덮어쓴다. 배포 직후 기존 KST 날짜를 의도하지 않게 소급 변경하지 않도록 실 API 검증을 하지 않는다. 변경 전 135건은 자동 백필하지 않는다. 원본 비교 가능한 날짜만 별도 이관 계획에 따라 처리한다.

## 상세설계: 변경 경계

`db/daily_store.py` 내 `store_daily_analysis`와 `store_daily_news`만 수정한다. 성공 시 기존 bool 반환/키 이름을 유지한다. 필수 필드 부재나 잘못된 타입은 함수의 기존 예외 포착 경로를 통해 False 반환과 경고 로그를 생성한다. 다른 2개 테이블 upsert는 `store_all_daily_data`의 현재 구조대로 계속 실행한다. 이 부분의 PARTIAL_FAILED 상향은 DATA-06에서 처리한다.

### 검증 사례

1. 실제 엔진 형태의 `trading_signal`을 입력하면 DB row의 신호가 일치하고 JSON 필드가 dict다.
2. `trading_signal` 키가 없으면 해당 row upsert 없이 False.
3. RSS 헤드라인 3건, Bullish, 점수 1.25 입력 시 DB row의 3개 컬럼이 일치하고 top_headlines는 list다.
4. `rss_result` 누락 시 해당 row upsert 없이 False. 모든 소스 실패의 유효 dict는 0건·Neutral로 저장된다.
5. 운영 DB에 연결하지 않은 fake Supabase client로 입력 payload를 검사한다.

### 롤백

이 커밋만 revert하면 기존 DB payload 매핑으로 돌아간다. 신규 저장 값의 복구는 재실행으로 덮어쓸 수 있으므로 롤백 전 신규 적재된 날짜의 DB row를 확인한다. 과거 135건에는 자동 쓰기를 하지 않는다.
