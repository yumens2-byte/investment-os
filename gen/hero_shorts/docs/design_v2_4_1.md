# v2.4.1 상세설계 — 공식 노션 캐논 연동·시각 고정 계층

작성: 2026-09-21 / 근거: 노션 캐논 정본 3건 실측 판독 + 마스터 지시 "노션 캐논 참고 상세설계"

## 0. 정정 공지 (v2.4 canon.py → 공식 캐논 대조 결과)

| 항목 | v2.4 (오류) | 공식 캐논 (실측) |
|---|---|---|
| 주역 히어로 | Guardian of Capital (네이비 슈트) — **캐논 부재 캐릭터** | **EDT (Endurance D Tiger)** — 타이거+청동/강철 로만 풀 플레이트+황금 D 엠블럼+레드 케이프+전기 체인소 (CHAR_HERO_001) |
| Exposure Futures Girl | 크림슨 트레이딩 자켓 | **FG-01** — 허리길이 검정×다크퍼플 LOOSE 헤어, 다크 블랙+보라 네온 아머, 듀얼 보라 피스톨, x4 EXPOSURE 마커 |
| Leverage Muscle Man | 그린 베스트 | **LEV-01** — 인간 남성(수인형 금지), 불꽃 머리(빨강→주황), 골드 눈, 상체 노출, x3 바벨 |
| Debt Titan | (없음) | 흑색 스파이크 아머+용암 균열 스파이크 괴물 (CHAR_VILLAIN_001) |

사유: v2.4 기준선을 "컷1 영상 묘사"에서 역산해 작성 — 공식 스펙 정의서를 대조하지 않은 설계 결함. 본 v2.4.1로 정정한다.

## 1. 캐논 SSOT 체계 (4계층)

- **L1 데이터 원장** `canon_data.json` — 노션 캐논 페이지 원문 KR verbatim + REF 슬롯 + 금지 요소. 출처: 스펙 정의서 v2.18.1(34b9208c…)·패치 v2.28(3829208c…)·RCL-07(3469208c…). 수작업 편집 금지, 노션 패치 후 재동기화만 허용.
- **L2 코드 게이트** `canon.py` — 공식 원문의 영어 프롬프트 번역(필드 전수 반영) + 캐릭터별 금지 문구 + REF 슬롯 조회. 프롬프트 빌드 유일 경로.
- **L3 시각 고정** — fal `minimax/h3/reference-to-video` `reference_image_urls`(최대 12파일)로 캐릭터 시트 픽셀 고정. REF 파일은 노션 REF LOCK 관례(예: EDT RCL-07-A 1000038175.png 잠정/B 1000038171.png 확정)를 따르고, 실제 이미지 URL은 `gen/data/ref_sheets.json`에 주입(코드에 URL 하드코딩 금지). 미등록 캐릭터는 text-to-video+캐논 문구 폴백.
- **L4 시각 QC G2b** — 생성 컷 포스터와 REF 시트 비교(understand_images) + 최종 판정 마스터 눈 확인. RCL-07의 QC 기록 체계(QC-02 눈색 GOLD 잠정 허용 등)를 준용해 CONDITIONAL 허용을 명문화.

## 2. 캐논 운영 규칙 (노션 관례 준용)

1. 캐논 변경 = 노션 패치 페이지(v2.x) 발행 → 승인 → canon_data.json 재동기화. 코드 직접 수정 금지.
2. 엠블럼 D는 EDT 전용 — 타 캐릭터 프롬프트에 D 엠블럼 문구 금지(G2 검사).
3. Form 트리거 준수: Form2(4-AND: 긴장≥75·ArcDay≥5·주역 EDT·balance≥+20), Form3(5-AND: 긴장≥95·ArcDay≥14·balance≥+50·Form2 선행, 시즌 1회) — 원장 조건 미충족 시 상위 Form 묘사 금지(현 Ep86~92: Form2 조건 도달분만 반영).
4. 신규 REF 등록은 REF LOCK 패턴(파일명+QC 점수+용도+교체 조건) 기록 후 슬롯 등재.

## 3. 후속 코드 반영 (다음 커밋)

- cutplanner.py SCENES: Guardian 참조 2곳 → EDT 주역 재구성(Ep86 BATTLE: EDT 단독→Ep90+ 팀전개)
- generator.py: `fal_ref` 백엔드(ref-to-video) 추가 + 과금 실측 로그
- qc: G2b 시각 비교 게이트 추가
- 전수테스트 갱신 후 실영상 테스트(컷2 단건 $0.60) — REF 시트 주입 상태로 진행

## 4. 비용 영향

0원 — REF 시트는 기존 승인 자산(컷1 포스터·RCL-07 파일) 재활용, 생성 추가 없음. fal ref-to-video 가격은 첫 실측에서 확인.
