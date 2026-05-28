"""Tests for post-merge integration planning (HARDENING-021A)."""

from unittest.mock import patch

from signposter.integration import (
    IntegrationPlan,
    format_integration_plan,
    plan_integration_for_pr,
)


def test_integration_plan_ready_for_merged_pr():
    with patch("signposter.integration._fetch_pr_merge_details") as mock_pr, \
         patch("signposter.integration.fetch_issue_by_number") as mock_issue:

        mock_pr.return_value = {
            "number": 5,
            "title": "docs change",
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "mergeCommit": {"oid": "abc123def456"},
            "body": "Related issue: #4",
        }

        class FakeIssue:
            labels = ["state:done", "area:docs"]

        mock_issue.return_value = FakeIssue()

        with patch("signposter.integration.fetch_issue_context") as mock_ctx:
            mock_ctx.return_value = {"state": "OPEN"}

            plan = plan_integration_for_pr("test/repo", 5)

        assert plan.status == "ready"
        assert plan.pr_state == "MERGED"
        assert plan.merge_commit == "abc123def456"
        assert plan.associated_issue == 4
        assert plan.issue_state == "OPEN"
        assert plan.current_workflow_state == "state:done"
        assert plan.proposed_workflow_state == "state:merged"
        assert plan.close_issue is True
        assert plan.close_reason == "completed"


def test_integration_plan_blocks_when_pr_not_merged():
    with patch("signposter.integration._fetch_pr_merge_details") as mock_pr:
        mock_pr.return_value = {
            "number": 5,
            "title": "test",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-4-xxx",
            "mergeCommit": None,
            "body": "",
        }

        plan = plan_integration_for_pr("test/repo", 5)
        assert "blocked — PR is not merged" in plan.status


def test_integration_plan_blocks_when_issue_missing():
    with patch("signposter.integration._fetch_pr_merge_details") as mock_pr:
        mock_pr.return_value = {
            "number": 99,
            "title": "no issue",
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": "feature/something",
            "mergeCommit": {"oid": "abc123"},
            "body": "",
        }

        plan = plan_integration_for_pr("test/repo", 99)
        assert "blocked — associated issue could not be detected" in plan.status


def test_format_integration_plan_contains_key_sections():
    plan = IntegrationPlan(
        pr_number=5,
        pr_title="docs change",
        pr_state="MERGED",
        merge_commit="cea5bc170c90eda3089412d15285e426da88b3a1",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        associated_issue=4,
        issue_state="OPEN",
        current_workflow_state="state:done",
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        main_ci_status="unknown",
        status="ready",
        notes=["No issue was closed."],
    )

    output = format_integration_plan(plan)

    assert "Signposter Integration Plan — PR #5" in output
    assert "state: MERGED" in output
    assert "merge commit: cea5bc17" in output
    assert "associated issue: #4" in output
    assert "proposed workflow state: state:merged" in output
    assert "close reason: completed" in output
    assert "No issue was closed" in output
    assert "Status:" in output
    assert "ready" in output
