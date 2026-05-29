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
        # H022C linkage defaults
        linkage_source="branch-pattern",
        linkage_confidence="high",
        formal_github_development_link="no/unknown",
        auto_close_keyword=False,
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


def test_lifecycle_status_shows_branch_pattern_source():
    s = _make_complete_status(
        pr_head="work/issue-4-test-task-isolated-worker-readme-note",
        linkage_source="branch-pattern",
        linkage_confidence="high",
        auto_close_keyword=False,
    )
    out = format_lifecycle_status(s)
    assert "Linkage:" in out
    assert "source: branch-pattern" in out
    assert "confidence: high" in out
    assert "auto-close keyword: no" in out


def test_lifecycle_status_shows_pr_body_related_issue_source():
    s = _make_complete_status(
        linkage_source="pr-body-related-issue",
        linkage_confidence="medium",
        auto_close_keyword=False,
    )
    out = format_lifecycle_status(s)
    assert "source: pr-body-related-issue" in out
    assert "confidence: medium" in out


def test_lifecycle_status_detects_closing_keyword():
    s = _make_complete_status(
        linkage_source="closing-keyword",
        linkage_confidence="high",
        auto_close_keyword=True,
        formal_github_development_link="yes",
    )
    out = format_lifecycle_status(s)
    assert "source: closing-keyword" in out
    assert "auto-close keyword: yes" in out
    assert "formal GitHub development link: yes" in out


def test_related_issue_does_not_set_auto_close_keyword():
    s = _make_complete_status(linkage_source="pr-body-related-issue", auto_close_keyword=False)
    out = format_lifecycle_status(s)
    assert "auto-close keyword: no" in out


def test_cli_rejects_both_issue_and_pr():
    # This is tested via the handler in cli.py (we only test the core here)
    # The core function still produces a status object
    s = plan_lifecycle_status("x/y", issue=1, pr=2)
    assert "exactly one" in s.status.lower()


def test_cli_rejects_neither():
    s = plan_lifecycle_status("x/y")
    assert "exactly one" in s.status.lower()

# =============================================================================
# Issue-to-PR detection regression tests (HARDENING-028B-Lite)
# =============================================================================


def _gh_pr_list_result(prs):
    import json

    return type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": json.dumps(prs),
            "stderr": "",
        },
    )()


def test_detect_associated_pr_from_issue_finds_open_pr_by_branch_pattern():
    from signposter.lifecycle import _detect_associated_pr_from_issue

    open_prs = [
        {
            "number": 9,
            "headRefName": "work/issue-8-smoke-test-post-h027-full-lifecycle-docs-note",
            "body": "",
        }
    ]

    with patch(
        "signposter.lifecycle.subprocess.run",
        return_value=_gh_pr_list_result(open_prs),
    ) as run:
        assert _detect_associated_pr_from_issue("ExatronOmega/signposter", 8) == 9

    assert run.call_count == 1
    assert "--state" in run.call_args.args[0]
    assert "open" in run.call_args.args[0]


def test_detect_associated_pr_from_issue_finds_open_pr_by_related_issue_body():
    from signposter.lifecycle import _detect_associated_pr_from_issue

    open_prs = [
        {
            "number": 9,
            "headRefName": "docs/smoke-003-note",
            "body": "Related issue: #8\n\nDocs-only smoke note.",
        }
    ]

    with patch(
        "signposter.lifecycle.subprocess.run",
        return_value=_gh_pr_list_result(open_prs),
    ):
        assert _detect_associated_pr_from_issue("ExatronOmega/signposter", 8) == 9


def test_detect_associated_pr_from_issue_prefers_open_pr_over_merged_pr():
    from signposter.lifecycle import _detect_associated_pr_from_issue

    open_prs = [
        {
            "number": 9,
            "headRefName": "work/issue-8-smoke-test-post-h027-full-lifecycle-docs-note",
            "body": "Related issue: #8",
        }
    ]
    merged_prs = [
        {
            "number": 7,
            "headRefName": "work/issue-8-old-merged-pr",
            "body": "Related issue: #8",
        }
    ]

    with patch(
        "signposter.lifecycle.subprocess.run",
        side_effect=[_gh_pr_list_result(open_prs), _gh_pr_list_result(merged_prs)],
    ) as run:
        assert _detect_associated_pr_from_issue("ExatronOmega/signposter", 8) == 9

    # Open PR match should short-circuit before merged PR search.
    assert run.call_count == 1
    assert "open" in run.call_args.args[0]


def test_detect_associated_pr_from_issue_falls_back_to_merged_pr():
    from signposter.lifecycle import _detect_associated_pr_from_issue

    open_prs = []
    merged_prs = [
        {
            "number": 7,
            "headRefName": "work/issue-8-old-merged-pr",
            "body": "Related issue: #8",
        }
    ]

    with patch(
        "signposter.lifecycle.subprocess.run",
        side_effect=[_gh_pr_list_result(open_prs), _gh_pr_list_result(merged_prs)],
    ) as run:
        assert _detect_associated_pr_from_issue("ExatronOmega/signposter", 8) == 7

    assert run.call_count == 2
    first_call_args = run.call_args_list[0].args[0]
    second_call_args = run.call_args_list[1].args[0]
    assert "open" in first_call_args
    assert "merged" in second_call_args
