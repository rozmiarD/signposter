"""Minimal git working tree inspection helpers for Signposter worker isolation.

Used to guard against accidental edits in a dirty main working tree.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

# Paths that are expected to be dirty during Signposter operation
# (these are typically gitignored)
ALLOWED_DIRTY_PREFIXES = (
    "artifacts/",
    "signposter-work/",
    ".signposter/",
    ".openclaw/",
)


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


def worktree_path_exists(path: str | Path) -> bool:
    """Check if a directory exists at the proposed worktree path."""
    try:
        return Path(path).expanduser().resolve().exists()
    except Exception:
        return False
