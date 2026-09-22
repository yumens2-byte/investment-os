# Hero Shorts 운영 런북 (v2.4.5 — 2026-09-22 기준)

## 1. 현재 상태
- 컷1 생성 완료: fal(minimax/h3) — `ep86_cut1.mp4` 768x1344·10.14s·오디오 포함, G1~G4 통과(세션 검증)
- 남은 생성: 컷2~6 (컷당 $0.60) / **지출 누계 $0.60**
- 파이프라인 전 구간(제출→폴링→다운로드) 작동 확인

## 2. 실행 절차 (GitHub Actions)
1. Actions → 🎬 Hero Shorts Gen → Run workflow
2. `step: generate`, `only:` 칸 (기본 2 = 단건, 연속생성금지 가드 상시 유효)
3. 성공 시 아티팩트 `hero-shorts-out` 다운로드 → 세션에 첨부
4. 세션에서: 합본(720x1280 트랜스코드) → QC 4게이트+G5 → 제르니오 예약
- 예약 스케줄(월·수 UTC 01:00=KST 10:00)은 plan 생성 전용(0원). 생성은 수동 dispatch만.

## 3. 장애 이력 5건 — 전부 해소
| 일시 | 증상 | 원인 | 해소 |
|---|---|---|---|
| 9/21 | VERSION ImportError | 웹 업로드가 `__init__.py`를 빈 파일로 덮어씀 | __init__ 수복(VERSION 2.4.4) |
| 9/21 | plan FileNotFoundError | `gen/data/out/ep86_plan.json` 미커밋 | plan 재상장 + generate 시 자동 plan 폴백 |
| 9/22 | 연속생성금지 RuntimeError | `only` 빈 칸 실행(폼 설명 오표기) | 기본값 2 + 설명 정정 |
| 9/22 | fal HTTP 403 | 모델 접근권·빌링(대시보드 조치로 해소) | 오류 본문 노출판 generator |
| 9/22 | ffprobe FileNotFoundError | 러너 ffmpeg 미기본 | Install ffmpeg 스텝 (반영 완료) |

## 4. 알려진 관리 사항
- **중복 디렉터리 `gen/hero_shorts/hero_shorts/` 잔존** → `git rm -r gen/hero_shorts/hero_shorts` 필요
- 음성 잔재: 컷 말미 "Thanks for watching!"(H3 임의 삽입, 0.08s) → 합본 시 오디오 트림/BGM 커버
- `costs.json`은 러너 간 미누적 → 지출은 아티팩트 수 기준 수동 관리(현재 $0.60)
- 웹 업로드 금지 — 반영은 zip 해제 후 git 커밋·푸시로 통일(폴더 구조 파손 방지)
- 레포 60일 미활성 시 스케줄 자동 정지 → 주기적 수동 실행으로 유지
- Actions(러너) 노션 직독용 `NOTION_TOKEN` 시크릿은 qc/assemble/run 스텝까지 Actions에서 돌릴 때만 필요(현재 불요)

## 5. 발행 체크리스트 (2026-09-23 10:00 KST 목표)
- [ ] 컷2~6 생성 ($3.00) — 컷1은 완료
- [ ] 세션 합본 720x1280 + 음성 잔재 커버
- [ ] QC 4게이트 + G5(캡션 ≤2200자·면책 문구·https 영구 URL·예약 6일 한도)
- [ ] 제르니오 `create_post --schedule_at 2026-09-23T10:00 Asia/Seoul` — 미리보기 마스터 승인 후 예약
- [ ] 발행 후 `list_posts` 회독

## 6. 인스타 파이프라인 (점검 완료 9/22)
- 계정 `_tiger18272` isActive·enabled, 토큰 ~11/19, Reel 단일영상 자동 변환, 예약은 젠스파크 영구 파일 URL만 유효
- 발행 이력 0건(초기 상태) — 첫 발행이 9/23 예약분
