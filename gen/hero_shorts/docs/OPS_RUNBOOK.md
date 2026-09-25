# Hero Shorts 운영 런북 (v2.6.0 — 2026-09-24 기준)

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

## 7. 발행 자동화 (v2.6.0 — 상세설계 ④~⑧)
- ④ 에피소드 자동 결정: `--ep` 미지정 시 트래커에서 다음 미발행 회차(발행 상태≠완료, 폐기 제외, 최소 번호) 자동 선택 — `ledger.find_next_episode()`
- ⑤ 캡션 자동 생성: `caption` 단계 또는 `zernio_publish`에서 plan+ROLE_LINES 기반 생성(새 사실·수치 미생성) — `gen/hero_shorts/caption.py`
- ⑥ 미디어 업로드 자동화: `upload_media` 단계 — `gsk upload`→`gsk download`로 공개링크 회수. `zernio_publish`에서 `--media-url` 생략 시 최종 영상 자동 업로드 — `gen/hero_shorts/mediashare.py`
- ⑦ 발행 원장 내구화: `publish_state.json`/`costs.json`을 AI Drive `/hero-shorts/`에 미러링·복원(실행 시작 시 로컬 부재분 복원, 발행 후 업로드). 비활성화: `HERO_STATE_SYNC=0` — `gen/hero_shorts/statestore.py`
- ⑧ 트래커 역동기화: 발행 완료 후 plan의 `source.row_id` 행 `발행 상태=완료` PATCH(Notion REST 토큰 필요 — gsk notion은 페이지 갱신 미지원). 결과는 원장 `tracker_sync`에 기록 — `ledger.update_publish_status()`
- v2.6.1 하드닝: `cmd_publish`도 발행 원장 보호 가드 적용(run/publish 재실행 시 기존 발행 기록 덮어쓰기 차단), `HERO_STATE_FILE` 환경변수로 원장 경로 격리(리허설·테스트 전용), statestore 업로드 실패 시 `aidrive mkdir` 1회 재시도, G5 검사기 UA 헤더 + GET 폴백(토큰 URL HEAD 403 실측 대응)

## 8. 승인 게이트 — 2페이즈 자동화 (v2.7.0)
- 구조: 러너1(화·목 08:00) `run --ep N --request-approval` → 텔레그램 영상+[승인]/[보류] 버튼 →
  러너2(화·목 10:00) `resolve_approvals` → **승인건만** `zernio_publish --ep N --require-audible-audio`
- fail-closed: `HERO_APPROVAL_REQUIRED=1` 또는 승인 요청 이력 존재 시, `approval.status=APPROVED`가 아니면 발행 거부
- 상세설계: docs/APPROVAL_GATE_DESIGN.md (보안 모델·상태 머신·테스트 목록 포함)
- 신규 환경변수: `TELEGRAM_BOT_TOKEN`(시크릿), `HERO_TELEGRAM_CHAT_ID`(허용 채팅 — 콜백 허용목록)
- 봇 생성·토큰 발급·스케줄 스킬 2건 등록은 마스터 조작/승인(휴먼게이트)

## 9. 다음 작업
1. 상태 파일/비용 원장 동시성 파일락
2. 대사 품질/속도 파라미터 튜닝
3. Genspark 스케줄 스킬 신규 등록(실행 층 — 러너1·2 포함) — 등록 주기/수신처 마스터 승인 필요
4. Ep87 실전 파일럿 — 러너1 수동 실행 → 텔레그램 수신·승인 탭 → 러너2 수동 실행(발행 확인) 후 스케줄 승인
