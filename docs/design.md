# Investment OS — 시스템 설계서 (v1.5.0)

> 작성일: 2026-03-28 | 작성자: GTT팀 | 상태: 개발 완료 · 파일럿 테스트 완료 (42/42 PASS)

---

## 1. 프로젝트 목표

| 항목 | 내용 |
|---|---|
| **목표** | 미국 금융시장 데이터 수집 → 리스크 분석 → X(Twitter) 자동 발행 End-to-End 자동화 |
| **현재 상태 (착수 전)** | 반자동 (수동 데이터 확인 + 수동 업로드) |
| **목표 상태** | 완전 무인 자동화 (일 3회 자동 게시) |
| **스택** | Python 3.11 + schedule + feedparser + Git |
| **발행 채널** | X (Twitter) — 단일 트윗 / 쓰레드 |

---

## 2. 버전 이력

| 버전 | 일자 | 핵심 변경 내용 |
|---|---|---|
| **v1.5.0** | 2026-03-28 | Reddit 유료화 대응 → 다중 RSS 9소스로 전환, 감성 엔진 고도화 |
| v1.4.0 | 2026-03-28 | 완전 Python 자동화 구현 (yfinance/FRED/RSS/X) |
| v1.3.2 | - | run_auto.py 픽스처 기반 골격 (AI 반자동) |
| v1.0.0 | - | 최초 .md 설계 문서 작성 |

---

## 3. 시스템 아키텍처

### 3.1 전체 흐름

```
[데이터 수집 레이어]       [분석 엔진 레이어]      [출력 레이어]
yfinance                  Macro Engine            core_data.json
FRED API        ───▶      Regime Engine   ───▶    validate_data
다중 RSS (9소스)           ETF Engine              validate_output
  Yahoo Finance           Risk Engine                  │
  CNBC                                                 ▼
  MarketWatch                                    [run_view.py]
  Investing.com                                  중복 검사
  Google News ×5                                      │
                                                       ▼
                                                 X 발행 (DRY_RUN or 실제)
                                                 history.json 기록
```

### 3.2 파일 구조

```
investment-os/
├── .env                          # API 키 (git 제외)
├── .gitignore
├── requirements.txt              # praw 제거됨 (v1.5.0)
│
├── config/
│   └── settings.py               # 전체 상수 중앙 관리
│                                 # RSS_SOURCES 9개 정의 포함
│
├── collectors/                   # 수집 레이어
│   ├── yahoo_finance.py          # SPY/Nasdaq/VIX/US10Y/WTI/DXY + ETF가격
│   ├── fred_client.py            # 기준금리 / HY스프레드 / 장단기금리차
│   ├── rss_extended.py           # ★ 다중 RSS 감성 수집 (v1.5.0 신규)
│   ├── news_rss.py               # rss_extended 위임 wrapper (하위호환)
│   └── reddit_client.py          # DEPRECATED stub (유료화로 비활성화)
│
├── engines/                      # 분석 엔진 레이어
│   ├── macro_engine.py           # Signal 생성 + Market Score (6축)
│   ├── regime_engine.py          # Market Regime + Risk Level
│   ├── etf_engine.py             # ETF Score/Rank/Strategy/Allocation
│   └── risk_engine.py            # Portfolio Risk + Trading Signal
│
├── core/                         # 핵심 공통 레이어
│   ├── json_builder.py           # JSON Core Data 조립 + 저장/로드
│   ├── validator.py              # validate_data / validate_output (Hard Gate)
│   └── duplicate_checker.py      # X 발행 전 중복 검사
│
├── publishers/                   # 발행 레이어
│   ├── x_formatter.py            # 트윗 텍스트 포맷 생성
│   └── x_publisher.py            # tweepy X 발행 (DRY_RUN 지원)
│
├── data/                         # 실행 데이터 (git 제외)
│   ├── outputs/
│   │   ├── core_data.json        # JSON Core Data (run_market 산출)
│   │   ├── validation_result.json
│   │   └── publish_payload.json
│   └── published/
│       └── history.json          # 발행 이력 (중복 체크용)
│
├── docs/
│   ├── design.md                 # 본 문서
│   ├── operator_manual.md        # 운영자 매뉴얼
│   └── user_manual.md            # 사용자 매뉴얼
│
├── run_market.py                 # 수집 + 분석 실행
├── run_view.py                   # 검증 + 발행 실행
├── scheduler.py                  # 자동 스케줄 데몬
└── pilot_test.py                 # 파일럿 테스트 (42/42 PASS)
```

---

## 4. 데이터 소스 정의 (v1.5.0 기준)

| 소스 | 모듈 | 수집 항목 | 비용 | 안정성 |
|---|---|---|---|---|
| Yahoo Finance | yahoo_finance.py | SPY/Nasdaq/VIX/US10Y/WTI/DXY/ETF가격 | 무료 | 비공식 ⚠️ |
| FRED API | fred_client.py | 기준금리/HY스프레드/장단기금리차 | 무료 (공식) | ✅ 안정 |
| Yahoo Finance RSS | rss_extended.py | 시장 뉴스 감성 | 무료 | ✅ 안정 |
| CNBC RSS | rss_extended.py | 시장/경제 뉴스 감성 | 무료 | ✅ 안정 |
| MarketWatch RSS | rss_extended.py | 시장 분석 감성 | 무료 | ✅ 안정 |
| Investing.com RSS | rss_extended.py | ETF/매크로 감성 | 무료 | △ 간헐 차단 |
| Google News RSS ×5 | rss_extended.py | 키워드 기반 감성 | 무료 | ✅ 안정 |
| Reddit | reddit_client.py | **비활성화 (유료화)** | 유료 | ❌ 제거 |

### Reddit 제거 근거

- 2023년 6월 Reddit API 유료화 시행
- 상업적 자동화 사용은 유료 플랜 필수
- 대체 소스 9개 RSS로 감성 커버리지 유지 또는 향상
- `reddit_client.py` 파일은 코드베이스에 DEPRECATED stub으로 유지 (하위호환)

---

## 5. 다중 RSS 감성 엔진 설계 (v1.5.0 신규)

### 5.1 수집 소스 목록 (settings.RSS_SOURCES)

| # | 소스명 | weight | max_items |
|---|---|---|---|
| 1 | Google News — Market | 1.0 | 10 |
| 2 | Google News — Fed | 1.2 | 8 |
| 3 | Google News — ETF | 1.0 | 8 |
| 4 | Yahoo Finance — Markets | 1.3 | 10 |
| 5 | Yahoo Finance — Top Stories | 1.1 | 10 |
| 6 | CNBC — Markets | **1.5** | 10 |
| 7 | CNBC — Economy | **1.4** | 8 |
| 8 | MarketWatch — Top Stories | 1.2 | 10 |
| 9 | Investing.com — Stock Market | 1.1 | 8 |

weight 기준: CNBC 계열 > Yahoo Finance > MarketWatch > Google News  
(신뢰도 및 시장 전문성 반영)

### 5.2 감성 분석 로직

```
헤드라인 수집
    ↓
부정어 처리 (no/not/never + 키워드 → 극성 반전)
    ↓
강도별 점수 부여
  - 강한 키워드 (surges, record high, crash ...) : ±2.0
  - 일반 키워드 (rally, gain, drop, fear ...)    : ±1.0
  - 부정 + 강세 키워드                           : -1.5
  - 부정 + 약세 키워드                           : +0.5
    ↓
SHA256 헤드라인 dedup (소스 간 동일 기사 중복 집계 방지)
    ↓
소스별 weight 기반 가중 평균 산출
    ↓
임계값 판단
  net_weighted_score ≥ +0.5 → Bullish
  net_weighted_score ≤ -0.5 → Bearish
  그 외                     → Neutral
```

### 5.3 장애 대응

- 소스별 독립 fetch → 한 소스 장애 시 나머지 계속 동작
- timeout_sec 소스별 설정 (8~10초)
- 전체 소스 실패 시 → Neutral 반환 (시스템 중단 없음)
- User-Agent 헤더 설정 → CNBC/MarketWatch 차단 방지

---

## 6. 분석 엔진 설계

### 6.1 Macro Engine (engines/macro_engine.py)

**입력**: 시장 스냅샷 + FRED 데이터 + 뉴스 감성  
**출력**: Signals + Market Score (6축)

| Market Score 축 | 구성 요소 | 점수 범위 |
|---|---|---|
| growth_score | VIX + 금리 | 1~5 (5=위험) |
| inflation_score | 유가 + 금리 | 1~5 |
| liquidity_score | 신용 + 달러 | 1~5 |
| risk_score | VIX + 감성 | 1~5 |
| financial_stability_score | HY스프레드 | 1~5 |
| commodity_pressure_score | WTI | 1~5 |

### 6.2 Regime Engine (engines/regime_engine.py)

**출력**: market_regime / market_risk_level (LOW/MEDIUM/HIGH) / regime_reason

**레짐 결정 우선순위**:
1. Shock Override — Oil Shock / Liquidity Crisis / Recession Risk 우선
2. Base Regime — Stagflation / Risk-Off / Risk-On / AI Bubble / Transition

**Risk Level 매핑**:

| 점수 범위 | Risk Level |
|---|---|
| 0 ~ 39 | LOW |
| 40 ~ 69 | MEDIUM |
| 70 ~ 100 | HIGH |

### 6.3 ETF Engine (engines/etf_engine.py)

**ETF Universe**:

| ETF | 그룹 | Risk-Off 시 배분 | Risk-On 시 배분 |
|---|---|---|---|
| QQQM | Growth/AI | 5% | 35% |
| XLK | Mega-cap Tech | 5% | 25% |
| SPYM | Income | 20% | 20% |
| XLE | Energy | 25% | 10% |
| ITA | Defense | 25% | 5% |
| TLT | Bond Hedge | 20% | 5% |

**Governance Rule** (검증 Hard Gate):
- 총 배분 합계 = 100% 강제
- Underweight stance ETF: allocation ≤ 10%
- Overweight stance ETF: allocation ≥ 15%
- TLT: allocation < 45% (극단 방어 방지)

### 6.4 Risk Engine (engines/risk_engine.py)

| Trading Signal | 발동 조건 |
|---|---|
| BUY | LOW Risk + 다수 ETF BUY 신호 |
| ADD | MEDIUM Risk + 선택적 기회 |
| HOLD | 방향성 불분명 |
| REDUCE | 하향 모멘텀 |
| HEDGE | HIGH Risk |
| SELL | 극단적 위험 |

---

## 7. Validation Hard Gates

### 7.1 validate_data (구조 검증)

| 검증 항목 | 실패 시 동작 |
|---|---|
| 9개 최상위 키 존재 | 발행 차단 |
| market_snapshot 6개 필드 타입 | 발행 차단 |
| allocation 총합 100% | 발행 차단 |
| market_risk_level Enum | 발행 차단 |
| trading_signal Enum | 발행 차단 |

### 7.2 validate_output (정합성 검증)

| 검증 항목 | 실패 시 동작 |
|---|---|
| market_risk_level 양쪽 일치 | 발행 차단 |
| Underweight ≤ 10% | 발행 차단 |
| Overweight ≥ 15% | 발행 차단 |
| TLT < 45% | 발행 차단 |

**규칙: 둘 중 하나라도 FAIL → publish block (Hard Gate)**

---

## 8. 중복 검사 설계

```
history.json (최근 200건 유지)
     ↓
비교 기준 1: 트윗 본문 SHA256 해시 (완전 일치 차단)
비교 기준 2: regime + risk_level + Top3 ETF 조합 해시 (동일 시장 상태 재발행 차단)
     ↓
중복 감지 → rc=2 반환 → run_view.py 발행 차단
비중복 확인 → 발행 진행 → history.json 기록
```

---

## 9. 운영 스케줄 (KST 기준)

| 시간 | 세션 | 모드 | 미국 시간(ET) |
|---|---|---|---|
| 평일 06:30 | morning | tweet | 전일 20:30 (Pre-market 준비) |
| 평일 23:30 | intraday | tweet | 당일 10:30 (장중 업데이트) |
| 평일 07:00 | close | tweet | 전일 17:00 (장 마감 후) |
| 매주 금요일 20:00 | close | thread | 07:00 ET (주간 분석) |

---

## 10. 발행 포맷

### 10.1 단일 트윗 (280자 이내)

```
📊 Morning Brief 🌅

📉 SPY -1.5% | VIX 28.5 | US10Y 4.62%
🛢️ WTI $95.4

🔴 Risk-Off
🟡 Risk MEDIUM
🎯 방어 우선
⏸️ "공격 금지 구간"

#ETF #투자 #미국증시
```

### 10.2 X 쓰레드 (6개 포스트)

1. 요약 + 해시태그
2. 시장 레짐 + 이유
3. 시장 스냅샷 수치
4. ETF 상위 3개 순위
5. Buy/Hold/Reduce 시그널
6. 포트폴리오 배분 %

---

## 11. 리스크 및 대응

| 리스크 | 영향도 | 대응 |
|---|---|---|
| yfinance 비공식 차단 | 높음 | Polygon.io fallback 슬롯 준비 |
| RSS 소스 일부 차단 | 낮음 | 나머지 8소스로 자동 대체 |
| X API 429 Rate Limit | 중간 | 3회 Retry + 30초 대기 |
| X 계정 자동화 감지 | 높음 | DRY_RUN=true 3일 검증 후 전환 |
| FRED 갱신 지연 | 낮음 | 일 1회 캐싱 허용 범위 |
| Validation FAIL | 중간 | 발행 차단 + 로그 즉시 확인 |
| 중복 발행 | 낮음 | history.json 해시 기반 자동 차단 |
