# narrative v2.1 배포 체크리스트 (2026-09-06)

이전 배포(`b178a75`) 위에 얹는 후속 패치. **전체 파일 교체.**

## 반영 순서

- [ ] 1. `comic/card_news_generator.py`   1.1.0 → **1.2.0**  (force_html 인자 신설)
- [ ] 2. `publishers/narrative_visual.py` 1.0.0 → **1.1.0**  (force_html=True 전달)
- [ ] 3. `tests/test_narrative_v2.py`     (테스트 3케이스 추가)
- [ ] 4. `.github/workflows/main.yml`     (NB-6 concurrency 그룹)

> 1번을 2번보다 먼저 올릴 것. 순서가 뒤바뀌면 `generate_single_card()`에
> `force_html` 인자가 없어 `TypeError`가 난다.
> 4번은 독립적이라 순서 무관.

## 변경 내용

### ② card_market HTML 폴백 강제 (승인분)

| | 변경 전 | 변경 후 |
|---|---|---|
| narrative의 card_market | Gemini 1순위 → 실패 시 HTML | **HTML 직행** |
| 코믹 파이프라인 `generate_cards()` | Gemini 1순위 | **무변경** |

근거: 2026-09-06 dry_run 1회차 실측에서 Gemini 생성 카드에
- 텍스트 오타 2건 (`Volablity` / `Liquiblity`)
- 레이더 축 6개 중 4개가 실제 Market Score 키와 무관한 값으로 임의 생성
  (`Momentum` / `Value` 등 — 실제는 inflation / risk / financial_stability / commodity_pressure)

Gemini 이미지 모델의 텍스트 렌더 한계로 프롬프트 교정 불가.

### ③ NB-6 concurrency 그룹 (승인분)

```yaml
concurrency:
  group: narrative-${{ github.ref }}
  cancel-in-progress: false
```

근거: dry_run 4회차에서 '직전 2회 제외' 규칙 위반 실측.
동시 실행 시 뒤 실행이 앞 실행의 cache save 이전 상태를 restore해
`narrative_visual_history.json` 이력이 동결된다.
`cancel-in-progress: false` — 진행 중 발행을 끊지 않고 큐잉 (morning/alert와 동일 패턴).

### ① vs_card 스타일 풀 — **변경하지 않음** (마스터 판단)

4종 유지. dry_run 5회차 실측에서 vs_card는 텍스트·수치가 모두 정확했고
화풍 변화는 의도된 변칙성이므로 그대로 둔다.

## 검증 결과

- `pytest` 2회 연속 **108 passed** (기존 105 + 신규 3)
- `ruff --select F,E9` 2회 연속 **All checks passed**
- `main.yml` YAML 파싱 OK / cron 5개 문자열 불변 / timeout 30분 유지
- 변경 규모: 4 files, +86 / -8

## 배포 후 확인

- [ ] narrative `workflow_dispatch` dry_run 실행
- [ ] card_market이 뽑힌 회차에서 로그 `[CardNews] Card 1 force_html=True → HTML 직행` 확인
- [ ] 해당 이미지가 1080×1350 HTML 렌더이고 **한글이 정상 표시**되는지 확인
      (Gemini 경로였던 1회차는 1024×1024 정사각 + 전면 영어였음)
- [ ] 연속 실행 시 두 번째 run이 큐잉되는지 확인 (NB-6)
