"""Reviewer-agent PR review planning (planning / dry-run only).

HARDENING-014: Provide a safe planning surface for OpenClaw reviewer
to inspect pull requests created from Signposter worker branches.
"""

from __future__ import annotations

import datetime
import json
import os
import re
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


def execute_pr_review(
    repo: str,
    pr_number: int,
    *,
    profile: str = "reviewer",
    runs_dir: Path | str = "artifacts/runs",
) -> dict:
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

    runs_dir = Path(runs_dir)
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


# =============================================================================
# HARDENING-017: Reviewer opinion parsing + conservative review gate
# =============================================================================




@dataclass(frozen=True)
class ReviewerOpinion:
    """Parsed structured opinion from the reviewer agent."""
    verdict: str | None  # APPROVE | NEEDS_CHANGES | BLOCK
    confidence: float | None
    risk: str | None  # low | medium | high
    scope_match: str | None  # yes | no
    ci_considered: str | None  # yes | no
    merge_recommendation: str | None  # yes | no
    automerge_eligible: str | None  # yes | no
    findings: list[str]
    reasoning: str | None
    raw_text: str


@dataclass(frozen=True)
class ReviewGateResult:
    """Result of the conservative review gate."""
    pr_number: int
    status: str  # "pass" | "blocked — <reason>"
    reason: str
    opinion: ReviewerOpinion
    gate_pass: bool
    merge_eligible: bool
    automerge_eligible: bool
    summary_path: str | None
    notes: list[str]


def parse_reviewer_opinion(text: str) -> ReviewerOpinion:
    """Parse the structured reviewer contract from raw or summary text.

    Tolerant line-based parser. Looks for the expected fields.
    """
    verdict = None
    confidence = None
    risk = None
    scope_match = None
    ci_considered = None
    merge_recommendation = None
    automerge_eligible = None
    findings: list[str] = []
    reasoning_lines: list[str] = []
    in_findings = False
    in_reasoning = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        low = line.lower()

        if line.startswith("Verdict:"):
            verdict = line.split(":", 1)[1].strip().upper()
            in_findings = False
            in_reasoning = False
        elif line.startswith("Confidence:"):
            val = line.split(":", 1)[1].strip()
            try:
                confidence = float(val)
            except ValueError:
                confidence = None
            in_findings = False
            in_reasoning = False
        elif low.startswith("risk:"):
            risk = line.split(":", 1)[1].strip().lower()
            in_findings = False
            in_reasoning = False
        elif low.startswith("scope match:"):
            scope_match = line.split(":", 1)[1].strip().lower()
            in_findings = False
            in_reasoning = False
        elif low.startswith("ci considered:"):
            ci_considered = line.split(":", 1)[1].strip().lower()
            in_findings = False
            in_reasoning = False
        elif low.startswith("merge recommendation:"):
            merge_recommendation = line.split(":", 1)[1].strip().lower()
            in_findings = False
            in_reasoning = False
        elif low.startswith("automerge eligible:"):
            automerge_eligible = line.split(":", 1)[1].strip().lower()
            in_findings = False
            in_reasoning = False
        elif line.startswith("Findings:"):
            in_findings = True
            in_reasoning = False
            continue
        elif line.startswith("Reasoning summary:") or line.startswith("Reasoning:"):
            in_reasoning = True
            in_findings = False
            continue

        if in_findings and line.startswith("-"):
            findings.append(line[1:].strip())
        elif in_reasoning and line and not line.startswith("-"):
            reasoning_lines.append(line)

    reasoning = " ".join(reasoning_lines).strip() if reasoning_lines else None

    return ReviewerOpinion(
        verdict=verdict,
        confidence=confidence,
        risk=risk,
        scope_match=scope_match,
        ci_considered=ci_considered,
        merge_recommendation=merge_recommendation,
        automerge_eligible=automerge_eligible,
        findings=findings,
        reasoning=reasoning,
        raw_text=text,
    )


def evaluate_review_gate(
    repo: str,
    pr_number: int,
    *,
    summary_path: str | None = None,
    allow_medium_risk: bool = False,
) -> ReviewGateResult:
    """Read reviewer artifacts and produce a conservative gate decision."""
    if summary_path is None:
        summary_path = f"artifacts/runs/pr-{pr_number}-reviewer.summary.md"

    notes = [
        "No GitHub review was submitted.",
        "No PR approval was submitted.",
        "No merge was performed.",
        "No issue was closed.",
    ]

    if not os.path.isfile(summary_path):
        # Try raw as fallback for parsing
        raw_path = f"artifacts/runs/pr-{pr_number}-reviewer.raw.txt"
        if os.path.isfile(raw_path):
            with open(raw_path, encoding="utf-8") as f:
                text = f.read()
            opinion = parse_reviewer_opinion(text)
            return ReviewGateResult(
                pr_number=pr_number,
                status="blocked — reviewer summary artifact missing (raw fallback used for parse)",
                reason="summary artifact not found",
                opinion=opinion,
                gate_pass=False,
                merge_eligible=False,
                automerge_eligible=False,
                summary_path=raw_path,
                notes=notes,
            )
        return ReviewGateResult(
            pr_number=pr_number,
            status="blocked — reviewer summary artifact missing",
            reason="summary artifact not found",
            opinion=ReviewerOpinion(
                None, None, None, None, None, None, None, [], None, ""
            ),
            gate_pass=False,
            merge_eligible=False,
            automerge_eligible=False,
            summary_path=None,
            notes=notes,
        )

    with open(summary_path, encoding="utf-8") as f:
        text = f.read()

    # Prefer the structured section inside the summary (first 25 lines area)
    # but also parse the whole thing
    opinion = parse_reviewer_opinion(text)

    # Conservative gate logic
    gate_pass = False
    reason = ""
    risk_allowed = opinion.risk in ("low", "LOW") or (
        allow_medium_risk and opinion.risk in ("medium", "MEDIUM")
    )

    if opinion.verdict != "APPROVE":
        reason = f"reviewer verdict is {opinion.verdict or 'unknown'}"
    elif opinion.confidence is None or opinion.confidence < 0.85:
        reason = f"confidence below threshold (got {opinion.confidence})"
    elif not risk_allowed:
        reason = f"reviewer risk is {opinion.risk or 'unknown'}"
    elif opinion.scope_match not in ("yes", "YES"):
        reason = "scope match is no"
    elif opinion.ci_considered not in ("yes", "YES"):
        reason = "CI was not considered"
    elif opinion.merge_recommendation not in ("yes", "YES"):
        reason = "merge recommendation is no"
    else:
        gate_pass = True
        if opinion.risk in ("medium", "MEDIUM"):
            reason = (
                "reviewer approved with high confidence, medium risk explicitly allowed, "
                "green CI, and matching scope"
            )
        else:
            reason = (
                "reviewer approved with high confidence, low risk, green CI, "
                "and matching scope"
            )

    automerge_ok = gate_pass and opinion.automerge_eligible in ("yes", "YES")

    status = "pass" if gate_pass else f"blocked — {reason}"

    return ReviewGateResult(
        pr_number=pr_number,
        status=status,
        reason=reason,
        opinion=opinion,
        gate_pass=gate_pass,
        merge_eligible=gate_pass,  # conservative: gate pass == merge eligible for now
        automerge_eligible=automerge_ok,
        summary_path=summary_path,
        notes=notes,
    )


def format_review_gate(result: ReviewGateResult) -> str:
    """Compact deterministic output for the review gate."""
    o = result.opinion
    lines = [f"Signposter Review Gate — PR #{result.pr_number}\n"]

    lines.append("Reviewer opinion:")
    lines.append(f"  verdict: {o.verdict or 'unknown'}")
    lines.append(f"  confidence: {o.confidence if o.confidence is not None else 'unknown'}")
    lines.append(f"  risk: {o.risk or 'unknown'}")
    lines.append(f"  scope match: {o.scope_match or 'unknown'}")
    lines.append(f"  ci considered: {o.ci_considered or 'unknown'}")
    lines.append(f"  merge recommendation: {o.merge_recommendation or 'unknown'}")
    lines.append(f"  automerge eligible: {o.automerge_eligible or 'unknown'}")

    lines.append("\nGate:")
    lines.append(f"  status: {result.status}")
    lines.append(f"  reason: {result.reason}")

    lines.append("\nNext:")
    lines.append(f"  merge eligible: {'yes' if result.merge_eligible else 'no'}")
    lines.append(f"  automerge eligible: {'yes' if result.automerge_eligible else 'no'}")

    if result.notes:
        lines.append("\nNotes:")
        for n in result.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)


# =============================================================================
# HARDENING-018: GitHub PR review submission (plan + guarded --apply)
# =============================================================================


@dataclass(frozen=True)
class ReviewSubmitPlan:
    """Plan for submitting a GitHub review based on reviewer gate."""
    pr_number: int
    action: str  # "approve" | "request_changes" | "comment" | "blocked"
    body: str
    gate_pass: bool
    gate_reason: str
    status: str  # "ready" | "blocked — ..." | "ready-for-request-changes"
    gh_preview: str
    notes: list[str]

    # HARDENING-018A identity fields
    current_user: str | None = None
    pr_author: str | None = None
    reviewer_token_configured: bool = False
    self_review_blocked: bool = False
    failure_reason: str | None = None



def build_review_body(opinion: ReviewerOpinion, gate: ReviewGateResult) -> str:
    """Build a compact, safe review body for GitHub."""
    findings = "\n".join(f"- {f}" for f in opinion.findings[:5]) or "- No specific findings listed."

    body = f"""Signposter reviewer gate: {opinion.verdict or "UNKNOWN"}

Confidence: {opinion.confidence if opinion.confidence is not None else "unknown"}
Risk: {opinion.risk or "unknown"}
Scope match: {opinion.scope_match or "unknown"}
CI considered: {opinion.ci_considered or "unknown"}
Merge recommendation: {opinion.merge_recommendation or "unknown"}
Automerge eligible: {opinion.automerge_eligible or "unknown"}

Findings:
{findings}

Summary:
Reviewer { 'approved' if (opinion.verdict or '').upper() == 'APPROVE' else 'reviewed' } this change.

No merge or issue close is implied by this review.
"""
    return body.strip()


def plan_review_submit(
    repo: str,
    pr_number: int,
    *,
    allow_medium_risk: bool = False,
) -> ReviewSubmitPlan:
    """Produce a dry-run plan for submitting a GitHub PR review.

    HARDENING-018A: Includes GitHub identity checks and self-review guard.
    """
    gate = evaluate_review_gate(
        repo,
        pr_number,
        allow_medium_risk=allow_medium_risk,
    )

    notes = [
        "No GitHub review was submitted.",
        "No merge was performed.",
        "No issue was closed.",
    ]

    reviewer_token = _get_reviewer_token()
    token_configured = bool(reviewer_token)

    current_user = _fetch_current_gh_user(repo, reviewer_token)
    pr_author = _fetch_pr_author(repo, pr_number, reviewer_token)

    self_review_blocked = False
    failure_reason = None

    if not gate.gate_pass:
        action = "blocked"
        status = gate.status
        body = ""
        gh_preview = f"gh pr review {pr_number} -R {repo} --comment  # (blocked — no approval)"
    else:
        verdict = (gate.opinion.verdict or "").upper()

        # HARDENING-018A: Self-review guard (core of this hardening)
        if (
            verdict == "APPROVE"
            and current_user
            and pr_author
            and current_user == pr_author
            and not token_configured
        ):
            self_review_blocked = True
            action = "blocked"
            status = "blocked — cannot approve own pull request with current GitHub identity"
            failure_reason = "current GitHub identity is the PR author and cannot approve own PR"
            body = ""
            gh_preview = f"gh pr review {pr_number} -R {repo} --approve  # BLOCKED: self-review"
        elif verdict == "APPROVE":
            action = "approve"
            status = "ready"
            body = build_review_body(gate.opinion, gate)
            gh_preview = f"gh pr review {pr_number} -R {repo} --approve --body \"...\"" 
        elif verdict == "NEEDS_CHANGES":
            action = "request_changes"
            status = "ready-for-request-changes"
            body = build_review_body(gate.opinion, gate)
            gh_preview = f"gh pr review {pr_number} -R {repo} --request-changes --body \"...\"" 
        else:
            action = "comment"
            status = "blocked"
            body = build_review_body(gate.opinion, gate)
            gh_preview = f"gh pr review {pr_number} -R {repo} --comment --body \"...\"" 

    return ReviewSubmitPlan(
        pr_number=pr_number,
        action=action,
        body=body,
        gate_pass=gate.gate_pass,
        gate_reason=gate.reason,
        status=status,
        gh_preview=gh_preview,
        notes=notes,
        current_user=current_user,
        pr_author=pr_author,
        reviewer_token_configured=token_configured,
        self_review_blocked=self_review_blocked,
        failure_reason=failure_reason,
    )


def format_review_submit_plan(plan: ReviewSubmitPlan) -> str:
    """Compact output for the submit plan (dry-run)."""
    lines = [f"Signposter Review Submit Plan — PR #{plan.pr_number}\n"]

    lines.append("Reviewer gate:")
    lines.append(f"  status: {'pass' if plan.gate_pass else 'blocked'}")
    lines.append(f"  reason: {plan.gate_reason}")

    # HARDENING-018A identity section
    lines.append("\nGitHub identity:")
    lines.append(f"  current user: {plan.current_user or 'unknown'}")
    lines.append(f"  PR author: {plan.pr_author or 'unknown'}")
    token_label = 'configured' if plan.reviewer_token_configured else 'not configured'
    lines.append(f"  reviewer token: {token_label}")

    lines.append("\nGitHub review:")
    lines.append(f"  action: {plan.action}")
    if plan.failure_reason:
        lines.append(f"  reason: {plan.failure_reason}")
    if plan.body:
        lines.append(f"  body length: {len(plan.body)} chars")
    lines.append(f"  command preview: {plan.gh_preview}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.self_review_blocked:
        lines.append("")
        lines.append("Hint: Configure SIGNPOSTER_REVIEWER_GH_TOKEN with a bot/review account")
        lines.append("      to submit formal reviews when the current user is the PR author.")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)


def submit_review(
    repo: str,
    pr_number: int,
    *,
    apply: bool = False,
    allow_medium_risk: bool = False,
) -> dict:
    """Execute (or dry-run) the GitHub PR review submission.

    HARDENING-018A: Respects self-review identity guard.
    Only performs the gh mutation when apply=True and the plan is ready for approval.
    """
    plan = plan_review_submit(
        repo,
        pr_number,
        allow_medium_risk=allow_medium_risk,
    )

    if not apply:
        return {
            "mode": "dry_run",
            "plan": plan,
        }

    # Mutation path — extremely guarded
    if plan.self_review_blocked or plan.action != "approve" or plan.status != "ready":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": plan.failure_reason or f"Refusing to submit review: {plan.status}",
        }

    if not plan.body:
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": "Empty review body",
        }

    reviewer_token = _get_reviewer_token()

    # Write a temporary body file for safe quoting
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
        tf.write(plan.body)
        body_file = tf.name

    try:
        cmd = [
            "gh", "pr", "review", str(pr_number),
            "-R", repo,
            "--approve",
            "--body-file", body_file,
        ]
        proc = _run_gh_with_token(cmd, reviewer_token)

        success = proc.returncode == 0

        # Improved diagnostics on failure (HARDENING-018A)
        if not success:
            return {
                "mode": "apply",
                "plan": plan,
                "success": False,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "command": " ".join(cmd),
                "error": f"gh pr review failed: {proc.stderr.strip()[:400]}",
            }

        return {
            "mode": "apply",
            "plan": plan,
            "success": success,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "command": " ".join(cmd),
        }
    finally:
        try:
            os.unlink(body_file)
        except Exception:
            pass


# =============================================================================
# HARDENING-018A: Identity guard + SIGNPOSTER_REVIEWER_GH_TOKEN support
# =============================================================================


def _get_gh_env(token: str | None = None) -> dict:
    """Return env dict with GH_TOKEN injected if provided."""
    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token
    return env


def _fetch_current_gh_user(repo: str | None = None, token: str | None = None) -> str | None:
    """Fetch the login of the current authenticated gh user (or token)."""
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_get_gh_env(token),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _fetch_pr_author(repo: str, pr_number: int, token: str | None = None) -> str | None:
    """Fetch the login of the PR author."""
    try:
        result = subprocess.run(
            [
                "gh", "pr", "view", str(pr_number),
                "-R", repo,
                "--json", "author",
                "--jq", ".author.login",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=_get_gh_env(token),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _get_reviewer_token() -> str | None:
    """Return the dedicated reviewer token from env, if configured."""
    return os.environ.get("SIGNPOSTER_REVIEWER_GH_TOKEN")


def _is_reviewer_token_configured() -> bool:
    return bool(_get_reviewer_token())


def _run_gh_with_token(cmd: list[str], token: str | None) -> subprocess.CompletedProcess:
    """Run a gh command with optional dedicated token."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        env=_get_gh_env(token),
    )
