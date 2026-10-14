"""v2.9.1 (P-1) — 회차 자동 선택이 원장 기준인지 검증."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gen.hero_shorts import ledger


def test_ledger_first_returns_base_plus_one():
    """원장 최대 회차가 주어지면 트래커 조회 없이 base+1 을 반환한다."""
    assert ledger.find_next_episode(base=87) == 88
    assert ledger.find_next_episode(base=0) == 1


def test_ledger_first_ignores_polluted_tracker(monkeypatch):
    """트래커가 과거 회차(Ep42)를 미완료로 반환해도 원장 기준이 이긴다."""
    monkeypatch.setattr(ledger, "query_tracker_rows", lambda ep=None: [{"번호": "42", "발행 상태": None}])
    assert ledger.find_next_episode(base=87) == 88


def test_no_base_falls_back_to_tracker(monkeypatch):
    """base 미제공 시에만 트래커 fallback(구 동작)이 동작한다."""
    monkeypatch.setattr(ledger, "query_tracker_rows", lambda ep=None: [{"번호": "42", "발행 상태": None}])
    assert ledger.find_next_episode() == 42


def test_no_base_and_empty_tracker_raises(monkeypatch):
    monkeypatch.setattr(ledger, "query_tracker_rows", lambda ep=None: [])
    with pytest.raises(RuntimeError):
        ledger.find_next_episode()
