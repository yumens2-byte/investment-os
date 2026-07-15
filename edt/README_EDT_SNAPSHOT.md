# EDT Market Snapshot — 설치 및 운영 가이드 v1.0

## 개요

EDT Universe 파이프라인 Phase A용 시장데이터를 매 거래일 자동 수집하여
Notion 고정 페이지에 기록한다. 기존 ICG 워크플로와 완전 독립.

```
GitHub Actions (KST 06:40 화~토)
  → FRED API + yfinance 수집
  → 12 v1.5 신뢰도 등급 태깅 JSON
  → Notion "EDT Market Snapshot" 페이지 code block 갱신
  → EDT 파이프라인에서 Claude가 1회 조회
```

## 수집 지표

| key | 소스 | 등급 | 비고 |
|---|---|---|---|
| us10y / us2y / t10y2y | FRED DGS10/DGS2/T10Y2Y | 높음 | 1일 지연 |
| effr / sofr | FRED EFFR/SOFR | 높음 | 1일 지연 |
| hy_oas | FRED BAMLH0A0HYM2 | 중간 | ~3일 지연 (참고치) |
| vix | yfinance ^VIX (실패 시 FRED VIXCLS 폴백) | 높음 | premium 차단 해소 |
| wti / dxy | yfinance CL=F / DX-Y.NYB | 중간 | |
| F&G Index | **제외** — EDT 측 web_search 유지 | — | API 부재 |

## 설치 절차 (마스터 수행, 1회)

### 1. 파일 배치
```
investment-os/
├── edt/
│   ├── edt_snapshot.py
│   └── README_EDT_SNAPSHOT.md
└── .github/workflows/
    └── edt_snapshot.yml
```
기존 파일은 일절 수정하지 않는다.

### 2. Notion 대상 페이지 준비
1. EDT Hub 하위에 빈 페이지 「EDT Market Snapshot」 생성
2. 페이지 우상단 ⋯ → 연결(Connections) → **NOTION_API_KEY가 속한
   integration을 해당 페이지에 추가** (미공유 시 404 발생)
3. 페이지 URL 끝 32자리가 페이지 ID
   예: notion.so/EDT-Market-Snapshot-**39f9208cbdc3...** ← 이 부분

### 3. Secret 1건 신규 등록
```
Settings → Secrets → Actions → New repository secret
  EDT_SNAPSHOT_PAGE_ID = (2에서 확보한 페이지 ID)
```
FRED_API_KEY / NOTION_API_KEY는 기존 등록분 그대로 사용.

### 4. 검증 (dry-run → 실기록)
```
Actions 탭 → EDT Market Snapshot → Run workflow
  1차: dry_run = true   → 로그에서 JSON 출력 확인
  2차: dry_run = false  → Notion 페이지에 JSON code block 생성 확인
```

## EDT 파이프라인 측 연동 규칙 (12 v1.9 패치 예정)

1. Phase A 시작 시 Claude가 snapshot 페이지 1회 조회
2. `generated_at` 24시간 초과 또는 `failed_metrics`에 핵심 지표 포함 시
   → 기존 12 v1.5 절차(MCP/FRED fetch/web_search)로 자동 폴백
3. web_search 교차확인은 F&G Index + 10Y 당일 장중치 2건만 수행
4. `meta.is_new_data = false` 시 → TRACK-CSR-16 주간 프레이밍 규칙 적용 검토

## 장애 대응

- 개별 지표 실패: 해당 지표만 grade="불가", 나머지 정상 기록
- 핵심 지표(10Y/2Y/VIX) 전량 실패: exit 1 → Actions 실패 알림
- Notion 기록 실패: Actions 실패 알림 → EDT 측은 자동 폴백이므로 발행 중단 없음
