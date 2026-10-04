"""F3 (2026-10-05): 유튜버 요약 사실 정합성 가드 테스트.

픽스처: tests/fixtures/core_data_morning_20261005.json (운영 run 37231539740 원본)
  streamer tweet 에 「긍정적인 고용지표 발표에도 …」 — 실제 labor_state "Weak Labor" (NFP +29K).
"""
import ast
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import streamer_fact_guard as g

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "core_data_morning_20261005.json"


@pytest.fixture
def data():
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["data"])


def _d(labor="Weak Labor", nfp=29.0, sp=0.73, nq=1.19):
    return {"signals": {"labor_state": labor, "nfp_change": nfp},
            "market_snapshot": {"sp500": sp, "nasdaq": nq}}


# ── 운영 실데이터 ────────────────────────────────────────────
def test_production_case_removes_only_mismatched_line(data):
    original = data["streamer_consensus"]["tweet"]
    out = g.apply(data["streamer_consensus"], data)
    tweet = out["tweet"]
    assert "긍정적인 고용지표" not in tweet
    # 나머지 줄·형식은 그대로 (헤더·전체 방향·면책 문구 유지)
    expected = "\n".join(ln for ln in original.split("\n") if "긍정적인 고용지표" not in ln)
    assert tweet == expected
    fg = out["fact_guard"]
    assert fg["bullets_before"] == 4 and fg["bullets_after"] == 3 and fg["blocked"] is False
    assert fg["removed_tweet_lines"][0]["reason"] == "labor:claim=positive,actual=Weak Labor,nfp=29.0"
    assert all("호조" not in p for p in out["summary_points"])
    assert data["streamer_consensus"]["tweet"] == original      # 원본 불변


# ── R1 고용 ──────────────────────────────────────────────────
@pytest.mark.parametrize("line,labor,ok", [
    ("• 고용지표 호조로 시장 상승", "Weak Labor", False),
    ("• 긍정적인 고용지표 발표에도 불확실성", "Weak Labor", False),
    ("• 고용 호조 속 금리 인상 우려", "Weak Labor", False),          # 고용은 의견 표현 섞여도 엄격
    ("• 비농업 고용 서프라이즈", "Job Loss", False),
    ("• 고용 둔화로 금리 인하 기대", "Weak Labor", True),
    ("• 고용 둔화 신호", "Strong Labor", False),
    ("• 고용 호조", "Strong Labor", True),
    ("• 고용 호조", "Moderate Labor", True),                       # 판정 안 함
    ("• 고용 호조", "No Data", True),                              # 데이터 없음 → 유지
    ("• 고용 둔화에도 소비 개선", "Strong Labor", True),           # 극성 혼재 → 판단 불가
    ("• 실업률 상승", "Strong Labor", True),                       # 실업은 키워드 제외
])
def test_labor_rule(line, labor, ok):
    assert g.check_point(line, _d(labor=labor))[0] is ok


# ── R2 증시 방향 ─────────────────────────────────────────────
@pytest.mark.parametrize("line,sp,nq,ok", [
    ("• 나스닥 상승세 지속", -0.5, -1.0, False),
    ("• 증시 하락세", 0.7, 1.2, False),
    ("• 증시 하락 우려", 0.7, 1.2, True),          # 의견 → 검사 안 함
    ("• 시장 조정 가능성", 0.7, 1.2, True),
    ("• 나스닥 상승세 지속", 0.7, 1.2, True),
    ("• 증시 상승", -0.5, 0.3, True),              # 지수 혼조 → 판단 불가
    ("• 증시 상승", None, None, True),             # 데이터 없음
])
def test_market_rule(line, sp, nq, ok):
    assert g.check_point(line, _d(sp=sp, nq=nq))[0] is ok


# ── 트윗 처리 ────────────────────────────────────────────────
def test_all_bullets_removed_blocks_tweet():
    tweet = "🎤 종합\n\n주요 의견:\n• 고용지표 호조\n• 비농업 고용 서프라이즈\n\n⚠️ 투자 참고"
    out = g.sanitize_tweet(tweet, _d())
    assert out["tweet"] == "" and out["bullets_before"] == 2 and out["bullets_after"] == 0


def test_tweet_without_bullets_unchanged():
    tweet = "🎤 미장 전문가 시각 종합\n\n최근 주요 영상:\n고용지표 호조 / 나스닥 신고가\n\n⚠️"
    assert g.sanitize_tweet(tweet, _d())["tweet"] == tweet


def test_empty_inputs():
    assert g.sanitize_tweet("", _d())["tweet"] == ""
    out = g.apply({}, _d())
    assert out["tweet"] == "" and out["fact_guard"]["blocked"] is False


# ── run_market 연동 (정적 검사) ──────────────────────────────
def test_run_market_applies_guard_right_after_injection():
    src = (ROOT / "run_market.py").read_text(encoding="utf-8")
    inject = src.index('data["streamer_consensus"] = {')
    guard = src.index("_streamer_fact_guard(data[\"streamer_consensus\"], data)")
    validation = src.index("# ── Step 7: Validation")
    assert inject < guard < validation
    # 가드 예외 시 tweet 을 비운다 (검증 안 된 요약 미발행)
    tree = ast.parse(src)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
                and "Step 6-YT-Guard" in ast.unparse(n)]
    assert handlers and 'data["streamer_consensus"]["tweet"] = ""' in ast.unparse(handlers[0]).replace("'", '"')
