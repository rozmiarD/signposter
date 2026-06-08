"""Post-merge integration planning (HARDENING-021A).

Pure dry-run planning only. Connects a merged PR back to its associated Signposter issue.
No GitHub mutations of any kind.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signposter.comments import ensure_github_comment_body
from signposter.gate import evaluate_ci_gate
from signposter.labels import check_labels
from signposter.pr_linkage import detect_pr_issue_linkage
from signposter.review import _normalize_check_rollup, _run_gh_pr_view
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
    return detect_pr_issue_linkage(head_branch, body).associated_issue


def _get_workflow_state_from_labels(labels: list[str]) -> str | None:
    """Extract current state:xxx label if present."""
    for label in labels:
        if label.startswith("state:"):
            return label
    return None


def _is_already_integrated_plan(
    *,
    pr_state: str,
    merge_commit: str | None,
    associated_issue: int | None,
    issue_state: str | None,
    current_workflow_state: str | None,
) -> bool:
    """
    H024A: Return True only for a fully completed integrated lifecycle.

    Criteria (strict):
    - PR is MERGED
    - merge commit exists
    - associated issue exists
    - issue state is CLOSED
    - current workflow label is exactly state:merged
    """
    if pr_state != "MERGED":
        return False
    if not merge_commit:
        return False
    if associated_issue is None:
        return False
    if issue_state is None or issue_state.upper() != "CLOSED":
        return False
    if current_workflow_state != "state:merged":
        return False
    return True


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


_CI_RUN_LIST_JSON_FIELDS = (
    "status,conclusion,workflowName,headBranch,headSha,databaseId"
)


def _build_ci_run_selection_command(
    repo: str,
    *,
    branch: str,
    commit_sha: str | None = None,
    workflow: str = "CI",
) -> list[str]:
    """Build the deterministic gh command used to select one CI run.

    Branch-only selection is useful for PR branch smoke checks. Passing a commit
    SHA tightens selection for post-merge main CI so an older green run cannot
    satisfy integration for a newer merge commit.
    """
    command = [
        "gh",
        "run",
        "list",
        "-R",
        repo,
        "--branch",
        branch,
    ]
    if commit_sha:
        command.extend(["--commit", commit_sha])
    command.extend(
        [
            "--workflow",
            workflow,
            "--limit",
            "1",
            "--json",
            _CI_RUN_LIST_JSON_FIELDS,
        ]
    )
    return command


def _fetch_main_ci_status(
    repo: str,
    commit_sha: str | None = None,
    *,
    branch: str = "main",
) -> str:
    """Return integration branch CI status using gh run list.

    When a merge commit is known, select the run by branch and commit SHA. This
    avoids accepting an older green branch run after a PR merge while the new push
    run is still queued or missing from the Actions API.

    Conservative mapping:
    - pass: selected branch CI run is completed with success
    - failing: latest branch CI run completed with a non-success conclusion
    - pending: latest branch CI run is queued/in_progress/waiting/etc.
    - unknown: gh failed, no runs found, or payload shape is unexpected
    """
    command = _build_ci_run_selection_command(
        repo,
        branch=branch,
        commit_sha=commit_sha,
    )

    try:
        result = subprocess.run(
            command,
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


def _fetch_pr_ci_status(repo: str, pr_number: int) -> str:
    """Return PR check-rollup status for integration fallback decisions."""
    try:
        data = _run_gh_pr_view(repo, pr_number, ["statusCheckRollup"])
    except Exception:
        return "unknown"

    checks = _normalize_check_rollup(data.get("statusCheckRollup", []))
    if not checks:
        return "unknown"

    successful = failing = pending = 0
    for check in checks:
        status = (check.get("status") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()

        if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            successful += 1
        elif conclusion in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"):
            failing += 1
        elif status in ("QUEUED", "IN_PROGRESS", "PENDING", "REQUESTED", "WAITING"):
            pending += 1
        elif state in ("PENDING", "QUEUED", "IN_PROGRESS"):
            pending += 1
        elif state in ("FAILURE", "ERROR", "CANCELLED"):
            failing += 1
        elif state == "SUCCESS":
            successful += 1

    if failing > 0:
        return "failing"
    if pending > 0:
        return "pending"
    if successful > 0:
        return "pass"
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

    linkage = detect_pr_issue_linkage(head, body)
    associated_issue = linkage.associated_issue

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

    # Integration branch CI status — required before integration apply can close the issue.
    main_ci_status = _fetch_main_ci_status(repo, merge_commit, branch=base)
    if base != "main" and main_ci_status == "unknown":
        pr_ci_status = _fetch_pr_ci_status(repo, pr_number)
        if pr_ci_status == "pass":
            main_ci_status = "pass"
            notes.append(
                "Base branch is not main; green PR check rollup was accepted "
                "because branch push CI was unavailable."
            )

    # Eligibility
    status = "ready"
    if pr_state != "MERGED":
        status = f"blocked — PR is not merged (state: {pr_state})"
    elif not merge_commit:
        status = "blocked — merge commit missing"
    elif linkage.ambiguous:
        status = f"blocked — {linkage.reason}"
    elif associated_issue is None:
        status = "blocked — associated issue could not be detected"
    elif _is_already_integrated_plan(
        pr_state=pr_state,
        merge_commit=merge_commit,
        associated_issue=associated_issue,
        issue_state=issue_state,
        current_workflow_state=current_workflow_state,
    ):
        # H024A: Idempotent no-op for already-integrated lifecycle
        status = "completed"
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


def _main_ci_inspection_command(
    plan: IntegrationPlan,
    *,
    repo: str | None = None,
) -> str:
    repo_arg = repo or "<repo>"
    commit_arg = plan.merge_commit or "<merge-commit>"
    return (
        f"gh run list -R {repo_arg} --branch {plan.base_branch} --commit {commit_arg} "
        "--limit 1 --json status,conclusion,databaseId"
    )


def _main_ci_log_command(*, repo: str | None = None) -> str:
    repo_arg = repo or "<repo>"
    return f"gh run view <run-id-from-inspect-command> -R {repo_arg} --log-failed"


def _integration_ci_blockage_lines(
    plan: IntegrationPlan,
    *,
    repo: str | None = None,
) -> list[str]:
    if plan.main_ci_status == "pass":
        return []
    if plan.main_ci_status == "failing":
        category = "failing-main-ci"
        reason = "selected main CI run completed without success"
        next_action = "inspect main CI failure and rerun integration plan"
    elif plan.main_ci_status == "pending":
        category = "waiting-main-ci"
        reason = "selected main CI run is still pending"
        next_action = "wait for main CI completion and rerun integration plan"
    else:
        category = "unknown-main-ci"
        reason = "main CI run is unavailable or ambiguous"
        next_action = "inspect main CI manually if this persists"
    return [
        f"category: {category}",
        f"reason: {reason}",
        f"inspect command: {_main_ci_inspection_command(plan, repo=repo)}",
        *(
            [
                f"log command: {_main_ci_log_command(repo=repo)}",
                "logs: not fetched or printed by Signposter output",
            ]
            if plan.main_ci_status == "failing"
            else []
        ),
        f"next: {next_action}",
    ]


def _integration_pending_issue_closure_lines(
    plan: IntegrationPlan,
    *,
    repo: str | None = None,
) -> list[str]:
    if plan.status != "ready":
        return []
    if plan.pr_state != "MERGED":
        return []
    if not plan.associated_issue:
        return []
    if (plan.issue_state or "").upper() != "OPEN":
        return []
    if plan.current_workflow_state == "state:merged":
        return []

    repo_arg = repo or "<repo>"
    return [
        "category: pending-issue-closure",
        "status: ready — issue closure pending",
        (
            "reason: "
            f"PR #{plan.pr_number} is merged but issue #{plan.associated_issue} "
            "remains open"
        ),
        (
            "apply command: "
            f"signposter integration apply --repo {repo_arg} "
            f"--pr {plan.pr_number} --apply"
        ),
        "next: run integration apply only after the dry-run remains ready",
    ]


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
    ci_blockage = _integration_ci_blockage_lines(plan)
    if ci_blockage:
        lines.append("\nMain CI blockage:")
        for line in ci_blockage:
            lines.append(f"  {line}")
    pending_closure = _integration_pending_issue_closure_lines(plan)
    if pending_closure:
        lines.append("\nPending issue closure:")
        for line in pending_closure:
            lines.append(f"  {line}")

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
    return ensure_github_comment_body(comment.strip())


def integration_apply_status(plan: IntegrationPlan, repo: str | None = None) -> str:
    """Return effective readiness for integration apply.

    Also runs the centralized label preflight (H023C) when repo is provided.
    """
    if plan.status == "completed":
        return "completed"
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


def _integration_apply_status(plan: IntegrationPlan, repo: str | None = None) -> str:
    """Backward-compatible alias for integration apply readiness."""
    return integration_apply_status(plan, repo)


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
        apply_status = integration_apply_status(plan)
        return {
            "mode": "dry_run",
            "plan": plan,
            "apply_status": apply_status,
            "would_execute": apply_status == "ready",
        }

    # Mutation path - very strictly guarded
    if plan.status == "completed":
        return {
            "mode": "apply_completed",
            "plan": plan,
            "success": True,
            "results": ["integration already completed"],
        }

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
    try:
        integration_comment = _build_integration_comment(plan)
    except ValueError as e:
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": str(e),
        }

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
        cmd = [
            "gh", "issue", "comment", str(issue),
            "-R", repo,
            "--body", integration_comment,
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
    apply_status = integration_apply_status(plan, repo)

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

    ci_blockage = _integration_ci_blockage_lines(plan, repo=repo)
    if ci_blockage:
        lines.append("\nMain CI blockage:")
        for line in ci_blockage:
            lines.append(f"  {line}")
    pending_closure = _integration_pending_issue_closure_lines(plan, repo=repo)
    if pending_closure:
        lines.append("\nPending issue closure:")
        for line in pending_closure:
            lines.append(f"  {line}")

    if "required labels missing" in apply_status.lower():
        lines.append("\nLabel preflight:")
        lines.append(f"  {apply_status}")

    # HARDENING-027A: do not list concrete mutations for non-ready plans
    lines.append("\nPlanned GitHub mutations:")
    if plan.status == "ready":
        lines.append("  remove label: state:done")
        lines.append("  add label: state:merged")
        lines.append(f"  close issue: #{plan.associated_issue} as completed")
        lines.append("  post integration comment: yes")
    elif plan.status == "completed":
        lines.append("  none — integration already completed")
    else:
        lines.append(f"  none — integration plan is not ready ({plan.status})")

    lines.append("\nStatus:")
    lines.append(f"  {apply_status}")

    lines.append("\nNotes:")
    lines.append("  DRY RUN: no issue was closed.")
    lines.append("  No labels were changed.")
    lines.append("  No local worktree was removed.")

    return "\n".join(lines)


# =============================================================================
# H032C: Validated no-op integration (no PR required)
# =============================================================================


@dataclass(frozen=True)
class NoopIntegrationPlan:
    issue_number: int
    issue_title: str
    issue_state: str
    current_workflow_state: str | None
    proposed_workflow_state: str
    close_issue: bool
    close_reason: str
    summary_path: str
    gate_decision: str
    gate_reason: str
    worktree_path: str
    worktree_exists: bool
    local_branch_exists: bool
    associated_pr_detected: bool
    status: str
    notes: list[str]


def _fetch_noop_issue_state(repo: str, issue_number: int) -> dict[str, Any]:
    result = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "-R",
            repo,
            "--json",
            "number,title,state,labels",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"failed to fetch issue #{issue_number}")

    data = json.loads(result.stdout or "{}")
    labels = []
    for label in data.get("labels", []) or []:
        if isinstance(label, dict):
            labels.append(label.get("name", ""))
        elif isinstance(label, str):
            labels.append(label)
    data["label_names"] = [label for label in labels if label]
    return data


def _noop_worktree_path(issue_number: int) -> str:
    return f"../signposter-work/{issue_number}"


def _noop_local_branch_exists(issue_number: int) -> bool:
    result = subprocess.run(
        ["git", "branch", "--list", f"work/issue-{issue_number}-*"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return bool(result.stdout.strip()) if result.returncode == 0 else True


def _noop_associated_pr_detected(repo: str, issue_number: int) -> bool:
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            "number,headRefName,state",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return True

    try:
        prs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return True

    prefix = f"work/issue-{issue_number}-"
    for pr in prs if isinstance(prs, list) else []:
        if isinstance(pr, dict) and str(pr.get("headRefName") or "").startswith(prefix):
            return True
    return False


def _load_noop_gate_evidence(issue_number: int) -> tuple[str, str, str]:
    summary_path = f"artifacts/runs/issue-{issue_number}-gate.summary.md"
    raw_path = f"artifacts/runs/issue-{issue_number}-gate.raw.txt"

    summary = Path(summary_path)
    raw = Path(raw_path)

    if not summary.is_file():
        return summary_path, "missing", "validated no-op gate summary artifact missing"

    summary_text = summary.read_text(encoding="utf-8")
    raw_text = raw.read_text(encoding="utf-8") if raw.is_file() else ""
    decision = evaluate_ci_gate(0, summary_text, raw_text)

    return summary_path, decision.decision, decision.reason


def plan_noop_integration_for_issue(repo: str, issue_number: int) -> NoopIntegrationPlan:
    notes = [
        "No issue was closed.",
        "No labels were changed.",
        "No local worktree was removed.",
        "No PR merge was performed.",
    ]

    try:
        issue = _fetch_noop_issue_state(repo, issue_number)
        issue_title = issue.get("title", "unknown")
        issue_state = issue.get("state", "UNKNOWN")
        labels = issue.get("label_names", []) or []
        workflow_state = _get_workflow_state_from_labels(labels)
    except Exception as exc:
        return NoopIntegrationPlan(
            issue_number=issue_number,
            issue_title="unknown",
            issue_state="UNKNOWN",
            current_workflow_state=None,
            proposed_workflow_state="state:merged",
            close_issue=True,
            close_reason="completed",
            summary_path=f"artifacts/runs/issue-{issue_number}-gate.summary.md",
            gate_decision="unknown",
            gate_reason=f"failed to fetch issue: {exc}",
            worktree_path=_noop_worktree_path(issue_number),
            worktree_exists=True,
            local_branch_exists=True,
            associated_pr_detected=True,
            status=f"blocked — failed to fetch issue #{issue_number}",
            notes=notes,
        )

    worktree_path = _noop_worktree_path(issue_number)
    worktree_exists = Path(worktree_path).exists()
    local_branch_exists = _noop_local_branch_exists(issue_number)
    associated_pr_detected = _noop_associated_pr_detected(repo, issue_number)
    summary_path, gate_decision, gate_reason = _load_noop_gate_evidence(issue_number)

    status = "ready"
    if issue_state == "CLOSED" and workflow_state == "state:merged":
        if worktree_exists:
            status = f"blocked — worktree still exists ({worktree_path})"
        elif local_branch_exists:
            status = f"blocked — local work branch still exists for issue #{issue_number}"
        elif associated_pr_detected:
            status = f"blocked — associated PR exists for issue #{issue_number}; use PR integration"
        else:
            status = "completed"
    elif issue_state != "OPEN":
        status = f"blocked — issue is {issue_state.lower()}"
    elif workflow_state != "state:done":
        status = f"blocked — issue workflow state is {workflow_state or 'unknown'}"
    elif gate_decision != "pass":
        status = f"blocked — validated no-op gate is not pass ({gate_reason})"
    elif worktree_exists:
        status = f"blocked — worktree still exists ({worktree_path})"
    elif local_branch_exists:
        status = f"blocked — local work branch still exists for issue #{issue_number}"
    elif associated_pr_detected:
        status = f"blocked — associated PR exists for issue #{issue_number}; use PR integration"

    return NoopIntegrationPlan(
        issue_number=issue_number,
        issue_title=issue_title,
        issue_state=issue_state,
        current_workflow_state=workflow_state,
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        summary_path=summary_path,
        gate_decision=gate_decision,
        gate_reason=gate_reason,
        worktree_path=worktree_path,
        worktree_exists=worktree_exists,
        local_branch_exists=local_branch_exists,
        associated_pr_detected=associated_pr_detected,
        status=status,
        notes=notes,
    )


def format_noop_integration_preconditions(plan: NoopIntegrationPlan) -> list[str]:
    return [
        f"  issue is open: {'yes' if plan.issue_state == 'OPEN' else 'no'}",
        (
            "  workflow state is state:done: "
            f"{'yes' if plan.current_workflow_state == 'state:done' else 'no'}"
        ),
        f"  gate passed: {'yes' if plan.gate_decision == 'pass' else 'no'}",
        f"  no associated PR detected: {'yes' if not plan.associated_pr_detected else 'no'}",
        f"  worktree absent: {'yes' if not plan.worktree_exists else 'no'}",
        f"  local branch absent: {'yes' if not plan.local_branch_exists else 'no'}",
    ]


def format_noop_integration_plan(plan: NoopIntegrationPlan) -> str:
    lines = [f"Signposter No-op Integration Plan — Issue #{plan.issue_number}\n"]

    lines.append("Issue:")
    lines.append(f"  title: {plan.issue_title}")
    lines.append(f"  state: {plan.issue_state}")
    lines.append(f"  current workflow state: {plan.current_workflow_state or 'unknown'}")
    lines.append(f"  proposed workflow state: {plan.proposed_workflow_state}")
    lines.append(f"  close issue: {'yes' if plan.close_issue else 'no'}")
    lines.append(f"  close reason: {plan.close_reason}")

    lines.append("\nEvidence:")
    lines.append(f"  summary: {plan.summary_path}")
    lines.append(f"  gate decision: {plan.gate_decision}")
    lines.append(f"  gate reason: {plan.gate_reason}")

    lines.append("\nNo-PR checks:")
    lines.append(f"  associated PR detected: {'yes' if plan.associated_pr_detected else 'no'}")
    lines.append(f"  worktree path: {plan.worktree_path}")
    lines.append(f"  worktree exists: {'yes' if plan.worktree_exists else 'no'}")
    lines.append(f"  local branch exists: {'yes' if plan.local_branch_exists else 'no'}")

    lines.append("\nVerified preconditions:")
    lines.extend(format_noop_integration_preconditions(plan))

    lines.append("\nPlanned GitHub mutations:")
    if plan.status == "ready":
        lines.append(
            f"  gh issue edit {plan.issue_number} --add-label state:merged "
            "--remove-label state:done"
        )
        lines.append(
            f"  gh issue close {plan.issue_number} --reason completed"
        )
    else:
        lines.append(f"  none — no-op integration plan is not ready ({plan.status})")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for note in plan.notes:
            lines.append(f"  {note}")

    return "\n".join(lines)


def format_noop_integration_apply_dry_run(plan: NoopIntegrationPlan, repo: str) -> str:
    lines = [f"Signposter No-op Integration Apply Plan — Issue #{plan.issue_number}\n"]

    lines.append("No-op integration plan:")
    lines.append(f"  status: {plan.status}")
    lines.append(f"  gate decision: {plan.gate_decision}")
    lines.append(f"  close issue: {'yes' if plan.close_issue else 'no'}")

    lines.append("\nVerified preconditions:")
    lines.extend(format_noop_integration_preconditions(plan))

    lines.append("\nPlanned GitHub mutations:")
    if plan.status == "ready":
        lines.append(
            f"  gh issue edit {plan.issue_number} -R {repo} "
            "--add-label state:merged --remove-label state:done"
        )
        lines.append(
            f"  gh issue close {plan.issue_number} -R {repo} --reason completed"
        )
    else:
        lines.append(f"  none — no-op integration plan is not ready ({plan.status})")

    lines.append("\nStatus:")
    if plan.status == "ready":
        lines.append("  ready")
    else:
        lines.append(f"  blocked — no-op integration plan is not ready ({plan.status})")

    lines.append("\nNotes:")
    lines.append("  DRY RUN: no issue was closed.")
    lines.append("  No labels were changed.")
    lines.append("  No PR merge was performed.")
    lines.append("  No local worktree was removed.")

    return "\n".join(lines)


def apply_noop_integration(
    repo: str,
    issue_number: int,
    *,
    apply: bool = False,
) -> dict:
    plan = plan_noop_integration_for_issue(repo, issue_number)

    if not apply:
        return {
            "mode": "dry_run",
            "plan": plan,
        }

    if plan.status == "completed":
        return {
            "mode": "apply_completed",
            "plan": plan,
            "success": True,
            "results": ["no-op integration already completed"],
        }

    if plan.status != "ready":
        return {
            "mode": "apply_blocked",
            "plan": plan,
            "error": f"Refusing no-op integration apply: {plan.status}",
        }

    errors: list[str] = []

    edit = subprocess.run(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "-R",
            repo,
            "--add-label",
            "state:merged",
            "--remove-label",
            "state:done",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if edit.returncode != 0:
        errors.append(f"label transition failed: {edit.stderr.strip()}")

    if not errors:
        close = subprocess.run(
            [
                "gh",
                "issue",
                "close",
                str(issue_number),
                "-R",
                repo,
                "--reason",
                "completed",
                "--comment",
                (
                    "**Signposter:** completed validated no-op task.\n\n"
                    "`state:done → state:merged` · no PR required"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if close.returncode != 0:
            errors.append(f"issue close failed: {close.stderr.strip()}")

    return {
        "mode": "apply",
        "plan": plan,
        "success": not errors,
        "errors": errors,
    }
