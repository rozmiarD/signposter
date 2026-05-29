"""Signposter claim/lease planner (dry-run only).

Determines which ready candidates would be claimed for execution.
No mutations are performed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from signposter.comments import format_claim_comment
from signposter.dependencies import is_dependency_blocked
from signposter.dispatch import DispatchDecision, run_dry_run
from signposter.labels import check_labels
from signposter.scan import LabeledItem, fetch_issue_context


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


def perform_claim_mutation(plan: ClaimPlan, repo: str, *, dry_run: bool = True) -> list[str]:
    """Perform (or simulate) the label mutation and comment for a single claim plan.

    Returns list of gh commands that were executed (or would be executed).
    """
    item = plan.item
    commands: list[str] = []

    # 1. Edit labels: remove state:ready, add state:active + gate label
    add_labels = ",".join(plan.labels_to_add)
    remove_labels = ",".join(plan.labels_to_remove)

    edit_cmd = [
        "gh", "issue", "edit", str(item.number),
        "-R", repo,
        "--add-label", add_labels,
        "--remove-label", remove_labels,
    ]
    commands.append(" ".join(edit_cmd))

    if not dry_run:
        subprocess.run(edit_cmd, check=True, capture_output=True, text=True)

    # 2. Add comment
    comment_body = format_claim_comment(
        route=plan.dispatch.proposed_route,
        gate=plan.dispatch.proposed_gate,
        lease_owner=plan.lease_owner,
    )

    comment_cmd = [
        "gh", "issue", "comment", str(item.number),
        "-R", repo,
        "--body", comment_body,
    ]
    commands.append(" ".join(comment_cmd))

    if not dry_run:
        subprocess.run(comment_cmd, check=True, capture_output=True, text=True)

    return commands


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


def _required_label_preflight(repo: str) -> tuple[bool, list[str], str | None]:
    """Centralized required-label preflight for claim (H023D-A)."""
    try:
        result = check_labels(repo)
        if result.error:
            return False, [], f"label preflight failed: {result.error}"
        if result.missing:
            return False, result.missing, None
        return True, [], None
    except Exception as e:
        return False, [], f"label preflight error: {str(e)[:200]}"


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

        # HARDENING-005: dependency awareness (computed)
        item = decision.item
        context = fetch_issue_context(repo, item.number) or {}
        body = context.get("body", "")
        blocked, block_reason = is_dependency_blocked(repo, body)

        if blocked:
            # Skip dependency-blocked ready items (do not claim)
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


def format_claim_plan_report(result: ClaimDryRunResult, *, dry_run: bool = True) -> str:
    """Produce a human-readable claim plan report with limit information."""
    plans = result.selected
    total = result.total_claimable
    limit = result.limit

    if total == 0:
        prefix = "Claim Dry-Run" if dry_run else "Claim Plan"
        return f"{prefix}: No claimable items (state:ready) found.\n"

    mode_word = "Dry-Run" if dry_run else "Plan"
    lines = [f"Signposter Claim / Lease {mode_word} Plan\n"]

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
    if dry_run:
        lines.append("Note: This is a DRY RUN. No labels or state were changed on GitHub.")
    else:
        lines.append("Note: This plan will be applied (labels will be mutated on GitHub).")

    return "\n".join(lines)


def cli_main(repo: str, limit: int = 1, *, apply: bool = False) -> int:
    """Programmatic entry point for the claim command."""
    try:
        result = plan_claims(repo, limit=limit)
        print(format_claim_plan_report(result, dry_run=not apply))

        if apply and result.selected:
            # H023D-A: Centralized label preflight before any mutation
            ok, missing, err = _required_label_preflight(repo)
            if not ok:
                reason = err or ("required labels missing: " + ", ".join(missing))
                print(f"\nStatus: blocked — {reason}")
                print("\nNotes:")
                print("  No issue was claimed.")
                print("  No labels were changed.")
                print("  No GitHub mutation was performed.")
                return 1

            print("\n=== APPLYING MUTATIONS (real changes) ===\n")
            for plan in result.selected:
                print(f"Applying claim to issue #{plan.item.number}...")
                commands = perform_claim_mutation(plan, repo, dry_run=False)
                for cmd in commands:
                    print(f"  Executed: {cmd}")
            print("\nMutation complete.")
        elif apply:
            print("No items selected to apply.")

        return 0
    except Exception as e:
        print(f"Claim failed: {e}", file=__import__("sys").stderr)
        return 1


def main() -> int:
    """Direct CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Claim/lease planner")
    parser.add_argument("--repo", required=True, help="Target repository (owner/repo)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in read-only dry-run mode (default behavior)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the claim mutation on GitHub (requires explicit confirmation)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of items to claim in this run (default: 1 for safety)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        # Default to dry-run for safety if neither flag is given
        args.dry_run = True

    return cli_main(args.repo, limit=args.limit, apply=args.apply)


if __name__ == "__main__":
    import sys
    sys.exit(main())
