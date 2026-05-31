"""Simple GitHub-label scheduler for Signposter."""

from __future__ import annotations

import re
from dataclasses import dataclass

from signposter.dependencies import is_dependency_blocked, parse_depends_on
from signposter.scan import LabeledItem, fetch_issue_context, fetch_open_issues

TERMINAL_STATES = {"done", "merged", "blocked", "failed"}
ISSUE_REF_RE = re.compile(r"#(\d+)")


@dataclass(frozen=True)
class SchedulerNext:
    """Next issue selected from GitHub labels without a manifest."""

    repo: str
    status: str
    issue: LabeledItem | None
    reason: str
    skipped: list[str]
    notes: list[str]


@dataclass(frozen=True)
class GraphMetadata:
    """Graph metadata parsed from a GitHub issue body."""

    depends_on: list[int]
    mainline: str | None
    parent: int | None
    return_to: int | None
    side_task: bool


def parse_graph_metadata(body: str | None) -> GraphMetadata:
    """Parse simple graph metadata from an issue body."""
    depends_on = parse_depends_on(body)
    mainline: str | None = None
    parent: int | None = None
    return_to: int | None = None
    side_task = False

    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith("mainline:"):
            value = line.split(":", 1)[1].strip()
            mainline = value or None
        elif lower.startswith("parent:"):
            parent = _first_issue_ref(line)
        elif lower.startswith("return-to:"):
            return_to = _first_issue_ref(line)
        elif lower.startswith("side-task"):
            value = line.split(":", 1)[1].strip().lower() if ":" in line else "yes"
            side_task = value in {"1", "true", "yes", "y"}

    return GraphMetadata(
        depends_on=depends_on,
        mainline=mainline,
        parent=parent,
        return_to=return_to,
        side_task=side_task,
    )


def _first_issue_ref(text: str) -> int | None:
    match = ISSUE_REF_RE.search(text)
    return int(match.group(1)) if match else None


def _state(labels: list[str]) -> str | None:
    for label in labels:
        if label.startswith("state:"):
            return label.split(":", 1)[1]
    return None


def select_next_issue(repo: str, *, limit: int = 50) -> SchedulerNext:
    """Select the first dependency-clear open issue labeled state:ready."""
    skipped: list[str] = []

    for issue in sorted(fetch_open_issues(repo, limit=limit), key=lambda item: item.number):
        state = _state(issue.labels)
        if state != "ready":
            if state in TERMINAL_STATES or state == "active":
                skipped.append(f"#{issue.number}: state:{state}")
            continue

        context = fetch_issue_context(repo, issue.number) or {}
        blocked, reason = is_dependency_blocked(repo, context.get("body"))
        if blocked:
            skipped.append(f"#{issue.number}: {reason}")
            continue

        return SchedulerNext(
            repo=repo,
            status="ready",
            issue=issue,
            reason="first open state:ready issue with clear dependencies",
            skipped=skipped,
            notes=[
                "Read-only scheduler selection.",
                "No GitHub mutation was performed.",
                "No worktree was created.",
                "No OpenClaw execution was performed.",
            ],
        )

    return SchedulerNext(
        repo=repo,
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=skipped,
        notes=[
            "Read-only scheduler selection.",
            "No GitHub mutation was performed.",
            "No worktree was created.",
            "No OpenClaw execution was performed.",
        ],
    )


def format_scheduler_next(result: SchedulerNext) -> str:
    """Render compact scheduler output."""
    lines = [
        "Signposter Scheduler Next",
        "",
        "Repo:",
        f"  {result.repo}",
        "",
        "Status:",
        f"  {result.status}",
        "",
        "Reason:",
        f"  {result.reason}",
    ]
    if result.issue:
        lines.extend(
            [
                "",
                "Next issue:",
                f"  #{result.issue.number} — {result.issue.title}",
                f"  {result.issue.html_url}",
            ]
        )
    if result.skipped:
        lines.extend(["", "Skipped:"])
        lines.extend(f"  {item}" for item in result.skipped)
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)
