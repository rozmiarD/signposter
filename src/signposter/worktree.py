"""Worktree / branch planning for isolated worker execution (dry-run only).

This module produces plans for safe, isolated execution using git worktrees
and branches. No mutations are performed.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from signposter.dependencies import is_dependency_blocked
from signposter.dispatch import classify_candidate
from signposter.git_utils import (
    branch_exists,
    get_current_branch,
    has_blocking_dirty_changes,
    remote_branch_exists,
    worktree_path_exists,
)
from signposter.scan import LabeledItem, fetch_issue_by_number, fetch_issue_context


@dataclass(frozen=True)
class WorktreePlan:
    """A dry-run plan for isolated execution of an issue."""

    issue_number: int
    title: str
    state: str | None
    route: str | None
    gate: str | None

    base_branch: str | None
    proposed_branch: str
    proposed_worktree: str

    working_tree_clean: bool
    branch_exists: bool
    worktree_exists: bool
    has_unresolved_dependencies: bool
    dependency_block_reason: str | None

    status: str  # "ready" | "blocked — <reason>"
    notes: list[str]
    remote_branch_exists: bool = False
    branch_collision_reason: str | None = None


def _slugify_title(title: str, max_len: int = 50) -> str:
    """Convert issue title to a safe, hyphenated slug."""
    if not title:
        return "untitled"

    # Lowercase, replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug)
    # Truncate
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def generate_proposed_branch(issue_number: int, title: str) -> str:
    """Generate deterministic branch name like work/issue-12-implement-feature-x."""
    slug = _slugify_title(title)
    return f"work/issue-{issue_number}-{slug}"


def generate_proposed_worktree(issue_number: int, base: str = "..") -> str:
    """Generate proposed worktree path."""
    return str(Path(base) / "signposter-work" / str(issue_number))


def plan_worktree_for_issue(repo: str, issue_number: int) -> WorktreePlan:
    """Produce a WorktreePlan for a specific issue (read-only)."""
    item: LabeledItem | None = fetch_issue_by_number(repo, issue_number)

    if item is None:
        return WorktreePlan(
            issue_number=issue_number,
            title="unknown",
            state=None,
            route=None,
            gate=None,
            base_branch=None,
            proposed_branch=f"work/issue-{issue_number}-unknown",
            proposed_worktree=generate_proposed_worktree(issue_number),
            working_tree_clean=False,
            branch_exists=False,
            worktree_exists=False,
            has_unresolved_dependencies=False,
            dependency_block_reason=None,
            status=f"blocked — could not fetch issue #{issue_number}",
            notes=["Issue not found or not accessible."],
        )

    dispatch = classify_candidate(item)
    state = dispatch.state
    route = dispatch.proposed_route
    gate = dispatch.proposed_gate
    role = dispatch.role
    phase = dispatch.phase

    # Git context
    base_branch = get_current_branch() or "main"
    proposed_branch = generate_proposed_branch(issue_number, item.title)
    proposed_worktree = generate_proposed_worktree(issue_number)

    tree_clean = not has_blocking_dirty_changes()
    branch_exists_flag = branch_exists(proposed_branch)
    remote_branch_exists_flag = remote_branch_exists(proposed_branch)
    worktree_exists_flag = worktree_path_exists(proposed_worktree)

    # Dependency check
    context = fetch_issue_context(repo, issue_number) or {}
    body = context.get("body", "") or ""
    blocked_by_deps, dep_reason = is_dependency_blocked(repo, body)

    # Determine status
    notes: list[str] = ["No branches or worktrees were created."]
    status = "ready"
    branch_collision_reason: str | None = None

    supports_worker_route = route == "worker"
    supports_reviewer_worker_route = (
        route == "reviewer"
        and role == "worker"
        and phase == "build"
    )
    supported_route = supports_worker_route or supports_reviewer_worker_route

    if supports_reviewer_worker_route:
        notes.append("Reviewer-route build task is supported because role is worker.")
    if gate == "human":
        notes.append(
            "Human-gated issue: local worktree planning is allowed; "
            "gate remains separate."
        )

    if state in ("done", "failed", "merged"):
        status = f"blocked — issue is state:{state}"
    elif blocked_by_deps:
        status = f"blocked — {dep_reason}"
    elif not tree_clean:
        status = "blocked — working tree has uncommitted changes"
    elif branch_exists_flag:
        status = f"blocked — proposed branch already exists: {proposed_branch}"
        branch_collision_reason = "local branch already exists"
    elif remote_branch_exists_flag:
        status = f"blocked — proposed remote branch already exists: origin/{proposed_branch}"
        branch_collision_reason = "remote-tracking branch already exists"
    elif worktree_exists_flag:
        status = f"blocked — proposed worktree path already exists: {proposed_worktree}"
        branch_collision_reason = "worktree path already exists"
    elif not supported_route:
        status = (
            f"blocked — route is '{route}' "
            "("
            "worktree planning requires route:worker or route:reviewer "
            "with role:worker phase:build"
            ")"
        )
    else:
        status = "ready"

    return WorktreePlan(
        issue_number=issue_number,
        title=item.title,
        state=state,
        route=route,
        gate=gate,
        base_branch=base_branch,
        proposed_branch=proposed_branch,
        proposed_worktree=proposed_worktree,
        working_tree_clean=tree_clean,
        branch_exists=branch_exists_flag,
        worktree_exists=worktree_exists_flag,
        has_unresolved_dependencies=blocked_by_deps,
        dependency_block_reason=dep_reason if blocked_by_deps else None,
        status=status,
        notes=notes,
        remote_branch_exists=remote_branch_exists_flag,
        branch_collision_reason=branch_collision_reason,
    )


def format_worktree_plan(plan: WorktreePlan) -> str:
    """Produce compact human-readable output for the plan."""
    lines = [f"Signposter Worktree Plan — Issue #{plan.issue_number}\n"]

    lines.append("Issue:")
    lines.append(f"  title: {plan.title}")
    lines.append(f"  state: {plan.state or 'unknown'}")
    lines.append(f"  route: {plan.route or 'unknown'}")
    if plan.gate:
        lines.append(f"  gate: {plan.gate}")

    lines.append("\nGit:")
    lines.append(f"  base branch: {plan.base_branch or 'unknown'}")
    lines.append(f"  proposed branch: {plan.proposed_branch}")
    lines.append(f"  proposed worktree: {plan.proposed_worktree}")
    lines.append(f"  working tree: {'clean' if plan.working_tree_clean else 'dirty'}")
    lines.append(f"  local branch exists: {'yes' if plan.branch_exists else 'no'}")
    lines.append(
        f"  remote branch exists: {'yes' if plan.remote_branch_exists else 'no'}"
    )
    lines.append(f"  worktree path exists: {'yes' if plan.worktree_exists else 'no'}")
    if plan.branch_collision_reason:
        lines.append(f"  collision reason: {plan.branch_collision_reason}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)


def format_worktree_apply_plan(plan: WorktreePlan, *, dry_run: bool = True) -> str:
    """Compact output for the apply command (dry-run or real apply)."""
    lines = [f"Signposter Worktree Apply Plan — Issue #{plan.issue_number}\n"]

    lines.append("Status:")
    lines.append(f"  {plan.status}")

    if plan.status == "ready":
        base = plan.base_branch or "main"
        cmd = [
            "git", "worktree", "add", "-b",
            plan.proposed_branch,
            plan.proposed_worktree,
            base,
        ]
        cmd_str = " ".join(cmd)

        if dry_run:
            lines.append("\nWould run:")
            lines.append(f"  {cmd_str}")
            lines.append("\nNote: This is a DRY RUN. No branches or worktrees were created.")
        else:
            lines.append("\nApplying:")
            lines.append(f"  {cmd_str}")
    else:
        lines.append("\nRefusing to create worktree.")

    return "\n".join(lines)


def apply_worktree_plan(plan: WorktreePlan, *, dry_run: bool = True) -> list[str]:
    """Execute (or simulate) the git worktree creation.

    Returns list of shell-style command strings that were or would be run.
    Only performs the mutation when dry_run=False and plan.status == 'ready'.
    """
    commands: list[str] = []

    if plan.status != "ready":
        return commands

    base = plan.base_branch or "main"
    cmd = [
        "git", "worktree", "add", "-b",
        plan.proposed_branch,
        plan.proposed_worktree,
        base,
    ]
    commands.append(" ".join(cmd))

    if not dry_run:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr or ""
            raise RuntimeError(f"git worktree add failed: {stderr.strip()}") from e

    return commands


def get_worktree_status_for_issue(issue_number: int, title: str | None = None) -> dict:
    """Lightweight diagnostic for runner integration (HARDENING-009).

    Returns:
      status: 'available' | 'missing'
      path, branch, exists
    """
    cwd = Path.cwd()
    expected_path = generate_proposed_worktree(issue_number)
    branch_name = generate_proposed_branch(issue_number, title or "task")
    candidate_paths = [Path(expected_path)]

    if cwd.name == str(issue_number):
        candidate_paths.append(cwd)
    if cwd.parent.name == "signposter-work":
        candidate_paths.append(cwd.parent / str(issue_number))

    existing_path = next((path for path in candidate_paths if path.exists()), None)
    exists = existing_path is not None
    resolved_path = str(existing_path) if existing_path is not None else expected_path

    return {
        "status": "available" if exists else "missing",
        "path": resolved_path,
        "branch": branch_name,
        "exists": exists,
    }
