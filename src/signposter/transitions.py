"""Signposter release / complete / fail transitions (dry-run only in bootstrap).

Handles state transitions for already-claimed (state:active) items.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from signposter.comments import (
    format_complete_comment,
    format_fail_comment,
    format_release_comment,
)
from signposter.labels import check_labels


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


def format_transition_plan(plan: TransitionPlan, *, dry_run: bool = True) -> str:
    """Produce a human-readable plan report for a transition.

    When dry_run=True (default): header says "Dry-Run" and includes a clear warning.
    When dry_run=False (apply mode): header says "Plan" and the warning is milder.
    """
    mode_word = "Dry-Run" if dry_run else "Plan"
    lines = [f"Signposter {plan.action.capitalize()} {mode_word} — Issue #{plan.issue_number}\n"]

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

    if dry_run:
        lines.append("\nNote: This is a DRY RUN. No changes were made to GitHub.")
    else:
        lines.append("\nNote: This plan will be applied to GitHub (labels + comment).")

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


def perform_transition_mutation(
    plan: TransitionPlan, repo: str, *, dry_run: bool = True
) -> list[str]:
    """Execute (or simulate) the label mutation and comment for a transition plan.

    Returns the list of gh commands that were (or would be) executed.
    """
    if not plan.valid:
        raise ValueError(f"Cannot apply invalid transition plan: {plan.reason}")

    # H023D-B: Centralized label preflight before any mutation
    if not dry_run:
        ok, missing, err = _required_label_preflight(repo)
        if not ok:
            reason = err or ("required labels missing: " + ", ".join(missing))
            raise RuntimeError(f"Label preflight failed — {reason}")

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

    # Build comment command using plan data for gate removal awareness
    if plan.action == "release":
        comment_body = format_release_comment()
    elif plan.action == "complete":
        comment_body = format_complete_comment()
    elif plan.action == "fail":
        removed_gates = any(lbl.startswith("gate:") for lbl in plan.labels_to_remove)
        comment_body = format_fail_comment(removed_gates=removed_gates)
    else:
        comment_body = f"**Signposter:** performed transition to `{plan.new_state}`."

    comment_cmd = [
        "gh", "issue", "comment", issue_num,
        "-R", repo,
        "--body", comment_body,
    ]
    commands.append(" ".join(comment_cmd))

    if not dry_run:
        subprocess.run(comment_cmd, check=True, capture_output=True, text=True)

    return commands


def _required_label_preflight(repo: str) -> tuple[bool, list[str], str | None]:
    """Centralized required-label preflight for release/complete/fail (H023D-B)."""
    try:
        result = check_labels(repo)
        if result.error:
            return False, [], f"label preflight failed: {result.error}"
        if result.missing:
            return False, result.missing, None
        return True, [], None
    except Exception as e:
        return False, [], f"label preflight error: {str(e)[:200]}"
