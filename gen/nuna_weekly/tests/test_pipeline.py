"""통합 E2E — dummy 백엔드·메모리 원장으로 생성→발행→검증 전 구간 (0원·무네트워크)."""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta

import pytest

from gen.nuna_weekly import config, db, pipeline
from gen.nuna_weekly.backends import make_backends
from gen.nuna_weekly.zernio import DummyZernioClient

RUN_DATE = date(2026, 10, 24)  # 토요일 (ISO 2026-W43) — DN2026_0086


def _daily() -> dict:
    snaps, news, analysis = [], [], []
    vals = {"2026-10-20": (-0.42, -0.61, 18.2), "2026-10-21": (0.35, 0.58, 17.4), "2026-10-22": (0.12, -0.20, 17.9),
            "2026-10-23": (0.27, 0.44, 17.1), "2026-10-24": (-0.18, -0.33, 17.6)}
    for d, (spy, ndx, vix) in vals.items():
        snaps.append({"snapshot_date": d, "spy_change": spy, "nasdaq_change": ndx, "vix": vix, "us10y": 4.21,
                      "oil_wti": 71.3, "dollar_index": 104.2, "usdkrw": 1330.5, "fear_greed": 44,
                      "fear_greed_label": "Fear"})
        news.append({"news_date": d, "top_headlines": [f"Stocks move on earnings {d}", "Fed speakers in focus"],
                     "top_issues": [{"topic": "실적 시즌", "summary": "빅테크 실적 대기"}]})
        analysis.append({"analysis_date": d, "regime": "Transition", "risk_level": "MEDIUM"})
    return {"daily_snapshots": snaps, "daily_news": news, "daily_analysis": analysis}


class _NoTelegram:
    def __init__(self):
        self.sent = []

    def notify_generated(self, episode_id, video_path, scheduled_at, dry):
        self.sent.append(("generated", episode_id, str(scheduled_at)))
        return {}

    def poll_hold(self, episode_id):
        return None

    def send_report(self, text, dry):
        self.sent.append(("report", text))
        return True


@pytest.fixture()
def env(monkeypatch, tmp_path):
    class Clock(datetime):
        current = datetime(2026, 10, 24, 6, 50, tzinfo=config.KST)

        @classmethod
        def now(cls, tz=None):
            fixed = cls.current
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        @classmethod
        def advance(cls, seconds):
            cls.current += timedelta(seconds=seconds)

    monkeypatch.setattr(pipeline, "datetime", Clock)
    monkeypatch.setenv("NUNA_MARKET_SOURCE", "supabase")
    monkeypatch.setenv("NUNA_DURATION_SEC", "60")
    monkeypatch.setattr(config, "OUT_ROOT", tmp_path / "out")
    for p in config.PLATFORMS:
        monkeypatch.setenv(config.ACCOUNT_ENV[p], f"acct{p}0001")
    return tmp_path


def _ctx(store, dry: bool, tg):
    zc = {a: DummyZernioClient(a) for a in ("main", "sub")}
    return pipeline.Ctx(RUN_DATE, dry, store=store, backends=make_backends(True), zernio_clients=zc, telegram=tg)


def test_full_flow_dummy(env):
    store = db.MemoryStore(daily=_daily())
    tg = _NoTelegram()
    ctx = _ctx(store, dry=False, tg=tg)
    assert ctx.episode_id == "nuna-2026-W43"

    assert pipeline.run_gen(ctx) == 0
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "NOTIFIED", ep.get("last_error")
    video = ep["assets_json"]["videos"]["main"]
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nw=1:nk=1", video], capture_output=True, text=True).stdout)
    assert abs(dur - 60.0) <= 1.0
    assert ep["qc_json"]["main"]["passed"] is True
    assert set(ep["media_json"]["main"]) == {"main", "sub"}
    assert store.month_spent_krw() <= config.budget_episode_krw()

    # 재실행은 no-op (멱등)
    assert pipeline.run_gen(ctx) == 0

    # 발행 → 예약 → 검증
    assert pipeline.run_publish(ctx, "publish", sleep=pipeline.datetime.advance) == 0
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "SCHEDULED"          # 예약까지 30분 초과 → 검증은 별도 dispatch
    assert ep["scheduled_for"].startswith("2026-10-24T08:30")
    assert pipeline.run_publish(ctx, "verify", sleep=lambda s: None) == 0
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "PUBLISHED", store.list_targets(ctx.episode_id)
    plats = {t["platform"]: t["status"] for t in store.list_targets(ctx.episode_id)}
    assert plats == {p: "published" for p in config.PLATFORMS}

    # 증빙 출력 — 실제 원장·QC JSON
    out = env / "e2e_result.json"
    out.write_text(json.dumps({"episode": {k: ep[k] for k in ("episode_id", "status", "scheduled_for")},
                               "qc": ep["qc_json"]["main"]["gates"], "targets": plats,
                               "cost_krw": store.month_spent_krw()}, ensure_ascii=False, indent=2, default=str))
    print(out.read_text())


def test_script_response_recovery_pilot_full_flow(env):
    """실제 실패 형태를 주입한 뒤 생성·QC·4채널 검증까지 모의 파일럿."""
    from types import SimpleNamespace
    from gen.nuna_weekly import script
    from gen.nuna_weekly.backends.llm import ClaudeBackend, DummyLLM
    from gen.nuna_weekly.tests.test_script import Responses, response

    store = db.MemoryStore(daily=_daily())
    ctx = _ctx(store, dry=False, tg=_NoTelegram())
    assert pipeline.run_gen(ctx, "collect") == 0
    facts = pipeline._facts(ctx.episode())
    user = script.build_user_prompt(facts, config.segments(60), RUN_DATE)
    good = DummyLLM().complete_json(script.SYSTEM_PROMPT, user, 8000).data
    messages = Responses(response('{"title":"unfinished', "max_tokens", 4000, "req-truncated"),
                         response(json.dumps(good, ensure_ascii=False), request_id="req-complete"))
    ctx._backends.llm = ClaudeBackend(client=SimpleNamespace(messages=messages))
    # 유료 intent·보호 경로도 검사하되 외부 서비스는 전부 가짜 클라이언트다.
    ctx._backends.is_dummy = False
    assert pipeline.run_gen(ctx) == 0
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "NOTIFIED" and ep["last_error"] is None
    assert ep["script_json"]["attempts"] == 2
    costs = [c for c in store.costs if c["step"] == "script"]
    assert [c["request_id"] for c in costs] == ["req-truncated", "req-complete"]
    assert len(messages.calls) == 2
    assert ep["qc_json"]["main"]["passed"]
    assert pipeline.run_publish(ctx, "publish", sleep=pipeline.datetime.advance) == 0
    assert pipeline.run_publish(ctx, "verify", sleep=lambda _: None) == 0
    assert store.get_episode(ctx.episode_id)["status"] == "PUBLISHED"
    assert len(store.list_targets(ctx.episode_id)) == 4


def test_dry_run_publish_skips(env):
    store = db.MemoryStore(daily=_daily())
    ctx = _ctx(store, dry=True, tg=_NoTelegram())
    assert pipeline.run_gen(ctx) == 0
    assert pipeline.run_publish(ctx, "publish", sleep=lambda s: None) == 0
    states = {t["status"] for t in store.list_targets(ctx.episode_id)}
    assert states == {"skipped"}
    assert store.get_episode(ctx.episode_id)["status"] == "NOTIFIED"


def test_hold_stops_publish(env):
    store = db.MemoryStore(daily=_daily())

    class HoldTG(_NoTelegram):
        def poll_hold(self, episode_id):
            return {"by": "1", "at": "2026-10-24T07:50:00+09:00"}

    ctx = _ctx(store, dry=False, tg=HoldTG())
    assert pipeline.run_gen(ctx) == 0
    assert pipeline.run_publish(ctx, "publish", sleep=lambda s: None) == 0
    assert store.get_episode(ctx.episode_id)["status"] == "HELD"
    assert store.list_targets(ctx.episode_id) == []


def test_failure_keeps_status_and_resumes(env, monkeypatch):
    store = db.MemoryStore(daily=_daily())
    ctx = _ctx(store, dry=False, tg=_NoTelegram())
    real = pipeline.images_mod.generate_images
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("fal 일시 오류")
        return real(*a, **k)

    monkeypatch.setattr(pipeline.images_mod, "generate_images", flaky)
    assert pipeline.run_gen(ctx) == 1
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "VOICED" and "fal 일시 오류" in ep["last_error"]
    assert pipeline.run_gen(ctx) == 0
    assert store.get_episode(ctx.episode_id)["status"] == "NOTIFIED"


def test_weekend_skip(env):
    store = db.MemoryStore(daily=_daily())
    ctx = pipeline.Ctx(date(2026, 10, 25), False, store=store, backends=make_backends(True),
                       zernio_clients={}, telegram=_NoTelegram())  # 일요일 스킵(DN2026_0086)
    assert pipeline.run_gen(ctx) == 0
    assert store.get_episode(ctx.episode_id)["status"] == "PLANNED"   # 원장에 SKIPPED 를 남기지 않음


def test_budget_blocks_when_month_cap_reached(env, monkeypatch):
    monkeypatch.setenv("NUNA_BUDGET_MONTH_KRW", "50")
    store = db.MemoryStore(daily=_daily())
    ctx = _ctx(store, dry=False, tg=_NoTelegram())
    assert pipeline.run_gen(ctx) == 1
    ep = store.get_episode(ctx.episode_id)
    assert "NUNA_BUDGET_EXCEEDED" in ep["last_error"]


# --------------------------------------------------------------------------- #
# DN2026_0086 R6-05 보완
# --------------------------------------------------------------------------- #
def test_sunday_rerun_keeps_saturday_episode_open(env):
    store = db.MemoryStore(daily=_daily())
    store.create_episode({"episode_id": "nuna-2026-W43", "publish_date": "2026-10-24", "us_days": [],
                          "duration_target_sec": 60, "pipeline_version": pipeline.VERSION})
    ctx = pipeline.Ctx(date(2026, 10, 25), False, store=store, backends=make_backends(True),
                       zernio_clients={}, telegram=_NoTelegram())
    assert pipeline.run_gen(ctx) == 0
    assert store.get_episode("nuna-2026-W43")["status"] == "PLANNED"


@pytest.mark.parametrize("version", ["1.2.0", None, "test"])
def test_old_format_episode_is_not_resumed(env, version):
    store = db.MemoryStore(daily=_daily())
    store.create_episode({"episode_id": "nuna-2026-W43", "publish_date": "2026-10-22", "us_days": [],
                          "duration_target_sec": 60, "pipeline_version": version})
    store.episodes["nuna-2026-W43"]["status"] = "IMAGED"
    ctx = _ctx(store, dry=False, tg=_NoTelegram())
    assert pipeline.run_gen(ctx) == 1
    assert store.get_episode("nuna-2026-W43")["status"] == "IMAGED"
