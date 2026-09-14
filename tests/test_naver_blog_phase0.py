"""Phase 0 tests for the Naver blog PREPARE_ONLY pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from blog.approval import recommend_approval
from blog.domain import ApprovalLevel
from blog.formatter import DISCLAIMER, build_blog_post
from blog.service import prepare_from_file
from blog.validator import validate_blog_package


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _core_data(timestamp: datetime = NOW) -> dict:
    return {
        "timestamp": timestamp.isoformat(),
        "data": {
            "market_snapshot": {
                "sp500": -0.38,
                "nasdaq": -0.29,
                "vix": 14.0,
                "us10y": 4.77,
                "oil": 91.2,
                "dollar_index": 103.4,
            },
            "market_regime": {
                "market_regime": "Transition",
                "market_risk_level": "MEDIUM",
            },
            "trading_signal": {
                "trading_signal": "HOLD",
                "signal_reason": "금리와 유가를 함께 확인할 구간입니다.",
            },
            "etf_allocation": {
                "allocation": {
                    "QQQM": 20,
                    "SPYM": 25,
                    "XLE": 20,
                    "ITA": 15,
                    "TLT": 20,
                },
                "total_weight": 100,
            },
            "output_helpers": {
                "one_line_summary": "전환 구간에서는 방향성보다 위험 관리가 우선입니다.",
                "top_headlines": [
                    "미국 국채 금리와 유가 흐름을 점검합니다.",
                    "주요 경제지표 발표 일정을 확인합니다.",
                ],
            },
            "sources": [
                {"name": "FRED", "url": "https://fred.stlouisfed.org/"},
                {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/"},
            ],
        },
    }


def test_build_blog_post_is_deterministic() -> None:
    source = _core_data()

    first = build_blog_post(source, "morning", created_at=NOW)
    second = build_blog_post(source, "morning", created_at=NOW)

    assert first.job_id == second.job_id
    assert first.idempotency_key == second.idempotency_key
    assert first.content_hash == second.content_hash
    assert first.metadata["publish_mode"] == "PREPARE_ONLY"
    assert DISCLAIMER in first.body_markdown


def test_valid_package_passes_and_approval_is_recommended() -> None:
    source = _core_data()
    package = build_blog_post(source, "morning", created_at=NOW)

    report = validate_blog_package(package, source, now=NOW)
    recommendation = recommend_approval(report, source, dry_run=False)

    assert report.passed
    assert recommendation.level == ApprovalLevel.RECOMMENDED
    assert recommendation.blocks_execution is False


def test_dry_run_approval_is_optional() -> None:
    source = _core_data()
    package = build_blog_post(source, "morning", created_at=NOW)
    report = validate_blog_package(package, source, now=NOW)

    recommendation = recommend_approval(report, source, dry_run=True)

    assert recommendation.level == ApprovalLevel.OPTIONAL
    assert recommendation.blocks_execution is False


def test_stale_market_data_is_blocked_not_approved() -> None:
    source = _core_data(NOW - timedelta(hours=48))
    package = build_blog_post(source, "morning", created_at=NOW)

    report = validate_blog_package(
        package,
        source,
        now=NOW,
        max_age_hours=36,
    )
    recommendation = recommend_approval(report, source, dry_run=False)

    assert not report.passed
    assert any(item.code == "MARKET_DATA_STALE" for item in report.errors)
    assert recommendation.level == ApprovalLevel.BLOCKED
    assert recommendation.blocks_execution is True


def test_missing_numeric_core_value_is_blocked() -> None:
    source = _core_data()
    source["data"]["market_snapshot"]["vix"] = None
    package = build_blog_post(source, "morning", created_at=NOW)

    report = validate_blog_package(package, source, now=NOW)

    assert not report.passed
    assert any(
        item.code == "CORE_VALUE_MISSING"
        and item.field == "market_snapshot.vix"
        for item in report.errors
    )


def test_prepare_from_file_materializes_idempotent_package(tmp_path) -> None:
    core_path = tmp_path / "core_data.json"
    core_path.write_text(
        json.dumps(_core_data(), ensure_ascii=False),
        encoding="utf-8",
    )
    output_root = tmp_path / "naver"

    first = prepare_from_file(
        core_path,
        session="morning",
        output_dir=output_root,
        dry_run=False,
        now=NOW,
    )
    second = prepare_from_file(
        core_path,
        session="morning",
        output_dir=output_root,
        dry_run=False,
        now=NOW,
    )

    package_dir = output_root / first["job_id"]
    assert first["success"] is True
    assert first["existing"] is False
    assert second["existing"] is True
    assert first["job_id"] == second["job_id"]
    assert (package_dir / "manifest.json").is_file()
    assert (package_dir / "title.txt").is_file()
    assert (package_dir / "body.md").is_file()
    assert (package_dir / "body.txt").is_file()
    assert (package_dir / "tags.txt").is_file()
    assert (package_dir / "sources.md").is_file()
    assert (package_dir / "validation-report.json").is_file()

    manifest = json.loads(
        (package_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["publish_mode"] == "PREPARE_ONLY"
    assert manifest["approval"]["level"] == "RECOMMENDED"
    assert manifest["validation"]["status"] == "PASS"


def test_cli_exposes_prepare_only_blog_command() -> None:
    from blog_main import build_parser

    args = build_parser().parse_args([
        "prepare",
        "--session",
        "morning",
        "--dry-run",
        "false",
    ])

    assert args.command == "prepare"
    assert args.session == "morning"
    assert args.dry_run == "false"
