# Investment OS — 운영자 매뉴얼 (v1.5.0)

> 대상: GTT팀 (시스템 운영 담당자)  
> 최종수정: 2026-03-28  
> v1.5.0 변경: Reddit 제거, 다중 RSS 9소스 운영

---

## 1. 초기 설치

### 1.1 사전 요구 사항

| 항목 | 버전 | 확인 명령 |
|---|---|---|
| Python | 3.11 이상 | `python --version` |
| pip | 최신 | `pip --version` |
| Git | 최신 | `git --version` |

### 1.2 설치 순서

```bash
# 1. 저장소 클론
git clone <repo_url> investment-os
cd investment-os

# 2. 가상환경 생성 (권장)
python -m venv venv
source venv/bin/activate          # Mac/Linux
# venv\Scripts\activate           # Windows

# 3. 의존성 설치 (Reddit 제거됨 — praw 없음)
pip install -r requirements.txt

# 4. .env 설정 (아래 1.3 참조)
```

### 1.3 .env 필수 항목

```bash
# ── FRED API (무료, 필수) ──────────────────────────
FRED_API_KEY=1111

# ── X (Twitter) API v2 (필수) ─────────────────────
X_API_KEY=1111
X_API_SECRET=1111
X_ACCESS_TOKEN=1111
X_ACCESS_TOKEN_SECRET=1111

# ── 운영 모드 ─────────────────────────────────────
DRY_RUN=true      # 반드시 true로 시작, 검증 완료 후 false 전환
LOG_LEVEL=INFO
```

> ⚠️ v1.5.0부터 Reddit 관련 환경변수 불필요. 기존 .env에 있어도 무시됨.

### 1.4 RSS 소스 별도 설정 불필요

다중 RSS 9소스는 `config/settings.py`에 URL이 고정 설정되어 있다.
API 키, 계정, 별도 인증 없이 즉시 동작한다.

---

## 2. 파일럿 테스트 절차

**실제 X 발행 전 최소 3일 DRY_RUN 검증 필수**

### 2.1 자동 파일럿 테스트 실행

```bash
# 전체 파이프라인 자동 검증 (네트워크 불필요)
python pilot_test.py --round all

# 기대 결과:
# ✅ Round 1: 28/28 PASS
# ✅ Round 2: 14/14 PASS
# 🎉 파일럿 테스트 2회 완료 — 특이사항 없음
```

### 2.2 수동 1차 파일럿 테스트 (실제 네트워크)

```bash
# Step 1: 수집 + 분석 실행
python run_market.py --session morning

# 확인 사항:
# ✅ data/outputs/core_data.json 생성
# ✅ 로그: "validate_data: PASS"
# ✅ 로그: "validate_output: PASS"
# ✅ 로그: "RSS소스 N성공/M실패" (N ≥ 3 이상이면 정상)
```

```bash
# Step 2: 발행 프리뷰 (DRY_RUN=true)
python run_view.py --mode tweet

# 확인 사항:
# ✅ "[DRY RUN] 실제 발행 건너뜀" 로그
# ✅ 트윗 텍스트 280자 이내
# ✅ data/published/history.json 기록
```

```bash
# Step 3: 세션별 전체 실행
python scheduler.py --run-now morning
python scheduler.py --run-now intraday
python scheduler.py --run-now close
```

### 2.3 수동 2차 파일럿 테스트

```bash
# 동일 조건 재실행 → 중복 차단 확인
python run_market.py --session morning
python run_view.py --mode tweet

# 확인 사항:
# ✅ "[DupChecker] 중복 감지" 로그 (동일 레짐이면 차단)
# 또는
# ✅ "[DupChecker] 중복 없음" (레짐 변경이면 발행 진행)
```

### 2.4 테스트 완료 기준

| 항목 | 기준 |
|---|---|
| pilot_test.py | 42/42 PASS |
| core_data.json | 매 실행마다 정상 생성 |
| Validation | 2회 연속 PASS |
| RSS 소스 | 3개 이상 성공 |
| 트윗 포맷 | 280자 이내 |
| 중복 검사 | 동일 레짐 재실행 시 차단 확인 |
| DRY_RUN 로그 | "[DRY RUN]" 문구 정상 출력 |

---

## 3. 실제 발행 전환

**파일럿 테스트 2회 완료 후 진행**

```bash
# .env 수정
DRY_RUN=false

# 단발 실행으로 최초 실제 발행 확인
python run_market.py --session morning
python run_view.py --mode tweet

# X 계정에서 게시 확인
# data/published/history.json 에서 실제 tweet_id 확인 (DRY_RUN 아닌 숫자값)
```

---

## 4. 자동 스케줄 운영

### 4.1 스케줄러 시작

```bash
# 포그라운드 (테스트용)
python scheduler.py

# 백그라운드 (운영용 — Linux/Mac)
nohup python scheduler.py >> logs/scheduler.log 2>&1 &
echo $! > scheduler.pid
cat scheduler.pid  # PID 확인
```

### 4.2 스케줄 시간표 (KST)

| 시간 | 작업 | 세션 | 모드 |
|---|---|---|---|
| 평일 06:30 | Morning Brief | morning | tweet |
| 평일 23:30 | Intraday Update | intraday | tweet |
| 평일 07:00 | Close Summary | close | tweet |
| 매주 금요일 20:00 | Weekly Thread | close | thread |

### 4.3 즉시 실행 (수동)

```bash
python scheduler.py --run-now morning
python scheduler.py --run-now intraday
python scheduler.py --run-now close
python scheduler.py --run-now weekly
```

### 4.4 스케줄러 중지

```bash
kill $(cat scheduler.pid)
```

---

## 5. 로그 모니터링

### 5.1 핵심 정상 로그 패턴

```
[run_market] 완료
RSS소스 7성공/2실패          ← 3개 이상이면 정상
validate_data: PASS
validate_output: PASS
발행 가능: True
[XPublisher] 발행 성공: tweet_id=XXXXXXX
[DupChecker] 발행 이력 기록: tweet_id=XXXXXXX
```

### 5.2 주의 상황 (WARNING — 발행은 진행)

```
[DupChecker] 중복 감지    → 정상 차단, 다음 스케줄에서 재시도
RSS소스 N성공/M실패        → 일부 소스 일시 장애, 나머지로 감성 집계
Rate limit (시도 N/3)     → 자동 재시도 중
```

### 5.3 오류 상황 (ERROR — 즉시 확인 필요)

```
validate_data FAIL     → data/outputs/validation_result.json 오류 확인
validate_output FAIL   → 정합성 오류, 엔진 로직 점검
run_market 실패        → 수집 실패 또는 예외, 네트워크 확인
X API 키 미설정        → .env 파일 확인
Rate limit 초과 — 발행 포기  → X API 플랜 확인
```

---

## 6. RSS 소스 운영 관리

### 6.1 현재 운영 소스 (9개)

| 소스 | weight | 장애 시 영향 |
|---|---|---|
| Google News — Market | 1.0 | 낮음 (5개 중 1개) |
| Google News — Fed | 1.2 | 낮음 |
| Google News — ETF | 1.0 | 낮음 |
| Yahoo Finance — Markets | 1.3 | 중간 |
| Yahoo Finance — Top Stories | 1.1 | 중간 |
| CNBC — Markets | **1.5** | 높음 (weight 최고) |
| CNBC — Economy | **1.4** | 높음 |
| MarketWatch — Top Stories | 1.2 | 중간 |
| Investing.com — Stock Market | 1.1 | 낮음 (간헐 차단) |

### 6.2 소스 추가/제거 방법

`config/settings.py`의 `RSS_SOURCES` 리스트에서 딕셔너리 항목 추가/삭제.
코드 변경 없이 소스 관리 가능.

```python
# 소스 추가 예시
RSS_SOURCES.append({
    "name": "Reuters — Markets",
    "url": "https://feeds.reuters.com/reuters/businessNews",
    "weight": 1.5,
    "max_items": 10,
    "timeout_sec": 10,
})
```

### 6.3 감성 임계값 조정

`config/settings.py`:
```python
SENTIMENT_BULLISH_THRESHOLD = 0.5   # 이 이상 → Bullish
SENTIMENT_BEARISH_THRESHOLD = -0.5  # 이 이하 → Bearish
```
실시간 뉴스 반응이 지나치게 민감하거나 둔감하면 ±0.2 단위로 조정.

---

## 7. 장애 대응

### 7.1 yfinance 수집 실패

```
증상: "[YF] 조회 실패" 로그 반복
원인: Yahoo Finance 일시 차단 또는 네트워크
즉시: 10분 대기 후 재실행
반복: 네트워크 확인 → 장기 지속 시 Polygon.io 교체 검토
```

### 7.2 Validation FAIL

```
증상: "validate_data FAIL" 또는 "validate_output FAIL"
확인: data/outputs/validation_result.json → errors 항목 확인
조치: python run_market.py --session morning 재실행
반복: engines/ 코드 점검
```

### 7.3 RSS 소스 전체 실패

```
증상: "모든 소스 실패 — Neutral 반환" 로그
원인: 네트워크 단절 또는 방화벽
영향: 감성=Neutral로 분석 계속 진행 (시스템 중단 없음)
조치: 네트워크 확인 → 방화벽에서 feedparser 허용 여부 확인
```

### 7.4 X 발행 실패 (Forbidden 403)

```
증상: "권한 없음 (X API Basic 플랜 필요)"
원인: Free 플랜으로는 쓰기(Write) 불가
조치: X Developer Portal에서 Basic 플랜($100/월) 업그레이드
```

### 7.5 X 계정 자동화 감지

```
증상: tweepy Forbidden 오류 반복
즉시: DRY_RUN=true 전환 (운영 중단)
조치: X 계정 이의신청 제출
재개: 3일 이상 DRY_RUN 검증 후 재전환
```

---

## 8. 정기 점검 항목

| 주기 | 점검 항목 |
|---|---|
| 매일 | 로그 확인, history.json 기록 확인 |
| 매주 | X 계정 게시물 확인, RSS 소스 성공률 확인 |
| 매월 | history.json 백업, API 키 유효성 확인 |
| 분기 | 감성 임계값 튜닝, yfinance 동작 재검증 |

---

## 9. 주요 설정 변경 가이드

### 9.1 스케줄 시간 변경

`config/settings.py`:
```python
SCHEDULE_MORNING = "06:30"    # KST 기준
SCHEDULE_INTRADAY = "23:30"
SCHEDULE_CLOSE = "07:00"
```
변경 후 스케줄러 재시작 필요.

### 9.2 리스크 임계값 조정

`config/settings.py`:
```python
VIX_LOW_THRESHOLD = 20.0      # VIX 20 이상 → MEDIUM
VIX_HIGH_THRESHOLD = 30.0     # VIX 30 이상 → HIGH
US10Y_HIGH_THRESHOLD = 4.5    # US10Y 4.5% 이상 → 고금리
OIL_HIGH_THRESHOLD = 90.0     # WTI $90 이상 → Oil 압박
```

### 9.3 중복 검사 범위 조정

`config/settings.py`:
```python
DUPLICATE_CHECK_COUNT = 10    # 최근 N건과 비교 (기본 10)
```

---

## 10. Git 업로드 기준

```bash
# 커밋 전 확인
# ✅ .env 미포함 확인 (git status에 .env 없어야 함)
# ✅ data/outputs/, data/published/ 미포함 확인

git add .
git commit -m "feat: v1.5.0 업데이트 내용"
git push origin main
```
