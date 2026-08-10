"""
tests/test_data_quiz.py — F안 데이터 퀴즈 (18케이스)
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from engines import data_quiz as dq  # noqa: E402


# ─────────────────────────────────────────────────────────────
# _bucket 경계 (8)
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (14.9, "A"),
    (15.0, "B"),   # low 이상 → B
    (19.9, "B"),
    (20.0, "C"),   # mid 이상 → C
    (24.9, "C"),
    (25.0, "D"),   # high 이상 → D
    (40.0, "D"),
    (0.0, "A"),
])
def test_bucket_vix_bounds(value, expected):
    assert dq._bucket(value, (15.0, 20.0, 25.0)) == expected


# ─────────────────────────────────────────────────────────────
# _bucket_labels (3)
# ─────────────────────────────────────────────────────────────

def test_bucket_labels_vix():
    spec = next(s for s in dq._QUIZ_SPECS if s["key"] == "vix")
    labels = dq._bucket_labels(spec)
    assert labels["A"] == "15.0 미만"
    assert labels["B"] == "15.0 ~ 20.0"
    assert labels["D"] == "25.0 이상"


def test_bucket_labels_oil_format():
    spec = next(s for s in dq._QUIZ_SPECS if s["key"] == "oil_wti")
    labels = dq._bucket_labels(spec)
    assert labels["A"] == "$70 미만"


def test_bucket_labels_change_format():
    spec = next(s for s in dq._QUIZ_SPECS if s["key"] == "spy_change")
    labels = dq._bucket_labels(spec)
    assert labels["B"] == "-1.00% ~ +0.00%"


# ─────────────────────────────────────────────────────────────
# build_quiz (5)
# ─────────────────────────────────────────────────────────────

def _snap(**kw):
    base = {
        "snapshot_date": "2026-08-07", "vix": None, "oil_wti": None,
        "us10y": None, "dollar_index": None, "fear_greed": None,
        "btc_usd": None, "spy_change": None, "nasdaq_change": None,
    }
    base.update(kw)
    return base


def test_build_quiz_none_when_no_metric():
    assert dq.build_quiz(_snap()) is None


def test_build_quiz_single_metric():
    quiz = dq.build_quiz(_snap(vix=18.5))
    assert quiz is not None
    assert quiz["metric_key"] == "vix"
    assert quiz["answer"] == "B"
    assert quiz["answer_value"] == "18.5"
    assert "2026-08-07" in quiz["question_text"]


def test_build_quiz_contains_all_options():
    quiz = dq.build_quiz(_snap(oil_wti=88.0))
    q = quiz["question_text"]
    assert "A)" in q and "B)" in q and "C)" in q and "D)" in q
    assert "투자 참고 정보" in q


def test_build_quiz_answer_label_matches():
    quiz = dq.build_quiz(_snap(fear_greed=80))
    assert quiz["answer"] == "D"
    assert quiz["answer_label"] == "75 이상"


def test_build_quiz_picks_from_available_only():
    for _ in range(10):
        quiz = dq.build_quiz(_snap(vix=18.0, btc_usd=95000.0))
        assert quiz["metric_key"] in ("vix", "btc_usd")


# ─────────────────────────────────────────────────────────────
# run_data_quiz 가드 (2) — _get_client mock
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
    def __init__(self, data):
        self._data = data

    def table(self, name):
        return _FakeQuery(self._data)


def test_run_quiz_idempotent(monkeypatch):
    monkeypatch.setattr(dq, "_get_client", lambda: _FakeClient([{"id": 1}]))
    result = dq.run_data_quiz()
    assert result["success"] is False and result["reason"] == "already_published"


def test_run_quiz_skips_without_snapshot(monkeypatch):
    monkeypatch.setattr(dq, "_get_client", lambda: _FakeClient([]))
    monkeypatch.setattr(dq, "_latest_snapshot", lambda: None)
    result = dq.run_data_quiz()
    assert result["success"] is False and result["reason"] == "no_snapshot"
