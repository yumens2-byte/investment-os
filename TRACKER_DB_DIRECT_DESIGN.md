# Hero Shorts DB 직독 전환 설계 (v2.4.10)

## 배경
Hero Shorts의 운영 SSOT는 별도 인덱스 페이지가 아니라 **`EDT 에피소드 트래커` Notion DB** 다. 따라서 스토리 소스는 트래커 DB 직독을 기준으로 유지한다.

## v2.4.10 기준 핵심 변경
- 트래커 관련 운영 식별값 기본 하드코딩 제거
- `ledger.py`는 환경변수 주입이 없으면 즉시 실패
- 메일 주소·운영 식별값을 소스 기본값으로 두지 않음
- 발행 단계는 `zernio_publish`로 통합 가능
- 발행 전 오디오 가청성 검사(`--require-audible-audio`) 지원

## 구성 원칙
1. SSOT는 트래커 DB다.
2. 회차 선택은 `번호` 속성 기준.
3. 동일 번호 다중 행은 아래 우선순위로 정본 선택한다.
   - `특이사항`에 `논리적 폐기`가 없는 행
   - `발행 상태 = 완료`
   - `createdTime` 최신
4. DB의 `메인 히어로` 표기는 운영 이력용 원문으로 두고, 시각 캐논은 `canon.py`가 우선한다.
5. 개인정보/운영 식별값/토큰은 소스 기본값으로 두지 않고 환경변수로 주입한다.

## 필수 환경변수
### 트래커 설정
- `HERO_SHORTS_TRACKER_DB_ID`
- `HERO_SHORTS_TRACKER_VIEW_URL`
- `HERO_SHORTS_TRACKER_DATA_SOURCE_ID`

### Notion API 직접 조회 시
- `NOTION_API_TOKEN`
- 또는 `NOTION_TOKEN`
- 또는 `NOTION_API_KEY`

## 구현 구조
- `ledger.py`
  - Actions: `POST /v1/data_sources/{data_source_id}/query`
  - 세션: Notion MCP `notion-query-data-sources` SQL
  - 누락 설정은 `_require_tracker_config()`에서 차단
- `pipeline.py`
  - `plan` 단계는 `ledger.load_episode()` 결과를 사용
  - `zernio_publish` 단계는 발행/모니터/알림을 통합

## 테스트 기준
- 다중 행 중 정본 선택 검증
- DB row → episode dict 정규화 검증
- 트래커 설정 누락 시 즉시 실패 검증
- 전수 테스트: `python3 -m gen.hero_shorts.tests.test_all`

## 운영 메모
- 트래커 값은 비공개 운영 공간(Notion)과 실행 환경변수에서만 관리한다.
- 공개 산출물(zip, patch, 코드 전달본)에는 개인 메일 주소나 운영 식별값을 넣지 않는다.
