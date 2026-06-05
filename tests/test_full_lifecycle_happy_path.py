from unittest.mock import patch

from signposter.cleanup import plan_cleanup_for_pr
from signposter.comments import contains_auto_close_keyword
from signposter.handoff import HandoffPlan
from signposter.integration import plan_integration_for_pr
from signposter.lifecycle import plan_lifecycle_status
from signposter.merge import plan_merge_for_pr
from signposter.pr import plan_pr_for_issue


def _review_gate_result(*, gate_pass: bool = True, risk: str = "low"):
    return type(
        "Gate",
        (),
        {
            "gate_pass": gate_pass,
            "opinion": type(
                "Opinion",
                (),
                {"verdict": "APPROVE", "confidence": 0.95, "risk": risk},
            )(),
        },
    )()


def _plan_merge_with(
    *,
    review_decision: str | None = "APPROVED",
    approving_reviewers: list[str] | None = None,
    checks_status: str = "pass",
    mergeable: str = "MERGEABLE",
    gate_pass: bool = True,
):
    repo = "test/repo"
    pr = 43
    branch = "work/issue-42-token-budget-report"
    body = "Related issue: #42"
    approving_reviewers = (
        ["AlphaExatron"] if approving_reviewers is None else approving_reviewers
    )

    with (
        patch("signposter.merge._run_gh_pr_view") as mock_view,
        patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews,
        patch("signposter.merge.evaluate_review_gate") as mock_gate,
        patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks,
    ):
        mock_view.return_value = {
            "title": "test: lifecycle blocked path",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeable": mergeable,
            "reviewDecision": review_decision,
            "body": body,
            "files": [{"path": "tests/test_full_lifecycle_happy_path.py"}],
            "additions": 20,
            "deletions": 2,
        }
        mock_reviews.return_value = {
            "pr_author": "ExatronOmega",
            "review_decision": review_decision,
            "approving_reviewers": approving_reviewers,
        }
        mock_gate.return_value = _review_gate_result(
            gate_pass=gate_pass,
            risk="medium",
        )
        mock_checks.return_value = {
            "status": checks_status,
            "successful": 1 if checks_status == "pass" else 0,
            "failing": 1 if checks_status == "failing" else 0,
            "pending": 1 if checks_status == "pending" else 0,
        }

        return plan_merge_for_pr(
            repo,
            pr,
            allow_medium_risk=True,
            allow_medium_scope=True,
        )


def test_full_lifecycle_happy_path_plans_pr_merge_integration_cleanup() -> None:
    """Mock the normal issue-to-PR-to-cleanup path without mutating GitHub or git."""
    repo = "test/repo"
    issue = 42
    pr = 43
    branch = "work/issue-42-token-budget-report"
    pr_body = ""

    handoff = HandoffPlan(
        issue_number=issue,
        title="H049 happy path",
        workflow_state="done",
        github_issue_state="OPEN",
        worktree_path="../signposter-work/42",
        branch=branch,
        worktree_exists=True,
        current_branch_in_worktree=branch,
        status_lines=[],
        changed_files=[],
        has_changes=False,
        suggested_commit_message="test: h049 happy path",
        suggested_next_commands=[],
        status="ready",
        notes=["No commit, push, PR, merge, or issue close was performed."],
    )

    with (
        patch("signposter.pr.plan_handoff_for_issue", return_value=handoff),
        patch(
            "signposter.pr._get_branch_changed_files",
            return_value=["src/signposter/runner.py", "tests/test_runner.py"],
        ),
    ):
        pr_plan = plan_pr_for_issue(repo, issue)

    pr_body = pr_plan.suggested_pr_body
    assert pr_plan.status == "ready"
    assert pr_plan.changed_files == ["src/signposter/runner.py", "tests/test_runner.py"]
    assert f"Related issue: #{issue}" in pr_body
    assert contains_auto_close_keyword(pr_body) is False

    with (
        patch("signposter.merge._run_gh_pr_view") as mock_view,
        patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews,
        patch("signposter.merge.evaluate_review_gate") as mock_gate,
        patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks,
    ):
        mock_view.return_value = {
            "title": pr_plan.suggested_pr_title,
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": pr_body,
            "files": [{"path": path} for path in pr_plan.changed_files],
            "additions": 20,
            "deletions": 2,
        }
        mock_reviews.return_value = {
            "pr_author": "ExatronOmega",
            "review_decision": "APPROVED",
            "approving_reviewers": ["AlphaExatron"],
        }
        mock_gate.return_value = type(
            "Gate",
            (),
            {
                "gate_pass": True,
                "opinion": type(
                    "Opinion",
                    (),
                    {"verdict": "APPROVE", "confidence": 0.95, "risk": "low"},
                )(),
            },
        )()
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }

        merge_plan = plan_merge_for_pr(repo, pr)

    assert merge_plan.status == "ready"
    assert merge_plan.associated_issue == issue
    assert merge_plan.has_non_author_approval is True
    assert merge_plan.has_auto_close_keywords is False

    with (
        patch("signposter.integration._fetch_pr_merge_details") as mock_pr,
        patch("signposter.integration.fetch_issue_by_number") as mock_issue,
        patch("signposter.integration.fetch_issue_context") as mock_ctx,
        patch("signposter.integration._fetch_main_ci_status", return_value="pass"),
    ):
        mock_pr.return_value = {
            "number": pr,
            "title": pr_plan.suggested_pr_title,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeCommit": {"oid": "abc123def456"},
            "body": pr_body,
        }
        mock_issue.return_value = type("Issue", (), {"labels": ["state:done"]})()
        mock_ctx.return_value = {"state": "OPEN"}

        integration_plan = plan_integration_for_pr(repo, pr)

    assert integration_plan.status == "ready"
    assert integration_plan.associated_issue == issue
    assert integration_plan.close_issue is True
    assert integration_plan.proposed_workflow_state == "state:merged"

    with (
        patch("signposter.cleanup._run_gh_pr_view") as mock_cleanup_pr,
        patch("signposter.cleanup.fetch_issue_context") as mock_cleanup_ctx,
        patch("signposter.cleanup._worktree_exists", return_value=True),
        patch("signposter.cleanup._local_branch_exists", return_value=True),
    ):
        mock_cleanup_pr.return_value = {
            "state": "MERGED",
            "headRefName": branch,
            "body": pr_body,
        }
        mock_cleanup_ctx.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        }

        cleanup_plan = plan_cleanup_for_pr(repo, pr)

    assert cleanup_plan.status == "ready"
    assert cleanup_plan.associated_issue == issue
    assert cleanup_plan.worktree_exists is True
    assert cleanup_plan.local_branch_exists is True

    with (
        patch("signposter.lifecycle.fetch_issue_by_number") as mock_lifecycle_issue,
        patch("signposter.lifecycle.fetch_issue_context") as mock_lifecycle_ctx,
        patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=pr),
        patch("signposter.lifecycle._run_gh_pr_view") as mock_lifecycle_pr,
        patch("signposter.lifecycle._worktree_exists", return_value=False),
        patch("signposter.lifecycle._local_branch_exists", return_value=False),
    ):
        mock_lifecycle_issue.return_value = type(
            "Issue",
            (),
            {
                "labels": [
                    "state:merged",
                    "phase:build",
                    "risk:low",
                    "role:worker",
                    "area:tests",
                ]
            },
        )()
        mock_lifecycle_ctx.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        }
        mock_lifecycle_pr.return_value = {
            "number": pr,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeCommit": {"oid": "abc123def456"},
            "body": pr_body,
            "reviews": [{"state": "APPROVED", "author": {"login": "AlphaExatron"}}],
        }

        lifecycle = plan_lifecycle_status(repo, issue=issue)

    assert lifecycle.status == "complete"
    assert lifecycle.pr_number == pr
    assert lifecycle.integrated is True
    assert lifecycle.cleanup_complete is True


def test_docs_only_lifecycle_smoke_from_worker_summary_to_cleanup() -> None:
    """Smoke a docs-only task from worker evidence through final cleanup."""
    from signposter.artifact import build_worker_summary
    from signposter.gate import evaluate_ci_gate

    repo = "test/repo"
    issue = 45
    pr = 46
    branch = "work/issue-45-docs-only-reference-update"
    changed_files = ["docs/troubleshooting.md"]
    worker_summary = build_worker_summary(
        repo=repo,
        issue=issue,
        agent="human/operator",
        changed_files=changed_files,
        implemented_behavior=[
            "Scoped documentation-only lifecycle reference was updated.",
        ],
        targeted_validation=[
            "git diff --check -- docs/troubleshooting.md",
        ],
        full_validation=[
            "ruff check .",
            "python -m pytest tests/ -q",
        ],
        manual_smoke=[
            "git diff --check -- docs/troubleshooting.md",
        ],
    )

    gate_decision = evaluate_ci_gate(0, worker_summary)
    assert gate_decision.decision == "pass"
    assert gate_decision.proposed_transition == "state:active → state:done"
    assert "Docs-only scope: yes" in worker_summary
    assert "Changed files are documentation-only: yes" in worker_summary

    handoff = HandoffPlan(
        issue_number=issue,
        title="H050 docs-only lifecycle smoke",
        workflow_state="done",
        github_issue_state="OPEN",
        worktree_path=f"../signposter-work/{issue}",
        branch=branch,
        worktree_exists=True,
        current_branch_in_worktree=branch,
        status_lines=[],
        changed_files=[],
        has_changes=False,
        suggested_commit_message="docs: h050 docs-only smoke",
        suggested_next_commands=[],
        status="ready",
        notes=["No commit, push, PR, merge, or issue close was performed."],
    )

    with (
        patch("signposter.pr.plan_handoff_for_issue", return_value=handoff),
        patch("signposter.pr._get_branch_changed_files", return_value=changed_files),
    ):
        pr_plan = plan_pr_for_issue(repo, issue)

    pr_body = pr_plan.suggested_pr_body
    assert pr_plan.status == "ready"
    assert pr_plan.changed_files == changed_files
    assert f"Related issue: #{issue}" in pr_body
    assert contains_auto_close_keyword(pr_body) is False

    with (
        patch("signposter.merge._run_gh_pr_view") as mock_view,
        patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews,
        patch("signposter.merge.evaluate_review_gate") as mock_review_gate,
        patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks,
    ):
        mock_view.return_value = {
            "title": pr_plan.suggested_pr_title,
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": pr_body,
            "files": [{"path": path} for path in changed_files],
            "additions": 4,
            "deletions": 0,
        }
        mock_reviews.return_value = {
            "pr_author": "ExatronOmega",
            "review_decision": "APPROVED",
            "approving_reviewers": ["AlphaExatron"],
        }
        mock_review_gate.return_value = _review_gate_result(risk="low")
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }

        merge_plan = plan_merge_for_pr(repo, pr)

    assert merge_plan.status == "ready"
    assert merge_plan.associated_issue == issue
    assert merge_plan.has_non_author_approval is True
    assert merge_plan.has_auto_close_keywords is False
    assert merge_plan.risk_level == "low"

    with (
        patch("signposter.integration._fetch_pr_merge_details") as mock_pr,
        patch("signposter.integration.fetch_issue_by_number") as mock_issue,
        patch("signposter.integration.fetch_issue_context") as mock_ctx,
        patch("signposter.integration._fetch_main_ci_status", return_value="pass"),
    ):
        mock_pr.return_value = {
            "number": pr,
            "title": pr_plan.suggested_pr_title,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeCommit": {"oid": "def456abc789"},
            "body": pr_body,
        }
        mock_issue.return_value = type("Issue", (), {"labels": ["state:done"]})()
        mock_ctx.return_value = {"state": "OPEN"}

        integration_plan = plan_integration_for_pr(repo, pr)

    assert integration_plan.status == "ready"
    assert integration_plan.associated_issue == issue
    assert integration_plan.close_issue is True
    assert integration_plan.proposed_workflow_state == "state:merged"

    with (
        patch("signposter.cleanup._run_gh_pr_view") as mock_cleanup_pr,
        patch("signposter.cleanup.fetch_issue_context") as mock_cleanup_ctx,
        patch("signposter.cleanup._worktree_exists", return_value=True),
        patch("signposter.cleanup._local_branch_exists", return_value=True),
    ):
        mock_cleanup_pr.return_value = {
            "state": "MERGED",
            "headRefName": branch,
            "body": pr_body,
        }
        mock_cleanup_ctx.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        }

        cleanup_plan = plan_cleanup_for_pr(repo, pr)

    assert cleanup_plan.status == "ready"
    assert cleanup_plan.associated_issue == issue
    assert cleanup_plan.worktree_exists is True
    assert cleanup_plan.local_branch_exists is True

    with (
        patch("signposter.lifecycle.fetch_issue_by_number") as mock_lifecycle_issue,
        patch("signposter.lifecycle.fetch_issue_context") as mock_lifecycle_ctx,
        patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=pr),
        patch("signposter.lifecycle._run_gh_pr_view") as mock_lifecycle_pr,
        patch("signposter.lifecycle._worktree_exists", return_value=False),
        patch("signposter.lifecycle._local_branch_exists", return_value=False),
    ):
        mock_lifecycle_issue.return_value = type(
            "Issue",
            (),
            {
                "labels": [
                    "state:merged",
                    "phase:build",
                    "risk:low",
                    "role:worker",
                    "area:docs",
                ]
            },
        )()
        mock_lifecycle_ctx.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        }
        mock_lifecycle_pr.return_value = {
            "number": pr,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeCommit": {"oid": "def456abc789"},
            "body": pr_body,
            "reviews": [{"state": "APPROVED", "author": {"login": "AlphaExatron"}}],
        }

        lifecycle = plan_lifecycle_status(repo, issue=issue)

    assert lifecycle.status == "complete"
    assert lifecycle.pr_number == pr
    assert lifecycle.integrated is True
    assert lifecycle.cleanup_complete is True


def test_code_change_lifecycle_smoke_exercises_review_and_merge_gates() -> None:
    """Smoke a representative code-change path through worker and merge gates."""
    from signposter.artifact import build_worker_summary
    from signposter.gate import evaluate_ci_gate

    repo = "test/repo"
    issue = 47
    pr = 48
    branch = "work/issue-47-code-change-lifecycle-smoke"
    changed_files = ["src/signposter/report.py", "tests/test_report.py"]
    worker_summary = build_worker_summary(
        repo=repo,
        issue=issue,
        agent="human/operator",
        changed_files=changed_files,
        implemented_behavior=[
            "Scoped code-change lifecycle smoke behavior was covered.",
        ],
        targeted_validation=[
            "python -m pytest tests/test_full_lifecycle_happy_path.py -q",
        ],
        full_validation=[
            "ruff check .",
            "python -m pytest tests/ -q",
        ],
        manual_smoke=[
            "python -m pytest tests/test_full_lifecycle_happy_path.py -q",
        ],
    )

    gate_decision = evaluate_ci_gate(0, worker_summary)
    assert gate_decision.decision == "pass"
    assert gate_decision.proposed_transition == "state:active → state:done"
    assert "Code behavior unchanged: yes" not in worker_summary

    pr_body = f"Related issue: #{issue}"
    with (
        patch("signposter.merge._run_gh_pr_view") as mock_view,
        patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews,
        patch("signposter.merge.evaluate_review_gate") as mock_review_gate,
        patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks,
    ):
        mock_view.return_value = {
            "title": "H050 code-change lifecycle smoke",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": branch,
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": pr_body,
            "files": [{"path": path} for path in changed_files],
            "additions": 110,
            "deletions": 4,
        }
        mock_reviews.return_value = {
            "pr_author": "ExatronOmega",
            "review_decision": "APPROVED",
            "approving_reviewers": ["AlphaExatron"],
        }
        mock_review_gate.return_value = _review_gate_result(risk="medium")
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }

        blocked_without_scope_override = plan_merge_for_pr(
            repo,
            pr,
            allow_medium_risk=True,
        )
        ready_with_overrides = plan_merge_for_pr(
            repo,
            pr,
            allow_medium_risk=True,
            allow_medium_scope=True,
        )

    assert blocked_without_scope_override.status == "blocked — PR scope is medium"
    assert blocked_without_scope_override.reviewer_gate_pass is True
    assert blocked_without_scope_override.reviewer_risk == "medium"
    assert ready_with_overrides.status == "ready"
    assert ready_with_overrides.risk_level == "medium"
    assert ready_with_overrides.size == "medium"
    assert ready_with_overrides.has_non_author_approval is True
    assert ready_with_overrides.has_auto_close_keywords is False
    assert "Medium-risk override explicitly allowed" in "\n".join(
        ready_with_overrides.notes
    )
    assert "Medium-scope override explicitly allowed" in "\n".join(
        ready_with_overrides.notes
    )


def test_full_lifecycle_blocked_paths_stop_before_mutation() -> None:
    failing_gate = _plan_merge_with(gate_pass=False)
    assert failing_gate.status == "blocked — local reviewer gate is not pass"
    assert "No merge was performed." in failing_gate.notes

    red_ci = _plan_merge_with(checks_status="failing")
    assert red_ci.status == "blocked — checks are failing"
    assert "No issue was closed." in red_ci.notes

    missing_approval = _plan_merge_with(approving_reviewers=[])
    assert missing_approval.status == "blocked — no non-author approval found"

    merge_blocked = _plan_merge_with(mergeable="CONFLICTING")
    assert merge_blocked.status == "blocked — PR is not mergeable (CONFLICTING)"

    with patch("signposter.integration._fetch_pr_merge_details") as mock_pr:
        mock_pr.return_value = {
            "number": 43,
            "title": "not merged",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-42-token-budget-report",
            "mergeCommit": None,
            "body": "Related issue: #42",
        }
        integration_blocked = plan_integration_for_pr("test/repo", 43)

    assert integration_blocked.status == "blocked — PR is not merged (state: OPEN)"
    assert "No GitHub mutation was performed." in integration_blocked.notes

    with (
        patch("signposter.cleanup._run_gh_pr_view") as mock_cleanup_pr,
        patch("signposter.cleanup.fetch_issue_context") as mock_cleanup_ctx,
    ):
        mock_cleanup_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-42-token-budget-report",
            "body": "Related issue: #42",
        }
        mock_cleanup_ctx.return_value = {
            "state": "OPEN",
            "labels": [{"name": "state:done"}],
        }
        cleanup_blocked = plan_cleanup_for_pr("test/repo", 43)

    assert "blocked — associated issue #42 is not CLOSED" in cleanup_blocked.status
    assert "No GitHub mutation was performed." in cleanup_blocked.notes


def test_full_lifecycle_validated_noop_gate_integration_and_status(
    monkeypatch,
    tmp_path,
) -> None:
    """Smoke the no-PR validated no-op path from gate evidence to lifecycle complete."""
    from signposter.gate import evaluate_ci_gate
    from signposter.integration import (
        apply_noop_integration,
        format_noop_integration_apply_dry_run,
        format_noop_integration_plan,
        plan_noop_integration_for_issue,
    )

    repo = "test/repo"
    issue = 44

    monkeypatch.chdir(tmp_path)
    artifact_dir = tmp_path / "artifacts" / "runs"
    artifact_dir.mkdir(parents=True)
    summary = """
# Signposter Execution Summary

**Repository:** test/repo
**Issue:** #44 — H049 validated no-op smoke
**Agent:** worker
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

Validated no-op completion: requested behavior already exists.
The existing implementation provides deterministic no-op gate handling.
Existing ready output is deterministic and terminal-friendly.
Existing blocked output is deterministic and terminal-friendly.

## Files changed

No files were changed in the isolated worktree.

## Validation evidence

Targeted validation in isolated worktree passed:
- ruff check tests/test_full_lifecycle_happy_path.py
- pytest tests/test_full_lifecycle_happy_path.py -q

Full validation in isolated worktree passed:
- ruff check .
- pytest tests/ -q

Manual CLI smoke passed.

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.

## Gate recommendation

PASS — scoped no-op worker task completed with validation evidence.
"""
    (artifact_dir / f"issue-{issue}-gate.summary.md").write_text(
        summary,
        encoding="utf-8",
    )

    decision = evaluate_ci_gate(0, summary)
    assert decision.decision == "pass"
    assert decision.proposed_transition == "state:active → state:done"

    class Proc:
        def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "view"]:
            return Proc(
                stdout=(
                    '{"number":44,"title":"H049 validated no-op smoke",'
                    '"state":"OPEN","labels":[{"name":"state:done"},'
                    '{"name":"phase:build"}]}'
                )
            )
        if cmd[:3] == ["git", "branch", "--list"]:
            return Proc(stdout="")
        if cmd[:3] == ["gh", "pr", "list"]:
            return Proc(stdout="[]")
        if cmd[:3] == ["gh", "issue", "edit"]:
            return Proc()
        if cmd[:3] == ["gh", "issue", "close"]:
            return Proc()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("signposter.integration.subprocess.run", fake_run)

    plan = plan_noop_integration_for_issue(repo, issue)
    assert plan.status == "ready"
    assert plan.gate_decision == "pass"
    assert plan.associated_pr_detected is False

    plan_output = format_noop_integration_plan(plan)
    apply_dry_run = format_noop_integration_apply_dry_run(plan, repo)
    assert "Signposter No-op Integration Plan — Issue #44" in plan_output
    assert "Status:\n  ready" in apply_dry_run
    assert "No PR merge was performed." in apply_dry_run

    result = apply_noop_integration(repo, issue, apply=True)
    assert result["success"] is True
    mutation_calls = [cmd for cmd in calls if cmd[:3] == ["gh", "issue", "edit"]]
    close_calls = [cmd for cmd in calls if cmd[:3] == ["gh", "issue", "close"]]
    assert mutation_calls == [
        [
            "gh",
            "issue",
            "edit",
            "44",
            "-R",
            "test/repo",
            "--add-label",
            "state:merged",
            "--remove-label",
            "state:done",
        ]
    ]
    assert close_calls == [
        [
            "gh",
            "issue",
            "close",
            "44",
            "-R",
            "test/repo",
            "--reason",
            "completed",
            "--comment",
            "**Signposter:** completed validated no-op task.\n\n"
            "`state:done → state:merged` · no PR required",
        ]
    ]

    with (
        patch("signposter.lifecycle.fetch_issue_by_number") as mock_issue,
        patch("signposter.lifecycle.fetch_issue_context") as mock_ctx,
        patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=None),
        patch("signposter.lifecycle._worktree_exists", return_value=False),
    ):
        mock_issue.return_value = type(
            "Issue",
            (),
            {
                "labels": [
                    "state:merged",
                    "phase:build",
                    "risk:low",
                    "role:worker",
                    "area:tests",
                ]
            },
        )()
        mock_ctx.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        }

        lifecycle = plan_lifecycle_status(repo, issue=issue)

    assert lifecycle.status == "complete"
    assert lifecycle.integrated is True
    assert lifecycle.cleanup_complete is True
