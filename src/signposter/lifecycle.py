"""Lifecycle status command (HARDENING-022A).

Read-only cross-phase summary for an issue or PR.
No mutations of any kind.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signposter.cleanup import _extract_issue_number, _local_branch_exists, _worktree_exists
from signposter.review import _run_gh_pr_view
from signposter.scan import fetch_issue_by_number, fetch_issue_context


@dataclass(frozen=True)
class LifecycleStatus:
    """Compact read-only lifecycle summary for an issue/PR."""

    # Input
    query_issue: int | None
    query_pr: int | None

    # Issue
    issue_number: int | None
    issue_state: str | None  # OPEN / CLOSED / ...
    workflow_state: str | None  # state:merged etc.
    phase: str | None
    risk: str | None
    role: str | None
    area: str | None

    # PR
    pr_number: int | None
    pr_state: str | None  # MERGED / OPEN / ...
    pr_base: str | None
    pr_head: str | None
    pr_merged: bool
    merge_commit: str | None

    # Review
    review_decision: str | None  # APPROVED / ...
    has_non_author_approval: bool
    reviewer_login: str | None

    # Integration
    integrated: bool
    issue_closed: bool

    # Cleanup (local)
    expected_worktree: str | None
    worktree_exists: bool
    local_branch_exists: bool
    cleanup_complete: bool

    # Linkage (H022C)
    linkage_source: str | None  # branch-pattern | pr-body-related-issue | closing-keyword | ...
    linkage_confidence: str | None  # high | medium | low
    formal_github_development_link: str | None  # yes | no | unknown
    auto_close_keyword: bool

    # Overall
    status: str
    # "complete" | "incomplete — <reason>" |
    # "incomplete — associated ... could not be detected"
    notes: list[str]


WORKFLOW_CATEGORIES = ("state:", "phase:", "risk:", "role:", "area:")


def _extract_workflow_labels(labels: list[str]) -> dict[str, str]:
    """Return the last seen label for each category (state/phase/risk/role/area)."""
    result: dict[str, str] = {}
    for label in labels or []:
        for prefix in WORKFLOW_CATEGORIES:
            if label.startswith(prefix):
                result[prefix.rstrip(":")] = label
    return result


def _fetch_pr_details(repo: str, pr: int) -> dict[str, Any]:
    """Fetch minimal PR fields needed for lifecycle."""
    return _run_gh_pr_view(
        repo,
        pr,
        ["number", "state", "baseRefName", "headRefName", "mergeCommit", "body", "reviews"],
    )


def _fetch_issue_labels_and_state(repo: str, issue: int) -> tuple[list[str], str | None]:
    """Return (labels, state) for an issue."""
    item = fetch_issue_by_number(repo, issue)
    if not item:
        return [], None

    ctx = fetch_issue_context(repo, issue) or {}
    state = ctx.get("state")

    labels: list[str] = []
    if item and item.labels:
        labels = list(item.labels)
    elif ctx.get("labels"):
        for lbl in ctx["labels"]:
            if isinstance(lbl, dict) and lbl.get("name"):
                labels.append(lbl["name"])
            elif isinstance(lbl, str):
                labels.append(lbl)

    return labels, state


def _contains_auto_close_keyword(text: str) -> bool:
    """
    Case-insensitive detection of *intentional* auto-close keywords.

    Only matches when the keyword appears to be referencing an issue
    (e.g. "Closes #4", "Fixes #123", "Resolves issue #4").

    This prevents false positives from words like "close" appearing
    in normal prose ("integration/close policy", "close the loop", etc).
    """
    if not text:
        return False

    # Match keyword followed by optional "issue" and then #number or number
    pattern = re.compile(
        r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)"
        r"(?:s|d)?(?:\s+issue)?\s*#?\d+",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def _detect_link_source(
    *, pr_head: str | None, body: str | None, detected_from: str
) -> tuple[str, str]:
    """
    Return (source, confidence).

    Rules (H022C):
    - branch-pattern: head matches work/issue-N-* → high
    - closing-keyword: body contains Closes/Fixes/Resolves → high
    - pr-body-related-issue: body contains "Related issue: #N" → medium
    - detected-pr-search: issue → PR discovery via search → medium
    - unknown: low
    """
    body = body or ""

    if pr_head and re.search(r"work/issue-\d+", pr_head):
        return "branch-pattern", "high"

    if _contains_auto_close_keyword(body):
        return "closing-keyword", "high"

    if re.search(r"Related issue:\s*#?\d+", body, re.IGNORECASE):
        return "pr-body-related-issue", "medium"

    if detected_from == "issue-search":
        return "detected-pr-search", "medium"

    return "unknown", "low"


def _detect_associated_pr_from_issue(repo: str, issue: int) -> int | None:
    """Best-effort: look for a merged PR that mentions the issue in body or head branch."""
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list", "-R", repo,
                "--state", "merged",
                "--json", "number,headRefName,body",
                "--limit", "50",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        prs = __import__("json").loads(result.stdout or "[]")
        for p in prs:
            head = p.get("headRefName", "")
            body = p.get("body", "") or ""
            if re.search(rf"work/issue-{issue}\b", head) or re.search(rf"#{issue}\b", body):
                return int(p["number"])
    except Exception:
        pass
    return None


def _get_non_author_approval(reviews: list[dict]) -> tuple[bool, str | None]:
    """Return (has_non_author_approval, reviewer_login) from reviews list."""
    if not reviews:
        return False, None
    for r in reviews:
        if not isinstance(r, dict):
            continue
        state = (r.get("state") or "").upper()
        author = r.get("author") or {}
        login = author.get("login") if isinstance(author, dict) else None
        if state == "APPROVED" and login:
            return True, login
    return False, None


def plan_lifecycle_status(
    repo: str,
    *,
    issue: int | None = None,
    pr: int | None = None,
) -> LifecycleStatus:
    """Produce a read-only LifecycleStatus.

    Accepts exactly one of --issue or --pr.
    """
    notes = [
        "Read-only status only.",
        "No GitHub mutation was performed.",
        "No local cleanup was performed.",
    ]

    if (issue is None) == (pr is None):
        # Caller must ensure exactly one is provided
        return LifecycleStatus(
            query_issue=issue,
            query_pr=pr,
            issue_number=None,
            issue_state=None,
            workflow_state=None,
            phase=None,
            risk=None,
            role=None,
            area=None,
            pr_number=None,
            pr_state=None,
            pr_base=None,
            pr_head=None,
            pr_merged=False,
            merge_commit=None,
            review_decision=None,
            has_non_author_approval=False,
            reviewer_login=None,
            integrated=False,
            issue_closed=False,
            expected_worktree=None,
            worktree_exists=False,
            local_branch_exists=False,
            cleanup_complete=False,
            linkage_source="unknown",
            linkage_confidence="low",
            formal_github_development_link="no/unknown",
            auto_close_keyword=False,
            status="incomplete — exactly one of --issue or --pr is required",
            notes=notes,
        )

    query_issue = issue
    query_pr = pr

    # === Resolve core identifiers ===
    issue_number = issue
    pr_number = pr
    pr_data: dict[str, Any] = {}
    issue_labels: list[str] = []
    issue_state: str | None = None

    try:
        if pr is not None:
            pr_data = _fetch_pr_details(repo, pr)
            pr_number = pr_data.get("number")
            head = pr_data.get("headRefName", "") or ""
            body = pr_data.get("body", "") or ""
            detected_issue = _extract_issue_number(head, body)
            if detected_issue is not None:
                issue_number = detected_issue

        if issue_number is not None:
            issue_labels, issue_state = _fetch_issue_labels_and_state(repo, issue_number)

        # If we still have no PR but have an issue, try best-effort detection
        if pr_number is None and issue_number is not None:
            detected_pr = _detect_associated_pr_from_issue(repo, issue_number)
            if detected_pr is not None:
                pr_number = detected_pr
                pr_data = _fetch_pr_details(repo, pr_number)

    except Exception as e:
        return LifecycleStatus(
            query_issue=query_issue,
            query_pr=query_pr,
            issue_number=issue_number,
            issue_state=issue_state,
            workflow_state=None,
            phase=None,
            risk=None,
            role=None,
            area=None,
            pr_number=pr_number,
            pr_state=None,
            pr_base=None,
            pr_head=None,
            pr_merged=False,
            merge_commit=None,
            review_decision=None,
            has_non_author_approval=False,
            reviewer_login=None,
            integrated=False,
            issue_closed=False,
            expected_worktree=None,
            worktree_exists=False,
            local_branch_exists=False,
            cleanup_complete=False,
            linkage_source="unknown",
            linkage_confidence="low",
            formal_github_development_link="no/unknown",
            auto_close_keyword=False,
            status=f"incomplete — failed to fetch data: {str(e)[:120]}",
            notes=notes,
        )

    # === Issue workflow labels ===
    wf = _extract_workflow_labels(issue_labels)
    workflow_state = wf.get("state")
    phase = wf.get("phase")
    risk = wf.get("risk")
    role = wf.get("role")
    area = wf.get("area")

    # === PR fields ===
    pr_state = pr_data.get("state") if pr_data else None
    pr_base = pr_data.get("baseRefName") if pr_data else None
    pr_head = pr_data.get("headRefName") if pr_data else None
    merge_commit_data = pr_data.get("mergeCommit") if pr_data else None
    merge_commit = merge_commit_data.get("oid") if isinstance(merge_commit_data, dict) else None
    pr_merged = pr_state == "MERGED" and bool(merge_commit)

    # === Review ===
    reviews = pr_data.get("reviews", []) if pr_data else []
    has_non_author, reviewer_login = _get_non_author_approval(reviews)
    review_decision = None
    if reviews:
        for r in reviews:
            if isinstance(r, dict) and (r.get("state") or "").upper() == "APPROVED":
                review_decision = "APPROVED"
                break

    # === Integration ===
    integrated = bool(workflow_state == "state:merged" and issue_state == "CLOSED")
    issue_closed = issue_state == "CLOSED" if issue_state else False

    # === Local cleanup ===
    expected_worktree = None
    worktree_exists = False
    local_branch_exists = False
    cleanup_complete = False

    if issue_number is not None:
        expected_worktree = str(Path("..") / "signposter-work" / str(issue_number))
        worktree_exists = _worktree_exists(expected_worktree)

    if pr_head:
        local_branch_exists = _local_branch_exists(pr_head)

    cleanup_complete = not worktree_exists and not local_branch_exists

    # === Overall status ===
    status = "complete"

    if issue_number is None:
        status = "incomplete — associated issue could not be detected"
    elif pr_number is None:
        status = "incomplete — associated PR could not be detected"
    elif issue_state is None or issue_state.upper() != "CLOSED":
        status = f"incomplete — issue #{issue_number} is not CLOSED"
    elif workflow_state != "state:merged":
        status = f"incomplete — issue #{issue_number} lacks state:merged label"
    elif not pr_merged:
        status = f"incomplete — PR #{pr_number} is not merged"
    elif not merge_commit:
        status = f"incomplete — PR #{pr_number} has no merge commit"
    elif worktree_exists:
        status = "incomplete — local worktree still exists"
    elif local_branch_exists:
        status = "incomplete — local branch still exists"
    else:
        status = "complete"

    # === Linkage source detection (H022C) ===
    body = pr_data.get("body", "") if pr_data else ""
    detected_from = (
        "issue-search"
        if (pr is None and issue_number is not None and pr_number is not None)
        else "direct"
    )

    source, confidence = _detect_link_source(
        pr_head=pr_head,
        body=body,
        detected_from=detected_from,
    )

    auto_close = _contains_auto_close_keyword(body)

    # Formal GitHub development link:
    # Only claim "yes" if we explicitly detected a closing keyword as the linkage source.
    if source == "closing-keyword":
        formal_dev_link = "yes"
    else:
        formal_dev_link = "no/unknown"

    return LifecycleStatus(
        query_issue=query_issue,
        query_pr=query_pr,
        issue_number=issue_number,
        issue_state=issue_state,
        workflow_state=workflow_state,
        phase=phase,
        risk=risk,
        role=role,
        area=area,
        pr_number=pr_number,
        pr_state=pr_state,
        pr_base=pr_base,
        pr_head=pr_head,
        pr_merged=pr_merged,
        merge_commit=merge_commit,
        review_decision=review_decision,
        has_non_author_approval=has_non_author,
        reviewer_login=reviewer_login,
        integrated=integrated,
        issue_closed=issue_closed,
        expected_worktree=expected_worktree,
        worktree_exists=worktree_exists,
        local_branch_exists=local_branch_exists,
        cleanup_complete=cleanup_complete,
        linkage_source=source,
        linkage_confidence=confidence,
        formal_github_development_link=formal_dev_link,
        auto_close_keyword=auto_close,
        status=status,
        notes=notes,
    )


def format_lifecycle_status(status: LifecycleStatus) -> str:
    """Compact deterministic output."""
    header = (
        f"Signposter Lifecycle Status — Issue #{status.issue_number}"
        if status.issue_number
        else f"Signposter Lifecycle Status — PR #{status.pr_number}"
    )
    lines = [f"{header}\n"]

    # Issue
    lines.append("Issue:")
    lines.append(f"  state: {status.issue_state or 'unknown'}")
    lines.append(f"  workflow state: {status.workflow_state or 'unknown'}")
    if status.phase:
        lines.append(f"  phase: {status.phase}")
    if status.risk:
        lines.append(f"  risk: {status.risk}")
    if status.role:
        lines.append(f"  role: {status.role}")
    if status.area:
        lines.append(f"  area: {status.area}")

    # PR
    lines.append("\nPR:")
    if status.pr_number:
        lines.append(f"  pr: #{status.pr_number}")
    else:
        lines.append("  pr: none detected")
    lines.append(f"  state: {status.pr_state or 'unknown'}")
    lines.append(f"  base: {status.pr_base or 'unknown'}")
    lines.append(f"  head: {status.pr_head or 'unknown'}")
    lines.append(f"  merged: {'yes' if status.pr_merged else 'no'}")
    lines.append(f"  merge commit: {status.merge_commit or 'none'}")

    # Review
    lines.append("\nReview:")
    lines.append(f"  review decision: {status.review_decision or 'unknown'}")
    lines.append(f"  non-author approval: {'yes' if status.has_non_author_approval else 'no'}")
    if status.reviewer_login:
        lines.append(f"  reviewer: {status.reviewer_login}")

    # Integration
    lines.append("\nIntegration:")
    lines.append(f"  integrated: {'yes' if status.integrated else 'no'}")
    lines.append(f"  issue closed: {'yes' if status.issue_closed else 'no'}")
    lines.append(f"  workflow state: {status.workflow_state or 'unknown'}")

    # Cleanup
    lines.append("\nCleanup:")
    lines.append(f"  expected worktree: {status.expected_worktree or 'n/a'}")
    lines.append(f"  worktree exists: {'yes' if status.worktree_exists else 'no'}")
    lines.append(f"  local branch exists: {'yes' if status.local_branch_exists else 'no'}")
    lines.append(f"  cleanup complete: {'yes' if status.cleanup_complete else 'no'}")

    # Linkage (H022C)
    lines.append("\nLinkage:")
    if status.linkage_source:
        lines.append("  issue-pr linked: yes")
        lines.append(f"  source: {status.linkage_source}")
        lines.append(f"  confidence: {status.linkage_confidence or 'unknown'}")
        formal = status.formal_github_development_link or "no/unknown"
        lines.append(f"  formal GitHub development link: {formal}")
        lines.append(f"  auto-close keyword: {'yes' if status.auto_close_keyword else 'no'}")
    else:
        lines.append("  issue-pr linked: no")

    # Status
    lines.append("\nStatus:")
    lines.append(f"  {status.status}")

    if status.notes:
        lines.append("\nNotes:")
        for n in status.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)
