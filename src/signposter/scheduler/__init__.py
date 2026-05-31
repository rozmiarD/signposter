"""Simple GitHub-label scheduler for Signposter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    active_notes: list[str] | None = None


@dataclass(frozen=True)
class SchedulerGraphItem:
    """One issue node in the scheduler graph."""

    number: int
    title: str
    state: str | None
    url: str
    metadata: GraphMetadata


@dataclass(frozen=True)
class SchedulerGraph:
    """Read-only scheduler graph snapshot."""

    repo: str
    items: list[SchedulerGraphItem]
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
    active_notes: list[str] = []
    ready_mainline: LabeledItem | None = None

    for issue in sorted(fetch_open_issues(repo, limit=limit), key=lambda item: item.number):
        state = _state(issue.labels)
        if state != "ready":
            if state in TERMINAL_STATES or state == "active":
                skipped.append(f"#{issue.number}: state:{state}")
            if state == "active":
                active_notes.append(_active_issue_note(issue))
            continue

        context = fetch_issue_context(repo, issue.number) or {}
        metadata = parse_graph_metadata(context.get("body"))
        blocked, reason = is_dependency_blocked(repo, context.get("body"))
        if blocked:
            skipped.append(f"#{issue.number}: {reason}")
            continue

        if metadata.side_task:
            return SchedulerNext(
                repo=repo,
                status="ready",
                issue=issue,
                reason="first open unblocked side-task selected",
                skipped=skipped,
                notes=[
                    "Read-only scheduler selection.",
                    "No GitHub mutation was performed.",
                    "No worktree was created.",
                    "No OpenClaw execution was performed.",
                ],
                active_notes=active_notes,
            )

        if ready_mainline is not None:
            continue
        ready_mainline = issue

    if ready_mainline is not None:
        return SchedulerNext(
            repo=repo,
            status="ready",
            issue=ready_mainline,
            reason="first open state:ready issue with clear dependencies",
            skipped=skipped,
            notes=[
                "Read-only scheduler selection.",
                "No GitHub mutation was performed.",
                "No worktree was created.",
                "No OpenClaw execution was performed.",
            ],
            active_notes=active_notes,
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
        active_notes=active_notes,
    )


def _active_issue_note(
    issue: LabeledItem,
    *,
    stale_after: timedelta = timedelta(days=2),
    now: datetime | None = None,
) -> str:
    checks: list[str] = []
    prompt = Path("artifacts") / "prompts" / f"issue-{issue.number}.md"
    worktree_exists = _active_issue_worktree_exists(issue.number)
    prompt_exists = prompt.exists()
    checks.append(f"worktree={'present' if worktree_exists else 'missing'}")
    checks.append(f"prompt={'present' if prompt_exists else 'missing'}")

    age = _issue_age(issue.updated_at, now=now)
    if age is None:
        checks.append("activity_age=unknown")
    elif age > stale_after:
        checks.append(f"activity_age=stale({age.days}d)")
    else:
        checks.append("activity_age=fresh")

    can_resume = worktree_exists or prompt_exists
    checks.append(f"resume={'possible' if can_resume else 'needs inspection'}")
    return f"#{issue.number}: " + ", ".join(checks)


def _active_issue_worktree_exists(issue_number: int) -> bool:
    cwd = Path.cwd()
    candidates = [
        Path("..") / "signposter-work" / str(issue_number),
    ]
    if cwd.name == str(issue_number):
        candidates.append(cwd)
    if cwd.parent.name == "signposter-work":
        candidates.append(cwd.parent / str(issue_number))
    return any(path.exists() for path in candidates)


def _issue_age(updated_at: str | None, *, now: datetime | None = None) -> timedelta | None:
    if not updated_at:
        return None
    current = now or datetime.now(UTC)
    text = updated_at.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        updated = datetime.fromisoformat(text)
    except ValueError:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return current - updated


def build_scheduler_graph(repo: str, *, limit: int = 50) -> SchedulerGraph:
    """Build a read-only graph snapshot from open GitHub issues."""
    items: list[SchedulerGraphItem] = []
    for issue in sorted(fetch_open_issues(repo, limit=limit), key=lambda item: item.number):
        context = fetch_issue_context(repo, issue.number) or {}
        items.append(
            SchedulerGraphItem(
                number=issue.number,
                title=issue.title,
                state=_state(issue.labels),
                url=issue.html_url,
                metadata=parse_graph_metadata(context.get("body")),
            )
        )

    return SchedulerGraph(
        repo=repo,
        items=items,
        notes=[
            "Read-only scheduler graph.",
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
    if result.active_notes:
        lines.extend(["", "Active issues:"])
        lines.extend(f"  {item}" for item in result.active_notes)
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def format_scheduler_explain(result: SchedulerNext) -> str:
    """Render a concise explanation of scheduler selection."""
    lines = [
        "Signposter Scheduler Explain",
        "",
        "Selection:",
    ]
    if result.issue:
        lines.extend(
            [
                f"  selected: #{result.issue.number} — {result.issue.title}",
                f"  reason: {result.reason}",
            ]
        )
    else:
        lines.extend(["  selected: none", f"  reason: {result.reason}"])

    lines.extend(["", "Skipped:"])
    if result.skipped:
        lines.extend(f"  {item}" for item in result.skipped)
    else:
        lines.append("  none")

    lines.extend(["", "Active issues:"])
    if result.active_notes:
        lines.extend(f"  {item}" for item in result.active_notes)
    else:
        lines.append("  none")

    lines.extend(["", "Status:", f"  {result.status}"])
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def format_scheduler_graph(result: SchedulerGraph) -> str:
    """Render compact scheduler graph output."""
    state_by_issue = {item.number: item.state for item in result.items}
    lines = [
        "Signposter Scheduler Graph",
        "",
        "Repo:",
        f"  {result.repo}",
        "",
        "Issues:",
    ]
    if not result.items:
        lines.append("  none")
    for item in result.items:
        meta = item.metadata
        deps = ", ".join(f"#{number}" for number in meta.depends_on) or "none"
        parent = f"#{meta.parent}" if meta.parent is not None else "none"
        return_to = f"#{meta.return_to}" if meta.return_to is not None else "none"
        parent_state = _linked_state(meta.parent, state_by_issue)
        return_state = _linked_state(meta.return_to, state_by_issue)
        mainline = meta.mainline or "none"
        side = "yes" if meta.side_task else "no"
        lines.extend(
            [
                f"  #{item.number} — {item.title}",
                f"    state: {item.state or 'unknown'}",
                f"    depends on: {deps}",
                f"    mainline: {mainline}",
                f"    parent: {parent}",
                f"    parent state: {parent_state}",
                f"    return-to: {return_to}",
                f"    return state: {return_state}",
                f"    return ready: {_return_ready(meta.return_to, state_by_issue)}",
                f"    side-task: {side}",
            ]
        )

    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def _linked_state(issue: int | None, state_by_issue: dict[int, str | None]) -> str:
    if issue is None:
        return "none"
    return state_by_issue.get(issue) or "unknown"


def _return_ready(issue: int | None, state_by_issue: dict[int, str | None]) -> str:
    if issue is None:
        return "n/a"
    return "yes" if state_by_issue.get(issue) == "ready" else "no"
