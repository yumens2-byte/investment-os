"""
kr_sleeve/errors.py — 계산을 멈추는 사유 (DN2026_0108)

서로 다른 실패 사유를 한 문구로 묶지 않는다(STD-PROC-15).
사유 코드는 DB·운영자 알림·job summary에 그대로 남는다.
"""
from __future__ import annotations

INPUT_MISSING = "INPUT_MISSING"            # core_data.json 파일 없음·JSON 아님
INPUT_INVALID = "INPUT_INVALID"            # 계약 위반(status·command·레짐·리스크·US 비중)
INPUT_STALE = "INPUT_STALE"                # timestamp 경과 초과
ALREADY_DONE = "ALREADY_DONE"              # 오늘 이미 계산·발행 완료(재실행 방지)
SOURCE_HTTP = "SOURCE_HTTP"                # 시세 HTTP 오류·연결 실패
SOURCE_FORMAT = "SOURCE_FORMAT"            # 시세 응답 형식 변경·파싱 실패
SOURCE_INSUFFICIENT = "SOURCE_INSUFFICIENT"  # 완료 봉 부족
SOURCE_STALE = "SOURCE_STALE"              # 마지막 완료 봉이 너무 오래됨
PRICE_OUT_OF_RANGE = "PRICE_OUT_OF_RANGE"  # 종가 ≤ 0 또는 가격제한폭 초과
DB_WRITE_FAILED = "DB_WRITE_FAILED"        # shadow/live 기록 실패

# 운영자 알림이 필요 없는 정상 흐름의 skip
BENIGN = frozenset({ALREADY_DONE})


class SleeveSkip(Exception):
    """계산을 멈추고 skipped로 기록할 때 쓴다."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
