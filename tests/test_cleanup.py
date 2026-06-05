"""Tests for HARDENING-021C — local worktree cleanup plan/apply."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

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


def test_cleanup_plan_ready_with_pr_body_related_issue_fallback():
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr, \
         patch("signposter.cleanup.fetch_issue_context") as mock_ctx, \
         patch("signposter.cleanup._worktree_exists", return_value=True), \
         patch("signposter.cleanup._local_branch_exists", return_value=True):
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "feature/body-link-fallback",
            "body": "Related issue: #4",
        }
        mock_ctx.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        }

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert plan.status == "ready"
        assert plan.associated_issue == 4
        assert plan.expected_worktree_path == "../signposter-work/4"
        assert plan.local_branch == "feature/body-link-fallback"


def test_cleanup_plan_completed_noop_when_worktree_absent():
    """completed/no-op plan when worktree and local branch are already absent."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr, \
         patch("signposter.cleanup.fetch_issue_context") as mock_ctx, \
         patch("signposter.cleanup._worktree_exists", return_value=False), \
         patch("signposter.cleanup._local_branch_exists", return_value=False):
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "body": "",
        }
        mock_ctx.return_value = {"state": "CLOSED", "labels": [{"name": "state:merged"}]}

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert plan.status == "completed"
        assert "Worktree already absent" in plan.notes[0]
        assert "Local branch already absent" in plan.notes[1]


def test_cleanup_plan_ready_when_worktree_absent_but_branch_exists():
    """branch-only cleanup remains ready until the local branch is deleted."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr, \
         patch("signposter.cleanup.fetch_issue_context") as mock_ctx, \
         patch("signposter.cleanup._worktree_exists", return_value=False), \
         patch("signposter.cleanup._local_branch_exists", return_value=True):
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-4-test-task-isolated-worker-readme-note",
            "body": "",
        }
        mock_ctx.return_value = {"state": "CLOSED", "labels": [{"name": "state:merged"}]}

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert plan.status == "ready"
        assert plan.worktree_exists is False
        assert plan.local_branch_exists is True
        assert "Local branch is still present" in plan.notes[1]


def test_cleanup_plan_blocked_when_pr_not_merged():
    """blocked when PR is not merged."""
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr:
        mock_pr.return_value = {"state": "OPEN", "headRefName": "work/issue-4-foo", "body": ""}

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

        assert "blocked — PR is not merged" in plan.status


def test_cleanup_plan_blocks_ambiguous_issue_linkage():
    with patch("signposter.cleanup._run_gh_pr_view") as mock_pr:
        mock_pr.return_value = {
            "state": "MERGED",
            "headRefName": "work/issue-4-foo",
            "body": "Related issue: #5",
        }

        plan = plan_cleanup_for_pr("ExatronOmega/signposter", 5)

    assert "blocked — associated issue link is ambiguous" in plan.status
    assert plan.associated_issue is None


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
    assert result["would_execute"] is True
    assert result["already_completed"] is False
    assert "DRY RUN" in format_cleanup_apply_dry_run(plan)


def test_cleanup_apply_refuses_when_plan_not_ready():
    """apply refuses when plan is not ready."""
    plan = _make_plan(status="blocked — PR is not merged")
    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan):
        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)
        assert result["mode"] == "apply_blocked"
        assert "Refusing cleanup apply" in result.get("error", "")


def test_cleanup_apply_already_cleaned_does_not_call_subprocess():
    """completed cleanup plans are idempotent and do not touch git."""
    plan = _make_plan(
        status="completed",
        worktree_exists=False,
        local_branch_exists=False,
    )

    with (
        patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan),
        patch("signposter.cleanup.subprocess.run") as mock_run,
    ):
        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

    mock_run.assert_not_called()
    assert result["mode"] == "apply_completed"
    assert result["success"] is True
    assert result["results"] == ["cleanup already completed"]

    output = format_cleanup_apply_result(result)
    assert "status: already completed" in output
    assert "worktree: already absent" in output
    assert "local branch: already absent" in output
    assert "No GitHub mutation was performed." in output


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


def test_cleanup_apply_deletes_branch_when_worktree_already_absent():
    """apply handles branch-only cleanup idempotently."""
    plan = _make_plan(status="ready", worktree_exists=False, local_branch_exists=True)
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    with patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan), \
         patch("signposter.cleanup._local_branch_exists", return_value=True), \
         patch("signposter.cleanup.subprocess.run", side_effect=fake_run):

        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

    assert result["success"] is True
    assert result["branch_deleted"] is True
    assert any("worktree already absent" in r for r in result.get("results", []))
    assert calls == [["git", "branch", "-D", plan.local_branch]]


def test_cleanup_apply_skips_already_deleted_branch_after_ready_plan():
    """apply is idempotent when a branch-only cleanup target disappears after planning."""
    plan = _make_plan(status="ready", worktree_exists=False, local_branch_exists=True)

    with (
        patch("signposter.cleanup.plan_cleanup_for_pr", return_value=plan),
        patch("signposter.cleanup._local_branch_exists", return_value=False),
        patch("signposter.cleanup.subprocess.run") as mock_run,
    ):
        result = apply_cleanup("ExatronOmega/signposter", 5, apply=True)

    mock_run.assert_not_called()
    assert result["success"] is True
    assert result["branch_deleted"] is False
    assert result["results"] == ["worktree already absent"]

    output = format_cleanup_apply_result(result)
    assert "worktree already absent" in output
    assert "deleted local branch: no (was not present)" in output
    assert "No GitHub mutation was performed." in output


def test_cli_cleanup_apply_dry_run_returns_blocked_exit_for_blocked_plan(
    monkeypatch, capsys
):
    from signposter.cli import main

    plan = _make_plan(status="blocked — PR is not merged")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "cleanup",
            "apply",
            "--repo",
            "test/repo",
            "--pr",
            "5",
        ],
    )

    with patch(
        "signposter.cli.apply_cleanup",
        return_value={"mode": "dry_run", "plan": plan, "would_execute": False},
    ), pytest.raises(SystemExit) as exc:
        main()

    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert "blocked — PR is not merged" in out
    assert "DRY RUN: no local worktree was removed." in out


def test_cli_cleanup_apply_completed_returns_success(monkeypatch, capsys):
    from signposter.cli import main

    plan = _make_plan(
        status="completed",
        worktree_exists=False,
        local_branch_exists=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "cleanup",
            "apply",
            "--repo",
            "test/repo",
            "--pr",
            "5",
            "--apply",
        ],
    )

    with patch(
        "signposter.cli.apply_cleanup",
        return_value={
            "mode": "apply_completed",
            "plan": plan,
            "success": True,
            "results": ["cleanup already completed"],
            "branch_deleted": False,
        },
    ), pytest.raises(SystemExit) as exc:
        main()

    out = capsys.readouterr().out
    assert exc.value.code == 0
    assert "status: already completed" in out
    assert "Status:" in out
    assert "completed" in out


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


def test_cleanup_plan_surfaces_pending_stale_worktree_and_branch():
    """ready plan names the leftover local cleanup and guarded apply command."""
    plan = _make_plan()

    out = format_cleanup_plan(plan)

    assert "Pending local cleanup:" in out
    assert "category: stale local worker state" in out
    assert "status: pending — local cleanup remains" in out
    assert "pending: worktree: ../signposter-work/4" in out
    assert (
        "pending: local branch: "
        "work/issue-4-test-task-isolated-worker-readme-note"
    ) in out
    assert (
        "next command: signposter cleanup apply --repo <repo> --pr 5 --apply"
        in out
    )
    assert "cleanup apply is local-only and remains guarded by --apply" in out


def test_cleanup_plan_surfaces_pending_branch_only_cleanup():
    """branch-only ready plan remains visible after worktree cleanup already happened."""
    plan = _make_plan(worktree_exists=False, local_branch_exists=True)

    out = format_cleanup_plan(plan)

    assert "Pending local cleanup:" in out
    assert "status: pending — local cleanup remains" in out
    assert "pending: worktree:" not in out
    assert (
        "pending: local branch: "
        "work/issue-4-test-task-isolated-worker-readme-note"
    ) in out
    assert (
        "next command: signposter cleanup apply --repo <repo> --pr 5 --apply"
        in out
    )


def test_cleanup_apply_dry_run_surfaces_pending_local_cleanup():
    """dry-run apply shows the same stale local cleanup details before mutation."""
    plan = _make_plan()

    out = format_cleanup_apply_dry_run(plan)

    assert "DRY RUN: no local worktree was removed." in out
    assert "Pending local cleanup:" in out
    assert "status: pending — local cleanup remains" in out
    assert "pending: worktree: ../signposter-work/4" in out
    assert (
        "next command: signposter cleanup apply --repo <repo> --pr 5 --apply"
        in out
    )


def test_cleanup_plan_omits_pending_cleanup_when_already_completed():
    """completed cleanup plan should stay a compact no-op surface."""
    plan = _make_plan(
        status="completed",
        worktree_exists=False,
        local_branch_exists=False,
    )

    out = format_cleanup_plan(plan)

    assert "Pending local cleanup:" not in out
    assert "cleanup eligible: no" in out
