"""Tests for HARDENING-022A — lifecycle status command."""

from __future__ import annotations

from unittest.mock import patch

from signposter.lifecycle import (
    LifecycleStatus,
    format_lifecycle_status,
    plan_lifecycle_status,
)


def _make_complete_status(**overrides) -> LifecycleStatus:
    base = dict(
        query_issue=4,
        query_pr=5,
        issue_number=4,
        issue_state="CLOSED",
        workflow_state="state:merged",
        phase="phase:build",
        risk="risk:low",
        role="role:worker",
        area="area:docs",
        pr_number=5,
        pr_state="MERGED",
        pr_base="main",
        pr_head="work/issue-4-test-task-isolated-worker-readme-note",
        pr_merged=True,
        merge_commit="abc123def456",
        review_decision="APPROVED",
        has_non_author_approval=True,
        reviewer_login="AlphaExatron",
        integrated=True,
        issue_closed=True,
        expected_worktree="../signposter-work/4",
        worktree_exists=False,
        local_branch_exists=False,
        cleanup_complete=True,
        status="complete",
        notes=[
            "Read-only status only.",
            "No GitHub mutation was performed.",
            "No local cleanup was performed.",
        ],
    )
    base.update(overrides)
    return LifecycleStatus(**base)


# =============================================================================
# Core happy path
# =============================================================================


def test_lifecycle_complete_for_issue_4_style_data():
    with patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=5), \
         patch("signposter.lifecycle._run_gh_pr_view") as m_pr, \
         patch("signposter.lifecycle._worktree_exists", return_value=False), \
         patch("signposter.lifecycle._local_branch_exists", return_value=False):

        m_issue.return_value = type(
            "I",
            (),
            {"labels": ["state:merged", "phase:build", "risk:low", "role:worker", "area:docs"]},
        )()
        m_ctx.return_value = {"state": "CLOSED", "labels": [{"name": "state:merged"}]}
        m_pr.return_value = {
            "number": 5,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "mergeCommit": {"oid": "abc123def456"},
            "body": "Related issue: #4",
            "reviews": [{"state": "APPROVED", "author": {"login": "AlphaExatron"}}],
        }

        status = plan_lifecycle_status("ExatronOmega/signposter", issue=4)

        assert status.status == "complete"
        assert status.issue_number == 4
        assert status.pr_number == 5
        assert status.pr_merged is True
        assert status.worktree_exists is False
        assert status.local_branch_exists is False


def test_lifecycle_complete_when_starting_from_pr():
    with patch("signposter.lifecycle._run_gh_pr_view") as m_pr, \
         patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._worktree_exists", return_value=False), \
         patch("signposter.lifecycle._local_branch_exists", return_value=False):

        m_pr.return_value = {
            "number": 5,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "mergeCommit": {"oid": "abc123"},
            "body": "",
            "reviews": [],
        }
        m_issue.return_value = type("I", (), {"labels": ["state:merged"]})()
        m_ctx.return_value = {"state": "CLOSED"}

        status = plan_lifecycle_status("ExatronOmega/signposter", pr=5)

        assert status.status == "complete"
        assert status.query_pr == 5
        assert status.issue_number == 4


# =============================================================================
# Incomplete cases (tested via real plan function + mocks)
# =============================================================================


def test_incomplete_when_issue_not_closed():
    with patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=5), \
         patch("signposter.lifecycle._run_gh_pr_view") as m_pr, \
         patch("signposter.lifecycle._worktree_exists", return_value=False), \
         patch("signposter.lifecycle._local_branch_exists", return_value=False):

        m_issue.return_value = type("I", (), {"labels": ["state:merged"]})()
        m_ctx.return_value = {"state": "OPEN"}
        m_pr.return_value = {
            "number": 5, "state": "MERGED", "baseRefName": "main",
            "headRefName": "work/issue-4-foo", "mergeCommit": {"oid": "x"},
            "body": "", "reviews": [],
        }

        s = plan_lifecycle_status("ExatronOmega/signposter", issue=4)
        assert "not CLOSED" in s.status


def test_incomplete_when_missing_state_merged():
    with patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=5), \
         patch("signposter.lifecycle._run_gh_pr_view") as m_pr, \
         patch("signposter.lifecycle._worktree_exists", return_value=False), \
         patch("signposter.lifecycle._local_branch_exists", return_value=False):

        m_issue.return_value = type("I", (), {"labels": []})()
        m_ctx.return_value = {"state": "CLOSED"}
        m_pr.return_value = {
            "number": 5, "state": "MERGED", "baseRefName": "main",
            "headRefName": "work/issue-4-foo", "mergeCommit": {"oid": "x"},
            "body": "", "reviews": [],
        }

        s = plan_lifecycle_status("ExatronOmega/signposter", issue=4)
        assert "lacks state:merged" in s.status


def test_incomplete_when_pr_not_merged():
    with patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=5), \
         patch("signposter.lifecycle._run_gh_pr_view") as m_pr, \
         patch("signposter.lifecycle._worktree_exists", return_value=False), \
         patch("signposter.lifecycle._local_branch_exists", return_value=False):

        m_issue.return_value = type("I", (), {"labels": ["state:merged"]})()
        m_ctx.return_value = {"state": "CLOSED"}
        m_pr.return_value = {
            "number": 5, "state": "OPEN", "baseRefName": "main",
            "headRefName": "work/issue-4-foo", "mergeCommit": None,
            "body": "", "reviews": [],
        }

        s = plan_lifecycle_status("ExatronOmega/signposter", pr=5)
        assert "is not merged" in s.status


def test_incomplete_when_worktree_still_exists():
    with patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=5), \
         patch("signposter.lifecycle._run_gh_pr_view") as m_pr, \
         patch("signposter.lifecycle._worktree_exists", return_value=True), \
         patch("signposter.lifecycle._local_branch_exists", return_value=False):

        m_issue.return_value = type("I", (), {"labels": ["state:merged"]})()
        m_ctx.return_value = {"state": "CLOSED"}
        m_pr.return_value = {
            "number": 5, "state": "MERGED", "baseRefName": "main",
            "headRefName": "work/issue-4-foo", "mergeCommit": {"oid": "x"},
            "body": "", "reviews": [],
        }

        s = plan_lifecycle_status("ExatronOmega/signposter", issue=4)
        assert "local worktree still exists" in s.status


def test_incomplete_when_local_branch_still_exists():
    with patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=5), \
         patch("signposter.lifecycle._run_gh_pr_view") as m_pr, \
         patch("signposter.lifecycle._worktree_exists", return_value=False), \
         patch("signposter.lifecycle._local_branch_exists", return_value=True):

        m_issue.return_value = type("I", (), {"labels": ["state:merged"]})()
        m_ctx.return_value = {"state": "CLOSED"}
        m_pr.return_value = {
            "number": 5, "state": "MERGED", "baseRefName": "main",
            "headRefName": "work/issue-4-foo", "mergeCommit": {"oid": "x"},
            "body": "", "reviews": [],
        }

        s = plan_lifecycle_status("ExatronOmega/signposter", issue=4)
        assert "local branch still exists" in s.status


# =============================================================================
# Blocked / unknown mapping
# =============================================================================


def test_blocked_when_no_associated_pr_from_issue():
    with patch("signposter.lifecycle.fetch_issue_by_number") as m_issue, \
         patch("signposter.lifecycle.fetch_issue_context") as m_ctx, \
         patch("signposter.lifecycle._detect_associated_pr_from_issue", return_value=None):

        m_issue.return_value = type("I", (), {"labels": ["state:merged"]})()
        m_ctx.return_value = {"state": "CLOSED"}

        status = plan_lifecycle_status("ExatronOmega/signposter", issue=99)

        assert "associated PR could not be detected" in status.status


def test_blocked_when_no_associated_issue_from_pr():
    with patch("signposter.lifecycle._run_gh_pr_view") as m_pr:
        m_pr.return_value = {
            "number": 99,
            "state": "MERGED",
            "headRefName": "some/random-branch",
            "body": "",
            "mergeCommit": {"oid": "x"},
            "reviews": [],
        }

        status = plan_lifecycle_status("ExatronOmega/signposter", pr=99)

        assert "associated issue could not be detected" in status.status


# =============================================================================
# Output & CLI contract
# =============================================================================


def test_output_contains_no_mutation_notes():
    s = _make_complete_status()
    out = format_lifecycle_status(s)
    assert "No GitHub mutation was performed" in out
    assert "No local cleanup was performed" in out
    assert "Read-only status only" in out


def test_cli_rejects_both_issue_and_pr():
    # This is tested via the handler in cli.py (we only test the core here)
    # The core function still produces a status object
    s = plan_lifecycle_status("x/y", issue=1, pr=2)
    assert "exactly one" in s.status.lower()


def test_cli_rejects_neither():
    s = plan_lifecycle_status("x/y")
    assert "exactly one" in s.status.lower()
