from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from signposter.cli import main
from signposter.lifecycle import LifecycleNext, LifecyclePreflight
from signposter.orchestrator import (
    format_orchestrator_next,
    plan_orchestrator_next,
)


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
