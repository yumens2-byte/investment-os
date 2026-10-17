# Hero Shorts YouTube 발행 전환 — 2026-10-18

- 채널: YouTube, 발행 수단: 기존 gsk Zernio create_post 경로.
- 계정 ID: 6ad38f1d80b2fa2e1133f591 (마스터 제공).
- 우선순위: CLI --account-id > HERO_YOUTUBE_ACCOUNT_ID > 위 기본값.
- 기본 플랫폼은 youtube. Instagram은 --platform instagram으로 선택 가능.
- TikTok 전용 publisher/ready/runtime 차단은 유지한다.

## 운영 실행

gsk와 연결된 Zernio 세션, 회차별 plan/영상/캡션/QC/승인 원장이 있는 운영 환경에서 실행한다.

```bash
bash gen/run_zernio_publish.sh 101 gen/data/out/ep101_plan.json gen/data/out/ep101_caption.txt https://example.com/ep101.mp4
```

URL과 회차는 실제 운영값으로 바꾼다. 위 URL은 예시이며 실행하지 않는다.
합본은 기존 HERO_COMBINED=1 인자 형식도 지원한다. 제목 정본은 plan.title이다.
승인 상태 APPROVED, QC G1~G4, 영상·오디오 및 미디어 URL 검증을 통과해야 발행한다.
published/pending/timeout 원장 기록이 있으면 중복 요청을 거부한다.
예약 발행 인자는 기존 schedule_at 경로를 유지한다.
기존 Instagram 발행 이력이 동일 회차에 있으면 보수적으로 중복 차단될 수 있으므로
자동으로 --allow-duplicate-post를 추가하지 않는다.

## 검증 및 제한

YouTube 제목은 본문 첫 줄(1~100자), 전체 본문은 5000자 이하로 검증한다.
YouTube 플랫폼 게시 상태와 youtube.com/youtu.be URL이 함께 있어야 완료로 기록한다.
집계 상태나 Instagram 성공만으로 YouTube 완료를 판단하지 않는다.
플랫폼별 게시시각을 기록하고 기존 승인/합본 메타를 보존한다.

GitHub Actions publish_youtube 및 기존 publish_combined 입력은 YouTube 경로로 연결된다.
새 Actions 러너에는 운영 영상·원장이 없으므로 복원 없이 발행하지 않는다.
preflight는 설정 확인만 수행하며 실제 연결 상태를 검증하지 않는다.
별도 Genspark 예약 스킬/로컬 복사본은 이 커밋으로 수정되지 않는다.
기존 스킬이 tiktok.runner ready/publish를 호출하면 중단 상태가 유지된다.
그 스킬은 최신 소스를 반영한 뒤 위 canonical shell 경로를 호출하도록 변경해야 한다.

검증: unittest 14건 통과(외부 API 모킹), Python/Bash/YAML 구문 검증.
현재 작업 환경에는 gsk가 없어 계정 실조회/실발행/예약 스킬 전환은 수행하지 않았다.

공식 근거: https://docs.zernio.com/platforms/youtube
Zernio는 별도 제목 지정이 없으면 content 첫 줄을 YouTube 제목으로 사용한다.
