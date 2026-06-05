"""PR planning for isolated worker branches (planning / dry-run only).

HARDENING-013: provide a safe planning surface for creating pull requests
from Signposter-managed worker branches.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from signposter.comments import contains_auto_close_keyword, redact_github_comment_body
from signposter.git_utils import BranchSyncStatus, get_branch_sync_status
from signposter.handoff import HandoffPlan, plan_handoff_for_issue

DEFAULT_GITHUB_COMMAND_TIMEOUT_SECONDS = 30
DEFAULT_GITHUB_ISSUE_READ_FIELDS = ("number", "title", "state", "labels")
DEFAULT_GITHUB_COMMAND_EXCERPT_MAX_CHARS = 300
DEFAULT_GITHUB_COMMAND_EXCERPT_MAX_LINES = 6


@dataclass(frozen=True)
class GitHubCommandResult:
    """Bounded result for a single GitHub CLI command attempt."""

    command: tuple[str, ...]
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    timeout_seconds: int


@dataclass(frozen=True)
class PRPlan:
    issue_number: int
    title: str
    workflow_state: str | None
    github_issue_state: str | None

    base_branch: str
    source_branch: str
    worktree_path: str
    current_branch_in_worktree: str | None

    base_sync_status: BranchSyncStatus
    changed_files: list[str]
    has_uncommitted_changes: bool

    suggested_pr_title: str
    suggested_pr_body: str
    suggested_next_commands: list[str]

    status: str
    notes: list[str]


@dataclass(frozen=True)
class PRCIPendingTimeoutStatus:
    """Read-only operator status for PR checks that remain pending too long."""

    repo: str
    pr_number: int
    checks_status: str
    successful_checks: int
    failing_checks: int
    pending_checks: int
    elapsed_seconds: int
    timeout_seconds: int
    status: str
    reason: str
    inspect_command: str
    notes: list[str]


@dataclass(frozen=True)
class PRExactCommitCISelectionStatus:
    """Read-only status for selecting a remote CI run for an exact commit."""

    repo: str
    branch: str
    commit_sha: str
    elapsed_seconds: int
    timeout_seconds: int
    run_id: str | None
    status: str
    reason: str
    select_command: str
    notes: list[str]


@dataclass(frozen=True)
class PRFailedCIDiagnosisStatus:
    """Read-only bounded diagnosis surface for failing PR checks."""

    repo: str
    pr_number: int
    checks_status: str
    successful_checks: int
    failing_checks: int
    pending_checks: int
    failed_check_summaries: tuple[str, ...]
    omitted_failed_checks: int
    status: str
    reason: str
    inspect_command: str
    notes: list[str]


def run_github_command_with_timeout(
    command: list[str] | tuple[str, ...],
    *,
    timeout_seconds: int = DEFAULT_GITHUB_COMMAND_TIMEOUT_SECONDS,
    run_command=subprocess.run,
) -> GitHubCommandResult:
    """Run one gh command with a bounded timeout.

    The helper is intentionally narrow: callers must pass a concrete `gh`
    command, and timeout is reported as terminal evidence for that attempt.
    Later mutation/recovery decisions remain explicit caller responsibility.
    """
    normalized = tuple(command)
    if not normalized or normalized[0] != "gh":
        raise ValueError("GitHub command wrapper only accepts commands starting with 'gh'")
    bounded_timeout = max(1, int(timeout_seconds))
    try:
        result = run_command(
            list(normalized),
            capture_output=True,
            text=True,
            check=False,
            timeout=bounded_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return GitHubCommandResult(
            command=normalized,
            status="timeout",
            returncode=None,
            stdout=_timeout_output_text(exc.stdout),
            stderr=_timeout_output_text(exc.stderr),
            timeout_seconds=bounded_timeout,
        )

    return GitHubCommandResult(
        command=normalized,
        status="completed" if result.returncode == 0 else "failed",
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        timeout_seconds=bounded_timeout,
    )


def _timeout_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _bounded_github_command_excerpt(
    value: str,
    *,
    max_chars: int = DEFAULT_GITHUB_COMMAND_EXCERPT_MAX_CHARS,
    max_lines: int = DEFAULT_GITHUB_COMMAND_EXCERPT_MAX_LINES,
) -> str:
    """Return a redacted, bounded excerpt for operator-facing gh diagnostics."""
    text = redact_github_comment_body(value or "").strip()
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    selected = lines[: max(1, max_lines)]
    excerpt = "\n".join(selected).strip()
    truncated = len(lines) > len(selected) or len(excerpt) > max_chars

    if not truncated and len(excerpt) <= max_chars:
        return excerpt

    marker = "\n... (truncated)"
    budget = max(1, max_chars - len(marker))
    excerpt = excerpt[:budget].rstrip()
    return f"{excerpt}{marker}"


def _append_bounded_stderr_excerpt(
    lines: list[str],
    result: GitHubCommandResult,
) -> None:
    excerpt = _bounded_github_command_excerpt(result.stderr)
    if not excerpt:
        return

    lines.append("  stderr excerpt (bounded):")
    for line in excerpt.splitlines():
        lines.append(f"    {line}")


def format_github_command_result(result: GitHubCommandResult) -> str:
    """Render a compact GitHub command attempt result."""
    lines = [
        "Signposter GitHub Command Result",
        "",
        "Command:",
        "  " + shlex.join(result.command),
        "",
        "Status:",
        f"  {result.status}",
        f"  returncode: {result.returncode if result.returncode is not None else 'none'}",
        f"  timeout_seconds: {result.timeout_seconds}",
        "",
        "Output:",
        f"  stdout: {'present' if result.stdout else 'empty'}",
        f"  stderr: {'present' if result.stderr else 'empty'}",
    ]
    _append_bounded_stderr_excerpt(lines, result)
    lines.extend(
        [
            "",
            "Notes:",
            "  No follow-up GitHub mutation was performed by this wrapper.",
            "  Callers must stop after timeout unless an explicit recovery path is planned.",
        ]
    )
    return "\n".join(lines)


def read_github_issue_with_timeout(
    repo: str,
    issue_number: int,
    *,
    fields: tuple[str, ...] = DEFAULT_GITHUB_ISSUE_READ_FIELDS,
    timeout_seconds: int = DEFAULT_GITHUB_COMMAND_TIMEOUT_SECONDS,
    run_command=subprocess.run,
) -> GitHubCommandResult:
    """Read one GitHub issue through gh with bounded timeout evidence."""
    json_fields = ",".join(fields)
    return run_github_command_with_timeout(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "-R",
            repo,
            "--json",
            json_fields,
        ],
        timeout_seconds=timeout_seconds,
        run_command=run_command,
    )


def format_github_issue_read_result(
    repo: str,
    issue_number: int,
    result: GitHubCommandResult,
) -> str:
    """Render a compact GitHub issue read attempt result."""
    lines = [
        "Signposter GitHub Issue Read Result",
        "",
        "Issue:",
        f"  repo: {repo}",
        f"  issue: #{issue_number}",
        "",
        "Command:",
        "  " + shlex.join(result.command),
        "",
        "Status:",
        f"  {result.status}",
        f"  returncode: {result.returncode if result.returncode is not None else 'none'}",
        f"  timeout_seconds: {result.timeout_seconds}",
        "",
        "Output:",
        f"  stdout: {'present' if result.stdout else 'empty'}",
        f"  stderr: {'present' if result.stderr else 'empty'}",
    ]
    _append_bounded_stderr_excerpt(lines, result)
    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            (
                "  Issue reads are read-only; callers must stop after timeout "
                "before later mutations."
            ),
        ]
    )
    return "\n".join(lines)


def edit_github_issue_with_timeout(
    repo: str,
    issue_number: int,
    *,
    title: str | None = None,
    body: str | None = None,
    add_labels: tuple[str, ...] = (),
    remove_labels: tuple[str, ...] = (),
    state: str | None = None,
    timeout_seconds: int = DEFAULT_GITHUB_COMMAND_TIMEOUT_SECONDS,
    run_command=subprocess.run,
) -> GitHubCommandResult:
    """Run one guarded gh issue edit command with bounded timeout evidence."""
    command = ["gh", "issue", "edit", str(issue_number), "-R", repo]
    has_edit = False

    if title:
        command.extend(["--title", title])
        has_edit = True
    if body:
        command.extend(["--body", body])
        has_edit = True
    if add_labels:
        command.extend(["--add-label", ",".join(add_labels)])
        has_edit = True
    if remove_labels:
        command.extend(["--remove-label", ",".join(remove_labels)])
        has_edit = True
    if state:
        command.extend(["--state", state])
        has_edit = True

    if not has_edit:
        raise ValueError("GitHub issue edit helper requires at least one explicit edit argument")

    return run_github_command_with_timeout(
        command,
        timeout_seconds=timeout_seconds,
        run_command=run_command,
    )


def format_github_issue_edit_result(
    repo: str,
    issue_number: int,
    result: GitHubCommandResult,
) -> str:
    """Render a compact GitHub issue edit attempt result."""
    lines = [
        "Signposter GitHub Issue Edit Result",
        "",
        "Issue:",
        f"  repo: {repo}",
        f"  issue: #{issue_number}",
        "",
        "Command:",
        "  " + shlex.join(result.command),
        "",
        "Status:",
        f"  {result.status}",
        f"  returncode: {result.returncode if result.returncode is not None else 'none'}",
        f"  timeout_seconds: {result.timeout_seconds}",
        "",
        "Output:",
        f"  stdout: {'present' if result.stdout else 'empty'}",
        f"  stderr: {'present' if result.stderr else 'empty'}",
    ]
    _append_bounded_stderr_excerpt(lines, result)
    lines.extend(
        [
            "",
            "Notes:",
            "  This helper is for guarded apply paths only.",
            "  No follow-up GitHub mutation was performed after this command attempt.",
            "  Callers must stop after timeout before any later mutation.",
        ]
    )
    return "\n".join(lines)


def plan_pr_ci_pending_timeout_status(
    repo: str,
    pr_number: int,
    *,
    checks_status: str,
    pending_checks: int,
    successful_checks: int = 0,
    failing_checks: int = 0,
    elapsed_seconds: int,
    timeout_seconds: int,
) -> PRCIPendingTimeoutStatus:
    """Return a compact read-only status for a pending PR CI wait.

    This helper does not poll GitHub itself. Callers provide the current check
    state and elapsed wait time so workflow loops can report timeout evidence
    without performing any hidden mutation or retry.
    """
    bounded_elapsed = max(0, int(elapsed_seconds))
    bounded_timeout = max(1, int(timeout_seconds))
    normalized_status = (checks_status or "unknown").strip().lower()
    pending = max(0, int(pending_checks))
    successful = max(0, int(successful_checks))
    failing = max(0, int(failing_checks))

    if normalized_status == "pending" and pending > 0:
        if bounded_elapsed >= bounded_timeout:
            status = "blocked — CI pending timeout"
            reason = (
                f"{pending} pending check(s) exceeded "
                f"{bounded_timeout}s wait budget"
            )
        else:
            status = "pending — CI checks still running"
            reason = (
                f"{pending} pending check(s), "
                f"{bounded_timeout - bounded_elapsed}s wait budget remaining"
            )
    elif normalized_status == "pass":
        status = "ready"
        reason = "PR checks passed"
    elif normalized_status == "failing" or failing > 0:
        status = "blocked — checks are failing"
        reason = f"{failing} failing check(s)"
    else:
        status = "blocked — checks status is unknown"
        reason = "GitHub check state is unavailable or ambiguous"

    notes = [
        "Read-only PR CI wait status.",
        "No GitHub mutation was performed.",
        "No merge was performed.",
        "No issue was closed.",
        "Callers must stop after pending timeout before merge or integration.",
    ]
    return PRCIPendingTimeoutStatus(
        repo=repo,
        pr_number=pr_number,
        checks_status=normalized_status,
        successful_checks=successful,
        failing_checks=failing,
        pending_checks=pending,
        elapsed_seconds=bounded_elapsed,
        timeout_seconds=bounded_timeout,
        status=status,
        reason=reason,
        inspect_command=f"gh pr checks {pr_number} --repo {repo}",
        notes=notes,
    )


def format_pr_ci_pending_timeout_status(result: PRCIPendingTimeoutStatus) -> str:
    """Render a compact read-only PR CI pending timeout status."""
    lines = [
        f"Signposter PR CI Status — PR #{result.pr_number}",
        "",
        "Checks:",
        f"  status: {result.checks_status}",
        f"  successful: {result.successful_checks}",
        f"  failing: {result.failing_checks}",
        f"  pending: {result.pending_checks}",
        "",
        "Wait budget:",
        f"  elapsed_seconds: {result.elapsed_seconds}",
        f"  timeout_seconds: {result.timeout_seconds}",
        "",
        "Status:",
        f"  {result.status}",
        "",
        "Reason:",
        f"  {result.reason}",
        "",
        "Recovery:",
        f"  inspect command: {result.inspect_command}",
        "  next: inspect checks, wait explicitly if appropriate, then rerun plan",
        "",
        "Notes:",
    ]
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def plan_pr_exact_commit_ci_selection_timeout_status(
    repo: str,
    *,
    branch: str,
    commit_sha: str,
    run_id: str | None,
    elapsed_seconds: int,
    timeout_seconds: int,
) -> PRExactCommitCISelectionStatus:
    """Return read-only status for exact-commit CI run selection.

    This helper does not poll GitHub itself. Callers provide the observed run ID
    and wait budget so automation can stop safely when CI for the exact commit
    cannot be selected.
    """
    bounded_elapsed = max(0, int(elapsed_seconds))
    bounded_timeout = max(1, int(timeout_seconds))
    normalized_run_id = (run_id or "").strip() or None
    normalized_sha = (commit_sha or "").strip()
    normalized_branch = (branch or "").strip()

    if normalized_run_id:
        status = "ready"
        reason = f"selected CI run {normalized_run_id} for exact commit {normalized_sha}"
    elif bounded_elapsed >= bounded_timeout:
        status = "blocked — exact commit CI run selection timeout"
        reason = (
            f"no CI run found for exact commit {normalized_sha} on "
            f"{normalized_branch} after {bounded_timeout}s"
        )
    else:
        status = "pending — exact commit CI run not registered yet"
        reason = (
            f"no CI run found for exact commit {normalized_sha}; "
            f"{bounded_timeout - bounded_elapsed}s wait budget remaining"
        )

    select_command = (
        "gh run list "
        f"-R {repo} --branch {normalized_branch} --commit {normalized_sha} "
        "--limit 1 --json databaseId --jq '.[0].databaseId'"
    )
    notes = [
        "Read-only exact-commit CI selection status.",
        "No GitHub mutation was performed.",
        "No merge was performed.",
        "No issue was closed.",
        "Callers must stop after selection timeout before merge or integration.",
    ]

    return PRExactCommitCISelectionStatus(
        repo=repo,
        branch=normalized_branch,
        commit_sha=normalized_sha,
        elapsed_seconds=bounded_elapsed,
        timeout_seconds=bounded_timeout,
        run_id=normalized_run_id,
        status=status,
        reason=reason,
        select_command=select_command,
        notes=notes,
    )


def format_pr_exact_commit_ci_selection_timeout_status(
    result: PRExactCommitCISelectionStatus,
) -> str:
    """Render compact exact-commit CI selection status."""
    lines = [
        "Signposter PR Exact-Commit CI Selection",
        "",
        "Target:",
        f"  repo: {result.repo}",
        f"  branch: {result.branch}",
        f"  commit: {result.commit_sha}",
        f"  run id: {result.run_id or 'none'}",
        "",
        "Wait budget:",
        f"  elapsed_seconds: {result.elapsed_seconds}",
        f"  timeout_seconds: {result.timeout_seconds}",
        "",
        "Status:",
        f"  {result.status}",
        "",
        "Reason:",
        f"  {result.reason}",
        "",
        "Selection:",
        f"  command: {result.select_command}",
        "",
        "Notes:",
    ]
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def _bounded_failed_check_summaries(
    failed_checks: list[str] | tuple[str, ...],
    *,
    max_checks: int,
    max_chars: int,
) -> tuple[tuple[str, ...], int]:
    bounded_count = max(1, int(max_checks))
    bounded_chars = max(20, int(max_chars))
    summaries: list[str] = []

    for check in failed_checks:
        summary = _bounded_github_command_excerpt(
            str(check),
            max_chars=bounded_chars,
            max_lines=2,
        )
        if summary:
            summaries.append(summary)

    selected = tuple(summaries[:bounded_count])
    omitted = max(0, len(summaries) - len(selected))
    return selected, omitted


def plan_pr_failed_ci_diagnosis_status(
    repo: str,
    pr_number: int,
    *,
    checks_status: str,
    failed_checks: list[str] | tuple[str, ...],
    successful_checks: int = 0,
    pending_checks: int = 0,
    max_failed_checks: int = 5,
    max_check_chars: int = 160,
) -> PRFailedCIDiagnosisStatus:
    """Return a compact read-only diagnosis for failing PR CI checks.

    This helper does not poll GitHub. Callers provide already-observed check
    summaries so status surfaces can show bounded failure evidence without
    posting raw logs or continuing toward merge/integration.
    """
    normalized_status = (checks_status or "unknown").strip().lower()
    successful = max(0, int(successful_checks))
    pending = max(0, int(pending_checks))
    failed_summaries, omitted = _bounded_failed_check_summaries(
        failed_checks,
        max_checks=max_failed_checks,
        max_chars=max_check_chars,
    )
    failing = len(failed_summaries) + omitted

    if normalized_status == "pass" and failing == 0:
        status = "ready"
        reason = "PR checks passed"
    elif failing > 0:
        status = "blocked — CI checks failing"
        reason = f"{failing} failing check(s)"
    else:
        status = "blocked — failing CI evidence missing"
        reason = "CI is not passing, but no failing check summary was provided"

    notes = [
        "Read-only PR failed-CI diagnosis.",
        "Failed check summaries are bounded and redacted.",
        "No GitHub mutation was performed.",
        "No merge was performed.",
        "No issue was closed.",
        "Callers must stop before merge or integration while CI is failing.",
    ]
    return PRFailedCIDiagnosisStatus(
        repo=repo,
        pr_number=pr_number,
        checks_status=normalized_status,
        successful_checks=successful,
        failing_checks=failing,
        pending_checks=pending,
        failed_check_summaries=failed_summaries,
        omitted_failed_checks=omitted,
        status=status,
        reason=reason,
        inspect_command=f"gh pr checks {pr_number} --repo {repo}",
        notes=notes,
    )


def format_pr_failed_ci_diagnosis_status(
    result: PRFailedCIDiagnosisStatus,
) -> str:
    """Render compact failed-CI diagnosis without raw logs."""
    lines = [
        f"Signposter PR Failed CI Diagnosis — PR #{result.pr_number}",
        "",
        "Checks:",
        f"  status: {result.checks_status}",
        f"  successful: {result.successful_checks}",
        f"  failing: {result.failing_checks}",
        f"  pending: {result.pending_checks}",
        "",
        "Status:",
        f"  {result.status}",
        "",
        "Reason:",
        f"  {result.reason}",
        "",
        "Failed checks:",
    ]
    if result.failed_check_summaries:
        lines.extend(f"  - {summary}" for summary in result.failed_check_summaries)
    else:
        lines.append("  none provided")
    if result.omitted_failed_checks:
        lines.append(f"  ... {result.omitted_failed_checks} additional failing check(s) omitted")
    lines.extend(
        [
            "",
            "Recovery:",
            f"  inspect command: {result.inspect_command}",
            "  next: inspect failing checks, fix the scoped issue, rerun CI",
            "",
            "Notes:",
        ]
    )
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def _get_branch_changed_files(
    worktree_path: str,
    base_branch: str,
    source_branch: str,
) -> list[str]:
    """Return committed file changes between base and source branch.

    This is read-only. If git cannot compute the diff, return an empty list and
    let the plan stay conservative.
    """
    result = subprocess.run(
        [
            "git",
            "-C",
            worktree_path,
            "diff",
            "--name-only",
            f"{base_branch}...{source_branch}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _make_pr_title(handoff: HandoffPlan) -> str:
    return handoff.suggested_commit_message


def _make_pr_body(plan: PRPlan) -> str:
    changed = "\n".join(f"- `{path}`" for path in plan.changed_files)
    if not changed:
        changed = "- No committed branch changes detected by planner."

    return "\n".join(
        [
            "## Summary",
            "",
            f"- Signposter handoff for issue #{plan.issue_number}.",
            f"- Source branch: `{plan.source_branch}`",
            f"- Base branch: `{plan.base_branch}`",
            "",
            "## Changed files",
            "",
            changed,
            "",
            "## Safety notes",
            "",
            "- Generated by Signposter PR planning surface.",
            "- No merge or issue close is implied by this PR.",
            "- Issue should remain open until explicit integration/close policy.",
            "",
            f"Related issue: #{plan.issue_number}",
        ]
    )


def _make_body_file_command(body_file: str, body: str) -> str:
    return "\n".join(
        [
            f"cat > {shlex.quote(body_file)} <<'EOF'",
            body,
            "EOF",
        ]
    )


def _make_pr_create_command(
    repo: str,
    base_branch: str,
    source_branch: str,
    title: str,
    body_file: str,
) -> str:
    return " ".join(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            shlex.quote(repo),
            "--base",
            shlex.quote(base_branch),
            "--head",
            shlex.quote(source_branch),
            "--title",
            shlex.quote(title),
            "--body-file",
            shlex.quote(body_file),
        ]
    )


def plan_pr_for_issue(
    repo: str,
    issue_number: int,
    *,
    base_branch: str = "main",
) -> PRPlan:
    """Produce a PRPlan (read-only, no mutations)."""
    handoff = plan_handoff_for_issue(repo, issue_number)
    base_sync = get_branch_sync_status(branch=base_branch)

    source_branch = handoff.branch
    has_uncommitted = handoff.has_changes

    if has_uncommitted:
        changed_files = handoff.changed_files
    elif handoff.worktree_exists:
        changed_files = _get_branch_changed_files(
            handoff.worktree_path,
            base_branch,
            source_branch,
        )
    else:
        changed_files = []

    pr_title = _make_pr_title(handoff)

    partial = PRPlan(
        issue_number=issue_number,
        title=handoff.title,
        workflow_state=handoff.workflow_state,
        github_issue_state=handoff.github_issue_state,
        base_branch=base_branch,
        source_branch=source_branch,
        worktree_path=handoff.worktree_path,
        current_branch_in_worktree=handoff.current_branch_in_worktree,
        base_sync_status=base_sync,
        changed_files=changed_files,
        has_uncommitted_changes=has_uncommitted,
        suggested_pr_title=pr_title,
        suggested_pr_body="",
        suggested_next_commands=[],
        status="planning",
        notes=[],
    )
    pr_body = _make_pr_body(partial)
    body_file = f"/tmp/signposter-pr-issue-{issue_number}.md"

    commands = [
        _make_body_file_command(body_file, pr_body),
        _make_pr_create_command(
            repo,
            base_branch,
            source_branch,
            pr_title,
            body_file,
        ),
    ]

    if not handoff.worktree_exists:
        status = "blocked — expected worktree is missing"
    elif handoff.workflow_state != "done":
        status = f"blocked — issue is not state:done (current: {handoff.workflow_state})"
    elif handoff.current_branch_in_worktree != source_branch:
        status = "blocked — worktree is not on expected source branch"
    elif has_uncommitted:
        status = "blocked — worktree has uncommitted changes; run handoff commit/push first"
    elif base_sync.status in {"ahead", "behind", "diverged"}:
        status = (
            f"blocked — base branch {base_branch} is not synchronized "
            f"with {base_sync.upstream} ({base_sync.status})"
        )
    elif not changed_files:
        status = f"blocked — no committed changes detected against {base_branch}"
    elif contains_auto_close_keyword(pr_title) or contains_auto_close_keyword(pr_body):
        status = "blocked — suggested PR metadata contains auto-close keyword"
    else:
        status = "ready"

    notes = [
        "No PR, merge, push, close, or GitHub mutation was performed.",
        "This command only plans PR metadata and suggested gh commands.",
        "Do not use auto-closing keywords until explicit close policy exists.",
        (
            "If this head branch or PR already exists, inspect and reuse it "
            "instead of creating duplicates."
        ),
    ]

    return PRPlan(
        issue_number=issue_number,
        title=handoff.title,
        workflow_state=handoff.workflow_state,
        github_issue_state=handoff.github_issue_state,
        base_branch=base_branch,
        source_branch=source_branch,
        worktree_path=handoff.worktree_path,
        current_branch_in_worktree=handoff.current_branch_in_worktree,
        base_sync_status=base_sync,
        changed_files=changed_files,
        has_uncommitted_changes=has_uncommitted,
        suggested_pr_title=pr_title,
        suggested_pr_body=pr_body,
        suggested_next_commands=commands,
        status=status,
        notes=notes,
    )


def format_pr_plan(plan: PRPlan) -> str:
    """Compact human-readable PR plan output."""
    lines = [f"Signposter PR Plan — Issue #{plan.issue_number}\n"]

    lines.append("Issue:")
    lines.append(f"  title: {plan.title}")
    lines.append(f"  workflow state: {plan.workflow_state or 'unknown'}")
    lines.append(f"  github issue: {plan.github_issue_state or 'open'}")

    lines.append("\nBranches:")
    lines.append(f"  base: {plan.base_branch}")
    lines.append(f"  head: {plan.source_branch}")

    lines.append("\nBase sync:")
    lines.append(f"  upstream: {plan.base_sync_status.upstream}")
    lines.append(f"  status: {plan.base_sync_status.status}")
    lines.append(f"  reason: {plan.base_sync_status.reason}")

    lines.append("\nWorktree:")
    lines.append(f"  path: {plan.worktree_path}")
    if plan.current_branch_in_worktree:
        lines.append(f"  current branch: {plan.current_branch_in_worktree}")
    lines.append(
        "  uncommitted changes: "
        f"{'yes' if plan.has_uncommitted_changes else 'no'}"
    )

    lines.append("\nChanged files:")
    if plan.changed_files:
        for path in plan.changed_files[:10]:
            lines.append(f"  {path}")
        if len(plan.changed_files) > 10:
            lines.append(f"  ... ({len(plan.changed_files)} total)")
        lines.append(f"  files changed: {len(plan.changed_files)}")
    else:
        lines.append("  (no changed files detected)")

    lines.append("\nSuggested PR:")
    lines.append(f"  title: {plan.suggested_pr_title}")
    lines.append("  body:")
    for body_line in plan.suggested_pr_body.splitlines():
        lines.append(f"    {body_line}")

    lines.append("\nSuggested next commands:")
    for command in plan.suggested_next_commands:
        for command_line in command.splitlines():
            lines.append(f"  {command_line}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    lines.append("\nNotes:")
    for note in plan.notes:
        lines.append(f"  {note}")

    return "\n".join(lines)
