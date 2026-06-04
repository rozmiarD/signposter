"""Tests for merge planning (HARDENING-019)."""

import sys
from unittest.mock import patch

import pytest

from signposter.merge import (
    MergePlan,
    _has_auto_close_keywords,
    format_merge_plan,
    plan_merge_for_pr,
)


def test_has_auto_close_keywords():
    assert _has_auto_close_keywords("This closes #42") is True
    assert _has_auto_close_keywords("Closed #42") is True
    assert _has_auto_close_keywords("Fixes #100") is True
    assert _has_auto_close_keywords("Fixed issue #100") is True
    assert _has_auto_close_keywords("Fixed issue: #100") is True
    assert _has_auto_close_keywords("Fixes foo/bar#7") is True
    assert _has_auto_close_keywords("Resolves github.com/foo/bar#7") is True
    assert _has_auto_close_keywords("Resolve https://github.com/foo/bar/issues/7") is True
    assert _has_auto_close_keywords("Related issue: #4") is False
    assert _has_auto_close_keywords("Fix docs for issue #4") is False
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


def test_merge_plan_blocks_ambiguous_issue_linkage():
    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate, \
         patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks:

        mock_view.return_value = {
            "title": "test",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-4-xxx",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": "Related issue: #5",
            "files": [{"path": "README.md"}],
            "additions": 5,
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
            })(),
        })()
        mock_checks.return_value = {"status": "pass", "successful": 1, "failing": 0, "pending": 0}

        plan = plan_merge_for_pr("test/repo", 5)

    assert "blocked — associated issue link is ambiguous" in plan.status
    assert plan.associated_issue is None


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


def test_format_merge_plan_surfaces_failing_ci_blockage():
    plan = MergePlan(
        pr_number=5,
        title="ci failure",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        mergeable="MERGEABLE",
        review_decision="APPROVED",
        checks_status="failing",
        successful_checks=2,
        failing_checks=1,
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
        status="blocked — checks are failing",
        notes=["No merge was performed."],
    )

    output = format_merge_plan(plan)

    assert "Check blockage:" in output
    assert "category: failing-ci" in output
    assert "reason: 1 failing check(s), 0 pending check(s)" in output
    assert "inspect command: gh pr checks 5 --repo <repo>" in output
    assert "next: inspect failing checks for PR #5 and rerun merge plan" in output


def test_format_merge_plan_surfaces_pending_ci_blockage():
    plan = MergePlan(
        pr_number=5,
        title="ci pending",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        mergeable="MERGEABLE",
        review_decision="APPROVED",
        checks_status="pending",
        successful_checks=1,
        failing_checks=0,
        pending_checks=2,
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
        status="pending — checks are still running",
        notes=["No merge was performed."],
    )

    output = format_merge_plan(plan)

    assert "category: waiting-ci" in output
    assert "reason: 2 pending check(s), 1 successful check(s)" in output
    assert "inspect command: gh pr checks 5 --repo <repo>" in output
    assert "next: wait for CI completion and rerun merge plan" in output


def test_fetch_pr_checks_for_merge_treats_timed_out_as_failing(monkeypatch):
    from signposter.merge import _fetch_pr_checks_for_merge

    def fake_view(repo, pr, fields):
        assert fields == ["statusCheckRollup"]
        return {
            "statusCheckRollup": [
                {"status": "COMPLETED", "conclusion": "TIMED_OUT", "name": "test"}
            ]
        }

    monkeypatch.setattr("signposter.merge._run_gh_pr_view", fake_view)

    assert _fetch_pr_checks_for_merge("test/repo", 5) == {
        "status": "failing",
        "successful": 0,
        "failing": 1,
        "pending": 0,
    }


# =============================================================================
# HARDENING-020 tests: guarded merge apply
# =============================================================================


def test_merge_apply_dry_run_does_not_call_subprocess():
    from signposter.merge import apply_merge

    with patch("signposter.merge.plan_merge_for_pr") as mock_plan, \
         patch("subprocess.run") as mock_sub:

        fake_plan = MergePlan(
            pr_number=5, title="test", state="OPEN", base_branch="main",
            head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
            review_decision="APPROVED", checks_status="pass",
            successful_checks=1, failing_checks=0, pending_checks=0,
            github_approved=True, approving_reviewers=["AlphaExatron"],
            has_non_author_approval=True, pr_author="ExatronOmega",
            reviewer_gate_pass=True, reviewer_verdict="APPROVE",
            reviewer_confidence=0.95, reviewer_risk="low",
            associated_issue=4, has_auto_close_keywords=False,
            files_changed=1, additions=8, deletions=0,
            risk_level="low", size="small",
            merge_method="squash", delete_branch_after_merge=True,
            command_preview="gh pr merge 5 -R test/repo --squash --delete-branch",
            status="ready", notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_merge("test/repo", 5, apply=False)

        mock_sub.assert_not_called()
        assert result["mode"] == "dry_run"
        assert "gh pr merge" in result["command"]
        assert result["would_execute"] is True


def test_merge_apply_dry_run_hides_command_when_plan_blocked():
    from signposter.merge import apply_merge

    with patch("signposter.merge.plan_merge_for_pr") as mock_plan, \
         patch("subprocess.run") as mock_sub:

        fake_plan = MergePlan(
            pr_number=5, title="test", state="OPEN", base_branch="main",
            head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
            review_decision="APPROVED", checks_status="failing",
            successful_checks=1, failing_checks=1, pending_checks=0,
            github_approved=True, approving_reviewers=["AlphaExatron"],
            has_non_author_approval=True, pr_author="ExatronOmega",
            reviewer_gate_pass=True, reviewer_verdict="APPROVE",
            reviewer_confidence=0.95, reviewer_risk="low",
            associated_issue=4, has_auto_close_keywords=False,
            files_changed=1, additions=8, deletions=0,
            risk_level="low", size="small",
            merge_method="squash", delete_branch_after_merge=True,
            command_preview="gh pr merge 5 -R test/repo --squash --delete-branch",
            status="blocked — checks are failing", notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_merge("test/repo", 5, apply=False)

        mock_sub.assert_not_called()
        assert result["mode"] == "dry_run"
        assert result["command"] == ""
        assert result["would_execute"] is False


def test_merge_apply_refuses_when_plan_not_ready():
    from signposter.merge import apply_merge

    with patch("signposter.merge.plan_merge_for_pr") as mock_plan:
        fake_plan = MergePlan(
            pr_number=5, title="test", state="OPEN", base_branch="main",
            head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
            review_decision="APPROVED", checks_status="pass",
            successful_checks=1, failing_checks=0, pending_checks=0,
            github_approved=True, approving_reviewers=["AlphaExatron"],
            has_non_author_approval=True, pr_author="ExatronOmega",
            reviewer_gate_pass=True, reviewer_verdict="APPROVE",
            reviewer_confidence=0.95, reviewer_risk="low",
            associated_issue=4, has_auto_close_keywords=False,
            files_changed=1, additions=8, deletions=0,
            risk_level="low", size="small",
            merge_method="squash", delete_branch_after_merge=True,
            command_preview="gh ...",
            status="blocked — checks are failing",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_merge("test/repo", 5, apply=True)

        assert result["mode"] == "apply_blocked"
        assert "Refusing to merge" in result.get("error", "")


def test_merge_apply_already_merged_does_not_call_subprocess():
    from signposter.merge import apply_merge

    fake_plan = MergePlan(
        pr_number=5, title="test", state="MERGED", base_branch="main",
        head_branch="work/issue-4-xxx", mergeable="UNKNOWN",
        review_decision="APPROVED", checks_status="pass",
        successful_checks=1, failing_checks=0, pending_checks=0,
        github_approved=True, approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True, pr_author="ExatronOmega",
        reviewer_gate_pass=True, reviewer_verdict="APPROVE",
        reviewer_confidence=0.95, reviewer_risk="low",
        associated_issue=4, has_auto_close_keywords=False,
        files_changed=1, additions=8, deletions=0,
        risk_level="low", size="small",
        merge_method="squash", delete_branch_after_merge=True,
        command_preview="gh pr merge 5 -R test/repo --squash --delete-branch",
        status="blocked — PR is merged",
        notes=[],
    )

    with (
        patch("signposter.merge.plan_merge_for_pr", return_value=fake_plan),
        patch("signposter.merge.subprocess.run") as mock_run,
    ):
        result = apply_merge("test/repo", 5, apply=True)

    mock_run.assert_not_called()
    assert result["mode"] == "apply_blocked"
    assert "blocked — PR is merged" in result.get("error", "")


def test_merge_apply_with_apply_calls_gh_correctly(monkeypatch):
    from signposter.merge import apply_merge

    class FakeProc:
        returncode = 0
        stdout = "Merge successful."
        stderr = ""

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)

    with patch("signposter.merge.plan_merge_for_pr") as mock_plan:
        fake_plan = MergePlan(
            pr_number=5, title="test", state="OPEN", base_branch="main",
            head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
            review_decision="APPROVED", checks_status="pass",
            successful_checks=1, failing_checks=0, pending_checks=0,
            github_approved=True, approving_reviewers=["AlphaExatron"],
            has_non_author_approval=True, pr_author="ExatronOmega",
            reviewer_gate_pass=True, reviewer_verdict="APPROVE",
            reviewer_confidence=0.95, reviewer_risk="low",
            associated_issue=4, has_auto_close_keywords=False,
            files_changed=1, additions=8, deletions=0,
            risk_level="low", size="small",
            merge_method="squash", delete_branch_after_merge=True,
            command_preview="gh pr merge 5 -R test/repo --squash --delete-branch",
            status="ready", notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_merge("test/repo", 5, apply=True)

    assert result["mode"] == "apply"
    assert result["success"] is True
    assert any("--squash" in str(c) and "--delete-branch" in str(c) for c in calls)


def test_merge_apply_failed_gh_includes_stderr():
    from signposter.merge import apply_merge

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "GraphQL error: something went wrong"

    with patch("subprocess.run", return_value=FakeProc()):
        with patch("signposter.merge.plan_merge_for_pr") as mock_plan:
            fake_plan = MergePlan(
                pr_number=5, title="test", state="OPEN", base_branch="main",
                head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
                review_decision="APPROVED", checks_status="pass",
                successful_checks=1, failing_checks=0, pending_checks=0,
                github_approved=True, approving_reviewers=["AlphaExatron"],
                has_non_author_approval=True, pr_author="ExatronOmega",
                reviewer_gate_pass=True, reviewer_verdict="APPROVE",
                reviewer_confidence=0.95, reviewer_risk="low",
                associated_issue=4, has_auto_close_keywords=False,
                files_changed=1, additions=8, deletions=0,
                risk_level="low", size="small",
                merge_method="squash", delete_branch_after_merge=True,
                command_preview="gh ...",
                status="ready", notes=[],
            )
            mock_plan.return_value = fake_plan

            result = apply_merge("test/repo", 5, apply=True)

    assert result["success"] is False
    assert "something went wrong" in result.get("error", "") or result.get("stderr", "")


def test_format_merge_apply_dry_run_contains_safety_notes():
    from signposter.merge import format_merge_apply_dry_run

    fake_plan = MergePlan(
        pr_number=5, title="test", state="OPEN", base_branch="main",
        head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
        review_decision="APPROVED", checks_status="pass",
        successful_checks=1, failing_checks=0, pending_checks=0,
        github_approved=True, approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True, pr_author="ExatronOmega",
        reviewer_gate_pass=True, reviewer_verdict="APPROVE",
        reviewer_confidence=0.95, reviewer_risk="low",
        associated_issue=4, has_auto_close_keywords=False,
        files_changed=1, additions=8, deletions=0,
        risk_level="low", size="small",
        merge_method="squash", delete_branch_after_merge=True,
        command_preview="gh pr merge 5 -R test/repo --squash --delete-branch",
        status="ready", notes=[],
    )

    output = format_merge_apply_dry_run(fake_plan)

    assert "DRY RUN: no merge was performed" in output
    assert "No issue was closed" in output
    assert "No local worktree was removed" in output
    assert "--delete-branch" in output


def test_cli_merge_apply_dry_run_returns_blocked_exit_for_blocked_plan(
    monkeypatch, capsys
):
    from signposter.cli import main

    blocked_plan = MergePlan(
        pr_number=5, title="test", state="OPEN", base_branch="main",
        head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
        review_decision="APPROVED", checks_status="failing",
        successful_checks=1, failing_checks=1, pending_checks=0,
        github_approved=True, approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True, pr_author="ExatronOmega",
        reviewer_gate_pass=True, reviewer_verdict="APPROVE",
        reviewer_confidence=0.95, reviewer_risk="low",
        associated_issue=4, has_auto_close_keywords=False,
        files_changed=1, additions=8, deletions=0,
        risk_level="low", size="small",
        merge_method="squash", delete_branch_after_merge=True,
        command_preview="gh pr merge 5 -R test/repo --squash --delete-branch",
        status="blocked — checks are failing", notes=[],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "merge",
            "apply",
            "--repo",
            "test/repo",
            "--pr",
            "5",
        ],
    )

    with patch(
        "signposter.cli.apply_merge",
        return_value={
            "mode": "dry_run",
            "plan": blocked_plan,
            "command": "",
            "would_execute": False,
        },
    ), pytest.raises(SystemExit) as exc:
        main()

    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert "blocked — merge plan is not ready" in out
    assert "gh pr merge 5" not in out
    assert "No issue was closed" in out


# =============================================================================
# HARDENING-020A regression tests: dry-run must accurately reflect plan readiness
# =============================================================================


def test_format_merge_apply_dry_run_shows_ready_only_when_plan_ready():
    from signposter.merge import format_merge_apply_dry_run

    ready_plan = MergePlan(
        pr_number=5, title="test", state="OPEN", base_branch="main",
        head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
        review_decision="APPROVED", checks_status="pass",
        successful_checks=1, failing_checks=0, pending_checks=0,
        github_approved=True, approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True, pr_author="ExatronOmega",
        reviewer_gate_pass=True, reviewer_verdict="APPROVE",
        reviewer_confidence=0.95, reviewer_risk="low",
        associated_issue=4, has_auto_close_keywords=False,
        files_changed=1, additions=8, deletions=0,
        risk_level="low", size="small",
        merge_method="squash", delete_branch_after_merge=True,
        command_preview="gh pr merge 5 ... --squash --delete-branch",
        status="ready", notes=[],
    )

    output = format_merge_apply_dry_run(ready_plan)
    assert "Status:\n  ready" in output or "Status: ready" in output


def test_format_merge_apply_dry_run_shows_blocked_when_plan_blocked():
    from signposter.merge import format_merge_apply_dry_run

    blocked_plan = MergePlan(
        pr_number=5, title="test", state="OPEN", base_branch="main",
        head_branch="work/issue-4-xxx", mergeable="UNKNOWN",
        review_decision="APPROVED", checks_status="pass",
        successful_checks=1, failing_checks=0, pending_checks=0,
        github_approved=True, approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True, pr_author="ExatronOmega",
        reviewer_gate_pass=True, reviewer_verdict="APPROVE",
        reviewer_confidence=0.95, reviewer_risk="low",
        associated_issue=4, has_auto_close_keywords=False,
        files_changed=1, additions=8, deletions=0,
        risk_level="low", size="small",
        merge_method="squash", delete_branch_after_merge=True,
        command_preview="gh pr merge 5 ... --squash --delete-branch",
        status="blocked — PR is not mergeable (UNKNOWN)",
        notes=[],
    )

    output = format_merge_apply_dry_run(blocked_plan)

    assert "blocked — merge plan is not ready" in output
    assert (
        "blocked — PR is not mergeable (UNKNOWN)" in output
        or "blocked — merge plan is not ready" in output
    )
    assert "Status:\n  ready" not in output  # must not falsely claim ready


def test_apply_refuses_when_plan_blocked_even_with_apply_flag():
    from signposter.merge import apply_merge

    with patch("signposter.merge.plan_merge_for_pr") as mock_plan:
        blocked_plan = MergePlan(
            pr_number=5, title="test", state="OPEN", base_branch="main",
            head_branch="work/issue-4-xxx", mergeable="UNKNOWN",
            review_decision="APPROVED", checks_status="pass",
            successful_checks=1, failing_checks=0, pending_checks=0,
            github_approved=True, approving_reviewers=["AlphaExatron"],
            has_non_author_approval=True, pr_author="ExatronOmega",
            reviewer_gate_pass=True, reviewer_verdict="APPROVE",
            reviewer_confidence=0.95, reviewer_risk="low",
            associated_issue=4, has_auto_close_keywords=False,
            files_changed=1, additions=8, deletions=0,
            risk_level="low", size="small",
            merge_method="squash", delete_branch_after_merge=True,
            command_preview="gh ...",
            status="blocked — PR is not mergeable (UNKNOWN)",
            notes=[],
        )
        mock_plan.return_value = blocked_plan

        result = apply_merge("test/repo", 5, apply=True)

    assert result["mode"] == "apply_blocked"
    assert "Refusing to merge" in result.get("error", "")


# =============================================================================
# HARDENING-027B: clarify merge apply output for already-merged / blocked plans
# =============================================================================

def test_merge_apply_dry_run_already_merged_shows_no_command():
    """Already-merged PRs must not display a concrete gh pr merge command."""
    from signposter.merge import MergePlan, format_merge_apply_dry_run

    plan = MergePlan(
        pr_number=7, title="test", state="MERGED", base_branch="main",
        head_branch="work/issue-6-xxx", mergeable="UNKNOWN",
        review_decision="APPROVED", checks_status="pass",
        successful_checks=1, failing_checks=0, pending_checks=0,
        github_approved=True, approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True, pr_author="ExatronOmega",
        reviewer_gate_pass=True, reviewer_verdict="APPROVE",
        reviewer_confidence=0.95, reviewer_risk="low",
        associated_issue=6, has_auto_close_keywords=False,
        files_changed=1, additions=2, deletions=0,
        risk_level="low", size="small",
        merge_method="squash", delete_branch_after_merge=True,
        command_preview="gh pr merge 7 -R ExatronOmega/signposter --squash --delete-branch",
        status="blocked — PR is merged",
        notes=[],
    )

    output = format_merge_apply_dry_run(plan)

    # Must not show the concrete command
    assert "gh pr merge 7" not in output
    # Must show the safe "none" wording
    assert "none — merge plan is not ready (blocked — PR is merged)" in output


def test_merge_apply_dry_run_ready_still_shows_command():
    """Ready merge plans must continue to show the real gh pr merge command."""
    from signposter.merge import MergePlan, format_merge_apply_dry_run

    plan = MergePlan(
        pr_number=5, title="test", state="OPEN", base_branch="main",
        head_branch="work/issue-4-xxx", mergeable="MERGEABLE",
        review_decision="APPROVED", checks_status="pass",
        successful_checks=1, failing_checks=0, pending_checks=0,
        github_approved=True, approving_reviewers=["AlphaExatron"],
        has_non_author_approval=True, pr_author="ExatronOmega",
        reviewer_gate_pass=True, reviewer_verdict="APPROVE",
        reviewer_confidence=0.95, reviewer_risk="low",
        associated_issue=4, has_auto_close_keywords=False,
        files_changed=1, additions=8, deletions=0,
        risk_level="low", size="small",
        merge_method="squash", delete_branch_after_merge=True,
        command_preview="gh pr merge 5 -R test/repo --squash --delete-branch",
        status="ready",
        notes=[],
    )

    output = format_merge_apply_dry_run(plan)

    assert "gh pr merge 5 -R test/repo --squash --delete-branch" in output
    assert "none —" not in output


def test_merge_plan_allows_medium_scope_with_explicit_override():
    """Medium scope remains blocked by default but can be explicitly allowed."""
    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate, \
         patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks:

        mock_view.return_value = {
            "title": "work: watch-001-define-lifecycle-watch-cli-contract",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-10-watch-001-define-lifecycle-watch-cli-contract",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": "Related issue: #10",
            "files": [
                {"path": "src/signposter/cli.py"},
                {"path": "tests/test_lifecycle.py"},
            ],
            "additions": 147,
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
                "confidence": 0.87,
                "risk": "low",
            })(),
        })()
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }

        blocked = plan_merge_for_pr("test/repo", 15)
        allowed = plan_merge_for_pr(
            "test/repo",
            15,
            allow_medium_scope=True,
        )

    assert blocked.status == "blocked — PR scope is medium"
    assert blocked.size == "medium"
    assert allowed.status == "ready"
    assert allowed.size == "medium"
    assert allowed.has_non_author_approval is True
    assert allowed.has_auto_close_keywords is False


def test_merge_plan_allows_large_scope_with_explicit_override():
    """Large scope remains blocked by default but can be explicitly allowed."""
    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate, \
         patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks:

        mock_view.return_value = {
            "title": "work: h035b-e-add-bounded-orchestrator-automation",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-48-h035b-e",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": "Related issue: #48",
            "files": [{"path": "src/signposter/orchestrator.py"}],
            "additions": 650,
            "deletions": 10,
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
                "confidence": 0.9,
                "risk": "high",
            })(),
        })()
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }

        blocked = plan_merge_for_pr("test/repo", 48, allow_high_risk=True)
        allowed = plan_merge_for_pr(
            "test/repo",
            48,
            allow_large_scope=True,
            allow_high_risk=True,
        )

    assert blocked.status == "blocked — PR scope is large"
    assert allowed.status == "ready"
    assert allowed.size == "large"


def test_apply_merge_passes_allow_medium_scope_override():
    from signposter.merge import apply_merge

    with patch("signposter.merge.plan_merge_for_pr") as mock_plan:
        fake_plan = MergePlan(
            pr_number=15,
            title="test",
            state="OPEN",
            base_branch="main",
            head_branch="work/issue-10",
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
            reviewer_confidence=0.87,
            reviewer_risk="low",
            associated_issue=10,
            has_auto_close_keywords=False,
            files_changed=2,
            additions=147,
            deletions=0,
            risk_level="medium",
            size="medium",
            merge_method="squash",
            delete_branch_after_merge=True,
            command_preview="gh pr merge 15 -R test/repo --squash --delete-branch",
            status="ready",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_merge(
            "test/repo",
            15,
            apply=False,
            allow_medium_scope=True,
        )

    mock_plan.assert_called_once_with(
        "test/repo",
        15,
        allow_medium_scope=True,
        allow_large_scope=False,
        allow_medium_risk=False,
        allow_high_risk=False,
    )
    assert result["mode"] == "dry_run"
    assert result["plan"].status == "ready"


def test_merge_plan_allows_medium_reviewer_risk_with_explicit_override():
    """Medium reviewer risk remains blocked by default but can be explicitly allowed."""
    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate, \
         patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks:

        mock_view.return_value = {
            "title": "work: watch-002-add-read-only-lifecycle-watch-data-collector",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-11-watch-002-add-read-only-lifecycle-watch-data-colle",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": "Related issue: #11",
            "files": [
                {"path": "src/signposter/cli.py"},
                {"path": "src/signposter/lifecycle.py"},
                {"path": "tests/test_lifecycle.py"},
            ],
            "additions": 162,
            "deletions": 31,
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
                "confidence": 0.90,
                "risk": "medium",
            })(),
        })()
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }

        blocked = plan_merge_for_pr(
            "test/repo",
            16,
            allow_medium_scope=True,
        )
        allowed = plan_merge_for_pr(
            "test/repo",
            16,
            allow_medium_scope=True,
            allow_medium_risk=True,
        )

    assert blocked.status == "blocked — reviewer risk is medium"
    assert allowed.status == "ready"
    assert allowed.reviewer_risk == "medium"
    assert allowed.size == "medium"



def test_merge_plan_allows_high_reviewer_risk_with_explicit_override():
    from signposter.merge import plan_merge_for_pr

    with patch("signposter.merge._run_gh_pr_view") as mock_view, \
         patch("signposter.merge._fetch_pr_reviews_and_author") as mock_reviews, \
         patch("signposter.merge.evaluate_review_gate") as mock_gate, \
         patch("signposter.merge._fetch_pr_checks_for_merge") as mock_checks:

        mock_view.return_value = {
            "title": "fix: add high-risk override",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-21-h033b-add-explicit-high-risk-review-override-path",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
            "body": "Related issue: #21",
            "author": {"login": "ExatronOmega"},
            "files": [{"path": "src/signposter/review.py"}],
            "additions": 30,
            "deletions": 2,
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
                "confidence": 0.91,
                "risk": "high",
            })(),
        })()
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }

        blocked = plan_merge_for_pr("test/repo", 21)
        allowed = plan_merge_for_pr("test/repo", 21, allow_high_risk=True)

    assert blocked.status == "blocked — reviewer risk is high"
    assert allowed.status == "ready"
    assert allowed.reviewer_risk == "high"
    assert allowed.has_non_author_approval is True
    assert allowed.has_auto_close_keywords is False
    assert "High-risk override explicitly allowed by operator for planning only." in allowed.notes


def test_format_merge_plan_includes_high_risk_planning_override_note():
    plan = MergePlan(
        pr_number=21,
        title="fix: add high-risk override",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-21",
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
        reviewer_confidence=0.91,
        reviewer_risk="high",
        associated_issue=21,
        has_auto_close_keywords=False,
        files_changed=1,
        additions=30,
        deletions=2,
        risk_level="high",
        size="small",
        merge_method="squash",
        delete_branch_after_merge=True,
        command_preview="gh pr merge 21 -R test/repo --squash --delete-branch",
        status="ready",
        notes=[
            "No merge was performed.",
            "No issue was closed.",
            "No branch was deleted.",
            "High-risk override explicitly allowed by operator for planning only.",
        ],
    )

    output = format_merge_plan(plan)

    assert "High-risk override explicitly allowed by operator for planning only." in output


def test_cli_merge_plan_accepts_and_passes_allow_high_risk(monkeypatch, capsys):
    from signposter.cli import main

    fake_plan = MergePlan(
        pr_number=21,
        title="fix: add high-risk override",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-21",
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
        reviewer_confidence=0.91,
        reviewer_risk="high",
        associated_issue=21,
        has_auto_close_keywords=False,
        files_changed=1,
        additions=30,
        deletions=2,
        risk_level="high",
        size="small",
        merge_method="squash",
        delete_branch_after_merge=True,
        command_preview="gh pr merge 21 -R test/repo --squash --delete-branch",
        status="ready",
        notes=["High-risk override explicitly allowed by operator for planning only."],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "merge",
            "plan",
            "--repo",
            "test/repo",
            "--pr",
            "21",
            "--allow-high-risk",
        ],
    )

    with patch("signposter.cli.plan_merge_for_pr", return_value=fake_plan) as mock_plan, \
         pytest.raises(SystemExit) as exc:
        main()

    out = capsys.readouterr().out
    assert exc.value.code == 0
    mock_plan.assert_called_once_with(
        "test/repo",
        21,
        allow_medium_scope=False,
        allow_large_scope=False,
        allow_medium_risk=False,
        allow_high_risk=True,
    )
    assert "High-risk override explicitly allowed by operator for planning only." in out


def test_cli_merge_plan_accepts_apply_override_flags(monkeypatch, capsys):
    from signposter.cli import main

    fake_plan = MergePlan(
        pr_number=22,
        title="automation",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-22",
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
        reviewer_confidence=0.91,
        reviewer_risk="medium",
        associated_issue=22,
        has_auto_close_keywords=False,
        files_changed=7,
        additions=200,
        deletions=10,
        risk_level="medium",
        size="medium",
        merge_method="squash",
        delete_branch_after_merge=True,
        command_preview="gh pr merge 22 -R test/repo --squash --delete-branch",
        status="ready",
        notes=[
            "Medium-risk override explicitly allowed by operator for planning only.",
            "Medium-scope override explicitly allowed by operator for planning only.",
            "Large-scope override explicitly allowed by operator for planning only.",
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "merge",
            "plan",
            "--repo",
            "test/repo",
            "--pr",
            "22",
            "--allow-medium-risk",
            "--allow-medium-scope",
            "--allow-large-scope",
        ],
    )

    with patch("signposter.cli.plan_merge_for_pr", return_value=fake_plan) as mock_plan, \
         pytest.raises(SystemExit) as exc:
        main()

    out = capsys.readouterr().out
    assert exc.value.code == 0
    mock_plan.assert_called_once_with(
        "test/repo",
        22,
        allow_medium_scope=True,
        allow_large_scope=True,
        allow_medium_risk=True,
        allow_high_risk=False,
    )
    assert "Medium-risk override explicitly allowed by operator for planning only." in out


def test_apply_merge_passes_allow_high_risk_override():
    from signposter.merge import MergePlan, apply_merge

    with patch("signposter.merge.plan_merge_for_pr") as mock_plan:
        fake_plan = MergePlan(
            pr_number=21,
            title="test",
            state="OPEN",
            base_branch="main",
            head_branch="work/issue-21",
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
            reviewer_confidence=0.91,
            reviewer_risk="high",
            associated_issue=21,
            has_auto_close_keywords=False,
            files_changed=1,
            additions=30,
            deletions=2,
            risk_level="high",
            size="small",
            merge_method="squash",
            delete_branch_after_merge=True,
            command_preview="gh pr merge 21 -R test/repo --squash --delete-branch",
            status="ready",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_merge(
            "test/repo",
            21,
            apply=False,
            allow_high_risk=True,
        )

    mock_plan.assert_called_once_with(
        "test/repo",
        21,
        allow_medium_scope=False,
        allow_large_scope=False,
        allow_medium_risk=False,
        allow_high_risk=True,
    )
    assert result["mode"] == "dry_run"
    assert result["plan"].status == "ready"
