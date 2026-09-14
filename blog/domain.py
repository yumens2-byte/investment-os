"""Domain models for the Naver blog PREPARE_ONLY pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ApprovalLevel(StrEnum):
    BLOCKED = "BLOCKED"
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"
    OPTIONAL = "OPTIONAL"


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True)
class SourceRef:
    name: str
    url: str = ""
    supports: tuple[str, ...] = ()


@dataclass(frozen=True)
class BlogImage:
    path: str
    sha256: str
    order: int
    caption: str = ""
    alt_text: str = ""


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: ValidationSeverity
    message: str
    field: str = ""


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple[ValidationFinding, ...] = ()

    @property
    def errors(self) -> tuple[ValidationFinding, ...]:
        return tuple(
            item for item in self.findings
            if item.severity == ValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[ValidationFinding, ...]:
        return tuple(
            item for item in self.findings
            if item.severity == ValidationSeverity.WARNING
        )

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.passed else "FAIL",
            "errors": [_serialize(item) for item in self.errors],
            "warnings": [_serialize(item) for item in self.warnings],
        }


@dataclass(frozen=True)
class ApprovalRecommendation:
    level: ApprovalLevel
    reasons: tuple[str, ...] = ()
    blocks_execution: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class BlogPostPackage:
    job_id: str
    idempotency_key: str
    content_hash: str
    content_version: int
    channel: str
    session: str
    publication_date_kst: str
    market_as_of: datetime
    created_at: datetime
    title: str
    summary: str
    body_markdown: str
    body_plain: str
    tags: tuple[str, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    images: tuple[BlogImage, ...] = ()
    source_data_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {
            key: _serialize(item)
            for key, item in asdict(value).items()
        }
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value
