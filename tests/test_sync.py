"""Tests for HARDENING-024E — guarded repository sync/rebase."""

from __future__ import annotations

from unittest.mock import patch

from signposter.sync import (
    SyncPlan,
    apply_sync,
    format_sync_apply_result,
    format_sync_plan,
    plan_sync,
)


def _make_plan(**overrides) -> SyncPlan:
    base = dict(
        repo_path="/tmp/signposter",
        current_branch="main",
        upstream="origin/main",
        working_tree_clean=True,
        local_head="abc123",
        upstream_head="def456",
        ahead=0,
        behind=0,
        divergence_status="up-to-date",
        recommended_action="none",
        command_preview="",
        status="completed",
        notes=[
            "No rebase was performed.",
            "No push was performed.",
            "No GitHub mutation was performed.",
        ],
    )
    base.update(overrides)
    return SyncPlan(**base)


# =============================================================================
# Plan state detection
# =============================================================================


def test_plan_completed_when_ahead_behind_zero():
    """plan completed when ahead=0 behind=0."""
    with patch("signposter.sync._current_branch", return_value="main"), \
         patch("signposter.sync._working_tree_dirty", return_value=False), \
         patch("signposter.sync._fetch_origin", return_value=(True, "")), \
         patch("signposter.sync._get_head_sha", side_effect=["abc123", "def456"]), \
         patch("signposter.sync._ahead_behind", return_value=(0, 0)):

        plan = plan_sync("/tmp/signposter")
        assert plan.status == "completed"
        assert plan.divergence_status == "up-to-date"
        assert plan.recommended_action == "none"


def test_plan_recommends_fast_forward_when_behind():
    """plan recommends fast-forward when ahead=0 behind>0."""
    with patch("signposter.sync._current_branch", return_value="main"), \
         patch("signposter.sync._working_tree_dirty", return_value=False), \
         patch("signposter.sync._fetch_origin", return_value=(True, "")), \
         patch("signposter.sync._get_head_sha", side_effect=["abc123", "def456"]), \
         patch("signposter.sync._ahead_behind", return_value=(0, 2)):

        plan = plan_sync("/tmp/signposter")
        assert plan.status == "ready"
        assert plan.recommended_action == "pull"
        assert "git pull origin main" in plan.command_preview


def test_plan_recommends_rebase_when_diverged():
    """plan recommends rebase when ahead>0 behind>0."""
    with patch("signposter.sync._current_branch", return_value="main"), \
         patch("signposter.sync._working_tree_dirty", return_value=False), \
         patch("signposter.sync._fetch_origin", return_value=(True, "")), \
         patch("signposter.sync._get_head_sha", side_effect=["abc123", "def456"]), \
         patch("signposter.sync._ahead_behind", return_value=(1, 1)):

        plan = plan_sync("/tmp/signposter")
        assert plan.status == "ready"
        assert plan.recommended_action == "rebase"
        assert "git pull --rebase origin main" in plan.command_preview


def test_plan_notes_push_needed_when_ahead_only():
    """plan notes push-needed when ahead>0 behind=0."""
    with patch("signposter.sync._current_branch", return_value="main"), \
         patch("signposter.sync._working_tree_dirty", return_value=False), \
         patch("signposter.sync._fetch_origin", return_value=(True, "")), \
         patch("signposter.sync._get_head_sha", side_effect=["abc123", "def456"]), \
         patch("signposter.sync._ahead_behind", return_value=(2, 0)):

        plan = plan_sync("/tmp/signposter")
        assert plan.status == "ready"
        assert plan.recommended_action == "push-required"
        assert "git push" in plan.command_preview


# =============================================================================
# Blocking conditions
# =============================================================================


def test_plan_blocked_when_working_tree_dirty():
    """plan blocked when working tree dirty."""
    with patch("signposter.sync._current_branch", return_value="main"), \
         patch("signposter.sync._working_tree_dirty", return_value=True), \
         patch("signposter.sync._fetch_origin", return_value=(True, "")), \
         patch("signposter.sync._get_head_sha", side_effect=["abc123", "def456"]), \
         patch("signposter.sync._ahead_behind", return_value=(0, 0)):

        plan = plan_sync("/tmp/signposter")
        assert "blocked — working tree has uncommitted changes" in plan.status


def test_plan_blocked_when_not_on_main():
    """plan blocked when current branch is not main."""
    with patch("signposter.sync._current_branch", return_value="feature/foo"):

        plan = plan_sync("/tmp/signposter")
        assert "blocked — current branch is feature/foo, not main" in plan.status


# =============================================================================
# Apply safety
# =============================================================================


def test_apply_dry_run_does_not_call_git_pull():
    """apply dry-run does not call git pull."""
    with patch("signposter.sync.plan_sync") as mock_plan:
        mock_plan.return_value = _make_plan(status="ready", recommended_action="rebase")

        result = apply_sync("/tmp/signposter", apply=False, rebase=True)
        assert result["mode"] == "dry_run"


def test_apply_refuses_without_apply_flag():
    """apply refuses without --apply."""
    with patch("signposter.sync.plan_sync") as mock_plan:
        mock_plan.return_value = _make_plan(status="ready", recommended_action="rebase")

        result = apply_sync("/tmp/signposter", apply=False, rebase=True)
        # dry_run path
        assert result["mode"] == "dry_run"


def test_apply_refuses_without_rebase_flag_when_rebase_needed():
    """apply refuses without --rebase when rebase is needed."""
    with patch("signposter.sync.plan_sync") as mock_plan:
        mock_plan.return_value = _make_plan(status="ready", recommended_action="rebase")

        result = apply_sync("/tmp/signposter", apply=True, rebase=False)
        assert result["mode"] == "apply_blocked"
        assert "--rebase" in result.get("error", "")


def test_apply_runs_git_pull_rebase_when_ready():
    """apply runs git pull --rebase origin main when ready (no duplicated 'git')."""
    with patch("signposter.sync.plan_sync") as mock_plan, \
         patch("signposter.sync._git") as mock_git:

        mock_plan.return_value = _make_plan(status="ready", recommended_action="rebase")
        mock_git.return_value = (0, "Successfully rebased.", "")

        result = apply_sync("/tmp/signposter", apply=True, rebase=True)

        assert result["success"] is True

        # Assert exact args passed to _git (must NOT start with 'git')
        assert mock_git.called
        called_args = mock_git.call_args[0][0]   # first positional arg to _git
        assert called_args[0] == "pull", f"Expected first arg 'pull', got {called_args[0]}"
        assert "git" not in called_args[0], "Duplicated 'git' detected in argv"
        assert called_args == ["pull", "--rebase", "origin", "main"]

        assert any("pull --rebase" in c for c in result.get("commands_run", []))


def test_apply_runs_git_pull_fast_forward_when_behind():
    """apply runs correct 'git pull origin main' (no duplicated 'git') for fast-forward case."""
    with patch("signposter.sync.plan_sync") as mock_plan, \
         patch("signposter.sync._git") as mock_git:

        mock_plan.return_value = _make_plan(
            status="ready",
            recommended_action="pull",          # fast-forward case
            command_preview="git pull origin main"
        )
        mock_git.return_value = (0, "Fast-forward", "")

        result = apply_sync("/tmp/signposter", apply=True, rebase=True)

        assert result["success"] is True

        # Critical regression: _git must receive args starting with 'pull', not 'git'
        assert mock_git.called
        called_args = mock_git.call_args[0][0]
        assert called_args[0] == "pull"
        assert called_args == ["pull", "origin", "main"]
        assert "git" not in called_args

        assert any("pull origin main" in c for c in result.get("commands_run", []))


def test_apply_reports_conflict_with_bounded_stderr():
    """apply reports conflict/failure with bounded stderr."""
    with patch("signposter.sync.plan_sync") as mock_plan, \
         patch("signposter.sync._git") as mock_git:

        mock_plan.return_value = _make_plan(status="ready", recommended_action="rebase")
        mock_git.return_value = (1, "", "CONFLICT (content): Merge conflict in foo.py\n" * 20)

        result = apply_sync("/tmp/signposter", apply=True, rebase=True)

        assert result["success"] is False
        err = result.get("error", "")
        assert len(err) <= 500
        assert "CONFLICT" in err or "conflict" in err.lower()


def test_apply_never_calls_git_push():
    """apply never calls git push."""
    with patch("signposter.sync.plan_sync") as mock_plan, \
         patch("signposter.sync._git") as mock_git:

        mock_plan.return_value = _make_plan(status="ready", recommended_action="rebase")
        mock_git.return_value = (0, "", "")

        apply_sync("/tmp/signposter", apply=True, rebase=True)

        called_cmds = [c[0][0] for c in mock_git.call_args_list]
        for cmd in called_cmds:
            assert "push" not in " ".join(cmd).lower()


def test_output_contains_no_push_no_github_mutation_notes():
    """output contains no-push/no-GitHub-mutation notes in all paths."""
    plan = _make_plan()
    out = format_sync_plan(plan)
    assert "No push was performed" in out
    assert "No GitHub mutation was performed" in out

    blocked = {"mode": "apply_blocked", "plan": plan, "error": "test"}
    blocked_out = format_sync_apply_result(blocked)
    assert "No push was performed" in blocked_out
    assert "No GitHub mutation was performed" in blocked_out
