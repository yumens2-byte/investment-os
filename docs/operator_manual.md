# Investment OS — 운영자 매뉴얼 (v1.24.0)

> 대상: GTT팀 (시스템 운영 담당자)
> 최종수정: 2026-04-04
> v1.24.0: Gemini AI 확장 11종 + SMA 모멘텀 + Supabase + 주말 콘텐츠

---

## 1. 초기 설치

### 1.1 사전 요구 사항

| 항목 | 버전 | 확인 명령 |
|---|---|---|
| Python | 3.11 이상 | `python --version` |
| pip | 최신 | `pip --version` |
| Playwright | 최신 | `playwright install --with-deps chromium` |

### 1.2 설치

```bash
git clone https://github.com/yumens2-byte/investment-os.git
cd investment-os
pip install -r requirements.txt
pip install playwright
playwright install --with-deps chromium
```

### 1.3 환경변수

```env
# Gemini (3키 자동전환 — 다른 Google 프로젝트에서 생성)
GEMINI_API_KEY=main키
GEMINI_API_SUB_KEY=sub키
GEMINI_API_SUB_SUB_KEY=sub2키

# Claude (뉴스 요약 전용)
ANTHROPIC_API_KEY=

# X (Twitter) — Pay-per-use
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

# 운영 제어
DRY_RUN=true          # true=모의발행, false=실발행
FORCE_RUN=false       # true=휴무일/주말에도 강제 실행
MULTILINGUAL_ENABLED=true  # C-11 다국어 ON/OFF
```

---

## 2. 실행 커맨드

### 2.1 평일 세션

```bash
python main.py run all --session morning --mode tweet      # 모닝 브리프
python main.py run all --session intraday --mode tweet     # 장중 업데이트
python main.py run all --session close --mode tweet        # 종가 요약
python main.py run all --session close --mode thread       # 종가 스레드
python main.py run all --session full --mode tweet         # 풀 대시보드
python main.py run all --session narrative --mode tweet    # AI 내러티브 + 시장 역사
python main.py run all --session weekly --mode thread      # 주간 스레드
```

### 2.2 Alert

```bash
python main.py alert    # 10분마다 자동 실행 (main.yml)
```

### 2.3 주말

```bash
python main.py weekend --day auto    # 요일 자동 판별
python main.py weekend --day sat     # 토요일 강제 (주간 리뷰 + 교육)
python main.py weekend --day sun     # 일요일 강제 (프리뷰 + 금융 상식)
```

### 2.4 테스트

```bash
python main.py test --round all      # 파일럿 2라운드 (42건)
python test_tier1_signals.py         # Tier 1 전수 (145건)
python test_e2e_pipeline.py          # E2E 파이프라인 (68건)
```

---

## 3. GitHub Actions 워크플로우

### 3.1 main.yml — 평일 6세션

| cron (UTC) | KST | 세션 |
|------------|-----|------|
| 21:30 일~목 | 06:30 월~금 | morning |
| 14:30 월~금 | 23:30 월~금 | intraday |
| 20:30 월~금 | 05:30+1 화~토 | close |
| 09:30 월~금 | 18:30 월~금 | full |
| 02:30 월~금 | 11:30 월~금 | narrative |
| 11:00 목 | 20:00 목 | weekly |
| */10 13~21 월~금 | */10 22~06 | alert |

### 3.2 weekend_content.yml — 주말

| cron (UTC) | KST | 세션 |
|------------|-----|------|
| 01:00 토 | 10:00 토 | 주간 리뷰 + 투자 교육 (C-4) |
| 01:00 일 | 10:00 일 | 다음 주 프리뷰 + 금융 상식 (C-4B) |

### 3.3 comic_daily.yml / comic_weekly.yml

일일 4컷 / 주간 8컷 VS 배틀카드 + 카드뉴스

---

## 4. 휴무일 체크

`config/us_market_holidays.py`에 2026/2027 미장 휴무일 등록.

| 커맨드 | 휴무일 체크 | 주말 체크 |
|--------|-----------|----------|
| `run` (전체 세션) | ✅ | ✅ 스킵 |
| `alert` | ✅ | ✅ 스킵 |
| `weekend` | ❌ | ❌ (주말 전용) |
| FORCE_RUN=true | ❌ 무시 | ❌ 무시 |

---

## 5. Gemini RPD 관리

### 5.1 한도

```
키당 20 RPD (프로젝트 단위, 키 단위 아님)
3개 다른 프로젝트 × 20 = 60 RPD/일
평일 소비: ~21 RPD → 여유 39
```

### 5.2 3단 키 전환

```
main key → 429 → sub key → 429 → sub2 key → 429 → DLQ
```

### 5.3 RPD 절약

- `MULTILINGUAL_ENABLED=false` → C-11 비활성화 (-16 RPD)
- FORCE_RUN 주말 연속 테스트 자제 (RPD 빠르게 소진)

---

## 6. 장애 대응

### 6.1 yfinance 실패

```
증상: Failed to get ticker '^GSPC' → requests fallback 시도
원인: Yahoo Finance 비공식 API 불안정 (주말 특히 빈번)
조치: requests fallback이 자동 동작. 데이터 수집은 정상 완료됨.
```

### 6.2 Gemini 429

```
증상: RESOURCE_EXHAUSTED → 다음 키 전환
원인: RPD 초과 (주말 연속 테스트 시 빈번)
조치: 3단 키 전환 자동 동작. 3개 모두 소진 시 fallback 처리.
```

### 6.3 Supabase 연결 실패

```
증상: SUPABASE_URL 미설정 또는 연결 타임아웃
원인: 환경변수 누락 또는 네트워크
조치: main.yml env에 SUPABASE_URL, SUPABASE_KEY 확인
```

---

## 7. 배포 절차

```
1. 코드 수정
2. python test_tier1_signals.py   ← 145/145 PASS
3. python test_e2e_pipeline.py    ← 68/68 PASS
4. GitHub push
5. FORCE_RUN=true 수동 실행 → 로그 확인
6. DRY_RUN=false 전환 (실 발행)
```

---

## 8. 주요 파일 수정 금지 목록

| 파일 | 이유 |
|------|------|
| `publishers/dashboard_builder.py` | matplotlib 기존 대시보드 — 절대 수정 금지 |
| `.github/workflows/*.yml` | zip 패치에서 항상 제외 |
| `data/published/*.json` | GitHub Actions 캐시 파일 |
