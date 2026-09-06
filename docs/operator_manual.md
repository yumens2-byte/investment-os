# Investment OS — 운영자 매뉴얼

이 문서의 **정본은 Notion**에서 관리합니다. 이 파일은 공개 저장소용 안내입니다.

## 정본 위치

- Notion 「Investment OS — 자동화 허브」 하위 운영 문서
- 접근 권한: PM팀 · GTT팀

## 공개 저장소에서 볼 수 있는 것

세션별 실행 시각, 워크플로 구성, 환경 변수, 로컬 실행 방법은
저장소 루트의 [README.md](../README.md)에 정리돼 있습니다.

## 장애 대응 시 참고

- 에러 발생 시 Notion 「개발 에러 회고록」에 원인과 예방 규칙을 기록합니다.
- 파이프라인·파라미터·시그니처 등 구조적 변경은 Notion 「GTT팀 공통 개발 지침서」에 기록합니다.
- 캐시는 CACHE-FREEZE 규약을 따릅니다 — `restore`는 실행 이전,
  `save`는 실행 이후 + `if: always()`.

## 버전

시스템 버전은 `config/settings.py`의 `SYSTEM_VERSION` 상수가 유일한 정본입니다.
문서에 버전 숫자를 하드코딩하지 않습니다.
