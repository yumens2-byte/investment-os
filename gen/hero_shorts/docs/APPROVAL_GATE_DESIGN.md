# Hero Shorts 인스타 발행 승인 게이트 상세설계 (v2.7.0 — 2026-09-26)

## 1. 목표와 제약
- 목표: 전 구간 자동화 + **인스타 발행 직전 텔레그램 승인 확인** — 승인 시에만 발행.
- 제약(실측): 세션은 하드 리미트 존재 → 승인 대기를 세션 안에서 폴링하는 구조는 탈락.
  **2페이즈 스케줄**로 분리해 대기를 세션 밖(원장 + 텔레그램)에 둔다.
- 실측 근거: `api.telegram.org` 도달성 HTTP 401(정식 봇 API 응답 JSON, 0.5s) — 네트워크 경로 열림 확인.
  토큰 주입 시 송수신 동작 (본 설계 개발은 전부 mock 오프라인 검증).

## 2. 2페이즈 흐름
| 페이즈 | 시각(KST) | 수행 | 원장 상태 변화 |
|---|---|---|---|
| 러너1(생성) | 화·목 08:00 (신설) | `run --ep N --request-approval` → plan~QC~원장 기록 → 미디어 업로드 → 텔레그램 sendVideo+버튼 | `QC_PASSED_READY` + `approval.status=READY_FOR_APPROVAL` |
| 마스터 | 여유 시간 | 텔레그램에서 영상 확인 후 [✅ 승인]/[⏸ 보류] 탭 | `APPROVED` / `HOLD` |
| 러너2(발행) | 화·목 10:00 (기존) | `resolve_approvals` → 승인건만 `zernio_publish --ep N` | `published` + 트래커 역동기화 |
| 러너2(미승인) | 〃 | 무응답 → 발행 없음, 경고 로그(영상·원장 보존) | `READY_FOR_APPROVAL` 유지 |

- 기존 CI(화·목 10:00 plan 0원)는 유지 — 러너1·2는 Genspark 스케줄 스킬(실행층)에 배치(등록은 휴먼게이트).
- 보류(HOLD) 회차: 영상 폐기하지 않음. 마스터가 늦게 승인하면 다음 러너2에서 resolve가
  미응답 콜백을 계속 수신(getUpdates 미확인 유지 원리)해 `HOLD→APPROVED` 반영 가능.

## 3. 상태 머신 (원장 `ep<N>.approval` 블록)
```
(없음) ─request_approval─▶ READY_FOR_APPROVAL ─callback approve─▶ APPROVED ─zernio_publish─▶ (발행)
                              │                └─callback hold───▶ HOLD ─┐(재승인 콜백 시 APPROVED)
                              └─(재요청 시 갱신)◀───────────────────────┘
```
- approval 블록 필드: `status, requested_at, chat_id, message_id, media_url, video_sent,
  decided_at, decided_by, callback_id`
- `cmd_publish`/`cmd_zernio_publish`는 기존 `approval` 블록을 보존(발행 후에도 결정 이력 유지).

## 4. 발행 가드 (fail-closed)
`cmd_zernio_publish` 중복방지 가드 직후:
- 게이트 활성 조건: `HERO_APPROVAL_REQUIRED=1` **또는** 원장에 승인 요청 이력 존재.
- 활성 상태에서 `approval.status != APPROVED` → 발행 거부(RuntimeError, publish_runtime 호출 없음).
- 게이트 비활성(환경 미설정 + 요청 이력 없음) → 기존 수동 흐름 그대로(하위호환).

## 5. 보안 설계
| 항목 | 설계 |
|---|---|
| 토큰 | `TELEGRAM_BOT_TOKEN` — 환경변수 전용(시크릿, 소스·문서 기록 금지) |
| 채팅 허용목록 | `HERO_TELEGRAM_CHAT_ID` 또는 기존 `TELEGRAM_PAID_CHANNEL_ID` 별칭(쉼표 복수) — 콜백 chat_id가 목록 밖이면 무시+ack |
| 1회 소비 | `answerCallbackQuery` + 메시지 "결정 반영" 편집 + 원장 `decided_at` 멱등 키 |
| 전송 실패 | sendVideo 실패(용량·네트워크) → sendMessage 링크 폴백(버튼 동일) |
| 설정 누락 | 승인 절차 전체 즉시 실패 — 조용한 통과 없음(fail-closed) |
| 원장 | approval 결정 기록은 비공개 원장(publish_state.json)에만 — 문서에는 변수명만 |

## 6. 모듈·인터페이스
- 신규 `gen/hero_shorts/approval.py`: `request_approval` / `poll_callbacks` / `resolve_pending`
  / `answer_callback` / `edit_message` / `_call_api`(JSON·multipart 이중) / `ApprovalError`
- `pipeline.py`:
  - 신규 단계: `request_approval`(단독 재요청용), `resolve_approvals`(러너2 선두)
  - `run --request-approval`: 러너1 원커맨드 — E2E 후 캡션/업로드/승인 요청까지
  - `--lookback-hours`(기본 36), `HERO_APPROVAL_REQUIRED` 게이트, 원장 approval 보존
- 러너2 조립: `resolve_approvals && zernio_publish --ep <N> --require-audible-audio`
  (ep 고정 권장 — 러너1 로그의 회차 사용)

## 7. 테스트 (TestV270ApprovalGate — 전부 mock 오프라인)
1. 설정 누락 fail-closed 2건 2. 버튼 페이로드+원장 기록 3. 콜백 파싱+채팅 허용목록 필터
4. resolve 승인 반영+멱등 5. 발행 가드 4건(미승인 차단/요청없음 차단/승인 통과/게이트 미설정 하위호환)

## 8. 남은 운영 전제 (개발 외, 마스터 조작/승인)
1. BotFather 봇 생성 → `TELEGRAM_BOT_TOKEN` 발급, 마스터 /start 후 chat_id 확정 → `HERO_TELEGRAM_CHAT_ID`
2. 스케줄 스킬 2건(러너1 08:00 / 러너2 10:00) 등록 — 휴먼게이트
3. 기존 전제 유지: 트래커 Ep87+ 행 등록, 실행 환경 Notion 토큰
4. 첫 실전: Ep87 파일럿 — 러너1 수동 실행 → 텔레그램 수신 확인 → 승인 탭 → 러너2 수동 실행(발행 확인) 후 스케줄 승인
