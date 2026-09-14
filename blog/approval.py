"""Approval recommendation policy for Phase 0.

A recommendation does not block package creation. Validation failures do.
Future browser authentication gates must remain mandatory and are not represented
as a timer or automatic bot approval.
"""

from __future__ import annotations

from typing import Any

from blog.domain import (
    ApprovalLevel,
    ApprovalRecommendation,
    ValidationReport,
)


def recommend_approval(
    report: ValidationReport,
    source_data: dict[str, Any],
    *,
    dry_run: bool,
) -> ApprovalRecommendation:
    if not report.passed:
        return ApprovalRecommendation(
            level=ApprovalLevel.BLOCKED,
            reasons=("blocking validation errors must be corrected",),
            blocks_execution=True,
        )

    if dry_run:
        return ApprovalRecommendation(
            level=ApprovalLevel.OPTIONAL,
            reasons=("DRY_RUN does not perform an external publication",),
            blocks_execution=False,
        )

    data = source_data.get("data", source_data)
    regime = data.get("market_regime", {})
    signal = data.get("trading_signal", {})
    reasons = [
        "financial content should receive a final factual review",
    ]

    risk = str(regime.get("market_risk_level") or "").upper()
    action = str(signal.get("trading_signal") or "").upper()
    if risk == "HIGH":
        reasons.append("market risk level is HIGH")
    if action in {"BUY", "ADD", "REDUCE", "HEDGE", "SELL"}:
        reasons.append(f"content contains an actionable system signal: {action}")
    if report.warnings:
        reasons.append(
            f"validation completed with {len(report.warnings)} warning(s)"
        )

    return ApprovalRecommendation(
        level=ApprovalLevel.RECOMMENDED,
        reasons=tuple(reasons),
        blocks_execution=False,
    )
