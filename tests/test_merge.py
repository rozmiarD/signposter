"""Tests for merge planning (HARDENING-019)."""

from unittest.mock import patch

from signposter.merge import (
    MergePlan,
    _has_auto_close_keywords,
    format_merge_plan,
    plan_merge_for_pr,
)


def test_has_auto_close_keywords():
    assert _has_auto_close_keywords("This closes #42") is True
    assert _has_auto_close_keywords("Fixes #100") is True
    assert _has_auto_close_keywords("Resolves github.com/foo/bar#7") is True
    assert _has_auto_close_keywords("Related issue: #4") is False
    assert _has_auto_close_keywords(None) is False


def test_merge_plan_blocks_on_closed_pr():
    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate:

        mock_view.return_value = {
            "title": "test", "state": "CLOSED", "baseRefName": "main",
            "headRefName": "work/issue-4-xxx", "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED", "body": "",
            "files": [], "additions": 0, "deletions": 0,
        }
        mock_reviews.return_value = {
            "pr_author": "someone",
            "review_decision": "APPROVED",
            "approving_reviewers": ["AlphaExatron"],
        }
        mock_gate.return_value = type("G", (), {
            "gate_pass": True,
            "opinion": type("O", (), {
                "verdict": "APPROVE",
                "confidence": 0.95,
                "risk": "low",
            })(),
        })()

        plan = plan_merge_for_pr("test/repo", 5)
        assert "blocked — PR is closed" in plan.status


def test_merge_plan_ready_for_ideal_pr5():
    """Happy path for a clean, approved, low-risk PR with non-author approval."""
    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate, \
         patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks:

        mock_view.return_value = {
            "title": "docs: test-task-isolated-worker-readme-note",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": "Related issue: #4",
            "files": [{"path": "README.md"}],
            "additions": 8,
            "deletions": 0,
        }
        mock_reviews.return_value = {
            "pr_author": "ExatronOmega",
            "review_decision": "APPROVED",
            "approving_reviewers": ["AlphaExatron"],
        }
        mock_gate.return_value = type("G", (), {
            "gate_pass": True,
            "opinion": type("O", (), {
                "verdict": "APPROVE",
                "confidence": 0.95,
                "risk": "low",
            })()
        })()
        mock_checks.return_value = {"status": "pass", "successful": 1, "failing": 0, "pending": 0}

        plan = plan_merge_for_pr("test/repo", 5)

        assert plan.status == "ready"
        assert plan.merge_method == "squash"
        assert plan.delete_branch_after_merge is True
        assert "--squash --delete-branch" in plan.command_preview
        assert plan.associated_issue == 4
        assert plan.has_non_author_approval is True
        assert plan.reviewer_gate_pass is True
        assert plan.size == "small"
        assert "No merge was performed" in plan.notes[0]


def test_merge_plan_blocks_on_auto_close_keywords():
    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate, \
         patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks:

        mock_view.return_value = {
            "title": "test", "state": "OPEN", "baseRefName": "main",
            "headRefName": "work/issue-4-xxx", "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED", "body": "This closes #4",
            "files": [{"path": "README.md"}], "additions": 5, "deletions": 0,
        }
        mock_reviews.return_value = {
            "pr_author": "ExatronOmega",
            "review_decision": "APPROVED",
            "approving_reviewers": ["AlphaExatron"],
        }
        mock_gate.return_value = type("G", (), {
            "gate_pass": True,
            "opinion": type("O", (), {
                "verdict": "APPROVE",
                "confidence": 0.95,
                "risk": "low",
            })(),
        })()
        mock_checks.return_value = {"status": "pass", "successful": 1, "failing": 0, "pending": 0}

        plan = plan_merge_for_pr("test/repo", 5)
        assert "blocked — PR body contains auto-close keywords" in plan.status


def test_format_merge_plan_contains_key_sections():
    plan = MergePlan(
        pr_number=5,
        title="docs change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        mergeable="MERGEABLE",
        review_decision="APPROVED",
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        github_approved=True,
        approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True,
        pr_author="ExatronOmega",
        reviewer_gate_pass=True,
        reviewer_verdict="APPROVE",
        reviewer_confidence=0.95,
        reviewer_risk="low",
        associated_issue=4,
        has_auto_close_keywords=False,
        files_changed=1,
        additions=8,
        deletions=0,
        risk_level="low",
        size="small",
        merge_method="squash",
        delete_branch_after_merge=True,
        command_preview="gh pr merge 5 ... --squash --delete-branch",
        status="ready",
        notes=["No merge was performed."],
    )

    output = format_merge_plan(plan)

    assert "Signposter Merge Plan — PR #5" in output
    assert "method: squash" in output
    assert "--squash --delete-branch" in output
    assert "No merge was performed" in output
    assert "delete branch after merge: yes" in output
    assert "AlphaExatron" in output or "non-author approval" in output
