"""Tests for HARDENING-021C — local worktree cleanup plan/apply."""

from __future__ import annotations

from unittest.mock import patch

from signposter.cleanup import (
    CleanupPlan,
    apply_cleanup,
    format_cleanup_apply_dry_run,
    format_cleanup_apply_result,
    format_cleanup_plan,
    plan_cleanup_for_pr,
)


def _make_plan(**overrides) -> CleanupPlan:
    base = dict(
        pr_number=5,
        pr_state="MERGED",
        head_branch="work/issue-4-test-task-isolated-worker-readme-note",
        associated_issue=4,
        issue_state="CLOSED",
        has_state_merged_label=True,
        expected_worktree_path="../signposter-work/4",
        worktree_exists=True,
        local_branch="work/issue-4-test-task-isolated-worker-readme-note",
        local_branch_exists=True,
        status="ready",
        notes=[
            "No local worktree was removed.",
            "No local branch was deleted.",
            "No GitHub mutation was performed.",
        ],
    )
    base.update(overrides)
    return CleanupPlan(**base)


# =============================================================================
# plan_cleanup_for_pr — ready / completed / blocked cases
# =============================================================================


def test_cleanup_plan_ready_when_all_conditions_met():
    """Ready cleanup plan for merged PR + closed state:merged issue + existing worktree."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr, \
         patch("signposter.cleanup.fetch_issue_context") as mock_ctx, \
         patch("signposter.cleanup._worktree_exists", return_value=True), \
         patch("signposter.cleanup._local_branch_exists", return_value=True):
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "body": "Related issue: #4",
        }
        mock_ctx.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        }

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert plan.status == "ready"
        assert plan.pr_state == "MERGED"
        assert plan.associated_issue == 4
        assert plan.worktree_exists is True
        assert "No GitHub mutation" in plan.notes[2]


def test_cleanup_plan_completed_noop_when_worktree_absent():
    """completed/no-op plan when worktree is already absent (not failed)."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr, \
         patch("signposter.cleanup.fetch_issue_context") as mock_ctx, \
         patch("signposter.cleanup._worktree_exists", return_value=False):
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "body": "",
        }
        mock_ctx.return_value = {"state": "CLOSED", "labels": [{"name": "state:merged"}]}

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert plan.status == "completed"
        assert "Worktree already absent" in plan.notes[0]


def test_cleanup_plan_blocked_when_pr_not_merged():
    """blocked when PR is not merged."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr:
        mock_pr.return_value = {"state": "OPEN", "headRefName": "work/issue-4-foo", "body": ""}

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert "blocked — PR is not merged" in plan.status


def test_cleanup_plan_blocked_when_issue_not_closed():
    """blocked when issue is not closed."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr, \
         patch("signposter.cleanup.fetch_issue_context") as mock_ctx:
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-4-foo",
            "body": "",
        }
        mock_ctx.return_value = {"state": "OPEN", "labels": []}

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert "blocked — associated issue" in plan.status
        assert "not CLOSED" in plan.status


def test_cleanup_plan_blocked_when_missing_state_merged_label():
    """blocked when issue does not have state:merged."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr, \
         patch("signposter.cleanup.fetch_issue_context") as mock_ctx:
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-4-foo",
            "body": "",
        }
        mock_ctx.return_value = {"state": "CLOSED", "labels": []}

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert "does not have label state:merged" in plan.status


# =============================================================================
# apply behavior
# =============================================================================


def test_cleanup_apply_dry_run_does_not_call_subprocess():
    """dry-run apply does not call subprocess."""
    plan = _make_plan()
    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan), \
         patch("signposter.cleanup.subprocess.run") as mock_run:
        result = apply_cleanup("ExatronOmega/signposter", 5, apply=False)
    mock_run.assert_not_called()
    assert result["mode"] == "dry_run"
    assert "DRY RUN" in format_cleanup_apply_dry_run(plan)


def test_cleanup_apply_refuses_when_plan_not_ready():
    """apply refuses when plan is not ready."""
    plan = _make_plan(status="blocked — PR is not merged")
    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan):
        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)
        assert result["mode"] == "apply_blocked"
        assert "Refusing cleanup apply" in result.get("error", "")


def test_cleanup_apply_replans_once_after_post_integration_issue_state_race():
    """apply should refresh once when integration just closed the issue."""
    stale = _make_plan(
        issue_state="OPEN",
        has_state_merged_label=False,
        status="blocked — associated issue #4 is not CLOSED (state: OPEN)",
    )
    ready = _make_plan(status="ready")

    with patch(
        "signposter.cleanup.plan_cleanup_for_pr",
        side_effect=[stale, ready],
    ), patch(
        "signposter.cleanup._local_branch_exists",
        return_value=False,
    ), patch(
        "signposter.cleanup.subprocess.run",
    ) as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

    assert result["success"] is True
    assert any("removed worktree" in r for r in result.get("results", []))


def test_cleanup_apply_still_blocks_when_refreshed_plan_stays_open():
    """apply must still refuse cleanup when the issue is genuinely open."""
    stale = _make_plan(
        issue_state="OPEN",
        has_state_merged_label=False,
        status="blocked — associated issue #4 is not CLOSED (state: OPEN)",
    )

    with patch(
        "signposter.cleanup.plan_cleanup_for_pr",
        side_effect=[stale, stale],
    ):
        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

    assert result["mode"] == "apply_blocked"
    assert "Refusing cleanup apply" in result.get("error", "")


def test_cleanup_apply_removes_worktree_when_ready():
    """apply removes expected worktree when ready."""
    plan = _make_plan(status="ready", worktree_exists=True)

    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan), \
         patch("signposter.cleanup._local_branch_exists", return_value=False), \
         patch("signposter.cleanup.subprocess.run") as mock_run:

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

        assert result["success"] is True
        assert any("removed worktree" in r for r in result.get("results", []))


def test_cleanup_apply_deletes_branch_only_if_present():
    """apply deletes expected local branch only if present."""
    plan = _make_plan(status="ready", local_branch_exists=True)

    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan), \
         patch("signposter.cleanup._local_branch_exists", return_value=True), \
         patch("signposter.cleanup.subprocess.run") as mock_run:

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

        assert result["success"] is True
        assert any("deleted local branch" in r for r in result.get("results", []))


def test_cleanup_apply_stops_before_branch_deletion_on_worktree_failure():
    """apply stops before branch deletion if worktree removal fails."""
    plan = _make_plan(status="ready")

    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan), \
         patch("signposter.cleanup.subprocess.run") as mock_run:

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "fatal: worktree not found"

        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

        assert result["success"] is False
        assert result.get("partial") is True
        assert any("worktree remove failed" in e for e in result.get("errors", []))


def test_cleanup_apply_reports_bounded_stderr_on_failure():
    """failed subprocess reports bounded stderr."""
    plan = _make_plan(status="ready")

    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan), \
         patch("signposter.cleanup.subprocess.run") as mock_run:

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "x" * 1000

        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

        assert result["success"] is False
        for e in result.get("errors", []):
            assert len(e) < 450  # bounded


def test_cleanup_output_contains_no_github_mutation_notes():
    """output contains no-GitHub-mutation notes in all paths."""
    plan = _make_plan()
    out = format_cleanup_plan(plan)
    assert "No GitHub mutation was performed" in out

    dry = format_cleanup_apply_dry_run(plan)
    assert "No GitHub mutation was performed" in dry

    blocked = {"mode": "apply_blocked", "plan": plan, "error": "test"}
    blocked_out = format_cleanup_apply_result(blocked)
    assert "No GitHub mutation was performed" in blocked_out
