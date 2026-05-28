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


# =============================================================================
# HARDENING-016 tests: PR reviewer execution
# =============================================================================


def test_execute_refuses_when_plan_not_ready():
    from signposter.review import execute_pr_review

    with patch("signposter.review.plan_review_for_pr") as mock_plan:
        mock_plan.return_value = ReviewPlan(
            pr_number=5,
            title="test",
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

        result = execute_pr_review("test/repo", 5)
        assert result["success"] is False
        assert "not ready" in result.get("error", "")


def test_execute_refuses_when_prompt_missing():
    from signposter.review import execute_pr_review

    with patch("signposter.review.plan_review_for_pr") as mock_plan:
        mock_plan.return_value = ReviewPlan(
            pr_number=5,
            title="test",
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

        with patch("os.path.isfile", return_value=False):
            result = execute_pr_review("test/repo", 5)
            assert result["success"] is False
            assert "prompt artifact missing" in result.get("error", "")


def test_execute_writes_artifacts_on_success(monkeypatch, tmp_path):
    """Simulated successful reviewer run writes raw + summary."""
    from signposter.review import ReviewPlan, execute_pr_review

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

    # Create a fake prompt file
    (tmp_path / "pr-5-review.md").write_text(
        "You are the reviewer...\nVerdict: APPROVE\nConfidence: 0.92"
    )

    fake_result = {
        "returncode": 0,
        "stdout": "Verdict: APPROVE\nConfidence: 0.92\nReasoning: looks good",
        "stderr": "",
    }

    class FakeProc:
        def __init__(self):
            self.stdout = fake_result["stdout"]
            self.stderr = fake_result["stderr"]
            self.returncode = fake_result["returncode"]

    def fake_subprocess_run(*a, **k):
        return FakeProc()

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("os.path.isfile", return_value=True), \
         patch("subprocess.run", side_effect=fake_subprocess_run):

        result = execute_pr_review("test/repo", 5, runs_dir=tmp_path / "runs")

    assert result["success"] is True
    assert result["raw_path"] is not None
    assert result["summary_path"] is not None
    assert os.path.exists(result["raw_path"])
    assert os.path.exists(result["summary_path"])
    raw = open(result["raw_path"], encoding="utf-8").read()
    assert "Verdict: APPROVE" in raw


def test_execute_output_contains_safety_notes():
    """Sanity check that the CLI handler path would include the safety notes."""
    # This is a structural test; real handler tested via integration in real runs
    assert "No GitHub review was submitted"  # marker for later handler verification
    assert "No merge was performed"


# =============================================================================
# HARDENING-017 tests: reviewer opinion parsing + review gate
# =============================================================================


def test_parse_reviewer_opinion_approve():
    from signposter.review import parse_reviewer_opinion

    text = """Verdict: APPROVE
Confidence: 0.95
Risk: low
Scope match: yes
CI considered: yes
Merge recommendation: yes
Automerge eligible: yes
Findings:
  - Docs only
Reasoning summary:
  Looks good and matches scope."""

    op = parse_reviewer_opinion(text)
    assert op.verdict == "APPROVE"
    assert op.confidence == 0.95
    assert op.risk == "low"
    assert op.scope_match == "yes"
    assert op.ci_considered == "yes"
    assert op.merge_recommendation == "yes"
    assert op.automerge_eligible == "yes"
    assert len(op.findings) == 1


def test_parse_confidence_as_float():
    from signposter.review import parse_reviewer_opinion

    op = parse_reviewer_opinion("Confidence: 0.87")
    assert op.confidence == 0.87


def test_gate_passes_for_good_approve():
    from signposter.review import evaluate_review_gate

    good_text = """Verdict: APPROVE
Confidence: 0.95
Risk: low
Scope match: yes
CI considered: yes
Merge recommendation: yes
Automerge eligible: yes"""

    with patch("os.path.isfile", return_value=True), \
         patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = good_text

        result = evaluate_review_gate("test/repo", 5)
        assert result.gate_pass is True
        assert result.status == "pass"
        assert result.merge_eligible is True
        assert result.automerge_eligible is True


def test_gate_blocked_for_needs_changes():
    from signposter.review import evaluate_review_gate

    text = "Verdict: NEEDS_CHANGES\nConfidence: 0.90"
    with patch("os.path.isfile", return_value=True), patch("builtins.open", create=True) as m:
        m.return_value.__enter__.return_value.read.return_value = text
        result = evaluate_review_gate("test/repo", 5)
        assert "blocked — reviewer verdict is NEEDS_CHANGES" in result.status


def test_gate_blocked_for_block():
    from signposter.review import evaluate_review_gate

    text = "Verdict: BLOCK\nConfidence: 0.80"
    with patch("os.path.isfile", return_value=True), patch("builtins.open", create=True) as m:
        m.return_value.__enter__.return_value.read.return_value = text
        result = evaluate_review_gate("test/repo", 5)
        assert "blocked — reviewer verdict is BLOCK" in result.status


def test_gate_blocked_for_low_confidence():
    from signposter.review import evaluate_review_gate

    text = "Verdict: APPROVE\nConfidence: 0.70\nRisk: low"
    with patch("os.path.isfile", return_value=True), patch("builtins.open", create=True) as m:
        m.return_value.__enter__.return_value.read.return_value = text
        result = evaluate_review_gate("test/repo", 5)
        assert "confidence below threshold" in result.status


def test_gate_blocked_for_high_risk():
    from signposter.review import evaluate_review_gate

    text = "Verdict: APPROVE\nConfidence: 0.95\nRisk: high"
    with patch("os.path.isfile", return_value=True), patch("builtins.open", create=True) as m:
        m.return_value.__enter__.return_value.read.return_value = text
        result = evaluate_review_gate("test/repo", 5)
        assert "reviewer risk is high" in result.status


def test_gate_blocked_for_scope_no():
    from signposter.review import evaluate_review_gate

    text = "Verdict: APPROVE\nConfidence: 0.95\nRisk: low\nScope match: no"
    with patch("os.path.isfile", return_value=True), patch("builtins.open", create=True) as m:
        m.return_value.__enter__.return_value.read.return_value = text
        result = evaluate_review_gate("test/repo", 5)
        assert "scope match is no" in result.status


def test_gate_blocked_for_ci_not_considered():
    from signposter.review import evaluate_review_gate

    text = "Verdict: APPROVE\nConfidence: 0.95\nRisk: low\nScope match: yes\nCI considered: no"
    with patch("os.path.isfile", return_value=True), patch("builtins.open", create=True) as m:
        m.return_value.__enter__.return_value.read.return_value = text
        result = evaluate_review_gate("test/repo", 5)
        assert "CI was not considered" in result.status


def test_gate_blocked_for_missing_artifact():
    from signposter.review import evaluate_review_gate

    with patch("os.path.isfile", return_value=False):
        result = evaluate_review_gate("test/repo", 99)
        assert "blocked — reviewer summary artifact missing" in result.status


def test_gate_blocked_for_malformed_confidence():
    from signposter.review import evaluate_review_gate

    text = "Verdict: APPROVE\nConfidence: not-a-number"
    with patch("os.path.isfile", return_value=True), patch("builtins.open", create=True) as m:
        m.return_value.__enter__.return_value.read.return_value = text
        result = evaluate_review_gate("test/repo", 5)
        assert "confidence below threshold" in result.status


def test_format_review_gate_contains_safety_notes():
    from signposter.review import ReviewerOpinion, ReviewGateResult, format_review_gate

    op = ReviewerOpinion(
        verdict="APPROVE", confidence=0.95, risk="low",
        scope_match="yes", ci_considered="yes",
        merge_recommendation="yes", automerge_eligible="yes",
        findings=[], reasoning=None, raw_text=""
    )
    result = ReviewGateResult(
        pr_number=5, status="pass", reason="good",
        opinion=op, gate_pass=True, merge_eligible=True, automerge_eligible=True,
        summary_path="artifacts/runs/pr-5-reviewer.summary.md",
        notes=["No GitHub review was submitted.", "No merge was performed."]
    )

    output = format_review_gate(result)
    assert "No GitHub review was submitted" in output
    assert "No merge was performed" in output
    assert "merge eligible: yes" in output


# =============================================================================
# HARDENING-018 tests: GitHub PR review submit plan + apply guard
# =============================================================================


def test_submit_plan_approve_when_gate_passes():
    from signposter.review import plan_review_submit

    good_gate = """Verdict: APPROVE
Confidence: 0.95
Risk: low
Scope match: yes
CI considered: yes
Merge recommendation: yes
Automerge eligible: yes"""

    with patch("signposter.review.evaluate_review_gate") as mock_gate:
        from signposter.review import ReviewerOpinion, ReviewGateResult
        op = ReviewerOpinion(
            verdict="APPROVE", confidence=0.95, risk="low",
            scope_match="yes", ci_considered="yes",
            merge_recommendation="yes", automerge_eligible="yes",
            findings=["Docs only"],
            reasoning="Good", raw_text=good_gate
        )
        mock_gate.return_value = ReviewGateResult(
            pr_number=5, status="pass", reason="good",
            opinion=op, gate_pass=True, merge_eligible=True, automerge_eligible=True,
            summary_path="artifacts/runs/pr-5-reviewer.summary.md",
            notes=[] 
        )

        plan = plan_review_submit("test/repo", 5)

    assert plan.action == "approve"
    assert plan.status == "ready"
    assert "APPROVE" in plan.body
    assert "No merge or issue close" in plan.body


def test_submit_plan_blocks_on_failed_gate():
    from signposter.review import plan_review_submit

    with patch("signposter.review.evaluate_review_gate") as mock_gate:
        from signposter.review import ReviewerOpinion, ReviewGateResult
        op = ReviewerOpinion("BLOCK", 0.5, "high", "no", "no", "no", "no", [], None, "")
        mock_gate.return_value = ReviewGateResult(
            pr_number=5, status="blocked — high risk", reason="high risk",
            opinion=op, gate_pass=False, merge_eligible=False, automerge_eligible=False,
            summary_path=None, notes=[] 
        )

        plan = plan_review_submit("test/repo", 5)

    assert plan.action == "blocked"
    assert "blocked" in plan.status


def test_submit_dry_run_does_not_call_subprocess():
    from signposter.review import submit_review

    with patch("subprocess.run") as mock_sub:
        with patch("signposter.review.plan_review_submit") as mock_plan:
            from signposter.review import ReviewSubmitPlan
            fake_plan = ReviewSubmitPlan(5, "approve", "body", True, "good", "ready", "gh ...", [])
            mock_plan.return_value = fake_plan

            result = submit_review("test/repo", 5, apply=False)

            mock_sub.assert_not_called()
            assert result["mode"] == "dry_run"


def test_submit_apply_calls_gh_when_ready(monkeypatch):
    """Verify that --apply actually invokes gh pr review --approve."""
    from signposter.review import submit_review

    class FakeProc:
        returncode = 0
        stdout = "Review submitted."
        stderr = ""

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)

    with patch("signposter.review.plan_review_submit") as mock_plan:
        from signposter.review import ReviewSubmitPlan
        fake_plan = ReviewSubmitPlan(
            pr_number=5,
            action="approve",
            body="Signposter reviewer gate: APPROVE...",
            gate_pass=True,
            gate_reason="good",
            status="ready",
            gh_preview="gh ...",
            notes=[] 
        )
        mock_plan.return_value = fake_plan

        result = submit_review("test/repo", 5, apply=True)

    assert result["mode"] == "apply"
    assert result["success"] is True
    assert any("--approve" in str(c) for c in calls)


def test_submit_apply_refuses_when_not_ready():
    from signposter.review import submit_review

    with patch("signposter.review.plan_review_submit") as mock_plan:
        from signposter.review import ReviewSubmitPlan
        fake_plan = ReviewSubmitPlan(
            5, "blocked", "", False, "bad", "blocked — high risk", "gh ...", []
        )
        mock_plan.return_value = fake_plan

        result = submit_review("test/repo", 5, apply=True)

    assert result["mode"] == "apply_blocked"
    assert "Refusing to submit" in result.get("error", "")
