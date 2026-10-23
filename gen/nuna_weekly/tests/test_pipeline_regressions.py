"""운영 장애 회귀: 조기 수집, 발행 전용 환경, 부분 실패 재개. 유료·외부 호출 없음."""
from datetime import date, datetime

import pytest

from gen.nuna_weekly import calendar_gate, collect, config, db, pipeline
from gen.nuna_weekly.models import Script
from gen.nuna_weekly.zernio import DummyZernioClient


@pytest.mark.parametrize("now", [
    datetime(2026, 10, 20, 5, 40, tzinfo=config.KST),  # 지정 실패 실행
    datetime(2026, 10, 22, 4, 59, tzinfo=config.KST),
])
def test_unclosed_week_never_calls_market_or_db(now):
    class NoIO:
        def __getattr__(self, name):
            pytest.fail(f"조기 실행에서 외부 호출: {name}")

    with pytest.raises(collect.CollectError, match="MARKET_NOT_READY"):
        collect.build_facts(calendar_gate.evaluate(date(2026, 10, 20)), NoIO(),
                            session=NoIO(), now_kst=now)


@pytest.mark.parametrize(("day", "hour"), [(date(2026, 10, 22), 5), (date(2026, 11, 5), 6)])
def test_readiness_observes_new_york_dst(day, hour):
    gate = calendar_gate.evaluate(day)
    with pytest.raises(collect.CollectError):
        collect.require_closed_sessions(gate, datetime(day.year, day.month, day.day, hour - 1, 59,
                                                       tzinfo=config.KST))
    collect.require_closed_sessions(gate, datetime(day.year, day.month, day.day, hour, tzinfo=config.KST))


def _publish_ctx(monkeypatch, tmp_path, status):
    monkeypatch.setattr(config, "OUT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "make_backends", lambda dry: pytest.fail("발행 중 생성 백엔드 초기화"))
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_SUB_PAY_KEY", "FAL_AI_KEY"):
        monkeypatch.delenv(key, raising=False)
    for platform in config.PLATFORMS:
        monkeypatch.setenv(config.ACCOUNT_ENV[platform], f"acct-{platform}-001")
    store = db.MemoryStore()
    # 미래 회차: 테스트 실행일과 무관하게 검증 대기 없음.
    ctx = pipeline.Ctx(date(2099, 10, 22), False, store=store,
                       zernio_clients={a: DummyZernioClient(a) for a in ("main", "sub")})
    monkeypatch.setattr(ctx, "telegram", lambda: None)
    store.create_episode({"episode_id": ctx.episode_id, "status": status,
                          "notified_at": "2099-10-22T06:50:00+09:00",
                          "script_json": Script(episode_id=ctx.episode_id, title="주간 브리핑", segments=[],
                              captions={p: config.DISCLAIMER for p in config.PLATFORMS}).to_dict(),
                          "media_json": {"main": {"main": "https://cdn.test/main.mp4",
                                                  "sub": "https://cdn.test/sub.mp4"}}})
    return ctx, store


@pytest.mark.parametrize("status", ["NOTIFIED", "SCHEDULED", "PARTIAL", "FAILED"])
def test_publish_without_generation_credentials_and_retry(monkeypatch, tmp_path, status):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, status)
    if status != "NOTIFIED":
        store.upsert_target({"episode_id": ctx.episode_id, "platform": "instagram", "status": "published",
                             "attempts": 1, "idempotency_key": "original-instagram"})
        store.upsert_target({"episode_id": ctx.episode_id, "platform": "youtube", "status": "failed",
                             "attempts": 1, "idempotency_key": "original-youtube"})
    assert pipeline.run_publish(ctx, sleep=lambda _: None) == 0
    calls = [c for cl in ctx.zernio().values() for c in cl.calls if c[0] == "create_post"]
    platforms = [p["platform"] for c in calls for p in c[2]["platforms"]]
    if status != "NOTIFIED":
        assert "instagram" not in platforms
        assert any(c[1].endswith(":sub:youtube:r1") for c in calls)
    else:
        assert set(platforms) == set(config.PLATFORMS)
    assert store.get_episode(ctx.episode_id)["status"] == "SCHEDULED"


def test_missing_or_incomplete_generation_fails_visibly(monkeypatch, tmp_path):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, "COLLECTED")
    assert pipeline.run_publish(ctx) == 1
    store.episodes.clear()
    assert pipeline.run_publish(ctx) == 1
    assert not any(c[0] == "create_post" for cl in ctx.zernio().values() for c in cl.calls)


def test_preflight_does_not_initialize_store_or_backends(monkeypatch):
    monkeypatch.setattr(pipeline, "Ctx", lambda *a, **k: pytest.fail("preflight 외부 초기화"))
    assert pipeline.main(["gen", "--step", "preflight", "--date", "2026-10-15"]) == 0
    assert pipeline.main(["gen", "--step", "typo"]) == 1


@pytest.mark.parametrize("status", ["ASSEMBLED", "QC_PASSED"])
def test_new_runner_reassembles_and_rechecks_qc(monkeypatch, tmp_path, status):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, status)
    store.update_episode(ctx.episode_id, {"assets_json": {"videos": {"main": str(tmp_path / "lost.mp4")}},
                                         "qc_json": {"main": {"passed": True}}})
    executed = []

    def step(c, name):
        executed.append(name)
        if name == "assemble":
            assert c.episode()["qc_json"] is None
        return {}

    monkeypatch.setattr(pipeline, "_do_step", step)
    assert pipeline.run_gen(ctx) == 0
    assert executed == ["assemble", "qc", "stage", "notify"]
    assert ctx.episode()["status"] == "NOTIFIED"


def test_expired_schedule_does_not_publish_stale_news(monkeypatch, tmp_path):
    ctx, _ = _publish_ctx(monkeypatch, tmp_path, "NOTIFIED")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=config.KST)

    monkeypatch.setattr(pipeline, "datetime", Clock)
    with pytest.raises(pipeline.PipelineError, match="예약 시각 경과"):
        pipeline.run_publish(ctx)
    assert not any(c[0] == "create_post" for cl in ctx.zernio().values() for c in cl.calls)
