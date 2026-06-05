"""Local worktree + branch cleanup for merged PRs (HARDENING-021C).

Pure local cleanup only. No GitHub mutations of any kind.
Plan is always read-only. Apply is guarded by explicit --apply.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signposter.pr_linkage import detect_pr_issue_linkage
from signposter.review import _run_gh_pr_view
from signposter.scan import fetch_issue_context


@dataclass(frozen=True)
class CleanupPlan:
    """Dry-run plan for local cleanup of a merged PR's worktree and branch."""

    pr_number: int
    pr_state: str  # MERGED | OPEN | CLOSED | UNKNOWN
    head_branch: str

    associated_issue: int | None
    issue_state: str | None  # OPEN | CLOSED | ...
    has_state_merged_label: bool

    expected_worktree_path: str
    worktree_exists: bool

    local_branch: str
    local_branch_exists: bool

    status: str  # "ready" | "completed" | "blocked — ..."
    notes: list[str]


def _extract_issue_number(head_branch: str, body: str | None) -> int | None:
    """Detect associated issue from branch convention (primary) or body (secondary)."""
    return detect_pr_issue_linkage(head_branch, body).associated_issue


def _compute_expected_worktree(issue_number: int) -> str:
    """Follow existing convention: ../signposter-work/N"""
    return str(Path("..") / "signposter-work" / str(issue_number))


def _worktree_exists(path: str) -> bool:
    try:
        return Path(path).expanduser().resolve().exists()
    except Exception:
        return False


def _local_branch_exists(branch: str) -> bool:
    if not branch:
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def plan_cleanup_for_pr(repo: str, pr_number: int) -> CleanupPlan:
    """Produce a read-only CleanupPlan for a PR.

    Only inspects PR + associated issue. No mutations.
    """
    notes: list[str] = [
        "No local worktree was removed.",
        "No local branch was deleted.",
        "No GitHub mutation was performed.",
    ]

    try:
        pr_data: dict[str, Any] = _run_gh_pr_view(
            repo,
            pr_number,
            ["number", "state", "headRefName", "body"],
        )
    except Exception as e:
        return CleanupPlan(
            pr_number=pr_number,
            pr_state="UNKNOWN",
            head_branch="",
            associated_issue=None,
            issue_state=None,
            has_state_merged_label=False,
            expected_worktree_path="",
            worktree_exists=False,
            local_branch="",
            local_branch_exists=False,
            status=f"blocked — failed to fetch PR #{pr_number}: {str(e)[:120]}",
            notes=notes,
        )

    pr_state = pr_data.get("state", "UNKNOWN")
    head = pr_data.get("headRefName", "") or ""
    body = pr_data.get("body") or ""

    linkage = detect_pr_issue_linkage(head, body)
    associated_issue = linkage.associated_issue

    issue_state: str | None = None
    has_state_merged_label = False

    if associated_issue is not None:
        try:
            ctx = fetch_issue_context(repo, associated_issue) or {}
            issue_state = ctx.get("state")

            labels = ctx.get("labels") or []
            for label in labels:
                if isinstance(label, dict) and label.get("name") == "state:merged":
                    has_state_merged_label = True
                    break
                if isinstance(label, str) and label == "state:merged":
                    has_state_merged_label = True
                    break
        except Exception:
            pass

    local_branch = head
    expected_worktree = ""
    worktree_exists = False
    local_branch_exists = False

    if associated_issue is not None:
        expected_worktree = _compute_expected_worktree(associated_issue)
        worktree_exists = _worktree_exists(expected_worktree)
        local_branch_exists = _local_branch_exists(local_branch)

    # Eligibility rules (strict)
    status = "ready"
    if pr_state != "MERGED":
        status = f"blocked — PR is not merged (state: {pr_state})"
    elif linkage.ambiguous:
        status = f"blocked — {linkage.reason}"
    elif associated_issue is None:
        status = "blocked — associated issue could not be detected from head branch"
    elif issue_state is None or issue_state.upper() != "CLOSED":
        status = (
            f"blocked — associated issue #{associated_issue} is not CLOSED "
            f"(state: {issue_state})"
        )
    elif not has_state_merged_label:
        status = f"blocked — issue #{associated_issue} does not have label state:merged"
    elif not worktree_exists and not local_branch_exists:
        # Worktree and local branch already gone -> no-op completed (not a failure)
        status = "completed"
        notes = [
            "Worktree already absent.",
            "Local branch already absent.",
            "No local worktree was removed.",
            "No local branch was deleted.",
            "No GitHub mutation was performed.",
        ]
    elif not worktree_exists:
        # Worktree already gone, but branch remains. Cleanup can finish locally.
        status = "ready"
        notes = [
            "Worktree already absent.",
            "Local branch is still present and can be deleted.",
            "No local worktree was removed.",
            "No local branch was deleted.",
            "No GitHub mutation was performed.",
        ]
    else:
        status = "ready"

    return CleanupPlan(
        pr_number=pr_number,
        pr_state=pr_state,
        head_branch=head,
        associated_issue=associated_issue,
        issue_state=issue_state,
        has_state_merged_label=has_state_merged_label,
        expected_worktree_path=expected_worktree,
        worktree_exists=worktree_exists,
        local_branch=local_branch,
        local_branch_exists=local_branch_exists,
        status=status,
        notes=notes,
    )


def format_cleanup_plan(plan: CleanupPlan) -> str:
    """Compact deterministic output for `cleanup plan`."""
    lines = [f"Signposter Cleanup Plan — PR #{plan.pr_number}\n"]

    lines.append("PR:")
    lines.append(f"  state: {plan.pr_state}")
    lines.append(f"  head: {plan.head_branch or 'unknown'}")
    lines.append("  remote branch exists: no")

    lines.append("\nIssue:")
    if plan.associated_issue:
        lines.append(f"  associated issue: #{plan.associated_issue}")
    else:
        lines.append("  associated issue: none detected")
    lines.append(f"  state: {plan.issue_state or 'unknown'}")
    ws = "state:merged" if plan.has_state_merged_label else "unknown"
    lines.append(f"  workflow state: {ws}")

    lines.append("\nLocal cleanup:")
    lines.append(f"  worktree path: {plan.expected_worktree_path or 'n/a'}")
    lines.append(f"  worktree exists: {'yes' if plan.worktree_exists else 'no'}")
    lines.append(f"  local branch: {plan.local_branch or 'n/a'}")
    lines.append(f"  local branch exists: {'yes' if plan.local_branch_exists else 'no'}")
    lines.append(f"  cleanup eligible: {'yes' if plan.status == 'ready' else 'no'}")

    pending = _format_pending_local_cleanup(plan)
    if pending:
        lines.append("\nPending local cleanup:")
        lines.extend(pending)

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)


def _format_pending_local_cleanup(plan: CleanupPlan) -> list[str]:
    """Return operator-facing pending cleanup details for ready local cleanup."""
    if plan.status != "ready":
        return []
    if not plan.worktree_exists and not plan.local_branch_exists:
        return []

    stale_items: list[str] = []
    if plan.worktree_exists:
        stale_items.append(f"worktree: {plan.expected_worktree_path or 'n/a'}")
    if plan.local_branch_exists:
        stale_items.append(f"local branch: {plan.local_branch or 'n/a'}")

    if not stale_items:
        return []

    lines = [
        "  category: stale local worker state",
        "  status: pending — local cleanup remains",
        "  reason: PR is merged and issue integration is complete, but local cleanup remains.",
    ]
    for item in stale_items:
        lines.append(f"  pending: {item}")
    lines.extend(
        [
            "  next command: signposter cleanup apply --repo <repo> "
            f"--pr {plan.pr_number} --apply",
            "  safety: cleanup apply is local-only and remains guarded by --apply.",
        ]
    )
    return lines


def _is_already_fully_cleaned(plan: CleanupPlan) -> bool:
    """
    H024B: Returns True only when the entire cleanup lifecycle is already complete.
    """
    return (
        plan.status == "completed"
        and not plan.worktree_exists
        and not plan.local_branch_exists
    )


def _needs_post_integration_refresh(plan: CleanupPlan) -> bool:
    """Return True when cleanup should re-read fresh lifecycle state once."""
    if plan.pr_state != "MERGED" or plan.associated_issue is None:
        return False
    return (
        "is not CLOSED" in plan.status
        or "does not have label state:merged" in plan.status
    )


def format_cleanup_apply_dry_run(plan: CleanupPlan) -> str:
    """Dry-run output for `cleanup apply` (default)."""
    lines = [f"Signposter Cleanup Apply Plan — PR #{plan.pr_number}\n"]

    lines.append("Cleanup plan:")
    lines.append(f"  status: {plan.status}")
    lines.append(f"  worktree path: {plan.expected_worktree_path or 'n/a'}")
    lines.append(f"  remove worktree: {'yes' if plan.worktree_exists else 'no'}")
    lines.append(f"  delete local branch if present: {'yes' if plan.local_branch_exists else 'no'}")

    pending = _format_pending_local_cleanup(plan)
    if pending:
        lines.append("\nPending local cleanup:")
        lines.extend(pending)

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    lines.append("\nNotes:")
    lines.append("  DRY RUN: no local worktree was removed.")
    lines.append("  No local branch was deleted.")
    lines.append("  No GitHub mutation was performed.")

    return "\n".join(lines)


def apply_cleanup(
    repo: str, pr_number: int, *, apply: bool = False
) -> dict:
    """Execute (or dry-run) local cleanup.

    Only performs git worktree remove + branch delete when apply=True and plan.status == "ready".
    Strict ordering: worktree first. If worktree removal fails, do NOT touch the branch.
    """
    plan = plan_cleanup_for_pr(repo, pr_number)

    if not apply:
        return {
            "mode": "dry_run",
            "plan": plan,
            "would_execute": plan.status == "ready",
            "already_completed": plan.status == "completed",
        }

    if _needs_post_integration_refresh(plan):
        refreshed_plan = plan_cleanup_for_pr(repo, pr_number)
        if refreshed_plan.status == "ready" or refreshed_plan.status == "completed":
            plan = refreshed_plan

    # Mutation path — strictly guarded
    if _is_already_fully_cleaned(plan):
        return {
            "mode": "apply_completed",
            "plan": plan,
            "success": True,
            "results": ["cleanup already completed"],
            "branch_deleted": False,
        }

    if plan.status != "ready":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": f"Refusing cleanup apply: plan status is '{plan.status}'",
        }

    if not plan.worktree_exists and not plan.local_branch_exists:
        # Should not happen if plan was ready, but defensive
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": "Worktree and local branch do not exist (plan status inconsistency)",
        }

    results: list[str] = []
    errors: list[str] = []

    worktree_path = plan.expected_worktree_path
    branch = plan.local_branch

    # 1. Remove worktree first (must succeed before touching branch)
    if plan.worktree_exists:
        try:
            cmd = ["git", "worktree", "remove", "--force", worktree_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()[:400]
                errors.append(f"git worktree remove failed: {stderr}")
            else:
                results.append(f"removed worktree: {worktree_path}")
        except Exception as e:
            errors.append(f"worktree removal error: {str(e)[:200]}")
    else:
        results.append("worktree already absent")

    if errors:
        # Fail fast — do not attempt branch deletion
        return {
            "mode": "apply",
            "plan": plan,
            "success": False,
            "results": results,
            "errors": errors,
            "partial": True,
        }

    # 2. Delete local branch only if it still exists (and only the exact PR head branch)
    branch_deleted = False
    if branch and _local_branch_exists(branch):
        try:
            cmd = ["git", "branch", "-D", branch]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()[:400]
                errors.append(f"git branch -D failed: {stderr}")
            else:
                results.append(f"deleted local branch: {branch}")
                branch_deleted = True
        except Exception as e:
            errors.append(f"branch deletion error: {str(e)[:200]}")

    if errors:
        return {
            "mode": "apply",
            "plan": plan,
            "success": False,
            "results": results,
            "errors": errors,
            "partial": True,
        }

    return {
        "mode": "apply",
        "plan": plan,
        "success": True,
        "results": results,
        "branch_deleted": branch_deleted,
    }


def format_cleanup_apply_result(result: dict) -> str:
    """Final output after real apply (or blocked)."""
    plan: CleanupPlan = result.get("plan")
    pr = plan.pr_number if plan else "?"

    if result.get("mode") == "apply_blocked":
        err = result.get("error", "unknown")
        lines = [f"Signposter Cleanup Apply — PR #{pr}\n"]
        lines.append("Status: blocked")
        lines.append(f"  reason: {err}")
        lines.append("")
        lines.append("Notes:")
        lines.append("  No local worktree was removed.")
        lines.append("  No local branch was deleted.")
        lines.append("  No GitHub mutation was performed.")
        return "\n".join(lines)

    if result.get("mode") == "apply_completed":
        lines = [f"Signposter Cleanup Apply — PR #{pr}\n"]
        lines.append("Local cleanup:")
        lines.append("  status: already completed")
        lines.append("  worktree: already absent")
        lines.append("  local branch: already absent")

        lines.append("\nStatus:")
        lines.append("  completed")

        lines.append("\nNotes:")
        lines.append("  No local worktree was removed.")
        lines.append("  No local branch was deleted.")
        lines.append("  No GitHub mutation was performed.")
        lines.append("  Issue was not modified.")
        lines.append("  PR was not modified.")

        return "\n".join(lines)

    if result.get("success"):
        results = result.get("results", [])
        branch_deleted = result.get("branch_deleted", False)

        lines = [f"Signposter Cleanup Apply — PR #{pr}\n"]
        lines.append("Local cleanup:")

        for r in results:
            lines.append(f"  {r}")

        if not any("worktree" in r for r in results):
            lines.append("  removed worktree: (none — already absent)")

        bd = "yes" if branch_deleted else "no (was not present)"
        lines.append(f"  deleted local branch: {bd}")

        lines.append("\nStatus:")
        lines.append("  completed")

        lines.append("\nNotes:")
        lines.append("  No GitHub mutation was performed.")
        lines.append("  Issue was not modified.")
        lines.append("  PR was not modified.")

        return "\n".join(lines)

    # Partial or failed
    errors = result.get("errors", [])
    results = result.get("results", [])
    lines = [f"Signposter Cleanup Apply — PR #{pr}\n"]
    lines.append("Status: failed / partial")
    if results:
        lines.append("Completed steps:")
        for r in results:
            lines.append(f"  {r}")
    if errors:
        lines.append("Errors:")
        for e in errors:
            lines.append(f"  {e}")
    lines.append("\nNotes:")
    lines.append("  No GitHub mutation was performed.")
    lines.append("  Issue was not modified.")
    lines.append("  PR was not modified.")
    return "\n".join(lines)
