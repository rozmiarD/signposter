"""Minimal git working tree inspection helpers for Signposter worker isolation.

Used to guard against accidental edits in a dirty main working tree.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Paths that are expected to be dirty during Signposter operation
# (these are typically gitignored)
ALLOWED_DIRTY_PREFIXES = (
    "artifacts/",
    "signposter-work/",
    ".signposter/",
    ".openclaw/",
)

PROTECTED_MUTATION_BRANCHES = ("main", "master", "trunk")
ISOLATED_WORK_BRANCH_PREFIXES = (
    "work/",
    "feature/",
    "fix/",
    "bugfix/",
    "hotfix/",
    "refactor/",
    "docs/",
    "test/",
    "chore/",
)


@dataclass(frozen=True)
class BranchSyncStatus:
    """Read-only local branch sync status against a remote-tracking branch."""

    branch: str
    upstream: str
    ahead: int | None
    behind: int | None
    status: str
    reason: str


@dataclass(frozen=True)
class RepoMutationSafety:
    """Read-only branch safety decision for mutating repo work."""

    cwd: str
    current_branch: str | None
    protected_branches: tuple[str, ...]
    isolated_work_branch_prefixes: tuple[str, ...]
    status: str
    reason: str
    requires_isolated_worktree: bool
    recommended_action: str


def get_git_status_short(cwd: str | Path = ".") -> list[str]:
    """Return `git status --short --untracked-files=all` output lines.

    Returns empty list if not a git repo or on error (fail-safe for now).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return lines
    except Exception:
        return []


def is_working_tree_clean(cwd: str | Path = ".") -> bool:
    """True if there are no uncommitted changes (including untracked files)."""
    return len(get_git_status_short(cwd)) == 0


def find_uncommitted_repo_changes(
    cwd: str | Path = ".",
    allowed_prefixes: Iterable[str] = ALLOWED_DIRTY_PREFIXES,
) -> list[str]:
    """Return list of dirty paths that are *not* in the allowed ignored/runtime set.

    These are the changes that should block worker execution.
    """
    status_lines = get_git_status_short(cwd)
    dirty: list[str] = []

    for line in status_lines:
        # git status --short format: "?? path" or " M path" etc.
        # We take everything after the first two characters + space.
        if len(line) < 3:
            continue
        path = line[3:].strip() if line[0:2].strip() else line[2:].strip()

        if not path:
            continue

        # Check if this path is covered by an allowed prefix
        is_allowed = any(path.startswith(prefix) for prefix in allowed_prefixes)
        if not is_allowed:
            dirty.append(path)

    return dirty


def has_blocking_dirty_changes(cwd: str | Path = ".") -> bool:
    """True if there are uncommitted changes outside of allowed runtime artifact paths."""
    return len(find_uncommitted_repo_changes(cwd)) > 0


def get_current_branch(cwd: str | Path = ".") -> str | None:
    """Return the name of the current git branch, or None if not on a branch or error."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
        return None
    except Exception:
        return None


def is_protected_mutation_branch(
    branch: str | None,
    protected_branches: Iterable[str] = PROTECTED_MUTATION_BRANCHES,
) -> bool:
    """Return true when `branch` is protected from direct worker mutation."""
    if not branch:
        return False
    protected = {b.strip() for b in protected_branches if b.strip()}
    return branch.strip() in protected


def is_isolated_work_branch(
    branch: str | None,
    prefixes: Iterable[str] = ISOLATED_WORK_BRANCH_PREFIXES,
) -> bool:
    """Return true when `branch` looks like an isolated task branch."""
    if not branch:
        return False
    value = branch.strip()
    return any(value.startswith(prefix) for prefix in prefixes if prefix)


def evaluate_repo_mutation_safety(
    cwd: str | Path = ".",
    *,
    protected_branches: Iterable[str] = PROTECTED_MUTATION_BRANCHES,
    isolated_work_branch_prefixes: Iterable[str] = ISOLATED_WORK_BRANCH_PREFIXES,
) -> RepoMutationSafety:
    """Evaluate whether mutating repo work may run in `cwd`.

    This guard is intentionally branch-scoped. Syncing or inspecting the default
    branch is still allowed elsewhere, but worker execution must happen from an
    isolated branch/worktree.
    """
    protected_tuple = tuple(b.strip() for b in protected_branches if b.strip())
    prefix_tuple = tuple(p for p in isolated_work_branch_prefixes if p)
    cwd_text = str(cwd)
    branch = get_current_branch(cwd)

    if branch is None:
        return RepoMutationSafety(
            cwd=cwd_text,
            current_branch=None,
            protected_branches=protected_tuple,
            isolated_work_branch_prefixes=prefix_tuple,
            status="blocked",
            reason="current git branch could not be determined",
            requires_isolated_worktree=True,
            recommended_action=(
                "switch to a task branch/worktree before running mutating worker work"
            ),
        )

    if is_protected_mutation_branch(branch, protected_tuple):
        return RepoMutationSafety(
            cwd=cwd_text,
            current_branch=branch,
            protected_branches=protected_tuple,
            isolated_work_branch_prefixes=prefix_tuple,
            status="blocked",
            reason=(
                f"current branch '{branch}' is protected from direct worker mutation"
            ),
            requires_isolated_worktree=True,
            recommended_action=(
                "create or resume an isolated worktree with "
                "`signposter worktree apply --apply`, then run with `--worktree`"
            ),
        )

    if is_isolated_work_branch(branch, prefix_tuple):
        reason = f"current branch '{branch}' matches an isolated work branch prefix"
    else:
        reason = f"current branch '{branch}' is not protected"

    return RepoMutationSafety(
        cwd=cwd_text,
        current_branch=branch,
        protected_branches=protected_tuple,
        isolated_work_branch_prefixes=prefix_tuple,
        status="allowed",
        reason=reason,
        requires_isolated_worktree=False,
        recommended_action="continue mutating work on this non-protected branch",
    )


def branch_exists(branch: str, cwd: str | Path = ".") -> bool:
    """Check if a local branch exists (read-only)."""
    if not branch:
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}"],
            cwd=str(cwd),
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def remote_branch_exists(
    branch: str,
    *,
    remote: str = "origin",
    cwd: str | Path = ".",
) -> bool:
    """Check if a remote-tracking branch exists in local git metadata."""
    if not branch:
        return False
    ref = f"refs/remotes/{remote}/{branch}"
    try:
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", ref],
            cwd=str(cwd),
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_branch_sync_status(
    cwd: str | Path = ".",
    *,
    branch: str = "main",
    remote: str = "origin",
) -> BranchSyncStatus:
    """Return read-only sync status for `branch` against `remote/branch`.

    This intentionally uses local git metadata only. It does not fetch, pull, or
    mutate refs; callers that need a fresh view must perform an explicit sync
    step before relying on this guard.
    """
    upstream = f"{remote}/{branch}"
    if not branch:
        return BranchSyncStatus(branch, upstream, None, None, "unknown", "branch is empty")

    def ref_exists(ref: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    if not ref_exists(branch):
        return BranchSyncStatus(
            branch,
            upstream,
            None,
            None,
            "unknown",
            f"local branch {branch} was not found",
        )
    if not ref_exists(upstream):
        return BranchSyncStatus(
            branch,
            upstream,
            None,
            None,
            "unknown",
            f"remote-tracking branch {upstream} was not found",
        )

    try:
        result = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", f"{branch}...{upstream}"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return BranchSyncStatus(
            branch,
            upstream,
            None,
            None,
            "unknown",
            f"failed to compare {branch} with {upstream}: {str(exc)[:120]}",
        )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:120]
        return BranchSyncStatus(
            branch,
            upstream,
            None,
            None,
            "unknown",
            f"failed to compare {branch} with {upstream}: {stderr or 'git returned non-zero'}",
        )

    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return BranchSyncStatus(
            branch,
            upstream,
            None,
            None,
            "unknown",
            "git rev-list did not return ahead/behind counts",
        )

    try:
        ahead = int(parts[0])
        behind = int(parts[1])
    except ValueError:
        return BranchSyncStatus(
            branch,
            upstream,
            None,
            None,
            "unknown",
            "git rev-list returned non-integer ahead/behind counts",
        )

    if ahead == 0 and behind == 0:
        status = "up-to-date"
        reason = f"{branch} matches {upstream}"
    elif ahead > 0 and behind == 0:
        status = "ahead"
        reason = f"{branch} is {ahead} commit(s) ahead of {upstream}"
    elif ahead == 0 and behind > 0:
        status = "behind"
        reason = f"{branch} is {behind} commit(s) behind {upstream}"
    else:
        status = "diverged"
        reason = f"{branch} has diverged from {upstream}: ahead={ahead}, behind={behind}"

    return BranchSyncStatus(branch, upstream, ahead, behind, status, reason)


def worktree_path_exists(path: str | Path) -> bool:
    """Check if a directory exists at the proposed worktree path."""
    try:
        return Path(path).expanduser().resolve().exists()
    except Exception:
        return False
