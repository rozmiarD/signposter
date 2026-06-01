from __future__ import annotations

import json
from argparse import Namespace

from signposter.cli import run_scheduler_manifest
from signposter.issue_manifest import (
    build_issue_dag_manifest,
    format_issue_dag_manifest_plan,
    plan_issue_dag_manifest,
)


def _proc(returncode: int = 0, stdout: str = "[]", stderr: str = ""):
    return type(
        "Proc",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


def test_build_issue_dag_manifest_parses_dependencies_and_dedupes() -> None:
    payload = [
        {
            "number": 2,
            "title": "Second",
            "body": "Depends-On: #1",
            "state": "OPEN",
            "labels": [{"name": "state:ready"}],
        },
        {
            "number": 2,
            "title": "Duplicate",
            "body": "",
            "state": "OPEN",
            "labels": [],
        },
        {
            "number": 1,
            "title": "First",
            "body": "",
            "state": "CLOSED",
            "labels": [{"name": "state:merged"}],
        },
    ]

    manifest = build_issue_dag_manifest(
        "example/repo",
        run_command=lambda *a, **k: _proc(stdout=json.dumps(payload)),
    )

    assert manifest["version"] == "planner.issue-dag-manifest.v0.1"
    assert [task["issue"] for task in manifest["tasks"]] == [1, 2]
    assert manifest["tasks"][0]["state"] == "merged"
    assert manifest["tasks"][1]["depends_on"] == [1]
    assert manifest["tasks"][1]["side_task"] is False


def test_build_issue_dag_manifest_parses_side_task_metadata() -> None:
    payload = [
        {
            "number": 7,
            "title": "Side task",
            "body": "Mainline: H042\nParent: #6\nReturn-To: #8\nSide-Task: yes",
            "state": "OPEN",
            "labels": [{"name": "state:ready"}],
        }
    ]

    manifest = build_issue_dag_manifest(
        "example/repo",
        run_command=lambda *a, **k: _proc(stdout=json.dumps(payload)),
    )

    task = manifest["tasks"][0]
    assert task["mainline"] == "H042"
    assert task["parent"] == 6
    assert task["return_to"] == 8
    assert task["side_task"] is True


def test_plan_issue_dag_manifest_dry_run_does_not_write(tmp_path) -> None:
    output = tmp_path / "manifest.json"

    plan = plan_issue_dag_manifest(
        "example/repo",
        output,
        run_command=lambda *a, **k: _proc(),
    )

    assert plan.status == "planned"
    assert not output.exists()
    assert "No manifest mutation was performed." in plan.notes


def test_plan_issue_dag_manifest_apply_writes_file(tmp_path) -> None:
    output = tmp_path / "manifest.json"

    plan = plan_issue_dag_manifest(
        "example/repo",
        output,
        apply=True,
        run_command=lambda *a, **k: _proc(),
    )

    assert plan.status == "written"
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["repo"] == "example/repo"


def test_format_issue_dag_manifest_plan_lists_tasks(tmp_path) -> None:
    payload = [
        {
            "number": 3,
            "title": "Third",
            "body": "Depends-On: #2",
            "state": "OPEN",
            "labels": [{"name": "state:ready"}],
        }
    ]
    plan = plan_issue_dag_manifest(
        "example/repo",
        tmp_path / "manifest.json",
        run_command=lambda *a, **k: _proc(stdout=json.dumps(payload)),
    )

    out = format_issue_dag_manifest_plan(plan)

    assert "Signposter Issue DAG Manifest" in out
    assert "#3: ready deps=#2 title=Third" in out
    assert "No GitHub mutation was performed." in out


def test_format_issue_dag_manifest_plan_shows_side_task_metadata(tmp_path) -> None:
    payload = [
        {
            "number": 9,
            "title": "Side",
            "body": "Depends-On: #8\nReturn-To: #10\nSide-Task: yes",
            "state": "OPEN",
            "labels": [{"name": "state:ready"}],
        }
    ]
    plan = plan_issue_dag_manifest(
        "example/repo",
        tmp_path / "manifest.json",
        run_command=lambda *a, **k: _proc(stdout=json.dumps(payload)),
    )

    out = format_issue_dag_manifest_plan(plan)

    assert "side-task=yes" in out
    assert "return-to=#10" in out


def test_scheduler_manifest_cli_dry_run(tmp_path, capsys) -> None:
    import signposter.cli as cli

    original = cli.plan_issue_dag_manifest

    def fake_plan(repo, output, limit=200, apply=False):
        return plan_issue_dag_manifest(
            repo,
            output,
            limit=limit,
            apply=apply,
            run_command=lambda *a, **k: _proc(),
        )

    try:
        cli.plan_issue_dag_manifest = fake_plan
        rc = run_scheduler_manifest(
            Namespace(
                repo="example/repo",
                output=str(tmp_path / "manifest.json"),
                limit=200,
                apply=False,
            )
        )
    finally:
        cli.plan_issue_dag_manifest = original

    captured = capsys.readouterr()
    assert rc == 0
    assert "Signposter Issue DAG Manifest" in captured.out
