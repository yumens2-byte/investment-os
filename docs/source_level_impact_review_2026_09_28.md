# 소스 수준 전수 실행 및 영향도 검토 (2026-09-28 KST)

대상: `design/main-pipeline-v2`의 `f122a67` 및 첨부 `investment-os-main (1).zip`, Kotlin 5개 파일. DRY_RUN·모의 데이터 기반 로컬 검증이며 운영 DB와 발행 API는 호출하지 않았다.

## 소스 기준 대조

- 첨부 ZIP 118개 파일 중 현 브랜치와 동일 72개, 내용 변경 42개, ZIP에만 있는 파일 4개. 현재 저장소에는 ZIP 이후 파일이 다수 추가됐다. 따라서 ZIP은 회귀 기준과 참고 소스이며 현 배포 후보와 동일하지 않다.
- Kotlin 5개는 저장소의 Python `main.yml`에서 호출하지 않고 `daily_*` 계약을 참조하지 않는다. 현 환경에 `kotlinc`/Gradle 및 의존성 프로젝트가 없어 컴파일·JUnit 실행은 미수행했다.
- Kotlin 정적 검토: `DslTest.kt`는 두 이름 입력 모두 `홍길동`으로 단언하고 `company()`는 `@Test`가 없으며 회사값 대신 이름을 검사한다. `BlogPostUpg.kt`와 `RunBlocking.kt`는 같은 패키지의 `BlogPostUpg`, `loadRssFeed`, `monitorBlogFeed`, `main` 중복 선언으로 함께 컴파일할 수 없다. `RssRead.kt`는 무한 입력 루프가 `@Test` 함수에 있어 자동 실행 시 종료되지 않는다. `BlogPostUpg.kt`의 신규 알림 조건은 `newFeeds.isEmpty()`로 반전돼 있다. Kotlin 소스는 별도 Gradle 프로젝트 경계와 요구사항을 정한 뒤 수정·테스트해야 한다.

## 실행 행렬

| 영역 | 실행 결과 | 판정 |
| --- | --- | --- |
| `tests/` 일반 pytest (자체 진입점 2개 제외) | 337 passed, 경고 1건 | 통과 |
| 자체 스크립트 `test_alert_state_backend`, `test_comic_pipeline` | 35/35, 9/9 | 통과 |
| `main.py test --round all` 단독 재검 | Round 1 28/28, Round 2 14/14 | 통과 |
| `full_test.py` | 146/146 | 통과 |
| `test_e2e_pipeline.py` | 67/67 | 통과 |
| `test_tier2_signals.py` | 126/126 | 통과 |
| Alert·이미지 스크립트 5개 | 114/114, 45/45, 90/90, 53/53, 55/55 | 통과 |
| `python -m unittest -q test_all` | 34 tests, OK | 통과 |
| `test_tier1_signals.py` | 144/145, 위기 `growth_score` 실제 3 vs 기대 4 이상 | 기존 결함. 첨부 ZIP의 원본에서도 144/145 동일 실패 |
| Hero Shorts `gen/hero_shorts/tests/test_all.py` 자체 실행 | 81 tests, 오류 3 | 별도 테스트 픽스처 경로 누락: `/tmp/opencode/ep86-post-final/ep86_caption_ko.txt`, `gen/data/out/ep86_plan.json` 등. pytest 직접 수집은 `unittest.main()`으로 중단됨 |
| Python 패키지 `collectors comic config core db edt engines gen publishers weekend tests` | compileall 통과 | 통과 |
| 저장소 모든 `.py` | 루트 `etf_engine.py`, `json_builder.py` 문법 실패 | 두 파일은 확장자 `.py`에 Markdown 설계서·매뉴얼을 담고 있으며 import 참조는 찾지 못함 |
| GitHub 워크플로 YAML | 14개 PyYAML 파싱 통과 | 통과 |

처음 `main.py`와 `full_test.py`를 동시에 실행할 때 파일럿 Round 2가 13/14로 실패했고, 단독 재실행은 42/42 통과했다. 두 테스트가 파일 상태를 공유하는 것으로 보이며 병렬 실행 격리를 추가 확인해야 한다. 이 결과는 병렬 전수테스트의 독립성 결함으로 기록한다.

## 변경 영향 및 조치 우선순위

1. 이번 `daily_store.py` 수정은 신규 `daily_analysis`·`daily_news` 저장 payload에 한정된다. 관련 계약·혼합형 조회 13건과 main 파일럿 단독 실행은 통과했다. `prediction_league`는 `daily_snapshots`를 직접 읽으며 이 필드는 이번 수정 대상이 아니다.
2. Tier 1 위기 점수 기대값과 실제 가중치의 정책 차이를 결정하고 회귀 시나리오를 수정하거나 계산식을 검토한다. 첨부 ZIP에서도 재현돼 신규 회귀로 분류하지 않는다.
3. Hero Shorts 테스트에서 절대 경로·사전 파일 의존을 임시 디렉터리 픽스처로 바꾸고, pytest 수집 시 진입점을 가드한다. 테스트 오류 3건은 기능 정상 판정을 막는다.
4. 루트의 Markdown `.py` 2개를 문서 확장자로 정리하고, Python 전수 compileall을 CI에 추가한다. 파일럿과 full_test의 상태 파일을 테스트별로 격리한다.
5. 운영 DB 쓰기·실제 GitHub Actions runner·외부 API 발행은 이번 검증 범위에 포함되지 않는다. 따라서 운영 전수 성공 또는 배포 승인으로 해석하지 않는다.
