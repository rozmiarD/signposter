from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from signposter.cli import main
from signposter.planner import (
    PLAN_VERSION,
    build_planner_draft,
    build_planner_next,
    build_planner_seed_plan,
    evaluate_worker_issue_body_size,
    format_planner_issue_body,
    format_planner_roadmap,
    mark_planner_task,
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


def test_build_planner_seed_plan_ready_contains_labels() -> None:
    plan = build_planner_draft("build lifecycle watch")

    seed_plan = build_planner_seed_plan(plan)

    assert seed_plan["status"] == "ready"
    assert seed_plan["errors"] == []
    assert seed_plan["issues"][0]["labels"] == [
        "phase:build",
        "risk:low",
        "role:worker",
        "area:cli",
    ]


def test_cli_planner_seed_reports_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "planner", "seed", "--plan", str(plan_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Signposter Planner Seed" in captured
    assert "Status:\n  ready" in captured
    assert "WATCH-001 — Define lifecycle watch CLI contract" in captured
    assert "No GitHub issue was created." in captured


def test_cli_planner_seed_blocks_invalid_plan(
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
        ["signposter", "planner", "seed", "--plan", str(plan_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "Status:\n  blocked" in captured
    assert "WATCH-001: contains auto-close keyword" in captured


def test_build_planner_next_selects_first_ready_issue() -> None:
    plan = build_planner_draft("build lifecycle watch")

    next_plan = build_planner_next(plan)

    assert next_plan["status"] == "ready"
    assert next_plan["next"]["key"] == "WATCH-001"


def test_build_planner_next_respects_completed_dependencies() -> None:
    plan = build_planner_draft("build lifecycle watch")
    plan["issues"][0]["status"] = "done"

    next_plan = build_planner_next(plan)

    assert next_plan["status"] == "ready"
    assert next_plan["next"]["key"] == "WATCH-002"


def test_cli_planner_next_reports_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "planner", "next", "--plan", str(plan_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Signposter Planner Next" in captured
    assert "Status:\n  ready" in captured
    assert "WATCH-001 — Define lifecycle watch CLI contract" in captured
    assert "No task execution was performed." in captured


def test_mark_planner_task_updates_status_and_next(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    write_planner_draft("build lifecycle watch", plan_path)

    result = mark_planner_task(
        plan_path,
        "WATCH-001",
        "done",
        "local validation passed",
    )

    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    next_plan = build_planner_next(saved)
    assert result["status"] == "updated"
    assert saved["issues"][0]["status"] == "done"
    assert saved["issues"][0]["status_reason"] == "local validation passed"
    assert next_plan["next"]["key"] == "WATCH-002"


def test_mark_planner_task_blocks_unknown_task(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    write_planner_draft("build lifecycle watch", plan_path)

    result = mark_planner_task(plan_path, "MISSING-999", "done")

    assert result["status"] == "blocked"
    assert result["errors"] == ["unknown task MISSING-999"]


def test_cli_planner_mark_updates_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "mark",
            "--plan",
            str(plan_path),
            "--task",
            "WATCH-001",
            "--status",
            "done",
            "--reason",
            "local validation passed",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert saved["issues"][0]["status"] == "done"
    assert "Signposter Planner Mark" in captured
    assert "No task execution was performed." in captured


def test_format_planner_issue_body_contains_agent_contract() -> None:
    plan = build_planner_draft("build lifecycle watch")
    issue = plan["issues"][0]

    body = format_planner_issue_body(plan, issue)

    assert body.startswith("Task: WATCH-001 — Define lifecycle watch CLI contract")
    assert "Context:" in body
    assert "Problem:" in body
    assert "Goal:" in body
    assert "Target command:" in body
    assert "Expected output:" in body
    assert "Scope:" in body
    assert "Rules:" in body
    assert "Implementation guidance:" in body
    assert "Tests:" in body
    assert "Acceptance:" in body
    assert "Stop conditions:" in body
    assert "Report back:" in body
    assert "No GitHub mutation was performed." in body
    assert "No OpenClaw execution was performed." in body


def test_build_planner_seed_plan_includes_issue_body() -> None:
    plan = build_planner_draft("build lifecycle watch")

    seed_plan = build_planner_seed_plan(plan)

    body = seed_plan["issues"][0]["body"]
    assert seed_plan["status"] == "ready"
    assert "Task: WATCH-001" in body
    assert "Do not mutate GitHub unless explicitly required and guarded by --apply." in body


def test_evaluate_worker_issue_body_size_passes_preferred_range() -> None:
    body = "\n".join(f"line {index}" for index in range(80))

    result = evaluate_worker_issue_body_size(body)

    assert result["status"] == "pass"
    assert result["line_count"] == 80
    assert result["warnings"] == []
    assert result["errors"] == []


def test_evaluate_worker_issue_body_size_warns_outside_preferred_range() -> None:
    short_body = "\n".join(f"line {index}" for index in range(20))
    long_body = "\n".join(f"line {index}" for index in range(130))

    short_result = evaluate_worker_issue_body_size(short_body)
    long_result = evaluate_worker_issue_body_size(long_body)

    assert short_result["status"] == "warning"
    assert "preferred min" in short_result["warnings"][0]
    assert long_result["status"] == "warning"
    assert "preferred max" in long_result["warnings"][0]


def test_evaluate_worker_issue_body_size_blocks_hard_limits() -> None:
    too_many_lines = "\n".join(f"line {index}" for index in range(166))
    too_many_chars = "x" * 12001

    line_result = evaluate_worker_issue_body_size(too_many_lines)
    char_result = evaluate_worker_issue_body_size(too_many_chars)

    assert line_result["status"] == "blocked"
    assert "hard max is 165" in line_result["errors"][0]
    assert char_result["status"] == "blocked"
    assert "hard max is 12000" in char_result["errors"][0]


def test_build_planner_seed_plan_includes_body_size() -> None:
    plan = build_planner_draft("build lifecycle watch")

    seed_plan = build_planner_seed_plan(plan)

    body_size = seed_plan["issues"][0]["body_size"]
    assert body_size["status"] in {"pass", "warning"}
    assert body_size["line_count"] > 0
    assert body_size["char_count"] > 0
    assert body_size["errors"] == []


def test_format_planner_roadmap_uses_roadmap_contract() -> None:
    plan = build_planner_draft("build lifecycle watch")

    roadmap = format_planner_roadmap(plan)

    assert roadmap.startswith("Roadmap: build lifecycle watch")
    assert "Intent:" in roadmap
    assert "Outcome:" in roadmap
    assert "Non-goals:" in roadmap
    assert "Assumptions:" in roadmap
    assert "Required capabilities:" in roadmap
    assert "Milestones:" in roadmap
    assert "Issue DAG:" in roadmap
    assert "Task sizing policy:" in roadmap
    assert "Risk model:" in roadmap
    assert "Mutation policy:" in roadmap
    assert "Validation strategy:" in roadmap
    assert "Stop conditions:" in roadmap
    assert "Follow-up policy:" in roadmap
    assert "Done definition:" in roadmap
    assert "Worker task preferred range: 60–120 lines." in roadmap
    assert "Do not collapse the whole project into one oversized worker issue." in roadmap


def test_format_planner_roadmap_blocks_invalid_plan() -> None:
    plan = build_planner_draft("build lifecycle watch")
    plan["issues"][0]["body"] = "Closes #1"

    roadmap = format_planner_roadmap(plan)

    assert "Status:\nblocked" in roadmap
    assert "Validation errors:" in roadmap
    assert "WATCH-001: contains auto-close keyword" in roadmap
