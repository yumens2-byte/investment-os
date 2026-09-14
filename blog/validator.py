"""Validation rules for generated Naver blog packages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from blog.domain import (
    BlogPostPackage,
    ValidationFinding,
    ValidationReport,
    ValidationSeverity,
)
from blog.formatter import DISCLAIMER


def validate_blog_package(
    package: BlogPostPackage,
    source_data: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float = 36.0,
) -> ValidationReport:
    """Validate data provenance and package integrity before any approval."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    findings: list[ValidationFinding] = []

    _required(package.title, "title", findings)
    _required(package.body_markdown, "body_markdown", findings)
    _required(package.summary, "summary", findings)

    if len(package.title) > 100:
        findings.append(_error(
            "TITLE_TOO_LONG",
            f"title length is {len(package.title)}; internal limit is 100",
            "title",
        ))

    if DISCLAIMER not in package.body_markdown:
        findings.append(_error(
            "DISCLAIMER_MISSING",
            "investment information disclaimer is missing",
            "body_markdown",
        ))

    if package.market_as_of > current + timedelta(minutes=5):
        findings.append(_error(
            "MARKET_TIMESTAMP_IN_FUTURE",
            "market timestamp is later than the validation clock",
            "market_as_of",
        ))
    elif current - package.market_as_of > timedelta(hours=max_age_hours):
        findings.append(_error(
            "MARKET_DATA_STALE",
            f"market data exceeds the {max_age_hours:g} hour freshness limit",
            "market_as_of",
        ))

    source_payload = source_data.get("data", source_data)
    snapshot = source_payload.get("market_snapshot", {})
    expected_markers = {
        "sp500": "S&P 500:",
        "nasdaq": "Nasdaq:",
        "vix": "VIX:",
        "us10y": "미국 10년물 금리:",
        "oil": "WTI:",
    }
    for field, marker in expected_markers.items():
        value = snapshot.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            findings.append(_error(
                "CORE_VALUE_MISSING",
                f"numeric core value is missing: market_snapshot.{field}",
                f"market_snapshot.{field}",
            ))
        elif marker not in package.body_markdown:
            findings.append(_error(
                "CORE_VALUE_NOT_RENDERED",
                f"core value section was not rendered: {field}",
                "body_markdown",
            ))

    if len(package.body_plain) < 300:
        findings.append(_warning(
            "BODY_SHORT",
            f"body length is {len(package.body_plain)}; review depth before publishing",
            "body_plain",
        ))

    if not package.sources:
        findings.append(_warning(
            "SOURCE_LINKS_MISSING",
            "no source links were found; human source review is recommended",
            "sources",
        ))
    elif any(not source.url for source in package.sources):
        findings.append(_warning(
            "SOURCE_URL_INCOMPLETE",
            "one or more source entries do not contain a URL",
            "sources",
        ))

    if len(package.tags) > 30:
        findings.append(_error(
            "TOO_MANY_TAGS",
            "tag count exceeds the internal limit of 30",
            "tags",
        ))

    for image in package.images:
        path = Path(image.path)
        if not path.is_file():
            findings.append(_error(
                "IMAGE_NOT_FOUND",
                f"image file does not exist: {path}",
                "images",
            ))

    return ValidationReport(findings=tuple(findings))


def _required(
    value: Any,
    field: str,
    findings: list[ValidationFinding],
) -> None:
    if value is None or (isinstance(value, str) and not value.strip()):
        findings.append(_error(
            "REQUIRED_FIELD_MISSING",
            f"required field is empty: {field}",
            field,
        ))


def _error(code: str, message: str, field: str) -> ValidationFinding:
    return ValidationFinding(
        code=code,
        severity=ValidationSeverity.ERROR,
        message=message,
        field=field,
    )


def _warning(code: str, message: str, field: str) -> ValidationFinding:
    return ValidationFinding(
        code=code,
        severity=ValidationSeverity.WARNING,
        message=message,
        field=field,
    )
