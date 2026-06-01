"""Minimal deterministic orchestrator planning surface.

This module intentionally does not execute lifecycle commands.  It wraps the
existing lifecycle-next state machine and adds orchestration-oriented stop
metadata that a future bounded loop can consume.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from signposter.lifecycle import LifecycleNext, plan_lifecycle_next
from signposter.scan import LabeledItem, fetch_open_issues
from signposter.scheduler import SchedulerNext, select_next_issue

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
    diagnosis_status: str | None = None
    diagnosis_reason: str | None = None
    raw_artifact_path: str | None = None
    summary_artifact_path: str | None = None
    fallback_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrchestratorLoop:
    """Result of a bounded orchestrator loop."""

    status: str
    cycles_requested: int
    cycles_run: int
    steps: list[OrchestratorStep]
    stop_reason: str | None
    notes: list[str]


@dataclass(frozen=True)
class OrchestratorRunNext:
    """Scheduler-selected next issue plus lifecycle action plan."""

    scheduler: SchedulerNext
    next: OrchestratorNext | None
    step: OrchestratorStep | None
    status: str
    notes: list[str]


@dataclass(frozen=True)
class OrchestratorRunNextLoop:
    """Result of a scheduler-driven bounded orchestrator loop."""

    status: str
    cycles_requested: int
    cycles_run: int
    max_tasks: int
    tasks_started: int
    selected_issue: int | None
    steps: list[OrchestratorStep]
    stop_reason: str | None
    notes: list[str]
    stop_category: str | None = None
    stop_tolerated: bool = False


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
    diagnosis_status, diagnosis_reason, raw_artifact_path, summary_artifact_path = (
        _extract_execute_diagnosis(proc.stdout, proc.stderr)
    )
    fallback_commands = _plan_fallback_commands(
        repo=repo,
        planned=planned,
        diagnosis_status=diagnosis_status,
    )
    return OrchestratorStep(
        next=planned,
        status=status,
        applied=proc.returncode == 0,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        stop_reason=stop_reason,
        notes=notes,
        diagnosis_status=diagnosis_status,
        diagnosis_reason=diagnosis_reason,
        raw_artifact_path=raw_artifact_path,
        summary_artifact_path=summary_artifact_path,
        fallback_commands=fallback_commands,
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


def plan_orchestrator_run_next(
    repo: str,
    *,
    limit: int = 50,
    allow_execute: bool = False,
) -> OrchestratorRunNext:
    """Select the next scheduler issue and plan its lifecycle action."""
    scheduler = select_next_issue(repo, limit=limit)
    planned: OrchestratorNext | None = None
    status = scheduler.status

    if scheduler.issue is not None:
        planned = plan_orchestrator_next(
            repo,
            issue=scheduler.issue.number,
            allow_execute=allow_execute,
        )
        status = planned.status

    return OrchestratorRunNext(
        scheduler=scheduler,
        next=planned,
        step=None,
        status=status,
        notes=[
            "Read-only run-next planning.",
            "No lifecycle command was executed.",
            "No GitHub mutation was performed.",
            "No local mutation was performed.",
            "No OpenClaw execution was performed.",
        ],
    )


def run_orchestrator_run_next(
    repo: str,
    *,
    limit: int = 50,
    apply: bool = False,
    execute: bool = False,
    run_command=subprocess.run,
) -> OrchestratorRunNext:
    """Select the next scheduler issue and optionally run one lifecycle step."""
    planned = plan_orchestrator_run_next(repo, limit=limit, allow_execute=execute)
    if planned.scheduler.issue is None or not apply:
        return planned

    step = run_orchestrator_step(
        repo,
        issue=planned.scheduler.issue.number,
        apply=True,
        execute=execute,
        run_command=run_command,
    )
    return OrchestratorRunNext(
        scheduler=planned.scheduler,
        next=step.next,
        step=step,
        status=step.status,
        notes=[
            "Run-next one-step execution.",
            "No command is executed unless --apply is present.",
        ],
    )


def run_orchestrator_run_next_loop(
    repo: str,
    *,
    limit: int = 50,
    max_cycles: int = 1,
    max_tasks: int = 1,
    apply: bool = False,
    execute: bool = False,
    tolerate_no_ready: bool = False,
    tolerate_active_ambiguity: bool = False,
    tolerate_blocked_lifecycle: bool = False,
    tolerate_failed_step: bool = False,
    run_command=subprocess.run,
) -> OrchestratorRunNextLoop:
    """Run scheduler-selected lifecycle work with hard task and cycle limits."""
    if max_cycles < 1:
        max_cycles = 1
    if max_tasks < 1:
        max_tasks = 1

    steps: list[OrchestratorStep] = []
    selected_issue: int | None = None
    tasks_started = 0
    stop_reason: str | None = None

    for _ in range(max_cycles):
        if selected_issue is None:
            selected_issue, stop_reason = _select_run_next_loop_issue(repo, limit=limit)
            if selected_issue is None:
                break
            tasks_started += 1
            if tasks_started > max_tasks:
                selected_issue = None
                stop_reason = "max tasks reached"
                break

        step = run_orchestrator_step(
            repo,
            issue=selected_issue,
            apply=apply,
            execute=execute,
            run_command=run_command,
        )
        steps.append(step)

        if step.status == "complete":
            selected_issue = None
            continue
        if step.status != "applied":
            stop_reason = step.stop_reason or step.status
            break

    status = "stopped"
    if not steps and stop_reason is None:
        status = "completed"
        stop_reason = "no ready or resumable active issue found"
    elif steps and len(steps) == max_cycles and steps[-1].status == "applied":
        status = "limit-reached"
        stop_reason = "max cycles reached"
    elif stop_reason == "max tasks reached":
        status = "limit-reached"

    stop_category = _run_next_loop_stop_category(steps, stop_reason)
    tolerated_categories = set()
    if tolerate_no_ready:
        tolerated_categories.add("no-ready")
    if tolerate_active_ambiguity:
        tolerated_categories.add("active-ambiguity")
    if tolerate_blocked_lifecycle:
        tolerated_categories.add("blocked-lifecycle")
    if tolerate_failed_step:
        tolerated_categories.add("failed-step")
    stop_tolerated = stop_category in tolerated_categories
    if status == "stopped" and stop_tolerated:
        status = "completed"

    return OrchestratorRunNextLoop(
        status=status,
        cycles_requested=max_cycles,
        cycles_run=len(steps),
        max_tasks=max_tasks,
        tasks_started=tasks_started,
        selected_issue=selected_issue,
        steps=steps,
        stop_reason=stop_reason,
        notes=[
            "Scheduler-driven bounded run-next loop.",
            "Default mode is dry-run; use --apply to execute allow-listed steps.",
            "OpenClaw execution still requires explicit --execute.",
        ],
        stop_category=stop_category,
        stop_tolerated=stop_tolerated,
    )


def _run_next_loop_stop_category(
    steps: list[OrchestratorStep],
    stop_reason: str | None,
) -> str | None:
    if stop_reason and stop_reason.startswith("multiple active issues require explicit --issue"):
        return "active-ambiguity"
    if not steps:
        return "no-ready"
    last_step = steps[-1]
    if last_step.status == "failed" or stop_reason == "step command failed":
        return "failed-step"
    if last_step.status == "blocked" or stop_reason in {
        "dry-run; rerun with --apply to execute this step",
        "OpenClaw execution requires explicit --execute",
    }:
        return "blocked-lifecycle"
    return None


def _select_run_next_loop_issue(repo: str, *, limit: int) -> tuple[int | None, str | None]:
    scheduler = select_next_issue(repo, limit=limit)
    if scheduler.issue is not None:
        return scheduler.issue.number, None

    open_issues = fetch_open_issues(repo, limit=limit)
    active = [issue for issue in open_issues if _issue_state(issue) == "active"]
    if len(active) == 1:
        return active[0].number, None
    if len(active) > 1:
        numbers = ", ".join(
            f"#{issue.number}" for issue in sorted(active, key=lambda item: item.number)
        )
        return None, f"multiple active issues require explicit --issue: {numbers}"

    resumable_done = [
        issue
        for issue in open_issues
        if _issue_state(issue) == "done"
        and plan_lifecycle_next(repo, issue=issue.number).status in {"actionable", "complete"}
    ]
    if len(resumable_done) == 1:
        return resumable_done[0].number, None
    if len(resumable_done) > 1:
        numbers = ", ".join(
            f"#{issue.number}" for issue in sorted(resumable_done, key=lambda item: item.number)
        )
        return None, f"multiple resumable done issues require explicit --issue: {numbers}"
    return None, scheduler.reason


def _issue_state(issue: LabeledItem) -> str | None:
    for label in issue.labels:
        if label.startswith("state:"):
            return label.split(":", 1)[1]
    return None


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
    if result.diagnosis_status or result.raw_artifact_path or result.summary_artifact_path:
        lines.extend(["", "Diagnosis:"])
        lines.append(f"  status: {result.diagnosis_status or 'unknown'}")
        if result.diagnosis_reason:
            lines.append(f"  reason: {result.diagnosis_reason}")
        if result.raw_artifact_path:
            lines.append(f"  raw artifact: {result.raw_artifact_path}")
        if result.summary_artifact_path:
            lines.append(f"  summary artifact: {result.summary_artifact_path}")
    if result.fallback_commands:
        lines.extend(["", "Fallback next commands:"])
        lines.extend(f"  {command}" for command in result.fallback_commands)
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


def format_orchestrator_loop_summary(result: OrchestratorLoop) -> str:
    """Render compact bounded loop output for automation logs."""
    last_step = result.steps[-1] if result.steps else None
    target = "none"
    action = "none"
    if last_step:
        if last_step.next.lifecycle.pr_number:
            target = f"pr #{last_step.next.lifecycle.pr_number}"
        elif last_step.next.lifecycle.issue_number:
            target = f"issue #{last_step.next.lifecycle.issue_number}"
        action = last_step.next.action

    lines = [
        "Signposter Orchestrator Loop Summary",
        f"target: {target}",
        f"action: {action}",
        f"status: {result.status}",
        f"stop: {result.stop_reason or 'none'}",
        f"steps: {result.cycles_run}",
    ]
    return "\n".join(lines)


def format_orchestrator_run_next(result: OrchestratorRunNext) -> str:
    """Render scheduler-selected next lifecycle plan."""
    lines = [
        "Signposter Orchestrator Run Next",
        "",
        "Scheduler:",
    ]
    if result.scheduler.issue:
        issue = result.scheduler.issue
        lines.extend(
            [
                f"  selected: #{issue.number} — {issue.title}",
                f"  reason: {result.scheduler.reason}",
            ]
        )
    else:
        lines.extend(["  selected: none", f"  reason: {result.scheduler.reason}"])

    lines.extend(["", "Lifecycle:"])
    if result.next:
        lines.extend(
            [
                f"  action: {result.next.action}",
                f"  command: {result.next.command}",
                f"  status: {result.next.status}",
            ]
        )
        if result.next.stop_reason:
            lines.append(f"  stop: {result.next.stop_reason}")
    else:
        lines.append("  none")

    if result.step:
        lines.extend(
            [
                "",
                "Step:",
                f"  applied: {'yes' if result.step.applied else 'no'}",
                f"  status: {result.step.status}",
            ]
        )
        if result.step.stop_reason:
            lines.append(f"  stop: {result.step.stop_reason}")
        if result.step.fallback_commands:
            lines.append("  fallback next commands:")
            lines.extend(f"    - {command}" for command in result.step.fallback_commands)

    lines.extend(["", "Status:", f"  {result.status}"])
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def format_orchestrator_run_next_summary(result: OrchestratorRunNext) -> str:
    """Render compact run-next output for automation loops."""
    issue = f"#{result.scheduler.issue.number}" if result.scheduler.issue else "none"
    action = result.next.action if result.next else "none"
    stop = "none"
    if result.step and result.step.stop_reason:
        stop = result.step.stop_reason
    elif result.next and result.next.stop_reason:
        stop = result.next.stop_reason

    lines = [
        "Signposter Automation Summary",
        f"selected: {issue}",
        f"action: {action}",
        f"status: {result.status}",
        f"stop: {stop}",
    ]
    return "\n".join(lines)


def format_orchestrator_run_next_loop(result: OrchestratorRunNextLoop) -> str:
    """Render scheduler-driven bounded loop output."""
    lines = [
        "Signposter Orchestrator Run Next Loop",
        "",
        "Limits:",
        f"  cycles requested: {result.cycles_requested}",
        f"  cycles run: {result.cycles_run}",
        f"  max tasks: {result.max_tasks}",
        f"  tasks started: {result.tasks_started}",
        "",
        "Selection:",
        (
            f"  current issue: #{result.selected_issue}"
            if result.selected_issue
            else "  current issue: none"
        ),
        "",
        "Steps:",
    ]
    if result.steps:
        for index, step in enumerate(result.steps, start=1):
            issue = step.next.lifecycle.issue_number
            target = f"issue #{issue}" if issue else "unknown target"
            lines.append(
                f"  {index}. {target}: {step.next.action} -> {step.status}"
                + (f" ({step.stop_reason})" if step.stop_reason else "")
            )
            for command in step.fallback_commands:
                lines.append(f"     fallback: {command}")
    else:
        lines.append("  none")

    if result.stop_reason:
        lines.extend(["", "Stop:", f"  {result.stop_reason}"])
    if result.stop_category:
        lines.extend(
            [
                "",
                "Stop policy:",
                f"  category: {result.stop_category}",
                f"  tolerated: {'yes' if result.stop_tolerated else 'no'}",
            ]
        )
    lines.extend(["", "Status:", f"  {result.status}"])
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def format_orchestrator_run_next_loop_summary(result: OrchestratorRunNextLoop) -> str:
    """Render compact run-next-loop output for automation loops."""
    issue = result.selected_issue
    if issue is None and result.steps:
        issue = result.steps[-1].next.lifecycle.issue_number
    selected = f"#{issue}" if issue else "none"
    action = result.steps[-1].next.action if result.steps else "none"
    stop = result.stop_reason or "none"

    lines = [
        "Signposter Automation Summary",
        f"selected: {selected}",
        f"action: {action}",
        f"status: {result.status}",
        f"stop: {stop}",
        f"stop_category: {result.stop_category or 'none'}",
        f"stop_tolerated: {'yes' if result.stop_tolerated else 'no'}",
        f"steps: {result.cycles_run}",
    ]
    return "\n".join(lines)


def write_orchestrator_run_next_loop_transcript(
    result: OrchestratorRunNextLoop,
    path: str | Path,
) -> Path:
    """Write a bounded local transcript for a run-next-loop result."""
    transcript_path = Path(path)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        format_orchestrator_run_next_loop_summary(result),
        "",
        "Steps:",
    ]
    if result.steps:
        for index, step in enumerate(result.steps, start=1):
            issue = step.next.lifecycle.issue_number
            selected = f"#{issue}" if issue is not None else "unknown"
            lines.append(
                f"{index}. selected={selected} action={step.next.action} "
                f"status={step.status} stop={step.stop_reason or 'none'}"
            )
            if step.diagnosis_status or step.raw_artifact_path or step.summary_artifact_path:
                if step.diagnosis_status:
                    lines.append(f"   diagnosis_status={step.diagnosis_status}")
                if step.diagnosis_reason:
                    lines.append(f"   diagnosis_reason={step.diagnosis_reason}")
                if step.raw_artifact_path:
                    lines.append(f"   raw_artifact={step.raw_artifact_path}")
                if step.summary_artifact_path:
                    lines.append(f"   summary_artifact={step.summary_artifact_path}")
            for command in step.fallback_commands:
                lines.append(f"   fallback_command={command}")
    else:
        lines.append("none")
    lines.extend(
        [
            "",
            "Notes:",
            "local artifact only",
            "no GitHub mutation was performed by transcript writing",
        ]
    )

    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript_path


_ARTIFACT_PATTERNS = (
    re.compile(r"Raw output:\s*(?P<path>\S+)"),
    re.compile(r"raw artifact:\s*(?P<path>\S+)"),
    re.compile(r"Summary:\s*(?P<path>\S+)"),
    re.compile(r"summary artifact:\s*(?P<path>\S+)"),
)
_DIAGNOSIS_STATUS_RE = re.compile(r"\*\*Execution Status:\*\*\s*(?P<value>[^\n]+)")
_DIAGNOSIS_REASON_RE = re.compile(r"\*\*Execution Reason:\*\*\s*(?P<value>[^\n]+)")


def _extract_execute_diagnosis(
    stdout: str,
    stderr: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    raw_path: str | None = None
    summary_path: str | None = None
    for line in combined.splitlines():
        for pattern in _ARTIFACT_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            path = match.group("path")
            if "summary" in pattern.pattern.lower():
                summary_path = path
            else:
                raw_path = path

    diagnosis_status: str | None = None
    diagnosis_reason: str | None = None
    if summary_path:
        summary_file = Path(summary_path)
        if summary_file.is_file():
            text = summary_file.read_text(encoding="utf-8")
            status_match = _DIAGNOSIS_STATUS_RE.search(text)
            reason_match = _DIAGNOSIS_REASON_RE.search(text)
            if status_match:
                diagnosis_status = status_match.group("value").strip()
            if reason_match:
                diagnosis_reason = reason_match.group("value").strip()

    return diagnosis_status, diagnosis_reason, raw_path, summary_path


_FALLBACK_ELIGIBLE_DIAGNOSES = {
    "timeout",
    "auth-runtime-failure",
    "unsupported-model",
    "runtime-stall",
    "config-drift",
}


def _plan_fallback_commands(
    *,
    repo: str,
    planned: OrchestratorNext,
    diagnosis_status: str | None,
) -> tuple[str, ...]:
    if diagnosis_status not in _FALLBACK_ELIGIBLE_DIAGNOSES:
        return ()

    lifecycle = planned.lifecycle
    if planned.action == "execute-worker" and lifecycle.issue_number is not None:
        issue = lifecycle.issue_number
        return (
            f"signposter artifact write-worker-summary --repo {repo} --issue {issue} --apply",
            f"signposter artifact validate-worker-summary --issue {issue}",
            f"signposter report --repo {repo} --issue {issue} --apply",
            f"signposter gate --repo {repo} --issue {issue}",
        )

    if planned.action == "review-pr" and lifecycle.pr_number is not None:
        pr = lifecycle.pr_number
        return (
            f"signposter artifact write-review-summary --pr {pr} --apply",
            f"signposter review validate-artifact --pr {pr}",
            f"signposter review gate --repo {repo} --pr {pr}",
        )

    return ()
