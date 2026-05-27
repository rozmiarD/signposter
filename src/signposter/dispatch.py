"""Signposter dispatch (dry-run only in bootstrap phase).

Classifies candidate items and proposes routing actions without performing any mutations.
"""

from __future__ import annotations

from dataclasses import dataclass

from signposter.scan import LabeledItem


@dataclass(frozen=True)
class DispatchDecision:
    """Represents the proposed action for a candidate item."""

    item: LabeledItem
    phase: str | None
    state: str | None
    role: str | None
    risk: str | None
    area: str | None
    proposed_route: str
    proposed_gate: str | None
    reason: str


def extract_label_value(labels: list[str], prefix: str) -> str | None:
    """Extract value from a label like 'phase:build' → 'build'."""
    for label in labels:
        if label.startswith(f"{prefix}:"):
            return label.split(":", 1)[1]
    return None


def classify_candidate(item: LabeledItem) -> DispatchDecision:
    """Pure classification logic for a single candidate item.

    This function contains no side effects and is fully testable.
    """
    phase = extract_label_value(item.labels, "phase")
    state = extract_label_value(item.labels, "state")
    role = extract_label_value(item.labels, "role")
    risk = extract_label_value(item.labels, "risk")
    area = extract_label_value(item.labels, "area")

    route = "reviewer"  # safe default
    gate: str | None = None
    reason = "default routing"

    # Explicit routing rules (bootstrap phase)
    if phase == "build" and role == "worker" and risk == "low":
        route = "worker"
        gate = "ci"
        reason = "low-risk build task assigned to worker with CI gate"
    elif phase == "review" and role == "reviewer":
        route = "reviewer"
        gate = "review"
        reason = "review phase routed to reviewer role with review gate"
    elif phase == "plan" and role == "planner":
        route = "planner"
        gate = None
        reason = "planning task routed to planner"
    elif risk == "high":
        route = "reviewer"
        gate = "human"
        reason = "high risk requires human gate"
    elif state == "ready":
        route = "worker"
        gate = "ci"
        reason = "ready state defaults to worker with CI gate"

    return DispatchDecision(
        item=item,
        phase=phase,
        state=state,
        role=role,
        risk=risk,
        area=area,
        proposed_route=route,
        proposed_gate=gate,
        reason=reason,
    )


def run_dry_run(repo: str) -> list[DispatchDecision]:
    """Execute a full dry-run dispatch for a repository.

    Reuses the scanner to fetch candidates, then classifies each one.
    """
    from signposter.scan import run_scan

    scan_result = run_scan(repo)
    candidates = scan_result.get("candidates", [])

    decisions = [classify_candidate(item) for item in candidates]
    return decisions


def format_dry_run_report(decisions: list[DispatchDecision]) -> str:
    """Produce a human-readable dry-run plan."""
    if not decisions:
        return "Dispatch Dry-Run: No candidate items found.\n"

    lines = ["Signposter Dispatch Dry-Run Plan\n"]

    for i, decision in enumerate(decisions, 1):
        item = decision.item
        lines.append(f"[{i}] {item.item_type.upper()} #{item.number} — {item.title}")
        lines.append(f"    URL: {item.html_url}")
        lines.append("    Extracted:")
        lines.append(f"      phase={decision.phase}, state={decision.state}, role={decision.role}")
        lines.append(f"      risk={decision.risk}, area={decision.area}")
        lines.append("    Proposed Action:")
        lines.append(f"      route → {decision.proposed_route}")
        if decision.proposed_gate:
            lines.append(f"      gate  → {decision.proposed_gate}")
        lines.append(f"      reason: {decision.reason}")
        lines.append("")

    lines.append(f"Total candidates evaluated: {len(decisions)}")
    lines.append("Note: This is a DRY RUN. No actions were taken on GitHub.")

    return "\n".join(lines)


def cli_main(repo: str) -> int:
    """Programmatic entry point (used by parent CLI)."""
    try:
        decisions = run_dry_run(repo)
        print(format_dry_run_report(decisions))
        return 0
    except Exception as e:
        print(f"Dispatch dry-run failed: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Direct CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Dispatch dry-run (read-only)")
    parser.add_argument("--repo", required=True, help="Target repository (owner/repo)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required flag for dry-run mode (no mutations)",
    )
    args = parser.parse_args()

    return cli_main(args.repo)


if __name__ == "__main__":
    import sys
    sys.exit(main())
