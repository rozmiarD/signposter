"""Signposter claim/lease planner (dry-run only).

Determines which ready candidates would be claimed for execution.
No mutations are performed.
"""

from __future__ import annotations

from dataclasses import dataclass

from signposter.dispatch import DispatchDecision, run_dry_run
from signposter.scan import LabeledItem


@dataclass(frozen=True)
class ClaimDryRunResult:
    """Result of a claim planning dry-run."""
    selected: list[ClaimPlan]
    total_claimable: int
    limit: int | None


@dataclass(frozen=True)
class ClaimPlan:
    """Represents a proposed claim action for a candidate item."""

    item: LabeledItem
    dispatch: DispatchDecision
    lease_owner: str
    proposed_state: str
    labels_to_remove: list[str]
    labels_to_add: list[str]
    reason: str


def _claim_sort_key(plan: ClaimPlan) -> tuple[int, int, int]:
    """Deterministic sort key for conservative claim ordering."""
    risk = plan.dispatch.risk or "medium"
    risk_priority = {"low": 0, "medium": 1, "high": 2}.get(risk, 1)

    phase = plan.dispatch.phase or "merge"
    phase_priority = {
        "build": 0,
        "review": 1,
        "plan": 2,
        "merge": 3,
    }.get(phase, 3)

    return (risk_priority, phase_priority, plan.item.number)


def plan_claims(repo: str, *, limit: int | None = 1) -> ClaimDryRunResult:
    """Produce claim plans for ready items, applying conservative limits and ordering."""
    """Produce claim plans for all currently claimable items.

    Only items with state:ready are considered claimable in this phase.
    """
    decisions = run_dry_run(repo)

    all_plans: list[ClaimPlan] = []

    for decision in decisions:
        if decision.state != "ready":
            continue

        lease_owner = "local-dry-run-worker"

        labels_to_remove = ["state:ready"]
        labels_to_add = ["state:active"]

        gate = decision.proposed_gate
        if gate:
            gate_label = f"gate:{gate}"
            if gate_label not in labels_to_add:
                labels_to_add.append(gate_label)

        reason = f"Claiming ready item for route '{decision.proposed_route}'"

        plan = ClaimPlan(
            item=decision.item,
            dispatch=decision,
            lease_owner=lease_owner,
            proposed_state="active",
            labels_to_remove=labels_to_remove,
            labels_to_add=labels_to_add,
            reason=reason,
        )
        all_plans.append(plan)

    # Apply deterministic conservative ordering
    all_plans.sort(key=_claim_sort_key)

    total = len(all_plans)

    if limit is None or limit >= total:
        selected = all_plans
    else:
        selected = all_plans[:limit]

    return ClaimDryRunResult(
        selected=selected,
        total_claimable=total,
        limit=limit,
    )


def format_claim_plan_report(result: ClaimDryRunResult) -> str:
    """Produce a human-readable dry-run claim report with limit information."""
    plans = result.selected
    total = result.total_claimable
    limit = result.limit

    if total == 0:
        return "Claim Dry-Run: No claimable items (state:ready) found.\n"

    lines = ["Signposter Claim / Lease Dry-Run Plan\n"]

    for i, plan in enumerate(plans, 1):
        item = plan.item
        d = plan.dispatch

        lines.append(f"[{i}] {item.item_type.upper()} #{item.number} — {item.title}")
        lines.append(f"    URL: {item.html_url}")
        lines.append("    Current state: ready")
        lines.append(f"    Proposed route: {d.proposed_route}")
        lines.append(f"    Proposed lease owner: {plan.lease_owner}")
        lines.append("    Proposed state transition: ready → active")
        lines.append(f"    Labels to remove: {', '.join(plan.labels_to_remove)}")
        lines.append(f"    Labels to add:    {', '.join(plan.labels_to_add)}")
        lines.append(f"    Reason: {plan.reason}")
        lines.append("")

    unclaimed = total - len(plans)

    lines.append(f"Total claimable items found: {total}")
    lines.append(f"Items selected for claim (limit={limit}): {len(plans)}")
    if unclaimed > 0:
        lines.append(f"Items left unclaimed due to limit: {unclaimed}")
    lines.append("")
    lines.append("Note: This is a DRY RUN. No labels or state were changed on GitHub.")

    return "\n".join(lines)


def cli_main(repo: str, limit: int = 1) -> int:
    """Programmatic entry point for the claim command."""
    try:
        result = plan_claims(repo, limit=limit)
        print(format_claim_plan_report(result))
        return 0
    except Exception as e:
        print(f"Claim dry-run failed: {e}", file=__import__("sys").stderr)
        return 1


def main() -> int:
    """Direct CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Claim/lease dry-run planner")
    parser.add_argument("--repo", required=True, help="Target repository (owner/repo)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required: run in dry-run mode only",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of items to claim in this run (default: 1 for safety)",
    )
    args = parser.parse_args()

    return cli_main(args.repo, limit=args.limit)


if __name__ == "__main__":
    import sys
    sys.exit(main())
