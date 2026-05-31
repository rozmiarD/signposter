from __future__ import annotations

import json
from argparse import Namespace

from signposter.cli import run_issue_factory
from signposter.issue_factory import (
    apply_issue_factory,
    format_issue_factory_plan,
    load_issue_factory_tasks,
    plan_issue_factory,
)


def _proc(returncode: int = 0, stdout: str = "[]", stderr: str = ""):
    return type(
        "Proc",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


def test_load_issue_factory_tasks_from_json(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "H100A",
                        "title": "Build thing",
                        "body": "Do it",
                        "labels": ["phase:build", "state:ready"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    tasks = load_issue_factory_tasks(path)

    assert tasks[0].task_id == "H100A"
    assert tasks[0].labels == ["phase:build", "state:ready"]


def test_load_issue_factory_tasks_from_tsv(tmp_path) -> None:
    path = tmp_path / "tasks.tsv"
    path.write_text(
        "id\ttitle\tbody\tlabels\nH100B\tBuild next\tDetails\tphase:build,state:ready\n",
        encoding="utf-8",
    )

    tasks = load_issue_factory_tasks(path)

    assert tasks[0].task_id == "H100B"
    assert tasks[0].title == "Build next"
    assert tasks[0].labels == ["phase:build", "state:ready"]


def test_plan_issue_factory_is_dry_run_and_idempotent_by_title(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {"id": "H100A", "title": "Existing"},
                {"id": "H100B", "title": "New"},
            ]
        ),
        encoding="utf-8",
    )

    def run_command(command, **kwargs):
        assert command[:3] == ["gh", "issue", "list"]
        return _proc(stdout=json.dumps([{"number": 12, "title": "H100A - Existing"}]))

    plan = plan_issue_factory("example/repo", path, run_command=run_command)

    assert plan.apply is False
    assert [item.status for item in plan.items] == ["exists", "create"]
    assert plan.items[0].existing_number == 12
    assert "No GitHub mutation was performed." in plan.notes


def test_apply_issue_factory_creates_only_missing_tasks(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {"id": "H100A", "title": "Existing"},
                {"id": "H100B", "title": "New", "labels": ["state:ready"]},
            ]
        ),
        encoding="utf-8",
    )
    calls = []

    def run_command(command, **kwargs):
        calls.append(command)
        if command[:3] == ["gh", "issue", "list"]:
            return _proc(stdout=json.dumps([{"number": 12, "title": "H100A - Existing"}]))
        assert command[:3] == ["gh", "issue", "create"]
        assert "--label" in command
        return _proc(stdout="https://github.com/example/repo/issues/99\n")

    plan = apply_issue_factory("example/repo", path, apply=True, run_command=run_command)

    assert plan.apply is True
    assert [item.status for item in plan.items] == ["exists", "created"]
    assert plan.items[1].existing_number == 99
    assert len([call for call in calls if call[:3] == ["gh", "issue", "create"]]) == 1


def test_format_issue_factory_plan_lists_status(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([{"id": "H100A", "title": "New"}]), encoding="utf-8")
    plan = plan_issue_factory("example/repo", path, run_command=lambda *a, **k: _proc())

    out = format_issue_factory_plan(plan)

    assert "Signposter Issue Factory Plan" in out
    assert "H100A: create" in out
    assert "Use --apply to create missing issues." in out


def test_issue_factory_cli_dry_run(tmp_path, capsys) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([{"id": "H100A", "title": "New"}]), encoding="utf-8")

    import signposter.cli as cli

    original = cli.apply_issue_factory
    try:
        cli.apply_issue_factory = lambda repo, task_path, apply=False: plan_issue_factory(
            repo,
            task_path,
            run_command=lambda *a, **k: _proc(),
        )
        rc = run_issue_factory(Namespace(repo="example/repo", tasks=str(path), apply=False))
    finally:
        cli.apply_issue_factory = original

    captured = capsys.readouterr()
    assert rc == 0
    assert "Signposter Issue Factory Plan" in captured.out
