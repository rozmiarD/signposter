from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from signposter.cli import main
from signposter.planner import (
    PLAN_VERSION,
    build_planner_draft,
    validate_planner_plan,
    write_planner_draft,
)


def test_build_planner_draft_has_expected_shape() -> None:
    plan = build_planner_draft("zaprojektuj lifecycle watcher")

    assert plan["version"] == PLAN_VERSION
    assert plan["status"] == "draft"
    assert plan["mode"] == "supervised"
    assert [issue["key"] for issue in plan["issues"]] == [
        "WATCH-001",
        "WATCH-002",
        "WATCH-003",
        "WATCH-004",
        "WATCH-005",
    ]
    assert plan["issues"][1]["depends_on"] == ["WATCH-001"]
    assert all(issue["allowed_mutations"] == [] for issue in plan["issues"])


def test_write_planner_draft_creates_json(tmp_path: Path) -> None:
    output_path = tmp_path / "artifacts" / "plans" / "watch.json"

    plan = write_planner_draft("build lifecycle watch", output_path)

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved == plan
    assert saved["goal"] == "build lifecycle watch"


def test_validate_planner_plan_rejects_unsafe_plan() -> None:
    plan = build_planner_draft("build lifecycle watch")
    plan["issues"][0]["body"] = "Fixes #123"
    plan["issues"][1]["depends_on"] = ["MISSING-999"]
    plan["issues"][2]["acceptance"] = []
    plan["issues"][3]["allowed_mutations"] = ["github"]

    errors = validate_planner_plan(plan)

    assert "WATCH-001: contains auto-close keyword" in errors
    assert "WATCH-002: unknown dependency MISSING-999" in errors
    assert "WATCH-003: acceptance must not be empty" in errors
    assert (
        "WATCH-004: allowed_mutations must be empty for local draft plans"
        in errors
    )


def test_cli_planner_draft_writes_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "plan.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "draft",
            "--goal",
            "build lifecycle watch",
            "--out",
            str(output_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code in (None, 0)
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr().out

    assert saved["status"] == "draft"
    assert "Signposter Planner Draft" in captured
    assert "No GitHub mutation was performed." in captured


def test_cli_planner_validate_reports_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "planner", "validate", "--plan", str(plan_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Signposter Planner Validate" in captured
    assert "Status:\n  pass" in captured
    assert "No OpenClaw execution was performed." in captured


def test_cli_planner_validate_reports_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    plan = build_planner_draft("build lifecycle watch")
    plan["issues"][0]["body"] = "Closes #1"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "planner", "validate", "--plan", str(plan_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "Status:\n  blocked" in captured
    assert "WATCH-001: contains auto-close keyword" in captured
