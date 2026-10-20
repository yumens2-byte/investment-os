"""STD-MODE-06 DRY_RUN 단일 파서 회귀 테스트 (DN2026_0022).

- config.settings.parse_dry_run 값별 판정표를 고정한다.
- is_dry_run()은 호출 시점 env를 따른다.
- 판정을 위임한 엔진들이 settings와 항상 같은 값을 내는지 확인한다.
- 빈 값으로 프로세스를 시작하면 발행 계층(x_publisher)도 dry_run인지 확인한다.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]

DRY = [None, "", " ", "-", "—", "none", "NONE", "true", "TRUE", " true "]
LIVE = ["false", "FALSE", " false ", "False"]
NONSTANDARD = ["abc", "0", "1", "no", "yes"]


@pytest.mark.parametrize("raw", DRY)
def test_dry_values(raw, caplog):
    with caplog.at_level(logging.WARNING, logger="config.settings"):
        assert settings.parse_dry_run(raw) is True
    assert not caplog.records  # 표준값·자리표시자는 경고하지 않는다


@pytest.mark.parametrize("raw", LIVE)
def test_live_only_for_false(raw):
    assert settings.parse_dry_run(raw) is False


@pytest.mark.parametrize("raw", NONSTANDARD)
def test_nonstandard_is_dry_with_single_warning(raw, caplog, monkeypatch):
    monkeypatch.setattr(settings, "_DRY_RUN_WARNED", set())
    with caplog.at_level(logging.WARNING, logger="config.settings"):
        assert settings.parse_dry_run(raw) is True
        assert settings.parse_dry_run(raw) is True  # 반복 호출해도 경고는 1건
    assert len(caplog.records) == 1


def test_is_dry_run_follows_env_at_call_time(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    assert settings.is_dry_run() is False
    monkeypatch.setenv("DRY_RUN", "")
    assert settings.is_dry_run() is True
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert settings.is_dry_run() is True


@pytest.mark.parametrize("module_name", [
    "engines.character_vote",
    "engines.data_quiz",
    "engines.prediction_league",
    "engines.viral_engine",
])
@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("false", False), (" FALSE ", False), ("", True), ("abc", True), (None, True),
])
def test_engines_delegate_to_settings(module_name, raw, expected, monkeypatch):
    module = __import__(module_name, fromlist=["_is_dry_run"])
    if raw is None:
        monkeypatch.delenv("DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("DRY_RUN", raw)
    assert module._is_dry_run() is expected


def test_viral_weekly_report_has_no_local_parser():
    import engines.viral_weekly_report as vwr

    assert not hasattr(vwr, "_is_dry_run")


@pytest.mark.parametrize("raw,expected", [
    ("", "True"), ("false", "False"), ("true", "True"), ("abc", "True"),
])
def test_publisher_constant_matches_parser_in_fresh_process(raw, expected):
    env = dict(os.environ, DRY_RUN=raw)
    code = (
        "import config.settings as s, publishers.x_publisher as x; "
        "print(s.DRY_RUN, x.DRY_RUN)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=60, check=True).stdout.split()
    assert out == [expected, expected]
