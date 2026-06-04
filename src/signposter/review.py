"""Reviewer-agent PR review planning (planning / dry-run only).

HARDENING-014: Provide a safe planning surface for reviewer
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

from signposter.artifact_safety import find_stale_or_failover_signal
from signposter.bug_ledger import (
    format_runtime_bug_ledger_record,
    record_runtime_bug_ledger_entry,
)
from signposter.codex_cli_backend import (
    execute_codex_cli_invocation,
    plan_codex_cli_invocation,
)
from signposter.comments import ensure_github_comment_body
from signposter.execution_backend import (
    build_backend_command_shape,
    resolve_execution_backend,
)
from signposter.openclaw_diagnostics import gather_openclaw_runtime_diagnostics
from signposter.openclaw_preflight import (
    check_openclaw_preflight,
    format_openclaw_preflight_block,
)
from signposter.openclaw_runtime import (
    OpenClawExecutionDiagnosis,
    classify_openclaw_execution,
    normalize_subprocess_output,
    openclaw_timeout_settings,
)
from signposter.role_routing import resolve_role_execution, select_role_for_review
from signposter.runner import build_openclaw_session_key
from signposter.token_usage import format_token_usage_accounting, summarize_token_usage

REVIEW_PROMPT_LIMITS = {
    "changed_files": 16,
    "pr_body_lines": 28,
    "pr_body_chars": 1600,
    "diff_lines": 90,
}


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
    proposed_runner: str = "openclaw"
    proposed_command_shape: str = ""
    backend_reason: str = "default Codex CLI execution backend"
    backend_execution_supported: bool = True
    backend_notes: tuple[str, ...] = ()
    selected_role_name: str = "REVIEWER_LIGHT"
    selected_model: str = "openai/gpt-5.4-mini"
    selected_reasoning_effort: str = "low"
    selected_openclaw_agent: str = "reviewer_light"
    role_selection_reason: str = "default review role selection"


def _git_output(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    return text or None


def _artifact_search_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(path: str | None) -> None:
        if not path:
            return
        resolved = str(Path(path).expanduser().resolve())
        if resolved in seen:
            return
        seen.add(resolved)
        roots.append(Path(resolved))

    add_root(_git_output(["rev-parse", "--show-toplevel"]))

    common_git_dir = _git_output(["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common_git_dir:
        add_root(str(Path(common_git_dir).resolve().parent))

    worktree_output = _git_output(["worktree", "list", "--porcelain"])
    if worktree_output:
        for line in worktree_output.splitlines():
            if line.startswith("worktree "):
                add_root(line.split(" ", 1)[1].strip())

    if not roots:
        add_root(str(Path.cwd()))

    return tuple(roots)


def _preferred_artifact_path(path: str) -> str:
    artifact = Path(path)
    if artifact.is_absolute():
        return str(artifact)
    return str(_artifact_search_roots()[0] / artifact)


def _resolve_existing_artifact_path(path: str) -> str | None:
    artifact = Path(path)
    if artifact.is_absolute():
        return str(artifact) if os.path.isfile(artifact) else None

    for root in _artifact_search_roots():
        candidate = root / artifact
        if os.path.isfile(candidate):
            return str(candidate)

    return None


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


def _fetch_pr_file_paths(repo: str, pr: int) -> list[str]:
    """Fetch changed file paths for routing and review heuristics."""
    data = _run_gh_pr_view(repo, pr, ["files"])
    files = data.get("files", []) or []
    return [file_info.get("path", "") for file_info in files if isinstance(file_info, dict)]


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
        "review",
        "merge",
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


def plan_review_for_pr(
    repo: str,
    pr_number: int,
    *,
    allow_high_risk: bool = False,
    backend: str | None = None,
) -> ReviewPlan:
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
        file_paths = _fetch_pr_file_paths(repo, pr_number)
    except Exception:
        files_info = {"files_changed": 0, "additions": 0, "deletions": 0}
        file_paths = []

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
    elif risk == "high" and not allow_high_risk:
        status = "blocked — high risk change detected"
    elif associated_issue is None:
        status = "blocked — could not map PR to a Signposter issue number"

    notes = [
        "No review was executed.",
        "No GitHub review was submitted.",
        "No merge was performed.",
        "No issue was closed.",
    ]

    if allow_high_risk:
        notes.append("High-risk override explicitly allowed by operator.")
        if risk == "high":
            notes.append("High-risk review planning is proceeding via explicit override.")

    role_selection = select_role_for_review(
        risk_level=risk,
        size=size,
        file_paths=file_paths,
    )
    backend_plan = resolve_execution_backend(backend)
    role_execution = resolve_role_execution(role_selection, backend=backend_plan.backend)
    session_key = build_openclaw_session_key(
        target_kind="pr",
        target_number=pr_number,
        profile="reviewer",
    )
    prompt_path = f"artifacts/prompts/pr-{pr_number}-review.md"
    command_shape = build_backend_command_shape(
        backend=backend_plan.backend,
        agent=role_execution.execution_agent,
        session_key=session_key,
        model=role_execution.model,
        reasoning_effort=role_execution.reasoning_effort,
        prompt_path=prompt_path,
    )

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
        prompt_artifact_path=prompt_path,
        proposed_runner=backend_plan.backend,
        proposed_command_shape=command_shape,
        backend_reason=backend_plan.reason,
        backend_execution_supported=backend_plan.execution_supported,
        backend_notes=backend_plan.notes,
        selected_role_name=role_selection.policy.name,
        selected_model=role_execution.model,
        selected_reasoning_effort=role_execution.reasoning_effort,
        selected_openclaw_agent=role_execution.execution_agent,
        role_selection_reason=role_selection.reason,
    )


def _review_check_blockage_lines(plan: ReviewPlan) -> list[str]:
    """Return bounded operator diagnostics for PR check states that stop review."""
    if plan.checks_status == "failing":
        return [
            "category: failing-ci",
            (
                "reason: "
                f"{plan.failing_checks} failing check(s), "
                f"{plan.pending_checks} pending check(s)"
            ),
            f"next: inspect failing checks for PR #{plan.pr_number} and rerun review plan",
        ]
    if plan.checks_status == "pending":
        return [
            "category: waiting-ci",
            (
                "reason: "
                f"{plan.pending_checks} pending check(s), "
                f"{plan.successful_checks} successful check(s)"
            ),
            "next: wait for CI completion and rerun review plan",
        ]
    if plan.checks_status == "unknown":
        return [
            "category: unknown-ci",
            "reason: GitHub check rollup is unavailable or ambiguous",
            "next: inspect PR checks manually if this persists",
        ]
    return []


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
    check_blockage = _review_check_blockage_lines(plan)
    if check_blockage:
        lines.append("\nCheck blockage:")
        for line in check_blockage:
            lines.append(f"  {line}")

    lines.append("\nScope:")
    lines.append(f"  files changed: {plan.files_changed}")
    lines.append(f"  additions: {plan.additions}")
    lines.append(f"  deletions: {plan.deletions}")
    lines.append(f"  risk: {plan.risk_level}")
    lines.append(f"  size: {plan.size}")

    lines.append("\nReviewer:")
    lines.append(f"  backend: {plan.proposed_runner}")
    lines.append(f"  agent: {plan.reviewer_profile}")
    lines.append(f"  selected role: {plan.selected_role_name}")
    lines.append(f"  role agent: {plan.selected_openclaw_agent}")
    lines.append(f"  model: {plan.selected_model}")
    lines.append(f"  reasoning: {plan.selected_reasoning_effort}")
    lines.append(f"  execute ready: {'yes' if plan.backend_execution_supported else 'no'}")
    lines.append(f"  prompt artifact: {plan.prompt_artifact_path}")
    lines.append(f"  command shape: {plan.proposed_command_shape}")
    lines.append(f"  backend reason: {plan.backend_reason}")
    lines.append(f"  reason: {plan.role_selection_reason}")
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
    if plan.backend_notes:
        lines.append("\nBackend notes:")
        for n in plan.backend_notes:
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
        omitted = len(lines) - max_lines
        diff = (
            "\n".join(lines[:max_lines])
            + "\n\n# Omitted due to budget"
            + f"\n# {omitted} diff lines omitted from {len(lines)} total lines."
        )
    return diff or "<no diff content>"


def _format_authoritative_changed_files(
    file_paths: list[str],
    *,
    max_files: int = REVIEW_PROMPT_LIMITS["changed_files"],
) -> str:
    if not file_paths:
        return "- <no changed files returned by GitHub>"
    selected = file_paths[:max_files]
    lines = [f"- {path}" for path in selected]
    omitted = len(file_paths) - len(selected)
    if omitted > 0:
        lines.append(f"- ...[omitted {omitted} additional changed files]")
    return "\n".join(lines)


def _compact_review_text(
    text: str,
    *,
    max_lines: int,
    max_chars: int,
    empty_fallback: str,
) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return empty_fallback

    lines = normalized.splitlines()
    selected: list[str] = []
    consumed = 0
    for line in lines:
        line_cost = len(line) + (1 if selected else 0)
        if len(selected) >= max_lines or consumed + line_cost > max_chars:
            break
        selected.append(line)
        consumed += line_cost

    excerpt = "\n".join(selected).strip()

    while True:
        omitted_lines = max(len(lines) - len(selected), 0)
        omitted_chars = max(len(normalized) - len(excerpt), 0)
        if not omitted_lines and not omitted_chars:
            return excerpt or empty_fallback

        omission_marker = f"...[omitted {omitted_lines} lines, {omitted_chars} chars]"
        candidate = f"{excerpt}\n{omission_marker}".strip() if excerpt else omission_marker
        if len(selected) <= max_lines and len(candidate) <= max_chars:
            return candidate

        if selected:
            selected.pop()
            excerpt = "\n".join(selected).strip()
            continue

        if len(omission_marker) > max_chars:
            return omission_marker[:max_chars].rstrip()
        return omission_marker


def build_review_prompt(
    plan: ReviewPlan,
    pr_body: str,
    diff: str,
    *,
    file_paths: list[str] | None = None,
) -> str:
    """Render the complete reviewer prompt with structured output contract."""
    plan_notes = "\n".join(f"- {n}" for n in plan.notes) if plan.notes else "- none"
    pr_body_excerpt = _compact_review_text(
        pr_body,
        max_lines=REVIEW_PROMPT_LIMITS["pr_body_lines"],
        max_chars=REVIEW_PROMPT_LIMITS["pr_body_chars"],
        empty_fallback="<no body provided>",
    )

    issue_line = (
        f"Issue #{plan.associated_issue}"
        if plan.associated_issue
        else "No associated issue detected via branch convention"
    )
    body_lines_limit = REVIEW_PROMPT_LIMITS["pr_body_lines"]
    body_chars_limit = REVIEW_PROMPT_LIMITS["pr_body_chars"]
    changed_files_limit = REVIEW_PROMPT_LIMITS["changed_files"]
    diff_lines_limit = REVIEW_PROMPT_LIMITS["diff_lines"]

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
- Plan notes:
{plan_notes}

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

## Prompt Budget
- Changed files shown: first {changed_files_limit} paths
- PR body excerpt: max {body_lines_limit} lines / {body_chars_limit} chars
- Diff excerpt: max {diff_lines_limit} lines
- Omitted sections are marked explicitly and should be treated as bounded evidence.

## Selected Role Policy
- backend: {plan.proposed_runner}
- backend reason: {plan.backend_reason}
- role identity: {plan.selected_role_name}
- selected model: {plan.selected_model}
- selected reasoning effort: {plan.selected_reasoning_effort}
- Execution agent/profile: {plan.reviewer_profile}
- role selection reason: {plan.role_selection_reason}
- command shape: {plan.proposed_command_shape}

## Prompt Contract
- expected output format: structured review opinion exactly matching the format below
- artifact requirements: raw backend output stays local; GitHub comments and
  reviews must use bounded summaries
- uncertainty handling: prefer NEEDS_CHANGES or BLOCK when evidence is insufficient

## Changed Files Excerpt (from GitHub metadata, bounded)
{_format_authoritative_changed_files(file_paths or [])}

Treat the list above as a bounded excerpt of the GitHub changed-file metadata.
If it includes an omitted marker, use the file count and diff excerpt as evidence that the
true scope is broader than the displayed list.

## PR Body (bounded)
{pr_body_excerpt}

## Diff (budgeted excerpt)
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


def write_review_prompt_artifact(
    repo: str,
    pr_number: int,
    *,
    allow_high_risk: bool = False,
) -> str:
    """Generate the review plan, then write the reviewer prompt artifact if ready.

    Returns the absolute path of the written file.
    Raises RuntimeError if the review plan status is not "ready".
    """
    plan = plan_review_for_pr(repo, pr_number, allow_high_risk=allow_high_risk)

    if plan.status != "ready":
        raise RuntimeError(f"Refusing to write prompt artifact: {plan.status}")

    # Fetch fresh body (plan may have truncated or partial)
    try:
        pr_data = _run_gh_pr_view(repo, pr_number, ["body"])
        pr_body = pr_data.get("body", "") or ""
    except Exception:
        pr_body = ""

    diff = get_pr_diff(repo, pr_number, max_lines=REVIEW_PROMPT_LIMITS["diff_lines"])
    file_paths = _fetch_pr_file_paths(repo, pr_number)

    content = build_review_prompt(plan, pr_body, diff, file_paths=file_paths)

    path = _preferred_artifact_path(plan.prompt_artifact_path)
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
    diagnosis: OpenClawExecutionDiagnosis | None = None,
    diagnostics_warnings: tuple[str, ...] = (),
) -> str:
    """Generate a bounded mechanical summary for a PR reviewer execution."""
    lines = [
        "# Signposter PR Reviewer Execution Summary\n",
        "**Repository:** (from plan)",
        f"**PR:** #{pr_number}",
        f"**Title:** {plan.title}",
        "**Agent:** reviewer",
        f"**Selected Role:** {plan.selected_role_name}",
        f"**Selected Model:** {plan.selected_model}",
        f"**Selected Reasoning Effort:** {plan.selected_reasoning_effort}",
        f"**Role Selection Reason:** {plan.role_selection_reason}",
        f"**Session Key:** {session_key}",
        f"**Prompt Artifact:** {plan.prompt_artifact_path}",
        f"**Started (UTC):** {start_time.isoformat()}",
        f"**Exit Code:** {exit_code}",
        f"**Raw Output:** {raw_path}",
        "",
    ]
    if diagnosis is not None:
        lines.append(f"**Execution Status:** {diagnosis.status}")
        lines.append(f"**Execution Reason:** {diagnosis.reason}")

    raw_text = stdout + ("\n" + stderr if stderr else "")
    token_usage = summarize_token_usage(
        role=plan.selected_role_name,
        model=plan.selected_model,
        reasoning_effort=plan.selected_reasoning_effort,
        output_text=raw_text,
    )
    lines.append(f"**Token Usage Status:** {token_usage.status}")
    line_count = len(raw_text.splitlines())
    byte_count = len(raw_text.encode("utf-8"))
    lines.append(f"**Output Size:** {line_count} lines, {byte_count} bytes")
    if diagnosis is not None and diagnosis.remediation:
        lines.append("\n## Remediation\n")
        lines.extend(f"- {item}" for item in diagnosis.remediation)
    if diagnostics_warnings:
        lines.append("\n## Runtime warnings\n")
        lines.extend(f"- {warning}" for warning in diagnostics_warnings)

    lines.append("")
    lines.extend(format_token_usage_accounting(token_usage).splitlines())

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
    allow_high_risk: bool = False,
    backend: str | None = None,
) -> dict:
    """Execute the reviewer agent locally against an existing PR review prompt artifact.

    Purely local execution. Writes raw + summary artifacts under artifacts/runs/.
    Never posts to GitHub.

    Returns a dict with execution result.
    """
    # Guard: plan must be ready
    plan = plan_review_for_pr(repo, pr_number, allow_high_risk=allow_high_risk, backend=backend)
    if plan.status != "ready":
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": f"review plan not ready: {plan.status}",
            "success": False,
        }
    if not plan.backend_execution_supported:
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": (
                f"execution backend '{plan.proposed_runner}' is not implemented for review execute"
            ),
            "success": False,
        }

    prompt_path = _resolve_existing_artifact_path(plan.prompt_artifact_path)
    if not prompt_path:
        try:
            prompt_path = write_review_prompt_artifact(
                repo,
                pr_number,
                allow_high_risk=allow_high_risk,
            )
        except Exception as e:
            return {
                "exit_code": 1,
                "raw_path": None,
                "summary_path": None,
                "error": f"prompt artifact missing and could not be written: {e}",
                "success": False,
            }

    session_key = build_openclaw_session_key(
        target_kind="pr",
        target_number=pr_number,
        profile=profile,
    )
    timeout_settings = openclaw_timeout_settings()
    execute_timeout = timeout_settings.execute_timeout
    subprocess_timeout = timeout_settings.subprocess_timeout
    config_error = getattr(timeout_settings, "config_error", None)

    if plan.proposed_runner == "codex-cli":
        runs_dir = Path(runs_dir)
        raw_path = runs_dir / f"pr-{pr_number}-{profile}.raw.txt"
        summary_path = runs_dir / f"pr-{pr_number}-{profile}.summary.md"
        last_message_path = runs_dir / f"pr-{pr_number}-{profile}.last-message.txt"
        invocation = plan_codex_cli_invocation(
            agent=plan.selected_openclaw_agent,
            session_key=session_key,
            model=plan.selected_model,
            reasoning_effort=plan.selected_reasoning_effort,
            prompt_path=prompt_path,
            working_dir=".",
            output_last_message_path=last_message_path,
            timeout_seconds=execute_timeout,
        )
        result = execute_codex_cli_invocation(
            invocation,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": result.exit_code,
            "raw_path": str(result.raw_path),
            "summary_path": str(result.summary_path),
            "success": result.success,
            "error": None if result.success else result.reason,
            "diagnosis_status": result.status,
        }

    preflight = check_openclaw_preflight(artifact_kind="review", target=pr_number)
    if not preflight.ok:
        print(format_openclaw_preflight_block(preflight))
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": preflight.reason,
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

    diagnostics = gather_openclaw_runtime_diagnostics()
    diagnostics_warnings = diagnostics.warnings + timeout_settings.warnings

    if config_error:
        runs_dir = Path(runs_dir)
        runs_dir.mkdir(parents=True, exist_ok=True)
        raw_path = runs_dir / f"pr-{pr_number}-reviewer.raw.txt"
        summary_path = runs_dir / f"pr-{pr_number}-reviewer.summary.md"
        combined = "[CONFIG ERROR]\n" + config_error
        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = OpenClawExecutionDiagnosis(
            status="config-error",
            reason=config_error,
            remediation=(
                "Fix the timeout environment configuration before rerunning OpenClaw.",
                "Do not continue review or merge automation with invalid timeout bounds.",
            ),
        )
        summary = _generate_pr_reviewer_summary(
            pr_number=pr_number,
            plan=plan,
            session_key=session_key,
            exit_code=-1,
            raw_path=str(raw_path),
            stdout="",
            stderr=config_error,
            start_time=datetime.datetime.now(datetime.UTC),
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_review_runtime_bug(
            pr_number=pr_number,
            plan=plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": -1,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": False,
            "error": diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }

    exec_cmd = [
        "openclaw", "agent",
        "--agent", plan.reviewer_profile,
        "--session-key", session_key,
        "--model", plan.selected_model,
        "--thinking", plan.selected_reasoning_effort,
        "--message", prompt_content,
        "--local",
        "--timeout", str(execute_timeout),
    ]

    print(
        "Running: "
        f"openclaw agent --agent {plan.reviewer_profile} "
        f"--session-key {session_key} --model {plan.selected_model} "
        f"--thinking {plan.selected_reasoning_effort} --local --timeout {execute_timeout}"
    )
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
            timeout=subprocess_timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout
        if stderr:
            combined += "\n\n=== STDERR ===\n" + stderr

        exit_code = proc.returncode

        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = classify_openclaw_execution(
            exit_code=exit_code,
            combined_output=combined,
            timed_out=False,
            diagnostics_warnings=diagnostics_warnings,
        )

        summary = _generate_pr_reviewer_summary(
            pr_number=pr_number,
            plan=plan,
            session_key=session_key,
            exit_code=exit_code,
            raw_path=str(raw_path),
            stdout=stdout,
            stderr=stderr,
            start_time=start_time,
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_review_runtime_bug(
            pr_number=pr_number,
            plan=plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )

        return {
            "exit_code": exit_code,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": diagnosis.status == "success",
            "error": None if diagnosis.status == "success" else diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }

    except subprocess.TimeoutExpired as e:
        stdout = normalize_subprocess_output(e.stdout)
        stderr = normalize_subprocess_output(e.stderr)
        combined = f"[TIMEOUT after {subprocess_timeout}s]\n"
        if stdout:
            combined += stdout
        if stderr:
            combined += "\n\n=== STDERR ===\n" + stderr
        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = classify_openclaw_execution(
            exit_code=None,
            combined_output=combined,
            timed_out=True,
            diagnostics_warnings=diagnostics_warnings,
            timeout_seconds=subprocess_timeout,
        )
        summary = _generate_pr_reviewer_summary(
            pr_number=pr_number,
            plan=plan,
            session_key=session_key,
            exit_code=-1,
            raw_path=str(raw_path),
            stdout=stdout,
            stderr=stderr,
            start_time=start_time,
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_review_runtime_bug(
            pr_number=pr_number,
            plan=plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": -1,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": False,
            "error": diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }
    except Exception as e:
        combined = f"[ERROR]\n{e}"
        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = classify_openclaw_execution(
            exit_code=-1,
            combined_output=combined,
            timed_out=False,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary = _generate_pr_reviewer_summary(
            pr_number=pr_number,
            plan=plan,
            session_key=session_key,
            exit_code=-1,
            raw_path=str(raw_path),
            stdout="",
            stderr=str(e),
            start_time=start_time,
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_review_runtime_bug(
            pr_number=pr_number,
            plan=plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": -1,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": False,
            "error": diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }


def _record_review_runtime_bug(
    *,
    pr_number: int,
    plan: ReviewPlan,
    diagnosis: OpenClawExecutionDiagnosis,
    raw_path: Path,
    summary_path: Path,
) -> None:
    record = record_runtime_bug_ledger_entry(
        target_kind="pr",
        target_number=pr_number,
        diagnosis_status=diagnosis.status,
        diagnosis_reason=diagnosis.reason,
        selected_role=plan.selected_role_name,
        selected_model=plan.selected_model,
        raw_path=str(raw_path),
        summary_path=str(summary_path),
    )
    summary_path.write_text(
        summary_path.read_text(encoding="utf-8")
        + "\n## Bug ledger\n\n"
        + format_runtime_bug_ledger_record(record)
        + "\n",
        encoding="utf-8",
    )


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


@dataclass(frozen=True)
class ReviewArtifactValidation:
    pr_number: int
    summary_path: str
    status: str
    errors: list[str]
    opinion: ReviewerOpinion
    notes: list[str]
    raw_path: str | None = None
    raw_exists: bool = False
    raw_stale_signal: str | None = None
    guidance: list[str] | None = None


_REVIEW_VALID_RISKS = ("low", "medium", "high")
_REVIEW_VALID_YES_NO = ("yes", "no")
_REVIEW_MANUAL_TAKEOVER_GUIDANCE = [
    (
        "manual reviewer summary required fields: Verdict, Confidence, Risk, "
        "Scope match, CI considered, Merge recommendation, Automerge eligible"
    ),
    (
        "manual reviewer summary required sections: Findings, Reasoning summary, "
        "Validation considered, Safety notes"
    ),
]


def _normalized_review_value(value: str | None) -> str | None:
    return value.strip().lower() if value is not None else None


def _format_review_field_value(value: str | None) -> str:
    return value if value is not None else "missing"


def _review_confidence_contract_error(
    confidence: float | None,
    *,
    threshold: float | None = None,
) -> str | None:
    if confidence is None:
        return "Confidence must be present and parseable"
    if confidence < 0 or confidence > 1:
        return "Confidence must be between 0 and 1"
    if threshold is not None and confidence < threshold:
        return f"Confidence below threshold ({confidence} < {threshold})"
    return None


def _review_enum_contract_error(
    field: str,
    value: str | None,
    allowed: tuple[str, ...],
) -> str | None:
    normalized = _normalized_review_value(value)
    if normalized in allowed:
        return None
    if len(allowed) == 2:
        choices = f"{allowed[0]} or {allowed[1]}"
    else:
        choices = ", ".join(allowed[:-1]) + f", or {allowed[-1]}"
    return f"{field} must be {choices}"


def _review_artifact_value(value: str | float | None) -> str:
    if value is None:
        return "missing"
    return str(value)


def _review_allowed_text(allowed: tuple[str, ...]) -> str:
    if len(allowed) == 2:
        return f"{allowed[0]} or {allowed[1]}"
    return ", ".join(allowed[:-1]) + f", or {allowed[-1]}"


def _review_artifact_enum_error(
    field: str,
    value: str | None,
    allowed: tuple[str, ...],
) -> str | None:
    if _normalized_review_value(value) in allowed:
        return None
    return (
        f"{field}: expected {_review_allowed_text(allowed)}; "
        f"got {_review_artifact_value(value)}"
    )


def _review_artifact_confidence_error(
    confidence: float | None,
    *,
    threshold: float,
) -> str | None:
    if confidence is None:
        return "Confidence: expected decimal 0..1; got missing or unparsable"
    if confidence < 0 or confidence > 1:
        return f"Confidence: expected 0..1; got {confidence}"
    if confidence < threshold:
        return f"Confidence: expected >= {threshold}; got {confidence}"
    return None


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


def validate_review_artifact(
    pr_number: int,
    *,
    summary_path: str | None = None,
    confidence_threshold: float = 0.85,
) -> ReviewArtifactValidation:
    """Validate the structured reviewer summary contract without side effects."""
    if summary_path is None:
        summary_path = f"artifacts/runs/pr-{pr_number}-reviewer.summary.md"
    resolved_summary_path = _resolve_existing_artifact_path(summary_path)

    notes = [
        "No GitHub review was submitted.",
        "No merge was performed.",
        "No issue was closed.",
    ]

    if not resolved_summary_path:
        raw_path = _review_raw_path_for_summary(summary_path)
        raw_exists = raw_path is not None and Path(raw_path).exists()
        raw_stale_signal = (
            find_stale_or_failover_signal(Path(raw_path).read_text(encoding="utf-8"))
            if raw_path and raw_exists
            else None
        )
        return ReviewArtifactValidation(
            pr_number=pr_number,
            summary_path=summary_path,
            status="blocked",
            errors=[f"summary artifact missing: {summary_path}"],
            opinion=ReviewerOpinion(None, None, None, None, None, None, None, [], None, ""),
            notes=notes,
            raw_path=raw_path,
            raw_exists=raw_exists,
            raw_stale_signal=raw_stale_signal,
            guidance=[
                "write a parser-compatible reviewer summary before review gate",
                "keep raw reviewer output local for diagnostic evidence",
                *_REVIEW_MANUAL_TAKEOVER_GUIDANCE,
            ],
        )

    with open(resolved_summary_path, encoding="utf-8") as f:
        text = f.read()
    opinion = parse_reviewer_opinion(text)
    errors: list[str] = []
    stale_signal = find_stale_or_failover_signal(text)
    raw_path = _review_raw_path_for_summary(resolved_summary_path)
    raw_exists = raw_path is not None and Path(raw_path).exists()
    raw_stale_signal = (
        find_stale_or_failover_signal(Path(raw_path).read_text(encoding="utf-8"))
        if raw_path and raw_exists
        else None
    )

    if stale_signal:
        errors.append(f"Artifact contains unsafe execution marker: {stale_signal}")
    if raw_stale_signal:
        errors.append(f"Raw artifact contains unsafe execution marker: {raw_stale_signal}")
    if opinion.verdict not in ("APPROVE", "NEEDS_CHANGES", "BLOCK"):
        errors.append(
            "Verdict: expected APPROVE, NEEDS_CHANGES, or BLOCK; "
            f"got {_review_artifact_value(opinion.verdict)}"
        )
    confidence_error = _review_artifact_confidence_error(
        opinion.confidence,
        threshold=confidence_threshold,
    )
    if confidence_error:
        errors.append(confidence_error)
    for field, value, allowed in (
        ("Risk", opinion.risk, _REVIEW_VALID_RISKS),
        ("Scope match", opinion.scope_match, _REVIEW_VALID_YES_NO),
        ("CI considered", opinion.ci_considered, _REVIEW_VALID_YES_NO),
        ("Merge recommendation", opinion.merge_recommendation, _REVIEW_VALID_YES_NO),
        ("Automerge eligible", opinion.automerge_eligible, _REVIEW_VALID_YES_NO),
    ):
        field_error = _review_artifact_enum_error(field, value, allowed)
        if field_error:
            errors.append(field_error)
    errors.extend(
        f"Schema: missing {field}"
        for field in _missing_reviewer_summary_schema_fields(text)
    )

    return ReviewArtifactValidation(
        pr_number=pr_number,
        summary_path=resolved_summary_path,
        status="ready" if not errors else "blocked",
        errors=errors,
        opinion=opinion,
        notes=notes,
        raw_path=raw_path,
        raw_exists=raw_exists,
        raw_stale_signal=raw_stale_signal,
        guidance=_review_artifact_guidance(
            errors=errors,
            raw_exists=raw_exists,
        ),
    )


def _review_raw_path_for_summary(summary_path: str) -> str | None:
    path = Path(summary_path)
    raw_name = (
        path.name.removesuffix(".summary.md") + ".raw.txt"
        if path.name.endswith(".summary.md")
        else f"pr-{path.stem}-reviewer.raw.txt"
    )
    raw_path = path.with_name(raw_name)
    if raw_path.is_absolute():
        return str(raw_path)
    resolved = _resolve_existing_artifact_path(str(raw_path))
    return resolved or str(raw_path)


def _missing_reviewer_summary_schema_fields(text: str) -> list[str]:
    lowered = (text or "").lower()
    required_any = {
        "agent or backend metadata": ("agent:", "**agent:**", "backend:", "**backend:**"),
        "pr number": ("pr: #", "**pr:** #"),
    }
    missing = [
        field
        for field, needles in required_any.items()
        if not any(needle in lowered for needle in needles)
    ]
    required = {
        "findings section": "findings:",
        "reasoning summary": "reasoning summary:",
        "validation considered section": "## validation considered",
        "safety notes section": "## safety notes",
        "no github review safety note": "no github review was submitted",
        "no pr approval safety note": "no pr approval was submitted",
        "no merge safety note": "no merge was performed",
        "no issue close safety note": "no issue was closed",
    }
    missing.extend(
        field for field, needle in required.items() if needle not in lowered
    )
    return missing


def _review_artifact_guidance(*, errors: list[str], raw_exists: bool) -> list[str]:
    guidance: list[str] = []
    if errors:
        guidance.append("repair reviewer artifact before review gate or submit")
        guidance.extend(_REVIEW_MANUAL_TAKEOVER_GUIDANCE)
    if any("unsafe execution marker" in error.lower() for error in errors):
        guidance.append(
            "preserve unsafe backend output separately and provide clean reviewer evidence"
        )
    if not raw_exists:
        guidance.append("raw reviewer artifact not found; keep raw local for backend runs")
    if not guidance:
        guidance.append("reviewer artifact contract is ready for review gate")
    return guidance


def format_review_artifact_validation(result: ReviewArtifactValidation) -> str:
    """Format review artifact validation output."""
    o = result.opinion
    lines = [f"Signposter Review Artifact Validation — PR #{result.pr_number}\n"]
    lines.append("Artifact:")
    lines.append(f"  summary: {result.summary_path}")
    lines.append("")
    lines.append("Parsed fields:")
    lines.append(f"  verdict: {o.verdict or 'unknown'}")
    lines.append(f"  confidence: {o.confidence if o.confidence is not None else 'unknown'}")
    lines.append(f"  risk: {o.risk or 'unknown'}")
    lines.append(f"  scope match: {o.scope_match or 'unknown'}")
    lines.append(f"  ci considered: {o.ci_considered or 'unknown'}")
    lines.append(f"  merge recommendation: {o.merge_recommendation or 'unknown'}")
    lines.append(f"  automerge eligible: {o.automerge_eligible or 'unknown'}")
    lines.append("")
    lines.append("Status:")
    lines.append(f"  {result.status}")
    if result.raw_path:
        lines.append("")
        lines.append("Raw artifact:")
        lines.append(f"  path: {result.raw_path}")
        lines.append(f"  exists: {'yes' if result.raw_exists else 'no'}")
    if result.raw_stale_signal:
        lines.append("")
        lines.append("Raw unsafe marker:")
        lines.append(f"  {result.raw_stale_signal}")
    if result.errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"  - {error}" for error in result.errors)
    guidance = result.guidance or []
    if guidance:
        lines.append("")
        lines.append("Guidance:")
        lines.extend(f"  - {item}" for item in guidance)
    lines.append("")
    lines.append("Notes:")
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def format_review_artifact_validation_summary(result: ReviewArtifactValidation) -> str:
    """Format concise review artifact validation output for automation logs."""
    o = result.opinion
    first_error = result.errors[0] if result.errors else "none"
    lines = [
        "Signposter Review Artifact Summary",
        f"pr: #{result.pr_number}",
        f"status: {result.status}",
        f"verdict: {o.verdict or 'unknown'}",
        f"confidence: {o.confidence if o.confidence is not None else 'unknown'}",
        f"risk: {o.risk or 'unknown'}",
        f"error: {first_error}",
    ]
    return "\n".join(lines)


def evaluate_review_gate(
    repo: str,
    pr_number: int,
    *,
    summary_path: str | None = None,
    allow_medium_risk: bool = False,
    allow_high_risk: bool = False,
) -> ReviewGateResult:
    """Read reviewer artifacts and produce a conservative gate decision."""
    if summary_path is None:
        summary_path = f"artifacts/runs/pr-{pr_number}-reviewer.summary.md"
    resolved_summary_path = _resolve_existing_artifact_path(summary_path)

    notes = [
        "No GitHub review was submitted.",
        "No PR approval was submitted.",
        "No merge was performed.",
        "No issue was closed.",
    ]

    if allow_high_risk:
        notes.append("High-risk override explicitly allowed by operator.")

    if not resolved_summary_path:
        # Try raw as fallback for parsing
        raw_path = _resolve_existing_artifact_path(
            f"artifacts/runs/pr-{pr_number}-reviewer.raw.txt"
        )
        if raw_path:
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

    artifact_validation = validate_review_artifact(
        pr_number,
        summary_path=resolved_summary_path,
    )
    unsafe_artifact_errors = [
        error for error in artifact_validation.errors
        if "unsafe execution marker" in error.lower()
    ]
    if unsafe_artifact_errors:
        reason = _review_artifact_gate_reason(unsafe_artifact_errors[0])
        return ReviewGateResult(
            pr_number=pr_number,
            status=f"blocked — reviewer artifact preflight: {reason}",
            reason=reason,
            opinion=artifact_validation.opinion,
            gate_pass=False,
            merge_eligible=False,
            automerge_eligible=False,
            summary_path=resolved_summary_path,
            notes=notes,
        )

    with open(resolved_summary_path, encoding="utf-8") as f:
        text = f.read()

    stale_signal = find_stale_or_failover_signal(text)
    if stale_signal:
        opinion = parse_reviewer_opinion(text)
        return ReviewGateResult(
            pr_number=pr_number,
            status=f"blocked — reviewer artifact contains stale/failover signal: {stale_signal}",
            reason=f"reviewer artifact contains stale/failover signal: {stale_signal}",
            opinion=opinion,
            gate_pass=False,
            merge_eligible=False,
            automerge_eligible=False,
            summary_path=resolved_summary_path,
            notes=notes,
        )

    # Prefer the structured section inside the summary (first 25 lines area)
    # but also parse the whole thing
    opinion = parse_reviewer_opinion(text)

    # Conservative gate logic
    gate_pass = False
    reason = ""
    risk = _normalized_review_value(opinion.risk)
    scope_match = _normalized_review_value(opinion.scope_match)
    ci_considered = _normalized_review_value(opinion.ci_considered)
    merge_recommendation = _normalized_review_value(opinion.merge_recommendation)
    automerge_eligible = _normalized_review_value(opinion.automerge_eligible)

    risk_allowed = (
        risk == "low"
        or (allow_medium_risk and risk == "medium")
        or (allow_high_risk and risk == "high")
    )

    if opinion.verdict != "APPROVE":
        reason = f"reviewer verdict is {opinion.verdict or 'unknown'}"
    elif confidence_error := _review_confidence_contract_error(opinion.confidence):
        reason = confidence_error.replace("Confidence", "reviewer confidence", 1)
    elif opinion.confidence is not None and opinion.confidence < 0.85:
        reason = f"confidence below threshold (got {opinion.confidence})"
    elif risk_error := _review_enum_contract_error(
        "reviewer risk",
        opinion.risk,
        _REVIEW_VALID_RISKS,
    ):
        reason = f"{risk_error} (got {_format_review_field_value(opinion.risk)})"
    elif not risk_allowed:
        reason = f"reviewer risk is {risk or 'unknown'}"
    elif scope_error := _review_enum_contract_error(
        "scope match",
        opinion.scope_match,
        _REVIEW_VALID_YES_NO,
    ):
        reason = f"{scope_error} (got {_format_review_field_value(opinion.scope_match)})"
    elif scope_match != "yes":
        reason = "scope match is no"
    elif ci_error := _review_enum_contract_error(
        "CI considered",
        opinion.ci_considered,
        _REVIEW_VALID_YES_NO,
    ):
        reason = f"{ci_error} (got {_format_review_field_value(opinion.ci_considered)})"
    elif ci_considered != "yes":
        reason = "CI was not considered"
    elif merge_error := _review_enum_contract_error(
        "merge recommendation",
        opinion.merge_recommendation,
        _REVIEW_VALID_YES_NO,
    ):
        reason = (
            f"{merge_error} "
            f"(got {_format_review_field_value(opinion.merge_recommendation)})"
        )
    elif merge_recommendation != "yes":
        reason = "merge recommendation is no"
    elif automerge_error := _review_enum_contract_error(
        "automerge eligible",
        opinion.automerge_eligible,
        _REVIEW_VALID_YES_NO,
    ):
        reason = (
            f"{automerge_error} "
            f"(got {_format_review_field_value(opinion.automerge_eligible)})"
        )
    else:
        gate_pass = True
        if risk == "high":
            reason = (
                "reviewer approved with high confidence, high risk explicitly allowed, "
                "green CI, and matching scope"
            )
        elif risk == "medium":
            reason = (
                "reviewer approved with high confidence, medium risk explicitly allowed, "
                "green CI, and matching scope"
            )
        else:
            reason = (
                "reviewer approved with high confidence, low risk, green CI, "
                "and matching scope"
            )

    automerge_ok = gate_pass and automerge_eligible == "yes"

    status = "pass" if gate_pass else f"blocked — {reason}"

    return ReviewGateResult(
        pr_number=pr_number,
        status=status,
        reason=reason,
        opinion=opinion,
        gate_pass=gate_pass,
        merge_eligible=gate_pass,  # conservative: gate pass == merge eligible for now
        automerge_eligible=automerge_ok,
        summary_path=resolved_summary_path,
        notes=notes,
        )


def _review_artifact_gate_reason(error: str) -> str:
    summary_prefix = "Artifact contains unsafe execution marker: "
    raw_prefix = "Raw artifact contains unsafe execution marker: "
    if error.startswith(summary_prefix):
        return (
            "reviewer artifact contains stale/failover signal: "
            f"{error.removeprefix(summary_prefix)}"
        )
    if error.startswith(raw_prefix):
        return (
            "reviewer raw artifact contains stale/failover signal: "
            f"{error.removeprefix(raw_prefix)}"
        )
    return f"reviewer artifact preflight: {error}"


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
    return ensure_github_comment_body(body.strip())


def plan_review_submit(
    repo: str,
    pr_number: int,
    *,
    allow_medium_risk: bool = False,
    allow_high_risk: bool = False,
) -> ReviewSubmitPlan:
    """Produce a dry-run plan for submitting a GitHub PR review.

    HARDENING-018A: Includes GitHub identity checks and self-review guard.
    """
    gate = evaluate_review_gate(
        repo,
        pr_number,
        allow_medium_risk=allow_medium_risk,
        allow_high_risk=allow_high_risk,
    )

    notes = [
        "No GitHub review was submitted.",
        "No merge was performed.",
        "No issue was closed.",
    ]

    if allow_high_risk:
        notes.append("High-risk review submission override explicitly allowed by operator.")

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
    allow_high_risk: bool = False,
) -> dict:
    """Execute (or dry-run) the GitHub PR review submission.

    HARDENING-018A: Respects self-review identity guard.
    Only performs the gh mutation when apply=True and the plan is ready for approval.
    """
    plan = plan_review_submit(
        repo,
        pr_number,
        allow_medium_risk=allow_medium_risk,
        allow_high_risk=allow_high_risk,
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
    try:
        review_body = ensure_github_comment_body(plan.body)
    except ValueError as e:
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": str(e),
        }

    reviewer_token = _get_reviewer_token()

    # Write a temporary body file for safe quoting
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
        tf.write(review_body)
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
