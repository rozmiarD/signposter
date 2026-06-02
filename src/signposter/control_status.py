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
    bugs: tuple[BugLedgerEntry, ...]
    notes: tuple[str, ...]


def build_control_plane_status(
    *,
    repo: str,
    planner_run: dict[str, Any] | None = None,
    scheduler_next: SchedulerNext | None = None,
    orchestrator_next: OrchestratorNext | None = None,
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

    if any(status == "blocked" for status in statuses):
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
        bugs=bugs,
        notes=(
            "Read-only control-plane status.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
            "No lifecycle command was executed.",
            "No OpenClaw execution was performed.",
        ),
    )


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
