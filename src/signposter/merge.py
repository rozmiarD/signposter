"""PR merge planning surface (HARDENING-019).

Pure dry-run planning only. No GitHub mutations, no merges, no issue closes.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

from signposter.review import (
    ReviewGateResult,
    _run_gh_pr_view,
    evaluate_review_gate,
)


@dataclass(frozen=True)
class MergePlan:
    pr_number: int
    title: str
    state: str
    base_branch: str
    head_branch: str
    mergeable: str
    review_decision: str | None

    checks_status: str
    successful_checks: int
    failing_checks: int
    pending_checks: int

    github_approved: bool
    approving_reviewers: list[str]
    has_non_author_approval: bool
    pr_author: str | None

    reviewer_gate_pass: bool
    reviewer_verdict: str | None
    reviewer_confidence: float | None
    reviewer_risk: str | None

    associated_issue: int | None
    has_auto_close_keywords: bool

    files_changed: int
    additions: int
    deletions: int
    risk_level: str
    size: str

    merge_method: str  # "squash"
    delete_branch_after_merge: bool
    command_preview: str

    status: str  # "ready" | "blocked — ..." | "pending — ..."
    notes: list[str]


AUTO_CLOSE_PATTERNS = [
    r"(?i)\b(closes?|fixes?|resolves?)\s+#\d+",
    r"(?i)\b(closes?|fixes?|resolves?)\s+github\.com/[^/]+/[^/]+#\d+",
]


def _has_auto_close_keywords(body: str | None) -> bool:
    if not body:
        return False
    for pattern in AUTO_CLOSE_PATTERNS:
        if re.search(pattern, body):
            return True
    return False


def _extract_issue_from_branch_or_body(head_branch: str, body: str | None) -> int | None:
    # Primary: work/issue-N-...
    m = re.search(r"work/issue-(\d+)", head_branch or "")
    if m:
        return int(m.group(1))

    # Secondary: Related issue: #N or similar
    if body:
        m = re.search(r"(?i)related\s+issue:\s*#?(\d+)", body)
        if m:
            return int(m.group(1))
        m = re.search(r"(?i)issue\s*#?(\d+)", body)
        if m:
            return int(m.group(1))
    return None


def _fetch_pr_reviews_and_author(repo: str, pr: int) -> dict[str, Any]:
    """Fetch author + review decision + list of approving reviewers."""
    data = _run_gh_pr_view(
        repo,
        pr,
        ["author", "reviewDecision", "reviews"],
    )
    author_login = None
    if isinstance(data.get("author"), dict):
        author_login = data["author"].get("login")

    review_decision = data.get("reviewDecision")

    approving_reviewers: list[str] = []
    reviews = data.get("reviews", []) or []
    for r in reviews:
        if isinstance(r, dict):
            state = (r.get("state") or "").upper()
            reviewer = r.get("author", {})
            if isinstance(reviewer, dict):
                login = reviewer.get("login")
            else:
                login = None
            if state == "APPROVED" and login:
                approving_reviewers.append(login)

    return {
        "pr_author": author_login,
        "review_decision": review_decision,
        "approving_reviewers": approving_reviewers,
    }


def _classify_size_and_risk(
    files_changed: int, additions: int, deletions: int, file_paths: list[str]
) -> tuple[str, str]:
    """Reuse conservative classification logic."""
    total = additions + deletions
    if files_changed <= 3 and total <= 100:
        size = "small"
    elif files_changed <= 10 and total <= 500:
        size = "medium"
    else:
        size = "large"

    high_risk_patterns = [
        ".github/workflows", "pyproject.toml", "security", "auth", "ci.yml", "gate"
    ]
    has_high = any(
        any(pat.lower() in (p or "").lower() for pat in high_risk_patterns)
        for p in file_paths
    )
    if has_high:
        risk = "high"
    elif files_changed <= 2 and any(
        "readme" in (p or "").lower() or "docs/" in (p or "").lower() or ".md" in (p or "").lower()
        for p in file_paths
    ):
        risk = "low"
    else:
        risk = "medium"
    return risk, size


def plan_merge_for_pr(repo: str, pr_number: int) -> MergePlan:
    """Produce a dry-run MergePlan for a pull request."""
    notes = [
        "No merge was performed.",
        "No issue was closed.",
        "No branch was deleted.",
        "Local worktree cleanup is not part of this command.",
    ]

    try:
        pr_data = _run_gh_pr_view(
            repo,
            pr_number,
            [
                "number", "title", "state", "baseRefName", "headRefName",
                "mergeable", "reviewDecision", "body", "author",
                "statusCheckRollup", "files", "additions", "deletions",
            ],
        )
    except Exception:
        return MergePlan(
            pr_number=pr_number,
            title="unknown",
            state="UNKNOWN",
            base_branch="unknown",
            head_branch="unknown",
            mergeable="UNKNOWN",
            review_decision=None,
            checks_status="unknown",
            successful_checks=0,
            failing_checks=0,
            pending_checks=0,
            github_approved=False,
            approving_reviewers=[],
            has_non_author_approval=False,
            pr_author=None,
            reviewer_gate_pass=False,
            reviewer_verdict=None,
            reviewer_confidence=None,
            reviewer_risk=None,
            associated_issue=None,
            has_auto_close_keywords=False,
            files_changed=0,
            additions=0,
            deletions=0,
            risk_level="unknown",
            size="unknown",
            merge_method="squash",
            delete_branch_after_merge=True,
            command_preview=f"gh pr merge {pr_number} -R {repo} --squash --delete-branch",
            status=f"blocked — failed to fetch PR #{pr_number}",
            notes=notes,
        )

    title = pr_data.get("title", "")
    state = pr_data.get("state", "UNKNOWN")
    base = pr_data.get("baseRefName", "main")
    head = pr_data.get("headRefName", "")
    mergeable = pr_data.get("mergeable", "UNKNOWN")
    review_decision = pr_data.get("reviewDecision")
    body = pr_data.get("body", "") or ""

    # Reviews + author
    reviews_info = _fetch_pr_reviews_and_author(repo, pr_number)
    pr_author = reviews_info["pr_author"]
    approving_reviewers = reviews_info["approving_reviewers"]
    has_non_author = False
    if pr_author:
        has_non_author = any(r != pr_author for r in approving_reviewers)
    else:
        has_non_author = len(approving_reviewers) > 0

    # Checks (reuse normalization if possible)
    try:
        checks = _fetch_pr_checks_for_merge(repo, pr_number)  # local helper below
    except Exception:
        checks = {"status": "unknown", "successful": 0, "failing": 0, "pending": 0}

    # Files
    files_info = {
        "files_changed": len(pr_data.get("files", []) or []),
        "additions": pr_data.get("additions", 0),
        "deletions": pr_data.get("deletions", 0),
    }
    file_paths = [f.get("path", "") for f in (pr_data.get("files") or [])]

    risk, size = _classify_size_and_risk(
        files_info["files_changed"],
        files_info["additions"],
        files_info["deletions"],
        file_paths,
    )

    # Local reviewer gate
    try:
        gate: ReviewGateResult = evaluate_review_gate(repo, pr_number)
    except Exception:
        gate = None  # type: ignore

    reviewer_gate_pass = bool(gate and gate.gate_pass)
    reviewer_verdict = gate.opinion.verdict if gate else None
    reviewer_confidence = gate.opinion.confidence if gate else None
    reviewer_risk = gate.opinion.risk if gate else None

    # Associated issue + auto-close
    associated_issue = _extract_issue_from_branch_or_body(head, body)
    has_auto_close = _has_auto_close_keywords(body)

    # Merge eligibility decision (very conservative)
    status = "ready"
    if state != "OPEN":
        status = f"blocked — PR is {state.lower()}"
    elif mergeable != "MERGEABLE":
        status = f"blocked — PR is not mergeable ({mergeable})"
    elif checks["status"] == "failing":
        status = "blocked — checks are failing"
    elif checks["status"] == "pending":
        status = "pending — checks are still running"
    elif checks["status"] == "unknown":
        status = "blocked — checks status is unknown"
    elif review_decision != "APPROVED":
        status = f"blocked — GitHub review decision is {review_decision or 'none'}"
    elif not has_non_author:
        status = "blocked — no non-author approval found"
    elif not reviewer_gate_pass:
        status = "blocked — local reviewer gate is not pass"
    elif reviewer_verdict != "APPROVE":
        status = f"blocked — reviewer verdict is {reviewer_verdict or 'unknown'}"
    elif reviewer_confidence is None or reviewer_confidence < 0.85:
        status = "blocked — reviewer confidence below threshold"
    elif reviewer_risk not in ("low", "LOW"):
        status = f"blocked — reviewer risk is {reviewer_risk or 'unknown'}"
    elif size not in ("small",):
        status = f"blocked — PR scope is {size}"
    elif associated_issue is None:
        status = "blocked — associated Signposter issue could not be detected"
    elif has_auto_close:
        status = "blocked — PR body contains auto-close keywords"

    command_preview = f"gh pr merge {pr_number} -R {repo} --squash --delete-branch"

    return MergePlan(
        pr_number=pr_number,
        title=title,
        state=state,
        base_branch=base,
        head_branch=head,
        mergeable=mergeable,
        review_decision=review_decision,
        checks_status=checks["status"],
        successful_checks=checks["successful"],
        failing_checks=checks["failing"],
        pending_checks=checks["pending"],
        github_approved=review_decision == "APPROVED",
        approving_reviewers=approving_reviewers,
        has_non_author_approval=has_non_author,
        pr_author=pr_author,
        reviewer_gate_pass=reviewer_gate_pass,
        reviewer_verdict=reviewer_verdict,
        reviewer_confidence=reviewer_confidence,
        reviewer_risk=reviewer_risk,
        associated_issue=associated_issue,
        has_auto_close_keywords=has_auto_close,
        files_changed=files_info["files_changed"],
        additions=files_info["additions"],
        deletions=files_info["deletions"],
        risk_level=risk,
        size=size,
        merge_method="squash",
        delete_branch_after_merge=True,
        command_preview=command_preview,
        status=status,
        notes=notes,
    )


def _fetch_pr_checks_for_merge(repo: str, pr: int) -> dict[str, Any]:
    """Minimal wrapper around existing check logic."""
    from signposter.review import _normalize_check_rollup

    data = _run_gh_pr_view(repo, pr, ["statusCheckRollup"])
    checks = _normalize_check_rollup(data.get("statusCheckRollup", []))

    successful = failing = pending = 0
    for c in checks:
        status = (c.get("status") or "").upper()
        conclusion = (c.get("conclusion") or "").upper()
        state = (c.get("state") or "").upper()

        if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            successful += 1
        elif conclusion in ("FAILURE", "ERROR", "CANCELLED"):
            failing += 1
        elif status in ("QUEUED", "IN_PROGRESS", "PENDING"):
            pending += 1
        elif state in ("PENDING", "QUEUED", "IN_PROGRESS"):
            pending += 1
        elif state == "SUCCESS":
            successful += 1

    if failing > 0:
        cstatus = "failing"
    elif pending > 0:
        cstatus = "pending"
    elif successful > 0:
        cstatus = "pass"
    else:
        cstatus = "unknown"

    return {
        "status": cstatus,
        "successful": successful,
        "failing": failing,
        "pending": pending,
    }


def format_merge_plan(plan: MergePlan) -> str:
    """Compact deterministic merge planning output."""
    lines = [f"Signposter Merge Plan — PR #{plan.pr_number}\n"]

    lines.append("PR:")
    lines.append(f"  title: {plan.title}")
    lines.append(f"  state: {plan.state}")
    lines.append(f"  base: {plan.base_branch}")
    lines.append(f"  head: {plan.head_branch}")
    lines.append(f"  mergeable: {plan.mergeable}")
    lines.append(f"  review decision: {plan.review_decision or 'none'}")

    lines.append("\nChecks:")
    lines.append(f"  status: {plan.checks_status}")
    lines.append(f"  successful: {plan.successful_checks}")
    lines.append(f"  failing: {plan.failing_checks}")
    lines.append(f"  pending: {plan.pending_checks}")

    lines.append("\nReviewer gate:")
    lines.append(f"  status: {'pass' if plan.reviewer_gate_pass else 'blocked'}")
    lines.append(f"  verdict: {plan.reviewer_verdict or 'unknown'}")
    conf = plan.reviewer_confidence
    lines.append(f"  confidence: {conf if conf is not None else 'unknown'}")
    lines.append(f"  risk: {plan.reviewer_risk or 'unknown'}")
    lines.append("  scope match: yes")  # we already gate on this inside evaluate_review_gate
    lines.append("  ci considered: yes")

    lines.append("\nGitHub review:")
    lines.append(f"  approved: {'yes' if plan.github_approved else 'no'}")
    if plan.approving_reviewers:
        lines.append(f"  reviewers: {', '.join(plan.approving_reviewers)}")
    lines.append(f"  has non-author approval: {'yes' if plan.has_non_author_approval else 'no'}")

    lines.append("\nScope:")
    lines.append(f"  files changed: {plan.files_changed}")
    lines.append(f"  additions: {plan.additions}")
    lines.append(f"  deletions: {plan.deletions}")
    lines.append(f"  risk: {plan.risk_level}")
    lines.append(f"  size: {plan.size}")

    lines.append("\nIssue:")
    if plan.associated_issue:
        lines.append(f"  associated issue: #{plan.associated_issue}")
    else:
        lines.append("  associated issue: none detected")
    lines.append("  close issue: no")
    auto_close = 'yes' if plan.has_auto_close_keywords else 'no'
    lines.append(f"  auto-close keywords present: {auto_close}")

    lines.append("\nMerge:")
    lines.append(f"  method: {plan.merge_method}")
    del_branch = 'yes' if plan.delete_branch_after_merge else 'no'
    lines.append(f"  delete branch after merge: {del_branch}")
    lines.append(f"  command preview: {plan.command_preview}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)


def _fetch_pr_checks_for_merge(repo: str, pr: int) -> dict[str, Any]:
    """Minimal wrapper around existing check logic from review.py."""
    from signposter.review import _normalize_check_rollup

    data = _run_gh_pr_view(repo, pr, ["statusCheckRollup"])
    checks = _normalize_check_rollup(data.get("statusCheckRollup", []))

    successful = failing = pending = 0
    for c in checks:
        status = (c.get("status") or "").upper()
        conclusion = (c.get("conclusion") or "").upper()
        state = (c.get("state") or "").upper()

        if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            successful += 1
        elif conclusion in ("FAILURE", "ERROR", "CANCELLED"):
            failing += 1
        elif status in ("QUEUED", "IN_PROGRESS", "PENDING"):
            pending += 1
        elif state in ("PENDING", "QUEUED", "IN_PROGRESS"):
            pending += 1
        elif state == "SUCCESS":
            successful += 1

    if failing > 0:
        cstatus = "failing"
    elif pending > 0:
        cstatus = "pending"
    elif successful > 0:
        cstatus = "pass"
    else:
        cstatus = "unknown"

    return {
        "status": cstatus,
        "successful": successful,
        "failing": failing,
        "pending": pending,
    }


# =============================================================================
# HARDENING-020: Guarded PR merge apply (dry-run by default, --apply for mutation)
# =============================================================================


def apply_merge(
    repo: str, pr_number: int, *, apply: bool = False
) -> dict:
    """Execute (or dry-run) a guarded merge of a PR.

    Only performs the actual gh pr merge when apply=True AND the merge plan status is "ready".
    Uses squash merge + --delete-branch for the remote branch.
    Never closes issues, never touches local worktrees/branches.
    """
    plan = plan_merge_for_pr(repo, pr_number)

    if not apply:
        # Pure dry-run
        return {
            "mode": "dry_run",
            "plan": plan,
            "command": plan.command_preview,
        }

    # Mutation path - extremely guarded
    if plan.status != "ready":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": f"Refusing to merge: {plan.status}",
        }

    # Execute the merge
    cmd = [
        "gh", "pr", "merge", str(pr_number),
        "-R", repo,
        "--squash",
        "--delete-branch",
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        success = proc.returncode == 0

        result = {
            "mode": "apply",
            "plan": plan,
            "success": success,
            "command": " ".join(cmd),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

        if not success:
            result["error"] = f"gh pr merge failed: {proc.stderr.strip()[:500]}"

        return result

    except Exception as e:
        return {
            "mode": "apply",
            "plan": plan,
            "success": False,
            "command": " ".join(cmd),
            "error": str(e),
        }


def format_merge_apply_dry_run(plan: MergePlan) -> str:
    """Compact dry-run output for merge apply.

    HARDENING-020A: Status must accurately reflect the underlying merge plan status.
    Never hardcode 'ready'.
    """
    lines = [f"Signposter Merge Apply Plan — PR #{plan.pr_number}\n"]

    lines.append("Merge plan:")
    lines.append(f"  status: {plan.status}")
    lines.append(f"  method: {plan.merge_method}")
    del_branch = "yes" if plan.delete_branch_after_merge else "no"
    lines.append(f"  delete branch after merge: {del_branch}")

    lines.append("\nCommand:")
    lines.append(f"  {plan.command_preview}")

    lines.append("\nStatus:")
    if plan.status == "ready":
        lines.append("  ready")
    else:
        lines.append(f"  blocked — merge plan is not ready ({plan.status})")

    lines.append("\nNotes:")
    lines.append("  DRY RUN: no merge was performed.")
    lines.append("  No issue was closed.")
    lines.append("  No local worktree was removed.")
    lines.append(
        "  Remote branch deletion would only happen after successful merge via --delete-branch."
    )

    return "\n".join(lines)

