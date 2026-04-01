# Investment OS (v1.15.0)

미국 금융시장 데이터 자동 수집 → 19개 시그널 분석 → X/텔레그램 자동 발행 시스템.

## 핵심 기능
- 19개 시그널 기반 시장 분석 (Tier 1/2/3)
- 8종 실시간 Alert (VIX/SPY/OIL/FED/CRISIS + ETF랭킹/레짐전환)
- ETF 6종 상세 전략 + 시그널 기반 매수/매도 근거 자동 생성
- X + 텔레그램 무료/유료 채널 자동 발행
- GitHub Actions 6세션 완전 자동 운영

## 실행
```bash
python main.py run all --session morning --mode tweet
```

## 문서
- [시스템 설계서](docs/design.md)
- [운영자 매뉴얼](docs/operator_manual.md)
- [사용자 매뉴얼](docs/user_manual.md)

## 테스트 (403/403 PASS)
```bash
python test_tier1_signals.py     # 145건
python test_tier2_signals.py     # 126건
python test_tier3_signals.py     # 42건
python test_b5_b6.py             # 51건
python test_b7_etf_rationale.py  # 39건
```
