"""
tests/test_character_vote.py — A안 캐릭터 투표 (25케이스)
외부 라이브러리 없이 실행 가능 (_get_client / 지연 import monkeypatch).
"""
from __future__ import annotations

import sys
import types

import pytest

sys.path.insert(0, ".")

from engines import character_vote as cv  # noqa: E402


# ─────────────────────────────────────────────────────────────
# _pick_winner (7)
# ─────────────────────────────────────────────────────────────

def test_pick_winner_single():
    assert cv._pick_winner({"EDT": 5, "Iron Nuna": 3}) == "EDT"


def test_pick_winner_all_zero():
    assert cv._pick_winner({"EDT": 0, "Iron Nuna": 0}) is None


def test_pick_winner_tie():
    assert cv._pick_winner({"EDT": 4, "Iron Nuna": 4}) is None


def test_pick_winner_empty():
    assert cv._pick_winner({}) is None


def test_pick_winner_negative_guard():
    assert cv._pick_winner({"EDT": -1}) is None


def test_pick_winner_three_way():
    assert cv._pick_winner({"A": 1, "B": 9, "C": 2}) == "B"


def test_pick_winner_tie_among_top_only():
    assert cv._pick_winner({"A": 9, "B": 9, "C": 2}) is None


# ─────────────────────────────────────────────────────────────
# _is_valid_tweet_id (5)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", None, "FAIL", "DRY_RUN", "X_FAIL"])
def test_invalid_tweet_ids(bad):
    assert cv._is_valid_tweet_id(bad) is False


def test_valid_tweet_id():
    assert cv._is_valid_tweet_id("1234567890") is True


# ─────────────────────────────────────────────────────────────
# select_candidates (4)
# ─────────────────────────────────────────────────────────────

def _mock_daily_store(monkeypatch, novel_text):
    mod = types.ModuleType("db.daily_store")
    mod.get_novel = lambda d: {"novel_text": novel_text} if novel_text else None
    monkeypatch.setitem(sys.modules, "db.daily_store", mod)


def test_select_candidates_from_novel(monkeypatch):
    _mock_daily_store(monkeypatch, "EDT와 Iron Nuna, Futures Girl이 War Dominion과 싸웠다")
    names = [c[0] for c in cv.select_candidates("2026-08-09")]
    assert set(names) == {"EDT", "Iron Nuna", "Futures Girl", "War Dominion"}


def test_select_candidates_max_four(monkeypatch):
    text = " ".join(cv.CHARACTERS.keys())  # 8명 전원 등장
    _mock_daily_store(monkeypatch, text)
    assert len(cv.select_candidates("2026-08-09")) == cv.MAX_CANDIDATES


def test_select_candidates_fallback_when_few(monkeypatch):
    _mock_daily_store(monkeypatch, "EDT만 등장")  # 1명 < 3
    names = [c[0] for c in cv.select_candidates("2026-08-09")]
    assert names == [c[0] for c in cv._FALLBACK_CANDIDATES]


def test_select_candidates_fallback_when_no_novel(monkeypatch):
    _mock_daily_store(monkeypatch, None)
    names = [c[0] for c in cv.select_candidates("2026-08-09")]
    assert names == [c[0] for c in cv._FALLBACK_CANDIDATES]


# ─────────────────────────────────────────────────────────────
# publish_vote 멱등/DRY_RUN (3)
# ─────────────────────────────────────────────────────────────

class _FakeExec:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        def _chain(*a, **k):
            return self
        return _chain

    def execute(self):
        return _FakeExec(self._data)


class _FakeClient:
    def __init__(self, select_data):
        self._select_data = select_data
        self.inserted = []

    def table(self, name):
        return _FakeQuery(self._select_data)


def test_publish_vote_idempotent(monkeypatch):
    monkeypatch.setattr(cv, "_get_client", lambda: _FakeClient([{"id": 1}]))
    result = cv.publish_vote(reply_to="123")
    assert result["success"] is False
    assert result["reason"] == "already_published"


def test_publish_vote_dry_run_no_db(monkeypatch):
    monkeypatch.setattr(cv, "_get_client", lambda: _FakeClient([]))
    monkeypatch.setenv("DRY_RUN", "true")
    _mock_daily_store(monkeypatch, None)

    pub_mod = types.ModuleType("publishers.x_publisher")
    pub_mod.publish_thread = lambda posts, reply_to=None: {
        "success": True, "tweet_ids": ["DRY_RUN"] * len(posts), "dry_run": True,
        "published_count": len(posts),
    }
    monkeypatch.setitem(sys.modules, "publishers.x_publisher", pub_mod)

    result = cv.publish_vote(reply_to=None)
    assert result["success"] is True
    assert result.get("dry_run") is True


def test_publish_vote_invalid_reply_to_falls_back(monkeypatch):
    """무효 reply_to(FAIL)는 None으로 처리되어 독립 발행."""
    captured = {}

    monkeypatch.setattr(cv, "_get_client", lambda: _FakeClient([]))
    monkeypatch.setenv("DRY_RUN", "true")
    _mock_daily_store(monkeypatch, None)

    def _pt(posts, reply_to=None):
        captured.setdefault("calls", []).append(reply_to)
        return {"success": True, "tweet_ids": ["DRY_RUN"] * len(posts),
                "dry_run": True, "published_count": len(posts)}

    pub_mod = types.ModuleType("publishers.x_publisher")
    pub_mod.publish_thread = _pt
    monkeypatch.setitem(sys.modules, "publishers.x_publisher", pub_mod)

    cv.publish_vote(reply_to="FAIL")
    assert captured["calls"][0] is None  # 헤더는 독립 발행


# ─────────────────────────────────────────────────────────────
# get_latest_winner (2)
# ─────────────────────────────────────────────────────────────

def test_get_latest_winner_found(monkeypatch):
    monkeypatch.setattr(
        cv, "_get_client",
        lambda: _FakeClient([{"winner": "EDT", "vote_date": "2026-08-08"}]),
    )
    assert cv.get_latest_winner() == "EDT"


def test_get_latest_winner_none(monkeypatch):
    monkeypatch.setattr(cv, "_get_client", lambda: _FakeClient([]))
    assert cv.get_latest_winner() is None
