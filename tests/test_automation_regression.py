from __future__ import annotations

import json
from unittest.mock import Mock, patch

from signposter.artifact import validate_worker_summary_artifact
from signposter.issue_factory import apply_issue_factory
from signposter.issue_manifest import build_issue_dag_manifest
from signposter.lifecycle import LifecycleNext, LifecyclePreflight
from signposter.orchestrator import (
    format_orchestrator_run_next_loop_summary,
    run_orchestrator_loop,
    run_orchestrator_run_next_loop,
)
from signposter.scan import LabeledItem
from signposter.scheduler import SchedulerNext, build_scheduler_graph, format_scheduler_graph


def _proc(returncode: int = 0, stdout: str = "[]", stderr: str = ""):
    return type(
        "Proc",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


def _next(**overrides) -> LifecycleNext:
    base = dict(
        query_issue=1,
        query_pr=None,
        issue_number=1,
        pr_number=None,
        issue_state="OPEN",
        workflow_state="state:active",
        pr_state=None,
        worktree_exists=True,
        local_branch_exists=True,
        prompt_exists=True,
        worker_summary_exists=False,
        preflight=LifecyclePreflight("ok", "ok", "ok"),
        blocked_next_action=None,
        action="execute-worker",
        command="signposter run --repo example/repo --issue 1 --execute --worktree",
        status="actionable",
        reason=None,
        notes=[],
    )
    base.update(overrides)
    return LifecycleNext(**base)


def test_regression_run_next_loop_preserves_execute_guard() -> None:
    active = LabeledItem(1, "One", "url", ["state:active"], "issue")
    scheduler = SchedulerNext("example/repo", "completed", None, "none", [], [])

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active]),
        patch("signposter.orchestrator.plan_lifecycle_next", return_value=_next()),
    ):
        result = run_orchestrator_run_next_loop("example/repo", max_cycles=1, apply=True)

    out = format_orchestrator_run_next_loop_summary(result)
    assert result.status == "stopped"
    assert result.stop_category == "blocked-lifecycle"
    assert "OpenClaw execution requires explicit --execute" in out


def test_regression_pr_tail_dry_run_does_not_execute_command() -> None:
    lifecycle = _next(
        query_issue=None,
        query_pr=9,
        issue_number=None,
        pr_number=9,
        action="review-pr",
        command="signposter review gate --repo example/repo --pr 9",
    )
    run_command = Mock()

    with patch("signposter.orchestrator.plan_lifecycle_next", return_value=lifecycle):
        result = run_orchestrator_loop(
            "example/repo",
            pr=9,
            max_cycles=1,
            run_command=run_command,
        )

    assert result.status == "stopped"
    assert result.stop_reason == "dry-run; rerun with --apply to execute this step"
    run_command.assert_not_called()


def test_regression_run_next_loop_ci_pending_sets_waiting_category() -> None:
    active = LabeledItem(1, "One", "url", ["state:active"], "issue")
    scheduler = SchedulerNext("example/repo", "completed", None, "none", [], [])
    proc = _proc(returncode=0, stdout="pending — checks are still running", stderr="")

    with (
        patch("signposter.orchestrator.select_next_issue", return_value=scheduler),
        patch("signposter.orchestrator.fetch_open_issues", return_value=[active]),
        patch(
            "signposter.orchestrator.plan_lifecycle_next",
            return_value=_next(action="review-pr"),
        ),
    ):
        result = run_orchestrator_run_next_loop(
            "example/repo",
            max_cycles=2,
            apply=True,
            execute=True,
            run_command=Mock(return_value=proc),
        )

    out = format_orchestrator_run_next_loop_summary(result)
    assert result.status == "stopped"
    assert result.stop_category == "waiting-ci"
    assert "ci checks pending" in out


def test_regression_issue_factory_apply_guard(tmp_path) -> None:
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{"id": "H200A", "title": "New"}]), encoding="utf-8")
    run_command = Mock(return_value=_proc())

    plan = apply_issue_factory("example/repo", tasks, apply=False, run_command=run_command)

    assert plan.apply is False
    assert plan.items[0].status == "create"
    assert all(call.args[0][:3] != ["gh", "issue", "create"] for call in run_command.mock_calls)


def test_regression_scheduler_graph_shows_dependencies_and_return_ready() -> None:
    issues = [
        LabeledItem(10, "Side", "url", ["state:active"], "issue"),
        LabeledItem(11, "Parent", "url", ["state:ready"], "issue"),
        LabeledItem(12, "Return", "url", ["state:ready"], "issue"),
    ]
    bodies = {
        10: {"body": "Depends-On: #9\nSide-Task: yes\nParent: #11\nReturn-To: #12"},
        11: {"body": ""},
        12: {"body": ""},
    }

    with (
        patch("signposter.scheduler.fetch_open_issues", return_value=issues),
        patch("signposter.scheduler.fetch_issue_context", side_effect=lambda repo, n: bodies[n]),
    ):
        graph = build_scheduler_graph("example/repo")

    out = format_scheduler_graph(graph)
    assert "depends on: #9" in out
    assert "parent state: ready" in out
    assert "return ready: yes" in out


def test_regression_issue_dag_manifest_dedupes_and_keeps_deps() -> None:
    payload = [
        {"number": 1, "title": "One", "body": "", "state": "OPEN", "labels": []},
        {"number": 2, "title": "Two", "body": "Depends-On: #1", "state": "OPEN", "labels": []},
        {"number": 2, "title": "Two duplicate", "body": "", "state": "OPEN", "labels": []},
    ]

    manifest = build_issue_dag_manifest(
        "example/repo",
        run_command=lambda *a, **k: _proc(stdout=json.dumps(payload)),
    )

    assert [task["issue"] for task in manifest["tasks"]] == [1, 2]
    assert manifest["tasks"][1]["depends_on"] == [1]


def test_regression_worker_artifact_validation_blocks_missing_summary(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = validate_worker_summary_artifact(123)

    assert result.status == "missing"
    assert "summary artifact" in result.missing
