# Hero Shorts 운영 런북 (v2.5.2 — 2026-09-24 기준)

## 1. 현재 상태
- SSOT: `EDT 에피소드 트래커` DB 직독 구조 유지
- 발행 파이프라인: `zernio_publish` 단계로 통합
- 게시 상태 조회: `gsk zernio list_posts --post_id <id>` 기반
- 공개 링크 회수: 게시 완료 시 `platformPostUrl` 저장
- 지연/실패 알림: Gmail 알림 지원
- 무음 발행 차단: `--require-audible-audio` 지원
- 보안 정리: 시크릿(토큰·API 키)만 환경변수 주입 — HERO_* 운영 식별값은 소스 기본값 복원(마스터 승인 v2.5.2)

## 2. Ep86 최신 실측 상태
- 게시 상태: `published`
- 공개 링크: `https://www.instagram.com/reel/DdpgqdpicEi/`
- 음성 포함 재발행본 오디오 감지 결과:
  - `audible: true`
  - `mean_volume_db: -32.1`
  - `max_volume_db: -6.9`
- 결론: Ep86은 현재 **음성 포함 재발행본** 기준으로 운영한다.

## 3. 실행 절차
1. `plan` → `generate` → `assemble`
2. `voice_plan` → `tts` → `mix_audio`
3. `qc` + G5 검증 + 오디오 가청성 검증
4. `zernio_publish` 실행
5. 게시 완료/지연/실패 결과를 `publish_state.json`에 기록

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

## 4. 환경변수 정책 (v2.5.2 개정)
### 시크릿 — 환경변수 전용(소스 하드코딩 금지)
- `FAL_AI_KEY`
- `NOTION_API_TOKEN` 또는 `NOTION_TOKEN` 또는 `NOTION_API_KEY` (셋 중 하나 이상)

### 소스 기본값 복원(마스터 승인) — 환경변수로 override 가능
- 트래커 3종: `HERO_SHORTS_TRACKER_DB_ID` / `HERO_SHORTS_TRACKER_VIEW_URL` / `HERO_SHORTS_TRACKER_DATA_SOURCE_ID`
- 발행/알림 3종: `HERO_ZERNIO_ACCOUNT_ID` / `HERO_ALERT_EMAIL` / `HERO_FROM_ACCOUNT`
- 생성 1종: `FAL_ENDPOINT` (소스 기본값 `minimax/h3/text-to-video` — 가격 실측 완료 엔드포인트)

## 5. 운영 규칙
- 개인정보/운영 식별값/토큰은 소스 기본값으로 두지 않는다.
- 발행용 미디어는 가청 오디오 검증을 통과한 파일만 사용한다.
- 문서화는 비공개 Notion 공간에서 유지하고, 외부 전달본은 식별값을 제거한 버전만 사용한다.
- Git 반영은 웹 업로드가 아니라 구조 유지 zip 해제 후 검토 → 커밋 방식으로 진행한다.

## 6. 음성 파이프라인 상태
- 컷별 대사 계획 생성: `voice_plan`
- TTS 생성: `tts` (`dummy`=0원 테스트, `gsk`=실TTS)
- 음성 믹싱: `mix_audio`
- 최종 발행 전 `--require-audible-audio`로 가청성 재검증

### 예시 절차
```bash
python3 -m gen.hero_shorts.pipeline voice_plan --ep 86 --plan <plan_json>
python3 -m gen.hero_shorts.pipeline tts --ep 86 --voice-plan <voice_plan_json> --tts-backend dummy
python3 -m gen.hero_shorts.pipeline mix_audio --ep 86 --voice-plan <voice_manifest_json>
```

## 7. 다음 작업
1. 중복 발행 방지와 재조회 모드 분리 — v2.5.1 완료(차단 기본+예외 플래그)
2. 상태 파일/비용 원장 동시성 안전화
3. media_url 실가용성 검증 — v2.5.3 완료(`check_media_url_reachable` 발행 직전 실접근 확인)
4. 대사 품질/속도 파라미터 튜닝
5. plan 단계 캐논 셀프체크 — v2.5.3 완료(캐논 누락 플랜 원천 차단)
