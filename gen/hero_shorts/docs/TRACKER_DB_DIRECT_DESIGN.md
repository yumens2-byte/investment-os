# Hero Shorts DB 직독 전환 설계 (v2.4.6)

## 배경
기존 Hero Shorts는 `EDT Arc State Ledger Index` 페이지의 코드블록에서 회차별 page_id를 읽고, 각 에피소드 페이지의 ```json 블록을 다시 읽는 2단계 구조였다.

마스터 확인 결과 실제 운영 SSOT는 별도 페이지 인덱스가 아니라 **`EDT 에피소드 트래커` Notion DB** 이다. 따라서 기존 구조는 운영 의도와 불일치했다.

## 실측 사실
- 트래커 DB URL: `https://app.notion.com/p/32a9e71588b843e1a650d2c9c87d1d9f?v=741c74121afe4b6da48fa89d688e1fa6&source=copy_link`
- DB id: `32a9e71588b843e1a650d2c9c87d1d9f`
- view fetch 실측 결과 data source id: `bdc5e21c-58eb-40f9-b659-8c6d33fd0dae`
- SQL query 실측 결과 Ep86~93 행 조회 가능
- Ep86은 2행 존재(폐기 행 + ACT2 정본 행)

## 전환 원칙
1. SSOT는 트래커 DB다.
2. 회차 선택은 `번호` 속성 기준.
3. 동일 번호 다중 행은 다음 우선순위로 정본 선택:
   - `특이사항`에 `논리적 폐기`가 없는 행
   - `발행 상태 = 완료`
   - `createdTime` 최신
4. DB의 `메인 히어로` 표기는 캐릭터 캐논 최종명과 다를 수 있으므로, 시각 캐논은 `canon.py`가 우선한다.
5. Actions는 기존 레포 관례 시크릿 `NOTION_API_KEY`를 재사용한다.

## 구현 구조
- `ledger.py`
  - Actions: `POST /v1/data-sources/{data_source_id}/query`
  - 세션: Notion MCP `notion-query-data-sources` SQL
  - 결과 행을 cutplanner 입력용 arc_state dict로 정규화
- `pipeline.py`
  - plan 단계는 변경 없음; `ledger.load_episode()`가 DB 직독 결과를 돌려줌

## 테스트 전략
- 오프라인 단위 테스트
  - 다중 행(Ep86) 중 정본 선택 검증
  - DB row → episode dict 정규화 검증
- 라이브 실측
  - 트래커 DB SQL query로 Ep86~93 행 존재 확인
  - query_data_sources 사용량 제한 도달 시 라이브 전수 질의 대신 단일 샘플 확인 후 오프라인 회귀로 대체

## 주의
- Notion MCP `query_data_sources`는 사용량 제한에 걸릴 수 있다. 이 경우 세션 라이브 질의는 `Try again later` 또는 [Learn more](https://app.notion.com/notion-mcp?source=mcp_tool_upsell_mcp&tool=query_data_sources&product=business&mcpRequestId=aa4a4b24-82e8-4cb6-8d65-5854e3f9bc1a&mcpUpsellOpportunityId=0695dabf-bd9b-479d-955e-126f10c57047&mcpClickSource=markdown_link&spaceId=11403613-7846-41be-b116-8c8a5ed95f63&notionAccountId=1c2d872b-594c-81f5-a74f-000283e061ef&action=learn_more).
- 레거시 인덱스 페이지는 참조 문서로 남을 수 있으나, Hero Shorts 스토리 소스로는 더 이상 사용하지 않는다.
