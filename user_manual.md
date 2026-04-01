# Investment OS — 운영자 매뉴얼 (v1.15.0)

> 대상: GTT팀 (시스템 운영 담당자)
> 최종수정: 2026-04-01
> v1.15.0: 시그널 19개 + B-5/B-6/B-7 + signals 저장 + TG 연결

---

## 1. 초기 설치

### 1.1 사전 요구 사항

| 항목 | 버전 | 확인 명령 |
|---|---|---|
| Python | 3.11 이상 | `python --version` |
| pip | 최신 | `pip --version` |
| Git | 최신 | `git --version` |

### 1.2 설치

```bash
git clone https://github.com/yumens2-byte/investment-os.git
cd investment-os
pip install -r requirements.txt
```

### 1.3 환경변수 (.env)

```
# X (Twitter)
X_API_KEY=xxx
X_API_SECRET=xxx
X_ACCESS_TOKEN=xxx
X_ACCESS_SECRET=xxx

# FRED
FRED_API_KEY=xxx

# Telegram
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID_FREE=xxx     # 무료 채널
TELEGRAM_CHAT_ID_PAID=xxx     # 유료 채널

# 운영
DRY_RUN=true                  # true=발행 안함, false=실제 발행
LOG_LEVEL=INFO
```

---

## 2. 실행 명령

### 2.1 기본 명령

```bash
# 시장 분석 (core_data.json 생성)
python main.py run market --session morning

# 발행 (X + 텔레그램)
python main.py run view --session morning --mode tweet

# Alert 감지 + 발행
python main.py run alert

# 전체 실행 (market → view → alert)
python main.py run all --session morning --mode tweet
```

### 2.2 세션 종류

| 세션 | 설명 |
|------|------|
| morning | 전일 마감 분석 (06:30 KST) |
| intraday | 장중 업데이트 (23:30 KST) |
| close | 장 마감 후 (07:00 KST) |
| full | Full Dashboard + 유료 리포트 (18:30 KST) |
| auto | 현재 시간 기반 자동 선택 |

---

## 3. 시그널 체계 (v1.15.0)

총 19개 시그널이 Market Score 6축에 반영됩니다.

### 3.1 Tier별 시그널

- **Tier 1** (기존 데이터 연결): Fear&Greed, BTC, 주가모멘텀, XLF/GLD
- **Tier 2** (새 데이터 추가): Breadth(RSP), VolTerm(VIX3M), ICSA, T5YIFR, EEM
- **Tier 3** (설계서 보완): AI모멘텀(SOXX), Nasdaq상대, 은행스트레스(KRE)

### 3.2 Validator 검증

19개 시그널 각각의 범위를 `core/validator.py`에서 검증합니다.
범위를 벗어나면 **발행 차단**.

---

## 4. Alert 운영

### 4.1 Alert 8종

| # | 타입 | 등급 | 쿨다운 |
|---|------|------|--------|
| 1 | VIX | L1~L2 | 4시간 |
| 2 | SPY | L1~L3 | 4시간 |
| 3 | OIL | L1~L2 | 4시간 |
| 4 | FED_SHOCK | L2~L3 | 4시간 |
| 5 | CRISIS | L2~L3 | 4시간 |
| 6 | VIX_COUNTDOWN | L1 | 하루 1회 |
| 7 | **ETF_RANK** | L1~L2 | 하루 1회 |
| 8 | **REGIME_CHANGE** | L1~L2 | 4시간 |

### 4.2 발송 채널

- **X**: 모든 Alert → 280자 트윗
- **TG 무료**: 모든 Alert → ETF_RANK/REGIME은 상세 포맷
- **TG 유료**: L2 이상만 → 프리미엄 포맷 (전체 랭킹, Score 변화, 원인 분석)

### 4.3 B-5/B-6 원인 분석

`core/signal_diff.py`가 이전/현재 signals를 비교하여 변화량 Top 3 시그널을 추출합니다.
이 정보는 Alert 메시지에 "원인: VIX Extreme ↑ + 신흥국 EM Crisis ↑" 형태로 표시됩니다.

---

## 5. 유료 채널 운영

### 5.1 B-7 ETF 상세 전략

Full Dashboard 세션(18:30 KST)에서 자동 발행.
`publishers/paid_report_formatter.py`가 19개 시그널 기반으로 ETF 6종 각각의 매수/매도 근거를 자동 생성합니다.

예시:
```
📈 TLT  1위  |  배분: 40%
   🟢 BUY  |  Overweight
   🔍 근거: VIX Extreme + Vol 백워데이션 + 공포탐욕 Extreme Fear
   ⚠️ 리스크: 금리 반등 시 가격 하락
```

---

## 6. GitHub Actions 스케줄

| 시간(KST) | 세션 | cron |
|-----------|------|------|
| 06:30 | morning tweet | `30 21 * * 1-5` (UTC) |
| 08:00 | alert | `0 23 * * 1-5` |
| 13:00 | alert | `0 4 * * 1-5` |
| 18:30 | full tweet | `30 9 * * 1-5` |
| 23:30 | intraday tweet | `30 14 * * 1-5` |
| 금 20:00 | close thread | `0 11 * * 5` |

---

## 7. 장애 대응

| 상황 | 대응 |
|------|------|
| yfinance 차단 | requests fallback 자동 전환 |
| RSS 소스 장애 | 나머지 8소스로 자동 대체 |
| FRED 갱신 지연 | 이전 값 캐싱 (일 1회 허용) |
| Tier2 수집 실패 | 중립값 자동 적용 (기존 로직 보존) |
| signals 빈 dict | ETF 근거 = "Regime 기반" fallback |
| Validation FAIL | 발행 차단 + 로그 확인 |

---

## 8. 데이터 파일

| 파일 | 위치 | 역할 |
|------|------|------|
| core_data.json | data/outputs/ | 전체 분석 결과 (signals 포함) |
| alert_history.json | data/published/ | Alert 발송 이력 + 쿨다운 |
| rank_history.json | data/published/ | ETF 랭킹 이력 (최근 30일) |
| regime_history.json | data/published/ | 레짐 이력 (최근 30일) |
| history.json | data/published/ | X 발행 이력 (중복 검사) |

---

## 9. 테스트

```bash
python test_tier1_signals.py    # 145건
python test_tier2_signals.py    # 126건
python test_tier3_signals.py    # 42건
python test_b5_b6.py            # 51건
python test_b7_etf_rationale.py # 39건
# 합계: 403건
```
