"""Operational guardrails shared by production GitHub Actions workflows."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci_alert_tests.yml"


def test_daily_comic_delay_is_a_real_step() -> None:
    """The anti-bot delay must not accidentally become shell script content."""
    text = (WORKFLOWS / "comic_daily.yml").read_text(encoding="utf-8")

    assert text.count("- name: Anti-bot random delay (schedule only)") == 1
    assert "          - name: Anti-bot random delay" not in text
    assert 'sleep "$DELAY"' in text


def test_every_production_job_has_a_timeout() -> None:
    """Every non-CI job needs a finite upper bound for failure detection."""
    ignored = {"ci_alert_tests.yml", "notify_watchdog.yml"}
    missing: list[str] = []

    for path in WORKFLOWS.glob("*.yml"):
        if path.name in ignored:
            continue
        text = path.read_text(encoding="utf-8")
        jobs_match = re.search(r"^jobs:\n(?P<body>[\s\S]+)$", text, re.MULTILINE)
        assert jobs_match is not None, f"{path.name} does not contain jobs"
        body = jobs_match.group("body")
        job_starts = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.MULTILINE))

        for index, match in enumerate(job_starts):
            end = job_starts[index + 1].start() if index + 1 < len(job_starts) else len(body)
            job_body = body[match.end():end]
            if "timeout-minutes:" not in job_body:
                missing.append(f"{path.name}:{match.group(1)}")

    assert not missing, "jobs without timeout-minutes: " + ", ".join(missing)


def test_long_random_delays_have_runtime_headroom() -> None:
    """A 15-minute anti-bot delay still needs time for setup and publishing."""
    expected_timeouts = {
        "comic_daily.yml": 30,
        "weekend_content.yml": 30,
    }

    for filename, minimum_timeout in expected_timeouts.items():
        text = (WORKFLOWS / filename).read_text(encoding="utf-8")
        timeout = re.search(r"^    timeout-minutes:\s*(\d+)", text, re.MULTILINE)
        assert timeout is not None
        assert int(timeout.group(1)) >= minimum_timeout
        assert "RANDOM % 900" in text


def test_watchdog_guardrails_are_connected_to_ci() -> None:
    """Changes to workflows and their guards must trigger and execute CI."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert text.count("- '.github/workflows/*.yml'") == 2
    assert "pytest -q tests/test_watchdog_workflow.py tests/test_workflow_operations.py" in text
    assert "rhysd/actionlint:1.7.7" in text
