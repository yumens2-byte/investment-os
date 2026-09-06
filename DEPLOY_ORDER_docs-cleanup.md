# 미결 3건 정리 배포 체크리스트 (2026-09-06)

P-01 / P-03 / P-05 동시 처리. 코드 로직 변경 없음 — 문서 + 파일 1개 삭제.

---

## STEP 1 — 파일 교체 4개

- [ ] 1. `docs/design.md`            (161 bytes → 안내문, 버전 표기 제거)
- [ ] 2. `docs/operator_manual.md`   (71 bytes → 안내문, 버전 표기 제거)
- [ ] 3. `docs/user_manual.md`       (107 bytes → 안내문, 버전 표기 제거)
- [ ] 4. `README.md`                 (버전 하드코딩 제거 + 정본 규약 명문화)

## STEP 2 — 파일 삭제 1개  ⚠️ 웹 UI에서 직접 수행

- [ ] 5. `publishers/run_view.py` **삭제**

GitHub 웹에서: 파일 열기 → 우상단 `···` → **Delete file** → Commit

### 삭제 근거 (2026-09-06 실측)

| 항목 | 값 |
|---|---|
| 정적 import (`.py` / `.yml` 전수) | **0건** |
| 동적 import (`importlib` / `__import__`) | **0건** |
| `publishers/__init__.py` | **빈 파일** — 재노출 없음 |
| `main.py` | `import run_view` (루트)만 사용 |
| 라인 수 | 395 (루트 `run_view.py`는 601 — 이미 분기됨) |
| `VERSION` 상수 | 없음 |

### 삭제해야 하는 이유

개정 전 **narrative 3중 발행 로직이 그대로 보존**돼 있다.

```
:163  publish_tweet_with_image(image_tweet_text, image_path)   ← 발행 ①
:273  tweet = format_narrative_tweet(narrative_text)
:274  _pub_narr(tweet)                                          ← 발행 ②
:282  vs_tweet = format_narrative_tweet(narrative_text)         ← ②와 동일 문자열
:283  _pub_vs(vs_tweet, vs_path)                                ← 발행 ③
```

지금은 호출부가 없어 무해하지만, 누군가 import 경로를 바꾸는 순간
D-1(동일 본문 2연속 발행)이 그대로 재발한다.
git 이력에 남으므로 필요 시 언제든 복원 가능하다.

---

## 변경 내용 요약

### P-01 — 버전 표기 정본 확정 (권고 C+A 채택)

- `SYSTEM_VERSION` 값 자체는 **변경하지 않음** (v1.20.0 유지)
- 문서에서 버전 숫자 하드코딩을 전부 제거 → 4중 불일치가 구조적으로 재발하지 않음
- README 개발 규약에 명문화:
  > 시스템 버전은 `config/settings.py`의 `SYSTEM_VERSION`이 유일한 정본.
  > 상수를 상향할 때는 `full_test.py`·`pilot_test.py`의 단언 2곳을 반드시 함께 수정.

**향후 릴리스에서 버전을 올릴 때 반드시 함께 고칠 곳**

| 파일 | 라인 | 현재 단언 |
|---|---|---|
| `full_test.py` | 41 | `SYSTEM_VERSION == "v1.20.0"` |
| `full_test.py` | 179 | `SYSTEM_VERSION == "v1.20.0"` |
| `pilot_test.py` | 410 | `SYSTEM_VERSION == "v1.20.0"` |

이 3곳을 놓치면 `pilot_test` job이 push 시점에 실패한다.

### P-03 — 레거시 사본 삭제 (A안)

STEP 2 참조.

### P-05 — docs 스텁 3종 정리 (B안)

"노션에서 관리" 한 줄짜리 스텁을 안내문으로 교체했다.
README가 `/docs/design.md`를 링크하므로 파일은 유지한다(삭제 시 깨진 링크 발생).

각 문서에 포함한 것:
- 정본이 Notion에 있다는 안내 + 접근 권한
- README 역링크
- design: 배포 전 필수 검증 커맨드 4종
- operator: 에러 회고록 / 공통 지침서 / CACHE-FREEZE 규약 안내
- user: 발행 채널 표 + 면책 문구

---

## 배포 후 확인

- [ ] `docs/` 3개 파일에서 `v1.24.0` 표기가 사라졌는지
- [ ] README 시스템 버전 행이 `SYSTEM_VERSION 상수가 유일한 정본`으로 바뀌었는지
- [ ] `publishers/run_view.py`가 저장소에서 사라졌는지
- [ ] 삭제 후 push 시 `pilot_test` job이 정상 통과하는지 (import 영향 없음 확인)
