from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from signposter.cli import main
from signposter.lifecycle import LifecycleNext, LifecyclePreflight
from signposter.orchestrator import (
    OrchestratorAutonomySmoke,
    OrchestratorRunNextLoop,
    format_orchestrator_loop_summary,
    format_orchestrator_next,
    format_orchestrator_run_next,
    format_orchestrator_run_next_loop,
    format_orchestrator_run_next_loop_summary,
    format_orchestrator_run_next_summary,
    format_orchestrator_step,
    plan_orchestrator_next,
    plan_orchestrator_run_next,
    plan_orchestrator_tail,
    run_orchestrator_autonomy_smoke,
    run_orchestrator_loop,
    run_orchestrator_run_next,
    run_orchestrator_run_next_loop,
    run_orchestrator_step,
    write_orchestrator_run_next_loop_transcript,
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
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=None),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.status == "blocked"
    assert result.would_execute is True
    assert result.would_mutate is False
    assert result.stop_reason == "Execution backend requires explicit --execute"


def test_orchestrator_next_allows_execute_planning_with_flag() -> None:
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=None),
    ):
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

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=None),
    ):
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

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=None),
    ):
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

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=None),
    ):
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

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=None),
    ):
        planned = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    with patch("signposter.cli.plan_orchestrator_next", return_value=planned):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Signposter Orchestrator Next — Issue #46" in captured.out
    assert "Execution backend requires explicit --execute" in captured.out
    assert "No GitHub mutation was performed." in captured.out


def test_orchestrator_next_plans_resume_existing_worktree_for_stale_active_issue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.takeover_category == "resume-existing-worktree"
    assert "existing worktree" in (result.takeover_reason or "")


def test_format_orchestrator_next_includes_resume_takeover_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    output = format_orchestrator_next(result)

    assert "Takeover plan:" in output
    assert "preserve evidence: keep existing raw, summary, prompt, branch" in output
    assert "resume path: resume existing worktree and prompt" in output
    assert "manual fallback: write a manual worker summary" in output
    assert "mutation policy: this plan is read-only" in output
    assert "Takeover output contract:" in output
    assert "status: takeover planned" in output
    assert "order: inspect evidence, resume when safe, then use bounded manual fallback" in output
    assert "gate: validate/report/gate must run before completion" in output
    assert "Recovery summary:" in output
    assert "category: resume-existing-worktree" in output
    assert (
        "next: signposter run --repo ExatronOmega/signposter "
        "--issue 46 --execute --worktree"
    ) in output
    assert "safety: read-only; apply/execute flags still required" in output


def test_orchestrator_next_surfaces_active_issue_activity_age(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2000-01-01T00:00:00Z",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    output = format_orchestrator_next(result)

    assert result.activity_updated_at == "2000-01-01T00:00:00Z"
    assert (result.activity_age or "").startswith("stale(")
    assert "activity updated at: 2000-01-01T00:00:00Z" in output
    assert "activity age: stale(" in output
    assert "Takeover plan:" in output


def test_orchestrator_next_plans_regenerate_prompt_for_stale_active_issue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    lifecycle_next = _next(prompt_exists=False, worktree_exists=True)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.takeover_category == "regenerate-prompt"


def test_orchestrator_next_plans_manual_fallback_for_stale_prompt_without_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    lifecycle_next = _next(prompt_exists=True, worktree_exists=False, local_branch_exists=False)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.takeover_category == "manual-worker-fallback"


def test_format_orchestrator_next_includes_manual_fallback_takeover_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    lifecycle_next = _next(prompt_exists=True, worktree_exists=False, local_branch_exists=False)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    output = format_orchestrator_next(result)

    assert "Takeover plan:" in output
    assert "resume path: repair or recreate worktree before continuing implementation" in output
    assert (
        "manual fallback: use the existing prompt to write a bounded manual worker summary"
        in output
    )


def test_orchestrator_next_plans_inspect_blocker_for_stale_active_issue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    lifecycle_next = _next(
        prompt_exists=False,
        worktree_exists=False,
        local_branch_exists=False,
        action="write-prompt",
        command="signposter run --repo ExatronOmega/signposter --issue 46 --write-prompt",
    )
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.takeover_category == "inspect-blocker"
    assert "lacks a safe resume path" in (result.takeover_reason or "")


def test_orchestrator_next_degrades_when_issue_fetch_fails() -> None:
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch(
            "signposter.orchestrator.fetch_issue_by_number",
            side_effect=RuntimeError("temporary GitHub failure"),
        ),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.takeover_category is None
    assert result.takeover_reason is None


def test_orchestrator_next_uses_local_artifact_freshness_before_issue_updated_at(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runs = tmp_path / "artifacts" / "runs"
    runs.mkdir(parents=True)
    raw = runs / "issue-46-worker.raw.txt"
    raw.write_text("recent worker output", encoding="utf-8")
    now = datetime.now(UTC).timestamp()
    os.utime(raw, (now, now))
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.takeover_category is None
    assert result.takeover_reason is None


def test_orchestrator_next_detects_missing_worker_summary_after_runtime_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runs = tmp_path / "artifacts" / "runs"
    runs.mkdir(parents=True)
    (runs / "issue-46-worker.codex-runtime.summary.md").write_text(
        "Status: unsupported-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at=datetime.now(UTC).isoformat(),
    )

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.takeover_category == "missing-worker-artifact"
    assert "preserved runtime evidence" in (result.takeover_reason or "")
    assert result.recovery_commands == (
        "signposter artifact write-worker-summary --repo ExatronOmega/signposter "
        "--issue 46 --apply",
        "signposter artifact validate-worker-summary --issue 46",
        "signposter report --repo ExatronOmega/signposter --issue 46 --apply",
        "signposter gate --repo ExatronOmega/signposter --issue 46",
    )


def test_format_orchestrator_next_includes_missing_worker_artifact_takeover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runs = tmp_path / "artifacts" / "runs"
    runs.mkdir(parents=True)
    (runs / "issue-46-worker.codex-runtime.raw.txt").write_text(
        "backend unavailable\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at=datetime.now(UTC).isoformat(),
    )

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    output = format_orchestrator_next(result)

    assert "category: missing-worker-artifact" in output
    assert "resume path: inspect preserved runtime artifacts" in output
    assert "manual fallback: write a bounded manual worker summary" in output
    assert "Takeover output contract:" in output
    assert "evidence: preserve local raw, summary, prompt, branch, and worktree context" in output
    assert "Recovery commands:" in output
    assert "signposter artifact write-worker-summary --repo ExatronOmega/signposter" in output


def test_orchestrator_next_detects_malformed_worker_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runs = tmp_path / "artifacts" / "runs"
    runs.mkdir(parents=True)
    (runs / "issue-46-worker.summary.md").write_text(
        "# incomplete worker summary\nAcceptance: pass\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    lifecycle_next = _next(
        worker_summary_exists=True,
        action="check-gate",
        command="signposter gate --repo ExatronOmega/signposter --issue 46",
        reason="worker evidence exists and gate should be checked",
    )
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at=datetime.now(UTC).isoformat(),
    )

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert result.status == "blocked"
    assert result.takeover_category == "malformed-worker-artifact"
    assert "canonical worker summary is malformed" in (result.takeover_reason or "")
    assert "missing fields:" in (result.takeover_reason or "")
    assert result.recovery_commands == (
        "signposter artifact validate-worker-summary --issue 46",
        "signposter artifact write-worker-summary --repo ExatronOmega/signposter "
        "--issue 46 --apply",
        "signposter artifact validate-worker-summary --issue 46",
        "signposter report --repo ExatronOmega/signposter --issue 46 --apply",
        "signposter gate --repo ExatronOmega/signposter --issue 46",
    )


def test_format_orchestrator_next_includes_malformed_worker_artifact_takeover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runs = tmp_path / "artifacts" / "runs"
    runs.mkdir(parents=True)
    (runs / "issue-46-worker.summary.md").write_text(
        "Status: unsupported-model\nTask execution complete: no\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    lifecycle_next = _next(
        worker_summary_exists=True,
        action="check-gate",
        command="signposter gate --repo ExatronOmega/signposter --issue 46",
        reason="worker evidence exists and gate should be checked",
    )
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at=datetime.now(UTC).isoformat(),
    )

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    output = format_orchestrator_next(result)

    assert "category: malformed-worker-artifact" in output
    assert "resume path: inspect canonical worker summary validation findings" in output
    assert "manual fallback: repair or replace the worker summary" in output
    assert "signposter artifact validate-worker-summary --issue 46" in output
    assert "Status:\n  blocked" in output


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
    assert result.stop_reason == "Execution backend requires explicit --execute"


def test_orchestrator_step_apply_blocks_takeover_until_manual_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )
    run_command = Mock()

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        result = run_orchestrator_step(
            "ExatronOmega/signposter",
            issue=46,
            apply=True,
            execute=True,
            run_command=run_command,
        )

    assert result.status == "blocked"
    assert result.applied is False
    assert result.stop_reason == (
        "takeover plan requires explicit manual recovery before apply: "
        "resume-existing-worktree"
    )
    assert any("Takeover apply guard stopped before running" in note for note in result.notes)
    run_command.assert_not_called()


def test_orchestrator_stuck_state_recovery_smoke_surfaces_takeover_and_ci_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    issue = LabeledItem(
        number=46,
        title="Issue 46",
        html_url="https://github.com/example/repo/issues/46",
        labels=["state:active"],
        item_type="issue",
        updated_at="2026-05-20T00:00:00Z",
    )

    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        planned = plan_orchestrator_next(
            "ExatronOmega/signposter",
            issue=46,
            allow_execute=True,
        )

    assert planned.status == "actionable"
    assert planned.takeover_category == "resume-existing-worktree"
    assert planned.lifecycle.worker_summary_exists is False
    output = format_orchestrator_next(planned)
    assert "Takeover plan:" in output
    assert "resume path: resume existing worktree and prompt" in output
    assert "manual fallback: write a manual worker summary" in output
    assert "mutation policy: this plan is read-only" in output
    assert planned.recovery_commands == (
        "signposter run --repo ExatronOmega/signposter --issue 46 --execute --worktree",
        "signposter artifact write-worker-summary --repo ExatronOmega/signposter "
        "--issue 46 --apply",
        "signposter artifact validate-worker-summary --issue 46",
        "signposter report --repo ExatronOmega/signposter --issue 46 --apply",
        "signposter gate --repo ExatronOmega/signposter --issue 46",
    )
    assert "Recovery commands:" in output
    assert "signposter run --repo ExatronOmega/signposter --issue 46 --execute --worktree" in output
    assert "signposter artifact write-worker-summary --repo ExatronOmega/signposter" in output

    run_command = Mock()
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        step = run_orchestrator_step(
            "ExatronOmega/signposter",
            issue=46,
            apply=True,
            execute=True,
            run_command=run_command,
        )

    assert step.status == "blocked"
    assert step.applied is False
    assert step.stop_reason == (
        "takeover plan requires explicit manual recovery before apply: "
        "resume-existing-worktree"
    )
    assert any(
        "Takeover apply guard stopped before running" in note for note in step.notes
    )
    run_command.assert_not_called()

    ci_blocked = _next(
        issue_number=46,
        pr_number=47,
        pr_state="OPEN",
        worker_summary_exists=True,
        action="merge-pr",
        command=(
            "signposter merge apply --repo ExatronOmega/signposter --pr 47 "
            "--apply"
        ),
        status="blocked",
        reason="blocked — checks are failing",
    )
    with (
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=ci_blocked),
        patch("signposter.orchestrator.fetch_issue_by_number", return_value=issue),
    ):
        blocked_pr = plan_orchestrator_next("ExatronOmega/signposter", issue=46)

    assert blocked_pr.status == "blocked"
    assert blocked_pr.stop_reason == "blocked — checks are failing"
    blocked_output = format_orchestrator_next(blocked_pr)
    assert "blocked — checks are failing" in blocked_output
    assert "No GitHub mutation was performed." in blocked_output


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


def test_orchestrator_pr_tail_loop_runs_bounded_pr_steps() -> None:
    lifecycle_next = _next(
        query_issue=None,
        query_pr=47,
        issue_number=46,
        pr_number=47,
        action="review-pr",
        command="signposter review gate --repo example/repo --pr 47",
    )
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_loop(
            "example/repo",
            pr=47,
            max_cycles=2,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.status == "limit-reached"
    assert result.cycles_run == 2
    assert all(step.next.lifecycle.pr_number == 47 for step in result.steps)


def test_orchestrator_pr_tail_loop_resumes_review_merge_integration_cleanup() -> None:
    lifecycle_steps = [
        _next(
            query_issue=None,
            query_pr=47,
            issue_number=46,
            pr_number=47,
            action="review-pr",
            command="signposter review plan --repo example/repo --pr 47",
        ),
        _next(
            query_issue=None,
            query_pr=47,
            issue_number=46,
            pr_number=47,
            action="merge-pr",
            command="signposter merge apply --repo example/repo --pr 47 --apply",
        ),
        _next(
            query_issue=None,
            query_pr=47,
            issue_number=46,
            pr_number=47,
            pr_state="MERGED",
            action="integrate-issue",
            command="signposter integration apply --repo example/repo --pr 47 --apply",
        ),
        _next(
            query_issue=None,
            query_pr=47,
            issue_number=46,
            pr_number=47,
            pr_state="MERGED",
            action="cleanup",
            command="signposter cleanup apply --repo example/repo --pr 47 --apply",
        ),
    ]
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("signposter.orchestrator.plan_lifecycle_next", side_effect=lifecycle_steps):
        result = run_orchestrator_loop(
            "example/repo",
            pr=47,
            max_cycles=4,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert [step.next.action for step in result.steps] == [
        "review-pr",
        "merge-pr",
        "integrate-issue",
        "cleanup",
    ]
    assert result.status == "limit-reached"


def test_orchestrator_pr_tail_loop_stops_with_bounded_ci_wait() -> None:
    lifecycle_next = _next(
        query_issue=None,
        query_pr=47,
        issue_number=46,
        pr_number=47,
        action="review-pr",
        command="signposter review gate --repo example/repo --pr 47",
    )
    proc = type(
        "Proc",
        (),
        {"returncode": 0, "stdout": "pending — checks are still running", "stderr": ""},
    )()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_loop(
            "example/repo",
            pr=47,
            max_cycles=3,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.status == "stopped"
    assert result.cycles_run == 1
    assert (
        result.stop_reason
        == "ci checks pending; bounded wait reached, rerun tail loop to continue"
    )
    assert result.stop_category == "waiting-ci"


def test_orchestrator_pr_tail_loop_blocks_on_ci_failure() -> None:
    lifecycle_next = _next(
        query_issue=None,
        query_pr=47,
        issue_number=46,
        pr_number=47,
        action="review-pr",
        command="signposter review gate --repo example/repo --pr 47",
    )
    proc = type(
        "Proc",
        (),
        {"returncode": 0, "stdout": "blocked — checks are failing", "stderr": ""},
    )()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_loop(
            "example/repo",
            pr=47,
            max_cycles=3,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.status == "stopped"
    assert result.cycles_run == 1
    assert result.stop_reason == "ci checks failing; stop and inspect review/merge diagnostics"
    assert result.stop_category == "failing-ci"


def test_format_orchestrator_loop_summary_shows_pr_tail_stop() -> None:
    lifecycle_next = _next(
        query_issue=None,
        query_pr=47,
        issue_number=46,
        pr_number=47,
        action="review-pr",
        command="signposter review gate --repo example/repo --pr 47",
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_loop("example/repo", pr=47, max_cycles=1)

    out = format_orchestrator_loop_summary(result)

    assert out.splitlines() == [
        "Signposter Orchestrator Loop Summary",
        "target: pr #47",
        "action: review-pr",
        "status: stopped",
        "stop: dry-run; rerun with --apply to execute this step",
        "stop_category: blocked-lifecycle",
        "steps: 1",
    ]


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
    assert result.step is None


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


def test_orchestrator_run_next_uses_manifest_active_task_before_scheduler_ready(
    tmp_path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        """
{
  "version": "planner.seed-manifest.v0.1",
  "repo": "example/repo",
  "status": "applied",
  "issues": [
    {
      "key": "H049-028",
      "title": "H049-028",
      "labels": ["phase:build"],
      "depends_on": [],
      "github_issue": 235,
      "github_url": "https://github.com/example/repo/issues/235"
    },
    {
      "key": "H049-032",
      "title": "H049-032",
      "labels": ["phase:build"],
      "depends_on": [],
      "github_issue": 239,
      "github_url": "https://github.com/example/repo/issues/239"
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    scheduler_ready = SchedulerNext(
        repo="example/repo",
        status="ready",
        issue=LabeledItem(239, "Issue 239", "url", ["state:ready"], "issue"),
        reason="scheduler saw a later ready task",
        skipped=[],
        notes=[],
    )
    lifecycle_next = _next(issue_number=235, action="write-prompt")

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler_ready),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = plan_orchestrator_run_next(
            "example/repo",
            manifest_path=manifest,
            sync_github=True,
            run_command=Mock(
                side_effect=[
                    type(
                        "Proc",
                        (),
                        {
                            "returncode": 0,
                            "stdout": '{"state":"OPEN","labels":[{"name":"state:active"}]}',
                            "stderr": "",
                        },
                    )(),
                    type(
                        "Proc",
                        (),
                        {
                            "returncode": 0,
                            "stdout": '{"state":"OPEN","labels":[{"name":"state:ready"}]}',
                            "stderr": "",
                        },
                    )(),
                ]
            ),
        )

    assert result.scheduler.issue is not None
    assert result.scheduler.issue.number == 235
    assert result.selection_source == "planner-manifest"
    assert "one active task H049-028" in result.selection_reason
    assert result.next is not None
    assert result.next.lifecycle.issue_number == 235


def test_format_orchestrator_run_next_shows_manifest_active_task_hint(
    tmp_path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        """
{
  "version": "planner.seed-manifest.v0.1",
  "repo": "example/repo",
  "status": "applied",
  "issues": [
    {
      "key": "H049-028",
      "title": "H049-028",
      "labels": ["phase:build"],
      "depends_on": [],
      "github_issue": 235,
      "github_url": "https://github.com/example/repo/issues/235"
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    lifecycle_next = _next(
        issue_number=235,
        action="write-prompt",
        command="signposter run --repo example/repo --issue 235 --write-prompt",
    )

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = plan_orchestrator_run_next(
            "example/repo",
            manifest_path=manifest,
            sync_github=True,
            run_command=Mock(
                return_value=type(
                    "Proc",
                    (),
                    {
                        "returncode": 0,
                        "stdout": '{"state":"OPEN","labels":[{"name":"state:active"}]}',
                        "stderr": "",
                    },
                )()
            ),
        )

    output = format_orchestrator_run_next(result)

    assert "Active task hint:" in output
    assert "source: planner manifest active task" in output
    assert "issue: #235" in output
    assert "resume this active task before selecting another ready task" in output
    assert "command: signposter run --repo example/repo --issue 235 --write-prompt" in output


def test_orchestrator_run_next_loop_uses_manifest_active_task(
    tmp_path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        """
{
  "version": "planner.seed-manifest.v0.1",
  "repo": "example/repo",
  "status": "applied",
  "issues": [
    {
      "key": "H049-028",
      "title": "H049-028",
      "labels": [],
      "depends_on": [],
      "github_issue": 235,
      "github_url": "https://github.com/example/repo/issues/235"
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    lifecycle_next = _next(issue_number=235, action="write-prompt")
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            manifest_path=manifest,
            sync_github=True,
            max_cycles=1,
            apply=True,
            run_command=Mock(
                side_effect=[
                    type(
                        "Proc",
                        (),
                        {
                            "returncode": 0,
                            "stdout": '{"state":"OPEN","labels":[{"name":"state:active"}]}',
                            "stderr": "",
                        },
                    )(),
                    proc,
                ]
            ),
        )

    assert result.steps[0].next.lifecycle.issue_number == 235
    assert result.selection_source == "planner-manifest"
    assert result.selected_issue == 235


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
    assert "source: github-scheduler" in out
    assert "manifest: not provided" in out
    assert "selected: #55" in out
    assert "action: execute-worker" in out
    assert "No lifecycle command was executed." in out


def test_format_orchestrator_run_next_summary_is_concise() -> None:
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

    out = format_orchestrator_run_next_summary(result)

    assert out.splitlines() == [
        "Signposter Automation Summary",
        "selected: #55",
        "source: github-scheduler",
        "action: execute-worker",
        "status: blocked",
        "stop: Execution backend requires explicit --execute",
        "recovery: execution-requires-explicit-execute",
    ]
    assert "command:" not in out
    assert "Notes:" not in out


def test_orchestrator_run_next_apply_runs_one_step() -> None:
    issue = LabeledItem(
        number=56,
        title="Issue 56",
        html_url="https://github.com/example/repo/issues/56",
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
    lifecycle_next = _next(
        issue_number=56,
        action="create-worktree",
        command="signposter worktree apply --repo example/repo --issue 56 --apply",
    )
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = run_orchestrator_run_next(
            "example/repo",
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.step is not None
    assert result.step.status == "applied"
    assert result.status == "applied"


def test_orchestrator_run_next_apply_blocks_execute_without_flag() -> None:
    issue = LabeledItem(
        number=56,
        title="Issue 56",
        html_url="https://github.com/example/repo/issues/56",
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
        result = run_orchestrator_run_next("example/repo", apply=True)

    assert result.step is not None
    assert result.step.status == "blocked"
    assert result.status == "blocked"
    assert result.step.stop_reason == "Execution backend requires explicit --execute"


def test_orchestrator_run_next_loop_continues_selected_issue_after_claim() -> None:
    issue = LabeledItem(
        number=57,
        title="Issue 57",
        html_url="https://github.com/example/repo/issues/57",
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
    lifecycle_steps = [
        _next(
            issue_number=57,
            action="claim-issue",
            command="signposter run --repo example/repo --issue 57 --claim",
        ),
        _next(
            issue_number=57,
            action="write-prompt",
            command="signposter run --repo example/repo --issue 57 --write-prompt",
        ),
        _next(issue_number=57),
    ]
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", side_effect=lifecycle_steps),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=3,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.cycles_run == 3
    assert result.tasks_started == 1
    assert [step.next.action for step in result.steps] == [
        "claim-issue",
        "write-prompt",
        "execute-worker",
    ]
    assert result.status == "stopped"
    assert result.stop_reason == "Execution backend requires explicit --execute"


def test_orchestrator_run_next_loop_resumes_single_active_issue() -> None:
    active = LabeledItem(
        number=57,
        title="Issue 57",
        html_url="https://github.com/example/repo/issues/57",
        labels=["state:active"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=[],
        notes=[],
    )
    lifecycle_next = _next(
        issue_number=57,
        action="write-prompt",
        command="signposter run --repo example/repo --issue 57 --write-prompt",
    )
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active]),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=1,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.steps[0].next.lifecycle.issue_number == 57
    assert result.steps[0].status == "applied"
    assert result.status == "limit-reached"


def test_orchestrator_run_next_loop_resumes_single_done_issue_tail() -> None:
    done = LabeledItem(
        number=61,
        title="Issue 61",
        html_url="https://github.com/example/repo/issues/61",
        labels=["state:done"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=[],
        notes=[],
    )
    lifecycle_steps = [
        _next(
            issue_number=61,
            workflow_state="state:done",
            worker_summary_exists=True,
            pr_number=90,
            pr_state="OPEN",
            action="review-pr",
            command="signposter review plan --repo example/repo --pr 90",
            reason="PR is open and not approved",
        ),
        _next(
            issue_number=61,
            workflow_state="state:done",
            worker_summary_exists=True,
            pr_number=90,
            pr_state="OPEN",
            action="review-pr",
            command="signposter review plan --repo example/repo --pr 90",
            reason="PR is open and not approved",
        ),
        _next(
            issue_number=61,
            workflow_state="state:done",
            worker_summary_exists=True,
            pr_number=90,
            pr_state="OPEN",
            action="merge-pr",
            command="signposter merge apply --repo example/repo --pr 90 --apply",
            reason="PR is approved and may be mergeable",
        ),
    ]
    proc = type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[done]),
        patch("signposter.orchestrator.plan_lifecycle_next", side_effect=lifecycle_steps),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=2,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert [step.next.action for step in result.steps] == ["review-pr", "merge-pr"]
    assert result.tasks_started == 1
    assert result.status == "limit-reached"


def test_orchestrator_run_next_loop_blocks_multiple_active_issues() -> None:
    active_1 = LabeledItem(1, "One", "url", ["state:active"], "issue")
    active_2 = LabeledItem(2, "Two", "url", ["state:active"], "issue")
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=[],
        notes=[],
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active_1, active_2]),
    ):
        result = run_orchestrator_run_next_loop("example/repo", max_cycles=2, apply=True)

    assert result.steps == []
    assert result.status == "stopped"
    assert result.stop_reason == "multiple active issues require explicit --issue: #1, #2"
    assert result.stop_category == "active-ambiguity"
    assert result.stop_tolerated is False


def test_orchestrator_run_next_loop_blocks_multiple_done_issue_tails() -> None:
    done_1 = LabeledItem(61, "Done 61", "url", ["state:done"], "issue")
    done_2 = LabeledItem(62, "Done 62", "url", ["state:done"], "issue")
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=[],
        notes=[],
    )
    done_tail = _next(
        workflow_state="state:done",
        worker_summary_exists=True,
        pr_number=90,
        pr_state="OPEN",
        action="review-pr",
        command="signposter review plan --repo example/repo --pr 90",
        reason="PR is open and not approved",
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[done_1, done_2]),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=done_tail),
    ):
        result = run_orchestrator_run_next_loop("example/repo", max_cycles=2, apply=True)

    assert result.steps == []
    assert result.status == "stopped"
    assert result.stop_reason == "multiple resumable done issues require explicit --issue: #61, #62"


def test_orchestrator_run_next_loop_ignores_completed_done_issue() -> None:
    done = LabeledItem(61, "Done 61", "url", ["state:done"], "issue")
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=[],
        notes=[],
    )
    lifecycle_complete = _next(
        issue_number=61,
        workflow_state="state:done",
        worker_summary_exists=True,
        pr_number=90,
        pr_state="MERGED",
        action="none",
        command="(none)",
        status="complete",
        reason="lifecycle already complete",
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[done]),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_complete),
    ):
        result = run_orchestrator_run_next_loop("example/repo", max_cycles=1, apply=True)

    assert result.steps == []
    assert result.status == "stopped"
    assert result.stop_reason == "no open dependency-clear state:ready issue found"
    assert result.stop_category == "no-ready"


def test_orchestrator_run_next_loop_can_tolerate_active_ambiguity() -> None:
    active_1 = LabeledItem(1, "One", "url", ["state:active"], "issue")
    active_2 = LabeledItem(2, "Two", "url", ["state:active"], "issue")
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=[],
        notes=[],
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active_1, active_2]),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=2,
            apply=True,
            tolerate_active_ambiguity=True,
        )

    assert result.status == "completed"
    assert result.stop_category == "active-ambiguity"
    assert result.stop_tolerated is True


def test_orchestrator_run_next_loop_dry_run_never_executes_command() -> None:
    issue = LabeledItem(
        number=59,
        title="Issue 59",
        html_url="https://github.com/example/repo/issues/59",
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
    lifecycle_next = _next(
        issue_number=59,
        action="create-worktree",
        command="signposter worktree apply --repo example/repo --issue 59 --apply",
    )
    run_command = Mock()

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=3,
            run_command=run_command,
        )

    assert result.cycles_run == 1
    assert result.status == "stopped"
    assert result.stop_reason == "dry-run; rerun with --apply to execute this step"
    assert result.stop_category == "blocked-lifecycle"
    assert result.stop_tolerated is False
    run_command.assert_not_called()


def test_orchestrator_run_next_loop_can_tolerate_blocked_lifecycle() -> None:
    issue = LabeledItem(
        number=59,
        title="Issue 59",
        html_url="https://github.com/example/repo/issues/59",
        labels=["state:ready"],
        item_type="issue",
    )
    scheduler = SchedulerNext("example/repo", "ready", issue, "first ready", [], [])
    lifecycle_next = _next(
        issue_number=59,
        action="create-worktree",
        command="signposter worktree apply --repo example/repo --issue 59 --apply",
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=1,
            tolerate_blocked_lifecycle=True,
        )

    assert result.status == "completed"
    assert result.stop_category == "blocked-lifecycle"
    assert result.stop_tolerated is True


def test_orchestrator_run_next_loop_stops_after_step_failure() -> None:
    issue = LabeledItem(
        number=59,
        title="Issue 59",
        html_url="https://github.com/example/repo/issues/59",
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
    lifecycle_next = _next(
        issue_number=59,
        action="create-worktree",
        command="signposter worktree apply --repo example/repo --issue 59 --apply",
    )
    proc = type("Proc", (), {"returncode": 2, "stdout": "", "stderr": "boom"})()

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=3,
            apply=True,
            run_command=Mock(return_value=proc),
        )

    assert result.cycles_run == 1
    assert result.status == "stopped"
    assert result.stop_reason == "step command failed"
    assert result.stop_category == "failed-step"
    assert result.stop_tolerated is False


def test_orchestrator_run_next_loop_can_tolerate_failed_step() -> None:
    issue = LabeledItem(59, "Issue 59", "url", ["state:ready"], "issue")
    scheduler = SchedulerNext("example/repo", "ready", issue, "first ready", [], [])
    lifecycle_next = _next(
        issue_number=59,
        action="create-worktree",
        command="signposter worktree apply --repo example/repo --issue 59 --apply",
    )
    proc = type("Proc", (), {"returncode": 2, "stdout": "", "stderr": "boom"})()

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=1,
            apply=True,
            tolerate_failed_step=True,
            run_command=Mock(return_value=proc),
        )

    assert result.status == "completed"
    assert result.stop_category == "failed-step"
    assert result.stop_tolerated is True


def test_orchestrator_run_next_loop_can_tolerate_no_ready() -> None:
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="no open dependency-clear state:ready issue found",
        skipped=[],
        notes=[],
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[]),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=1,
            tolerate_no_ready=True,
        )

    assert result.status == "completed"
    assert result.stop_category == "no-ready"
    assert result.stop_tolerated is True


def test_orchestrator_run_next_loop_enforces_max_tasks() -> None:
    issue_1 = LabeledItem(59, "Issue 59", "url", ["state:ready"], "issue")
    issue_2 = LabeledItem(60, "Issue 60", "url", ["state:ready"], "issue")
    schedulers = [
        SchedulerNext("example/repo", "ready", issue_1, "first ready", [], []),
        SchedulerNext("example/repo", "ready", issue_2, "next ready", [], []),
    ]
    lifecycle_complete = _next(
        issue_number=59,
        action="none",
        command="(none)",
        status="complete",
    )

    with (
        patch("signposter.orchestrator.select_next_issue", side_effect=schedulers),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_complete),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=3,
            max_tasks=1,
            apply=True,
        )

    assert result.cycles_run == 1
    assert result.tasks_started == 2
    assert result.status == "limit-reached"
    assert result.stop_reason == "max tasks reached"


def test_format_orchestrator_run_next_loop_contains_limits_and_steps() -> None:
    active = LabeledItem(
        number=57,
        title="Issue 57",
        html_url="https://github.com/example/repo/issues/57",
        labels=["state:active"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="none",
        skipped=[],
        notes=[],
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active]),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next(issue_number=57)),
    ):
        result = run_orchestrator_run_next_loop("example/repo", max_cycles=1, apply=True)

    out = format_orchestrator_run_next_loop(result)

    assert "Signposter Orchestrator Run Next Loop" in out
    assert "cycles requested: 1" in out
    assert "Guard audit:" in out
    assert "max cycles: enforced" in out
    assert "max tasks: enforced" in out
    assert "apply required: yes" in out
    assert "execute required for backend: yes" in out
    assert "1. issue #57: execute-worker -> blocked" in out
    assert "stop category: blocked-lifecycle" in out
    assert "stop tolerated: no" in out
    assert "category: blocked-lifecycle" in out
    assert "tolerated: no" in out


def test_format_orchestrator_run_next_loop_summary_is_concise() -> None:
    active = LabeledItem(
        number=57,
        title="Issue 57",
        html_url="https://github.com/example/repo/issues/57",
        labels=["state:active"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="none",
        skipped=[],
        notes=[],
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active]),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next(issue_number=57)),
    ):
        result = run_orchestrator_run_next_loop("example/repo", max_cycles=1, apply=True)

    out = format_orchestrator_run_next_loop_summary(result)

    assert out.splitlines() == [
        "Signposter Automation Summary",
        "selected: #57",
        "source: github-active-resume",
        "action: execute-worker",
        "status: stopped",
        "stop: Execution backend requires explicit --execute",
        "recovery: execution-requires-explicit-execute",
        "stop_category: blocked-lifecycle",
        "stop_tolerated: no",
        "steps: 1",
    ]
    assert "command:" not in out
    assert "Notes:" not in out


def test_write_orchestrator_run_next_loop_transcript_is_local_and_bounded(tmp_path) -> None:
    active = LabeledItem(
        number=57,
        title="Issue 57",
        html_url="https://github.com/example/repo/issues/57",
        labels=["state:active"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="none",
        skipped=[],
        notes=[],
    )

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active]),
        patch(
            "signposter.orchestrator.plan_lifecycle_next",
            return_value=_next(issue_number=57),
        ),
    ):
        result = run_orchestrator_run_next_loop("example/repo", max_cycles=1, apply=True)

    path = write_orchestrator_run_next_loop_transcript(
        result,
        tmp_path / "runs" / "loop.txt",
    )

    out = path.read_text(encoding="utf-8")
    assert path.exists()
    assert "Signposter Automation Summary" in out
    assert "selected: #57" in out
    assert "action: execute-worker" in out
    assert "status: stopped" in out
    assert "stop: Execution backend requires explicit --execute" in out
    assert "1. selected=#57 action=execute-worker status=blocked" in out
    assert "local artifact only" in out
    assert "no GitHub mutation was performed by transcript writing" in out


def test_orchestrator_step_extracts_execute_diagnosis_from_summary_artifact(tmp_path) -> None:
    lifecycle_next = _next(
        issue_number=57,
        action="execute-worker",
        command="signposter run --repo example/repo --issue 57 --execute --worktree",
    )
    summary = tmp_path / "issue-57-worker.summary.md"
    summary.write_text(
        "\n".join(
            [
                "# Signposter Execution Summary",
                "**Execution Status:** runtime-stall",
                (
                    "**Execution Reason:** Codex CLI runtime stalled without producing "
                    "a usable bounded result."
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proc = type(
        "Proc",
        (),
        {
            "returncode": 1,
            "stdout": (
                "Execution completed for issue #57\n"
                "  Exit code: 1\n"
                "  Raw output: artifacts/runs/issue-57-worker.raw.txt\n"
                f"  Summary:   {summary}\n"
            ),
            "stderr": "",
        },
    )()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_step(
            "example/repo",
            issue=57,
            apply=True,
            execute=True,
            run_command=Mock(return_value=proc),
        )

    assert result.status == "failed"
    assert result.diagnosis_status == "runtime-stall"
    assert "stalled" in (result.diagnosis_reason or "")
    assert result.raw_artifact_path == "artifacts/runs/issue-57-worker.raw.txt"
    assert result.summary_artifact_path == str(summary)
    assert result.fallback_commands == (
        "signposter artifact write-worker-summary --repo example/repo --issue 57 --apply",
        "signposter artifact validate-worker-summary --issue 57",
        "signposter report --repo example/repo --issue 57 --apply",
        "signposter gate --repo example/repo --issue 57",
    )


def test_orchestrator_step_plans_worker_fallback_for_unsupported_model(tmp_path) -> None:
    lifecycle_next = _next(
        issue_number=58,
        action="execute-worker",
        command="signposter run --repo example/repo --issue 58 --execute --worktree",
    )
    summary = tmp_path / "issue-58-worker.summary.md"
    summary.write_text(
        "\n".join(
            [
                "# Signposter Execution Summary",
                "**Execution Status:** unsupported-model",
                "**Execution Reason:** Selected model is not available in Codex CLI runtime.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proc = type(
        "Proc",
        (),
        {
            "returncode": 1,
            "stdout": (
                "Execution completed for issue #58\n"
                "  Exit code: 1\n"
                f"  Summary:   {summary}\n"
            ),
            "stderr": "",
        },
    )()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_step(
            "example/repo",
            issue=58,
            apply=True,
            execute=True,
            run_command=Mock(return_value=proc),
        )

    out = format_orchestrator_step(result)

    assert result.diagnosis_status == "unsupported-model"
    assert "Takeover guidance:" in out
    assert "inspect raw and summary artifacts" in out
    assert "resume the existing worktree" in out
    assert "Fallback next commands:" in out
    assert "signposter artifact write-worker-summary --repo example/repo --issue 58 --apply" in out
    assert "signposter gate --repo example/repo --issue 58" in out


def test_orchestrator_step_extracts_codex_cli_summary_status_format(tmp_path) -> None:
    lifecycle_next = _next(
        issue_number=59,
        action="execute-worker",
        command="signposter run --repo example/repo --issue 59 --execute --worktree",
    )
    summary = tmp_path / "issue-59-worker.summary.md"
    summary.write_text(
        "\n".join(
            [
                "# Signposter Codex CLI Execution Summary",
                "**Backend:** codex-cli",
                "**Status:** unsupported-model",
                "**Reason:** Codex CLI exited with code 1; classified as unsupported-model.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proc = type(
        "Proc",
        (),
        {
            "returncode": 1,
            "stdout": (
                "Execution completed for issue #59\n"
                "  Exit code: 1\n"
                "  Raw output: artifacts/runs/issue-59-worker.raw.txt\n"
                f"  Summary:   {summary}\n"
            ),
            "stderr": "",
        },
    )()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_step(
            "example/repo",
            issue=59,
            apply=True,
            execute=True,
            run_command=Mock(return_value=proc),
        )

    out = format_orchestrator_step(result)

    assert result.diagnosis_status == "unsupported-model"
    assert "classified as unsupported-model" in (result.diagnosis_reason or "")
    assert "raw artifact: artifacts/runs/issue-59-worker.raw.txt" in out
    assert "summary artifact:" in out
    assert "Takeover guidance:" in out
    assert "signposter artifact write-worker-summary --repo example/repo --issue 59 --apply" in out


def test_orchestrator_step_plans_review_fallback_for_runtime_stall(tmp_path) -> None:
    lifecycle_next = _next(
        query_issue=None,
        query_pr=47,
        issue_number=46,
        pr_number=47,
        action="review-pr",
        command="signposter review execute --repo example/repo --pr 47",
    )
    summary = tmp_path / "pr-47-reviewer.summary.md"
    summary.write_text(
        "\n".join(
            [
                "# Signposter Reviewer Summary",
                "**Execution Status:** runtime-stall",
                (
                    "**Execution Reason:** Codex CLI runtime stalled without producing "
                    "a usable bounded result."
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proc = type(
        "Proc",
        (),
        {
            "returncode": 1,
            "stdout": (
                "Review execution completed for PR #47\n"
                f"  Summary:   {summary}\n"
            ),
            "stderr": "",
        },
    )()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle_next):
        result = run_orchestrator_step(
            "example/repo",
            pr=47,
            apply=True,
            execute=True,
            run_command=Mock(return_value=proc),
        )

    assert result.fallback_commands == (
        "signposter artifact write-review-summary --pr 47 --apply",
        "signposter review validate-artifact --pr 47",
        "signposter review gate --repo example/repo --pr 47",
    )


def test_write_orchestrator_transcript_includes_execute_diagnosis(tmp_path) -> None:
    step = type(
        "Step",
        (),
        {
            "next": type(
                "Next",
                (),
                {"lifecycle": _next(issue_number=57), "action": "execute-worker"},
            )(),
            "status": "failed",
            "stop_reason": "step command failed",
            "diagnosis_status": "timeout",
            "diagnosis_reason": (
                "Codex CLI execution exceeded the bounded subprocess timeout (25s)."
            ),
            "raw_artifact_path": "artifacts/runs/issue-57-worker.raw.txt",
            "summary_artifact_path": "artifacts/runs/issue-57-worker.summary.md",
            "fallback_commands": (
                "signposter artifact write-worker-summary --repo example/repo --issue 57 --apply",
                "signposter artifact validate-worker-summary --issue 57",
            ),
        },
    )()
    result = OrchestratorRunNextLoop(
        status="completed",
        cycles_requested=1,
        cycles_run=1,
        max_tasks=1,
        tasks_started=1,
        selected_issue=57,
        steps=[step],
        stop_reason="step command failed",
        notes=[],
        stop_category="failed-step",
        stop_tolerated=True,
    )

    path = write_orchestrator_run_next_loop_transcript(result, tmp_path / "runs" / "loop.txt")
    out = path.read_text(encoding="utf-8")

    assert "diagnosis_status=timeout" in out
    assert (
        "diagnosis_reason=Codex CLI execution exceeded the bounded subprocess timeout (25s)."
        in out
    )
    assert "raw_artifact=artifacts/runs/issue-57-worker.raw.txt" in out
    assert "summary_artifact=artifacts/runs/issue-57-worker.summary.md" in out
    assert (
        "fallback_command=signposter artifact write-worker-summary "
        "--repo example/repo --issue 57 --apply"
        in out
    )


def test_format_orchestrator_run_next_loop_shows_fallback_commands(tmp_path) -> None:
    lifecycle_next = _next(issue_number=57)
    step = type(
        "Step",
        (),
        {
            "next": type("Next", (), {"lifecycle": lifecycle_next, "action": "execute-worker"})(),
            "status": "failed",
            "stop_reason": "step command failed",
            "fallback_commands": (
                "signposter artifact write-worker-summary --repo example/repo --issue 57 --apply",
                "signposter gate --repo example/repo --issue 57",
            ),
        },
    )()
    result = OrchestratorRunNextLoop(
        status="stopped",
        cycles_requested=1,
        cycles_run=1,
        max_tasks=1,
        tasks_started=1,
        selected_issue=57,
        steps=[step],
        stop_reason="step command failed",
        notes=[],
        stop_category="failed-step",
        stop_tolerated=False,
    )

    out = format_orchestrator_run_next_loop(result)

    assert (
        "fallback: signposter artifact write-worker-summary "
        "--repo example/repo --issue 57 --apply" in out
    )
    assert "fallback: signposter gate --repo example/repo --issue 57" in out


def test_run_next_loop_cli_writes_transcript(tmp_path, monkeypatch, capsys) -> None:
    transcript = tmp_path / "loop.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "orchestrator",
            "run-next-loop",
            "--repo",
            "example/repo",
            "--summary",
            "--tolerate-no-ready",
            "--tolerate-active-ambiguity",
            "--tolerate-blocked-lifecycle",
            "--tolerate-failed-step",
            "--transcript",
            str(transcript),
        ],
    )

    result = OrchestratorRunNextLoop(
        status="completed",
        cycles_requested=1,
        cycles_run=0,
        max_tasks=1,
        tasks_started=0,
        selected_issue=None,
        steps=[],
        stop_reason="no ready or resumable active issue found",
        notes=[],
    )
    with patch(
        "signposter.cli.run_orchestrator_run_next_loop",
        return_value=result,
    ) as run_loop:
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "Signposter Automation Summary" in captured.out
    assert f"Transcript: {transcript}" in captured.out
    assert transcript.exists()
    assert run_loop.call_args.kwargs["tolerate_no_ready"] is True
    assert run_loop.call_args.kwargs["tolerate_active_ambiguity"] is True
    assert run_loop.call_args.kwargs["tolerate_blocked_lifecycle"] is True
    assert run_loop.call_args.kwargs["tolerate_failed_step"] is True


def test_run_orchestrator_autonomy_smoke_writes_summary_and_transcript(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        (
            "{\n"
            '  "repo": "example/repo",\n'
            '  "status": "seeded",\n'
            '  "issues": [\n'
            "    {\n"
            '      "key": "AUTO-001",\n'
            '      "title": "Task",\n'
            '      "github_issue": 55,\n'
            '      "github_url": "https://github.com/example/repo/issues/55",\n'
            '      "depends_on": []\n'
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    artifact_path = tmp_path / "smoke.txt"
    transcript_path = tmp_path / "smoke.transcript.txt"

    scheduler_issue = LabeledItem(
        number=55,
        title="Issue 55",
        html_url="https://github.com/example/repo/issues/55",
        labels=["state:ready"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="example/repo",
        status="ready",
        issue=scheduler_issue,
        reason="first ready",
        skipped=[],
        notes=[],
    )
    loop = OrchestratorRunNextLoop(
        status="completed",
        cycles_requested=1,
        cycles_run=0,
        max_tasks=1,
        tasks_started=0,
        selected_issue=None,
        steps=[],
        stop_reason="no ready or resumable active issue found",
        notes=[],
        stop_category="no-ready",
        stop_tolerated=True,
    )

    with (
        patch(
            "signposter.orchestrator._fetch_manifest_issue_states",
            return_value={55: "ready"},
        ),
        patch(
            "signposter.orchestrator.run_orchestrator_run_next",
            return_value=type(
                "RunNext",
                (),
                {
                    "scheduler": scheduler,
                    "next": type("Next", (), {"action": "create-worktree"})(),
                    "step": None,
                    "status": "ready",
                    "notes": [],
                },
            )(),
        ),
        patch("signposter.orchestrator.run_orchestrator_run_next_loop", return_value=loop),
    ):
        result = run_orchestrator_autonomy_smoke(
            "example/repo",
            manifest_path=manifest_path,
            sync_github=True,
            artifact_path=artifact_path,
            transcript_path=transcript_path,
        )

    assert result.status == "completed"
    assert artifact_path.exists()
    assert transcript_path.exists()
    text = artifact_path.read_text(encoding="utf-8")
    assert "Signposter Autonomy Smoke" in text
    assert "selected: AUTO-001 -> #55" in text
    assert "No GitHub mutation was performed." in text
    assert "No execution backend was started." in text


def test_autonomy_smoke_cli_writes_artifact(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    artifact_path = tmp_path / "smoke.txt"
    result = OrchestratorAutonomySmoke(
        repo="example/repo",
        manifest_path="manifest.json",
        status="completed",
        planner_next={"status": "ready", "reason": "ok", "next": None},
        run_next=type(
            "RunNext",
            (),
            {
                "scheduler": SchedulerNext(
                    repo="example/repo",
                    status="completed",
                    issue=None,
                    reason="none",
                    skipped=[],
                    notes=[],
                ),
                "next": None,
                "step": None,
                "status": "completed",
                "notes": [],
            },
        )(),
        loop=OrchestratorRunNextLoop(
            status="completed",
            cycles_requested=1,
            cycles_run=0,
            max_tasks=1,
            tasks_started=0,
            selected_issue=None,
            steps=[],
            stop_reason="none",
            notes=[],
            stop_category="no-ready",
            stop_tolerated=True,
        ),
        artifact_path=str(artifact_path),
        transcript_path=None,
        notes=["No GitHub mutation was performed."],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "orchestrator",
            "autonomy-smoke",
            "--repo",
            "example/repo",
            "--artifact",
            str(artifact_path),
        ],
    )

    with patch("signposter.cli.run_orchestrator_autonomy_smoke", return_value=result):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "Signposter Autonomy Smoke" in captured.out
    assert "summary:" in captured.out
