"""PR planning for isolated worker branches (planning / dry-run only).

HARDENING-013: provide a safe planning surface for creating pull requests
from Signposter-managed worker branches.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from signposter.comments import contains_auto_close_keyword
from signposter.handoff import HandoffPlan, plan_handoff_for_issue

DEFAULT_GITHUB_COMMAND_TIMEOUT_SECONDS = 30
DEFAULT_GITHUB_ISSUE_READ_FIELDS = ("number", "title", "state", "labels")


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

    changed_files: list[str]
    has_uncommitted_changes: bool

    suggested_pr_title: str
    suggested_pr_body: str
    suggested_next_commands: list[str]

    status: str
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
        "",
        "Notes:",
        "  No follow-up GitHub mutation was performed by this wrapper.",
        "  Callers must stop after timeout unless an explicit recovery path is planned.",
    ]
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
        "",
        "Notes:",
        "  No GitHub mutation was performed.",
        "  Issue reads are read-only; callers must stop after timeout before later mutations.",
    ]
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
        "",
        "Notes:",
        "  This helper is for guarded apply paths only.",
        "  No follow-up GitHub mutation was performed after this command attempt.",
        "  Callers must stop after timeout before any later mutation.",
    ]
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
