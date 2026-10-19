"""통합 E2E — dummy 백엔드·메모리 원장으로 생성→발행→검증 전 구간 (0원·무네트워크)."""
from __future__ import annotations

import json
import subprocess
from datetime import date

import pytest

from gen.nuna_weekly import config, db, pipeline
from gen.nuna_weekly.backends import make_backends
from gen.nuna_weekly.zernio import DummyZernioClient

RUN_DATE = date(2026, 10, 22)  # 목요일 (ISO 2026-W43)


def _daily() -> dict:
    snaps, news, analysis = [], [], []
    vals = {"2026-10-20": (-0.42, -0.61, 18.2), "2026-10-21": (0.35, 0.58, 17.4), "2026-10-22": (0.12, -0.20, 17.9)}
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
    assert pipeline.run_publish(ctx, "publish", sleep=lambda s: None) == 0
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "SCHEDULED"          # 예약까지 30분 초과 → 검증은 별도 dispatch
    assert ep["scheduled_for"].startswith("2026-10-22T08:30")
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
            return {"by": "1", "at": "2026-10-22T07:50:00+09:00"}

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
    ctx = pipeline.Ctx(date(2026, 10, 24), False, store=store, backends=make_backends(True),
                       zernio_clients={}, telegram=_NoTelegram())
    assert pipeline.run_gen(ctx) == 0
    assert store.get_episode(ctx.episode_id)["status"] == "SKIPPED"


def test_budget_blocks_when_month_cap_reached(env, monkeypatch):
    monkeypatch.setenv("NUNA_BUDGET_MONTH_KRW", "50")
    store = db.MemoryStore(daily=_daily())
    ctx = _ctx(store, dry=False, tg=_NoTelegram())
    assert pipeline.run_gen(ctx) == 1
    ep = store.get_episode(ctx.episode_id)
    assert "NUNA_BUDGET_EXCEEDED" in ep["last_error"]
