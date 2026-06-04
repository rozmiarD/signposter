"""Cross-surface tests for GitHub/local mutation boundaries."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from signposter.artifact import build_worker_summary
from signposter.claim import build_claim_plan, perform_claim_mutation
from signposter.cleanup import CleanupPlan, apply_cleanup
from signposter.dispatch import DispatchDecision
from signposter.integration import IntegrationPlan, apply_integration
from signposter.merge import MergePlan, apply_merge
from signposter.report import report_main
from signposter.review import ReviewSubmitPlan, submit_review
from signposter.scan import LabeledItem


class Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def _issue(number: int = 10) -> LabeledItem:
    return LabeledItem(
        number=number,
        title=f"Task {number}",
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=["state:ready", "phase:build", "role:worker", "risk:low"],
        item_type="issue",
    )


def _dispatch(item: LabeledItem) -> DispatchDecision:
    return DispatchDecision(
        item=item,
        phase="build",
        state="ready",
        role="worker",
        risk="low",
        area="tests",
        proposed_route="worker",
        proposed_gate="ci",
        reason="test",
    )


def _merge_plan(**overrides) -> MergePlan:
    base = dict(
        pr_number=7,
        title="test",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-10-test",
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
        associated_issue=10,
        has_auto_close_keywords=False,
        files_changed=1,
        additions=5,
        deletions=0,
        risk_level="low",
        size="small",
        merge_method="squash",
        delete_branch_after_merge=True,
        command_preview="gh pr merge 7 -R test/repo --squash --delete-branch",
        status="ready",
        notes=[],
    )
    base.update(overrides)
    return MergePlan(**base)


def _integration_plan(**overrides) -> IntegrationPlan:
    base = dict(
        pr_number=7,
        pr_title="test",
        pr_state="MERGED",
        merge_commit="abc123",
        base_branch="main",
        head_branch="work/issue-10-test",
        associated_issue=10,
        issue_state="OPEN",
        current_workflow_state="state:done",
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        main_ci_status="pass",
        status="ready",
        notes=[],
    )
    base.update(overrides)
    return IntegrationPlan(**base)


def _cleanup_plan(**overrides) -> CleanupPlan:
    base = dict(
        pr_number=7,
        pr_state="MERGED",
        head_branch="work/issue-10-test",
        associated_issue=10,
        issue_state="CLOSED",
        has_state_merged_label=True,
        expected_worktree_path="../signposter-work/10",
        worktree_exists=True,
        local_branch="work/issue-10-test",
        local_branch_exists=True,
        status="ready",
        notes=[],
    )
    base.update(overrides)
    return CleanupPlan(**base)


def test_claim_apply_stops_before_comment_when_label_edit_fails():
    plan = build_claim_plan(_dispatch(_issue(10)))
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        raise subprocess.CalledProcessError(1, cmd, stderr="label edit rejected")

    with patch("signposter.claim.subprocess.run", side_effect=fake_run):
        with pytest.raises(subprocess.CalledProcessError):
            perform_claim_mutation(plan, "test/repo", dry_run=False)

    assert len(calls) == 1
    assert calls[0][:3] == ["gh", "issue", "edit"]


def test_report_apply_posts_exactly_one_issue_comment(tmp_path: Path):
    summary = tmp_path / "issue-10-worker.summary.md"
    raw = tmp_path / "issue-10-worker.raw.txt"
    summary.write_text(
        build_worker_summary(
            repo="test/repo",
            issue=10,
            changed_files=["src/signposter/report.py"],
            implemented_behavior=["Report mutation boundary summary is schema-compatible."],
            targeted_validation=["python -m pytest tests/test_mutation_boundaries.py -q"],
        ),
        encoding="utf-8",
    )
    raw.write_text("manual raw evidence\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    with patch("signposter.report.subprocess.run", side_effect=fake_run):
        exit_code = report_main("test/repo", 10, summary, apply=True)

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][:3] == ["gh", "issue", "comment"]
    assert "--body" in calls[0]


def test_review_submit_apply_uses_single_gh_review_command():
    plan = ReviewSubmitPlan(
        pr_number=7,
        action="approve",
        body="Signposter reviewer gate: APPROVE",
        gate_pass=True,
        gate_reason="pass",
        status="ready",
        gh_preview="gh pr review 7 -R test/repo --approve --body-file ...",
        notes=[],
    )
    calls: list[list[str]] = []

    def fake_run_gh_with_token(cmd, token):
        calls.append(cmd)
        return Proc()

    with (
        patch("signposter.review.plan_review_submit", return_value=plan),
        patch("signposter.review._get_reviewer_token", return_value="token"),
        patch("signposter.review._run_gh_with_token", side_effect=fake_run_gh_with_token),
    ):
        result = submit_review("test/repo", 7, apply=True)

    assert result["success"] is True
    assert len(calls) == 1
    assert calls[0][:3] == ["gh", "pr", "review"]
    assert "--approve" in calls[0]
    assert "--body-file" in calls[0]


def test_merge_apply_uses_single_gh_merge_command():
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    with (
        patch("signposter.merge.plan_merge_for_pr", return_value=_merge_plan()),
        patch("signposter.merge.subprocess.run", side_effect=fake_run),
    ):
        result = apply_merge("test/repo", 7, apply=True)

    assert result["success"] is True
    assert calls == [
        ["gh", "pr", "merge", "7", "-R", "test/repo", "--squash", "--delete-branch"]
    ]


def test_integration_apply_runs_label_comment_close_in_order():
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    with (
        patch("signposter.integration.plan_integration_for_pr", return_value=_integration_plan()),
        patch("signposter.integration.check_labels") as mock_check,
        patch("signposter.integration.subprocess.run", side_effect=fake_run),
    ):
        mock_check.return_value.missing = []
        mock_check.return_value.error = None
        result = apply_integration("test/repo", 7, apply=True)

    assert result["success"] is True
    assert [cmd[:3] for cmd in calls] == [
        ["gh", "issue", "edit"],
        ["gh", "issue", "comment"],
        ["gh", "issue", "close"],
    ]


def test_cleanup_apply_runs_worktree_then_branch_without_github_calls():
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    with (
        patch("signposter.cleanup.plan_cleanup_for_pr", return_value=_cleanup_plan()),
        patch("signposter.cleanup._local_branch_exists", return_value=True),
        patch("signposter.cleanup.subprocess.run", side_effect=fake_run),
    ):
        result = apply_cleanup("test/repo", 7, apply=True)

    assert result["success"] is True
    assert calls == [
        ["git", "worktree", "remove", "--force", "../signposter-work/10"],
        ["git", "branch", "-D", "work/issue-10-test"],
    ]
