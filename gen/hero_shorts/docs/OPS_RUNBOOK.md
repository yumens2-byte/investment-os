# Hero Shorts 운영 런북 (v2.4.10 — 2026-09-24 기준)

## 1. 현재 상태
- SSOT: `EDT 에피소드 트래커` DB 직독 구조 유지
- 발행 파이프라인: `zernio_publish` 단계로 통합
- 게시 상태 조회: `gsk zernio list_posts --post_id <id>` 기반
- 공개 링크 회수: 게시 완료 시 `platformPostUrl` 저장
- 지연/실패 알림: Gmail 알림 지원
- 무음 발행 차단: `--require-audible-audio` 지원
- 보안 정리: 개인정보 메일/운영 식별값 기본 하드코딩 제거

## 2. Ep86 최신 실측 상태
- 게시 상태: `published`
- 공개 링크: `https://www.instagram.com/reel/Ddo2g8xj74H/`
- 현재 발행본 오디오 감지 결과:
  - `audible: false`
  - `mean_volume_db: -91.0`
  - `max_volume_db: -91.0`
- 결론: 현재 발행본은 사실상 무음이며, 다음 단계는 **대사/음성 생성 + 믹싱 파이프라인** 구현이다.

## 3. 실행 절차
1. `plan` → `generate` → `assemble` → `qc`
2. 발행 전 G5 검증 + 오디오 가청성 검증
3. `zernio_publish` 실행
4. 게시 완료/지연/실패 결과를 `publish_state.json`에 기록

### 발행 명령 예시
```bash
python3 -m gen.hero_shorts.pipeline zernio_publish \
  --ep 86 \
  --plan <plan_json> \
  --caption-file <caption_txt> \
  --media-url <genspark_file_url> \
  --require-audible-audio
```

### 래퍼 스크립트
```bash
gen/run_zernio_publish.sh <ep> <plan_json> <caption_txt> <media_url> [schedule_at]
```

## 4. 필수 환경변수
### 발행/알림
- `HERO_ZERNIO_ACCOUNT_ID`
- `HERO_ALERT_EMAIL`
- `HERO_FROM_ACCOUNT`

### 트래커
- `HERO_SHORTS_TRACKER_DB_ID`
- `HERO_SHORTS_TRACKER_VIEW_URL`
- `HERO_SHORTS_TRACKER_DATA_SOURCE_ID`

### 생성
- `FAL_AI_KEY`
- `FAL_ENDPOINT`

### Notion 직접 조회 시
- `NOTION_API_TOKEN`
- 또는 `NOTION_TOKEN`
- 또는 `NOTION_API_KEY`

## 5. 운영 규칙
- 개인정보/운영 식별값/토큰은 소스 기본값으로 두지 않는다.
- 발행용 미디어는 가청 오디오 검증을 통과한 파일만 사용한다.
- 문서화는 비공개 Notion 공간에서 유지하고, 외부 전달본은 식별값을 제거한 버전만 사용한다.
- Git 반영은 웹 업로드가 아니라 구조 유지 zip 해제 후 검토 → 커밋 방식으로 진행한다.

## 6. 다음 작업
1. 컷별 대사 스키마 설계
2. 한국어 TTS 생성 모듈 추가
3. 음성 + 영상 믹싱 단계 추가
4. 오디오 QC 통과 후 재발행
