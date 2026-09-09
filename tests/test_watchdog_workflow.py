"""Regression tests for the pipeline watchdog workflow."""

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = REPOSITORY_ROOT / ".github" / "workflows"
WATCHDOG_PATH = WORKFLOW_DIRECTORY / "notify_watchdog.yml"


def _workflow_name(path: Path) -> str:
    match = re.search(r"^name:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, f"{path.name} does not define a top-level name"
    return match.group(1).strip("\"'")


def _watched_workflow_names() -> set[str]:
    text = WATCHDOG_PATH.read_text(encoding="utf-8")
    match = re.search(r"^    workflows:\n(?P<items>(?:      - .+\n)+)", text, re.MULTILINE)
    assert match is not None, "watchdog does not define a workflow_run workflow list"
    return {
        line.removeprefix("      - ").strip().strip("\"'")
        for line in match.group("items").splitlines()
    }


def test_watchdog_monitors_every_production_workflow() -> None:
    """Every workflow other than CI and the watchdog itself must be monitored."""
    ignored_files = {"ci_alert_tests.yml", WATCHDOG_PATH.name}
    production_names = {
        _workflow_name(path)
        for path in WORKFLOW_DIRECTORY.glob("*.yml")
        if path.name not in ignored_files
    }

    assert _watched_workflow_names() == production_names


def test_watchdog_stays_failure_only_and_least_privileged() -> None:
    text = WATCHDOG_PATH.read_text(encoding="utf-8")

    assert "github.event.workflow_run.conclusion != 'success'" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "permissions: {}" in text
    assert "actions/checkout" not in text


def test_watchdog_validates_telegram_delivery() -> None:
    text = WATCHDOG_PATH.read_text(encoding="utf-8")

    assert "secrets.TELEGRAM_BOT_TOKEN" in text
    assert "secrets.TELEGRAM_PAID_CHANNEL_ID" in text
    assert "--retry 3 --retry-all-errors --retry-delay 2" in text
    assert '--connect-timeout 5 --max-time 20' in text
    assert 'response.get("ok") is not True' in text
