"""DN2026_0106 회귀: TTS 길이 초과 구간 단축 재작성(A) · 비용 확정 실패의 재실행 허용(C). 네트워크·유료 호출 없음.

장애: 2026-11-07 run 42323193008 — W45 S2 9.24s > 8.00s × 1.15 → TTSFitError, 회차 SCRIPTED 정지,
      last_error 'tts: TTSFitError: …' 가 재실행을 PAID_RECOVERY_REQUIRED 로 차단.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from gen.nuna_weekly import config, db, pipeline, script, tts
from gen.nuna_weekly.backends import Backends, LLMResult
from gen.nuna_weekly.backends.llm import DummyLLM
from gen.nuna_weekly.backends.tts import DummyTTS
from gen.nuna_weekly.budget import Budget
from gen.nuna_weekly.db import MemoryStore
from gen.nuna_weekly.models import Script, ScriptSegment
from gen.nuna_weekly.tests.test_script import PUB, make_facts

SLOW_MARK = "누적"   # DummyLLM week 구간 문구 — 단축 시 뒤에서 잘려 사라진다


class SlowWeekTTS(DummyTTS):
    """'누적' 이 있는 내레이션만 3.5자/초(초과 유도). 나머지는 DummyTTS 속도."""

    def __init__(self, always_slow_token: str | None = None) -> None:
        super().__init__()
        self.texts: list[str] = []
        self.always = always_slow_token

    def duration_for(self, text: str) -> float:  # type: ignore[override]
        self.texts.append(text)
        if self.always and self.always in text:
            return 20.0   # 단축해도 해소되지 않는 초과
        if SLOW_MARK in text:
            return round(len(text) / 3.5, 3)
        return DummyTTS.duration_for(text)


def _dummy_script(segs) -> Script:
    return script.generate(make_facts(), segs, DummyLLM(), Budget(MemoryStore(), "x", fx_rate=1400.0), PUB)


# --------------------------------------------------------------------------- #
# tts.synthesize_segments
# --------------------------------------------------------------------------- #
def test_shorten_target_chars_uses_measured_speed():
    # W45 S2 실측: 46자 9.24s, 목표 8s → 46 × 8/9.24 × 0.95 = 37.8 → 37
    assert tts.shorten_target_chars(46, 9.24, 8.0) == 37
    assert tts.shorten_target_chars(40, 0.0, 8.0) == 40


def test_overflow_calls_shortener_once_then_fits(tmp_path):
    seg = config.segments(60)[1]
    sc = Script(episode_id="e", title="t", captions={},
                segments=[ScriptSegment(seg.seg_id, seg.seconds, "가" * 40 + SLOW_MARK)])   # 42자 → 12.0s
    calls = []

    def shorten(seg_id, text, raw, target):
        calls.append((seg_id, len(text), raw, target))
        return "가" * 30

    b = Budget(MemoryStore(), "e", fx_rate=1400.0)
    assets = tts.synthesize_segments(sc, [seg], SlowWeekTTS(), b, tmp_path, shorten=shorten)
    assert len(calls) == 1 and calls[0][0] == seg.seg_id and calls[0][2] > seg.seconds * tts.MAX_ATEMPO
    assert abs(assets[0].seconds - seg.seconds) <= tts.FIT_TOLERANCE_SEC
    assert [c["step"] for c in b.store.costs] == ["tts", "tts"]   # 초과 1회 + 재합성 1회, 모두 확정
    assert all(c["state"] == "charged" for c in b.store.costs)


def test_overflow_still_long_after_shorten_raises(tmp_path):
    seg = config.segments(60)[1]
    sc = Script(episode_id="e", title="t", captions={},
                segments=[ScriptSegment(seg.seg_id, seg.seconds, "가" * 40 + SLOW_MARK)])
    with pytest.raises(tts.TTSFitError):
        tts.synthesize_segments(sc, [seg], SlowWeekTTS(), Budget(MemoryStore(), "e", fx_rate=1400.0),
                                tmp_path, shorten=lambda *a: "나" * 40 + SLOW_MARK)


# --------------------------------------------------------------------------- #
# script.shorten_segment
# --------------------------------------------------------------------------- #
def test_shorten_segment_dummy_keeps_numbers_and_limit():
    segs = config.segments(60)
    sc = _dummy_script(segs)
    s2 = next(s for s in sc.segments if s.seg_id == "S2")
    b = Budget(MemoryStore(), "x", fx_rate=1400.0)
    new = script.shorten_segment(sc, "S2", len(s2.narration) - 6, make_facts(), segs, DummyLLM(), b, PUB)
    n2 = next(s for s in new.segments if s.seg_id == "S2").narration
    assert len(n2) <= len(s2.narration) - 6 and n2
    assert script.verify_numbers(new, make_facts(), PUB) == []
    assert [s.narration for s in new.segments if s.seg_id != "S2"] == \
           [s.narration for s in sc.segments if s.seg_id != "S2"]
    assert [c["step"] for c in b.store.costs] == ["script"]


def test_shorten_disclaimer_segment_keeps_disclaimer():
    segs = config.segments(60)
    sc = _dummy_script(segs)
    s8 = next(s for s in sc.segments if s.seg_id == "S8")
    new = script.shorten_segment(sc, "S8", len(s8.narration) - 1, make_facts(), segs, DummyLLM(),
                                 Budget(MemoryStore(), "x", fx_rate=1400.0), PUB)
    assert next(s for s in new.segments if s.seg_id == "S8").narration.endswith(config.DISCLAIMER)


class FakeLLM:
    name = "fake"

    def __init__(self, narration: str) -> None:
        self.narration = narration

    def complete_json(self, system, user, max_tokens):
        assert "<shorten>" in user
        return LLMResult(data={"narration": self.narration}, input_tokens=100, output_tokens=20,
                         model="fake", request_id="req-1")


@pytest.mark.parametrize("bad", ["이번 주 S&P500 +9.99% 마감했습니다 흐름입니다", "", "가" * 60])
def test_shorten_segment_rejects_invalid_after_settling_cost(bad):
    segs = config.segments(60)
    sc = _dummy_script(segs)
    s2 = next(s for s in sc.segments if s.seg_id == "S2")
    b = Budget(MemoryStore(), "x", fx_rate=1400.0)
    with pytest.raises(script.ScriptError):
        script.shorten_segment(sc, "S2", len(s2.narration) - 2, make_facts(), segs, FakeLLM(bad), b, PUB)
    assert [(c["step"], c["state"]) for c in b.store.costs] == [("script", "charged")]


def test_shorten_segment_refuses_tiny_target():
    segs = config.segments(60)
    sc = _dummy_script(segs)
    with pytest.raises(script.ScriptError, match="자동 단축 불가"):
        script.shorten_segment(sc, "S2", 5, make_facts(), segs, DummyLLM(),
                               Budget(MemoryStore(), "x", fx_rate=1400.0), PUB)


# --------------------------------------------------------------------------- #
# pipeline — 단축 연결(A) · 재실행 허용(C)
# --------------------------------------------------------------------------- #
def _scripted_ctx(monkeypatch, tmp_path, tts_backend, llm=None, last_error=None):
    monkeypatch.setattr(config, "OUT_ROOT", tmp_path)
    monkeypatch.setenv("NUNA_DURATION_SEC", "60")
    store = db.MemoryStore()
    be = Backends(llm or DummyLLM(), tts_backend, None, is_dummy=False)   # live 경로(유료 재호출 게이트) 검증
    ctx = pipeline.Ctx(date(2099, 10, 22), False, store=store, backends=be)
    monkeypatch.setattr(ctx, "telegram", lambda: None)
    segs = config.segments(60)
    store.create_episode({"episode_id": ctx.episode_id, "status": "SCRIPTED", "pipeline_version": pipeline.VERSION,
                          "duration_target_sec": 60, "facts_json": make_facts().to_dict(),
                          "script_json": _dummy_script(segs).to_dict(), "last_error": last_error})
    return ctx, store


def test_tts_step_shortens_and_persists_script(monkeypatch, tmp_path):
    ctx, store = _scripted_ctx(monkeypatch, tmp_path, SlowWeekTTS())
    before = next(s for s in store.get_episode(ctx.episode_id)["script_json"]["segments"] if s["seg_id"] == "S2")
    assert pipeline.run_gen(ctx, only="tts") == 0
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "VOICED" and ep["last_error"] is None
    after = next(s for s in ep["script_json"]["segments"] if s["seg_id"] == "S2")
    assert len(after["narration"]) < len(before["narration"]) and SLOW_MARK not in after["narration"]
    assert Path(ep["assets_json"]["audio"]["tts_S2"]["local_path"]).is_file()
    assert sum(1 for c in store.costs if c["step"] == "script") == 1


def test_tts_fit_failure_is_rerunnable_not_paid_blocked(monkeypatch, tmp_path):
    # 단축해도 'S&P500' 이 남아 계속 느림 → TTSFitError
    ctx, store = _scripted_ctx(monkeypatch, tmp_path, SlowWeekTTS(always_slow_token="S&P500"))
    assert pipeline.run_gen(ctx, only="tts") == 1
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "SCRIPTED"
    assert ep["last_error"].startswith(f"{pipeline.TTS_FIT}:tts: TTSFitError:")
    assert all(c["state"] == "charged" for c in store.costs)
    # 재실행: PAID_RECOVERY_REQUIRED 로 막히지 않고 다시 시도한다(결과는 같은 실패)
    assert pipeline.run_gen(ctx, only="tts") == 1


def test_shorten_validation_failure_becomes_tts_fit(monkeypatch, tmp_path):
    ctx, store = _scripted_ctx(monkeypatch, tmp_path, SlowWeekTTS(), llm=FakeLLM("이번 주 S&P500 +9.99% 마감"))
    assert pipeline.run_gen(ctx, only="tts") == 1
    err = store.get_episode(ctx.episode_id)["last_error"]
    assert err.startswith(f"{pipeline.TTS_FIT}:tts:") and "구간 단축 실패" in err


@pytest.mark.parametrize("legacy", ["tts: TTSFitError: S2 내레이션 길이 초과: 9.24s > 8.00s × 1.15 — 대본 단축 필요",
                                    "TTS_FIT:tts: TTSFitError: S2 내레이션 길이 초과"])
def test_settled_tts_errors_allow_rerun(monkeypatch, tmp_path, legacy):
    ctx, store = _scripted_ctx(monkeypatch, tmp_path, SlowWeekTTS(), last_error=legacy)
    assert pipeline.run_gen(ctx, only="tts") == 0
    assert store.get_episode(ctx.episode_id)["status"] == "VOICED"


@pytest.mark.parametrize("previous", ["tts: TTSError: Gemini 503", "PAID_STEP_IN_PROGRESS:tts",
                                      "images: TTSFitError: 다른 단계"])
def test_unsettled_paid_errors_still_blocked(monkeypatch, tmp_path, previous):
    ctx, store = _scripted_ctx(monkeypatch, tmp_path, SlowWeekTTS(), last_error=previous)
    if previous.startswith("images"):
        assert not pipeline._cost_settled_error(previous, "images")
        return
    monkeypatch.setattr(pipeline, "_do_step", lambda *a: pytest.fail("유료 단계 재실행"))
    with pytest.raises(pipeline.PipelineError, match="PAID_RECOVERY_REQUIRED"):
        pipeline.run_gen(ctx, only="tts")


def test_shorten_floor_keeps_chars_min_when_target_allows():
    # R6-01 경고 1: W45 S2(8s) 목표 37자 → 하한 = min(chars_min 32, int(37×0.85)=31) = 31자
    from dataclasses import replace
    segs = config.segments(60)
    base = _dummy_script(segs)
    long46 = ("이번 주 S&P500 +1.50%, 나스닥 +0.99% 누적입니다. 변동성 지수도 함께 짚어봅니다")[:46]
    sc = replace(base, segments=[replace(x, narration=long46) if x.seg_id == "S2" else x for x in base.segments])
    with pytest.raises(script.ScriptError, match="하한 31자"):
        script.shorten_segment(sc, "S2", 37, make_facts(), segs, FakeLLM("이번 주 S&P500 +1.50% 흐름입니다"),
                               Budget(MemoryStore(), "x", fx_rate=1400.0), PUB)


class BrokenLLM:
    name = "broken"

    def complete_json(self, system, user, max_tokens):
        raise RuntimeError("connection reset")   # 과금 여부 불명


def test_shorten_network_error_stays_paid_blocked(monkeypatch, tmp_path):
    # R6-01 경고 3: 단축 LLM 이 과금 불명으로 실패하면 TTS_FIT 이 아니라 'tts:' 로 남아 재실행이 차단된다
    ctx, store = _scripted_ctx(monkeypatch, tmp_path, SlowWeekTTS(), llm=BrokenLLM())
    assert pipeline.run_gen(ctx, only="tts") == 1
    err = store.get_episode(ctx.episode_id)["last_error"]
    assert err.startswith("tts: RuntimeError") and not pipeline._cost_settled_error(err, "tts")
    monkeypatch.setattr(pipeline, "_do_step", lambda *a: pytest.fail("유료 단계 재실행"))
    with pytest.raises(pipeline.PipelineError, match="PAID_RECOVERY_REQUIRED"):
        pipeline.run_gen(ctx, only="tts")
