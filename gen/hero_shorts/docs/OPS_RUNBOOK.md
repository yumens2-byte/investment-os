# Hero Shorts 운영 런북 (v2.4.6 — 2026-09-23 기준)

## 1. 현재 상태
- 컷1 생성 완료: fal(minimax/h3) — `ep86_cut1.mp4` 768x1344·10.14s·오디오 포함, G1~G4 통과(세션 검증)
- 남은 생성: 컷2~6 (컷당 $0.60) / **지출 누계 $0.60**
- 파이프라인 전 구간(제출→폴링→다운로드) 작동 확인
- **스토리 소스 SSOT 전환**: 2026-09-23부터 인덱스 페이지 기반이 아니라 **`EDT 에피소드 트래커` DB 직독**이 정식 구조

## 2. 실행 절차 (GitHub Actions)
1. Actions → 🎬 Hero Shorts Gen → Run workflow
2. `step: plan` 또는 `step: generate`, `only:` 칸 (기본 2 = 단건, 연속생성금지 가드 상시 유효)
3. 1분 전체 영상은 `step: run`과 `confirm_all: true`를 함께 선택한다. 이 모드는
   plan→전체 컷 생성→합본→QC를 **같은 러너에서** 끝낸다. GitHub-hosted runner는 실행마다
   초기화되므로 여러 `generate` 실행의 산출물을 다음 실행의 `assemble`에서 합칠 수 없다.
4. `run`은 전체 컷 과금 승인이 없으면 실행 전에 종료하며, fal 백엔드만 사용한다.
5. 성공 시 실행별 아티팩트 `hero-shorts-<run id>-<attempt>` 다운로드 → 세션에 첨부
6. 세션에서: QC 결과 확인→G5→제르니오 예약
- 예약 스케줄(화·목 UTC 01:00 = KST 10:00)은 plan 생성 전용(0원). 생성은 수동 dispatch만.

## 3. 스토리 소스 구조 (v2.4.6)
- SSOT DB: `EDT 에피소드 트래커`
  - database id: `32a9e71588b843e1a650d2c9c87d1d9f`
  - data source id: `bdc5e21c-58eb-40f9-b659-8c6d33fd0dae`
  - default view url: `view://741c7412-1afe-4b6d-a48f-a89d688e1fa6`
- 세션/개발: Notion MCP `query_data_sources` SQL 사용
- Actions: 공식 Notion REST `data-sources/{id}/query` 사용
- 동일 번호 다중 행은 `특이사항`의 폐기 마커 + `발행 상태=완료` + `createdTime` 최신순으로 정본 선택

## 4. 트래커 DB 실측 요약 (9/23)
- Ep86~93는 트래커 DB에서 직접 조회 가능함을 세션 실측으로 확인
- Ep86은 **2행 존재**:
  - 폐기 행: `No Battle / Gold Bond Muscle / 진행중`
  - ACT2 정본 행: `Tactical Victory / (트래커 표기: Guardian of Capital) / 완료 / Balance 27`
- Ep93까지 DB에 존재하며, 9/22 생성분까지 적재되어 있음
- 트래커 `메인 히어로` 표기는 운영 이력용 원문으로 보존되며, 영상 캐논 표기는 항상 `EDT`/`canon.py`가 우선한다

## 5. 장애 이력 6건 — 해소/관리
| 일시 | 증상 | 원인 | 상태 |
|---|---|---|---|
| 9/21 | VERSION ImportError | 웹 업로드가 `__init__.py`를 빈 파일로 덮어씀 | 해소 |
| 9/21 | plan FileNotFoundError | `gen/data/out/ep86_plan.json` 미커밋 | 해소 |
| 9/22 | 연속생성금지 RuntimeError | `only` 빈 칸 실행(폼 설명 오표기) | 해소 |
| 9/22 | fal HTTP 403 | 모델 접근권·빌링 | 해소 |
| 9/22 | ffprobe FileNotFoundError | 러너 ffmpeg 미기본 | 해소 |
| 9/23 | plan 경로 불일치 | 기존 레포는 `NOTION_API_KEY`, Hero Shorts만 별도 인덱스/`NOTION_TOKEN` 기대 | **DB 직독 구조로 전환 착수** |
| 9/23 | `run`이 실제 영상 대신 dummy 생성 | workflow가 `run`에 `--backend fal`을 전달하지 않음 | 해소 |
| 9/23 | 단계별 실행 뒤 합본 파일 유실 | GitHub-hosted runner는 실행 간 파일을 보존하지 않음 | 동일 실행 E2E로 해소 |
| 9/23 | fal 제출 경로 오류/403 | 구형 `minimax/h3/text-to-video` endpoint 지정 | Hailuo 2.3 공식 slug로 교체 |

## 6. 알려진 관리 사항
- **중복 디렉터리 `gen/hero_shorts/hero_shorts/` 잔존** → `git rm -r gen/hero_shorts/hero_shorts` 필요
- 음성 잔재: 컷 말미 "Thanks for watching!"(H3 임의 삽입, 0.08s) → 합본 시 오디오 트림/BGM 커버
- `costs.json`은 러너 간 미누적 → 지출은 아티팩트 수 기준 수동 관리(현재 $0.60)
- 웹 업로드 금지 — 반영은 zip 해제 후 git 커밋·푸시로 통일(폴더 구조 파손 방지)
- 레포 60일 미활성 시 스케줄 자동 정지 → 주기적 수동 실행으로 유지
- Actions 노션 접근은 기존 레포 시크릿 `NOTION_API_KEY`를 기본 재사용하고, `NOTION_TOKEN`도 별칭으로 허용

## 7. 발행 체크리스트
- [ ] 컷2~6 생성 ($3.00)
- [ ] 세션 합본 720x1280 + 음성 잔재 커버
- [ ] QC 4게이트 + G5
- [ ] 제르니오 예약 / 발행 후 `list_posts` 회독
