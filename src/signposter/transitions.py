"""Signposter release / complete / fail transitions (dry-run only in bootstrap).

Handles state transitions for already-claimed (state:active) items.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TransitionPlan:
    """Represents a planned state transition for an issue."""

    issue_number: int
    current_labels: list[str]
    labels_to_remove: list[str]
    labels_to_add: list[str]
    new_state: str
    action: str  # "release", "complete", or "fail"
    valid: bool
    reason: str


def fetch_issue_labels(repo: str, issue: int) -> list[str]:
    """Read current labels for an issue using gh issue view (read-only)."""
    result = subprocess.run(
        [
            "gh", "issue", "view", str(issue),
            "-R", repo,
            "--json", "labels"
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch issue #{issue}: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    labels = [lbl["name"] for lbl in data.get("labels", [])]
    return labels


def _extract_state(labels: list[str]) -> str | None:
    for label in labels:
        if label.startswith("state:"):
            return label.split(":", 1)[1]
    return None


def _has_gate(labels: list[str]) -> bool:
    return any(label.startswith("gate:") for label in labels)


def plan_release(labels: list[str], issue_number: int) -> TransitionPlan:
    """Plan a release transition (active → ready)."""
    current_state = _extract_state(labels)

    if current_state != "active":
        return TransitionPlan(
            issue_number=issue_number,
            current_labels=labels,
            labels_to_remove=[],
            labels_to_add=[],
            new_state=current_state or "unknown",
            action="release",
            valid=False,
            reason=f"Item is not in state:active (current: {current_state})",
        )

    labels_to_remove = ["state:active"]
    labels_to_add = ["state:ready"]

    # Remove any gate:* labels
    gate_labels = [lbl for lbl in labels if lbl.startswith("gate:")]
    labels_to_remove.extend(gate_labels)

    return TransitionPlan(
        issue_number=issue_number,
        current_labels=labels,
        labels_to_remove=labels_to_remove,
        labels_to_add=labels_to_add,
        new_state="ready",
        action="release",
        valid=True,
        reason="Releasing active item back to ready state",
    )


def plan_complete(labels: list[str], issue_number: int) -> TransitionPlan:
    """Plan a complete transition (active → done)."""
    current_state = _extract_state(labels)

    if current_state != "active":
        return TransitionPlan(
            issue_number=issue_number,
            current_labels=labels,
            labels_to_remove=[],
            labels_to_add=[],
            new_state=current_state or "unknown",
            action="complete",
            valid=False,
            reason=f"Item is not in state:active (current: {current_state})",
        )

    labels_to_remove = ["state:active"]
    labels_to_add = ["state:done"]

    gate_labels = [lbl for lbl in labels if lbl.startswith("gate:")]
    labels_to_remove.extend(gate_labels)

    return TransitionPlan(
        issue_number=issue_number,
        current_labels=labels,
        labels_to_remove=labels_to_remove,
        labels_to_add=labels_to_add,
        new_state="done",
        action="complete",
        valid=True,
        reason="Marking item as successfully completed",
    )


def plan_fail(labels: list[str], issue_number: int) -> TransitionPlan:
    """Plan a fail transition (active → failed)."""
    current_state = _extract_state(labels)

    if current_state != "active":
        return TransitionPlan(
            issue_number=issue_number,
            current_labels=labels,
            labels_to_remove=[],
            labels_to_add=[],
            new_state=current_state or "unknown",
            action="fail",
            valid=False,
            reason=f"Item is not in state:active (current: {current_state})",
        )

    labels_to_remove = ["state:active"]
    labels_to_add = ["state:failed"]

    gate_labels = [lbl for lbl in labels if lbl.startswith("gate:")]
    labels_to_remove.extend(gate_labels)

    return TransitionPlan(
        issue_number=issue_number,
        current_labels=labels,
        labels_to_remove=labels_to_remove,
        labels_to_add=labels_to_add,
        new_state="failed",
        action="fail",
        valid=True,
        reason="Marking item as failed",
    )


def format_transition_plan(plan: TransitionPlan) -> str:
    """Produce a human-readable dry-run report for a transition."""
    lines = [f"Signposter {plan.action.capitalize()} Dry-Run — Issue #{plan.issue_number}\n"]

    if not plan.valid:
        lines.append(f"❌ Invalid transition: {plan.reason}")
        return "\n".join(lines)

    lines.append("Current state: active")
    lines.append(f"Planned new state: {plan.new_state}")
    lines.append("")
    lines.append("Label changes:")

    if plan.labels_to_remove:
        lines.append(f"  Remove: {', '.join(plan.labels_to_remove)}")
    if plan.labels_to_add:
        lines.append(f"  Add:    {', '.join(plan.labels_to_add)}")

    lines.append("")
    lines.append(f"Reason: {plan.reason}")
    lines.append("\nNote: This is a DRY RUN. No changes were made to GitHub.")

    return "\n".join(lines)


def run_transition_dry_run(repo: str, issue: int, action: str) -> TransitionPlan:
    """High-level entry point for dry-run transitions."""
    labels = fetch_issue_labels(repo, issue)

    if action == "release":
        plan = plan_release(labels, issue)
    elif action == "complete":
        plan = plan_complete(labels, issue)
    elif action == "fail":
        plan = plan_fail(labels, issue)
    else:
        raise ValueError(f"Unknown action: {action}")

    return plan


def _get_transition_comment(action: str, new_state: str) -> str:
    """Return the exact comment text for a given transition action."""
    if action == "release":
        return "Signposter released this item: state=ready."
    elif action == "complete":
        return "Signposter completed this item: state=done."
    elif action == "fail":
        return "Signposter marked this item as failed: state=failed."
    else:
        return f"Signposter performed transition to {new_state}."


def perform_transition_mutation(
    plan: TransitionPlan, repo: str, *, dry_run: bool = True
) -> list[str]:
    """Execute (or simulate) the label mutation and comment for a transition plan.

    Returns the list of gh commands that were (or would be) executed.
    """
    if not plan.valid:
        raise ValueError(f"Cannot apply invalid transition plan: {plan.reason}")

    commands: list[str] = []
    issue_num = str(plan.issue_number)

    # Build label edit command
    add_labels = ",".join(plan.labels_to_add)
    remove_labels = ",".join(plan.labels_to_remove)

    edit_cmd = [
        "gh", "issue", "edit", issue_num,
        "-R", repo,
        "--add-label", add_labels,
        "--remove-label", remove_labels,
    ]
    commands.append(" ".join(edit_cmd))

    if not dry_run:
        subprocess.run(edit_cmd, check=True, capture_output=True, text=True)

    # Build comment command
    comment_body = _get_transition_comment(plan.action, plan.new_state)
    comment_cmd = [
        "gh", "issue", "comment", issue_num,
        "-R", repo,
        "--body", comment_body,
    ]
    commands.append(" ".join(comment_cmd))

    if not dry_run:
        subprocess.run(comment_cmd, check=True, capture_output=True, text=True)

    return commands
