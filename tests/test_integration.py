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


# =============================================================================
# HARDENING-021B tests: guarded integration apply
# =============================================================================


def test_integration_apply_dry_run_does_not_mutate(monkeypatch):
    from signposter.integration import apply_integration

    with patch("signposter.integration.plan_integration_for_pr") as mock_plan:
        fake_plan = IntegrationPlan(
            pr_number=5, pr_title="test", pr_state="MERGED",
            merge_commit="abc123", base_branch="main", head_branch="work/issue-4-xxx",
            associated_issue=4, issue_state="OPEN",
            current_workflow_state="state:done",
            proposed_workflow_state="state:merged",
            close_issue=True, close_reason="completed",
            main_ci_status="pass",
            status="ready", notes=[],
        )
        mock_plan.return_value = fake_plan

        with patch("subprocess.run") as mock_sub:
            result = apply_integration("test/repo", 5, apply=False)
            mock_sub.assert_not_called()

        assert result["mode"] == "dry_run"


def test_integration_apply_refuses_when_plan_not_ready():
    from signposter.integration import apply_integration

    with patch("signposter.integration.plan_integration_for_pr") as mock_plan:
        fake_plan = IntegrationPlan(
            pr_number=5, pr_title="test", pr_state="MERGED",
            merge_commit="abc123", base_branch="main", head_branch="work/issue-4-xxx",
            associated_issue=4, issue_state="OPEN",
            current_workflow_state="state:done",
            proposed_workflow_state="state:merged",
            close_issue=True, close_reason="completed",
            main_ci_status="pass",
            status="blocked — something",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_integration("test/repo", 5, apply=True)

    assert result["mode"] == "apply_blocked"
    assert "Refusing integration apply" in result.get("error", "")


def test_integration_apply_refuses_when_not_state_done():
    from signposter.integration import apply_integration

    with patch("signposter.integration.plan_integration_for_pr") as mock_plan:
        fake_plan = IntegrationPlan(
            pr_number=5, pr_title="test", pr_state="MERGED",
            merge_commit="abc123", base_branch="main", head_branch="work/issue-4-xxx",
            associated_issue=4, issue_state="OPEN",
            current_workflow_state="state:active",  # wrong
            proposed_workflow_state="state:merged",
            close_issue=True, close_reason="completed",
            main_ci_status="pass",
            status="ready",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_integration("test/repo", 5, apply=True)

    assert result["mode"] == "apply_blocked"
    assert "state:done" in result.get("error", "")


def test_integration_apply_with_apply_calls_expected_commands(monkeypatch):
    from signposter.integration import apply_integration

    class FakeProc:
        returncode = 0
        stdout = "success"
        stderr = ""

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(" ".join(cmd))
        return FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)

    with patch("signposter.integration.plan_integration_for_pr") as mock_plan, \
         patch("signposter.integration.check_labels") as mock_check:
        mock_check.return_value.missing = []
        mock_check.return_value.error = None

        fake_plan = IntegrationPlan(
            pr_number=5, pr_title="test", pr_state="MERGED",
            merge_commit="abc123", base_branch="main", head_branch="work/issue-4-xxx",
            associated_issue=4, issue_state="OPEN",
            current_workflow_state="state:done",
            proposed_workflow_state="state:merged",
            close_issue=True, close_reason="completed",
            main_ci_status="pass",
            status="ready",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_integration("test/repo", 5, apply=True)

    assert result.get("success") is True
    # Check that we tried to do label edit, comment, and close
    assert any("issue edit" in c and "state:merged" in c for c in calls)
    assert any("issue comment" in c for c in calls)
    assert any("issue close" in c and "completed" in c for c in calls)


def test_integration_apply_failed_mutation_includes_stderr(monkeypatch):
    from signposter.integration import apply_integration

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "GraphQL error: rate limit"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())

    with patch("signposter.integration.plan_integration_for_pr") as mock_plan, \
         patch("signposter.integration.check_labels") as mock_check:
        mock_check.return_value.missing = []
        mock_check.return_value.error = None

        fake_plan = IntegrationPlan(
            pr_number=5, pr_title="test", pr_state="MERGED",
            merge_commit="abc123", base_branch="main", head_branch="work/issue-4-xxx",
            associated_issue=4, issue_state="OPEN",
            current_workflow_state="state:done",
            proposed_workflow_state="state:merged",
            close_issue=True, close_reason="completed",
            main_ci_status="pass",
            status="ready",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_integration("test/repo", 5, apply=True)

    assert result.get("success") is False
    assert "rate limit" in str(result.get("errors", []))


def test_integration_apply_dry_run_blocks_when_main_ci_unknown():
    from signposter.integration import IntegrationPlan, format_integration_apply_dry_run

    plan = IntegrationPlan(
        pr_number=5,
        pr_title="test",
        pr_state="MERGED",
        merge_commit="abc123",
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
        notes=[],
    )

    output = format_integration_apply_dry_run(plan)

    assert "main CI: unknown" in output
    assert "blocked — main CI is not confirmed pass (got unknown)" in output
    assert "Status:\n  ready" not in output


def test_integration_apply_dry_run_ready_when_main_ci_pass():
    from signposter.integration import IntegrationPlan, format_integration_apply_dry_run

    plan = IntegrationPlan(
        pr_number=5,
        pr_title="test",
        pr_state="MERGED",
        merge_commit="abc123",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        associated_issue=4,
        issue_state="OPEN",
        current_workflow_state="state:done",
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        main_ci_status="pass",
        status="ready",
        notes=[],
    )

    output = format_integration_apply_dry_run(plan)

    assert "main CI: pass" in output
    assert "Status:\n  ready" in output


def test_fetch_main_ci_status_passes_on_latest_success(monkeypatch):
    from signposter.integration import _fetch_main_ci_status

    class FakeProc:
        returncode = 0
        stdout = (
            '[{"status":"completed","conclusion":"success",'
            '"workflowName":"CI","headBranch":"main"}]'
        )
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())

    assert _fetch_main_ci_status("test/repo") == "pass"


def test_fetch_main_ci_status_failing_on_latest_failure(monkeypatch):
    from signposter.integration import _fetch_main_ci_status

    class FakeProc:
        returncode = 0
        stdout = (
            '[{"status":"completed","conclusion":"failure",'
            '"workflowName":"CI","headBranch":"main"}]'
        )
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())

    assert _fetch_main_ci_status("test/repo") == "failing"


def test_fetch_main_ci_status_pending_on_in_progress(monkeypatch):
    from signposter.integration import _fetch_main_ci_status

    class FakeProc:
        returncode = 0
        stdout = (
            '[{"status":"in_progress","conclusion":"",'
            '"workflowName":"CI","headBranch":"main"}]'
        )
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())

    assert _fetch_main_ci_status("test/repo") == "pending"


def test_fetch_main_ci_status_unknown_on_gh_failure(monkeypatch):
    from signposter.integration import _fetch_main_ci_status

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())

    assert _fetch_main_ci_status("test/repo") == "unknown"


def test_integration_apply_refuses_when_required_labels_missing(monkeypatch):
    """H023C: apply must refuse before any mutation if required labels are missing."""
    from signposter.integration import IntegrationPlan, apply_integration

    with patch("signposter.integration.plan_integration_for_pr") as mock_plan, \
         patch("signposter.integration.check_labels") as mock_check, \
         patch("subprocess.run") as mock_sub:
        mock_check.return_value.missing = ["state:merged"]
        mock_check.return_value.error = None

        fake_plan = IntegrationPlan(
            pr_number=5,
            pr_title="test",
            pr_state="MERGED",
            merge_commit="abc123",
            base_branch="main",
            head_branch="work/issue-4-xxx",
            associated_issue=4,
            issue_state="OPEN",
            current_workflow_state="state:done",
            proposed_workflow_state="state:merged",
            close_issue=True,
            close_reason="completed",
            main_ci_status="pass",
            status="ready",
            notes=[],
        )
        mock_plan.return_value = fake_plan

        result = apply_integration("test/repo", 5, apply=True)

    assert result["mode"] == "apply_blocked"
    assert "state:merged" in result.get("error", "")
    # No mutations should have been attempted
    mock_sub.assert_not_called()


# =============================================================================
# HARDENING-027A: clarify integration apply output for completed plans
# =============================================================================

def test_integration_apply_dry_run_completed_plan_shows_no_mutations():
    """Completed integration plans must not list concrete pending mutations."""
    from signposter.integration import IntegrationPlan, format_integration_apply_dry_run

    plan = IntegrationPlan(
        pr_number=7,
        pr_title="test",
        pr_state="MERGED",
        merge_commit="abc123",
        base_branch="main",
        head_branch="work/issue-6-xxx",
        associated_issue=6,
        issue_state="CLOSED",
        current_workflow_state="state:merged",
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        main_ci_status="pass",
        status="completed",
        notes=[],
    )

    output = format_integration_apply_dry_run(plan)

    # Must not list the concrete mutations
    assert "remove label: state:done" not in output
    assert "add label: state:merged" not in output
    assert "close issue: #6 as completed" not in output
    assert "post integration comment: yes" not in output

    # Must clearly say no pending mutations
    assert "none — integration already completed" in output


def test_integration_apply_dry_run_not_ready_shows_no_mutations():
    """Blocked/not-ready plans must not list concrete mutations."""
    from signposter.integration import IntegrationPlan, format_integration_apply_dry_run

    plan = IntegrationPlan(
        pr_number=7,
        pr_title="test",
        pr_state="MERGED",
        merge_commit="abc123",
        base_branch="main",
        head_branch="work/issue-6-xxx",
        associated_issue=6,
        issue_state="CLOSED",
        current_workflow_state="state:merged",
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        main_ci_status="pass",
        status="blocked",
        notes=[],
    )

    output = format_integration_apply_dry_run(plan)

    assert "remove label: state:done" not in output
    assert "none — integration plan is not ready (blocked)" in output


def test_integration_apply_dry_run_ready_still_lists_mutations():
    """Ready plans must continue to show the actual planned mutations."""
    from signposter.integration import IntegrationPlan, format_integration_apply_dry_run

    plan = IntegrationPlan(
        pr_number=5,
        pr_title="test",
        pr_state="MERGED",
        merge_commit="abc123",
        base_branch="main",
        head_branch="work/issue-4-xxx",
        associated_issue=4,
        issue_state="OPEN",
        current_workflow_state="state:done",
        proposed_workflow_state="state:merged",
        close_issue=True,
        close_reason="completed",
        main_ci_status="pass",
        status="ready",
        notes=[],
    )

    output = format_integration_apply_dry_run(plan)

    assert "remove label: state:done" in output
    assert "add label: state:merged" in output
    assert "close issue: #4 as completed" in output
    assert "post integration comment: yes" in output
    assert "none —" not in output  # should not use the 'none' wording


def test_noop_integration_plan_ready(monkeypatch, tmp_path):
    from signposter.integration import plan_noop_integration_for_issue

    monkeypatch.chdir(tmp_path)
    artifact_dir = tmp_path / "artifacts" / "runs"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "issue-12-gate.summary.md").write_text(
        """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

WATCH-003 was evaluated as a no-op completion: the requested behavior already exists.
The existing implementation provides deterministic terminal-friendly output.
Existing ready output is deterministic and terminal-friendly.
Existing blocked output is deterministic and terminal-friendly.

No files were changed in the isolated worktree.

Targeted validation in isolated worktree passed.
Full validation in isolated worktree passed.
Manual CLI smoke passed.

No GitHub mutation was performed.
No OpenClaw execution was performed.
No manifest mutation was performed.
No unrelated files were changed.
""",
        encoding="utf-8",
    )

    class Proc:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "issue", "view"]:
            return Proc(
                stdout=(
                    '{"number":12,"title":"WATCH-003","state":"OPEN",'
                    '"labels":[{"name":"state:done"},{"name":"phase:build"}]}'
                )
            )
        if cmd[:3] == ["git", "branch", "--list"]:
            return Proc(stdout="")
        if cmd[:3] == ["gh", "pr", "list"]:
            return Proc(stdout="[]")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    plan = plan_noop_integration_for_issue("test/repo", 12)

    assert plan.status == "ready"
    assert plan.current_workflow_state == "state:done"
    assert plan.gate_decision == "pass"
    assert plan.worktree_exists is False
    assert plan.local_branch_exists is False
    assert plan.associated_pr_detected is False


def test_noop_integration_plan_blocks_when_worktree_exists(monkeypatch, tmp_path):
    from signposter.integration import plan_noop_integration_for_issue

    monkeypatch.chdir(tmp_path)
    (tmp_path.parent / "signposter-work" / "12").mkdir(parents=True)
    artifact_dir = tmp_path / "artifacts" / "runs"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "issue-12-gate.summary.md").write_text(
        """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass
no-op completion
requested behavior already exists
existing implementation
existing ready output
existing blocked output
No files were changed.
Targeted validation in isolated worktree passed.
Full validation in isolated worktree passed.
Manual CLI smoke passed.
No GitHub mutation was performed.
No OpenClaw execution was performed.
No manifest mutation was performed.
No unrelated files were changed.
""",
        encoding="utf-8",
    )

    class Proc:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "issue", "view"]:
            return Proc(
                stdout=(
                    '{"number":12,"title":"WATCH-003","state":"OPEN",'
                    '"labels":[{"name":"state:done"}]}'
                )
            )
        if cmd[:3] == ["git", "branch", "--list"]:
            return Proc(stdout="")
        if cmd[:3] == ["gh", "pr", "list"]:
            return Proc(stdout="[]")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    plan = plan_noop_integration_for_issue("test/repo", 12)

    assert "worktree still exists" in plan.status

