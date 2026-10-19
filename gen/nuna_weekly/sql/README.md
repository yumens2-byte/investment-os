# gen/nuna_weekly/sql — 적용 완료 마이그레이션 기록

Supabase 프로젝트 `ccomoimhhttaklfadaos` 에 **2026-10-19 적용 완료**된 마이그레이션의 원문 기록이다.
재적용 금지 — 원본 이력은 `supabase_migrations.schema_migrations` 에 있다.

| 순서 | 이름 | 내용 |
| --- | --- | --- |
| 1 | `nuna_001_tables` | 테이블 4개(nuna_episodes / nuna_publish_targets / nuna_cost_ledger / nuna_step_runs), 인덱스, RLS 활성 |
| 2 | `nuna_002_guards` | updated_at 트리거, 비용 원장 append-only 가드, `nuna_reserve_cost` 예산 함수, service_role GRANT |
| 3 | `nuna_003_least_privilege` | anon·authenticated 권한 전부 회수 (마스터 확인 후 적용) |

검증(2026-10-19, 롤백 트랜잭션): T1~T9 9건 통과 — 설계서 16장.
