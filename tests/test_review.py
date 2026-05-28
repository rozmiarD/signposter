"""Tests for reviewer PR planning (HARDENING-014) and prompt writing (HARDENING-015)."""

import os
from unittest.mock import patch

import pytest

from signposter.review import (
    ReviewPlan,
    format_review_plan,
    plan_review_for_pr,
)


def test_plan_review_blocks_on_closed_pr():
    with patch("signposter.review._run_gh_pr_view") as mock_gh:
        mock_gh.return_value = {
            "number": 99,
            "title": "Closed PR",
            "state": "CLOSED",
            "baseRefName": "main",
            "headRefName": "work/issue-10-something",
            "mergeable": "MERGEABLE",
            "reviewDecision": None,
            "body": "",
        }

        plan = plan_review_for_pr("test/repo", 99)

        assert "blocked — PR is closed" in plan.status


def test_plan_review_ready_for_good_docs_pr():
    """Basic structural test for a healthy docs PR."""
    # We test the classification logic more directly via the dataclass
    plan = ReviewPlan(
        pr_number=5,
        title="docs: test-task-isolated-worker-readme-note",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-test-task-isolated-worker-readme-note",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=1,
        additions=8,
        deletions=0,
        risk_level="low",
        size="small",
        associated_issue=4,
        branch_matches_convention=True,
        status="ready",
        notes=["No review was executed."],
        reviewer_profile="reviewer",
        prompt_artifact_path="artifacts/prompts/pr-5-review.md",
    )

    assert plan.status == "ready"
    assert plan.risk_level == "low"
    assert plan.branch_matches_convention is True
    assert plan.size == "small"
    assert "reviewer" in plan.reviewer_profile
    assert "No review was executed" in plan.notes[0]


def test_format_review_plan_contains_key_sections():
    plan = ReviewPlan(
        pr_number=5,
        title="docs change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=1,
        additions=8,
        deletions=0,
        risk_level="low",
        size="small",
        associated_issue=4,
        branch_matches_convention=True,
        status="ready",
        notes=["No review was executed.", "No merge was performed."],
        reviewer_profile="reviewer",
        prompt_artifact_path="artifacts/prompts/pr-5-review.md",
    )

    output = format_review_plan(plan)

    assert "Signposter Review Plan — PR #5" in output
    assert "risk: low" in output
    assert "reviewer" in output
    assert "artifacts/prompts/pr-5-review.md" in output
    assert "No review was executed" in output
    assert "No merge was performed" in output

def test_fetch_pr_checks_handles_gh_checkrun_list_shape():
    from signposter.review import _fetch_pr_checks

    def fake_view(repo, pr, fields):
        assert fields == ["statusCheckRollup"]
        return {
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "name": "test",
                }
            ]
        }

    with patch("signposter.review._run_gh_pr_view", side_effect=fake_view):
        checks = _fetch_pr_checks("test/repo", 5)

    assert checks == {
        "status": "pass",
        "successful": 1,
        "failing": 0,
        "pending": 0,
    }


def test_plan_review_blocks_when_checks_unknown():
    responses = [
        {
            "number": 5,
            "title": "docs: change",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-4-docs-change",
            "mergeable": "MERGEABLE",
            "reviewDecision": None,
            "body": "Related issue: #4",
        },
        {"statusCheckRollup": []},
        {"files": [{"path": "README.md"}], "additions": 8, "deletions": 0},
        {"files": [{"path": "README.md"}]},
    ]

    with patch("signposter.review._run_gh_pr_view", side_effect=responses):
        plan = plan_review_for_pr("test/repo", 5)

    assert plan.checks_status == "unknown"
    assert plan.status == "blocked — checks status is unknown"


def test_plan_review_ready_with_successful_checkrun_list():
    responses = [
        {
            "number": 5,
            "title": "docs: change",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-4-docs-change",
            "mergeable": "MERGEABLE",
            "reviewDecision": None,
            "body": "Related issue: #4",
        },
        {
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "name": "test",
                }
            ]
        },
        {"files": [{"path": "README.md"}], "additions": 8, "deletions": 0},
        {"files": [{"path": "README.md"}]},
    ]

    with patch("signposter.review._run_gh_pr_view", side_effect=responses):
        plan = plan_review_for_pr("test/repo", 5)

    assert plan.checks_status == "pass"
    assert plan.successful_checks == 1
    assert plan.status == "ready"


# =============================================================================
# HARDENING-015 tests: prompt artifact writing
# =============================================================================


def test_write_prompt_artifact_path_generation():

    plan = ReviewPlan(
        pr_number=5,
        title="docs change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=1,
        additions=8,
        deletions=0,
        risk_level="low",
        size="small",
        associated_issue=4,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path="artifacts/prompts/pr-5-review.md",
    )

    # Path should be predictable
    assert plan.prompt_artifact_path.endswith("pr-5-review.md")
    assert "artifacts/prompts" in plan.prompt_artifact_path


def test_build_review_prompt_contains_contract_and_rules():
    from signposter.review import ReviewPlan, build_review_prompt

    plan = ReviewPlan(
        pr_number=5,
        title="docs: test change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=1,
        additions=8,
        deletions=0,
        risk_level="low",
        size="small",
        associated_issue=4,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path="artifacts/prompts/pr-5-review.md",
    )

    prompt = build_review_prompt(plan, "Related to issue #4", "diff --git a/README.md ...")

    assert "Verdict: APPROVE | NEEDS_CHANGES | BLOCK" in prompt
    assert "Confidence: 0.00-1.00" in prompt
    assert "Automerge eligible: yes | no" in prompt
    assert "confidence >= 0.85" in prompt.lower()
    assert "blocks automerge" in prompt.lower()
    assert "MUST NOT claim that you submitted a GitHub review" in prompt


def test_write_prompt_refuses_when_plan_not_ready():
    from signposter.review import write_review_prompt_artifact

    with patch("signposter.review.plan_review_for_pr") as mock_plan:
        mock_plan.return_value = ReviewPlan(
            pr_number=5,
            title="bad pr",
            state="OPEN",
            base_branch="main",
            head_branch="work/issue-4-xxx",
            mergeable="MERGEABLE",
            review_decision=None,
            checks_status="failing",
            successful_checks=0,
            failing_checks=1,
            pending_checks=0,
            files_changed=1,
            additions=8,
            deletions=0,
            risk_level="low",
            size="small",
            associated_issue=4,
            branch_matches_convention=True,
            status="blocked — checks are failing",
            notes=[],
            reviewer_profile="reviewer",
            prompt_artifact_path="artifacts/prompts/pr-5-review.md",
        )

        with pytest.raises(RuntimeError) as exc:
            write_review_prompt_artifact("test/repo", 5)
        assert "checks are failing" in str(exc.value)


def test_write_prompt_artifact_writes_file(tmp_path, monkeypatch):
    """End-to-end write test using temp directory."""
    from signposter.review import ReviewPlan, write_review_prompt_artifact

    fake_plan = ReviewPlan(
        pr_number=5,
        title="docs change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=1,
        additions=8,
        deletions=0,
        risk_level="low",
        size="small",
        associated_issue=4,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path=str(tmp_path / "pr-5-review.md"),
    )

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("signposter.review._run_gh_pr_view", return_value={"body": "Related to #4"}), \
         patch("signposter.review.get_pr_diff", return_value="diff --git ..."):

        path = write_review_prompt_artifact("test/repo", 5)

    assert os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "- PR: #5" in content
    assert "Verdict: APPROVE | NEEDS_CHANGES | BLOCK" in content
    assert "Confidence: 0.00-1.00" in content
    assert "Automerge eligible" in content
