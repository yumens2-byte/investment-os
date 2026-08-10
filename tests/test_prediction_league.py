"""
tests/test_prediction_league.py — C안 주간 예측 리그 (22케이스)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from engines import prediction_league as pl  # noqa: E402

KST = timezone(timedelta(hours=9))


# ─────────────────────────────────────────────────────────────
# judge 경계 (8)
# ─────────────────────────────────────────────────────────────

def test_judge_up():
    assert pl.judge(100.0, 101.0) == "up"


def test_judge_down():
    assert pl.judge(100.0, 99.0) == "down"


def test_judge_flat_inside_threshold_up():
    # +0.04% < 0.05% → flat
    assert pl.judge(100.0, 100.04) == "flat"


def test_judge_flat_inside_threshold_down():
    assert pl.judge(100.0, 99.96) == "flat"


def test_judge_boundary_exact_threshold_is_up():
    # 정확히 0.05%는 flat 아님 (abs(pct) < 0.05 조건)
    assert pl.judge(100.0, 100.05) == "up"


def test_judge_boundary_exact_threshold_is_down():
    assert pl.judge(100.0, 99.95) == "down"


def test_judge_zero_baseline_flat():
    assert pl.judge(0.0, 100.0) == "flat"


def test_judge_equal_flat():
    assert pl.judge(650.0, 650.0) == "flat"


# ─────────────────────────────────────────────────────────────
# _week_monday (3)
# ─────────────────────────────────────────────────────────────

def test_week_monday_on_monday():
    d = datetime(2026, 8, 10, 12, 0, tzinfo=KST)  # 월요일
    assert pl._week_monday(d) == "2026-08-10"


def test_week_monday_on_saturday():
    d = datetime(2026, 8, 15, 12, 0, tzinfo=KST)  # 토요일
    assert pl._week_monday(d) == "2026-08-10"


def test_week_monday_on_sunday():
    d = datetime(2026, 8, 16, 12, 0, tzinfo=KST)  # 일요일
    assert pl._week_monday(d) == "2026-08-10"


# ─────────────────────────────────────────────────────────────
# 공용 mock
# ─────────────────────────────────────────────────────────────

class _FakeExec:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data, sink=None, table=""):
        self._data = data
        self._sink = sink
        self._table = table

    def insert(self, payload):
        if self._sink is not None:
            self._sink.append((self._table, "insert", payload))
        return self

    def update(self, payload):
        if self._sink is not None:
            self._sink.append((self._table, "update", payload))
        return self

    def __getattr__(self, name):
        def _chain(*a, **k):
            return self
        return _chain

    def execute(self):
        return _FakeExec(self._data)


class _FakeClient:
    def __init__(self, data, sink=None):
        self._data = data
        self.sink = sink if sink is not None else []

    def table(self, name):
        return _FakeQuery(self._data, sink=self.sink, table=name)


# ─────────────────────────────────────────────────────────────
# run_prediction_open (5)
# ─────────────────────────────────────────────────────────────

def test_open_idempotent(monkeypatch):
    monkeypatch.setattr(pl, "_get_client", lambda: _FakeClient([{"id": 1}]))
    result = pl.run_prediction_open()
    assert result["success"] is False and result["reason"] == "already_open"


def test_open_skips_when_baseline_unavailable(monkeypatch):
    monkeypatch.setattr(pl, "_get_client", lambda: _FakeClient([]))
    monkeypatch.setattr(pl, "_collect_spy_price", lambda: None)
    result = pl.run_prediction_open()
    assert result["success"] is False
    assert result["reason"] == "baseline_unavailable"


def test_open_dry_run_no_db(monkeypatch):
    import types
    monkeypatch.setattr(pl, "_get_client", lambda: _FakeClient([]))
    monkeypatch.setattr(pl, "_collect_spy_price", lambda: 650.25)
    monkeypatch.setenv("DRY_RUN", "true")

    pub = types.ModuleType("publishers.x_publisher")
    pub.publish_thread = lambda posts, reply_to=None: {
        "success": True, "tweet_ids": ["DRY_RUN"] * len(posts),
        "dry_run": True, "published_count": len(posts),
    }
    monkeypatch.setitem(sys.modules, "publishers.x_publisher", pub)

    result = pl.run_prediction_open()
    assert result["success"] is True and result.get("dry_run") is True
    assert result["baseline"] == 650.25


def test_open_publish_failure(monkeypatch):
    import types
    monkeypatch.setattr(pl, "_get_client", lambda: _FakeClient([]))
    monkeypatch.setattr(pl, "_collect_spy_price", lambda: 650.0)

    pub = types.ModuleType("publishers.x_publisher")
    pub.publish_thread = lambda posts, reply_to=None: {
        "success": False, "tweet_ids": [], "published_count": 0,
    }
    monkeypatch.setitem(sys.modules, "publishers.x_publisher", pub)

    result = pl.run_prediction_open()
    assert result["success"] is False and result["reason"] == "open_tweet_failed"


def test_open_template_contains_baseline():
    text = pl._OPEN_TEMPLATES[0].format(baseline=650.25)
    assert "$650.25" in text


# ─────────────────────────────────────────────────────────────
# run_prediction_settle (6)
# ─────────────────────────────────────────────────────────────

def _round_row(week="2026-08-03", settle="2026-08-07",
               baseline=650.0, open_id="900"):
    return {
        "id": "r1", "week_key": week, "baseline_value": baseline,
        "settle_date": settle, "open_tweet_id": open_id,
        "option_tweets": {"up": "901", "down": "902"},
        "created_at": f"{week}T11:17:00+00:00",
    }


def test_settle_no_targets(monkeypatch):
    monkeypatch.setattr(pl, "_get_client", lambda: _FakeClient([]))
    result = pl.run_prediction_settle()
    assert result["success"] is True and result["settled"] == 0


def test_settle_void_when_expired(monkeypatch):
    client = _FakeClient([])
    monkeypatch.setattr(pl, "_get_client", lambda: client)
    old = _round_row(week="2026-07-06", settle="2026-07-10")
    ok = pl._settle_one(old)
    assert ok is False
    assert any(op == "update" and p.get("status") == "void"
               for _, op, p in client.sink)


def test_settle_keeps_open_when_price_unavailable(monkeypatch):
    client = _FakeClient([])
    monkeypatch.setattr(pl, "_get_client", lambda: client)
    monkeypatch.setattr(pl, "_collect_spy_price", lambda: None)
    recent = _round_row(settle=datetime.now(KST).strftime("%Y-%m-%d"))
    ok = pl._settle_one(recent)
    assert ok is False
    assert not any(op == "update" for _, op, p in client.sink)


def test_settle_success(monkeypatch):
    import types
    client = _FakeClient([])
    monkeypatch.setattr(pl, "_get_client", lambda: client)
    monkeypatch.setattr(pl, "_collect_spy_price", lambda: 655.0)
    monkeypatch.setattr(pl, "_collect_vote_stats", lambda opts: "")

    pub = types.ModuleType("publishers.x_publisher")
    pub.publish_thread = lambda posts, reply_to=None: {
        "success": True, "tweet_ids": ["777"], "published_count": 1,
    }
    monkeypatch.setitem(sys.modules, "publishers.x_publisher", pub)

    recent = _round_row(settle=datetime.now(KST).strftime("%Y-%m-%d"))
    ok = pl._settle_one(recent)
    assert ok is True
    updates = [p for _, op, p in client.sink if op == "update"]
    assert updates and updates[0]["status"] == "settled"
    assert updates[0]["result"] == "up"


def test_settle_dry_run_keeps_open(monkeypatch):
    import types
    client = _FakeClient([])
    monkeypatch.setattr(pl, "_get_client", lambda: client)
    monkeypatch.setattr(pl, "_collect_spy_price", lambda: 655.0)
    monkeypatch.setattr(pl, "_collect_vote_stats", lambda opts: "")

    pub = types.ModuleType("publishers.x_publisher")
    pub.publish_thread = lambda posts, reply_to=None: {
        "success": True, "tweet_ids": ["DRY_RUN"], "dry_run": True,
        "published_count": 1,
    }
    monkeypatch.setitem(sys.modules, "publishers.x_publisher", pub)

    recent = _round_row(settle=datetime.now(KST).strftime("%Y-%m-%d"))
    ok = pl._settle_one(recent)
    assert ok is False
    assert not any(op == "update" for _, op, p in client.sink)


def test_vote_stats_failure_returns_empty(monkeypatch):
    import types
    trk = types.ModuleType("engines.viral_performance_tracker")

    def _boom(ids):
        raise RuntimeError("rate limit")

    trk.fetch_metrics_batch = _boom
    monkeypatch.setitem(sys.modules, "engines.viral_performance_tracker", trk)
    assert pl._collect_vote_stats({"up": "1", "down": "2"}) == ""
