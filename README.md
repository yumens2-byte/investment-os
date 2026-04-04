# Investment OS (v1.24.0)

미국 금융시장 데이터 자동 수집 → 19개 시그널 + Gemini AI 분석 → X/텔레그램 자동 발행 시스템.

## 핵심 기능

- **19개 시그널** 기반 시장 분석 (Tier 1/2/3)
- **8종 실시간 Alert** (VIX/SPY/OIL/FED/CRISIS + ETF랭킹/레짐전환/VIX카운트다운)
- **ETF 6종** 상세 전략 + SMA5/SMA20 모멘텀 보정 (E-1)
- **Gemini AI 확장** — AI 트윗(C-1), 뉴스 Top5(C-9), 레짐 크로스체크(C-8), 다국어(C-11), Vision 차트(C-13)
- **주말 콘텐츠** — 주간 리뷰/다음 주 프리뷰 + 투자 교육(C-4) + 금융 상식(C-4B)
- **코믹 콘텐츠** — VS 배틀카드, 카드뉴스 3장, 밈 생성
- X + 텔레그램 무료/유료 채널 자동 발행
- Supabase 일일 데이터 적재 (4테이블)
- GitHub Actions 9세션 + 4 yml 완전 자동 운영

## 기술 스택

| 영역 | 기술 |
|------|------|
| Runtime | Python 3.11 + GitHub Actions |
| AI | Gemini 2.5 Flash-Lite (3키 자동전환) + Claude API (뉴스 요약) |
| 데이터 | yfinance + FRED API + RSS 9소스 + Fear & Greed |
| DB | Supabase (PostgreSQL) — SQLAlchemy + NullPool |
| 이미지 | matplotlib + Playwright (HTML 대시보드) |
| 발행 | X API (Pay-per-use) + Telegram Bot API |

## 프로젝트 구조

```
investment-os/
├── main.py                    # CLI 엔트리포인트
├── run_market.py              # 시장 분석 파이프라인 (Step 1~8)
├── run_view.py                # 발행 파이프라인 (Step 0~7)
├── run_alert.py               # Alert 파이프라인
├── run_weekend.py             # 주말 콘텐츠 (토/일)
├── scheduler.py               # 세션 스케줄러
│
├── collectors/                # 데이터 수집
│   ├── yahoo_finance.py       # 시장 스냅샷 + ETF + FX + Crypto + SMA
│   ├── fred_client.py         # FRED 거시경제 지표
│   ├── rss_extended.py        # RSS 9소스 뉴스 수집
│   ├── news_analyzer.py       # Gemini 뉴스 Top5 분석 (C-9)
│   ├── news_summarizer.py     # Claude 뉴스 3줄 요약
│   └── fear_greed.py          # Fear & Greed Index
│
├── engines/                   # 분석 엔진
│   ├── macro_engine.py        # Tier 1/2/3 시그널 (19개)
│   ├── regime_engine.py       # 레짐 판단 + Gemini 크로스체크 (C-8)
│   ├── etf_engine.py          # ETF 점수/랭킹/배분 + SMA 보정 (E-1)
│   ├── risk_engine.py         # 리스크/시그널/포지션 사이징
│   ├── alert_engine.py        # 8종 Alert 감지
│   ├── narrative_engine.py    # Gemini 시장 내러티브
│   ├── history_engine.py      # 오늘의 시장 역사 (C-5)
│   └── chart_analyzer.py      # Gemini Vision 차트 분석 (C-13)
│
├── publishers/                # 발행 모듈
│   ├── x_publisher.py         # X API 발행 (tweet/thread/image)
│   ├── x_formatter.py         # AI 트윗 생성 (C-1) + 레짐 연동 톤 (F-4)
│   ├── telegram_publisher.py  # TG 무료/유료/다국어 채널 발행
│   ├── translator.py          # 다국어 번역 (C-11)
│   ├── image_generator.py     # 이미지 생성 라우터
│   ├── dashboard_builder.py   # matplotlib 대시보드 (morning/intraday/close)
│   ├── dashboard_html_builder.py  # HTML/Playwright 대시보드 (full)
│   ├── paid_report_formatter.py   # 유료 ETF 상세 리포트
│   ├── alert_formatter.py     # Alert 포맷 + AI 해설 (C-2)
│   └── weekly_pdf_builder.py  # 주간 PDF 리포트
│
├── comic/                     # 코믹 콘텐츠
│   ├── card_news_generator.py # 카드뉴스 3장
│   ├── vs_card_generator.py   # VS 배틀카드
│   ├── meme_generator.py      # 밈 생성
│   └── html_image_engine.py   # HTML→PNG 변환
│
├── weekend/                   # 주말 콘텐츠
│   ├── weekly_review.py       # 토요일 — 주간 리뷰
│   ├── next_week_preview.py   # 일요일 — 다음 주 프리뷰
│   ├── education_series.py    # 토요일 — 투자 교육 (C-4, 30개)
│   └── finance_basics.py      # 일요일 — 금융 상식 (C-4B, 25개)
│
├── core/                      # 핵심 유틸리티
│   ├── gemini_gateway.py      # Gemini 3키 자동전환 + Vision
│   ├── json_builder.py        # core_data.json 조립
│   ├── validator.py           # 데이터/출력 검증
│   ├── weekly_tracker.py      # 주간 데이터 축적
│   └── dlq.py                 # Dead Letter Queue
│
├── db/                        # 데이터베이스
│   ├── supabase_client.py     # Supabase 연결
│   └── daily_store.py         # 일일 데이터 적재 (4테이블)
│
├── config/                    # 설정
│   ├── settings.py            # 버전, 환경변수
│   ├── us_market_holidays.py  # 미장 휴무일 (2026/2027)
│   └── event_calendar.py      # 경제 이벤트 캘린더
│
└── .github/workflows/         # CI/CD
    ├── main.yml               # 평일 6세션 (morning~narrative)
    ├── weekend_content.yml    # 주말 토/일
    ├── comic_daily.yml        # 일일 코믹
    └── comic_weekly.yml       # 주간 코믹
```

## 운영 스케줄 (KST)

### 평일

| 시간 | 세션 | 내용 |
|------|------|------|
| 06:30 | morning | 시장 분석 + AI 트윗 + 대시보드 |
| 11:30 | narrative | AI 시장 해설 + 시장 역사 (C-5) |
| 18:30 | intraday | 장중 업데이트 |
| 05:30+1 | close | 종가 요약 |
| 14:30 | full | 풀 대시보드 + 유료 리포트 + Vision (C-13) |
| 20:00(목) | weekly | 주간 스레드 + PDF 리포트 |
| 10분 | alert | VIX/SPY/OIL 실시간 모니터링 |

### 주말

| 시간 | 내용 |
|------|------|
| 토 10:00 | 주간 리뷰 + 투자 교육 (C-4) |
| 일 10:00 | 다음 주 프리뷰 + 금융 상식 (C-4B) |

## 실행

```bash
# 평일 세션
python main.py run all --session morning --mode tweet
python main.py run all --session full --mode tweet
python main.py run all --session narrative --mode tweet
python main.py run all --session close --mode thread

# Alert
python main.py alert

# 주말
python main.py weekend --day sat
python main.py weekend --day sun

# 주말 강제 실행 (FORCE_RUN)
FORCE_RUN=true python main.py run all --session morning --mode tweet
```

## Gemini AI 확장 (C 시리즈)

| # | 기능 | RPD |
|---|------|-----|
| C-1 | AI 트윗 (레짐 연동 톤) | 기존 |
| C-2 | Alert AI 해설 | 기존 |
| C-3 | ETF 배분 근거 자연어 | 기존 |
| C-4 | 투자 교육 시리즈 (매주 토, 30개) | +1 |
| C-4B | 금융 기본 상식 (매주 일, 25개) | +1 |
| C-5 | 오늘의 시장 역사 | +1 |
| C-8 | 레짐 Gemini 크로스체크 | +1 |
| C-9 | 뉴스 Top5 + impact_score | 기존 |
| C-11 | 다국어 (한→영/일) | +16 |
| C-12 | AI 스레드 자동 생성 | +1 |
| C-13 | Gemini Vision 차트 분석 | +2 |

## 고도화 (완료)

| # | 기능 | 내용 |
|---|------|------|
| F-4 | 레짐 연동 톤 | HIGH→긴급/경계, MEDIUM→진지/신중, LOW→낙관/유머 |
| E-5 | Bearish 감성 차단 | Gemini bearish 시 Risk-On 차단 (5번째 가드) |
| E-1 | ETF SMA 모멘텀 | SMA5/SMA20 골든/데드크로스 ±1점 보정 |

## 테스트

```bash
# 필수 (배포 전)
python test_tier1_signals.py      # 145건
python test_e2e_pipeline.py       # 68건

# 선택
python test_tier2_signals.py      # 78건
python test_tier3_signals.py      # 30건
python test_b5_b6.py              # 53건
python test_b7_etf_rationale.py   # 30건

# 전체
python full_test.py               # 145건 (통합)
python main.py test --round all   # 42건 (파일럿)
```

## 환경변수

```env
# Gemini (3키 자동전환)
GEMINI_API_KEY=
GEMINI_API_SUB_KEY=
GEMINI_API_SUB_SUB_KEY=

# Claude (뉴스 요약)
ANTHROPIC_API_KEY=

# X (Twitter)
X_API_KEY=
X_API_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_TOKEN_SECRET=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_FREE_CHANNEL_ID=
TELEGRAM_PAID_CHANNEL_ID=

# Supabase
SUPABASE_URL=
SUPABASE_KEY=

# FRED
FRED_API_KEY=

# 운영
DRY_RUN=true
FORCE_RUN=false
MULTILINGUAL_ENABLED=true
```

## 문서

- [시스템 설계서](docs/design.md)
- [운영자 매뉴얼](docs/operator_manual.md)
- [사용자 매뉴얼](docs/user_manual.md)

## 라이선스

Private Repository — EDT Investment Team
