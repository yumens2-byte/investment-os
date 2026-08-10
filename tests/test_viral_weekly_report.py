"""
tests/test_viral_weekly_report.py — B안 주간 성적표 (18케이스)
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from engines import viral_weekly_report as vw  # noqa: E402


def _item(er=0.01, reply=5, like=10, imp=1000, a="회사 관두고 전업투자", b="월급 존버"):
    return {
        "opt_a": a, "opt_b": b, "engagement_rate": er,
        "reply_count": reply, "like_count": like,
        "impression_count": imp, "milestone_hours": 80,
    }


# ─────────────────────────────────────────────────────────────
# _shorten (4)
# ─────────────────────────────────────────────────────────────

def test_shorten_no_trim():
    assert vw._shorten("짧은 문장") == "짧은 문장"


def test_shorten_trim():
    text = "가" * 50
    out = vw._shorten(text, max_len=40)
    assert len(out) == 40 and out.endswith("…")


def test_shorten_none():
    assert vw._shorten(None) == ""


def test_shorten_strip():
    assert vw._shorten("  공백  ") == "공백"


# ─────────────────────────────────────────────────────────────
# _format_item_line (5)
# ─────────────────────────────────────────────────────────────

def test_format_line_medal():
    assert vw._format_item_line(1, _item()).startswith("🥇")
    assert vw._format_item_line(3, _item()).startswith("🥉")


def test_format_line_stats_all():
    line = vw._format_item_line(1, _item(reply=7, like=11, imp=500))
    assert "💬7" in line and "❤️11" in line and "👀500" in line


def test_format_line_impression_none_omitted():
    line = vw._format_item_line(1, _item(imp=None))
    assert "👀" not in line


def test_format_line_reply_none_omitted():
    line = vw._format_item_line(1, _item(reply=None))
    assert "💬" not in line


def test_format_line_contains_vs():
    line = vw._format_item_line(2, _item())
    assert "vs" in line


# ─────────────────────────────────────────────────────────────
# build_scoreboard_tweet (4)
# ─────────────────────────────────────────────────────────────

def test_build_tweet_and_tg():
    tweet, tg = vw.build_scoreboard_tweet([_item(), _item(er=0.02), _item(er=0.005)])
    assert "투자 참고 정보" in tweet
    assert tg.startswith("<b>")


def test_build_tweet_header_from_pool():
    tweet, _ = vw.build_scoreboard_tweet([_item()])
    assert any(tweet.startswith(h) for h in vw._HEADERS)


def test_build_tweet_length_cap():
    items = [_item(a="가" * 200, b="나" * 200) for _ in range(3)]
    tweet, _ = vw.build_scoreboard_tweet(items)
    assert len(tweet) <= 3900


def test_build_tweet_ranks():
    tweet, _ = vw.build_scoreboard_tweet([_item(), _item(), _item()])
    assert "🥇" in tweet and "🥈" in tweet and "🥉" in tweet


# ─────────────────────────────────────────────────────────────
# run_weekly_scoreboard 가드 (3)
# ─────────────────────────────────────────────────────────────

def test_run_skips_when_not_saturday(monkeypatch):
    monkeypatch.setenv("FORCE_RUN", "false")
    monkeypatch.setattr(vw, "_force_run", lambda: False)

    class _FakeDT:
        @staticmethod
        def weekday():
            return 2  # 수요일

    import datetime as _dt
    real_now = _dt.datetime.now

    class _Patch(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_now(tz)
            # 수요일로 강제 (2026-08-12는 수요일)
            return base.replace(year=2026, month=8, day=12)

    monkeypatch.setattr(vw, "datetime", _Patch)
    result = vw.run_weekly_scoreboard()
    assert result["success"] is False
    assert result["reason"] == "not_saturday"


def test_run_skips_insufficient_data(monkeypatch):
    monkeypatch.setattr(vw, "_force_run", lambda: True)
    monkeypatch.setattr(vw, "collect_weekly_top", lambda: [_item()])
    result = vw.run_weekly_scoreboard()
    assert result["success"] is False
    assert result["reason"] == "insufficient_data"


def test_min_items_constant():
    assert vw.MIN_ITEMS == 3 and vw.TOP_LIMIT == 3


# ─────────────────────────────────────────────────────────────
# collect_weekly_top 정렬 (2) — _get_client mock
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


def test_collect_sorted_by_er(monkeypatch):
    logs = [
        {"log_id": "L1", "opt_a": "a1", "opt_b": "b1", "tweet_id": "1",
         "created_at": "2026-08-09"},
        {"log_id": "L2", "opt_a": "a2", "opt_b": "b2", "tweet_id": "2",
         "created_at": "2026-08-08"},
    ]
    metrics = [
        {"log_id": "L1", "milestone_hours": 80, "impression_count": 100,
         "like_count": 1, "reply_count": 1, "engagement_rate": 0.01},
        {"log_id": "L2", "milestone_hours": 80, "impression_count": 100,
         "like_count": 5, "reply_count": 5, "engagement_rate": 0.05},
    ]

    class _Client:
        def table(self, name):
            return _FakeQuery(logs if name == "viral_logs" else metrics)

    monkeypatch.setattr(vw, "_get_client", lambda: _Client())
    items = vw.collect_weekly_top()
    assert items[0]["opt_a"] == "a2"  # ER 0.05 우선


def test_collect_empty_logs(monkeypatch):
    class _Client:
        def table(self, name):
            return _FakeQuery([])

    monkeypatch.setattr(vw, "_get_client", lambda: _Client())
    assert vw.collect_weekly_top() == []
