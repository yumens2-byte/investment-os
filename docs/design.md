# Investment OS — 시스템 설계서 (v1.24.0)

> 작성일: 2026-04-04 | 작성자: GTT팀 | 상태: 운영 중 (213/213 PASS)
> GitHub: https://github.com/yumens2-byte/investment-os (Private)

---

## 1. 프로젝트 목표

| 항목 | 내용 |
|---|---|
| **목표** | 미국 금융시장 데이터 수집 → 19개 시그널 + Gemini AI 분석 → X/텔레그램 자동 발행 |
| **현재 상태** | GitHub Actions 9세션 + 4 yml 완전 자동 운영 |
| **스택** | Python 3.11 + GitHub Actions + Gemini 2.5 Flash-Lite + Supabase |
| **발행 채널** | X (Twitter) + 텔레그램 무료/유료 채널 |
| **AI 엔진** | Gemini 3키 자동전환 (main/sub/sub2) + Claude (뉴스 요약) |

---

## 2. 버전 이력

| 버전 | 일자 | 핵심 변경 |
|---|---|---|
| **v1.24.0** | 2026-04-04 | C-8 레짐크로스체크 + C-12 AI스레드 + C-1 AI트윗고도화 + C-5 시장역사 + C-9 뉴스Top5 + C-11 다국어 + C-13 Vision + C-4 투자교육/금융상식 + E-1 SMA모멘텀 + E-5 Bearish차단 + F-4 레짐톤 |
| v1.22.0 | 2026-04-03 | B-20 주말콘텐츠 + B-21 코믹확장 + B-24 휴무일체크 + B-25 Supabase적재 + google-genai SDK |
| v1.20.0 | 2026-04-02 | Gemini 3키전환 + DLQ + 이미지생성 + 코믹파이프라인 |
| v1.15.0 | 2026-04-01 | B-5/B-6/B-7 + signals 저장 + TG 무료/유료 연결 |
| v1.14.0 | 2026-04-01 | Tier 3 시그널 (AI모멘텀/Nasdaq상대/은행스트레스) |
| v1.13.0 | 2026-04-01 | Tier 2 시그널 (Breadth/VolTerm/ICSA/T5YIFR/EEM) |
| v1.12.0 | 2026-04-01 | Tier 1 시그널 (F&G/BTC/모멘텀/XLF-GLD) |
| v1.11.0 | 2026-03-30 | 텔레그램 무료/유료 + Alert 6종 + VIX 카운트다운 |

---

## 3. 파이프라인 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                     GitHub Actions (4 yml)                   │
│  main.yml │ weekend_content.yml │ comic_daily │ comic_weekly │
└─────┬───────────────┬────────────────┬──────────────────────┘
      │               │                │
      ▼               ▼                ▼
┌─────────────┐ ┌──────────┐ ┌─────────────┐
│ run_market  │ │run_weekend│ │comic/pipeline│
│ (Step 1~8)  │ │ (sat/sun) │ │ (daily/wkly)│
└──────┬──────┘ └────┬─────┘ └──────┬──────┘
       │              │               │
       ▼              ▼               ▼
┌─────────────────────────────────────────────┐
│              core_data.json                  │
│ (market_snapshot + signals + regime + ETF)   │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│               run_view                       │
│  Step 0: DLQ 재처리                          │
│  Step 1: core_data 로드                      │
│  Step 2: Validation                          │
│  Step 3: AI 트윗 생성 (C-1)                  │
│  Step 4: 중복 검사                            │
│  Step 5: 대시보드 이미지 생성                  │
│  Step 6: X 발행 + TG 발행                    │
│  Step 6-ML: 다국어 발행 (C-11)               │
│  Step 7: 이력 기록                            │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
        X API    TG Bot   Supabase
```

---

## 4. 데이터 수집 (Step 1)

| 소스 | 모듈 | 수집 항목 |
|------|------|----------|
| Yahoo Finance | `yahoo_finance.py` | SPY/VIX/US10Y/Oil/DXY + ETF 6종 + BTC/ETH + FX 3쌍 + SMA5/SMA20 |
| FRED | `fred_client.py` | 기준금리/HY스프레드/수익률곡선/실업수당/기대인플레 |
| RSS 9소스 | `rss_extended.py` | 금융 뉴스 헤드라인 (Yahoo/CNBC/MW/Investing/Google 등) |
| Gemini | `news_analyzer.py` | Top5 이슈 + impact_score + 종합 감성 (C-9) |
| Claude | `news_summarizer.py` | 뉴스 3줄 요약 |
| alternative.me | `fear_greed.py` | Fear & Greed Index |

---

## 5. 분석 엔진 (Step 2~5)

### 5.1 Macro Engine — 19개 시그널

| Tier | 시그널 | 지표 |
|------|--------|------|
| 기본 | VIX, US10Y, Oil, DXY, Credit | 5개 |
| Tier 1 | F&G, BTC, 모멘텀, XLF/GLD | 4개 |
| Tier 2 | Breadth, VolTerm, Claims, InflExp, EM | 5개 |
| Tier 3 | AI모멘텀, Nasdaq상대, Banking | 3개 |
| 뉴스 | RSS 감성, Gemini 감성 | 2개 |

### 5.2 Regime Engine — Risk-On 가드 5중

1. F&G Extreme Fear (≤1)
2. VIX Elevated (25+)
3. Oil High/Shock ($95+)
4. Vol Term Backwardation
5. **Gemini Bearish (E-5)**

### 5.3 ETF Engine — SMA 보정 (E-1)

```
Base Score (Regime) + 당일 변동률 보정 + SMA5/SMA20 트렌드 보정
  → golden_cross: +1점
  → dead_cross: -1점
```

---

## 6. Gemini AI 확장 (C 시리즈)

| # | 기능 | 모듈 | RPD |
|---|------|------|-----|
| C-1 | AI 트윗 + 레짐톤(F-4) | `x_formatter.py` | 기존 |
| C-2 | Alert AI 해설 | `alert_formatter.py` | 기존 |
| C-3 | ETF 배분 근거 | `paid_report_formatter.py` | 기존 |
| C-4 | 투자 교육 (매주 토, 30개) | `education_series.py` | +1 |
| C-4B | 금융 상식 (매주 일, 25개) | `finance_basics.py` | +1 |
| C-5 | 오늘의 시장 역사 | `history_engine.py` | +1 |
| C-8 | 레짐 크로스체크 | `regime_engine.py` | +1 |
| C-9 | 뉴스 Top5 + impact_score | `news_analyzer.py` | 기존 |
| C-11 | 다국어 (한→영/일) | `translator.py` | +16 |
| C-12 | AI 스레드 | `x_formatter.py` | +1 |
| C-13 | Vision 차트 분석 | `chart_analyzer.py` | +2 |

---

## 7. Supabase 데이터 적재

| 테이블 | 내용 | UPSERT |
|--------|------|--------|
| daily_snapshots | 시장 스냅샷 (Yahoo + FRED + F&G) | snapshot_date |
| daily_analysis | 분석 결과 (레짐/시그널/ETF) | analysis_date |
| daily_news | 뉴스 분석 (RSS + Gemini) | news_date |
| daily_alerts | Alert 발동 이력 | alert_date |

---

## 8. RPD 예산 (Gemini Free Tier)

```
프로젝트 3개 × 20 RPD = 60 RPD/일
평일 소비: ~21 RPD/일 → 여유 39 RPD
```

주의: RPD는 API 키 단위가 아니라 **프로젝트 단위**.
같은 프로젝트에서 키 3개 만들면 20 RPD 공유.
다른 프로젝트(다른 Google 계정)에서 만들어야 독립 할당.

---

## 9. 테스트 매트릭스

| 파일 | 건수 | 카테고리 |
|------|------|----------|
| test_tier1_signals.py | 145 | Tier 1 시그널 전수 |
| test_e2e_pipeline.py | 68 | E2E 파이프라인 |
| test_tier2_signals.py | 78 | Tier 2 시그널 전수 |
| test_tier3_signals.py | 30 | Tier 3 시그널 전수 |
| test_b5_b6.py | 53 | Alert 엔진 |
| test_b7_etf_rationale.py | 30 | ETF 근거 생성 |
| pilot_test.py | 42 | 통합 2라운드 |
| full_test.py | 145 | 전체 통합 |

**배포 전 필수**: `test_tier1_signals.py` (145건) + `test_e2e_pipeline.py` (68건)
