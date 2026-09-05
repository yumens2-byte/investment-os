# narrative 세션 고도화 (rev.2026-09-06)

> Notion 공통 지침서(`3359208cbdc3818e9f0af0ceebc381ac`) 기록용 요약

## 1. 배경

narrative 세션(KST 월~금 11:36)이 **1회 실행당 X에 3건을 연속 발행**하고 있었고,
그중 2건은 본문이 완전히 동일했다. 발행 간 딜레이는 0초였다.

| 순번 | 위치(변경 전) | 본문 | 이미지 |
|---|---|---|---|
| ① | `run_view.py:251` | `format_image_tweet(data,"narrative")` | full 대시보드 |
| ② | `run_view.py:392` | `format_narrative_tweet(narrative)` | 없음 |
| ③ | `run_view.py:400` | `format_narrative_tweet(narrative)` ← ②와 동일 문자열 | VS 카드 |

## 2. 발행 구조 변경 (N-1)

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| X 발행 API 호출 | 3회 | 2회 (1스레드) |
| 동일 본문 중복 | 2건 | 0건 |
| 발행 간 딜레이 | 0초 | publish_thread 랜덤 (진입 15~30s + 포스트 간 9~34s) |
| Gemini 호출 | 1회 | 1회 (Step 3 결과를 Step 6-TG가 재사용) |
| 실효 발행 창 | 약 100초 | 약 15분 |

파이프라인 배선:
`Step 3(narrative 분기, posts 구성)` → `Step 4(R-1 적용)` → `Step 5.5(이미지 로테이션)`
→ `Step 6(기존 이미지+스레드 경로 재사용, 무변경)` → `Step 6-TG(텔레그램만)`

## 3. 변칙화 (N-2 / N-3)

| 축 | 변경 전 | 변경 후 |
|---|---|---|
| 트윗 헤더 | `📝 AI 시장 해설` 고정 | 풀 10종 랜덤 (무헤더 3종 포함) |
| 해시태그 | `#ETF #투자 #미국증시 #AI분석` 고정·순서 고정 | `x_formatter.build_hashtags(session="narrative")` 위임 + `_NARRATIVE_TAG_POOL` 4종 |
| 문체 | `SYSTEM_INSTRUCTION` 단일 | `tone_policy` narrative 3셀 (냉정 해부 / 차분 해설 / 여유 해설) |
| temperature | 0.7 고정 | 0.62~0.92 랜덤 |
| 문단 구조 | 3~5줄 산문 고정 | 스타일 3종(prose/bullet/lead) × 줄수 6종 |
| 이미지 | full 대시보드 1종 고정 | 후보 5종 가중 로테이션 + 직전 2회 제외 |
| VS카드 화풍 | 영문 프롬프트 1종 고정 | Style 절 4종 랜덤 |
| VS카드 HTML | 팔레트 1종 | 팔레트 3종 로테이션 |
| TG 헤더 | `📝 AI 시장 해설` 고정 | 풀 4종 랜덤 |

### 이미지 후보 가중치

| variant | 생성 경로 | 가중치 |
|---|---|---|
| `full_dashboard` | `build_html_dashboard(variant="full")` | 25% |
| `compact_dashboard` | `build_html_dashboard(variant="compact")` | 25% |
| `vs_card` | `vs_card_generator.generate_vs_card` | 20% |
| `card_market` | `card_news_generator.generate_single_card(card_no=1)` | 15% |
| `none` (이미지 없음) | — | 15% |

이력: `data/published/narrative_visual_history.json` (최근 7건).
직전 2회 사용 variant는 후보에서 제외 → 연속 반복 차단.
선택 후보 생성 실패 시 남은 후보로 순차 폴백, 전부 실패하면 텍스트 전용 발행.

## 4. 결함 수정

| # | 결함 | 조치 |
|---|---|---|
| D-1 | 동일 본문 2연속 발행 | Step 6-TG의 X 발행 3줄 제거 |
| D-2 | 3연발 딜레이 0초 | 스레드 경로로 전환해 기존 랜덤 대기 사용 |
| D-3 | `history.json` 캐시 부재로 `duplicate_checker` 무동작 | NB-4: morning / full / narrative 3개 job에 `publish-history` 캐시 신설 |
| D-4 | narrative job 한국어 폰트 미설치 | NB-1: `fonts-nanum` step 추가 + NB-5로 artifact에 `data/images/` 포함(실물 검증 경로) |
| D-5 | tone_policy narrative 미적용 | narrative 3셀 추가, `_SUPPORTED_SESSIONS` 도입 |
| D-7 | `vs_card_generator` VERSION 부재 | VERSION 신설 (+ `card_news_generator`, `duplicate_checker`, `run_view`도 신설) |
| F-8 | `_session_label` / `format_image_tweet`에 narrative 키 누락 → "Market Snapshot" 표기 | 양쪽 모두 narrative 라벨 추가 |
| R-1 | narrative가 morning과 `regime_hash` 충돌 → NB-4 적용 시 상시 차단 | `is_duplicate(..., skip_regime_hash=True)` 추가. **content_hash 검사는 유지** |

### 작업 중 발견한 기존 테스트 결함 (본 작업과 무관)

`test_ai_tone_modules_pilot.py::test_version_loaded`가 `TONE_VERSION == "1.0.0"`을
하드코딩해 **tone_policy v1.1.0 이후 계속 실패 상태**였다. 원본 ZIP에서도 재현된다.
버전 계열만 검사하도록 수정했다. `test_x_formatter_v150.py`도 동일 패턴이라 함께 수정.

## 5. 변경 파일 (13개)

| 파일 | 버전 |
|---|---|
| `engines/narrative_engine.py` | 1.1.0 → **2.0.0** |
| `publishers/narrative_visual.py` | **신규 1.0.0** |
| `core/tone_policy.py` | 1.2.0 → **1.3.0** |
| `run_view.py` | (없음) → **1.31.0** |
| `publishers/x_formatter.py` | 1.5.0 → **1.5.1** |
| `publishers/dashboard_html_builder.py` | v3.1.0 → **v3.2.0** |
| `comic/vs_card_generator.py` | (없음) → **1.1.0** |
| `comic/card_news_generator.py` | (없음) → **1.1.0** |
| `core/duplicate_checker.py` | (없음) → **1.1.0** |
| `.github/workflows/main.yml` | rev.2026-09-06 |
| `tests/test_narrative_v2.py` | 신규 |
| `tests/test_ai_tone_modules_pilot.py` | 수정 |
| `tests/test_x_formatter_v150.py` | 수정 |

## 6. main.yml 패치

| 패치 | 대상 job | 내용 |
|---|---|---|
| NB-1 | narrative | `fonts-nanum` 설치 (캐시 키 `apt-fonts-nanum-v1` morning과 공유) |
| NB-2 | narrative | Anti-bot delay `RANDOM % 100` → `RANDOM % 900` |
| NB-3 | narrative | `timeout-minutes` 15 → 30 |
| NB-4 | morning / full / narrative | `publish-history` 캐시 + narrative만 `narrative-visual` 캐시 |
| NB-5 | narrative | artifact에 `data/images/` 추가 |

**cron 문자열과 job `if` 문자열은 일절 변경하지 않았다** (원본 대비 `diff` 무차이 확인).
**NB-2와 NB-3은 분리 배포 금지** — delay만 올리면 타임아웃이 확정된다.

## 7. 검증 결과

| 항목 | 결과 |
|---|---|
| `ruff check --select F,E9` 2회 | 베이스라인 26건 → **25건** (신규 유입 0, F401 1건 해소) |
| `pytest` 2회 연속 | **158 passed** (신규 30 케이스 포함) |
| ZIP 재추출 후 재실행 2회 | **105 passed** |
| `py_compile` 전 변경 파일 | OK |
| main.yml YAML 파싱 | OK |
| cron / job-if 불변 | 원본과 `diff` 무차이 |
| 통합 스모크 | ToneSpec 결선 / 프롬프트 4요소 주입 / 2트윗 분할 / 본문 중복 없음 / 3일 연속 variant 미반복 전부 OK |

## 8. 배포 후 확인 항목

1. narrative artifact의 `data/images/*.png`에서 **한글이 정상 렌더되는지** (D-4 실물 검증)
2. 발행 로그에 `[NarrVisual] 선택: <variant>`가 매일 다르게 찍히는지
3. `[Narrative] variant: style=... temp=...` 로그로 텍스트 변칙 동작 확인
4. X 발행 건수가 1스레드(최대 2트윗)로 줄었는지
5. 2주 후 노출·인게이지먼트 지표 비교 — 스레드 전환의 영향 평가
