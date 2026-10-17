"""gen/hero_shorts/tests/test_tiktok_freeze.py — 틱톡 라인 동결 잠금 (2026-10-18, 결정 A안)
=====================================================================================
목적: 틱톡 발행 라인을 **삭제하지 않고 동결**하기로 한 결정(마스터 A안)이
      실제로 유효한 상태로 유지되도록 회귀 잠금을 건다.

      누군가(또는 미래의 에이전트가) 아래 중 하나를 하면 이 테스트가 즉시 실패한다:
        · TIKTOK_PUBLISH_SUSPENDED 플래그를 False 로 바꾼다
        · 환경변수로 정지 가드를 우회하는 경로를 만든다
        · tiktok 발행 모듈에서 assert_publish_allowed 호출을 제거한다
        · 정지 가드가 **유튜브·쓰레드를 과차단**하도록 넓힌다(반대 방향 회귀)

발행 0회 · 외부 호출 0회 · 순수 오프라인.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from gen.hero_shorts import publish_runtime as pr
from gen.hero_shorts.tiktok import publish as tk_publish

ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- #
# 1. 동결 플래그 자체
# --------------------------------------------------------------------------- #
def test_suspension_flag_is_on():
    assert pr.TIKTOK_PUBLISH_SUSPENDED is True


def test_flag_is_not_env_overridable():
    """정지 가드는 환경변수로 풀 수 없다 — 소스에 os.getenv 분기가 없어야 한다."""
    src = inspect.getsource(pr.assert_publish_allowed)
    assert "TIKTOK_PUBLISH_SUSPENDED" in src
    # 플래그 대입이 getenv 결과에 의존하면 우회 가능해진다
    assert "getenv(\"TIKTOK_PUBLISH_SUSPENDED\")" not in src
    assert "environ[\"TIKTOK_PUBLISH_SUSPENDED\"]" not in src


# --------------------------------------------------------------------------- #
# 2. 틱톡은 차단된다 (플랫폼명 · 대소문자 · 계정 ID 경유)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform", ["tiktok", "TikTok", "TIKTOK", " tiktok "])
def test_tiktok_platform_blocked(platform, monkeypatch):
    monkeypatch.delenv("HERO_TIKTOK_ACCOUNT_ID", raising=False)
    with pytest.raises(RuntimeError, match="TIKTOK_PUBLISH_SUSPENDED"):
        pr.assert_publish_allowed(platform=platform)


def test_tiktok_blocked_via_configured_account_id(monkeypatch):
    """플랫폼명을 속여도 계정 ID 로 틱톡이면 막힌다 — 우회 차단."""
    monkeypatch.setenv("HERO_TIKTOK_ACCOUNT_ID", "tiktok-acct-xyz")
    with pytest.raises(RuntimeError, match="TIKTOK_PUBLISH_SUSPENDED"):
        pr.assert_publish_allowed(platform="youtube", account_id="tiktok-acct-xyz")


def test_tiktok_blocked_via_tiktok_options(monkeypatch):
    monkeypatch.delenv("HERO_TIKTOK_ACCOUNT_ID", raising=False)
    with pytest.raises(RuntimeError, match="TIKTOK_PUBLISH_SUSPENDED"):
        pr.assert_publish_allowed(account_id=None, segment=1)


# --------------------------------------------------------------------------- #
# 3. 반대 방향 회귀 — 유튜브·쓰레드는 막히면 안 된다
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform", ["youtube", "threads", "instagram"])
def test_new_channels_not_overblocked(platform, monkeypatch):
    monkeypatch.delenv("HERO_TIKTOK_ACCOUNT_ID", raising=False)
    assert pr.assert_publish_allowed(platform=platform) is None


# --------------------------------------------------------------------------- #
# 4. 발행 모듈이 가드를 실제로 통과해야 한다 (정적 잠금)
# --------------------------------------------------------------------------- #
def test_tiktok_publish_calls_guard():
    src = inspect.getsource(tk_publish)
    assert "assert_publish_allowed" in src, "틱톡 발행 경로에서 정지 가드가 사라졌다"


def test_tiktok_runner_ready_reports_suspended():
    runner_src = (ROOT / "gen/hero_shorts/tiktok/runner.py").read_text(encoding="utf-8")
    assert "TIKTOK_PUBLISH_SUSPENDED" in runner_src
    assert '"suspended"' in runner_src


# --------------------------------------------------------------------------- #
# 5. 삭제 금지 — 동결 대상이 실제로 남아 있어야 한다
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel", [
    "gen/hero_shorts/tiktok/__init__.py",
    "gen/hero_shorts/tiktok/watchdog.py",
    "gen/hero_shorts/tiktok/next_run.py",
    "gen/hero_shorts/tiktok/publish.py",
    "gen/hero_shorts/tiktok/runner.py",
    "gen/hero_shorts/tiktok/tests/test_ready.py",
])
def test_frozen_modules_still_present(rel):
    assert (ROOT / rel).is_file(), f"동결 대상이 삭제되었다: {rel}"


def test_watchdog_workflow_still_references_package():
    """토대 워크플로우가 이 패키지를 계속 호출한다 — 삭제 금지 근거 1."""
    wf = (ROOT / ".github/workflows/hero_shorts_watchdog.yml").read_text(encoding="utf-8")
    assert "gen.hero_shorts.tiktok.watchdog" in wf


def test_watchdog_aggregates_legacy_tiktok_block():
    """삭제 금지 근거 2 — 레거시 tiktok 블록이 감시 증거로 계속 집계된다."""
    from gen.hero_shorts.tiktok import watchdog as wd
    assert "tiktok" in wd.LEDGER_BLOCK_NAMES
    assert set(wd.LEDGER_BLOCK_NAMES) == {"tiktok", "youtube", "threads"}
