"""Compact read-only control-plane status view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from signposter.bug_ledger import BugLedgerEntry
from signposter.orchestrator import OrchestratorNext
from signposter.scheduler import SchedulerNext


@dataclass(frozen=True)
class ControlPlaneStatus:
    repo: str
    status: str
    planner: dict[str, Any] | None
    scheduler: SchedulerNext | None
    orchestrator: OrchestratorNext | None
    agreement: dict[str, Any]
    refresh: dict[str, Any]
    bugs: tuple[BugLedgerEntry, ...]
    notes: tuple[str, ...]


def build_control_plane_status(
    *,
    repo: str,
    planner_run: dict[str, Any] | None = None,
    scheduler_next: SchedulerNext | None = None,
    orchestrator_next: OrchestratorNext | None = None,
    refresh_command: str | None = None,
    bugs: tuple[BugLedgerEntry, ...] = (),
) -> ControlPlaneStatus:
    """Build a bounded status object from existing source-of-truth results."""
    statuses = [
        str(planner_run.get("planner_status", "unknown"))
        for planner_run in [planner_run]
        if planner_run is not None
    ]
    if scheduler_next is not None:
        statuses.append(scheduler_next.status)
    if orchestrator_next is not None:
        statuses.append(orchestrator_next.status)
    agreement = _build_agreement(
        planner_run=planner_run,
        scheduler_next=scheduler_next,
        orchestrator_next=orchestrator_next,
    )

    if agreement["status"] == "disagreement":
        status = "blocked"
    elif any(status == "blocked" for status in statuses):
        status = "blocked"
    elif statuses and all(status in {"completed", "complete"} for status in statuses):
        status = "completed"
    else:
        status = "ready"

    return ControlPlaneStatus(
        repo=repo,
        status=status,
        planner=planner_run,
        scheduler=scheduler_next,
        orchestrator=orchestrator_next,
        agreement=agreement,
        refresh={
            "mode": "single-snapshot",
            "auto_refresh": "off",
            "command": refresh_command,
            "reason": (
                "explicit reruns keep GitHub reads bounded and avoid hidden "
                "workflow polling"
            ),
        },
        bugs=bugs,
        notes=(
            "Read-only control-plane status.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
            "No lifecycle command was executed.",
            "No OpenClaw execution was performed.",
        ),
    )


def _build_agreement(
    *,
    planner_run: dict[str, Any] | None,
    scheduler_next: SchedulerNext | None,
    orchestrator_next: OrchestratorNext | None,
) -> dict[str, Any]:
    planner_issue = _planner_next_issue(planner_run)
    scheduler_issue = (
        scheduler_next.issue.number
        if scheduler_next is not None and scheduler_next.issue is not None
        else None
    )
    active_issues = _unique_issues(
        [
            *_planner_active_issues(planner_run),
            *_scheduler_active_issues(scheduler_next),
        ]
    )
    lifecycle = getattr(orchestrator_next, "lifecycle", None)
    orchestrator_issue = getattr(lifecycle, "issue_number", None)

    sources = {
        "planner": planner_issue,
        "scheduler": scheduler_issue,
        "orchestrator": orchestrator_issue,
    }
    for issue in active_issues:
        sources[f"active:#{issue}"] = issue
    evaluated = {source: issue for source, issue in sources.items() if issue is not None}
    unique_issues = set(evaluated.values())

    if len(active_issues) > 1:
        status = "disagreement"
        reason = "multiple active issues require explicit operator selection"
    elif len(evaluated) < 2:
        status = "not evaluated"
        reason = "fewer than two target-selecting sources were evaluated"
    elif len(unique_issues) == 1:
        status = "aligned"
        issue = next(iter(unique_issues))
        reason = f"evaluated sources point at issue #{issue}"
    else:
        status = "disagreement"
        reason = "evaluated sources point at different issues"

    return {
        "status": status,
        "reason": reason,
        "planner_issue": planner_issue,
        "scheduler_issue": scheduler_issue,
        "orchestrator_issue": orchestrator_issue,
        "active_issues": active_issues,
    }


def _planner_next_issue(planner_run: dict[str, Any] | None) -> int | None:
    if planner_run is None:
        return None
    next_plan = planner_run.get("next")
    if not isinstance(next_plan, dict):
        return None
    next_task = next_plan.get("next")
    if not isinstance(next_task, dict):
        return None
    issue = next_task.get("github_issue")
    if issue is None:
        return None
    try:
        return int(issue)
    except (TypeError, ValueError):
        return None


def _scheduler_active_issues(scheduler_next: SchedulerNext | None) -> tuple[int, ...]:
    if scheduler_next is None:
        return ()
    issues: list[int] = []
    for item in scheduler_next.skipped:
        if ": state:active" not in item:
            continue
        raw_issue = item.split(":", 1)[0].strip().removeprefix("#")
        try:
            issue = int(raw_issue)
        except ValueError:
            continue
        if issue not in issues:
            issues.append(issue)
    return tuple(issues)


def _planner_active_issues(planner_run: dict[str, Any] | None) -> tuple[int, ...]:
    if planner_run is None:
        return ()
    issues: list[int] = []
    for task in planner_run.get("active_tasks", []):
        if not isinstance(task, dict):
            continue
        issue = task.get("github_issue")
        if issue is None:
            continue
        try:
            issue_number = int(issue)
        except (TypeError, ValueError):
            continue
        issues.append(issue_number)
    return tuple(issues)


def _unique_issues(values: list[int]) -> tuple[int, ...]:
    unique: list[int] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def format_control_plane_status(result: ControlPlaneStatus) -> str:
    """Format a compact operator-facing status view."""
    lines = [
        "Signposter Control Plane Status",
        "",
        "Status:",
        f"  {result.status}",
        "",
        "Repo:",
        f"  {result.repo}",
    ]

    agreement = result.agreement
    lines.extend(
        [
            "",
            "Agreement:",
            f"  status: {agreement.get('status', 'unknown')}",
            f"  planner issue: {_format_issue_ref(agreement.get('planner_issue'))}",
            f"  scheduler issue: {_format_issue_ref(agreement.get('scheduler_issue'))}",
            f"  orchestrator issue: {_format_issue_ref(agreement.get('orchestrator_issue'))}",
            f"  active issues: {_format_issue_refs(agreement.get('active_issues'))}",
            f"  reason: {agreement.get('reason', 'unknown')}",
        ]
    )

    refresh = result.refresh
    lines.extend(
        [
            "",
            "Refresh:",
            f"  mode: {refresh.get('mode', 'unknown')}",
            f"  auto-refresh: {refresh.get('auto_refresh', 'off')}",
            f"  command: {refresh.get('command') or 'rerun the same explicit status command'}",
            f"  reason: {refresh.get('reason', 'unknown')}",
        ]
    )

    lines.extend(["", "Planner:"])
    if result.planner is None:
        lines.append("  manifest: not provided")
    else:
        counts = result.planner.get("status_counts", {})
        next_task = result.planner.get("next", {}).get("next")
        lines.append(f"  status: {result.planner.get('planner_status', 'unknown')}")
        if counts:
            lines.append(
                "  counts: "
                f"total={counts.get('total', 0)} "
                f"ready={counts.get('ready', 0)} "
                f"active={counts.get('active', 0)} "
                f"merged={counts.get('merged', 0)} "
                f"blocked={counts.get('blocked', 0)}"
            )
        if next_task:
            lines.append(
                f"  next: {next_task['key']} "
                f"(#{next_task['github_issue']}, state={next_task['state']})"
            )
        else:
            lines.append("  next: none")

    lines.extend(["", "Scheduler:"])
    if result.scheduler is None:
        lines.append("  status: not evaluated")
    else:
        lines.append(f"  status: {result.scheduler.status}")
        if result.scheduler.issue is not None:
            issue = result.scheduler.issue
            lines.append(f"  next: #{issue.number} — {issue.title}")
        else:
            lines.append("  next: none")
        active_counts = result.scheduler.active_counts or {}
        if active_counts:
            compact = ", ".join(f"{key}={value}" for key, value in sorted(active_counts.items()))
            lines.append(f"  active: {compact}")
        active_notes = result.scheduler.active_notes or []
        if active_notes:
            lines.append("  active diagnostics:")
            lines.extend(f"    {item}" for item in active_notes[:5])
            if len(active_notes) > 5:
                lines.append(f"    ... {len(active_notes) - 5} more")

    lines.extend(["", "Orchestrator:"])
    if result.orchestrator is None:
        lines.append("  status: not evaluated")
    else:
        lines.append(f"  status: {result.orchestrator.status}")
        lines.append(f"  action: {result.orchestrator.action}")
        if result.orchestrator.stop_reason:
            lines.append(f"  stop: {result.orchestrator.stop_reason}")
        if result.orchestrator.takeover_category:
            lines.append(
                "  takeover: "
                f"{result.orchestrator.takeover_category} — "
                f"{result.orchestrator.takeover_reason or 'unspecified'}"
            )

    lines.extend(["", "Bug ledger:"])
    if not result.bugs:
        lines.append("  recent: none")
    else:
        for entry in result.bugs:
            target = ""
            if entry.current_issue is not None:
                target = f" issue=#{entry.current_issue}"
            elif entry.current_pr is not None:
                target = f" pr=#{entry.current_pr}"
            lines.append(f"  {entry.key} [{entry.status}]{target}: {entry.summary}")

    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def _format_issue_ref(value: Any) -> str:
    if value is None:
        return "none"
    return f"#{value}"


def _format_issue_refs(values: Any) -> str:
    if not values:
        return "none"
    return ", ".join(f"#{value}" for value in values)
