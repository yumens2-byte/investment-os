"""publish.py 단위 테스트 — DummyZernioClient·MemoryStore, 네트워크 없음."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from gen.nuna_weekly import config
from gen.nuna_weekly.db import MemoryStore
from gen.nuna_weekly.models import Script
from gen.nuna_weekly.publish import (
    PublishError,
    accounts_from_env,
    build_entries,
    health_blocks,
    iso_kst,
    plan_requests,
    publish,
    redact_obj,
    scheduled_for,
    verify,
)
from gen.nuna_weekly.zernio import DummyZernioClient

EP = "2026-W43"
CAP = f"이번 주 미국 증시 요약 #{{}}\n{config.DISCLAIMER}"
ACCTS = {p: f"{i}" * 24 for i, p in enumerate(config.PLATFORMS, 1)}
MEDIA = {"main": {"main": "https://cdn.test/main_m.mp4?sig=1", "sub": "https://cdn.test/main_s.mp4"},
         "fb_cut": {"sub": "https://cdn.test/fb_s.mp4"}}
PDATE = date(2026, 10, 26)


def script(**over) -> Script:
    caps = {p: CAP.format(p) for p in config.PLATFORMS}
    caps.update(over)
    return Script(episode_id=EP, title="NUNA 주간 브리핑 " + "가" * 120, segments=[], captions=caps)


def clients(**kw):
    return {"main": DummyZernioClient("main", **kw), "sub": DummyZernioClient("sub", **kw)}


def test_scheduled_for():
    assert iso_kst(scheduled_for(PDATE, None)) == "2026-10-26T08:30:00+09:00"
    late = datetime(2026, 10, 26, 8, 0, tzinfo=config.KST)
    assert iso_kst(scheduled_for(PDATE, late)) == "2026-10-26T09:02:00+09:00"
    early = datetime(2026, 10, 25, 22, 0, tzinfo=config.KST)
    assert iso_kst(scheduled_for(PDATE, early)) == "2026-10-26T08:30:00+09:00"


def test_build_entries_shapes():
    e = build_entries(script(), ACCTS)
    assert e["instagram"]["platformSpecificData"] == {"shareToFeed": True, "thumbOffset": 2000}
    assert e["threads"]["platformSpecificData"] == {"topic_tag": config.THREADS_TOPIC_TAG}
    yt = e["youtube"]["platformSpecificData"]
    assert len(yt["title"]) == 100 and yt["containsSyntheticMedia"] is True and yt["madeForKids"] is False
    assert e["facebook"]["platformSpecificData"]["contentType"] == "reel"
    assert e["threads"]["accountId"] == ACCTS["threads"]


def test_build_entries_validation():
    with pytest.raises(PublishError):
        build_entries(script(threads="x" * 501 + config.DISCLAIMER), ACCTS)
    with pytest.raises(PublishError):
        build_entries(script(instagram="면책 없음"), ACCTS)


def test_plan_requests():
    specs = plan_requests(EP, MEDIA, "fb_cut")
    assert [(s["key_alias"], s["platforms"], s["idempotency_key"]) for s in specs] == [
        ("main", ["instagram"], f"{EP}:main:instagram"),   # DN2026_0085 플랫폼별 분리
        ("main", ["threads"], f"{EP}:main:threads"),
        ("sub", ["youtube"], f"{EP}:sub:youtube"),
        ("sub", ["facebook"], f"{EP}:sub:facebook")]
    assert specs[3]["media_url"] == "https://cdn.test/fb_s.mp4"
    assert specs[2]["media_url"] == "https://cdn.test/main_s.mp4"
    with pytest.raises(PublishError):
        plan_requests(EP, {"main": {"main": "x"}}, "main")


def test_publish_ok_and_idempotent_rerun():
    st, cl = MemoryStore(), clients()
    res = publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    assert {p: r.status for p, r in res.items()} == {p: "scheduled" for p in config.PLATFORMS}
    posts = [c for c in cl["main"].calls + cl["sub"].calls if c[0] == "create_post"]
    assert sorted(c[1] for c in posts) == sorted([f"{EP}:main:instagram", f"{EP}:main:threads",
                                                  f"{EP}:sub:youtube", f"{EP}:sub:facebook"])
    body = posts[0][2]
    assert body["scheduledFor"] == "2026-10-26T08:30:00+09:00" and body["timezone"] == "Asia/Seoul"
    assert "publishNow" not in body
    rows = {t["platform"]: t for t in st.list_targets(EP)}
    assert rows["threads"]["attempts"] == 1 and rows["threads"]["zernio_post_id"]
    req = str(rows["instagram"]["request_json"])
    assert ACCTS["instagram"] not in req and "sig=1" not in req
    # 재실행 — POST 없음
    n = len(cl["main"].calls) + len(cl["sub"].calls)
    publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    assert not [c for c in cl["main"].calls[n:] + cl["sub"].calls if c[0] == "create_post" and c not in posts]


def test_publish_dry():
    st, cl = MemoryStore(), clients()
    res = publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "fb_cut", dry=True)
    assert all(r.status == "skipped" for r in res.values())
    assert cl["main"].calls == [] and cl["sub"].calls == []
    assert all(t["last_error"] == "DRY_RUN" for t in st.list_targets(EP))


def test_publish_validation_before_any_call():
    st, cl = MemoryStore(), clients()
    with pytest.raises(PublishError):
        publish(EP, st, cl, script(youtube="면책 없음"), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    assert cl["main"].calls == [] and st.list_targets(EP) == []


def test_health_block_and_isolation():
    health = {"accounts": [{"platform": "threads", "status": "unhealthy", "canPost": False},
                           {"platform": "instagram", "status": "healthy", "canPost": True}]}
    st = MemoryStore()
    cl = {"main": DummyZernioClient("main", health=health), "sub": DummyZernioClient("sub", http_status=500)}
    res = publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    assert res["threads"].status == "blocked" and res["instagram"].status == "scheduled"
    assert res["youtube"].status == "pending" and res["facebook"].status == "pending"
    keys = [c[1] for c in cl["main"].calls if c[0] == "create_post"]
    assert keys == [f"{EP}:main:instagram"]


def test_health_unknown_shape_does_not_block():
    assert health_blocks({"weird": 1}, ["threads"], ACCTS) == {}
    assert health_blocks({"accounts": [{"platform": "threads"}]}, ["threads"], ACCTS) == {}


def test_transport_exception_keeps_pending():
    class Boom(DummyZernioClient):
        def create_post(self, body, idempotency_key):
            raise ConnectionError("x")
    st = MemoryStore()
    cl = {"main": Boom("main"), "sub": DummyZernioClient("sub")}
    res = publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    assert res["instagram"].status == "pending" and res["youtube"].status == "scheduled"


def test_retry_after_failure_uses_new_key():
    st = MemoryStore()
    publish(EP, st, {"main": DummyZernioClient("main", http_status=400), "sub": DummyZernioClient("sub")},
            script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    cl = clients()
    publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    assert [c[1] for c in cl["main"].calls if c[0] == "create_post"] == [f"{EP}:main:instagram:r1",
                                                                    f"{EP}:main:threads:r1"]  # DN2026_0085
    assert not [c for c in cl["sub"].calls if c[0] == "create_post"]


def test_verify():
    st, cl = MemoryStore(), clients()
    publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    slept = []
    out = verify(EP, st, cl, sleep=slept.append)
    assert sorted(out["published"]) == sorted(config.PLATFORMS) and out["pending"] == []
    rows = {t["platform"]: t for t in st.list_targets(EP)}
    assert rows["youtube"]["platform_post_url"].startswith("https://youtube.com/")
    assert slept == []


def test_verify_pending_rounds_and_failed():
    st = MemoryStore()
    publish(EP, st, clients(), script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    slept = []
    out = verify(EP, st, clients(get_status="scheduled"), max_rounds=3, interval_sec=7, sleep=slept.append)
    assert len(out["pending"]) == 4 and slept == [7, 7]
    out = verify(EP, st, clients(get_status="failed"), sleep=slept.append)
    assert sorted(out["failed"]) == sorted(config.PLATFORMS)


def test_redact_obj():
    r = redact_obj({"platforms": [{"accountId": "abc123"}], "u": "https://a.test/x?token=s"})
    assert r["platforms"][0]["accountId"] == "***" and r["u"] == "https://a.test/x***"


def test_accounts_from_env(monkeypatch):
    for p, env in config.ACCOUNT_ENV.items():
        monkeypatch.setenv(env, ACCTS[p])
    assert accounts_from_env() == ACCTS


def test_main_key_403_on_one_platform_does_not_fail_the_other():
    """DN2026_0085 재현: W44 베타에서 Instagram·Threads 묶음 요청이 403 하나로 둘 다 실패했다."""
    class Reject403ForInstagram(DummyZernioClient):
        def create_post(self, body, idempotency_key):
            plats = [p.get("platform") for p in body["platforms"]]
            if "instagram" in plats:
                self.calls.append(("create_post", idempotency_key, body))
                return 403, {"error": "One or more accounts do not belong to this user"}
            return super().create_post(body, idempotency_key)

    st = MemoryStore()
    cl = {"main": Reject403ForInstagram("main"), "sub": DummyZernioClient("sub")}
    res = publish(EP, st, cl, script(), MEDIA, ACCTS, None, PDATE, "main", dry=False)
    assert res["instagram"].status == "failed"
    assert res["threads"].status == "scheduled"
    assert res["youtube"].status == "scheduled" and res["facebook"].status == "scheduled"
    bodies = [c[2] for c in cl["main"].calls if c[0] == "create_post"]
    assert [[p["platform"] for p in b["platforms"]] for b in bodies] == [["instagram"], ["threads"]]
