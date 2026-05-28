"""Reviewer-agent PR review planning (planning / dry-run only).

HARDENING-014: Provide a safe planning surface for OpenClaw reviewer
to inspect pull requests created from Signposter worker branches.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReviewPlan:
    pr_number: int
    title: str
    state: str  # OPEN, CLOSED, MERGED
    base_branch: str
    head_branch: str
    mergeable: str  # MERGEABLE, CONFLICTING, UNKNOWN
    review_decision: str | None  # APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED, None

    checks_status: str  # pass, failing, pending, unknown
    successful_checks: int
    failing_checks: int
    pending_checks: int

    files_changed: int
    additions: int
    deletions: int

    risk_level: str  # low, medium, high
    size: str  # small, medium, large

    associated_issue: int | None
    branch_matches_convention: bool

    status: str  # "ready" | "blocked — <reason>" | "pending — <reason>"
    notes: list[str]

    reviewer_profile: str
    prompt_artifact_path: str


def _run_gh_pr_view(repo: str, pr: int, fields: list[str]) -> dict[str, Any]:
    """Run gh pr view and return parsed JSON."""
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(pr),
            "-R",
            repo,
            "--json",
            ",".join(fields),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr.strip()}")

    return json.loads(result.stdout)


def _normalize_check_rollup(rollup: Any) -> list[dict[str, Any]]:
    """Normalize gh statusCheckRollup shapes into a list of check dicts."""
    if isinstance(rollup, list):
        return [item for item in rollup if isinstance(item, dict)]

    if isinstance(rollup, dict):
        contexts = rollup.get("contexts")
        if isinstance(contexts, list):
            return [item for item in contexts if isinstance(item, dict)]

        nodes = rollup.get("nodes")
        if isinstance(nodes, list):
            return [item for item in nodes if isinstance(item, dict)]

    return []


def _fetch_pr_checks(repo: str, pr: int) -> dict[str, Any]:
    """Fetch check status for a PR."""
    data = _run_gh_pr_view(repo, pr, ["statusCheckRollup"])
    checks = _normalize_check_rollup(data.get("statusCheckRollup", []))

    successful = 0
    failing = 0
    pending = 0

    for check in checks:
        status = (check.get("status") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()

        if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            successful += 1
        elif conclusion in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"):
            failing += 1
        elif status in ("QUEUED", "IN_PROGRESS", "REQUESTED", "WAITING", "PENDING"):
            pending += 1
        elif state in ("PENDING", "QUEUED", "IN_PROGRESS"):
            pending += 1
        elif state in ("FAILURE", "ERROR", "CANCELLED"):
            failing += 1
        elif state == "SUCCESS":
            successful += 1

    if failing > 0:
        checks_status = "failing"
    elif pending > 0:
        checks_status = "pending"
    elif successful > 0:
        checks_status = "pass"
    else:
        checks_status = "unknown"

    return {
        "status": checks_status,
        "successful": successful,
        "failing": failing,
        "pending": pending,
    }


def _fetch_pr_files(repo: str, pr: int) -> dict[str, int]:
    """Fetch changed files summary."""
    data = _run_gh_pr_view(repo, pr, ["files", "additions", "deletions"])
    files = data.get("files", []) or []

    return {
        "files_changed": len(files),
        "additions": data.get("additions", 0),
        "deletions": data.get("deletions", 0),
    }


def _classify_risk_and_size(
    files_changed: int, additions: int, deletions: int, file_paths: list[str]
) -> tuple[str, str]:
    """Return (risk_level, size)."""
    total_changes = additions + deletions

    # Size classification
    if files_changed <= 3 and total_changes <= 100:
        size = "small"
    elif files_changed <= 10 and total_changes <= 500:
        size = "medium"
    else:
        size = "large"

    # Risk classification (conservative)
    high_risk_patterns = [
        ".github/workflows",
        "pyproject.toml",
        "requirements",
        "Dockerfile",
        "security",
        "auth",
        "credential",
        "ci.yml",
        "gate",
    ]

    has_high_risk = any(
        any(pat in path.lower() for pat in high_risk_patterns)
        for path in file_paths
    )

    if has_high_risk:
        risk = "high"
    elif files_changed <= 2 and any(
        "readme" in p.lower() or "docs/" in p.lower() or ".md" in p.lower()
        for p in file_paths
    ):
        risk = "low"
    else:
        risk = "medium"

    return risk, size


def _extract_issue_from_branch(branch: str) -> int | None:
    """Extract issue number from work/issue-N-... convention."""
    if not branch:
        return None
    # work/issue-4-...
    import re
    m = re.search(r"work/issue-(\d+)", branch)
    if m:
        return int(m.group(1))
    return None


def plan_review_for_pr(repo: str, pr_number: int) -> ReviewPlan:
    """Produce a dry-run ReviewPlan for a pull request."""
    try:
        pr_data = _run_gh_pr_view(
            repo,
            pr_number,
            [
                "number", "title", "state", "baseRefName", "headRefName",
                "mergeable", "reviewDecision", "body"
            ],
        )
    except Exception:
        return ReviewPlan(
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
            files_changed=0,
            additions=0,
            deletions=0,
            risk_level="unknown",
            size="unknown",
            associated_issue=None,
            branch_matches_convention=False,
            status=f"blocked — failed to fetch PR #{pr_number}",
            notes=["No review was executed.", "No GitHub review was submitted."],
            reviewer_profile="reviewer",
            prompt_artifact_path=f"artifacts/prompts/pr-{pr_number}-review.md",
        )

    title = pr_data.get("title", "")
    state = pr_data.get("state", "UNKNOWN")
    base = pr_data.get("baseRefName", "main")
    head = pr_data.get("headRefName", "")
    mergeable = pr_data.get("mergeable", "UNKNOWN")
    review_decision = pr_data.get("reviewDecision")

    # Branch convention check
    branch_matches = head.startswith("work/issue-")
    associated_issue = _extract_issue_from_branch(head)

    # Checks
    try:
        checks = _fetch_pr_checks(repo, pr_number)
    except Exception:
        checks = {"status": "unknown", "successful": 0, "failing": 0, "pending": 0}

    # Files
    try:
        files_info = _fetch_pr_files(repo, pr_number)
    except Exception:
        files_info = {"files_changed": 0, "additions": 0, "deletions": 0}

    # For risk classification we need file paths. We approximate with a second call if needed.
    # For planning we keep it simple.
    file_paths: list[str] = []
    try:
        files_data = _run_gh_pr_view(repo, pr_number, ["files"])
        file_paths = [f.get("path", "") for f in files_data.get("files", [])]
    except Exception:
        pass

    risk, size = _classify_risk_and_size(
        files_info["files_changed"],
        files_info["additions"],
        files_info["deletions"],
        file_paths,
    )

    # Status logic
    status = "ready"
    if state != "OPEN":
        status = f"blocked — PR is {state.lower()}"
    elif checks["status"] == "failing":
        status = "blocked — checks are failing"
    elif checks["status"] == "pending":
        status = "pending — checks are still running"
    elif checks["status"] == "unknown":
        status = "blocked — checks status is unknown"
    elif mergeable != "MERGEABLE":
        status = f"blocked — PR is not mergeable ({mergeable})"
    elif not branch_matches:
        status = "blocked — branch does not match Signposter worker convention (work/issue-N-...)"
    elif risk == "high":
        status = "blocked — high risk change detected"
    elif associated_issue is None:
        status = "blocked — could not map PR to a Signposter issue number"

    notes = [
        "No review was executed.",
        "No GitHub review was submitted.",
        "No merge was performed.",
        "No issue was closed.",
    ]

    return ReviewPlan(
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
        files_changed=files_info["files_changed"],
        additions=files_info["additions"],
        deletions=files_info["deletions"],
        risk_level=risk,
        size=size,
        associated_issue=associated_issue,
        branch_matches_convention=branch_matches,
        status=status,
        notes=notes,
        reviewer_profile="reviewer",
        prompt_artifact_path=f"artifacts/prompts/pr-{pr_number}-review.md",
    )


def format_review_plan(plan: ReviewPlan) -> str:
    """Compact deterministic output for review planning."""
    lines = [f"Signposter Review Plan — PR #{plan.pr_number}\n"]

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

    lines.append("\nScope:")
    lines.append(f"  files changed: {plan.files_changed}")
    lines.append(f"  additions: {plan.additions}")
    lines.append(f"  deletions: {plan.deletions}")
    lines.append(f"  risk: {plan.risk_level}")
    lines.append(f"  size: {plan.size}")

    lines.append("\nReviewer:")
    lines.append(f"  agent: {plan.reviewer_profile}")
    lines.append("  model/profile: existing OpenClaw reviewer profile")
    lines.append(f"  prompt artifact: {plan.prompt_artifact_path}")
    lines.append(
        "  expected output: structured review opinion with verdict, "
        "confidence, risk, findings, recommendation"
    )

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)
