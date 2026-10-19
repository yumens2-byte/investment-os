"""
gen/nuna_weekly — Iron Nuna 주간 미국시장 뉴스 쇼츠 파이프라인
================================================================
설계서: Claude Docs "NUNA 주간 뉴스 쇼츠 상세설계서" (2026-10-19)

흐름: preflight → collect → script → tts → images → clips → assemble → qc
      → stage → notify  (워크플로 A: nuna_weekly_gen.yml)
      publish → verify  (워크플로 B: nuna_weekly_publish.yml)

hero_shorts 를 import 하지 않는 자립형 패키지다(설계서 1장 Q1).
"""

VERSION = "1.0.0"
