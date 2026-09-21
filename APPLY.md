# 반영 방법 (investment-os 저장소 루트 기준)

## 방법 1 — 파일 복사 (권장, 가장 단순)
1. 압축 해제 → `gen/` 폴더와 `.github/workflows/hero_shorts_gen.yml`을 저장소 루트에 그대로 복사
2. 커밋: git add gen .github/workflows/hero_shorts_gen.yml && git commit -m "feat: add gen hybrid pipeline v2.4 (canon-locked, budget-capped, dummy E2E)"
3. 이 파일(APPLY.md)은 커밋에서 제외

## 방법 2 — 패치 적용 (커밋 이력 그대로)
git am hero-shorts-gen-v24-all.patch  (커밋 3건: 5a5dc7d·b2a8799·0209180 재현)

## 실측 기준 (2026-09-21)
- 전수테스트 14/14 통과, 기존 파일 수정 0건 (gen/ + 워크플로우 신규만)
- fal 엔드포인트: minimax/h3/text-to-video (공식 문서 확정)
- 남은 것: 실영상 테스트(컷2 단건 $0.60 → 컷3~6) + QC + 9/23 제르니오 예약
