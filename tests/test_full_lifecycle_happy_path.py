from unittest.mock import patch

from signposter.cleanup import plan_cleanup_for_pr
from signposter.comments import contains_auto_close_keyword
from signposter.handoff import HandoffPlan
from signposter.integration import plan_integration_for_pr
from signposter.lifecycle import plan_lifecycle_status
from signposter.merge import plan_merge_for_pr
from signposter.pr import plan_pr_for_issue


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
