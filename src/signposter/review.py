"""Reviewer-agent PR review planning (planning / dry-run only).

HARDENING-014: Provide a safe planning surface for OpenClaw reviewer
to inspect pull requests created from Signposter worker branches.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
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
    elif mergeable == "CONFLICTING":
        status = "blocked — PR has merge conflicts"
    # UNKNOWN is tolerated (common with gh until background computation finishes)
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


# =============================================================================
# HARDENING-015: Prompt artifact writing (planning only)
# =============================================================================


def get_pr_diff(repo: str, pr_number: int, max_lines: int = 150) -> str:
    """Fetch a bounded PR diff using gh CLI."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "-R", repo],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return f"<diff fetch failed: {result.stderr.strip()[:200]}>"

    diff = result.stdout.strip()
    lines = diff.splitlines()
    if len(lines) > max_lines:
        diff = "\n".join(lines[:max_lines]) + f"\n... (truncated, {len(lines)} total lines)"
    return diff or "<no diff content>"


def build_review_prompt(plan: ReviewPlan, pr_body: str, diff: str) -> str:
    """Render the complete reviewer prompt with structured output contract."""
    issue_line = (
        f"Issue #{plan.associated_issue}"
        if plan.associated_issue
        else "No associated issue detected via branch convention"
    )

    content = f"""You are an expert code reviewer acting as the Signposter reviewer agent.

## PR Under Review
- PR: #{plan.pr_number}
- Title: {plan.title}
- State: {plan.state}
- Base branch: {plan.base_branch}
- Head branch: {plan.head_branch}
- Mergeable: {plan.mergeable}
- Current review decision: {plan.review_decision or "none"}

## Signposter Workflow Context
- Associated issue: {issue_line}
- Branch follows worker convention (work/issue-N-...): {plan.branch_matches_convention}

## CI / Checks Status
- Overall status: {plan.checks_status}
- Successful checks: {plan.successful_checks}
- Failing checks: {plan.failing_checks}
- Pending checks: {plan.pending_checks}

## Change Scope
- Files changed: {plan.files_changed}
- Additions: {plan.additions}
- Deletions: {plan.deletions}
- Risk classification: {plan.risk_level}
- Size classification: {plan.size}

## PR Body
{pr_body or "<no body provided>"}

## Diff (bounded excerpt)
```diff
{diff}
```

## Your Task
Perform a careful, evidence-based review of this pull request.

You **must** return a structured review opinion using exactly this format:

Verdict: APPROVE | NEEDS_CHANGES | BLOCK
Confidence: 0.00-1.00
Risk: low | medium | high
Scope match: yes | no
CI considered: yes | no
Findings:
  - <one-line finding>
  - ...
Merge recommendation: yes | no
Automerge eligible: yes | no
Reasoning summary:
  <1-3 sentences of evidence-based reasoning>

## Strict Rules (do not violate)
- Confidence MUST be a number between 0.00 and 1.00 (two decimal places preferred).
- Confidence >= 0.85 + low risk + small scope + green CI + clear scope match MAY be considered
  for future low-risk automerge.
- Confidence < 0.85 blocks any automerge consideration.
- High-risk findings or uncertainty about files/scope/CI/issue mapping blocks automerge.
- You MUST NOT claim that you submitted a GitHub review, merged the PR, or closed any issue.
- Base your verdict only on the metadata, body, and diff provided above.
- For docs-only low-risk small green-CI changes that match the issue,
  APPROVE is usually appropriate.
- When in doubt, prefer NEEDS_CHANGES or BLOCK and document the specific concern in Findings.

Begin your structured review now.
"""
    return content


def write_review_prompt_artifact(repo: str, pr_number: int) -> str:
    """Generate the review plan, then write the reviewer prompt artifact if ready.

    Returns the absolute path of the written file.
    Raises RuntimeError if the review plan status is not "ready".
    """
    plan = plan_review_for_pr(repo, pr_number)

    if plan.status != "ready":
        raise RuntimeError(f"Refusing to write prompt artifact: {plan.status}")

    # Fetch fresh body (plan may have truncated or partial)
    try:
        pr_data = _run_gh_pr_view(repo, pr_number, ["body"])
        pr_body = pr_data.get("body", "") or ""
    except Exception:
        pr_body = ""

    diff = get_pr_diff(repo, pr_number, max_lines=120)

    content = build_review_prompt(plan, pr_body, diff)

    path = plan.prompt_artifact_path
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return path


# =============================================================================
# HARDENING-016: Local reviewer execution (planning + execution only, no GitHub)
# =============================================================================



def _generate_pr_reviewer_summary(
    *, pr_number: int, plan: ReviewPlan, session_key: str, exit_code: int,
    raw_path: str, stdout: str, stderr: str, start_time,
) -> str:
    """Generate a bounded mechanical summary for a PR reviewer execution."""
    lines = [
        "# Signposter PR Reviewer Execution Summary\n",
        "**Repository:** (from plan)",
        f"**PR:** #{pr_number}",
        f"**Title:** {plan.title}",
        "**Agent:** reviewer",
        f"**Session Key:** {session_key}",
        f"**Prompt Artifact:** {plan.prompt_artifact_path}",
        f"**Started (UTC):** {start_time.isoformat()}",
        f"**Exit Code:** {exit_code}",
        f"**Raw Output:** {raw_path}",
        "",
    ]

    raw_text = stdout + ("\n" + stderr if stderr else "")
    line_count = len(raw_text.splitlines())
    byte_count = len(raw_text.encode("utf-8"))
    lines.append(f"**Output Size:** {line_count} lines, {byte_count} bytes")

    # Try to extract structured verdict if present in output
    verdict = None
    confidence = None
    for line in raw_text.splitlines():
        if line.strip().startswith("Verdict:"):
            verdict = line.split(":", 1)[1].strip()
        if line.strip().startswith("Confidence:"):
            confidence = line.split(":", 1)[1].strip()

    if verdict:
        lines.append(f"**Extracted Verdict:** {verdict}")
    if confidence:
        lines.append(f"**Extracted Confidence:** {confidence}")

    lines.append("\n## First 25 lines of output\n")
    first_lines = raw_text.splitlines()[:25]
    lines.append("```\n" + "\n".join(first_lines) + "\n```")

    if line_count > 40:
        lines.append("\n## Last 15 lines of output\n")
        last_lines = raw_text.splitlines()[-15:]
        lines.append("```\n" + "\n".join(last_lines) + "\n```")

    lines.append("\n---\nGenerated by Signposter (PR reviewer local execution capture)")

    return "\n".join(lines)


def execute_pr_review(repo: str, pr_number: int, *, profile: str = "reviewer") -> dict:
    """Execute the reviewer agent locally against an existing PR review prompt artifact.

    Purely local execution. Writes raw + summary artifacts under artifacts/runs/.
    Never posts to GitHub.

    Returns a dict with execution result.
    """
    # Guard: plan must be ready
    plan = plan_review_for_pr(repo, pr_number)
    if plan.status != "ready":
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": f"review plan not ready: {plan.status}",
            "success": False,
        }

    prompt_path = plan.prompt_artifact_path
    if not os.path.isfile(prompt_path):
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": f"prompt artifact missing: {prompt_path}",
            "success": False,
        }

    # Read prompt
    try:
        with open(prompt_path, encoding="utf-8") as f:
            prompt_content = f.read()
    except Exception as e:
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": f"failed to read prompt: {e}",
            "success": False,
        }

    session_key = f"signposter-pr-{pr_number}-reviewer"

    exec_cmd = [
        "openclaw", "agent",
        "--agent", profile,
        "--session-key", session_key,
        "--message", prompt_content,
        "--local",
    ]

    print(f"Running: openclaw agent --agent {profile} --session-key {session_key} --local")
    print(f"Using prompt: {prompt_path} (length: {len(prompt_content)} chars)")

    runs_dir = Path("artifacts/runs")
    runs_dir.mkdir(parents=True, exist_ok=True)

    raw_path = runs_dir / f"pr-{pr_number}-reviewer.raw.txt"
    summary_path = runs_dir / f"pr-{pr_number}-reviewer.summary.md"

    start_time = datetime.datetime.now(datetime.UTC)

    try:
        proc = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min safety cap
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout
        if stderr:
            combined += "\n\n=== STDERR ===\n" + stderr

        exit_code = proc.returncode

        raw_path.write_text(combined, encoding="utf-8")

        summary = _generate_pr_reviewer_summary(
            pr_number=pr_number,
            plan=plan,
            session_key=session_key,
            exit_code=exit_code,
            raw_path=str(raw_path),
            stdout=stdout,
            stderr=stderr,
            start_time=start_time,
        )
        summary_path.write_text(summary, encoding="utf-8")

        return {
            "exit_code": exit_code,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": exit_code == 0,
        }

    except subprocess.TimeoutExpired as e:
        raw_path.write_text(f"[TIMEOUT after 600s]\n{e}", encoding="utf-8")
        return {"exit_code": -1, "raw_path": str(raw_path), "success": False}
    except Exception as e:
        raw_path.write_text(f"[ERROR]\n{e}", encoding="utf-8")
        return {"exit_code": -1, "raw_path": str(raw_path), "success": False}


