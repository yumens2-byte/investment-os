"""Compatibility guards between the existing pipeline and the blog pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from blog.service import prepare_from_file
from blog_main import build_parser as build_blog_parser
from main import build_parser as build_existing_parser


ROOT = Path(__file__).resolve().parents[1]
EXISTING_AREAS = (
    "collectors",
    "comic",
    "config",
    "core",
    "db",
    "engines",
    "publishers",
    "weekend",
)
IMPORT_EXISTING = re.compile(
    r"^\s*(?:from|import)\s+(?:"
    + "|".join(EXISTING_AREAS)
    + r")(?:\.|\s|$)",
    re.MULTILINE,
)
IMPORT_BLOG = re.compile(r"^\s*(?:from|import)\s+blog(?:\.|\s|$)", re.MULTILINE)


@pytest.mark.parametrize(
    ("argv", "expected_command"),
    [
        (["run", "market", "--session", "morning"], "run"),
        (["run", "view", "--mode", "thread"], "run"),
        (["run", "all", "--session", "close", "--mode", "tweet"], "run"),
        (["schedule", "--now", "morning"], "schedule"),
        (["test", "--round", "1"], "test"),
        (["alert"], "alert"),
        (["status"], "status"),
        (["weekend", "--day", "sat"], "weekend"),
    ],
)
def test_existing_cli_contract_is_preserved(
    argv: list[str],
    expected_command: str,
) -> None:
    args = build_existing_parser().parse_args(argv)
    assert args.command == expected_command


def test_blog_cli_is_a_separate_entrypoint() -> None:
    args = build_blog_parser().parse_args([
        "prepare",
        "--session",
        "weekly",
        "--dry-run",
        "true",
    ])
    assert args.command == "prepare"
    assert args.session == "weekly"

    with pytest.raises(SystemExit):
        build_existing_parser().parse_args(["blog", "prepare"])


def test_existing_pipeline_has_no_dependency_on_blog() -> None:
    offenders: list[str] = []
    for area in EXISTING_AREAS:
        for path in (ROOT / area).rglob("*.py"):
            if IMPORT_BLOG.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    for path in ROOT.glob("*.py"):
        if path.name == "blog_main.py":
            continue
        if IMPORT_BLOG.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)

    assert not offenders, "existing pipeline imports blog: " + ", ".join(offenders)


def test_blog_package_has_no_dependency_on_existing_pipeline() -> None:
    offenders: list[str] = []
    for path in (ROOT / "blog").rglob("*.py"):
        if IMPORT_EXISTING.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, "blog imports existing pipeline: " + ", ".join(offenders)


def test_prepare_reads_source_without_mutation_and_writes_only_blog_output(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    source = {
        "timestamp": now.isoformat(),
        "data": {
            "market_snapshot": {
                "sp500": 0.1,
                "nasdaq": 0.2,
                "vix": 15.0,
                "us10y": 4.2,
                "oil": 75.0,
                "dollar_index": 102.0,
            },
            "market_regime": {
                "market_regime": "Transition",
                "market_risk_level": "MEDIUM",
            },
            "trading_signal": {
                "trading_signal": "HOLD",
                "signal_reason": "compatibility fixture",
            },
            "etf_allocation": {
                "allocation": {"QQQM": 50, "TLT": 50},
            },
            "sources": [
                {"name": "fixture", "url": "https://example.com/source"},
            ],
        },
    }
    source_dir = tmp_path / "existing"
    source_dir.mkdir()
    source_path = source_dir / "core_data.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    source_before = source_path.read_bytes()
    source_hash_before = hashlib.sha256(source_before).hexdigest()

    output_root = tmp_path / "isolated-blog-output"
    result = prepare_from_file(
        source_path,
        session="morning",
        output_dir=output_root,
        dry_run=True,
        now=now,
    )

    assert result["success"] is True
    assert result["publish_mode"] == "PREPARE_ONLY"
    assert source_path.read_bytes() == source_before
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash_before
    assert Path(result["output_dir"]).is_relative_to(output_root)
    assert sorted(path.name for path in source_dir.iterdir()) == ["core_data.json"]
