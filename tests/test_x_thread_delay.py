"""
tests/test_x_thread_delay.py
================================
publishers/x_publisher.py v1.1.0 — 스레드 포스트 간 발행 간격 랜덤화 검증.

검증 범위:
  1. 범위 해석 (_resolve_thread_delay_range) — 정상/예산초과/경계
  2. publish_thread 실발행 경로에서 고정 간격이 사라졌는지
  3. 시그니처·반환 스펙 불변 (회귀)
"""
from __future__ import annotations

import random

import pytest

from publishers import x_publisher as xp

# ─────────────────────────────────────────────────────────────────────────
# 1. 범위 해석
# ─────────────────────────────────────────────────────────────────────────

def test_range_no_gap_returns_base():
    """포스트 1개(간격 0) → 축소 없이 기본 범위."""
    lo, hi = xp._resolve_thread_delay_range(1)
    assert lo == pytest.approx(xp.X_THREAD_DELAY_MIN_SEC)
    assert hi == pytest.approx(xp.X_THREAD_DELAY_MAX_SEC)


def test_range_zero_posts_safe():
    """포스트 0개여도 예외 없이 유효 범위 반환."""
    lo, hi = xp._resolve_thread_delay_range(0)
    assert 0 < lo < hi


def test_range_within_budget_not_scaled():
    """예산 내(7포스트=6간격, 평균 21.5초 → 129초 < 180초) → 축소 없음."""
    lo, hi = xp._resolve_thread_delay_range(7)
    assert lo == pytest.approx(xp.X_THREAD_DELAY_MIN_SEC)
    assert hi == pytest.approx(xp.X_THREAD_DELAY_MAX_SEC)


def test_range_over_budget_is_scaled():
    """포스트가 많으면 범위가 축소되고 총 기대 대기가 예산 이내로 들어온다."""
    posts = 30
    lo, hi = xp._resolve_thread_delay_range(posts)
    assert lo < xp.X_THREAD_DELAY_MIN_SEC
    assert hi < xp.X_THREAD_DELAY_MAX_SEC
    expected_total = (lo + hi) / 2 * (posts - 1)
    assert expected_total <= xp.X_THREAD_DELAY_BUDGET_SEC + 1e-6


def test_range_never_below_floor_and_stays_random():
    """극단적으로 긴 스레드에서도 하한 보장 + 상한 > 하한(난수성 유지)."""
    lo, hi = xp._resolve_thread_delay_range(10_000)
    assert lo >= xp.X_THREAD_DELAY_FLOOR_SEC
    assert hi > lo


# ─────────────────────────────────────────────────────────────────────────
# 2. 발행 경로
# ─────────────────────────────────────────────────────────────────────────

class _FakeClient:
    pass


@pytest.fixture
def _live_publish(monkeypatch):
    """DRY_RUN 해제 + X 호출 스텁 + sleep 계측."""
    slept: list[float] = []

    monkeypatch.setattr(xp, "DRY_RUN", False)
    monkeypatch.setattr(xp, "_get_client", lambda: _FakeClient())

    counter = {"n": 0}

    def _fake_single(client, text, reply_to_id=None):
        counter["n"] += 1
        return f"tid{counter['n']}"

    monkeypatch.setattr(xp, "_publish_single", _fake_single)
    monkeypatch.setattr(xp.time, "sleep", lambda s: slept.append(float(s)))
    return slept


def test_inter_post_delays_are_random(_live_publish):
    """포스트 간 대기가 고정값이 아니며 해석된 범위 안에 있다."""
    random.seed(20260831)
    posts = [f"p{i}" for i in range(6)]
    result = xp.publish_thread(posts)

    assert result["success"] is True
    assert result["published_count"] == 6

    # slept[0]은 함수 진입 쿨다운(randint 15~30), 이후가 포스트 간 대기
    gaps = _live_publish[1:]
    assert len(gaps) == 5, f"간격 수 불일치: {gaps}"

    lo, hi = xp._resolve_thread_delay_range(6)
    for g in gaps:
        assert lo <= g <= hi

    assert len(set(gaps)) > 1, "간격이 고정값이다 (랜덤화 실패)"
    assert all(abs(g - 1.5) > 0.01 for g in gaps), "구 고정 1.5초가 남아 있다"


def test_single_post_has_no_inter_delay(_live_publish):
    """포스트 1개면 포스트 간 대기가 발생하지 않는다."""
    random.seed(1)
    xp.publish_thread(["only"])
    assert len(_live_publish) == 1  # 진입 쿨다운 1회뿐


def test_dry_run_skips_publish_loop(monkeypatch):
    """DRY_RUN이면 발행 루프에 진입하지 않는다 (기존 동작 불변)."""
    slept: list[float] = []
    monkeypatch.setattr(xp, "DRY_RUN", True)
    monkeypatch.setattr(xp.time, "sleep", lambda s: slept.append(float(s)))

    result = xp.publish_thread(["a", "b", "c"])

    assert result["dry_run"] is True
    assert result["tweet_ids"] == ["DRY_RUN"] * 3
    assert len(slept) == 1  # 진입 쿨다운만


# ─────────────────────────────────────────────────────────────────────────
# 3. 회귀 — 시그니처/반환 스펙 불변
# ─────────────────────────────────────────────────────────────────────────

def test_return_contract_unchanged(_live_publish):
    """반환 키 세트 불변. 단수 tweet_id 키는 여전히 없다."""
    random.seed(7)
    result = xp.publish_thread(["a", "b"], reply_to="123")
    assert set(result) == {"success", "published_count", "tweet_ids", "dry_run"}
    assert "tweet_id" not in result
    assert isinstance(result["tweet_ids"], list)


def test_signature_unchanged():
    """publish_thread(posts, reply_to=None) 시그니처 유지 → 호출부 무수정."""
    import inspect

    sig = inspect.signature(xp.publish_thread)
    params = list(sig.parameters)
    assert params == ["posts", "reply_to"]
    assert sig.parameters["reply_to"].default is None


def test_version_bumped():
    assert xp.VERSION == "1.1.0"
