"""Handoff planning for isolated worker branches (planning / dry-run only).

Provides a planning surface for committing, pushing, and handing off work
done inside a Signposter-managed worktree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from signposter.git_utils import get_current_branch, get_git_status_short
from signposter.worktree import (
    generate_proposed_branch,
    generate_proposed_worktree,
    get_worktree_status_for_issue,
)


@dataclass(frozen=True)
class HandoffPlan:
    issue_number: int
    title: str
    workflow_state: str | None  # from labels, e.g. "done", "active"
    github_issue_state: str | None  # "OPEN", "CLOSED"

    worktree_path: str
    branch: str
    worktree_exists: bool
    current_branch_in_worktree: str | None

    status_lines: list[str]  # e.g. ["M README.md", "?? newfile"]
    changed_files: list[str]
    has_changes: bool

    suggested_commit_message: str
    suggested_next_commands: list[str]

    status: str  # "ready" or "blocked — <reason>"
    notes: list[str]


def _slug_for_commit(title: str) -> str:
    """Create a short slug for commit messages."""
    if not title:
        return "task"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "task"


def _normalize_label_names(labels: list[object]) -> list[str]:
    """Return label names from either strings or GitHub label dicts."""
    names: list[str] = []
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def _infer_commit_prefix(labels: list[object]) -> str:
    """Very lightweight prefix inference."""
    label_names = _normalize_label_names(labels)
    if any(lbl.startswith("area:docs") for lbl in label_names):
        return "docs:"
    if any(lbl.startswith("area:tests") for lbl in label_names):
        return "test:"
    return "work:"


def _parse_status_path(line: str) -> str:
    """Return file path from git status --short style output.

    Handles both raw porcelain lines, e.g. " M README.md",
    and normalized/trimmed lines, e.g. "M README.md".
    """
    if not line.strip():
        return ""

    if line.startswith("?? "):
        return line[3:].strip()

    # Raw porcelain v1 uses two status columns followed by a space:
    # " M README.md", "M  README.md", "MM README.md".
    if len(line) >= 4 and line[2] == " ":
        return line[3:].strip()

    # Some helpers normalize/strip the leading blank status column,
    # producing "M README.md" instead of " M README.md".
    if len(line) >= 3 and line[1] == " ":
        return line[2:].strip()

    return line.strip()


def plan_handoff_for_issue(repo: str, issue_number: int) -> HandoffPlan:
    """Produce a HandoffPlan (read-only, no mutations)."""
    from signposter.dispatch import classify_candidate
    from signposter.scan import LabeledItem, fetch_issue_by_number, fetch_issue_context

    # 1. Fetch issue and classify
    item: LabeledItem | None = fetch_issue_by_number(repo, issue_number)
    if item is None:
        return HandoffPlan(
            issue_number=issue_number,
            title="unknown",
            workflow_state=None,
            github_issue_state=None,
            worktree_path=generate_proposed_worktree(issue_number),
            branch=generate_proposed_branch(issue_number, "unknown"),
            worktree_exists=False,
            current_branch_in_worktree=None,
            status_lines=[],
            changed_files=[],
            has_changes=False,
            suggested_commit_message=f"work: issue-{issue_number}",
            suggested_next_commands=[],
            status=f"blocked — could not fetch issue #{issue_number}",
            notes=["No commit, push, PR, merge, or issue close was performed."],
        )

    dispatch = classify_candidate(item)
    workflow_state = dispatch.state

    # Get labels for prefix inference
    context = fetch_issue_context(repo, issue_number) or {}
    labels = context.get("labels", []) if isinstance(context.get("labels"), list) else []

    # Worktree info
    ws = get_worktree_status_for_issue(issue_number, item.title)
    worktree_path = ws["path"]
    expected_branch = ws["branch"]
    worktree_exists = ws["exists"]

    if not worktree_exists:
        return HandoffPlan(
            issue_number=issue_number,
            title=item.title,
            workflow_state=workflow_state,
            github_issue_state="OPEN",  # we don't fetch real state here for simplicity
            worktree_path=worktree_path,
            branch=expected_branch,
            worktree_exists=False,
            current_branch_in_worktree=None,
            status_lines=[],
            changed_files=[],
            has_changes=False,
            suggested_commit_message=(
                f"{_infer_commit_prefix(labels)} issue-{issue_number} "
                f"{_slug_for_commit(item.title)}"
            ),
            suggested_next_commands=[],
            status="blocked — expected worktree is missing",
            notes=["No commit, push, PR, merge, or issue close was performed."],
        )

    # Git status inside the worktree
    status_lines = get_git_status_short(cwd=worktree_path)
    changed_files = []
    for line in status_lines:
        path = _parse_status_path(line)
        if path:
            changed_files.append(path)

    has_changes = len(changed_files) > 0

    current_branch = get_current_branch(cwd=worktree_path)

    # Suggested commit message
    prefix = _infer_commit_prefix(labels)
    slug = _slug_for_commit(item.title)
    suggested_commit = f"{prefix} {slug}"

    # Next commands
    next_cmds = [
        f"git -C {worktree_path} diff",
        f"git -C {worktree_path} add -A",
        f'git -C {worktree_path} commit -m "{suggested_commit}"',
        f"git -C {worktree_path} push -u origin {expected_branch}",
    ]

    # Status determination
    if not has_changes:
        status = "blocked — no changes found in worktree"
    elif workflow_state != "done":
        status = f"blocked — issue is not state:done (current: {workflow_state})"
    else:
        status = "ready"

    notes = [
        "No commit, push, PR, merge, or issue close was performed.",
        "GitHub issue should remain open until explicit integration + close policy exists.",
    ]

    return HandoffPlan(
        issue_number=issue_number,
        title=item.title,
        workflow_state=workflow_state,
        github_issue_state="OPEN",  # conservative default for planning
        worktree_path=worktree_path,
        branch=expected_branch,
        worktree_exists=True,
        current_branch_in_worktree=current_branch,
        status_lines=status_lines,
        changed_files=changed_files,
        has_changes=has_changes,
        suggested_commit_message=suggested_commit,
        suggested_next_commands=next_cmds,
        status=status,
        notes=notes,
    )


def format_handoff_plan(plan: HandoffPlan) -> str:
    """Compact human-readable handoff plan output."""
    lines = [f"Signposter Handoff Plan — Issue #{plan.issue_number}\n"]

    lines.append("Issue:")
    lines.append(f"  title: {plan.title}")
    lines.append(f"  workflow state: {plan.workflow_state or 'unknown'}")
    lines.append(f"  github issue: {plan.github_issue_state or 'open'}")

    lines.append("\nWorktree:")
    lines.append(f"  status: {'available' if plan.worktree_exists else 'missing'}")
    lines.append(f"  path: {plan.worktree_path}")
    lines.append(f"  branch: {plan.branch}")

    if plan.current_branch_in_worktree:
        lines.append(f"  current branch in worktree: {plan.current_branch_in_worktree}")

    lines.append("\nChanges:")
    if plan.has_changes:
        for f in plan.changed_files[:10]:
            lines.append(f"  {f}")
        if len(plan.changed_files) > 10:
            lines.append(f"  ... ({len(plan.changed_files)} total)")
        lines.append(f"  files changed: {len(plan.changed_files)}")
    else:
        lines.append("  (no changes detected)")

    lines.append("\nSuggested commit:")
    lines.append(f"  {plan.suggested_commit_message}")

    if plan.suggested_next_commands:
        lines.append("\nSuggested next commands:")
        for cmd in plan.suggested_next_commands:
            lines.append(f"  {cmd}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)
