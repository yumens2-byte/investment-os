# Investment OS — 시스템 설계서 (v1.15.0)

> 작성일: 2026-04-01 | 작성자: GTT팀 | 상태: 운영 중 (403/403 PASS)
> GitHub: https://github.com/yumens2-byte/investment-os (Private)

---

## 1. 프로젝트 목표

| 항목 | 내용 |
|---|---|
| **목표** | 미국 금융시장 데이터 수집 → 19개 시그널 리스크 분석 → X/텔레그램 자동 발행 |
| **현재 상태** | GitHub Actions 6세션 완전 자동 운영 |
| **스택** | Python 3.11 + GitHub Actions + yfinance + FRED + feedparser |
| **발행 채널** | X (Twitter) + 텔레그램 무료/유료 채널 |

---

## 2. 버전 이력

| 버전 | 일자 | 핵심 변경 |
|---|---|---|
| **v1.15.0** | 2026-04-01 | B-5/B-6/B-7 + signals 저장 버그픽스 + TG 무료/유료 발송 연결 |
| v1.14.0 | 2026-04-01 | Tier 3 시그널 (AI모멘텀/Nasdaq상대/은행스트레스) |
| v1.13.0 | 2026-04-01 | Tier 2 시그널 (Breadth/VolTerm/ICSA/T5YIFR/EEM) |
| v1.12.0 | 2026-04-01 | Tier 1 시그널 (F&G/BTC/모멘텀/XLF-GLD) |
| v1.11.0 | 2026-03-30 | 텔레그램 무료/유료 + Alert 6종 + VIX 카운트다운 |
| v1.5.0 | 2026-03-28 | 다중 RSS 9소스 전환 |

---

## 3. 시스템 아키텍처

### 3.1 전체 흐름

```
[수집 레이어]                  [분석 레이어]              [출력 레이어]
yfinance (시장+ETF+FX         Macro Engine              core_data.json
  +Tier2: RSP/VIX3M            (19시그널→6축Score)        ├ signals (19개)
  /EEM/SOXX/KRE)              Regime Engine               ├ market_score
FRED (6시리즈)       ───▶     ETF Engine          ───▶    ├ etf_strategy
다중 RSS (9소스)              Risk Engine                  └ etf_rationale(B-7)
Fear & Greed API              Alert Engine (8종)              │
Crypto (BTC/ETH)                                             ▼
                              [B-5/B-6/B-7]            [run_view.py]
                              rank_tracker              X + TG 무료/유료
                              regime_tracker            [run_alert.py]
                              signal_diff               Alert X + TG
                              etf_rationale
```

### 3.2 핵심 파일

| 카테고리 | 파일 | 줄 수 | 역할 |
|---------|------|-------|------|
| 수집 | collectors/yahoo_finance.py | 386 | 시장+ETF+FX+Tier2 수집 |
| 수집 | collectors/fred_client.py | 176 | FRED 6시리즈 (기존4+ICSA+T5YIFR) |
| 엔진 | engines/macro_engine.py | 923 | 19개 Signal + Market Score v1.3 |
| 엔진 | engines/etf_engine.py | 485 | ETF Score/Rank/Strategy + B-7 근거생성 |
| 엔진 | engines/alert_engine.py | 604 | 8종 Alert (기존6+ETF_RANK+REGIME) |
| 코어 | core/json_builder.py | 119 | core_data 조립 (signals 포함) |
| 코어 | core/validator.py | 208 | 19시그널+6Score 범위 검증 |
| 코어 | core/regime_tracker.py | 204 | 레짐 이력+전환 감지 |
| 코어 | core/signal_diff.py | 225 | 시그널 변화량 Top3 추출 |
| 발행 | publishers/paid_report_formatter.py | 152 | B-7 시그널 기반 ETF 근거 포맷 |
| 오케 | run_market.py | 290 | 수집+분석 파이프라인 |
| 오케 | run_alert.py | 423 | Alert 감지+발행 파이프라인 |

---

## 4. 19개 시그널 체계

| Tier | 시그널 | 데이터 | Score 반영 | 범위 |
|------|--------|--------|-----------|------|
| 기존7 | VIX/US10Y/Oil/DXY/Credit/YC/Sentiment | yfinance+FRED+RSS | 각 Score | 1~5 |
| T1-1 | Fear & Greed | alternative.me | Risk 20% | 1~5 |
| T1-2 | BTC 변동 | yfinance | Risk 15% | 1~4 |
| T1-3 | 주가 모멘텀 | snapshot | Growth 20% | 1~5 |
| T1-4 | XLF/GLD 상대강도 | yfinance | Stability 20% | 1~3 |
| T2-1 | Market Breadth | RSP/SPY | Growth 10% | 1~3 |
| T2-2 | Vol Term Structure | VIX/VIX3M | Risk 30% | 1~3 |
| T2-3 | Initial Claims | FRED ICSA | Growth 10% | 1~3 |
| T2-4 | Inflation Expectation | FRED T5YIFR | Inflation 35% | 1~3 |
| T2-5 | EM Stress | EEM+DXY | Liquidity 40% | 1~4 |
| T3-1 | AI Momentum | SOXX/QQQ | Growth 15% | 1~3 |
| T3-2 | Nasdaq Relative | NASDAQ-SP500 | Growth 15% | 1~3 |
| T3-3 | Banking Stress | KRE/XLF | Stability 30% | 1~3 |

### Market Score v1.3 가중치

- **Growth**: VIX 15% + 금리 15% + 모멘텀 20% + Breadth 10% + ICSA 10% + AI 15% + Nasdaq 15%
- **Inflation**: Oil 35% + 금리 30% + 기대인플레 35%
- **Liquidity**: Stability 30% + Dollar 30% + EM 40%
- **Risk**: VIX 20% + 감성 15% + F&G 20% + BTC 15% + VolTerm 30%
- **Stability**: HY 50% + XLF/GLD 20% + Banking 30%

---

## 5. Alert 시스템 (8종)

| Alert | 등급 | 조건 | 쿨다운 |
|-------|------|------|--------|
| VIX | L1~L2 | VIX 28/35 | 4h |
| SPY | L1~L3 | SP500 -2.5/-4/-6% | 4h |
| OIL | L1~L2 | WTI $100 or +4% | 4h |
| FED_SHOCK | L2~L3 | SPY+Fed RSS | 4h |
| CRISIS | L2~L3 | VIX+SPY+US10Y 동시 | 4h |
| VIX_COUNTDOWN | L1 | VIX 25/27/29 | 1일 |
| **ETF_RANK** | L1~L2 | Top1변경(L2)/Top3교체(L1) | 1일 |
| **REGIME_CHANGE** | L1~L2 | danger(L2)/recovery(L1) | 4h |

### Alert 발송 경로

| Alert | X | TG 무료 | TG 유료 (L2이상) |
|-------|---|---------|-----------------|
| ETF_RANK | ✅ 280자 | ✅ 상세+원인Top3 | ✅ 전체랭킹+원인+전략 |
| REGIME | ✅ 280자 | ✅ Score+원인 | ✅ Score+ETF전략+원인 |
| 기존6종 | ✅ | ✅ | ✅ VIX/CRISIS/FED만 |

---

## 6. B-7: ETF 상세 전략 (유료)

ETF 6종 각각의 매수/매도 근거를 19개 시그널 기반으로 자동 생성.
Full Dashboard 세션(18:30 KST)에서 유료 채널 발행.

| ETF | 영향 시그널 |
|-----|-----------|
| QQQM/XLK | growth, ai_momentum, nasdaq_rel, breadth, vol_term |
| SPYM | growth, risk, financial_stability, claims |
| XLE | commodity_pressure, infl_exp, em_stress |
| ITA | risk, financial_stability, em_stress |
| TLT | risk, vol_term, rate, fear_greed, claims |

---

## 7. Validation (Hard Gate)

- 19개 시그널 개별 범위 검증
- 6개 Market Score 범위 (1~5)
- allocation 총합 100%
- Enum 검증 (risk_level, trading_signal)
- **어느 하나라도 FAIL → 발행 차단**

---

## 8. 운영 스케줄 (GitHub Actions, KST)

| 시간 | 세션 | 내용 |
|------|------|------|
| 06:30 | morning tweet | 전일 마감 분석 |
| 08:00 | alert | Alert 감지 (B-5/B-6 포함) |
| 13:00 | alert | Alert 감지 |
| 18:30 | full tweet | Full Dashboard + 유료 B-7 리포트 |
| 23:30 | intraday tweet | 장중 업데이트 |
| 금 20:00 | close thread | 주간 분석 |

---

## 9. 테스트 (403/403 PASS)

| 테스트 | 건수 |
|--------|------|
| Tier 1 시그널 | 145 |
| Tier 2 시그널 | 126 |
| Tier 3 시그널 | 42 |
| B-5/B-6 Alert | 51 |
| B-7 ETF 전략 | 39 |
| **합계** | **403** |
