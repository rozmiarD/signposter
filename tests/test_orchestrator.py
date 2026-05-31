from __future__ import annotations

import sys
from unittest.mock import Mock, patch

import pytest

from signposter.cli import main
from signposter.lifecycle import LifecycleNext, LifecyclePreflight
from signposter.orchestrator import (
    format_orchestrator_next,
    format_orchestrator_run_next,
    plan_orchestrator_next,
    plan_orchestrator_run_next,
    plan_orchestrator_tail,
    run_orchestrator_loop,
    run_orchestrator_step,
)
from signposter.scan import LabeledItem
from signposter.scheduler import SchedulerNext


def _next(**overrides) -> LifecycleNext:
    base = dict(
        query_issue=46,
        query_pr=None,
        issue_number=46,
        pr_number=None,
        issue_state="OPEN",
        workflow_state="state:active",
        pr_state=None,
        worktree_exists=True,
        local_branch_exists=True,
        prompt_exists=True,
        worker_summary_exists=False,
        preflight=LifecyclePreflight(
            labels_status="pass",
            sync_status="up-to-date",
            worktree_status="clean",
        ),
        blocked_next_action=None,
        action="execute-worker",
        command="signposter run --repo ExatronOmega/signposter --issue 46 --execute --worktree",
        status="actionable",
        reason="active issue has a prompt but no worker summary",
        notes=["Read-only recommendation only."],
    )
    base.update(overrides)
    return LifecycleNext(**base)


def test_orchestrator_next_blocks_execute_without_flag() -> None:
    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.status == "blocked"
    assert result.would_execute is True
    assert result.would_mutate is False
    assert result.stop_reason == "OpenClaw execution requires explicit --execute"


def test_orchestrator_next_allows_execute_planning_with_flag() -> None:
    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()):
        result = plan_orchestrator_next(
            "ExatronOmega/signposter",
            issue=46,
            allow_execute=True,
        )

    assert result.status == "actionable"
    assert result.stop_reason is None
    assert result.would_execute is True


def test_orchestrator_next_marks_mutating_lifecycle_action() -> None:
    lifecycle_next = _next(
        workflow_state="state:ready",
        prompt_exists=False,
        action="create-worktree",
        command="signposter worktree apply --repo ExatronOmega/signposter --issue 46 --apply",
        reason="ready issue has no local worktree",
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.status == "actionable"
    assert result.would_mutate is True
    assert result.would_execute is False
    assert result.stop_reason is None


def test_orchestrator_next_preserves_blocked_preflight_reason() -> None:
    lifecycle_next = _next(
        action="inspect-working-tree",
        command="git status --short --branch",
        status="blocked",
        reason="local working tree must be clean before lifecycle mutation",
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.status == "blocked"
    assert result.stop_reason == "local working tree must be clean before lifecycle mutation"


def test_orchestrator_next_formats_complete_lifecycle() -> None:
    lifecycle_next = _next(
        issue_state="CLOSED",
        workflow_state="state:merged",
        pr_number=45,
        pr_state="MERGED",
        worktree_exists=False,
        local_branch_exists=False,
        prompt_exists=True,
        worker_summary_exists=True,
        action="none",
        command="(none)",
        status="complete",
        reason="lifecycle already complete",
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        planned = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    out = format_orchestrator_next(planned)

    assert "Signposter Orchestrator Next — Issue #46" in out
    assert "action: none" in out
    assert "Status:\n  complete" in out
    assert "No lifecycle command was executed." in out


def test_cli_orchestrator_next_rejects_missing_target(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "orchestrator", "next", "--repo", "ExatronOmega/signposter"],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "exactly one of --issue or --pr is required" in captured.err


def test_cli_orchestrator_next_uses_read_only_surface(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "orchestrator",
            "next",
            "--repo",
            "ExatronOmega/signposter",
            "--issue",
            "46",
        ],
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()):
        planned = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    with patch("signposter.cli.plan_orchestrator_next", return_value=planned):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Signposter Orchestrator Next — Issue #46" in captured.out
    assert "OpenClaw execution requires explicit --execute" in captured.out
    assert "No GitHub mutation was performed." in captured.out


def test_orchestrator_step_dry_run_does_not_execute() -> None:
    lifecycle_next = _next(
        workflow_state="state:ready",
        action="create-worktree",
        command="signposter worktree apply --repo ExatronOmega/signposter --issue 46 --apply",
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_step("ExatronOmega/signposter", issue=46)

    assert result.status == "ready"
    assert result.applied is False
    assert result.stop_reason == "dry-run; rerun with --apply to execute this step"


def test_orchestrator_step_apply_runs_allowlisted_command() -> None:
    lifecycle_next = _next(
        workflow_state="state:ready",
        action="create-worktree",
        command="signposter worktree apply --repo ExatronOmega/signposter --issue 46 --apply",
    )
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        run_command = Mock(return_value=proc)
        result = run_orchestrator_step(
            "ExatronOmega/signposter",
            issue=46,
            apply=True,
            run_command=run_command,
        )

    assert result.status == "applied"
    assert result.applied is True
    run_command.assert_called_once()
    command = run_command.call_args.args[0]
    assert command[:2] == [sys.executable, "-c"]
    assert command[-5:] == [
        "worktree",
        "apply",
        "--repo",
        "ExatronOmega/signposter",
        "--issue",
        "46",
        "--apply",
    ][-5:]


def test_orchestrator_step_allows_write_prompt_action() -> None:
    lifecycle_next = _next(
        workflow_state="state:active",
        action="write-prompt",
        command="signposter run --repo ExatronOmega/signposter --issue 46 --write-prompt",
    )
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_step(
            "ExatronOmega/signposter",
            issue=46,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.status == "applied"
    assert result.applied is True


def test_orchestrator_step_blocks_execute_without_flag() -> None:
    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()):
        result = run_orchestrator_step(
            "ExatronOmega/signposter",
            issue=46,
            apply=True,
        )

    assert result.status == "blocked"
    assert result.stop_reason == "OpenClaw execution requires explicit --execute"


def test_orchestrator_loop_stops_after_dry_run_step() -> None:
    lifecycle_next = _next(
        workflow_state="state:ready",
        action="create-worktree",
        command="signposter worktree apply --repo ExatronOmega/signposter --issue 46 --apply",
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_loop("ExatronOmega/signposter", issue=46, max_cycles=3)

    assert result.status == "stopped"
    assert result.cycles_run == 1
    assert result.stop_reason == "dry-run; rerun with --apply to execute this step"


def test_orchestrator_loop_stops_at_cycle_limit_after_applied_steps() -> None:
    lifecycle_next = _next(
        workflow_state="state:ready",
        action="create-worktree",
        command="signposter worktree apply --repo ExatronOmega/signposter --issue 46 --apply",
    )
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_loop(
            "ExatronOmega/signposter",
            issue=46,
            max_cycles=2,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.status == "limit-reached"
    assert result.cycles_run == 2
    assert result.stop_reason == "max cycles reached"


def test_orchestrator_tail_delegates_to_pr_lifecycle_next() -> None:
    lifecycle_next = _next(query_issue=None, query_pr=47, issue_number=46, pr_number=47)

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next) as plan:
        result = plan_orchestrator_tail("ExatronOmega/signposter", pr=47)

    assert result.lifecycle.pr_number == 47
    plan.assert_called_once_with("ExatronOmega/signposter", issue=None, pr=47)


def test_orchestrator_run_next_plans_scheduler_selected_issue() -> None:
    issue = LabeledItem(
        number=55,
        title="Issue 55",
        html_url="https://github.com/example/repo/issues/55",
        labels=["state:ready"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="ready",
        issue=issue,
        reason="first ready",
        skipped=[],
        notes=[],
    )
    lifecycle_next = _next(issue_number=55, action="create-worktree")

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = plan_orchestrator_run_next("example/repo")

    assert result.scheduler.issue is not None
    assert result.scheduler.issue.number == 55
    assert result.next is not None
    assert result.next.action == "create-worktree"


def test_orchestrator_run_next_handles_no_scheduler_issue() -> None:
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="none",
        skipped=[],
        notes=[],
    )

    with patch("signposter.orchestrator.select_next_issue", return_value=scheduler):
        result = plan_orchestrator_run_next("example/repo")

    assert result.next is None
    assert result.status == "completed"


def test_format_orchestrator_run_next_contains_selection_and_action() -> None:
    issue = LabeledItem(
        number=55,
        title="Issue 55",
        html_url="https://github.com/example/repo/issues/55",
        labels=["state:ready"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="ready",
        issue=issue,
        reason="first ready",
        skipped=[],
        notes=[],
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
    ):
        result = plan_orchestrator_run_next("example/repo")

    out = format_orchestrator_run_next(result)

    assert "Signposter Orchestrator Run Next" in out
    assert "selected: #55" in out
    assert "action: execute-worker" in out
    assert "No lifecycle command was executed." in out
