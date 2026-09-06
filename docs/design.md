# Investment OS — 시스템 설계서

이 문서의 **정본은 Notion**에서 관리합니다. 이 파일은 공개 저장소용 안내입니다.

## 정본 위치

- Notion 「🏗️ Investment OS — 시스템 설계 문서 (아키텍처 · 흐름도 · 기능 명세)」
- 접근 권한: PM팀 · GTT팀 · CLDE팀 · 품질팀

## 공개 저장소에서 볼 수 있는 것

시스템 구조 · 발행 세션 · 모듈 버전 · 개발 규약은 저장소 루트의
[README.md](../README.md)에 정리돼 있습니다.

## 배포 전 필수 검증

```bash
ruff check .                      # 2회 연속 PASS
pytest tests/ -q                  # 2회 연속 PASS
python test_tier1_signals.py      # Tier1 시그널 회귀
python test_e2e_pipeline.py       # E2E 파이프라인 회귀
```

알림 회귀는 `ci_alert_tests.yml`이 PR에서 자동 실행하며, 실패 시 머지가 차단됩니다.

## 버전

시스템 버전은 `config/settings.py`의 `SYSTEM_VERSION` 상수가 유일한 정본입니다.
이 값은 대시보드 이미지 푸터와 `core_data.json`의 `version` 필드에 그대로 렌더되므로,
문서에 버전 숫자를 하드코딩하지 않습니다.
