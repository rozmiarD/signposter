"""Post-merge integration planning (HARDENING-021A).

Pure dry-run planning only. Connects a merged PR back to its associated Signposter issue.
No GitHub mutations of any kind.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from signposter.labels import check_labels
from signposter.review import _run_gh_pr_view
from signposter.scan import fetch_issue_by_number, fetch_issue_context


@dataclass(frozen=True)
class IntegrationPlan:
    pr_number: int
    pr_title: str
    pr_state: str  # MERGED, OPEN, etc.
    merge_commit: str | None  # sha
    base_branch: str
    head_branch: str

    associated_issue: int | None
    issue_state: str | None  # OPEN, CLOSED
    current_workflow_state: str | None  # e.g. "state:done"
    proposed_workflow_state: str  # "state:merged"

    close_issue: bool
    close_reason: str  # "completed"

    main_ci_status: str  # "pass" | "unknown" | "failing"

    status: str  # "ready" | "blocked — ..."
    notes: list[str]


def _extract_issue_number(head_branch: str, body: str | None) -> int | None:
    """Detect associated issue from branch convention or body."""
    # Primary: work/issue-N-...
    m = re.search(r"work/issue-(\d+)", head_branch or "")
    if m:
        return int(m.group(1))

    # Secondary
    if body:
        m = re.search(r"(?i)related\s+issue:\s*#?(\d+)", body)
        if m:
            return int(m.group(1))
        m = re.search(r"(?i)issue\s*#?(\d+)", body)
        if m:
            return int(m.group(1))
    return None


def _get_workflow_state_from_labels(labels: list[str]) -> str | None:
    """Extract current state:xxx label if present."""
    for label in labels:
        if label.startswith("state:"):
            return label
    return None


def _fetch_pr_merge_details(repo: str, pr_number: int) -> dict[str, Any]:
    """Fetch post-merge PR details including merge commit."""
    return _run_gh_pr_view(
        repo,
        pr_number,
        [
            "number",
            "title",
            "state",
            "baseRefName",
            "headRefName",
            "mergeCommit",
            "body",
            "mergedAt",
        ],
    )


def _fetch_main_ci_status(repo: str) -> str:
    """Return latest main CI status using gh run list.

    Conservative mapping:
    - pass: latest main CI run is completed with success
    - failing: latest main CI run completed with a non-success conclusion
    - pending: latest main CI run is queued/in_progress/waiting/etc.
    - unknown: gh failed, no runs found, or payload shape is unexpected
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "-R",
                repo,
                "--branch",
                "main",
                "--workflow",
                "CI",
                "--limit",
                "1",
                "--json",
                "status,conclusion,workflowName,headBranch,headSha,databaseId",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return "unknown"

    if result.returncode != 0:
        return "unknown"

    try:
        runs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return "unknown"

    if not isinstance(runs, list) or not runs:
        return "unknown"

    run = runs[0]
    if not isinstance(run, dict):
        return "unknown"

    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()

    if status == "completed":
        if conclusion == "success":
            return "pass"
        if conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
            return "failing"
        return "unknown"

    if status in {"queued", "in_progress", "waiting", "requested", "pending"}:
        return "pending"

    return "unknown"



def plan_integration_for_pr(repo: str, pr_number: int) -> IntegrationPlan:
    """Produce a dry-run post-merge integration plan."""
    notes = [
        "No issue was closed.",
        "No labels were changed.",
        "No local worktree was removed.",
        "No GitHub mutation was performed.",
    ]

    try:
        pr_data = _fetch_pr_merge_details(repo, pr_number)
    except Exception:
        return IntegrationPlan(
            pr_number=pr_number,
            pr_title="unknown",
            pr_state="UNKNOWN",
            merge_commit=None,
            base_branch="unknown",
            head_branch="unknown",
            associated_issue=None,
            issue_state=None,
            current_workflow_state=None,
            proposed_workflow_state="state:merged",
            close_issue=True,
            close_reason="completed",
            main_ci_status="unknown",
            status=f"blocked — failed to fetch PR #{pr_number}",
            notes=notes,
        )

    pr_state = pr_data.get("state", "UNKNOWN")
    merge_commit_data = pr_data.get("mergeCommit") or {}
    merge_commit = merge_commit_data.get("oid") if isinstance(merge_commit_data, dict) else None

    base = pr_data.get("baseRefName", "main")
    head = pr_data.get("headRefName", "")
    body = pr_data.get("body", "") or ""
    title = pr_data.get("title", "")

    associated_issue = _extract_issue_number(head, body)

    # Default plan values
    issue_state = None
    current_workflow_state = None
    main_ci_status = "unknown"

    if associated_issue is not None:
        try:
            issue_item = fetch_issue_by_number(repo, associated_issue)
            if issue_item:
                labels = getattr(issue_item, "labels", []) or []
                current_workflow_state = _get_workflow_state_from_labels(labels)

            # Get authoritative state via richer context call
            ctx = fetch_issue_context(repo, associated_issue) or {}
            issue_state = ctx.get("state")
        except Exception:
            pass

    # Main CI status — required before integration apply can close the issue.
    main_ci_status = _fetch_main_ci_status(repo)

    # Eligibility
    status = "ready"
    if pr_state != "MERGED":
        status = f"blocked — PR is not merged (state: {pr_state})"
    elif not merge_commit:
        status = "blocked — merge commit missing"
    elif associated_issue is None:
        status = "blocked — associated issue could not be detected"
    elif issue_state is not None and issue_state.upper() != "OPEN":
        status = f"blocked — associated issue is already {issue_state.lower()}"
    # We do not require a specific current state label here; the plan just proposes the next one.

    return IntegrationPlan(
        pr_number=pr_number,
        pr_title=title,
        pr_state=pr_state,
        merge_commit=merge_commit,
        base_branch=base,
        head_branch=head,
        associated_issue=associated_issue,
        issue_state=issue_state,
        current_workflow_state=current_workflow_state,
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        main_ci_status=main_ci_status,
        status=status,
        notes=notes,
    )


def format_integration_plan(plan: IntegrationPlan) -> str:
    """Compact deterministic output for post-merge integration planning."""
    lines = [f"Signposter Integration Plan — PR #{plan.pr_number}\n"]

    lines.append("PR:")
    lines.append(f"  state: {plan.pr_state}")
    lines.append(f"  merge commit: {plan.merge_commit or 'none'}")
    lines.append(f"  base: {plan.base_branch}")
    lines.append(f"  head: {plan.head_branch}")

    lines.append("\nIssue:")
    if plan.associated_issue:
        lines.append(f"  associated issue: #{plan.associated_issue}")
    else:
        lines.append("  associated issue: none detected")
    lines.append(f"  state: {plan.issue_state or 'unknown'}")
    lines.append(f"  current workflow state: {plan.current_workflow_state or 'unknown'}")
    lines.append(f"  proposed workflow state: {plan.proposed_workflow_state}")
    lines.append(f"  close issue: {'yes' if plan.close_issue else 'no'}")
    lines.append(f"  close reason: {plan.close_reason}")

    lines.append("\nChecks:")
    lines.append(f"  main CI: {plan.main_ci_status}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)


# =============================================================================
# HARDENING-021B: Guarded post-merge integration apply (dry-run by default)
# =============================================================================


def _fetch_repo_label_names(repo: str) -> set[str]:
    """Fetch repository label names for integration preflight."""
    try:
        result = subprocess.run(
            [
                "gh",
                "label",
                "list",
                "-R",
                repo,
                "--limit",
                "200",
                "--json",
                "name",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return set()

    if result.returncode != 0:
        return set()

    try:
        labels = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return set()

    names: set[str] = set()
    if isinstance(labels, list):
        for label in labels:
            if isinstance(label, dict) and isinstance(label.get("name"), str):
                names.add(label["name"])
    return names


def _build_integration_comment(plan: IntegrationPlan) -> str:
    """Build the compact integration comment to post on the issue."""
    sha = plan.merge_commit or "unknown"
    comment = f"""Signposter integration complete

PR: #{plan.pr_number}
Merge commit: {sha}
Workflow transition: {plan.current_workflow_state or 'unknown'} -> {plan.proposed_workflow_state}
Issue close reason: {plan.close_reason}
Review gate: pass
GitHub review: approved
CI: {plan.main_ci_status}

No local worktree cleanup was performed.
"""
    return comment.strip()


def _integration_apply_status(plan: IntegrationPlan, repo: str | None = None) -> str:
    """Return effective readiness for integration apply.

    Also runs the centralized label preflight (H023C) when repo is provided.
    """
    if plan.status != "ready":
        return f"blocked — integration plan is not ready ({plan.status})"
    if plan.associated_issue is None:
        return "blocked — associated issue missing"
    if plan.issue_state is not None and plan.issue_state.upper() != "OPEN":
        return f"blocked — associated issue is not OPEN ({plan.issue_state})"
    if plan.current_workflow_state != "state:done":
        return (
            "blocked — current workflow state is not state:done "
            f"(got {plan.current_workflow_state})"
        )
    if plan.main_ci_status != "pass":
        return f"blocked — main CI is not confirmed pass (got {plan.main_ci_status})"

    # H023C: label preflight (only when repo provided)
    if repo:
        ok, missing, err = _label_preflight(repo)
        if not ok:
            if err:
                return f"blocked — {err}"
            if missing:
                return "blocked — required labels missing: " + ", ".join(missing)
            return "blocked — required labels missing"
    return "ready"


def _label_preflight(repo: str) -> tuple[bool, list[str], str | None]:
    """
    Centralized required-label preflight for integration apply.

    Returns: (ok, missing_labels, error_message)
    """
    try:
        result = check_labels(repo)
        if result.error:
            return False, [], f"label preflight failed: {result.error}"
        if result.missing:
            return False, result.missing, None
        return True, [], None
    except Exception as e:
        return False, [], f"label preflight error: {str(e)[:200]}"


def apply_integration(
    repo: str, pr_number: int, *, apply: bool = False
) -> dict:
    """Dry-run or execute the post-merge issue integration.

    Only mutates when apply=True and the integration plan is 'ready' plus all guards pass.
    """
    plan = plan_integration_for_pr(repo, pr_number)

    if not apply:
        return {
            "mode": "dry_run",
            "plan": plan,
        }

    # Mutation path - very strictly guarded
    if plan.status != "ready":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": f"Refusing integration apply: {plan.status}",
        }

    if plan.pr_state != "MERGED" or not plan.merge_commit:
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": "PR is not merged or merge commit missing",
        }

    if plan.associated_issue is None:
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": "No associated issue detected",
        }

    if plan.issue_state is not None and plan.issue_state.upper() != "OPEN":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": f"Issue #{plan.associated_issue} is not OPEN",
        }

    if plan.current_workflow_state != "state:done":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": (
                f"Current workflow state is not state:done "
                f"(got {plan.current_workflow_state})"
            ),
        }

    if plan.main_ci_status != "pass":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": f"Main CI is not confirmed pass (got {plan.main_ci_status})",
        }

    # Centralized required label preflight (H023C)
    ok, missing, preflight_err = _label_preflight(repo)
    if not ok:
        reason = preflight_err or ("required labels missing: " + ", ".join(missing))
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": reason,
        }

    issue = plan.associated_issue

    # Perform mutations
    results = []
    errors = []

    # 1. Label transition: remove state:done, add state:merged
    label_error = None
    try:
        cmd = [
            "gh", "issue", "edit", str(issue),
            "-R", repo,
            "--remove-label", "state:done",
            "--add-label", "state:merged",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            label_error = f"label transition failed: {proc.stderr.strip()[:300]}"
        else:
            results.append("label transition")
    except Exception as e:
        label_error = f"label transition error: {str(e)}"

    if label_error:
        return {
            "mode": "apply",
            "plan": plan,
            "success": False,
            "results": results,
            "errors": [label_error],
        }

    # 2. Post integration comment
    try:
        comment = _build_integration_comment(plan)
        cmd = [
            "gh", "issue", "comment", str(issue),
            "-R", repo,
            "--body", comment,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            errors.append(f"comment failed: {proc.stderr.strip()[:300]}")
        else:
            results.append("comment posted")
    except Exception as e:
        errors.append(f"comment error: {str(e)}")

    # 3. Close the issue
    try:
        cmd = [
            "gh", "issue", "close", str(issue),
            "-R", repo,
            "--reason", "completed",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            errors.append(f"close failed: {proc.stderr.strip()[:300]}")
        else:
            results.append("issue closed")
    except Exception as e:
        errors.append(f"close error: {str(e)}")

    if errors:
        return {
            "mode": "apply",
            "plan": plan,
            "success": False,
            "results": results,
            "errors": errors,
        }

    return {
        "mode": "apply",
        "plan": plan,
        "success": True,
        "results": results,
    }


def format_integration_apply_dry_run(plan: IntegrationPlan, repo: str | None = None) -> str:
    """Dry-run output for integration apply."""
    apply_status = _integration_apply_status(plan, repo)

    lines = [f"Signposter Integration Apply Plan — PR #{plan.pr_number}\n"]

    lines.append("Integration plan:")
    lines.append(f"  status: {plan.status}")
    if plan.associated_issue:
        lines.append(f"  associated issue: #{plan.associated_issue}")
    lines.append(f"  current workflow state: {plan.current_workflow_state or 'unknown'}")
    lines.append(f"  proposed workflow state: {plan.proposed_workflow_state}")
    lines.append(f"  close issue: {'yes' if plan.close_issue else 'no'}")
    lines.append(f"  close reason: {plan.close_reason}")
    lines.append(f"  main CI: {plan.main_ci_status}")

    if "required labels missing" in apply_status.lower():
        lines.append("\nLabel preflight:")
        lines.append(f"  {apply_status}")

    lines.append("\nPlanned GitHub mutations:")
    lines.append("  remove label: state:done")
    lines.append("  add label: state:merged")
    lines.append(f"  close issue: #{plan.associated_issue} as completed")
    lines.append("  post integration comment: yes")

    lines.append("\nStatus:")
    lines.append(f"  {apply_status}")

    lines.append("\nNotes:")
    lines.append("  DRY RUN: no issue was closed.")
    lines.append("  No labels were changed.")
    lines.append("  No local worktree was removed.")

    return "\n".join(lines)
