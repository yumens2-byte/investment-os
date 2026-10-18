"""publish_selector 오프라인 회귀 테스트 — 승인 게이트 A-G1/G3/G4."""

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "gen" / "hero_shorts" / "monitor" / "publish_selector.py"

spec = importlib.util.spec_from_file_location("publish_selector_under_test", SCRIPT)
sel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sel)


def _ep(appr="APPROVED", yt="pending", th="pending", yt_att=0, th_att=0):
    return {
        "approval": {"status": appr},
        "targets": {
            "youtube": {"status": yt, "attempts": [{"at": "2026-10-18T00:00:00Z"}] * yt_att},
            "threads": {"status": th, "attempts": [{"at": "2026-10-18T00:00:00Z"}] * th_att},
        },
    }


def test_approved_pending_is_candidate():
    out = sel.select({"_schema": 2, "ep89": _ep()})
    assert out["candidates"] == [{"ep": "ep89", "platforms": ["youtube", "threads"]}]
    assert out["skipped"] == []


def test_non_approved_is_skipped_fail_closed():
    for appr in ("HOLD", None, "READY_FOR_APPROVAL"):
        out = sel.select({"ep89": _ep(appr=appr)})
        assert out["candidates"] == [], appr
        assert out["skipped"][0]["gate"] == "A-G1"


def test_attempt_budget_exhausted_is_skipped():
    out = sel.select({"ep88": _ep(th_att=5)})
    assert out["candidates"][0]["platforms"] == ["youtube"]      # threads 만 상한
    assert out["skipped"][0]["gate"] == "A-G3"
    out2 = sel.select({"ep88": _ep(yt_att=3, th_att=3)})
    assert out2["candidates"] == []
    assert {s["gate"] for s in out2["skipped"]} == {"A-G3"}


def test_run_cap_one_episode_per_run():
    ledger = {"ep89": _ep(), "ep90": _ep()}
    out = sel.select(ledger)
    assert [c["ep"] for c in out["candidates"]] == ["ep89"]
    assert out["skipped"][0]["gate"] == "A-G4"


def test_published_and_meta_keys_ignored():
    ledger = {"_schema": 2, "ep86": _ep(yt="published", th="published")}
    out = sel.select(ledger)
    assert out["candidates"] == []
    assert out["skipped"][0]["reason"].startswith("발행 대상 플랫폼 없음")


def test_pending_inflight_attempt_blocks_platform():
    ep = _ep(th_att=1)
    ep["targets"]["threads"]["attempts"] = [{"at": "2026-10-18T09:10:10Z", "outcome": "pending"}]
    ep["targets"]["threads"]["post_id"] = "6ad48c8302dff79071e36a59"
    out = sel.select({"ep87": ep})
    assert out["candidates"][0]["platforms"] == ["youtube"]   # threads만 잠금
    assert out["skipped"][0]["gate"] == "A-G3"
    assert "pending" in out["skipped"][0]["reason"]
    # 유튜브도 동일: pending이면 스킵
    ep2 = _ep(yt_att=1)
    ep2["targets"]["youtube"]["attempts"] = [{"at": "2026-10-18T09:10:10Z", "outcome": "pending"}]
    out2 = sel.select({"ep87": ep2})
    assert out2["candidates"][0]["platforms"] == ["threads"]
