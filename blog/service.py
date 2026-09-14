"""Application service for materializing PREPARE_ONLY blog artifacts."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from blog.approval import recommend_approval
from blog.formatter import build_blog_post
from blog.validator import validate_blog_package


def prepare_from_file(
    core_data_path: str | Path,
    *,
    session: str,
    output_dir: str | Path | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    source_path = Path(core_data_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"core data file not found: {source_path}")

    with source_path.open(encoding="utf-8") as stream:
        core_envelope = json.load(stream)

    package = build_blog_post(
        core_envelope,
        session,
        created_at=now or datetime.now(timezone.utc),
    )
    report = validate_blog_package(
        package,
        core_envelope,
        now=now,
        max_age_hours=float(
            os.getenv("NAVER_BLOG_MAX_AGE_HOURS", "36")
        ),
    )
    approval = recommend_approval(
        report,
        core_envelope,
        dry_run=dry_run,
    )

    root = Path(
        output_dir
        or os.getenv(
            "NAVER_BLOG_OUTPUT_DIR",
            "data/outputs/naver_blog",
        )
    )
    target = root / package.job_id
    if target.is_dir():
        return {
            "success": report.passed,
            "existing": True,
            "job_id": package.job_id,
            "output_dir": str(target),
            "validation": report.to_dict(),
            "approval": approval.to_dict(),
            "publish_mode": "PREPARE_ONLY",
        }

    root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(
        prefix=f".{package.job_id}-",
        dir=root,
    ))
    try:
        _write_text(temp_dir / "title.txt", package.title + "\n")
        _write_text(temp_dir / "body.md", package.body_markdown)
        _write_text(temp_dir / "body.txt", package.body_plain)
        _write_text(
            temp_dir / "tags.txt",
            " ".join(f"#{tag}" for tag in package.tags) + "\n",
        )
        _write_text(temp_dir / "sources.md", _render_sources(package.sources))
        _write_json(temp_dir / "validation-report.json", report.to_dict())
        _write_json(
            temp_dir / "manifest.json",
            {
                "package": package.to_dict(),
                "validation": report.to_dict(),
                "approval": approval.to_dict(),
                "publish_mode": "PREPARE_ONLY",
                "dry_run": dry_run,
            },
        )
        try:
            temp_dir.replace(target)
        except FileExistsError:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return {
        "success": report.passed,
        "existing": False,
        "job_id": package.job_id,
        "output_dir": str(target),
        "validation": report.to_dict(),
        "approval": approval.to_dict(),
        "publish_mode": "PREPARE_ONLY",
    }


def _render_sources(sources) -> str:
    if not sources:
        return "# Sources\n\nNo source links were included in core data.\n"
    lines = ["# Sources", ""]
    for source in sources:
        lines.append(
            f"- [{source.name}]({source.url})"
            if source.url
            else f"- {source.name}"
        )
    return "\n".join(lines) + "\n"


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, content: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
