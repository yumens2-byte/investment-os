# Investment OS

미국 시장 데이터를 수집·분석해 X와 Telegram에 투자 분석 콘텐츠를 자동 발행하는 파이프라인입니다.
전 과정이 GitHub Actions 위에서 무인 운영됩니다.

| 항목 | 값 |
|---|---|
| 시스템 버전 | `config/settings.py`의 `SYSTEM_VERSION` 상수가 유일한 정본 |
| 코드네임 | EDT Investment |
| Runtime | Python 3.11 + GitHub Actions (ubuntu-latest) |
| 저장소 | Public |

---

## 파이프라인 구조

```
수집(collectors) → 분석(engines) → core_data.json → 검증(core/validator)
                                                        │
                                        ┌───────────────┴───────────────┐
                                   중복 검사                        이미지 생성
                              (core/duplicate_checker)        (publishers/*_builder)
                                        └───────────────┬───────────────┘
                                                        ▼
                                            발행 (X / Telegram)
```

단일 진입점은 `main.py` 하나입니다.

```bash
python main.py run market --session morning      # 수집 + 분석만
python main.py run view   --mode tweet           # 검증 + 발행만
python main.py run all    --session morning --mode tweet
python main.py alert                             # 알림 세션
python main.py weekend --day auto                # 주말 콘텐츠
python main.py test --round all                  # 파일럿 테스트
python main.py status                            # 최근 실행 요약
```

---

## 발행 세션

| 세션 | 실행 시각 (KST) | cron (UTC) | 상태 | 내용 |
|---|---|---|---|---|
| `morning` | 월~금 06:36 | `36 21 * * 0-4` | 운영 | 장 마감 후 모닝 브리핑 + 대시보드 |
| `narrative` | 월~금 11:36 | `36 2 * * 1-5` | 운영 | AI 시장 해설 스레드 + 로테이션 이미지 |
| `full` | 월~금 18:36 | `36 9 * * 1-5` | 운영 | 풀 대시보드 + 유료 리포트 |
| `alert` | 월~금 22:00~06:50 | `*/10 13-14`, `*/10 15-21` | 운영 | 임계값 기반 실시간 알림 16종 |
| `intraday` / `close` / `weekly` | — | 비활성 | 수동 | `workflow_dispatch` 전용 |

> **cron 작성 규칙**: GitHub Actions cron은 100% UTC 기준입니다.
> UTC 15:00 이후 cron은 KST 기준 다음 날이 되므로 **요일을 -1일 시프트**해야 합니다.
> cron 문자열을 바꿀 때는 ① `on.schedule` ② 해당 job의 `if` 비교 문자열 ③ `main.yml` 헤더 매핑표
> **3곳을 반드시 동시 수정**해야 합니다. ②를 누락하면 해당 job은 에러 없이 완전히 미발화합니다.

---

## 알림 종류 (16종)

`VIX` · `VIX_COUNTDOWN` · `SPY` · `OIL` · `FED_SHOCK` · `CRISIS` · `ETF_RANK` · `REGIME_CHANGE`
`PCR_EXTREME` · `CRYPTO_BASIS` · `MOVE_SPIKE` · `SMA200_BREAK` · `STAGFLATION` · `YIELD_SPREAD_DEEP`
`CPI_HOT` · `SOFR_STRESS`

---

## 디렉터리 구조

| 디렉터리 | 역할 | 파일 수 |
|---|---|---|
| `collectors/` | 외부 데이터 수집 (FRED, Yahoo, RSS, Fear&Greed, Crypto 등) | 15 |
| `engines/` | 분석 엔진 (macro / regime / risk / etf / alert / narrative / viral) | 25 |
| `publishers/` | 발행 및 포맷 (X, Telegram, 대시보드, 스레드, 번역) | 19 |
| `core/` | 공통 인프라 (validator, duplicate_checker, gemini_gateway, tone_policy, DLQ) | 16 |
| `config/` | 설정, 휴장일, 이벤트 캘린더 | 5 |
| `comic/` | 코믹 / 카드뉴스 / VS 카드 이미지 생성 | 8 |
| `weekend/` | 주말 전용 콘텐츠 | 7 |
| `db/` | Supabase 적재 계층 | 7 |
| `tests/` | 회귀 테스트 | 24 |

---

## 주요 모듈 버전

| 모듈 | 버전 | 역할 |
|---|---|---|
| `run_view.py` | 1.31.0 | 검증 → 중복검사 → 이미지 → 발행 |
| `engines/narrative_engine.py` | 2.0.0 | AI 시장 해설 생성 + 스레드 구성 |
| `engines/macro_engine.py` | 1.11.0 | 매크로 시그널 산출 |
| `engines/alert_engine.py` | 1.1.2 | 알림 16종 판정 |
| `core/gemini_gateway.py` | 3.1.0 | Gemini 4키 로테이션 + DLQ |
| `core/tone_policy.py` | 1.3.0 | 세션 × 리스크 톤 매트릭스 |
| `core/duplicate_checker.py` | 1.1.0 | 콘텐츠 / 레짐 해시 중복 차단 |
| `publishers/x_formatter.py` | 1.5.1 | X 본문 포맷 + 해시태그 |
| `publishers/x_publisher.py` | 1.1.0 | tweepy 발행 + 랜덤 딜레이 |
| `publishers/dashboard_html_builder.py` | v3.2.0 | HTML + Playwright 대시보드 |
| `publishers/narrative_visual.py` | 1.0.0 | narrative 이미지 후보 로테이션 |
| `publishers/thread_builder.py` | 2.0.0 | 스레드 분할 + 후킹 / CTA |
| `collectors/yahoo_finance.py` | 1.9.0 | 시세 수집 (requests → yfinance 폴백) |
| `collectors/fred_client.py` | 1.6.0 | 매크로 지표 수집 |

---

## 데이터 소스

| 구분 | 소스 |
|---|---|
| 매크로 | FRED API |
| 시세 | Yahoo Finance (requests 1순위 → yfinance 2순위) |
| 심리 | CNN Fear & Greed |
| 뉴스 | RSS 11개 소스 + AI 요약 |
| 소셜 | LunarCrush, Reddit, YouTube RSS |
| 파생 | Put/Call Ratio, Crypto Funding Rate (OKX → Bybit 폴백) |
| AI | Gemini 2.5 Flash-Lite / Flash / Pro, Claude API |

ETF 유니버스: `QQQM` `XLK` `SPYM` `XLE` `ITA` `TLT`

---

## 워크플로

| 파일 | 이름 | 용도 |
|---|---|---|
| `main.yml` | Investment OS Auto Publish | 메인 발행 파이프라인 (전 세션) |
| `ci_alert_tests.yml` | Alert Regression Tests | 알림 회귀 357 케이스, 실패 시 머지 차단 |
| `comic_daily.yml` / `comic_weekly.yml` / `comic_novel.yml` | Comic Pipeline | 코믹 콘텐츠 자동화 |
| `viral_content.yml` / `viral_daily_report.yml` / `viral_performance.yml` | Viral | 바이럴 콘텐츠 및 성과 추적 |
| `prediction_league.yml` | Prediction League & Data Quiz | 예측 리그 / 퀴즈 |
| `weekend_content.yml` | Weekend Content | 주말 콘텐츠 |
| `edt_snapshot.yml` | EDT Market Snapshot | 스냅샷 적재 |

---

## 환경 변수

**필수 시크릿**

```
FRED_API_KEY
X_API_KEY  X_API_SECRET  X_ACCESS_TOKEN  X_ACCESS_TOKEN_SECRET
TELEGRAM_BOT_TOKEN  TELEGRAM_FREE_CHANNEL_ID  TELEGRAM_PAID_CHANNEL_ID
GEMINI_API_KEY  GEMINI_API_SUB_KEY  GEMINI_API_SUB_SUB_KEY  GEMINI_API_SUB_PAY_KEY
ANTHROPIC_API_KEY
SUPABASE_URL  SUPABASE_KEY
```

**선택 / 튜닝**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DRY_RUN` | `true` | `true`면 실제 발행 차단 (검증용) |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `X_THREAD_DELAY_MIN_SEC` | `9` | 스레드 포스트 간 최소 대기 |
| `X_THREAD_DELAY_MAX_SEC` | `34` | 스레드 포스트 간 최대 대기 |
| `X_THREAD_DELAY_BUDGET_SEC` | `180` | 스레드 전체 대기 예산 |
| `CORE_DATA_MAX_AGE_HOURS` | — | core_data 신선도 임계 |
| `LUNAR_CRUSH_API_KEY` | — | 소셜 지표 (무료 플랜) |
| `MULTILINGUAL_ENABLED` | — | 다국어 발행 토글 |
| `YOUTUBE_CHANNELS` | — | 유튜버 컨센서스 수집 대상 채널 |
| `FORCE_RUN` | — | 휴장일 가드를 무시하고 강제 실행 |

---

## 로컬 실행

```bash
pip install -r requirements.txt
playwright install chromium --with-deps    # 대시보드 렌더링용
sudo apt-get install -y fonts-nanum        # 한글 렌더링 필수

export DRY_RUN=true                        # 실제 발행 차단
python main.py run all --session morning --mode tweet
```

`DRY_RUN=true`면 X / Telegram 발행 없이 로그만 출력합니다. 로컬 검증 시 반드시 켜 두십시오.

---

## 테스트

```bash
pytest tests/ -q                           # 전체
pytest tests/test_narrative_v2.py -v       # narrative 회귀
ruff check .                               # 린트

# 알림 회귀 (ci_alert_tests.yml과 동일, 357 케이스)
python tests/test_alert_unit_boundaries.py
python tests/test_alert_integration_full.py
python tests/test_alert_policy_matrix.py
python tests/test_b21a_x_image_guard.py
python tests/test_b21a_integration_sim.py
```

---

## 개발 규약

- 모든 모듈은 파일 상단에 `VERSION` 상수를 두고, 수정 시 갱신하며 실행 시작 로그에 출력합니다.
- **시스템 버전은 `config/settings.py`의 `SYSTEM_VERSION`이 유일한 정본입니다.**
  이 값은 대시보드 이미지 푸터와 `core_data.json`의 `version` 필드에 그대로 렌더되므로,
  README·문서·테스트에 버전 숫자를 하드코딩하지 않습니다. 상수를 상향할 때는
  `full_test.py`와 `pilot_test.py`의 단언 2곳을 반드시 함께 수정해야 합니다.
- 배포 전 `ruff check` + `pytest`를 **각 2회 연속 PASS**시킵니다.
- 부분 diff보다 **전체 파일 교체**를 사용합니다 (웹 에디터 붙여넣기 들여쓰기 오류 방지).
- 캐시는 **CACHE-FREEZE 규약**을 따릅니다. `actions/cache/restore@v4`는 실행 스텝 **이전**,
  `actions/cache/save@v4`는 실행 스텝 **이후 + `if: always()`**.
  데이터 성격 캐시에 고정 키 + full action 패턴을 쓰면 이력이 동결되어 유실됩니다.
- `DRY_RUN` 표현식은 `${{ github.event_name == 'workflow_dispatch' && inputs.dry_run || 'true' }}`
  패턴을 사용합니다. schedule 트리거에는 `workflow_dispatch.inputs`가 없어 폴백이 필요합니다.
- `publishers/dashboard_builder.py`(matplotlib)는 수정하지 않습니다.
  HTML 대시보드는 `publishers/dashboard_html_builder.py`로 분리되어 있습니다.

### 자동화 계정 운영 원칙

- 동일 일정 · 동일 문구 발행을 금지합니다. 시각 · 해시태그 · 본문 · 이미지 레이아웃을 모두 무작위화합니다.
- 발행 간격에 고정 `sleep`을 쓰지 않고 랜덤 지연을 적용합니다.
- 각 job에 랜덤 딜레이 스텝을 두어 cron 시각에 발행이 몰리지 않게 합니다.

---

## 문서

상세 설계 · 운영 문서는 별도 Private 저장소에서 관리합니다:
[investment-os-docs](https://github.com/yumens2-byte/investment-os-docs)

- 시스템 설계서 (`/docs/design.md`)
- 시스템 설계서 추가 (`/design/RUN_MARKET_INTEGRATI.md`)
- 시스템 설계서 추가 (`/design/ANALYSIS_DESIGN.md`)
- 운영자 매뉴얼 (`/docs/operator_manual.md`)
- 사용자 매뉴얼 (`/docs/user_manual.md`)

---

## 면책

이 저장소가 생성하는 모든 콘텐츠는 **정보 제공 목적**이며 투자 권유가 아닙니다.
투자 판단과 그 결과에 대한 책임은 이용자 본인에게 있습니다.

## 라이선스

Public Repository — EDT Investment Team
