"""Post-merge integration planning (HARDENING-021A).

Pure dry-run planning only. Connects a merged PR back to its associated Signposter issue.
No GitHub mutations of any kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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

    # Main CI status - conservative: we don't have a reliable cheap check here yet.
    # For now we leave it "unknown" unless we add a specific check later.
    # (Future work could query the merge commit status on main.)

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
