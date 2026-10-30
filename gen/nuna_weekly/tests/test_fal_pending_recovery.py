"""fal 대기 상한·작업 기록 보존·재개 회귀 (2026-10-29 nuna-2026-W44 clips 타임아웃).

실제 장애: kling i2v 작업이 300s 시점 IN_PROGRESS → FalError → last_error 'clips: ...' →
재실행은 PAID_RECOVERY_REQUIRED 로 차단, pending 은 러너 로컬에만 있어 소실, 비용 원장에 request_id 없음.
가짜 세션만 사용 — 네트워크·유료 호출 없음.
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from gen.nuna_weekly import config, db, images, pipeline
from gen.nuna_weekly.backends import Backends
from gen.nuna_weekly.backends.fal import DummyFal, FalError, FalPendingTimeout, FalQueueBackend, request_signature
from gen.nuna_weekly.budget import Budget
from gen.nuna_weekly.fal_jobs import run_paid
from gen.nuna_weekly.models import Asset, Script, ScriptSegment

KEY = "fal-key-0123456789abcdef"


class Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code = status
        self._body = body
        self.content = content
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class QueueSession:
    """fal queue 흉내 — POST 마다 req-N 발급, status 는 states[req] (기본 COMPLETED)."""

    def __init__(self, states=None, video=b"", start=1):
        self.states = dict(states or {})
        self.video = video
        self.n = start - 1
        self.posts: list[str] = []
        self.log: list[tuple[str, str]] = []

    def request(self, method, url, headers=None, timeout=None, data=None):
        self.log.append((method, url))
        if method == "POST":
            self.n += 1
            rid = f"req-{self.n}"
            self.posts.append(rid)
            return Resp(200, {"request_id": rid, "status_url": f"https://queue.test/s/{rid}",
                              "response_url": f"https://queue.test/r/{rid}"})
        rid = url.rsplit("/", 1)[1]
        if "/s/" in url:
            return Resp(200, {"status": self.states.get(rid, "COMPLETED")})
        return Resp(200, {"video": {"url": f"https://cdn.test/{rid}.mp4"},
                          "images": [{"url": f"https://cdn.test/{rid}.png"}]})

    def get(self, url, timeout=None):
        self.log.append(("DL", url))
        return Resp(200, content=self.video or b"PNGDATA" * 10)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("FAL_AI_KEY", KEY)
    for name in ("NUNA_FAL_CLIP_MAX_WAIT_SEC", "NUNA_FAL_IMAGE_MAX_WAIT_SEC"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def mp4(tmp_path_factory) -> bytes:
    p = tmp_path_factory.mktemp("v") / "v.mp4"
    DummyFal._video(p, config.CLIP_SECONDS)
    return p.read_bytes()


def fal_backend(sess) -> FalQueueBackend:
    return FalQueueBackend(session=sess, sleep=lambda s: None, poll_interval=3.0)


class Jobs:
    def __init__(self):
        self.d: dict[str, dict] = {}

    def get(self, key):
        return self.d.get(key)

    def save(self, key, job):
        self.d[key] = dict(job)


# --------------------------------------------------------------------------- #
# A. 작업 종류별 대기 상한
# --------------------------------------------------------------------------- #
def test_wait_defaults_split_by_kind():
    assert config.fal_image_max_wait_sec() == 300
    assert config.fal_clip_max_wait_sec() == 900


@pytest.mark.parametrize("value", ["59", "1201"])
def test_wait_bounds_rejected(monkeypatch, value):
    monkeypatch.setenv("NUNA_FAL_CLIP_MAX_WAIT_SEC", value)
    with pytest.raises(ValueError, match="NUNA_FAL_CLIP_MAX_WAIT_SEC"):
        config.fal_clip_max_wait_sec()


def test_max_wait_sec_sets_poll_count(tmp_path):
    sess = QueueSession({"req-1": "IN_PROGRESS"})
    with pytest.raises(FalPendingTimeout) as ei:
        fal_backend(sess).run(config.FAL_CLIP_ENDPOINT, {"duration": "5"}, tmp_path / "c.mp4",
                              tmp_path / "c.json", max_wait_sec=9)
    polls = [u for m, u in sess.log if m == "GET" and "/s/" in u]
    assert len(polls) == 3
    assert ei.value.request_id == "req-1" and ei.value.last_status == "IN_PROGRESS"
    assert "대기 상한 9s" in str(ei.value) and isinstance(ei.value, FalError)


# --------------------------------------------------------------------------- #
# B. 제출 즉시 작업 기록 → 새 러너에서 재제출 없이 재개
# --------------------------------------------------------------------------- #
def test_on_submit_called_before_polling(tmp_path):
    seen: list[dict] = []
    sess = QueueSession()

    def on_submit(job):
        assert not any("/s/" in u for _, u in sess.log)  # 폴링 전
        seen.append(job)

    fal_backend(sess).run(config.FAL_IMAGE_ENDPOINT, {"prompt": "p"}, tmp_path / "a.png", tmp_path / "a.json",
                          on_submit=on_submit)
    assert [j["request_id"] for j in seen] == ["req-1"]
    assert set(seen[0]) >= {"endpoint", "signature", "request_id", "status_url", "response_url"}


def test_resume_from_ledger_job_without_local_file(tmp_path):
    payload = {"prompt": "p", "duration": "5"}
    job = {"endpoint": config.FAL_CLIP_ENDPOINT, "signature": request_signature(payload), "request_id": "req-7",
           "status_url": "https://queue.test/s/req-7", "response_url": "https://queue.test/r/req-7"}
    sess = QueueSession(video=b"x" * 20000)
    r = fal_backend(sess).run(config.FAL_CLIP_ENDPOINT, payload, tmp_path / "c.mp4", tmp_path / "none.json",
                              pending_job=job, submit_allowed=False)
    assert sess.posts == [] and r.request_id == "req-7"


def test_submit_blocked_when_resume_only_and_job_mismatch(tmp_path):
    sess = QueueSession()
    job = {"endpoint": config.FAL_CLIP_ENDPOINT, "signature": "other", "request_id": "req-7",
           "status_url": "https://queue.test/s/req-7", "response_url": "https://queue.test/r/req-7"}
    with pytest.raises(FalError, match="재제출 차단"):
        fal_backend(sess).run(config.FAL_CLIP_ENDPOINT, {"duration": "5"}, tmp_path / "c.mp4", tmp_path / "c.json",
                              pending_job=job, submit_allowed=False)
    assert sess.posts == []


def _charged(store):
    return [c for c in store.costs if c["state"] == "charged"]


def test_run_paid_timeout_keeps_request_id_in_ledger_and_job(tmp_path):
    store, jobs = db.MemoryStore(), Jobs()
    payload = {"prompt": "p", "duration": "5"}
    sess = QueueSession({"req-1": "IN_PROGRESS"})
    with pytest.raises(FalPendingTimeout) as ei:
        run_paid(fal_backend(sess), Budget(store, "EP", fx_rate=1500.0), jobs, "clip_S1", step="clips",
                 endpoint=config.FAL_CLIP_ENDPOINT, payload=payload, out_path=tmp_path / "c.mp4",
                 pending_path=tmp_path / "c.json", units=5, unit="sec", est_usd=0.35, max_wait_sec=60)
    assert ei.value.persisted is True
    assert jobs.get("clip_S1")["request_id"] == "req-1"
    [row] = _charged(store)
    assert row["request_id"] == "req-1" and row["usd"] == pytest.approx(0.35)


def test_run_paid_resume_adds_no_cost_row(tmp_path, mp4):
    store, jobs = db.MemoryStore(), Jobs()
    payload = {"prompt": "p", "duration": "5"}
    jobs.save("clip_S1", {"endpoint": config.FAL_CLIP_ENDPOINT, "signature": request_signature(payload),
                          "request_id": "req-1", "status_url": "https://queue.test/s/req-1",
                          "response_url": "https://queue.test/r/req-1"})
    sess = QueueSession(video=mp4)
    res = run_paid(fal_backend(sess), Budget(store, "EP", fx_rate=1500.0), jobs, "clip_S1", step="clips",
                   endpoint=config.FAL_CLIP_ENDPOINT, payload=payload, out_path=tmp_path / "c.mp4",
                   pending_path=tmp_path / "c.json", units=5, unit="sec", est_usd=0.35, max_wait_sec=60)
    assert res.request_id == "req-1" and sess.posts == [] and store.costs == []


def test_run_paid_changed_payload_resubmits_with_charge(tmp_path):
    store, jobs = db.MemoryStore(), Jobs()
    jobs.save("img_S2", {"endpoint": config.FAL_IMAGE_ENDPOINT, "signature": "stale", "request_id": "req-9",
                         "status_url": "https://queue.test/s/req-9", "response_url": "https://queue.test/r/req-9"})
    sess = QueueSession()
    res = run_paid(fal_backend(sess), Budget(store, "EP", fx_rate=1500.0), jobs, "img_S2", step="images",
                   endpoint=config.FAL_IMAGE_ENDPOINT, payload={"prompt": "new"}, out_path=tmp_path / "a.png",
                   pending_path=tmp_path / "a.json", units=1, unit="image", est_usd=0.039, max_wait_sec=60)
    assert sess.posts == ["req-1"] and res.request_id == "req-1"
    assert [c["request_id"] for c in _charged(store)] == ["req-1"]


# --------------------------------------------------------------------------- #
# C. 파이프라인 — W44 장애 재현 → 새 러너 재개
# --------------------------------------------------------------------------- #
def _seed_imaged(store, ctx, tmp_path):
    segs = config.segments(60)
    sc = Script(episode_id=ctx.episode_id, title="t", captions={}, segments=[
        ScriptSegment(s.seg_id, s.seconds, "내레이션", image_prompt=f"scene {s.seg_id}",
                      clip_prompt=f"motion {s.seg_id}") for s in segs])
    imgs = {}
    for s in images.image_segments(segs):
        p = tmp_path / f"img_{s.seg_id}.png"
        DummyFal._image(p)
        imgs[s.seg_id] = Asset(kind="image", key=f"img_{s.seg_id}", local_path=str(p),
                               remote_url=f"https://cdn.test/img_{s.seg_id}.png", request_id=f"img-{s.seg_id}")
    store.create_episode({"episode_id": ctx.episode_id, "status": "IMAGED", "duration_target_sec": 60,
                          "script_json": sc.to_dict(),
                          "assets_json": {"images": pipeline._assets_dump(imgs)}})


def _live_ctx(monkeypatch, store, out_root, sess):
    monkeypatch.setattr(config, "OUT_ROOT", out_root)
    ctx = pipeline.Ctx(date(2099, 10, 22), False, store=store,
                       backends=Backends(None, None, fal_backend(sess), False))
    monkeypatch.setattr(ctx, "telegram", lambda: None)
    ctx._budget = Budget(store, ctx.episode_id, fx_rate=1500.0)
    return ctx


def test_clip_timeout_then_new_runner_resumes_without_resubmit(monkeypatch, tmp_path, mp4):
    monkeypatch.setenv("NUNA_DURATION_SEC", "60")
    store = db.MemoryStore()
    sess1 = QueueSession({"req-1": "IN_PROGRESS"}, video=mp4)
    ctx = _live_ctx(monkeypatch, store, tmp_path / "runner1", sess1)
    _seed_imaged(store, ctx, tmp_path)

    assert pipeline.run_gen(ctx, only="clips") == 1
    ep = store.get_episode(ctx.episode_id)
    assert ep["status"] == "IMAGED"
    assert ep["last_error"].startswith("FAL_PENDING:clips: FalPendingTimeout")
    assert ep["assets_json"]["fal_pending"]["clip_S1"]["request_id"] == "req-1"
    assert ep["assets_json"]["images"]  # 기록 보존 중 기존 자산 유지
    assert [(c["step"], c["request_id"]) for c in _charged(store)] == [("clips", "req-1")]

    # 새 러너: 로컬 파일 없음, 원격 작업은 그 사이 완료
    sess2 = QueueSession(video=mp4, start=2)
    ctx2 = _live_ctx(monkeypatch, store, tmp_path / "runner2", sess2)
    assert pipeline.run_gen(ctx2, only="clips") == 0
    ep = store.get_episode(ctx2.episode_id)
    assert ep["status"] == "CLIPPED" and ep["last_error"] is None
    assert "fal_pending" not in ep["assets_json"]
    assert sess2.posts == ["req-2"]  # S1 재제출 없음, S8 만 신규
    assert ep["assets_json"]["clips"]["S1"]["request_id"] == "req-1"
    assert sorted(c["request_id"] for c in _charged(store)) == ["req-1", "req-2"]


def test_partial_clip_success_is_reused_on_new_runner(monkeypatch, tmp_path, mp4):
    """S1 완료 · S8 타임아웃 → 새 러너에서 S1 도 재과금 없이 원격 결과를 다시 받는다."""
    monkeypatch.setenv("NUNA_DURATION_SEC", "60")
    store = db.MemoryStore()
    ctx = _live_ctx(monkeypatch, store, tmp_path / "r1", QueueSession({"req-2": "IN_PROGRESS"}, video=mp4))
    _seed_imaged(store, ctx, tmp_path)
    assert pipeline.run_gen(ctx, only="clips") == 1
    assert set(store.get_episode(ctx.episode_id)["assets_json"]["fal_pending"]) == {"clip_S1", "clip_S8"}

    sess2 = QueueSession(video=mp4, start=3)
    ctx2 = _live_ctx(monkeypatch, store, tmp_path / "r2", sess2)
    assert pipeline.run_gen(ctx2, only="clips") == 0
    assert sess2.posts == [] and len(_charged(store)) == 2


def test_fal_pending_marker_without_job_record_stays_blocked(monkeypatch, tmp_path):
    store = db.MemoryStore()
    ctx = _live_ctx(monkeypatch, store, tmp_path / "r", QueueSession())
    _seed_imaged(store, ctx, tmp_path)
    store.update_episode(ctx.episode_id, {"last_error": "FAL_PENDING:clips: FalPendingTimeout: x"})
    monkeypatch.setattr(pipeline, "_do_step", lambda *a: pytest.fail("유료 단계 재실행"))
    with pytest.raises(pipeline.PipelineError, match="PAID_RECOVERY_REQUIRED"):
        pipeline.run_gen(ctx, only="clips")


def test_non_persisted_timeout_keeps_paid_block(monkeypatch, tmp_path):
    """작업 기록을 원장에 못 남긴 타임아웃은 기존대로 대사 후 복구 대상."""
    store = db.MemoryStore()
    ctx = _live_ctx(monkeypatch, store, tmp_path / "r", QueueSession())
    _seed_imaged(store, ctx, tmp_path)

    def boom(c, step):
        raise FalPendingTimeout("t", "req-x", "IN_PROGRESS")

    monkeypatch.setattr(pipeline, "_do_step", boom)
    ctx._backends = SimpleNamespace(is_dummy=False)
    assert pipeline.run_gen(ctx, only="clips") == 1
    assert store.get_episode(ctx.episode_id)["last_error"].startswith("clips: ")
