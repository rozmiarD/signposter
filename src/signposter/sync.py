"""Guarded local repository sync/rebase commands (HARDENING-024E).

Read-only plan by default. Apply requires explicit --apply + --rebase.
Never performs git push. Never touches GitHub.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SyncPlan:
    """Plan for repository synchronization."""

    repo_path: str
    current_branch: str
    upstream: str
    working_tree_clean: bool

    local_head: str
    upstream_head: str

    ahead: int
    behind: int
    divergence_status: str  # "up-to-date" | "fast-forward" | "diverged" | "ahead"

    recommended_action: str  # "none" | "pull" | "rebase" | "push-required"
    command_preview: str

    status: str  # "ready" | "completed" | "blocked — ..."
    notes: list[str]


def _git(args: list[str], cwd: str | Path, timeout: int = 30) -> tuple[int, str, str]:
    """Run git command and return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:
        return 1, "", str(e)[:300]


def _current_branch(cwd: str | Path) -> str:
    code, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return out if code == 0 else ""


def _working_tree_dirty(cwd: str | Path) -> bool:
    code, out, _ = _git(["status", "--porcelain"], cwd)
    return code == 0 and bool(out.strip())


def _fetch_origin(cwd: str | Path) -> tuple[bool, str]:
    """Safe fetch. Returns (success, error_message)."""
    code, _, stderr = _git(["fetch", "origin"], cwd, timeout=60)
    if code != 0:
        return False, (stderr or "fetch failed")[:400]
    return True, ""


def _ahead_behind(cwd: str | Path, upstream: str = "origin/main") -> tuple[int, int]:
    """Return (ahead, behind) using rev-list --left-right --count."""
    code, out, _ = _git(
        ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], cwd
    )
    if code != 0:
        return 0, 0
    try:
        left, right = out.strip().split()
        return int(left), int(right)
    except Exception:
        return 0, 0


def _get_head_sha(cwd: str | Path, ref: str = "HEAD") -> str:
    code, out, _ = _git(["rev-parse", ref], cwd)
    return out[:12] if code == 0 else "unknown"


def plan_sync(repo_path: str | Path) -> SyncPlan:
    """Produce a read-only sync plan."""
    cwd = Path(repo_path).resolve()
    notes = [
        "No rebase was performed.",
        "No push was performed.",
        "No GitHub mutation was performed.",
    ]

    current_branch = _current_branch(cwd)
    if not current_branch:
        return SyncPlan(
            repo_path=str(cwd),
            current_branch="unknown",
            upstream="origin/main",
            working_tree_clean=True,
            local_head="unknown",
            upstream_head="unknown",
            ahead=0,
            behind=0,
            divergence_status="unknown",
            recommended_action="none",
            command_preview="",
            status="blocked — could not determine current branch",
            notes=notes,
        )

    if current_branch != "main":
        return SyncPlan(
            repo_path=str(cwd),
            current_branch=current_branch,
            upstream="origin/main",
            working_tree_clean=True,
            local_head="unknown",
            upstream_head="unknown",
            ahead=0,
            behind=0,
            divergence_status="unknown",
            recommended_action="none",
            command_preview="",
            status=f"blocked — current branch is {current_branch}, not main",
            notes=notes,
        )

    working_tree_clean = not _working_tree_dirty(cwd)

    # Safe fetch (allowed in plan)
    fetch_ok, fetch_err = _fetch_origin(cwd)
    if not fetch_ok:
        return SyncPlan(
            repo_path=str(cwd),
            current_branch=current_branch,
            upstream="origin/main",
            working_tree_clean=working_tree_clean,
            local_head="unknown",
            upstream_head="unknown",
            ahead=0,
            behind=0,
            divergence_status="unknown",
            recommended_action="none",
            command_preview="",
            status=f"blocked — fetch failed: {fetch_err}",
            notes=notes,
        )

    upstream = "origin/main"
    local_head = _get_head_sha(cwd, "HEAD")
    upstream_head = _get_head_sha(cwd, upstream)

    ahead, behind = _ahead_behind(cwd, upstream)

    if ahead == 0 and behind == 0:
        status = "completed"
        recommended_action = "none"
        command_preview = ""
        divergence_status = "up-to-date"
    elif ahead == 0 and behind > 0:
        status = "ready"
        recommended_action = "pull"
        command_preview = "git pull origin main"
        divergence_status = "fast-forward"
    elif ahead > 0 and behind == 0:
        status = "ready"
        recommended_action = "push-required"
        command_preview = "git push (manual after review)"
        divergence_status = "ahead"
    else:
        status = "ready"
        recommended_action = "rebase"
        command_preview = "git pull --rebase origin main"
        divergence_status = "diverged"

    if not working_tree_clean:
        status = "blocked — working tree has uncommitted changes"

    return SyncPlan(
        repo_path=str(cwd),
        current_branch=current_branch,
        upstream=upstream,
        working_tree_clean=working_tree_clean,
        local_head=local_head,
        upstream_head=upstream_head,
        ahead=ahead,
        behind=behind,
        divergence_status=divergence_status,
        recommended_action=recommended_action,
        command_preview=command_preview,
        status=status,
        notes=notes,
    )


def format_sync_plan(plan: SyncPlan) -> str:
    """Compact deterministic output for sync plan."""
    lines = [f"Signposter Sync Plan — {plan.repo_path}\n"]

    lines.append("Repository:")
    lines.append(f"  path: {plan.repo_path}")
    lines.append(f"  current branch: {plan.current_branch}")
    lines.append(f"  upstream: {plan.upstream}")
    lines.append(f"  working tree: {'clean' if plan.working_tree_clean else 'dirty'}")

    lines.append("\nRemote:")
    lines.append("  fetch: performed")
    lines.append(f"  local HEAD: {plan.local_head}")
    lines.append(f"  upstream HEAD: {plan.upstream_head}")

    lines.append("\nDivergence:")
    lines.append(f"  ahead: {plan.ahead}")
    lines.append(f"  behind: {plan.behind}")
    lines.append(f"  status: {plan.divergence_status}")

    lines.append("\nRecommendation:")
    lines.append(f"  action: {plan.recommended_action}")
    if plan.command_preview:
        lines.append(f"  command preview: {plan.command_preview}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)


def apply_sync(
    repo_path: str | Path, *, apply: bool = False, rebase: bool = False
) -> dict[str, Any]:
    """Execute (or dry-run) the sync operation."""
    plan = plan_sync(repo_path)

    if not apply:
        return {"mode": "dry_run", "plan": plan}

    if plan.status != "ready":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": f"Refusing sync apply: {plan.status}",
        }

    if not rebase:
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": "Refusing sync apply: --rebase flag is required for rebase operations",
        }

    if plan.recommended_action not in ("rebase", "pull"):
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": (
                "Refusing sync apply: no rebase/pull recommended "
                f"(action={plan.recommended_action})"
            ),
        }

    cwd = Path(repo_path).resolve()

    # Run git pull --rebase origin main
    cmd = ["git", "pull", "--rebase", "origin", "main"]
    code, stdout, stderr = _git(cmd, cwd, timeout=120)

    if code != 0:
        return {
            "mode": "apply",
            "plan": plan,
            "success": False,
            "error": (stderr or stdout or "rebase failed")[:500],
            "commands_run": [" ".join(cmd)],
        }

    return {
        "mode": "apply",
        "plan": plan,
        "success": True,
        "commands_run": [" ".join(cmd)],
    }


def format_sync_apply_result(result: dict[str, Any]) -> str:
    """Format result of sync apply or blocked attempt."""
    plan: SyncPlan = result.get("plan")
    repo = plan.repo_path if plan else "?"

    if result.get("mode") == "dry_run":
        lines = [f"Signposter Sync Apply Plan — {repo}\n"]
        lines.append("Sync plan:")
        lines.append(f"  status: {plan.status}")
        lines.append(f"  action: {plan.recommended_action}")
        if plan.command_preview:
            lines.append(f"  command preview: {plan.command_preview}")
        lines.append("\nStatus:")
        lines.append(f"  {plan.status}")
        lines.append("\nNotes:")
        for note in plan.notes:
            lines.append(f"  {note}")
        return "\n".join(lines)

    if result.get("mode") == "apply_blocked":
        err = result.get("error", "unknown")
        lines = [f"Signposter Sync Apply — {repo}\n"]
        lines.append("Status: blocked")
        lines.append(f"  reason: {err}")
        lines.append("\nNotes:")
        lines.append("  No rebase was performed.")
        lines.append("  No push was performed.")
        lines.append("  No GitHub mutation was performed.")
        return "\n".join(lines)

    if result.get("success"):
        lines = [f"Signposter Sync Apply — {repo}\n"]
        lines.append("Local sync:")
        lines.append("  status: completed")
        lines.append("  rebase: performed")
        lines.append("\nStatus:")
        lines.append("  completed")
        lines.append("\nNotes:")
        lines.append("  No push was performed.")
        lines.append("  No GitHub mutation was performed.")
        return "\n".join(lines)

    # Failure case
    err = result.get("error", "unknown")
    lines = [f"Signposter Sync Apply — {repo}\n"]
    lines.append("Status: failed")
    lines.append(f"  error: {err}")
    lines.append("\nNotes:")
    lines.append("  No push was performed.")
    lines.append("  No GitHub mutation was performed.")
    return "\n".join(lines)
