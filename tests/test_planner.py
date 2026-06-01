from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from signposter.cli import main
from signposter.planner import (
    PLAN_VERSION,
    apply_planner_advance_plan,
    apply_planner_seed_manifest,
    build_planner_advance_plan_from_status,
    build_planner_draft,
    build_planner_impact_from_status,
    build_planner_next,
    build_planner_next_from_status,
    build_planner_run_plan_from_status,
    build_planner_seed_manifest,
    build_planner_seed_plan,
    build_planner_status,
    build_planner_step_from_next,
    evaluate_worker_issue_body_size,
    format_gh_issue_create_command,
    format_planner_advance_plan,
    format_planner_impact,
    format_planner_issue_body,
    format_planner_next_from_status,
    format_planner_roadmap,
    format_planner_run_plan,
    format_planner_status,
    format_planner_step,
    format_seed_label_preflight,
    mark_planner_task,
    prepare_planner_seed_manifest,
    validate_planner_plan,
    validate_seed_plan_labels,
    write_planner_draft,
    write_planner_seed_issue_bodies,
    write_planner_seed_manifest,
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
        "state:ready",
    ]
    assert "state:ready" not in seed_plan["issues"][1]["labels"]


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


def test_format_planner_roadmap_uses_generic_roadmap_contract() -> None:
    plan = build_planner_draft("build lifecycle watch")

    roadmap = format_planner_roadmap(plan)

    assert roadmap.startswith("Roadmap Template")
    assert "User goal:" in roadmap
    assert "Purpose:" in roadmap
    assert "Roadmap role:" in roadmap
    assert "Outcome:" in roadmap
    assert "Non-goals:" in roadmap
    assert "Planning sections:" in roadmap
    assert "Milestone model:" in roadmap
    assert "Worker task sizing policy:" in roadmap
    assert "Risk model:" in roadmap
    assert "Mutation policy:" in roadmap
    assert "Validation strategy:" in roadmap
    assert "Stop conditions:" in roadmap
    assert "Follow-up policy:" in roadmap
    assert "Done definition:" in roadmap
    assert "Preferred range: 60–120 lines." in roadmap
    assert "Do not hard-code product-specific task names" in roadmap
    assert "WATCH-001" not in roadmap
    assert "Define lifecycle watch CLI contract" not in roadmap
    assert "signposter lifecycle watch" not in roadmap


def test_format_planner_roadmap_blocks_invalid_plan() -> None:
    plan = build_planner_draft("build lifecycle watch")
    plan["issues"][0]["body"] = "Closes #1"

    roadmap = format_planner_roadmap(plan)

    assert "Status:\nblocked" in roadmap
    assert "Validation errors:" in roadmap
    assert "WATCH-001: contains auto-close keyword" in roadmap


def test_cli_planner_roadmap_prints_generic_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "planner", "roadmap", "--plan", str(plan_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert captured.startswith("Roadmap Template")
    assert "User goal:" in captured
    assert "Milestone model:" in captured
    assert "WATCH-001" not in captured
    assert "signposter lifecycle watch" not in captured


def test_cli_planner_roadmap_writes_markdown_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    out_path = tmp_path / "roadmaps" / "roadmap.md"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "roadmap",
            "--plan",
            str(plan_path),
            "--out",
            str(out_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    saved = out_path.read_text(encoding="utf-8")
    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert saved.startswith("Roadmap Template")
    assert "Output:" in captured
    assert str(out_path) in captured


def test_build_planner_seed_plan_blocks_oversized_issue_body() -> None:
    plan = build_planner_draft("build lifecycle watch")
    plan["issues"][0]["acceptance"] = [
        f"acceptance item {index}" for index in range(200)
    ]

    seed_plan = build_planner_seed_plan(plan)

    assert seed_plan["status"] == "blocked"
    assert any("WATCH-001" in error for error in seed_plan["errors"])
    assert any("hard max is 165" in error for error in seed_plan["errors"])


def test_cli_planner_seed_show_body_prints_issue_body(
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
            "seed",
            "--plan",
            str(plan_path),
            "--show-body",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "body size:" in captured
    assert "----- BEGIN ISSUE BODY -----" in captured
    assert "Task: WATCH-001" in captured
    assert "No GitHub issue was created." in captured


def test_cli_planner_seed_without_show_body_keeps_output_compact(
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
    assert "body size:" in captured
    assert "----- BEGIN ISSUE BODY -----" not in captured
    assert "Task: WATCH-001" not in captured


def test_format_gh_issue_create_command_quotes_args() -> None:
    command = format_gh_issue_create_command(
        repo="ExatronOmega/signposter",
        title="WATCH-001 — Define lifecycle watch CLI contract",
        body_file=Path("artifacts/plans/issue-bodies/WATCH-001.md"),
        labels=["phase:build", "risk:low", "role:worker", "area:cli", "state:ready"],
    )

    assert command.startswith("gh \\\n  issue \\\n  create")
    assert "--repo \\\n  ExatronOmega/signposter" in command
    assert "--body-file \\\n  artifacts/plans/issue-bodies/WATCH-001.md" in command
    assert "--label \\\n  phase:build" in command
    assert "--label \\\n  state:ready" in command
    assert "WATCH-001" in command


def test_build_planner_seed_plan_includes_github_title() -> None:
    plan = build_planner_draft("build lifecycle watch")

    seed_plan = build_planner_seed_plan(plan)

    assert seed_plan["issues"][0]["github_title"] == (
        "WATCH-001 — Define lifecycle watch CLI contract"
    )


def test_cli_planner_seed_show_commands_prints_gh_preview(
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
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--show-commands",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "GitHub title: WATCH-001" in captured
    assert "----- BEGIN GH COMMAND -----" in captured
    assert "gh \\" in captured
    assert "ExatronOmega/signposter" in captured
    assert "Command previews are not executed." in captured
    assert "No GitHub issue was created." in captured


def test_cli_planner_seed_without_show_commands_keeps_command_hidden(
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
    assert "----- BEGIN GH COMMAND -----" not in captured
    assert "gh issue create" not in captured


def test_write_planner_seed_issue_bodies_writes_markdown_files(tmp_path: Path) -> None:
    plan = build_planner_draft("build lifecycle watch")
    seed_plan = build_planner_seed_plan(plan)
    body_dir = tmp_path / "issue-bodies"

    written = write_planner_seed_issue_bodies(seed_plan, body_dir)

    assert [path.name for path in written] == [
        "WATCH-001.md",
        "WATCH-002.md",
        "WATCH-003.md",
        "WATCH-004.md",
        "WATCH-005.md",
    ]
    assert (body_dir / "WATCH-001.md").read_text(encoding="utf-8").startswith(
        "Task: WATCH-001"
    )


def test_cli_planner_seed_write_bodies_writes_local_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--write-bodies",
            "--body-dir",
            str(body_dir),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Written issue body files:" in captured
    assert "Local files only." in captured
    assert (body_dir / "WATCH-001.md").exists()
    assert (body_dir / "WATCH-001.md").read_text(encoding="utf-8").startswith(
        "Task: WATCH-001"
    )


def test_build_planner_seed_manifest_contains_future_issue_mapping(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)

    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )

    first_issue = manifest["issues"][0]
    assert manifest["version"] == "planner.seed-manifest.v0.1"
    assert manifest["status"] == "dry-run"
    assert manifest["repo"] == "ExatronOmega/signposter"
    assert first_issue["key"] == "WATCH-001"
    assert first_issue["github_issue"] is None
    assert first_issue["body_file"].endswith("WATCH-001.md")
    assert "No GitHub mutation was performed." in manifest["notes"]


def test_write_planner_seed_manifest_writes_json(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )

    write_planner_seed_manifest(manifest, manifest_path)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved == manifest
    assert saved["issues"][0]["github_issue"] is None


def test_cli_planner_seed_write_manifest_writes_local_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--write-bodies",
            "--body-dir",
            str(body_dir),
            "--write-manifest",
            "--manifest",
            str(manifest_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert manifest["version"] == "planner.seed-manifest.v0.1"
    assert manifest["status"] == "dry-run"
    assert manifest["repo"] == "ExatronOmega/signposter"
    assert manifest["issues"][0]["key"] == "WATCH-001"
    assert manifest["issues"][0]["github_issue"] is None
    assert manifest["issues"][0]["body_file"].endswith("WATCH-001.md")
    assert (body_dir / "WATCH-001.md").exists()
    assert "Prepared seed manifest:" in captured
    assert "Existing manifest:" in captured
    assert "none — created" in captured
    assert "No GitHub mutation was performed during manifest preparation." in captured
    assert "No GitHub issue was created." in captured


def test_cli_planner_seed_without_write_manifest_does_not_write_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    manifest_path = tmp_path / "seed-manifest.json"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert not manifest_path.exists()
    assert "Written seed manifest:" not in captured


class _FakeGhIssueCreateResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr




def _fake_label_list_result() -> _FakeGhIssueCreateResult:
    return _FakeGhIssueCreateResult(
        0,
        stdout="\n".join(
            [
                "phase:build",
                "risk:low",
                "role:worker",
                "area:cli",
                "area:tests",
                "area:docs",
                "state:ready",
            ]
        ),
    )


class _FakeGhIssueCreateRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> _FakeGhIssueCreateResult:
        self.calls.append(args)
        issue_number = len(self.calls) + 100
        return _FakeGhIssueCreateResult(
            0,
            stdout=f"https://github.com/ExatronOmega/signposter/issues/{issue_number}",
        )


def test_apply_planner_seed_manifest_uses_fake_runner_and_updates_manifest(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    write_planner_seed_issue_bodies(seed_plan, body_dir)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    write_planner_seed_manifest(manifest, manifest_path)
    runner = _FakeGhIssueCreateRunner()

    result = apply_planner_seed_manifest(manifest_path, runner)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "applied"
    assert result["errors"] == []
    assert len(result["created"]) == 5
    assert len(runner.calls) == 5
    assert runner.calls[0][:6] == [
        "gh",
        "issue",
        "create",
        "--repo",
        "ExatronOmega/signposter",
        "--title",
    ]
    assert saved["status"] == "applied"
    assert saved["issues"][0]["github_issue"] == 101
    assert saved["issues"][0]["github_url"].endswith("/issues/101")
    assert saved["issues"][1]["github_depends_on"] == [101]
    assert saved["issues"][1]["dependency_metadata"] == [
        {
            "key": "WATCH-001",
            "github_issue": 101,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/101",
        }
    ]


def test_apply_planner_seed_manifest_blocks_missing_body_files(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    write_planner_seed_manifest(manifest, manifest_path)
    runner = _FakeGhIssueCreateRunner()

    result = apply_planner_seed_manifest(manifest_path, runner)

    assert result["status"] == "blocked"
    assert result["created"] == []
    assert "missing body file" in result["errors"][0]
    assert runner.calls == []


def test_apply_planner_seed_manifest_stops_on_runner_failure(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    write_planner_seed_issue_bodies(seed_plan, body_dir)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    write_planner_seed_manifest(manifest, manifest_path)

    def failing_runner(args: list[str]) -> _FakeGhIssueCreateResult:
        return _FakeGhIssueCreateResult(1, stderr="boom")

    result = apply_planner_seed_manifest(manifest_path, failing_runner)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["created"] == []
    assert result["errors"] == ["boom"]
    assert saved["status"] == "partial"


def test_cli_planner_seed_apply_uses_fake_subprocess_and_updates_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    write_planner_draft("build lifecycle watch", plan_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        if command[:3] == ["gh", "label", "list"]:
            return _fake_label_list_result()
        calls.append(command)
        issue_number = len(calls) + 200
        return _FakeGhIssueCreateResult(
            0,
            stdout=f"https://github.com/ExatronOmega/signposter/issues/{issue_number}",
        )

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--body-dir",
            str(body_dir),
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert len(calls) == 5
    assert calls[0][:6] == [
        "gh",
        "issue",
        "create",
        "--repo",
        "ExatronOmega/signposter",
        "--title",
    ]
    assert manifest["status"] == "applied"
    assert manifest["issues"][0]["github_issue"] == 201
    assert manifest["issues"][0]["github_url"].endswith("/issues/201")
    assert manifest["issues"][1]["github_depends_on"] == [201]
    assert (body_dir / "WATCH-001.md").exists()
    assert "Planner Seed Apply" in captured
    assert "WATCH-001 -> #201" in captured
    assert "OpenClaw execution was not performed." in captured


def test_cli_planner_seed_apply_stops_on_fake_subprocess_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"

    write_planner_draft("build lifecycle watch", plan_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        if command[:3] == ["gh", "label", "list"]:
            return _fake_label_list_result()
        return _FakeGhIssueCreateResult(1, stderr="gh failed safely")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--body-dir",
            str(body_dir),
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert manifest["status"] == "partial"
    assert manifest["issues"][0]["github_issue"] is None
    assert "Planner Seed Apply" in captured
    assert "Status:\n  failed" in captured
    assert "gh failed safely" in captured


def test_prepare_planner_seed_manifest_creates_new_manifest(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)

    result = prepare_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
        manifest_path=manifest_path,
    )

    assert result["status"] == "ready"
    assert result["errors"] == []
    assert result["reused_existing"] is False
    assert manifest_path.exists()


def test_prepare_planner_seed_manifest_reuses_completed_manifest(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=1):
        issue["github_issue"] = 100 + index
    write_planner_seed_manifest(manifest, manifest_path)

    result = prepare_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
        manifest_path=manifest_path,
    )

    assert result["status"] == "completed"
    assert result["errors"] == []
    assert result["reused_existing"] is True
    assert result["manifest"]["issues"][0]["github_issue"] == 101


def test_prepare_planner_seed_manifest_reuses_partial_manifest(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "partial"
    manifest["issues"][0]["github_issue"] = 101
    write_planner_seed_manifest(manifest, manifest_path)

    result = prepare_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
        manifest_path=manifest_path,
    )

    assert result["status"] == "ready"
    assert result["errors"] == []
    assert result["reused_existing"] is True
    assert result["manifest"]["issues"][0]["github_issue"] == 101


def test_prepare_planner_seed_manifest_blocks_incompatible_manifest(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="OtherOrg/other",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    write_planner_seed_manifest(manifest, manifest_path)

    result = prepare_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
        manifest_path=manifest_path,
    )

    assert result["status"] == "blocked"
    assert result["errors"] == ["manifest repo mismatch"]
    assert result["reused_existing"] is True


def test_cli_planner_seed_apply_completed_manifest_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=1):
        issue["github_issue"] = 300 + index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{300 + index}"
    write_planner_seed_manifest(manifest, manifest_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0)

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--body-dir",
            str(body_dir),
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert exc_info.value.code in (None, 0)
    assert calls == []
    assert saved["status"] == "applied"
    assert saved["issues"][0]["github_issue"] == 301
    assert "Prepared seed manifest:" in captured
    assert "Status:\n  completed" in captured
    assert "Existing manifest:\n  reused" in captured
    assert "Planner Seed Apply" not in captured


def test_cli_planner_seed_apply_blocks_incompatible_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="OtherOrg/other",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    write_planner_seed_manifest(manifest, manifest_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0)

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--body-dir",
            str(body_dir),
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert calls == []
    assert "Status:\n  blocked" in captured
    assert "manifest repo mismatch" in captured
    assert "Planner Seed Apply" not in captured


def test_cli_planner_seed_apply_partial_manifest_continues_missing_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    write_planner_seed_issue_bodies(seed_plan, body_dir)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "partial"
    manifest["issues"][0]["github_issue"] = 301
    manifest["issues"][0]["github_url"] = "https://github.com/ExatronOmega/signposter/issues/301"
    write_planner_seed_manifest(manifest, manifest_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        if command[:3] == ["gh", "label", "list"]:
            return _fake_label_list_result()
        calls.append(command)
        issue_number = 400 + len(calls)
        return _FakeGhIssueCreateResult(
            0,
            stdout=f"https://github.com/ExatronOmega/signposter/issues/{issue_number}",
        )

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--body-dir",
            str(body_dir),
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert len(calls) == 4
    assert saved["status"] == "applied"
    assert saved["issues"][0]["github_issue"] == 301
    assert saved["issues"][1]["github_issue"] == 401
    assert saved["issues"][1]["github_depends_on"] == [301]
    assert "Status:\n  ready" in captured
    assert "Planner Seed Apply" in captured
    assert "WATCH-002 -> #401" in captured


def test_validate_seed_plan_labels_reports_ready() -> None:
    plan = build_planner_draft("build lifecycle watch")
    seed_plan = build_planner_seed_plan(plan)

    result = validate_seed_plan_labels(
        seed_plan,
        {
            "phase:build",
            "risk:low",
            "role:worker",
            "area:cli",
            "area:tests",
            "area:docs",
            "state:ready",
        },
    )

    assert result["status"] == "ready"
    assert result["missing_labels"] == []
    assert result["errors"] == []


def test_validate_seed_plan_labels_reports_missing_labels() -> None:
    plan = build_planner_draft("build lifecycle watch")
    seed_plan = build_planner_seed_plan(plan)

    result = validate_seed_plan_labels(
        seed_plan,
        {"phase:build", "risk:low", "role:worker", "area:tests", "area:docs"},
    )

    assert result["status"] == "blocked"
    assert result["missing_labels"] == ["area:cli", "state:ready"]
    assert result["errors"] == [
        "missing GitHub label: area:cli",
        "missing GitHub label: state:ready",
    ]


def test_format_seed_label_preflight_includes_safety_notes() -> None:
    result = {
        "status": "blocked",
        "required_labels": ["area:cli", "phase:build"],
        "missing_labels": ["area:cli"],
        "errors": ["missing GitHub label: area:cli"],
    }

    output = format_seed_label_preflight(result)

    assert "Seed Label Preflight" in output
    assert "Status:\n  blocked" in output
    assert "missing GitHub label: area:cli" in output
    assert "No GitHub issue was created." in output
    assert "No OpenClaw execution was performed." in output


def test_cli_planner_seed_apply_blocks_missing_label_before_issue_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    issue_create_calls: list[list[str]] = []

    write_planner_draft("build lifecycle watch", plan_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        if command[:3] == ["gh", "label", "list"]:
            return _FakeGhIssueCreateResult(
                0,
                stdout="\n".join(
                    [
                        "phase:build",
                        "risk:low",
                        "role:worker",
                        "area:tests",
                        "area:docs",
                    ]
                ),
            )

        issue_create_calls.append(command)
        return _FakeGhIssueCreateResult(
            0,
            stdout="https://github.com/ExatronOmega/signposter/issues/999",
        )

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--body-dir",
            str(body_dir),
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert exc_info.value.code == 1
    assert issue_create_calls == []
    assert manifest["status"] == "dry-run"
    assert "Seed Label Preflight" in captured
    assert "Status:\n  blocked" in captured
    assert "missing GitHub label: area:cli" in captured
    assert "missing GitHub label: state:ready" in captured
    assert "Planner Seed Apply" not in captured


def test_cli_planner_seed_show_commands_uses_selected_body_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "custom-issue-bodies"
    write_planner_draft("build lifecycle watch", plan_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "seed",
            "--plan",
            str(plan_path),
            "--repo",
            "ExatronOmega/signposter",
            "--body-dir",
            str(body_dir),
            "--show-commands",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert str(body_dir / "WATCH-001.md") in captured
    assert "artifacts/plans/issue-bodies/WATCH-001.md" not in captured
    assert "----- BEGIN GH COMMAND -----" in captured


def test_build_planner_status_reports_unseeded_manifest(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )

    status = build_planner_status(manifest)

    assert status["version"] == "planner.status.v0.1"
    assert status["status"] == "unseeded"
    assert status["tasks"][0]["key"] == "WATCH-001"
    assert status["tasks"][0]["github_issue"] is None
    assert status["tasks"][0]["state"] == "unseeded"


def test_build_planner_status_reports_seeded_active_manifest(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(manifest, {10: "open", 11: "open"})

    assert status["status"] == "active"
    assert status["manifest_status"] == "applied"
    assert status["tasks"][0]["state"] == "open"
    assert status["tasks"][1]["state"] == "open"
    assert status["tasks"][2]["state"] == "unknown"


def test_format_planner_status_contains_safety_notes(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )

    output = format_planner_status(build_planner_status(manifest))

    assert "Signposter Planner Status" in output
    assert "WATCH-001 — issue: none — state: unseeded" in output
    assert "depends on: WATCH-001" in output
    assert "No GitHub mutation was performed." in output
    assert "No OpenClaw execution was performed." in output
    assert "No task execution was performed." in output


def test_build_planner_seed_manifest_materializes_github_ready_dependency_metadata(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)

    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )

    assert manifest["issues"][0]["dependency_metadata"] == []
    assert manifest["issues"][1]["dependency_metadata"] == [
        {"key": "WATCH-001", "github_issue": None, "github_url": ""}
    ]
    assert manifest["issues"][1]["github_depends_on"] == []
    assert manifest["issue_key_map"] == {}


def test_cli_planner_status_prints_local_manifest_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    manifest["issues"][0]["github_issue"] = 10
    manifest["issues"][0]["github_url"] = "https://github.com/ExatronOmega/signposter/issues/10"
    write_planner_seed_manifest(manifest, manifest_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "planner", "status", "--manifest", str(manifest_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Signposter Planner Status" in captured
    assert "WATCH-001 — issue: #10 — state: unknown" in captured
    assert "No GitHub mutation was performed." in captured


def test_cli_planner_status_sync_github_fetches_issue_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    write_planner_seed_manifest(manifest, manifest_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "status",
            "--manifest",
            str(manifest_path),
            "--sync-github",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert len(calls) == 5
    assert calls[0][:4] == ["gh", "issue", "view", "10"]
    assert "WATCH-001 — issue: #10 — state: open" in captured
    assert "No task execution was performed." in captured


def test_build_planner_next_from_status_selects_first_open_ready_task(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(
        manifest,
        {
            10: "open",
            11: "open",
            12: "open",
            13: "open",
            14: "open",
        },
    )

    result = build_planner_next_from_status(status)

    assert result["status"] == "ready"
    assert result["next"]["key"] == "WATCH-001"
    assert result["next"]["github_issue"] == 10
    assert result["waiting"] == []


def test_build_planner_next_from_status_respects_closed_dependencies(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(
        manifest,
        {
            10: "closed",
            11: "open",
            12: "open",
            13: "open",
            14: "open",
        },
    )

    result = build_planner_next_from_status(status)

    assert result["status"] == "ready"
    assert result["next"]["key"] == "WATCH-002"
    assert result["next"]["github_issue"] == 11


def test_build_planner_next_from_status_waits_for_open_dependency(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(
        manifest,
        {
            10: "closed",
            11: "unknown",
            12: "open",
            13: "open",
            14: "open",
        },
    )

    result = build_planner_next_from_status(status)

    assert result["status"] == "waiting"
    assert result["next"] is None
    assert result["blocked"][0]["key"] == "WATCH-002"
    assert result["waiting"][0]["key"] == "WATCH-003"


def test_format_planner_next_from_status_contains_safety_notes(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    manifest["issues"][0]["github_issue"] = 10
    manifest["issues"][0]["github_url"] = "https://github.com/ExatronOmega/signposter/issues/10"
    status = build_planner_status(manifest, {10: "open"})

    output = format_planner_next_from_status(build_planner_next_from_status(status))

    assert "Signposter Planner Next" in output
    assert "WATCH-001 — issue: #10 — state: open" in output
    assert "No GitHub mutation was performed." in output
    assert "No OpenClaw execution was performed." in output
    assert "No task execution was performed." in output


def test_apply_planner_advance_plan_executes_single_ready_mutation(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(manifest, {10: "closed", 11: "open"})
    advance_plan = build_planner_advance_plan_from_status(
        status,
        issue=10,
        manifest_path=str(manifest_path),
    )

    result = apply_planner_advance_plan(
        advance_plan,
        repo="ExatronOmega/signposter",
        run_command=lambda command: calls.append(command),
    )

    assert result == {
        "status": "applied",
        "issue": 10,
        "promoted": [
            {
                "key": "WATCH-002",
                "github_issue": 11,
                "labels_added": ["state:ready"],
            }
        ],
        "commands": [
            "gh issue edit 11 -R ExatronOmega/signposter --add-label state:ready"
        ],
        "errors": [],
    }
    assert calls == [
        ["gh", "issue", "edit", "11", "-R", "ExatronOmega/signposter", "--add-label", "state:ready"]
    ]


def test_apply_planner_advance_plan_refuses_blocked_plan() -> None:
    advance_plan = {
        "status": "blocked",
        "issue": 10,
        "targets": [],
        "planned_github_mutations": [],
        "reasons": ["issue is not completed: state=open"],
    }
    calls: list[list[str]] = []

    result = apply_planner_advance_plan(
        advance_plan,
        repo="ExatronOmega/signposter",
        run_command=lambda command: calls.append(command),
    )

    assert result["status"] == "blocked"
    assert result["promoted"] == []
    assert result["commands"] == []
    assert result["errors"] == ["advance plan is not ready"]
    assert calls == []


def test_apply_planner_advance_plan_requires_exactly_one_target(
    tmp_path: Path,
) -> None:
    advance_plan = {
        "status": "ready",
        "issue": 10,
        "targets": [
            {
                "key": "WATCH-002",
                "github_issue": 11,
                "labels_to_add": ["state:ready"],
            },
            {
                "key": "WATCH-003",
                "github_issue": 12,
                "labels_to_add": ["state:ready"],
            },
        ],
        "planned_github_mutations": [],
        "reasons": [],
    }
    calls: list[list[str]] = []

    result = apply_planner_advance_plan(
        advance_plan,
        repo="ExatronOmega/signposter",
        run_command=lambda command: calls.append(command),
    )

    assert result["status"] == "blocked"
    assert result["promoted"] == []
    assert result["commands"] == []
    assert result["errors"] == ["expected exactly one advance target, found 2"]
    assert calls == []


def test_build_planner_run_plan_from_status_reports_next_open_task(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(
        manifest,
        {
            10: "open",
            11: "open",
            12: "open",
            13: "open",
            14: "open",
        },
    )

    result = build_planner_run_plan_from_status(
        status,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "ready"
    assert result["planner_status"] == "active"
    assert result["next"]["status"] == "ready"
    assert result["next"]["next"]["key"] == "WATCH-001"
    assert result["step"]["suggested_command"] == (
        "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run"
    )
    assert result["advance_candidates"] == []
    assert result["requires_llm_analysis"] is False


def test_build_planner_run_plan_from_status_reports_advance_candidate(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(
        manifest,
        {
            10: "closed",
            11: "open",
            12: "open",
            13: "open",
            14: "open",
        },
    )

    result = build_planner_run_plan_from_status(
        status,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "ready"
    assert result["next"]["next"]["key"] == "WATCH-002"
    assert result["advance_candidates"] == [
        {
            "issue": 10,
            "task_key": "WATCH-001",
            "decision": "advance_mainline",
            "suggested_command": (
                f"signposter planner advance --manifest {manifest_path} "
                "--issue 10 --dry-run"
            ),
            "targets": ["WATCH-002"],
        }
    ]
    assert result["requires_llm_analysis"] is False


def test_format_planner_run_plan_contains_dashboard_sections(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(manifest, {10: "open"})

    output = format_planner_run_plan(
        build_planner_run_plan_from_status(
            status,
            manifest_path=str(manifest_path),
        )
    )

    assert "Signposter Planner Run" in output
    assert "Status:\n  ready" in output
    assert "Planner status:\n  active" in output
    assert "Next task:" in output
    assert "WATCH-001 — issue: #10 — state: open" in output
    assert "Suggested step command:" in output
    assert "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run" in output
    assert "Advance candidates:" in output
    assert "none" in output
    assert "No GitHub mutation was performed." in output
    assert "No manifest mutation was performed." in output
    assert "No claim was performed." in output
    assert "No worktree was created." in output
    assert "No OpenClaw execution was performed." in output
    assert "No LLM analysis was performed." in output


def test_build_planner_advance_plan_from_status_promotes_downstream_task(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(
        manifest,
        {
            10: "closed",
            11: "open",
            12: "open",
            13: "open",
            14: "open",
        },
    )

    result = build_planner_advance_plan_from_status(
        status,
        issue=10,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "ready"
    assert result["issue"] == 10
    assert result["source_task"]["key"] == "WATCH-001"
    assert result["targets"] == [
        {
            "key": "WATCH-002",
            "github_issue": 11,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
            "state": "open",
            "labels_to_add": ["state:ready"],
        }
    ]
    assert result["planned_github_mutations"] == [
        "gh issue edit 11 -R ExatronOmega/signposter --add-label state:ready"
    ]
    assert result["planned_manifest_mutations"] == []
    assert result["requires_llm_analysis"] is False


def test_build_planner_advance_plan_from_status_blocks_open_source_task(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    manifest["issues"][0]["github_issue"] = 10
    manifest["issues"][0]["github_url"] = "https://github.com/ExatronOmega/signposter/issues/10"

    status = build_planner_status(manifest, {10: "open"})

    result = build_planner_advance_plan_from_status(
        status,
        issue=10,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "blocked"
    assert result["targets"] == []
    assert result["planned_github_mutations"] == []
    assert result["planned_manifest_mutations"] == []
    assert result["requires_llm_analysis"] is False
    assert "issue is not completed" in result["reasons"][0]


def test_format_planner_advance_plan_contains_dry_run_mutation_and_safety_notes(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(manifest, {10: "closed", 11: "open"})

    output = format_planner_advance_plan(
        build_planner_advance_plan_from_status(
            status,
            issue=10,
            manifest_path=str(manifest_path),
        )
    )

    assert "Signposter Planner Advance — Issue #10" in output
    assert "Status:\n  ready" in output
    assert "Source task:" in output
    assert "WATCH-001 — state: closed" in output
    assert "Would promote:" in output
    assert "WATCH-002 — issue: #11 — state: open" in output
    assert "Planned GitHub mutations:" in output
    assert "gh issue edit 11 -R ExatronOmega/signposter --add-label state:ready" in output
    assert "No GitHub mutation was performed." in output
    assert "No manifest mutation was performed." in output
    assert "No OpenClaw execution was performed." in output
    assert "No LLM analysis was performed." in output


def test_build_planner_impact_from_status_advances_low_impact_completed_task(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(
        manifest,
        {
            10: "closed",
            11: "open",
            12: "open",
            13: "open",
            14: "open",
        },
    )

    result = build_planner_impact_from_status(
        status,
        issue=10,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "ready"
    assert result["issue"] == 10
    assert result["task"]["key"] == "WATCH-001"
    assert result["impact"]["score"] == 10
    assert result["impact"]["level"] == "low"
    assert result["impact"]["decision"] == "advance_mainline"
    assert result["downstream_tasks"] == ["WATCH-002"]
    assert result["requires_llm_analysis"] is False
    assert result["suggested_command"] == (
        f"signposter planner advance --manifest {manifest_path} --issue 10 --dry-run"
    )


def test_build_planner_impact_from_status_blocks_open_task(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    manifest["issues"][0]["github_issue"] = 10
    manifest["issues"][0]["github_url"] = "https://github.com/ExatronOmega/signposter/issues/10"

    status = build_planner_status(manifest, {10: "open"})

    result = build_planner_impact_from_status(
        status,
        issue=10,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "blocked"
    assert result["impact"]["decision"] == "wait_for_completion"
    assert result["suggested_command"] is None
    assert result["requires_llm_analysis"] is False
    assert "issue is not completed" in result["reasons"][0]


def test_format_planner_impact_contains_score_decision_and_safety_notes(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(manifest, {10: "closed", 11: "open"})

    output = format_planner_impact(
        build_planner_impact_from_status(
            status,
            issue=10,
            manifest_path=str(manifest_path),
        )
    )

    assert "Signposter Planner Impact — Issue #10" in output
    assert "Status:\n  ready" in output
    assert "score: 10" in output
    assert "level: low" in output
    assert "decision: advance_mainline" in output
    assert "downstream: WATCH-002" in output
    assert f"signposter planner advance --manifest {manifest_path} --issue 10 --dry-run" in output
    assert "No GitHub mutation was performed." in output
    assert "No manifest mutation was performed." in output
    assert "No OpenClaw execution was performed." in output
    assert "No LLM analysis was performed." in output


def test_build_planner_step_from_next_suggests_dry_run_command(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    manifest["issues"][0]["github_issue"] = 10
    manifest["issues"][0]["github_url"] = "https://github.com/ExatronOmega/signposter/issues/10"

    status = build_planner_status(manifest, {10: "open"})
    next_result = build_planner_next_from_status(status)

    result = build_planner_step_from_next(next_result)

    assert result["status"] == "ready"
    assert result["next"]["key"] == "WATCH-001"
    assert result["suggested_command"] == (
        "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run"
    )
    assert result["workflow_hints"] == [
        {
            "label": "inspect lifecycle",
            "command": (
                "signposter lifecycle status --repo ExatronOmega/signposter --issue 10"
            ),
        },
        {
            "label": "claim dry-run",
            "command": "signposter claim --repo ExatronOmega/signposter --dry-run",
        },
        {
            "label": "worktree plan",
            "command": (
                "signposter worktree plan --repo ExatronOmega/signposter --issue 10"
            ),
        },
        {
            "label": "run dry-run",
            "command": (
                "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run"
            ),
        },
    ]
    assert result["errors"] == []


def test_build_planner_next_from_status_selects_ready_workflow_state(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"

    status = build_planner_status(manifest, {10: "done", 11: "ready"})

    result = build_planner_next_from_status(status)

    assert result["status"] == "ready"
    assert result["next"]["key"] == "WATCH-002"
    assert result["next"]["state"] == "ready"


def test_format_planner_step_contains_suggested_command_and_safety_notes(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    manifest["issues"][0]["github_issue"] = 10
    manifest["issues"][0]["github_url"] = "https://github.com/ExatronOmega/signposter/issues/10"

    status = build_planner_status(manifest, {10: "open"})
    next_result = build_planner_next_from_status(status)

    output = format_planner_step(build_planner_step_from_next(next_result))

    assert "Signposter Planner Step" in output
    assert "Status:\n  ready" in output
    assert "WATCH-001 — issue: #10 — state: open" in output
    assert "Suggested command:" in output
    assert "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run" in output
    assert "Workflow hints:" in output
    assert "inspect lifecycle:" in output
    assert "signposter lifecycle status --repo ExatronOmega/signposter --issue 10" in output
    assert "claim dry-run:" in output
    assert "signposter claim --repo ExatronOmega/signposter --dry-run" in output
    assert "worktree plan:" in output
    assert "signposter worktree plan --repo ExatronOmega/signposter --issue 10" in output
    assert "Hints only; no command above was executed." in output
    assert "No GitHub mutation was performed." in output
    assert "No claim was performed." in output
    assert "No worktree was created." in output
    assert "No OpenClaw execution was performed." in output
    assert "No task execution was performed." in output


def test_cli_planner_run_dry_run_shows_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "run",
            "--manifest",
            str(manifest_path),
            "--sync-github",
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert len(calls) == 5
    assert calls[0][:4] == ["gh", "issue", "view", "10"]
    assert "Signposter Planner Run" in captured
    assert "Status:\n  ready" in captured
    assert "Planner status:\n  active" in captured
    assert "Next task:" in captured
    assert "WATCH-001 — issue: #10 — state: open" in captured
    assert "Suggested step command:" in captured
    assert "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run" in captured
    assert "Advance candidates:" in captured
    assert "none" in captured
    assert "No GitHub mutation was performed." in captured
    assert "No manifest mutation was performed." in captured
    assert "No claim was performed." in captured
    assert "No worktree was created." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No LLM analysis was performed." in captured


def test_cli_planner_run_sync_github_uses_workflow_state_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        issue_number = command[3]
        if issue_number == "10":
            return _FakeGhIssueCreateResult(
                0,
                stdout=json.dumps(
                    {
                        "state": "OPEN",
                        "labels": [{"name": "state:done"}],
                    }
                ),
            )
        return _FakeGhIssueCreateResult(
            0,
            stdout=json.dumps(
                {
                    "state": "OPEN",
                    "labels": [],
                }
            ),
        )

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "run",
            "--manifest",
            str(manifest_path),
            "--sync-github",
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert len(calls) == 5
    assert "WATCH-002 — issue: #11 — state: open" in captured
    assert "issue #10 / WATCH-001:" in captured
    assert "decision: advance_mainline" in captured
    assert "targets: WATCH-002" in captured
    assert (
        f"signposter planner advance --manifest {manifest_path} --issue 10 --dry-run"
        in captured
    )


def test_cli_planner_run_requires_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "run",
            "--manifest",
            str(manifest_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert "Signposter Planner Run" in captured
    assert "Status:\n  blocked" in captured
    assert "--dry-run is required" in captured
    assert "No GitHub mutation was performed." in captured
    assert "No manifest mutation was performed." in captured
    assert "No claim was performed." in captured
    assert "No worktree was created." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No LLM analysis was performed." in captured


def test_cli_planner_advance_dry_run_promotes_downstream_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        if command[:4] == ["gh", "issue", "view", "10"]:
            return _FakeGhIssueCreateResult(0, stdout="CLOSED\n")
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "advance",
            "--manifest",
            str(manifest_path),
            "--issue",
            "10",
            "--sync-github",
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert len(calls) == 5
    assert calls[0][:4] == ["gh", "issue", "view", "10"]
    assert "Signposter Planner Advance — Issue #10" in captured
    assert "Status:\n  ready" in captured
    assert "WATCH-001 — state: closed" in captured
    assert "Would promote:" in captured
    assert "WATCH-002 — issue: #11 — state: open" in captured
    assert "gh issue edit 11 -R ExatronOmega/signposter --add-label state:ready" in captured
    assert "No GitHub mutation was performed." in captured
    assert "No manifest mutation was performed." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No LLM analysis was performed." in captured


def test_cli_planner_advance_apply_blocks_open_source_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "advance",
            "--manifest",
            str(manifest_path),
            "--issue",
            "10",
            "--sync-github",
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert len(calls) == 5
    assert "Signposter Planner Advance — Issue #10" in captured
    assert "Status:\n  blocked" in captured
    assert "issue is not completed: state=open" in captured
    assert "gh issue edit 11" not in captured


def test_cli_planner_advance_apply_blocks_when_state_ready_label_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        if command[:4] == ["gh", "issue", "view", "10"]:
            return _FakeGhIssueCreateResult(0, stdout="CLOSED\n")
        if command[:3] == ["gh", "label", "list"]:
            return _FakeGhIssueCreateResult(0, stdout="state:active\n")
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "advance",
            "--manifest",
            str(manifest_path),
            "--issue",
            "10",
            "--sync-github",
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert "Planner Advance Label Preflight" in captured
    assert "Status:\n  blocked" in captured
    assert "missing GitHub label: state:ready" in captured
    assert not any(command[:3] == ["gh", "issue", "edit"] for command in calls)


def test_cli_planner_advance_apply_executes_single_label_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        if command[:4] == ["gh", "issue", "view", "10"]:
            return _FakeGhIssueCreateResult(0, stdout="CLOSED\n")
        if command[:3] == ["gh", "label", "list"]:
            return _FakeGhIssueCreateResult(0, stdout="state:ready\n")
        return _FakeGhIssueCreateResult(0, stdout="")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "advance",
            "--manifest",
            str(manifest_path),
            "--issue",
            "10",
            "--sync-github",
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    edit_calls = [command for command in calls if command[:3] == ["gh", "issue", "edit"]]
    assert edit_calls == [
        [
            "gh",
            "issue",
            "edit",
            "11",
            "-R",
            "ExatronOmega/signposter",
            "--add-label",
            "state:ready",
        ]
    ]
    assert "Signposter Planner Advance Apply" in captured
    assert "Status:\n  applied" in captured
    assert "WATCH-002 -> #11 added labels: state:ready" in captured
    assert "No manifest mutation was performed." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No LLM analysis was performed." in captured


def test_cli_planner_advance_requires_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "advance",
            "--manifest",
            str(manifest_path),
            "--issue",
            "10",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert "Signposter Planner Advance" in captured
    assert "Status:\n  blocked" in captured
    assert "--dry-run or --apply is required" in captured
    assert "No GitHub mutation was performed." in captured
    assert "No manifest mutation was performed." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No LLM analysis was performed." in captured


def test_cli_planner_impact_manifest_sync_github_blocks_open_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "impact",
            "--manifest",
            str(manifest_path),
            "--issue",
            "10",
            "--sync-github",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert len(calls) == 5
    assert calls[0][:4] == ["gh", "issue", "view", "10"]
    assert "Signposter Planner Impact — Issue #10" in captured
    assert "Status:\n  blocked" in captured
    assert "decision: wait_for_completion" in captured
    assert "issue is not completed: state=open" in captured
    assert "No GitHub mutation was performed." in captured
    assert "No manifest mutation was performed." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No LLM analysis was performed." in captured


def test_cli_planner_step_manifest_sync_github_suggests_dry_run_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []

    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "step",
            "--manifest",
            str(manifest_path),
            "--sync-github",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert len(calls) == 5
    assert calls[0][:4] == ["gh", "issue", "view", "10"]
    assert "Signposter Planner Step" in captured
    assert "Status:\n  ready" in captured
    assert "WATCH-001 — issue: #10 — state: open" in captured
    assert "Suggested command:" in captured
    assert "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run" in captured
    assert "Workflow hints:" in captured
    assert "signposter lifecycle status --repo ExatronOmega/signposter --issue 10" in captured
    assert "signposter claim --repo ExatronOmega/signposter --dry-run" in captured
    assert "signposter worktree plan --repo ExatronOmega/signposter --issue 10" in captured
    assert "Hints only; no command above was executed." in captured
    assert "No GitHub mutation was performed." in captured
    assert "No claim was performed." in captured
    assert "No worktree was created." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No task execution was performed." in captured


def test_cli_planner_step_missing_manifest_blocks_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_manifest = tmp_path / "missing-seed-manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "step",
            "--manifest",
            str(missing_manifest),
            "--sync-github",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert "Signposter Planner Step" in captured
    assert "Status:\n  blocked" in captured
    assert f"manifest file not found: {missing_manifest}" in captured
    assert "Traceback" not in captured
    assert "No GitHub mutation was performed." in captured
    assert "No claim was performed." in captured
    assert "No worktree was created." in captured
    assert "No OpenClaw execution was performed." in captured
    assert "No task execution was performed." in captured


def test_cli_planner_next_manifest_local_reports_waiting_for_unknown_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    write_planner_seed_manifest(manifest, manifest_path)

    monkeypatch.setattr(
        sys,
        "argv",
        ["signposter", "planner", "next", "--manifest", str(manifest_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Signposter Planner Next" in captured
    assert "Status:\n  waiting" in captured
    assert "WATCH-001 — unsupported task state: unknown" in captured
    assert "No GitHub mutation was performed." in captured


def test_cli_planner_next_manifest_sync_github_selects_ready_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    calls: list[list[str]] = []
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)
    manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
    )
    manifest["status"] = "applied"
    for index, issue in enumerate(manifest["issues"], start=10):
        issue["github_issue"] = index
        issue["github_url"] = f"https://github.com/ExatronOmega/signposter/issues/{index}"
    write_planner_seed_manifest(manifest, manifest_path)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        return _FakeGhIssueCreateResult(0, stdout="OPEN\n")

    monkeypatch.setattr("signposter.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "next",
            "--manifest",
            str(manifest_path),
            "--sync-github",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert len(calls) == 5
    assert calls[0][:4] == ["gh", "issue", "view", "10"]
    assert "Status:\n  ready" in captured
    assert "WATCH-001 — issue: #10 — state: open" in captured
    assert "No OpenClaw execution was performed." in captured


def test_cli_planner_next_requires_plan_or_manifest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["signposter", "planner", "next"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "Status:\n  blocked" in captured
    assert "either --plan or --manifest is required" in captured
