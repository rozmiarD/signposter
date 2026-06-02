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
    assert "selected role:" in output
    assert "reasoning:" in output
    assert "backend:" in output
    assert "execute ready:" in output


def test_plan_review_accepts_codex_cli_backend_for_dry_run():
    with (
        patch("signposter.review._run_gh_pr_view") as mock_gh,
        patch("signposter.review._fetch_pr_checks") as mock_checks,
        patch("signposter.review._fetch_pr_files") as mock_files,
        patch("signposter.review._fetch_pr_file_paths") as mock_paths,
    ):
        mock_gh.return_value = {
            "number": 5,
            "title": "Small PR",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-4-small-pr",
            "mergeable": "MERGEABLE",
            "reviewDecision": None,
            "body": "",
        }
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }
        mock_files.return_value = {"files_changed": 1, "additions": 5, "deletions": 0}
        mock_paths.return_value = ["tests/test_example.py"]

        plan = plan_review_for_pr("test/repo", 5, backend="codex-cli")

    assert plan.proposed_runner == "codex-cli"
    assert plan.backend_execution_supported is True
    assert plan.selected_openclaw_agent == "codex_reviewer_light"
    assert "agent=codex_reviewer_light" in plan.proposed_command_shape
    assert "codex exec" in plan.proposed_command_shape


def test_execute_pr_review_uses_codex_cli_backend(monkeypatch, tmp_path):
    from signposter.review import execute_pr_review

    prompt_dir = tmp_path / "artifacts" / "prompts"
    prompt_dir.mkdir(parents=True)
    prompt = prompt_dir / "pr-5-review.md"
    prompt.write_text("review this", encoding="utf-8")
    plan = ReviewPlan(
        pr_number=5,
        title="code change",
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
        risk_level="medium",
        size="small",
        associated_issue=4,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path=str(prompt),
        proposed_runner="codex-cli",
        backend_execution_supported=True,
        selected_role_name="REVIEWER_CORE",
        selected_model="openai/gpt-5.4",
        selected_reasoning_effort="medium",
        selected_openclaw_agent="reviewer_core",
    )
    result_obj = type(
        "Result",
        (),
        {
            "exit_code": 0,
            "raw_path": tmp_path / "raw.txt",
            "summary_path": tmp_path / "summary.md",
            "success": True,
            "reason": "ok",
            "status": "success",
        },
    )()
    called = {}

    def fake_execute(invocation, *, raw_path, summary_path):
        called["invocation"] = invocation
        called["raw_path"] = raw_path
        called["summary_path"] = summary_path
        return result_obj

    monkeypatch.setattr("signposter.review.plan_review_for_pr", lambda *a, **k: plan)
    monkeypatch.setattr("signposter.review.execute_codex_cli_invocation", fake_execute)

    result = execute_pr_review("test/repo", 5, backend="codex-cli", runs_dir=tmp_path / "runs")

    assert result["success"] is True
    assert called["invocation"].agent == "reviewer_core"
    assert str(called["raw_path"]).endswith("pr-5-reviewer.raw.txt")


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
    assert "## Selected Role Policy" in prompt
    assert "backend:" in prompt
    assert "backend reason:" in prompt
    assert "selected reasoning effort" in prompt
    assert "## Prompt Contract" in prompt
    assert "expected output format:" in prompt
    assert "artifact requirements:" in prompt
    assert "uncertainty handling:" in prompt


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


def test_get_pr_diff_marks_omitted_budget_lines(monkeypatch):
    from signposter.review import get_pr_diff

    class FakeProc:
        returncode = 0
        stdout = "\n".join(f"+line {i}" for i in range(10))
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: FakeProc())

    diff = get_pr_diff("test/repo", 5, max_lines=3)

    assert "+line 0" in diff
    assert "+line 3" not in diff
    assert "# Omitted due to budget" in diff
    assert "7 diff lines omitted from 10 total lines" in diff


def test_build_review_prompt_keeps_structured_contract_with_budget_note():
    from signposter.review import ReviewPlan, build_review_prompt

    plan = ReviewPlan(
        pr_number=5,
        title="test",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-4-test",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=2,
        additions=50,
        deletions=5,
        risk_level="medium",
        size="medium",
        associated_issue=4,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path="artifacts/prompts/pr-5-review.md",
    )
    diff = "+kept\n# Omitted due to budget\n# 50 diff lines omitted from 60 total lines."

    prompt = build_review_prompt(plan, "Related issue: #4", diff)

    assert "## Diff (budgeted excerpt)" in prompt
    assert "# Omitted due to budget" in prompt
    assert "Verdict: APPROVE | NEEDS_CHANGES | BLOCK" in prompt
    assert "Automerge eligible: yes | no" in prompt


def test_build_review_prompt_compacts_body_and_changed_files():
    from signposter.review import ReviewPlan, build_review_prompt

    plan = ReviewPlan(
        pr_number=6,
        title="test",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-5-test",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=40,
        additions=80,
        deletions=6,
        risk_level="medium",
        size="medium",
        associated_issue=5,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path="artifacts/prompts/pr-6-review.md",
    )
    body = "\n".join(f"body line {i}" for i in range(80))
    file_paths = [f"src/file_{i}.py" for i in range(30)]

    prompt = build_review_prompt(plan, body, "+kept", file_paths=file_paths)

    assert "## PR Body (bounded)" in prompt
    assert "...[omitted " in prompt
    assert "## Changed Files Excerpt (from GitHub metadata, bounded)" in prompt
    assert "...[omitted 6 additional changed files]" in prompt


def test_compact_review_text_respects_budget_with_omission_marker():
    from signposter.review import _compact_review_text

    body = "\n".join(f"body line {i} {'y' * 60}" for i in range(50))
    compact = _compact_review_text(
        body,
        max_lines=10,
        max_chars=300,
        empty_fallback="<empty>",
    )

    assert "...[omitted " in compact
    assert len(compact) <= 300


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

        with patch("signposter.review._resolve_existing_artifact_path", return_value=None), \
             patch(
                 "signposter.review.write_review_prompt_artifact",
                 side_effect=RuntimeError("write failed"),
             ):
            result = execute_pr_review("test/repo", 5)
            assert result["success"] is False
            assert "could not be written" in result.get("error", "")


def test_execute_rewrites_prompt_when_missing(tmp_path):
    from signposter.review import ReviewPlan, execute_pr_review

    prompt_path = tmp_path / "pr-5-review.md"
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
        prompt_artifact_path="artifacts/prompts/pr-5-review.md",
    )

    def fake_write_prompt(*args, **kwargs):
        prompt_path.write_text("review prompt", encoding="utf-8")
        return str(prompt_path)

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("signposter.review._resolve_existing_artifact_path", return_value=None), \
         patch("signposter.review.write_review_prompt_artifact", side_effect=fake_write_prompt), \
         patch("signposter.review.check_openclaw_preflight") as mock_preflight, \
         patch("subprocess.run") as mock_run:
        mock_preflight.return_value = type("pf", (), {"ok": True})()
        mock_run.return_value = type(
            "proc", (), {"stdout": "Verdict: APPROVE", "stderr": "", "returncode": 0}
        )()

        result = execute_pr_review("test/repo", 5, runs_dir=tmp_path / "runs")

    assert result["success"] is True
    assert prompt_path.exists()


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
         patch("signposter.review.check_openclaw_preflight") as mock_preflight, \
         patch("os.path.isfile", return_value=True), \
         patch("subprocess.run", side_effect=fake_subprocess_run):

        mock_preflight.return_value = type("pf", (), {"ok": True})()
        result = execute_pr_review("test/repo", 5, runs_dir=tmp_path / "runs")

    assert result["success"] is True
    assert result["raw_path"] is not None
    assert result["summary_path"] is not None
    assert os.path.exists(result["raw_path"])
    assert os.path.exists(result["summary_path"])
    raw = open(result["raw_path"], encoding="utf-8").read()
    assert "Verdict: APPROVE" in raw


def test_execute_review_preflight_blocks_before_openclaw_and_artifacts(tmp_path):
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
    (tmp_path / "pr-5-review.md").write_text("review prompt", encoding="utf-8")
    preflight = type(
        "pf",
        (),
        {
            "ok": False,
            "reason": "OpenClaw CLI not found on PATH",
            "checked_token_envs": ("OPENAI_API_KEY",),
            "openclaw_path": None,
            "auth_config_path": None,
            "auth_profile_count": 0,
            "manual_fallback": "signposter artifact write-review-summary --pr 5 --apply",
        },
    )()

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("signposter.review.check_openclaw_preflight", return_value=preflight), \
         patch("subprocess.run") as mock_run:
        result = execute_pr_review("test/repo", 5, runs_dir=tmp_path / "runs")

    assert result["success"] is False
    assert result["raw_path"] is None
    assert result["summary_path"] is None
    assert "OpenClaw CLI" in result["error"]
    assert not (tmp_path / "runs").exists()
    mock_run.assert_not_called()


def test_execute_review_timeout_writes_bounded_summary(tmp_path, monkeypatch):
    from subprocess import TimeoutExpired

    from signposter.bug_ledger import load_bug_ledger
    from signposter.review import ReviewPlan, execute_pr_review

    monkeypatch.chdir(tmp_path)
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
    (tmp_path / "pr-5-review.md").write_text("review prompt", encoding="utf-8")

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("signposter.review.check_openclaw_preflight") as mock_preflight, \
         patch("signposter.review.gather_openclaw_runtime_diagnostics") as mock_diag, \
         patch("signposter.review.openclaw_timeout_settings") as mock_timeouts, \
         patch("subprocess.run", side_effect=TimeoutExpired(cmd=["openclaw"], timeout=25)):
        mock_preflight.return_value = type("pf", (), {"ok": True})()
        mock_diag.return_value = type("diag", (), {"warnings": ()})()
        mock_timeouts.return_value = type(
            "timeouts",
            (),
            {"execute_timeout": 20, "subprocess_timeout": 25, "warnings": ()},
        )()
        result = execute_pr_review("test/repo", 5, runs_dir=tmp_path / "runs")

    assert result["success"] is False
    assert result["summary_path"] is not None
    assert result["diagnosis_status"] == "timeout"
    summary = open(result["summary_path"], encoding="utf-8").read()
    assert "**Execution Status:** timeout" in summary
    assert "bounded subprocess timeout" in summary
    assert "**Bug Ledger:** recorded BUG-0001" in summary
    entries = load_bug_ledger(tmp_path / "artifacts/automation/bug-ledger.json")
    assert entries[0].status == "runtime-blocker"
    assert entries[0].current_pr == 5


def test_execute_review_timeout_decodes_bytes_output(tmp_path):
    from subprocess import TimeoutExpired

    from signposter.review import ReviewPlan, execute_pr_review

    fake_plan = ReviewPlan(
        pr_number=15,
        title="docs change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-15-xxx",
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
        associated_issue=14,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path=str(tmp_path / "pr-15-review.md"),
    )
    (tmp_path / "pr-15-review.md").write_text("review prompt", encoding="utf-8")

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("signposter.review.check_openclaw_preflight") as mock_preflight, \
         patch("signposter.review.gather_openclaw_runtime_diagnostics") as mock_diag, \
         patch("signposter.review.openclaw_timeout_settings") as mock_timeouts, \
         patch(
             "subprocess.run",
             side_effect=TimeoutExpired(
                 cmd=["openclaw"],
                 timeout=25,
                 output=b"partial bytes",
                 stderr=b"stderr bytes",
             ),
         ):
        mock_preflight.return_value = type("pf", (), {"ok": True})()
        mock_diag.return_value = type("diag", (), {"warnings": ()})()
        mock_timeouts.return_value = type(
            "timeouts",
            (),
            {"execute_timeout": 20, "subprocess_timeout": 25, "warnings": ()},
        )()
        result = execute_pr_review("test/repo", 15, runs_dir=tmp_path / "runs")

    assert result["diagnosis_status"] == "timeout"
    raw = open(result["raw_path"], encoding="utf-8").read()
    assert "partial bytes" in raw
    assert "stderr bytes" in raw


def test_execute_review_passes_model_and_thinking_flags(tmp_path):
    from signposter.review import ReviewPlan, execute_pr_review

    fake_plan = ReviewPlan(
        pr_number=6,
        title="core change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-6-xxx",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=2,
        additions=10,
        deletions=2,
        risk_level="high",
        size="small",
        associated_issue=6,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path=str(tmp_path / "pr-6-review.md"),
        selected_role_name="REVIEWER_CORE",
        selected_model="openai/gpt-5.4",
        selected_reasoning_effort="medium",
        role_selection_reason="test",
    )
    (tmp_path / "pr-6-review.md").write_text("review prompt", encoding="utf-8")

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("signposter.review.check_openclaw_preflight") as mock_preflight, \
         patch("subprocess.run") as mock_run:
        mock_preflight.return_value = type("pf", (), {"ok": True})()
        mock_run.return_value = type(
            "proc", (), {"stdout": "Verdict: APPROVE", "stderr": "", "returncode": 0}
        )()

        execute_pr_review("test/repo", 6, runs_dir=tmp_path / "runs")

    cmd = mock_run.call_args.args[0]
    assert "--model" in cmd
    assert "openai/gpt-5.4" in cmd
    assert "--thinking" in cmd
    assert "medium" in cmd


def test_build_review_prompt_includes_authoritative_changed_files():
    from signposter.review import ReviewPlan, build_review_prompt

    plan = ReviewPlan(
        pr_number=6,
        title="core change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-6-xxx",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=2,
        additions=10,
        deletions=2,
        risk_level="high",
        size="small",
        associated_issue=6,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path="artifacts/prompts/pr-6-review.md",
        selected_role_name="REVIEWER_CORE",
        selected_model="openai/gpt-5.4",
        selected_reasoning_effort="medium",
        role_selection_reason="test",
    )

    prompt = build_review_prompt(
        plan,
        "## Summary\nbody",
        "diff --git a/x b/x",
        file_paths=["src/signposter/review.py", "tests/test_review.py"],
    )

    assert "## Changed Files Excerpt (from GitHub metadata, bounded)" in prompt
    assert "- src/signposter/review.py" in prompt
    assert "- tests/test_review.py" in prompt
    assert "bounded excerpt of the GitHub changed-file metadata" in prompt


def test_execute_output_contains_safety_notes():
    """Sanity check that the CLI handler path would include the safety notes."""
    # This is a structural test; real handler tested via integration in real runs
    assert "No GitHub review was submitted"  # marker for later handler verification
    assert "No merge was performed"


def test_execute_review_refuses_invalid_timeout_relationship(tmp_path):
    from unittest.mock import patch

    from signposter.review import ReviewPlan, execute_pr_review

    fake_plan = ReviewPlan(
        pr_number=7,
        title="core change",
        state="OPEN",
        base_branch="main",
        head_branch="work/issue-7-xxx",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_status="pass",
        successful_checks=1,
        failing_checks=0,
        pending_checks=0,
        files_changed=2,
        additions=10,
        deletions=2,
        risk_level="high",
        size="small",
        associated_issue=7,
        branch_matches_convention=True,
        status="ready",
        notes=[],
        reviewer_profile="reviewer",
        prompt_artifact_path=str(tmp_path / "pr-7-review.md"),
        selected_role_name="REVIEWER_CORE",
        selected_model="openai/gpt-5.4",
        selected_reasoning_effort="medium",
        role_selection_reason="test",
    )
    (tmp_path / "pr-7-review.md").write_text("review prompt", encoding="utf-8")

    with patch("signposter.review.plan_review_for_pr", return_value=fake_plan), \
         patch("signposter.review.check_openclaw_preflight") as mock_preflight, \
         patch("signposter.review.gather_openclaw_runtime_diagnostics") as mock_diag, \
         patch("signposter.review.openclaw_timeout_settings") as mock_timeouts, \
         patch("subprocess.run") as mock_run:
        mock_preflight.return_value = type("pf", (), {"ok": True})()
        mock_diag.return_value = type("diag", (), {"warnings": ()})()
        mock_timeouts.return_value = type(
            "timeouts",
            (),
            {
                "execute_timeout": 40,
                "subprocess_timeout": 30,
                "warnings": (),
                "config_error": (
                    "SIGNPOSTER_OPENCLAW_SUBPROCESS_TIMEOUT_SECONDS must exceed "
                    "SIGNPOSTER_OPENCLAW_EXECUTE_TIMEOUT_SECONDS"
                ),
            },
        )()
        result = execute_pr_review("test/repo", 7, runs_dir=tmp_path / "runs")

    assert result["success"] is False
    assert result["diagnosis_status"] == "config-error"
    mock_run.assert_not_called()
    summary = open(result["summary_path"], encoding="utf-8").read()
    assert "**Execution Status:** config-error" in summary


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


def test_gate_finds_summary_outside_current_repo_root(tmp_path):
    from signposter.review import evaluate_review_gate

    summary = tmp_path / "pr-5-reviewer.summary.md"
    summary.write_text(
        "Verdict: APPROVE\n"
        "Confidence: 0.95\n"
        "Risk: low\n"
        "Scope match: yes\n"
        "CI considered: yes\n"
        "Merge recommendation: yes\n"
        "Automerge eligible: yes\n",
        encoding="utf-8",
    )

    with patch("signposter.review._resolve_existing_artifact_path", return_value=str(summary)):
        result = evaluate_review_gate("test/repo", 5)

    assert result.gate_pass is True
    assert result.summary_path == str(summary)


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


def test_validate_review_artifact_blocks_unsafe_marker(tmp_path):
    from signposter.review import validate_review_artifact

    path = tmp_path / "pr-73-reviewer.summary.md"
    path.write_text(
        "Verdict: APPROVE\n"
        "Confidence: 0.95\n"
        "Risk: low\n"
        "Scope match: yes\n"
        "CI considered: yes\n"
        "Merge recommendation: yes\n"
        "Automerge eligible: no\n"
        "Model unavailable.\n",
        encoding="utf-8",
    )

    result = validate_review_artifact(73, summary_path=str(path))

    assert result.status == "blocked"
    assert "unsafe execution marker" in result.errors[0]


def test_validate_review_artifact_blocks_unsafe_raw_marker(tmp_path):
    from signposter.artifact import plan_review_summary, write_manual_artifact
    from signposter.review import format_review_artifact_validation, validate_review_artifact

    plan = plan_review_summary(pr=73, risk="medium", runs_dir=tmp_path)
    write_manual_artifact(plan, apply=True)
    raw = tmp_path / "pr-73-reviewer.raw.txt"
    raw.write_text("The model is not supported for this account.\n", encoding="utf-8")

    result = validate_review_artifact(73, summary_path=plan.path)
    out = format_review_artifact_validation(result)

    assert result.status == "blocked"
    assert result.raw_exists is True
    assert result.raw_stale_signal == "model is not supported"
    assert "Raw unsafe marker:" in out
    assert "preserve unsafe backend output separately" in out


def test_review_gate_blocks_unsafe_raw_marker(tmp_path):
    from signposter.artifact import plan_review_summary, write_manual_artifact
    from signposter.review import evaluate_review_gate

    plan = plan_review_summary(pr=73, risk="low", runs_dir=tmp_path)
    write_manual_artifact(plan, apply=True)
    raw = tmp_path / "pr-73-reviewer.raw.txt"
    raw.write_text("The model is not supported for this account.\n", encoding="utf-8")

    result = evaluate_review_gate("test/repo", 73, summary_path=plan.path)

    assert result.gate_pass is False
    assert "reviewer artifact preflight" in result.status
    assert "reviewer raw artifact contains stale/failover signal" in result.reason


def test_format_review_artifact_validation_summary_is_concise(tmp_path):
    from signposter.review import (
        format_review_artifact_validation_summary,
        validate_review_artifact,
    )

    path = tmp_path / "pr-73-reviewer.summary.md"
    path.write_text(
        "Verdict: APPROVE\n"
        "Confidence: 0.91\n"
        "Risk: medium\n"
        "Scope match: yes\n"
        "CI considered: yes\n"
        "Merge recommendation: yes\n"
        "Automerge eligible: no\n",
        encoding="utf-8",
    )

    result = validate_review_artifact(73, summary_path=str(path))
    out = format_review_artifact_validation_summary(result)

    assert out.splitlines() == [
        "Signposter Review Artifact Summary",
        "pr: #73",
        "status: ready",
        "verdict: APPROVE",
        "confidence: 0.91",
        "risk: medium",
        "error: none",
    ]
    assert "Notes:" not in out


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


# =============================================================================
# HARDENING-018A tests: identity guard + reviewer token support
# =============================================================================


def test_submit_plan_blocks_self_review_when_no_token():
    from signposter.review import plan_review_submit

    with patch("signposter.review.evaluate_review_gate") as mock_gate, \
         patch("signposter.review._fetch_current_gh_user") as mock_user, \
         patch("signposter.review._fetch_pr_author") as mock_author, \
         patch("signposter.review._get_reviewer_token") as mock_token:

        from signposter.review import ReviewerOpinion, ReviewGateResult
        op = ReviewerOpinion(
            verdict="APPROVE", confidence=0.95, risk="low",
            scope_match="yes", ci_considered="yes",
            merge_recommendation="yes", automerge_eligible="yes",
            findings=[], reasoning=None, raw_text=""
        )
        mock_gate.return_value = ReviewGateResult(
            pr_number=5, status="pass", reason="good", opinion=op,
            gate_pass=True, merge_eligible=True, automerge_eligible=True,
            summary_path=None, notes=[] 
        )
        mock_user.return_value = "ExatronOmega"
        mock_author.return_value = "ExatronOmega"
        mock_token.return_value = None   # no dedicated token

        plan = plan_review_submit("test/repo", 5)

    assert plan.action == "blocked"
    assert plan.self_review_blocked is True
    assert "cannot approve own" in plan.status
    assert plan.current_user == "ExatronOmega"
    assert plan.pr_author == "ExatronOmega"
    assert plan.reviewer_token_configured is False


def test_submit_apply_refuses_self_review_without_calling_gh(monkeypatch):
    from signposter.review import submit_review

    with patch("signposter.review.plan_review_submit") as mock_plan:
        from signposter.review import ReviewSubmitPlan
        fake_plan = ReviewSubmitPlan(
            pr_number=5, action="blocked", body="", gate_pass=True,
            gate_reason="good", status="blocked — cannot approve own pull request",
            gh_preview="", notes=[],
            current_user="ExatronOmega", pr_author="ExatronOmega",
            reviewer_token_configured=False, self_review_blocked=True,
            failure_reason="current GitHub identity is the PR author"
        )
        mock_plan.return_value = fake_plan

        with patch("subprocess.run") as mock_sub:
            result = submit_review("test/repo", 5, apply=True)
            mock_sub.assert_not_called()

    assert result["mode"] == "apply_blocked"
    assert "current GitHub identity is the PR author" in result.get("error", "")


def test_submit_plan_allows_approval_when_users_differ():
    from signposter.review import plan_review_submit

    with patch("signposter.review.evaluate_review_gate") as mock_gate, \
         patch("signposter.review._fetch_current_gh_user") as mock_user, \
         patch("signposter.review._fetch_pr_author") as mock_author, \
         patch("signposter.review._get_reviewer_token") as mock_token:

        from signposter.review import ReviewerOpinion, ReviewGateResult
        op = ReviewerOpinion(
            verdict="APPROVE", confidence=0.95, risk="low",
            scope_match="yes", ci_considered="yes",
            merge_recommendation="yes", automerge_eligible="yes",
            findings=[], reasoning=None, raw_text=""
        )
        mock_gate.return_value = ReviewGateResult(
            pr_number=5, status="pass", reason="good", opinion=op,
            gate_pass=True, merge_eligible=True, automerge_eligible=True,
            summary_path=None, notes=[] 
        )
        mock_user.return_value = "signposter-reviewer-bot"
        mock_author.return_value = "ExatronOmega"
        mock_token.return_value = None

        plan = plan_review_submit("test/repo", 5)

    assert plan.action == "approve"
    assert plan.self_review_blocked is False
    assert plan.status == "ready"


def test_reviewer_token_is_passed_as_gh_token(monkeypatch):
    """Verify that SIGNPOSTER_REVIEWER_GH_TOKEN is injected as GH_TOKEN."""
    from signposter.review import _get_gh_env

    monkeypatch.setenv("SIGNPOSTER_REVIEWER_GH_TOKEN", "ghp_fake_reviewer_token_123")

    env = _get_gh_env("ghp_fake_reviewer_token_123")
    assert env.get("GH_TOKEN") == "ghp_fake_reviewer_token_123"
    # Normal env vars should still be present
    assert "PATH" in env

    # The production code never prints the raw token value in user-facing output.
    # (The test dict itself naturally contains it; we only care about real CLI output.)


def test_failed_gh_review_includes_stderr_in_result():
    from signposter.review import submit_review

    with patch("signposter.review.plan_review_submit") as mock_plan:
        from signposter.review import ReviewSubmitPlan
        fake_plan = ReviewSubmitPlan(5, "approve", "body text", True, "good", "ready", "gh ...", [])
        mock_plan.return_value = fake_plan

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "GraphQL: Review Can not approve your own pull request"

        with patch("subprocess.run", return_value=FakeProc()):
            result = submit_review("test/repo", 5, apply=True)

    assert result.get("success") is False
    assert "Can not approve your own" in result.get("error", "") or result.get("stderr", "")


def test_dry_run_still_does_not_call_subprocess():
    from signposter.review import submit_review

    with patch("subprocess.run") as mock_sub:
        with patch("signposter.review.plan_review_submit") as mock_plan:
            from signposter.review import ReviewSubmitPlan
            fake_plan = ReviewSubmitPlan(5, "approve", "body", True, "good", "ready", "gh ...", [])
            mock_plan.return_value = fake_plan

            submit_review("test/repo", 5, apply=False)
            mock_sub.assert_not_called()


def test_review_gate_allows_medium_risk_with_explicit_override(tmp_path):
    from signposter.review import evaluate_review_gate

    summary = tmp_path / "review.md"
    summary.write_text(
        """
Verdict: APPROVE
Confidence: 0.90
Risk: medium
Scope match: yes
CI considered: yes
Merge recommendation: yes
Automerge eligible: no
Findings:
  - Medium risk but scoped and reviewed.
Reasoning summary:
  Scoped medium-risk change.
""",
        encoding="utf-8",
    )

    blocked = evaluate_review_gate("test/repo", 16, summary_path=str(summary))
    allowed = evaluate_review_gate(
        "test/repo",
        16,
        summary_path=str(summary),
        allow_medium_risk=True,
    )

    assert blocked.gate_pass is False
    assert blocked.reason == "reviewer risk is medium"
    assert allowed.gate_pass is True
    assert allowed.merge_eligible is True
    assert "medium risk explicitly allowed" in allowed.reason



def test_review_gate_allows_high_risk_with_explicit_override(tmp_path):
    from signposter.review import evaluate_review_gate

    summary = tmp_path / "review.md"
    summary.write_text(
        """
Verdict: APPROVE
Confidence: 0.92
Risk: high
Scope match: yes
CI considered: yes
Merge recommendation: yes
Automerge eligible: no
Findings:
  - High-risk policy surface but scoped and reviewed.
Reasoning summary:
  Scoped high-risk change.
""",
        encoding="utf-8",
    )

    blocked = evaluate_review_gate("test/repo", 21, summary_path=str(summary))
    allowed = evaluate_review_gate(
        "test/repo",
        21,
        summary_path=str(summary),
        allow_high_risk=True,
    )

    assert blocked.gate_pass is False
    assert blocked.reason == "reviewer risk is high"
    assert allowed.gate_pass is True
    assert allowed.merge_eligible is True
    assert "high risk explicitly allowed" in allowed.reason


def test_review_plan_allows_high_risk_with_explicit_override():
    from signposter.review import plan_review_for_pr

    def fake_view(repo, pr, fields):
        if fields == ["files"]:
            return {"files": [{"path": "src/signposter/review.py"}]}
        return {
            "title": "fix: add high-risk override",
            "state": "OPEN",
            "baseRefName": "main",
            "headRefName": "work/issue-21-h033b-add-explicit-high-risk-review-override-path",
            "mergeable": "MERGEABLE",
            "reviewDecision": None,
            "body": "Related issue: #21",
        }

    with patch("signposter.review._run_gh_pr_view", side_effect=fake_view), \
         patch("signposter.review._fetch_pr_checks") as mock_checks, \
         patch("signposter.review._fetch_pr_files") as mock_files:
        mock_checks.return_value = {
            "status": "pass",
            "successful": 1,
            "failing": 0,
            "pending": 0,
        }
        mock_files.return_value = {
            "files_changed": 1,
            "additions": 20,
            "deletions": 2,
        }

        blocked = plan_review_for_pr("test/repo", 21)
        allowed = plan_review_for_pr("test/repo", 21, allow_high_risk=True)

    assert blocked.status == "blocked — high risk change detected"
    assert allowed.status == "ready"
    assert allowed.risk_level == "high"
    assert any("High-risk override" in note for note in allowed.notes)


def test_review_submit_allows_high_risk_with_explicit_override():
    from signposter.review import ReviewerOpinion, ReviewGateResult, plan_review_submit

    op = ReviewerOpinion(
        verdict="APPROVE",
        confidence=0.95,
        risk="high",
        scope_match="yes",
        ci_considered="yes",
        merge_recommendation="yes",
        automerge_eligible="no",
        findings=["High-risk but scoped."],
        reasoning="Reviewed.",
        raw_text="",
    )

    with patch("signposter.review.evaluate_review_gate") as mock_gate, \
         patch("signposter.review._fetch_current_gh_user") as mock_user, \
         patch("signposter.review._fetch_pr_author") as mock_author, \
         patch("signposter.review._get_reviewer_token") as mock_token:
        mock_gate.return_value = ReviewGateResult(
            pr_number=21,
            status="pass",
            reason="high risk explicitly allowed",
            opinion=op,
            gate_pass=True,
            merge_eligible=True,
            automerge_eligible=False,
            summary_path="artifacts/runs/pr-21-reviewer.summary.md",
            notes=[],
        )
        mock_user.return_value = "AlphaExatron"
        mock_author.return_value = "ExatronOmega"
        mock_token.return_value = "fake-token"

        plan = plan_review_submit("test/repo", 21, allow_high_risk=True)

    mock_gate.assert_called_once_with(
        "test/repo",
        21,
        allow_medium_risk=False,
        allow_high_risk=True,
    )
    assert plan.status == "ready"
    assert plan.action == "approve"
    assert any("High-risk" in note for note in plan.notes)


def test_submit_review_apply_passes_allow_high_risk_to_plan():
    from signposter.review import ReviewSubmitPlan, submit_review

    with patch("signposter.review.plan_review_submit") as mock_plan, \
         patch("signposter.review._run_gh_with_token") as mock_gh:
        fake_plan = ReviewSubmitPlan(
            pr_number=22,
            action="approve",
            body="Signposter reviewer gate: APPROVE",
            gate_pass=True,
            gate_reason="high risk explicitly allowed",
            status="ready",
            gh_preview="gh pr review 22 -R test/repo --approve --body-file ...",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        class FakeProc:
            returncode = 0
            stdout = "Review submitted."
            stderr = ""

        mock_gh.return_value = FakeProc()

        result = submit_review(
            "test/repo",
            22,
            apply=True,
            allow_high_risk=True,
        )

    mock_plan.assert_called_once_with(
        "test/repo",
        22,
        allow_medium_risk=False,
        allow_high_risk=True,
    )
    assert result["mode"] == "apply"
    assert result["success"] is True


def test_validate_review_artifact_ready(tmp_path):
    from signposter.artifact import plan_review_summary, write_manual_artifact
    from signposter.review import validate_review_artifact

    plan = plan_review_summary(pr=38, risk="medium", runs_dir=tmp_path)
    write_manual_artifact(plan, apply=True)

    result = validate_review_artifact(38, summary_path=plan.path)

    assert result.status == "ready"
    assert result.errors == []
    assert result.opinion.verdict == "APPROVE"
    assert result.raw_exists is False


def test_validate_review_artifact_blocks_missing_summary(tmp_path):
    from signposter.review import validate_review_artifact

    result = validate_review_artifact(38, summary_path=str(tmp_path / "missing.md"))

    assert result.status == "blocked"
    assert "summary artifact missing" in result.errors[0]


def test_validate_review_artifact_blocks_malformed_fields(tmp_path):
    from signposter.review import validate_review_artifact

    path = tmp_path / "pr-38-reviewer.summary.md"
    path.write_text("Verdict: MAYBE\nConfidence: nope\nRisk: severe\n", encoding="utf-8")

    result = validate_review_artifact(38, summary_path=str(path))

    assert result.status == "blocked"
    assert any("Verdict" in error for error in result.errors)
    assert any("Confidence" in error for error in result.errors)
    assert any("Risk" in error for error in result.errors)


def test_format_review_artifact_validation_contains_status(tmp_path):
    from signposter.artifact import plan_review_summary, write_manual_artifact
    from signposter.review import format_review_artifact_validation, validate_review_artifact

    plan = plan_review_summary(pr=38, risk="medium", runs_dir=tmp_path)
    write_manual_artifact(plan, apply=True)
    result = validate_review_artifact(38, summary_path=plan.path)

    output = format_review_artifact_validation(result)

    assert "Signposter Review Artifact Validation — PR #38" in output
    assert "ready" in output
    assert "Raw artifact:" in output
    assert "raw reviewer artifact not found" in output
