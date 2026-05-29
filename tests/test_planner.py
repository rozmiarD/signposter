from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from signposter.cli import main
from signposter.planner import (
    PLAN_VERSION,
    apply_planner_seed_manifest,
    build_planner_draft,
    build_planner_next,
    build_planner_seed_manifest,
    build_planner_seed_plan,
    evaluate_worker_issue_body_size,
    format_gh_issue_create_command,
    format_planner_issue_body,
    format_planner_roadmap,
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
        labels=["phase:build", "risk:low", "role:worker", "area:cli"],
    )

    assert command.startswith("gh \\\n  issue \\\n  create")
    assert "--repo \\\n  ExatronOmega/signposter" in command
    assert "--body-file \\\n  artifacts/plans/issue-bodies/WATCH-001.md" in command
    assert "--label \\\n  phase:build" in command
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
    assert "Status:\n  ready" in captured
    assert "Planner Seed Apply" in captured
    assert "WATCH-002 -> #401" in captured


def test_validate_seed_plan_labels_reports_ready() -> None:
    plan = build_planner_draft("build lifecycle watch")
    seed_plan = build_planner_seed_plan(plan)

    result = validate_seed_plan_labels(
        seed_plan,
        {"phase:build", "risk:low", "role:worker", "area:cli", "area:tests", "area:docs"},
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
    assert result["missing_labels"] == ["area:cli"]
    assert result["errors"] == ["missing GitHub label: area:cli"]


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
    assert "Planner Seed Apply" not in captured
