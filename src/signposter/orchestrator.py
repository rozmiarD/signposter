"""Minimal deterministic orchestrator planning surface.

This module intentionally does not execute lifecycle commands.  It wraps the
existing lifecycle-next state machine and adds orchestration-oriented stop
metadata that a future bounded loop can consume.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass

from signposter.lifecycle import LifecycleNext, plan_lifecycle_next

EXECUTION_REQUIRED_ACTIONS = {"execute-worker"}
MUTATION_REQUIRED_ACTIONS = {
    "labels-ensure",
    "sync-rebase",
    "create-worktree",
    "claim-issue",
    "write-prompt",
    "create-pr",
    "review-pr",
    "merge-pr",
    "integrate-issue",
    "cleanup",
}
APPLYABLE_ACTIONS = MUTATION_REQUIRED_ACTIONS | EXECUTION_REQUIRED_ACTIONS
SIGNPOSTER_ENTRYPOINT = (
    "import sys; "
    "from signposter.cli import main; "
    "sys.argv = ['signposter'] + sys.argv[1:]; "
    "main()"
)


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


@dataclass(frozen=True)
class OrchestratorStep:
    """Result of one bounded orchestrator step."""

    next: OrchestratorNext
    status: str
    applied: bool
    exit_code: int | None
    stdout: str
    stderr: str
    stop_reason: str | None
    notes: list[str]


@dataclass(frozen=True)
class OrchestratorLoop:
    """Result of a bounded orchestrator loop."""

    status: str
    cycles_requested: int
    cycles_run: int
    steps: list[OrchestratorStep]
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


def run_orchestrator_step(
    repo: str,
    *,
    issue: int | None = None,
    pr: int | None = None,
    apply: bool = False,
    execute: bool = False,
    run_command=subprocess.run,
) -> OrchestratorStep:
    """Run at most one allow-listed lifecycle command."""
    planned = plan_orchestrator_next(
        repo,
        issue=issue,
        pr=pr,
        allow_execute=execute,
    )
    notes = [
        "One-cycle orchestrator step.",
        "No command is executed unless --apply is present.",
    ]

    if planned.status == "complete":
        return OrchestratorStep(
            next=planned,
            status="complete",
            applied=False,
            exit_code=None,
            stdout="",
            stderr="",
            stop_reason="lifecycle already complete",
            notes=notes,
        )

    if planned.status != "actionable":
        return OrchestratorStep(
            next=planned,
            status="blocked",
            applied=False,
            exit_code=None,
            stdout="",
            stderr="",
            stop_reason=planned.stop_reason or "next action is not actionable",
            notes=notes,
        )

    if planned.action not in APPLYABLE_ACTIONS:
        return OrchestratorStep(
            next=planned,
            status="blocked",
            applied=False,
            exit_code=None,
            stdout="",
            stderr="",
            stop_reason=f"action is not allow-listed for orchestrator step: {planned.action}",
            notes=notes,
        )

    if planned.would_execute and not execute:
        return OrchestratorStep(
            next=planned,
            status="blocked",
            applied=False,
            exit_code=None,
            stdout="",
            stderr="",
            stop_reason="OpenClaw execution requires explicit --execute",
            notes=notes,
        )

    if not apply:
        return OrchestratorStep(
            next=planned,
            status="ready",
            applied=False,
            exit_code=None,
            stdout="",
            stderr="",
            stop_reason="dry-run; rerun with --apply to execute this step",
            notes=notes,
        )

    command = _normalized_command(planned.command)
    proc = run_command(
        command,
        capture_output=True,
        text=True,
        timeout=300,
    )
    status = "applied" if proc.returncode == 0 else "failed"
    stop_reason = None if proc.returncode == 0 else "step command failed"
    return OrchestratorStep(
        next=planned,
        status=status,
        applied=proc.returncode == 0,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        stop_reason=stop_reason,
        notes=notes,
    )


def run_orchestrator_loop(
    repo: str,
    *,
    issue: int | None = None,
    pr: int | None = None,
    max_cycles: int = 1,
    apply: bool = False,
    execute: bool = False,
    run_command=subprocess.run,
) -> OrchestratorLoop:
    """Run a bounded orchestrator loop and stop on any non-applied state."""
    if max_cycles < 1:
        max_cycles = 1

    steps: list[OrchestratorStep] = []
    stop_reason: str | None = None

    for _ in range(max_cycles):
        step = run_orchestrator_step(
            repo,
            issue=issue,
            pr=pr,
            apply=apply,
            execute=execute,
            run_command=run_command,
        )
        steps.append(step)

        if step.status != "applied":
            stop_reason = step.stop_reason or step.status
            break

    status = "completed" if steps and steps[-1].status == "complete" else "stopped"
    if steps and len(steps) == max_cycles and steps[-1].status == "applied":
        status = "limit-reached"
        stop_reason = "max cycles reached"

    return OrchestratorLoop(
        status=status,
        cycles_requested=max_cycles,
        cycles_run=len(steps),
        steps=steps,
        stop_reason=stop_reason,
        notes=[
            "Bounded orchestrator loop.",
            "Stops after any blocked, failed, complete, or dry-run step.",
        ],
    )


def plan_orchestrator_tail(
    repo: str,
    *,
    pr: int,
    allow_execute: bool = False,
) -> OrchestratorNext:
    """Plan the next PR-tail lifecycle action for an open or merged PR."""
    return plan_orchestrator_next(repo, pr=pr, allow_execute=allow_execute)


def _normalized_command(command: str) -> list[str]:
    args = shlex.split(command)
    if not args:
        raise RuntimeError("empty orchestrator command")
    if args[0] == "signposter":
        return [sys.executable, "-c", SIGNPOSTER_ENTRYPOINT, *args[1:]]
    if args[0] == "git" and args[1:] == ["status", "--short", "--branch"]:
        return args
    raise RuntimeError(f"refusing unsupported orchestrator command: {command}")


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


def format_orchestrator_step(result: OrchestratorStep) -> str:
    """Render one orchestrator step result."""
    lines = [
        "Signposter Orchestrator Step",
        "",
        "Next:",
        f"  action: {result.next.action}",
        f"  command: {result.next.command}",
        f"  would mutate: {'yes' if result.next.would_mutate else 'no'}",
        f"  would execute OpenClaw: {'yes' if result.next.would_execute else 'no'}",
        "",
        "Execution:",
        f"  applied: {'yes' if result.applied else 'no'}",
        f"  exit code: {result.exit_code if result.exit_code is not None else 'n/a'}",
    ]
    if result.stop_reason:
        lines.extend(["", "Stop:", f"  {result.stop_reason}"])
    lines.extend(["", "Status:", f"  {result.status}"])
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def format_orchestrator_loop(result: OrchestratorLoop) -> str:
    """Render bounded orchestrator loop output."""
    lines = [
        "Signposter Orchestrator Loop",
        "",
        "Cycles:",
        f"  requested: {result.cycles_requested}",
        f"  run: {result.cycles_run}",
        "",
        "Steps:",
    ]
    for index, step in enumerate(result.steps, start=1):
        lines.append(
            f"  {index}. {step.next.action} -> {step.status}"
            + (f" ({step.stop_reason})" if step.stop_reason else "")
        )
    if not result.steps:
        lines.append("  none")
    if result.stop_reason:
        lines.extend(["", "Stop:", f"  {result.stop_reason}"])
    lines.extend(["", "Status:", f"  {result.status}"])
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)
