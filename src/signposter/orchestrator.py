"""Minimal deterministic orchestrator planning surface.

This module intentionally does not execute lifecycle commands.  It wraps the
existing lifecycle-next state machine and adds orchestration-oriented stop
metadata that a future bounded loop can consume.
"""

from __future__ import annotations

from dataclasses import dataclass

from signposter.lifecycle import LifecycleNext, plan_lifecycle_next

EXECUTION_REQUIRED_ACTIONS = {"execute-worker"}
MUTATION_REQUIRED_ACTIONS = {
    "labels-ensure",
    "sync-rebase",
    "create-worktree",
    "claim-issue",
    "create-pr",
    "review-pr",
    "merge-pr",
    "integrate-issue",
    "cleanup",
}


@dataclass(frozen=True)
class OrchestratorNext:
    """Read-only next-step decision for a future orchestrator loop."""

    lifecycle: LifecycleNext
    status: str
    action: str
    command: str
    would_execute: bool
    would_mutate: bool
    stop_reason: str | None
    notes: list[str]


def plan_orchestrator_next(
    repo: str,
    *,
    issue: int | None = None,
    pr: int | None = None,
    allow_execute: bool = False,
) -> OrchestratorNext:
    """Plan the next orchestrator step without performing it."""
    lifecycle = plan_lifecycle_next(repo, issue=issue, pr=pr)
    action = lifecycle.action

    would_execute = action in EXECUTION_REQUIRED_ACTIONS
    would_mutate = action in MUTATION_REQUIRED_ACTIONS
    stop_reason: str | None = None
    status = lifecycle.status

    if would_execute and not allow_execute:
        status = "blocked"
        stop_reason = "OpenClaw execution requires explicit --execute"
    elif lifecycle.status == "blocked":
        stop_reason = lifecycle.reason

    notes = [
        "Read-only orchestrator planning only.",
        "No lifecycle command was executed.",
        "No GitHub mutation was performed.",
        "No local mutation was performed.",
        "No OpenClaw execution was performed.",
    ]

    if would_execute and not allow_execute:
        notes.append("Use a worker artifact fallback or rerun with --execute in a future executor.")

    return OrchestratorNext(
        lifecycle=lifecycle,
        status=status,
        action=action,
        command=lifecycle.command,
        would_execute=would_execute,
        would_mutate=would_mutate,
        stop_reason=stop_reason,
        notes=notes,
    )


def format_orchestrator_next(result: OrchestratorNext) -> str:
    """Render compact operator-friendly orchestrator next-step output."""
    lifecycle = result.lifecycle
    if lifecycle.issue_number:
        header = f"Signposter Orchestrator Next — Issue #{lifecycle.issue_number}"
    else:
        header = f"Signposter Orchestrator Next — PR #{lifecycle.pr_number}"

    lines = [
        header,
        "",
        "Current:",
        f"  issue state: {lifecycle.issue_state or 'unknown'}",
        f"  workflow state: {lifecycle.workflow_state or 'unknown'}",
        f"  pr: #{lifecycle.pr_number}" if lifecycle.pr_number else "  pr: none detected",
        f"  worktree: {'present' if lifecycle.worktree_exists else 'missing'}",
        f"  local branch: {'present' if lifecycle.local_branch_exists else 'missing'}",
        f"  prompt: {'present' if lifecycle.prompt_exists else 'missing'}",
        f"  worker summary: {'present' if lifecycle.worker_summary_exists else 'missing'}",
        "",
        "Next:",
        f"  action: {result.action}",
        f"  command: {result.command}",
        f"  would mutate: {'yes' if result.would_mutate else 'no'}",
        f"  would execute OpenClaw: {'yes' if result.would_execute else 'no'}",
    ]

    if result.stop_reason:
        lines.extend(["", "Stop:", f"  {result.stop_reason}"])

    lines.extend(["", "Status:", f"  {result.status}"])
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)
