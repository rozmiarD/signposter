from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from signposter.cli import main
from signposter.planner import PLAN_VERSION, build_planner_draft, write_planner_draft


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
    assert "No OpenClaw execution was performed." in captured
