"""운영 반영 전 장애 주입: 보류, 불명확한 전송, 부분 원장, 빈 QC, 비정상 수치."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from gen.nuna_weekly import collect, config, db, pipeline
from gen.nuna_weekly.publish import publish, verify
from gen.nuna_weekly.tests.test_collect import NOW, FakeSession, Resp, chart, daily
from gen.nuna_weekly.tests.test_pipeline_regressions import _publish_ctx
from gen.nuna_weekly.tests.test_publish import ACCTS, EP, MEDIA, PDATE, clients, script


@pytest.mark.parametrize("hold_round", [3, 41, None])
def test_hold_window_is_polled_before_first_post(monkeypatch, tmp_path, hold_round):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, "NOTIFIED")

    class Clock(datetime):
        current = datetime(2099, 10, 22, 7, 30, tzinfo=config.KST)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    class TG:
        polls = 0

        def poll_hold(self, ep):
            self.polls += 1
            if self.polls == hold_round:
                return {"at": "2099-10-22T07:50:00+09:00", "by": "1"}
            return None

        def send_report(self, text, dry):
            return True

    tg = TG()
    monkeypatch.setattr(pipeline, "datetime", Clock)
    monkeypatch.setattr(ctx, "telegram", lambda: tg)
    sleeps = []

    def advance(seconds):
        sleeps.append(seconds)
        Clock.current += timedelta(seconds=seconds)

    assert pipeline.run_publish(ctx, sleep=advance) == 0
    posts = [c for cl in ctx.zernio().values() for c in cl.calls if c[0] == "create_post"]
    if hold_round:
        assert not posts and store.get_episode(ctx.episode_id)["status"] == "HELD"
    else:
        assert tg.polls == 41 and sum(sleeps) == 1200
        assert posts and max(sleeps) <= 30


@pytest.mark.parametrize("http", [408, 409, 429, 500, 502, 503])
def test_unknown_http_never_creates_new_post_on_retry(http):
    store = db.MemoryStore()
    publish(EP, store, clients(http_status=http), script(), MEDIA, ACCTS, None, PDATE, "main", False)
    assert {t["status"] for t in store.list_targets(EP)} == {"pending"}
    retry = clients()
    result = publish(EP, store, retry, script(), MEDIA, ACCTS, None, PDATE, "main", False)
    assert all("RECONCILE_REQUIRED" in r.error for r in result.values())
    assert not any(c[0] == "create_post" for cl in retry.values() for c in cl.calls)


def test_write_ahead_survives_post_response_ledger_failure():
    class BrokenStore(db.MemoryStore):
        def update_target(self, ep, platform, fields):
            if platform == "threads" and "response_json" in fields:
                raise RuntimeError("response ledger failed")
            return super().update_target(ep, platform, fields)

    store, cl = BrokenStore(), clients()
    with pytest.raises(RuntimeError, match="response ledger"):
        publish(EP, store, cl, script(), MEDIA, ACCTS, None, PDATE, "main", False)
    thread = next(t for t in store.list_targets(EP) if t["platform"] == "threads")
    assert thread["status"] == "pending" and thread["attempts"] == 1
    assert thread["request_json"]["idempotency_key"] == f"{EP}:main"
    retry = clients()
    publish(EP, store, retry, script(), MEDIA, ACCTS, None, PDATE, "main", False)
    assert not any(c[0] == "create_post" for c in retry["main"].calls)


def test_pending_with_post_id_is_verified_without_creating_post():
    store = db.MemoryStore()
    store.upsert_target({"episode_id": EP, "platform": "youtube", "status": "pending", "attempts": 1,
                         "key_alias": "sub", "idempotency_key": "old", "zernio_post_id": "known-id"})
    cl = clients()
    result = verify(EP, store, cl, sleep=lambda _: None)
    assert result["published"] == ["youtube"]
    assert not any(c[0] == "create_post" for c in cl["sub"].calls)


def test_missing_channel_cannot_be_aggregate_published(monkeypatch, tmp_path):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, "SCHEDULED")
    for p in config.PLATFORMS[:-1]:
        store.upsert_target({"episode_id": ctx.episode_id, "platform": p, "status": "published",
                             "idempotency_key": p})
    assert pipeline.run_publish(ctx, "verify", sleep=lambda _: None) == 1
    assert ctx.episode()["status"] == "PARTIAL"


@pytest.mark.parametrize("step", ["qc", "stage"])
def test_empty_artifacts_never_pass_gate(monkeypatch, tmp_path, step):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, "ASSEMBLED")
    monkeypatch.setattr(pipeline, "_facts", lambda _: object())
    with pytest.raises(pipeline.PipelineError, match="누락"):
        pipeline._do_step(ctx, step)
    assert not any(c[0] == "stage_file" for cl in ctx.zernio().values() for c in cl.calls)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_market_values_fail_closed(monkeypatch, value):
    from gen.nuna_weekly.calendar_gate import evaluate

    monkeypatch.setenv("NUNA_MARKET_SOURCE", "direct")
    with pytest.raises(collect.CollectError, match="Yahoo 응답 형식"):
        collect.build_facts(evaluate(NOW.date()), db.MemoryStore(),
                            session=FakeSession({"^VIX": Resp(chart("^VIX", [value] * 5))}), now_kst=NOW)
    monkeypatch.setenv("NUNA_MARKET_SOURCE", "supabase")
    rows = daily()
    rows["daily_snapshots"][0]["vix"] = value
    with pytest.raises(collect.CollectError, match="필수 지표 누락"):
        collect.build_facts(evaluate(NOW.date()), db.MemoryStore(rows), now_kst=NOW)


def test_retry_cap_prevents_db_check_constraint_failure():
    store = db.MemoryStore()
    for p in config.PLATFORMS:
        store.upsert_target({"episode_id": EP, "platform": p, "status": "failed", "attempts": 5,
                             "idempotency_key": p})
    cl = clients()
    result = publish(EP, store, cl, script(), MEDIA, ACCTS, None, PDATE, "main", False)
    assert all(r.error == "ATTEMPTS_EXHAUSTED" for r in result.values())
    assert not any(c[0] == "create_post" for c in cl["main"].calls + cl["sub"].calls)


@pytest.mark.parametrize("previous", ["images: FalError: timeout", "PAID_STEP_IN_PROGRESS:images"])
def test_paid_retry_on_new_runner_is_blocked_until_reconciled(monkeypatch, tmp_path, previous):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, "VOICED")
    ctx._backends = SimpleNamespace(is_dummy=False)
    store.update_episode(ctx.episode_id, {"last_error": previous})
    monkeypatch.setattr(pipeline, "_do_step", lambda *a: pytest.fail("유료 단계 재실행"))
    with pytest.raises(pipeline.PipelineError, match="PAID_RECOVERY_REQUIRED"):
        pipeline.run_gen(ctx, only="images")


def test_paid_intent_is_saved_before_call(monkeypatch, tmp_path):
    ctx, store = _publish_ctx(monkeypatch, tmp_path, "VOICED")
    ctx._backends = SimpleNamespace(is_dummy=False)

    def fail(c, step):
        assert c.episode()["last_error"] == "PAID_STEP_IN_PROGRESS:images"
        raise RuntimeError("provider interrupted")

    monkeypatch.setattr(pipeline, "_do_step", fail)
    assert pipeline.run_gen(ctx, only="images") == 1
    assert ctx.episode()["status"] == "VOICED"
    with pytest.raises(pipeline.PipelineError, match="PAID_RECOVERY_REQUIRED"):
        pipeline.run_gen(ctx, only="images")
