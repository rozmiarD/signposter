from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from signposter.cli import main
from signposter.planner import (
    NEXT_ROADMAP_MIN_DAG_NODES,
    PLAN_VERSION,
    apply_planner_advance_plan,
    apply_planner_seed_manifest,
    build_next_roadmap_bootstrap_contract,
    build_next_roadmap_bootstrap_status_artifact,
    build_planner_advance_plan_from_status,
    build_planner_draft,
    build_planner_impact_from_status,
    build_planner_next,
    build_planner_next_from_status,
    build_planner_regeneration_plan,
    build_planner_run_plan_from_status,
    build_planner_seed_manifest,
    build_planner_seed_plan,
    build_planner_side_task_plan,
    build_planner_status,
    build_planner_status_artifact,
    build_planner_status_counts,
    build_planner_step_from_next,
    classify_planner_task,
    evaluate_worker_issue_body_size,
    format_gh_issue_create_command,
    format_next_roadmap_bootstrap_contract,
    format_planner_advance_apply_result,
    format_planner_advance_plan,
    format_planner_impact,
    format_planner_issue_body,
    format_planner_next_from_status,
    format_planner_regeneration_plan,
    format_planner_roadmap,
    format_planner_run_plan,
    format_planner_seed_plan,
    format_planner_side_task_plan,
    format_planner_status,
    format_planner_step,
    format_seed_label_preflight,
    mark_planner_task,
    prepare_planner_seed_manifest,
    validate_next_roadmap_bootstrap_contract,
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


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Closed #123",
        "Fixed issue #123",
        "Resolve https://github.com/acme/project/issues/123",
    ],
)
def test_validate_planner_plan_rejects_extended_auto_close_variants(
    unsafe_text: str,
) -> None:
    plan = build_planner_draft("build lifecycle watch")
    plan["issues"][0]["acceptance"] = [unsafe_text]

    errors = validate_planner_plan(plan)

    assert "WATCH-001: contains auto-close keyword" in errors


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


def test_format_planner_issue_body_contains_dependency_metadata() -> None:
    plan = build_planner_draft("build lifecycle watch")
    issue = plan["issues"][1]

    body = format_planner_issue_body(plan, issue)

    assert "Dependencies:\n* WATCH-001" in body
    assert "Dependency metadata:" in body
    assert "* key: WATCH-001" in body
    assert "github issue: assigned during guarded seed apply" in body
    assert "status: pending" in body


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
    assert "Next-roadmap bootstrap contract:" in roadmap
    assert "Done definition:" in roadmap
    assert "Preferred range: 60–120 lines." in roadmap
    assert f"at least {NEXT_ROADMAP_MIN_DAG_NODES} small dependency-aware DAG nodes" in roadmap
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


def test_classify_planner_task_assigns_docs_only_lightweight_profile() -> None:
    result = classify_planner_task(
        {
            "key": "DOC-001",
            "title": "README operator wording update",
            "body": "Update README only; no behavior change.",
            "risk": "low",
            "area": "docs",
        }
    )

    assert result["scope_class"] == "narrow"
    assert result["validation_profile"] == "docs-only"
    assert result["dry_run_policy"] == "optional"
    assert "python -m pytest tests/ -q" not in result["required_evidence"]


def test_classify_planner_task_assigns_planner_targeted_plus_lint() -> None:
    result = classify_planner_task(
        {
            "key": "PLAN-001",
            "title": "Planner output formatting task",
            "body": "Adjust planner output and tests.",
            "risk": "medium",
            "area": "planner",
        }
    )

    assert result["scope_class"] == "normal"
    assert result["validation_profile"] == "targeted-plus-lint"
    assert result["dry_run_policy"] == "required"
    assert "python -m pytest tests/test_planner.py -q" in result["required_evidence"]


def test_classify_planner_task_assigns_full_suite_for_shared_safety() -> None:
    result = classify_planner_task(
        {
            "key": "SAFE-001",
            "title": "Lifecycle mutation boundary hardening",
            "body": "Change lifecycle and merge gate behavior.",
            "risk": "high",
            "area": "lifecycle",
        }
    )

    assert result["scope_class"] == "normal"
    assert result["validation_profile"] == "full-suite"
    assert result["dry_run_policy"] == "required"
    assert "python -m pytest tests/ -q" in result["required_evidence"]


def test_classify_planner_task_uses_wide_only_for_broad_work() -> None:
    narrow = classify_planner_task(
        {
            "key": "SMALL-001",
            "title": "One small CLI wording fix",
            "body": "One localized output wording adjustment.",
            "risk": "low",
            "area": "cli",
        }
    )
    wide = classify_planner_task(
        {
            "key": "WIDE-001",
            "title": "Architecture roadmap bootstrap",
            "body": "Regenerate roadmap and split global architecture work.",
            "risk": "medium",
            "area": "planner",
        }
    )

    assert narrow["scope_class"] == "narrow"
    assert wide["scope_class"] == "wide"
    assert wide["dry_run_policy"] == "required"


def test_build_planner_regeneration_plan_is_deterministic_and_bounded() -> None:
    manifest = {
        "repo": "ExatronOmega/signposter",
        "plan": "docs/roadmaps/h051-plan.json",
        "issues": [
            {
                "key": "H051-017",
                "title": "H051-017 — Planner advance retry-safe output",
                "labels": ["risk:high", "area:planner"],
            },
            {
                "key": "H051-018",
                "title": "H051-018 — No duplicate mutation after timeout regression",
                "labels": ["risk:medium", "area:planner"],
            },
            {
                "key": "H051-019",
                "title": "H051-019 — Operator-visible GitHub stall guidance",
                "labels": ["risk:low", "area:docs"],
            },
            {
                "key": "H051-020",
                "title": "H051-020 — GitHub command stderr bounded output",
                "labels": ["risk:medium", "area:github"],
            },
            {
                "key": "H051-080",
                "title": "H051-080 — H051 final audit and H052 bootstrap",
                "labels": ["risk:high", "area:planner"],
            },
        ],
    }
    plan = {
        "goal": "H051 - autonomous Signposter reliability hardening",
        "issues": [
            {
                "key": "H051-019",
                "body": "Operator docs-only guidance for GitHub stalls.",
            }
        ],
    }

    result = build_planner_regeneration_plan(
        manifest=manifest,
        manifest_path="docs/roadmaps/h051-seed-manifest.json",
        plan=plan,
    )
    output = format_planner_regeneration_plan(result)

    assert result["status"] == "ready"
    assert result["tasks_inspected"] == 5
    assert result["tasks_kept"] == 5
    assert result["tasks_expanded"] == 0
    assert result["policy"]["llm_analysis"] is False
    assert result["preserved_tasks"][0]["key"] == "H051-017"
    assert all(
        "H051-017" not in update["keys"]
        for update in result["proposed_issue_updates"]
    )
    assert "Signposter Planner Regeneration" in output
    assert "tasks inspected: 5" in output
    assert "scope classifier: enabled" in output
    assert "validation profiles: enabled" in output
    assert "dry-run optimization: enabled" in output
    assert "H051-017" in output
    assert "No GitHub mutation was performed." in output
    assert "No backend execution was performed." in output


def test_cli_planner_regenerate_dry_run_no_github_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    plan_path = tmp_path / "plan.json"
    manifest = {
        "repo": "ExatronOmega/signposter",
        "plan": str(plan_path),
        "issues": [
            {
                "key": "H051-019",
                "title": "H051-019 — Operator-visible GitHub stall guidance",
                "labels": ["risk:low", "area:docs"],
            }
        ],
    }
    plan = {"goal": "H051 - autonomous Signposter reliability hardening", "issues": []}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("planner regenerate dry-run must not call subprocess")

    monkeypatch.setattr("signposter.cli.subprocess.run", fail_if_called)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "regenerate",
            "--repo",
            "ExatronOmega/signposter",
            "--manifest",
            str(manifest_path),
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert "Signposter Planner Regeneration" in captured
    assert "Status:\n  ready" in captured
    assert "validation: docs-only" in captured
    assert "No GitHub mutation was performed." in captured
    assert "No backend execution was performed." in captured


def test_cli_planner_regenerate_apply_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    manifest_path.write_text(json.dumps({"issues": []}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "regenerate",
            "--manifest",
            str(manifest_path),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert "Status:\n  blocked" in captured
    assert "--apply is not implemented for planner regeneration" in captured
    assert "No GitHub mutation was performed." in captured


def test_next_roadmap_bootstrap_contract_formats_ready_output() -> None:
    contract = build_next_roadmap_bootstrap_contract(
        current_prefix="H050",
        next_prefix="H051",
    )

    errors = validate_next_roadmap_bootstrap_contract(contract)
    output = format_next_roadmap_bootstrap_contract(contract)

    assert errors == []
    assert output.startswith("Signposter Next Roadmap Bootstrap Contract")
    assert "Status:\nready" in output
    assert "current: H050" in output
    assert "next: H051" in output
    assert "minimum DAG nodes: 80" in output
    assert "final current-roadmap completion audit" in output
    assert "run seed/sync dry-run before any GitHub mutation" in output
    assert "first eligible next task is identified" in output
    assert "No GitHub mutation was performed." in output
    assert "No issue was closed." in output


def test_next_roadmap_bootstrap_contract_blocks_unsafe_contract() -> None:
    contract = build_next_roadmap_bootstrap_contract(
        current_prefix="H050",
        next_prefix="H050",
        minimum_dag_nodes=12,
    )
    contract["required_steps"].remove("run seed/sync dry-run before any GitHub mutation")
    contract["safety_rules"] = []

    errors = validate_next_roadmap_bootstrap_contract(contract)
    output = format_next_roadmap_bootstrap_contract(contract)

    assert "next_prefix must differ from current_prefix" in errors
    assert "minimum_dag_nodes must be at least 80" in errors
    assert (
        "required_steps missing required item: run seed/sync dry-run before any GitHub mutation"
        in errors
    )
    expected_safety_error = (
        "safety_rules missing required item: "
        "GitHub mutation only through guarded Signposter --apply paths"
    )
    assert expected_safety_error in errors
    assert "Status:\nblocked" in output
    assert "Validation errors:" in output
    assert "No GitHub mutation was performed." in output


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


def test_format_planner_seed_plan_command_preview_is_deterministic(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)

    first = format_planner_seed_plan(
        plan_path,
        seed_plan,
        repo="ExatronOmega/signposter",
        body_dir=body_dir,
        show_commands=True,
    )
    second = format_planner_seed_plan(
        plan_path,
        seed_plan,
        repo="ExatronOmega/signposter",
        body_dir=body_dir,
        show_commands=True,
    )

    assert second == first
    assert "command preview:" in first
    assert "----- BEGIN GH COMMAND -----" in first
    assert "----- END GH COMMAND -----" in first
    assert "Command previews are not executed." in first
    assert "No GitHub mutation was performed." in first
    assert "No GitHub issue was created." in first
    assert "Planner Seed Apply" not in first
    assert "Seed Label Preflight" not in first
    assert "Executed:" not in first


def test_cli_planner_seed_dry_run_preview_excludes_apply_sections(
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
    assert "Signposter Planner Seed" in captured
    assert "command preview:" in captured
    assert "Dry-run only." in captured
    assert "Command previews are not executed." in captured
    assert "No GitHub mutation was performed." in captured
    assert "No GitHub issue was created." in captured
    assert "Planner Seed Apply" not in captured
    assert "Seed Label Preflight" not in captured
    assert "Written issue body files:" not in captured
    assert "Written seed manifest:" not in captured


def test_cli_planner_seed_write_manifest_without_apply_never_runs_apply_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    write_planner_draft("build lifecycle watch", plan_path)

    def fail_label_preflight(repo: str) -> set[str]:
        raise AssertionError(f"label preflight must not run in dry-run mode: {repo}")

    def fail_seed_apply(*args, **kwargs):
        raise AssertionError("seed apply must not run without --apply")

    monkeypatch.setattr("signposter.cli._fetch_repo_label_names", fail_label_preflight)
    monkeypatch.setattr("signposter.cli.apply_planner_seed_manifest", fail_seed_apply)
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
            "--show-commands",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr().out

    assert exc_info.value.code in (None, 0)
    assert manifest["status"] == "dry-run"
    assert manifest["issues"][0]["github_issue"] is None
    assert (body_dir / "WATCH-001.md").exists()
    assert "command preview:" in captured
    assert "Command previews are not executed." in captured
    assert "Prepared seed manifest:" in captured
    assert "No GitHub mutation was performed during manifest preparation." in captured
    assert "No GitHub issue was created." in captured
    assert "Seed Label Preflight" not in captured
    assert "Planner Seed Apply" not in captured
    assert "Executed:" not in captured


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
            "status": "resolved",
        }
    ]


def test_apply_planner_seed_manifest_completed_manifest_is_noop(
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
        issue["github_issue"] = 200 + index
        issue["github_url"] = (
            f"https://github.com/ExatronOmega/signposter/issues/{200 + index}"
        )
    write_planner_seed_manifest(manifest, manifest_path)
    runner = _FakeGhIssueCreateRunner()

    result = apply_planner_seed_manifest(manifest_path, runner)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "applied"
    assert result["created"] == []
    assert result["errors"] == []
    assert runner.calls == []
    assert saved["status"] == "applied"
    assert saved["issues"][0]["github_issue"] == 201
    assert saved["issues"][4]["github_issue"] == 205


def test_apply_planner_seed_manifest_partial_manifest_skips_existing_issues(
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
    manifest["status"] = "partial"
    for index, issue in enumerate(manifest["issues"][:2], start=1):
        issue["github_issue"] = 300 + index
        issue["github_url"] = (
            f"https://github.com/ExatronOmega/signposter/issues/{300 + index}"
        )
    write_planner_seed_manifest(manifest, manifest_path)
    runner = _FakeGhIssueCreateRunner()

    result = apply_planner_seed_manifest(manifest_path, runner)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    created_keys = [created["key"] for created in result["created"]]
    created_titles = [
        command[command.index("--title") + 1] for command in runner.calls
    ]
    assert result["status"] == "applied"
    assert created_keys == ["WATCH-003", "WATCH-004", "WATCH-005"]
    assert created_titles == [
        issue["github_title"] for issue in seed_plan["issues"][2:]
    ]
    assert all("WATCH-001" not in title for title in created_titles)
    assert all("WATCH-002" not in title for title in created_titles)
    assert saved["issues"][0]["github_issue"] == 301
    assert saved["issues"][1]["github_issue"] == 302
    assert saved["issues"][2]["github_issue"] == 101
    assert saved["issues"][2]["github_depends_on"] == [302]


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


def test_apply_planner_seed_manifest_blocks_duplicate_task_key_before_create(
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
    manifest["issues"].append(dict(manifest["issues"][0]))
    write_planner_seed_manifest(manifest, manifest_path)
    runner = _FakeGhIssueCreateRunner()

    result = apply_planner_seed_manifest(manifest_path, runner)

    assert result["status"] == "blocked"
    assert result["created"] == []
    assert result["errors"] == ["duplicate task key in seed manifest: WATCH-001"]
    assert runner.calls == []


def test_apply_planner_seed_manifest_blocks_duplicate_github_issue_mapping(
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
    manifest["issues"][0]["github_issue"] = 101
    manifest["issues"][1]["github_issue"] = 101
    write_planner_seed_manifest(manifest, manifest_path)
    runner = _FakeGhIssueCreateRunner()

    result = apply_planner_seed_manifest(manifest_path, runner)

    assert result["status"] == "blocked"
    assert result["created"] == []
    assert result["errors"] == [
        "duplicate GitHub issue mapping: #101 is assigned to WATCH-001 and WATCH-002"
    ]
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


def test_apply_planner_seed_manifest_stops_after_partial_create_failure(
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
    calls: list[list[str]] = []

    def partially_failing_runner(args: list[str]) -> _FakeGhIssueCreateResult:
        calls.append(args)
        if len(calls) == 1:
            return _FakeGhIssueCreateResult(
                0,
                stdout="https://github.com/ExatronOmega/signposter/issues/501",
            )
        return _FakeGhIssueCreateResult(1, stderr="second create broke")

    result = apply_planner_seed_manifest(manifest_path, partially_failing_runner)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["created"] == [
        {
            "key": "WATCH-001",
            "github_issue": 501,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/501",
        }
    ]
    assert result["errors"] == ["second create broke"]
    assert len(calls) == 2
    assert saved["status"] == "partial"
    assert saved["issues"][0]["github_issue"] == 501
    assert saved["issues"][1]["github_issue"] is None
    assert saved["issues"][2]["github_issue"] is None


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


def test_cli_planner_seed_apply_reports_partial_create_before_failure(
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
        if len(calls) == 1:
            return _FakeGhIssueCreateResult(
                0,
                stdout="https://github.com/ExatronOmega/signposter/issues/601",
            )
        return _FakeGhIssueCreateResult(1, stderr="second create broke")

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
    assert len(calls) == 2
    assert manifest["status"] == "partial"
    assert manifest["issues"][0]["github_issue"] == 601
    assert manifest["issues"][1]["github_issue"] is None
    assert "Planner Seed Apply" in captured
    assert "Status:\n  failed" in captured
    assert "WATCH-001 -> #601" in captured
    assert "second create broke" in captured
    assert "WATCH-003 ->" not in captured


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


def test_prepare_planner_seed_manifest_blocks_duplicate_existing_task_key(
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
    manifest["issues"].append(dict(manifest["issues"][0]))
    write_planner_seed_manifest(manifest, manifest_path)

    result = prepare_planner_seed_manifest(
        plan_path=plan_path,
        repo="ExatronOmega/signposter",
        seed_plan=seed_plan,
        body_dir=body_dir,
        manifest_path=manifest_path,
    )

    assert result["status"] == "blocked"
    assert "duplicate task key in seed manifest: WATCH-001" in result["errors"]
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
    assert "Label preflight runs before any GitHub issue creation." in output
    assert "Missing labels block before any GitHub issue creation." in output
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
    assert "Label preflight runs before any GitHub issue creation." in captured
    assert "Missing labels block before any GitHub issue creation." in captured
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


def test_build_planner_status_surfaces_stale_and_mismatched_issue_mappings(
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
            10: {
                "state": "stale",
                "github_state": "missing",
                "workflow_state": None,
                "mapping_status": "stale",
                "mapping_reason": "issue not found",
            },
            11: {
                "state": "open",
                "github_state": "open",
                "workflow_state": None,
                "mapping_status": "mismatched",
                "mapping_reason": "GitHub issue title does not match planner manifest",
                "github_title": "Unexpected title",
            },
        },
    )

    counts = build_planner_status_counts(status["tasks"])
    next_result = build_planner_next_from_status(status)
    output = format_planner_status(status)
    next_output = format_planner_next_from_status(next_result)

    assert counts["blocked"] == 2
    assert status["tasks"][0]["mapping_status"] == "stale"
    assert status["tasks"][1]["mapping_status"] == "mismatched"
    assert status["tasks"][1]["expected_title"] == status["tasks"][1]["title"]
    assert status["tasks"][1]["github_title"] == "Unexpected title"
    assert next_result["status"] == "blocked"
    assert next_result["blocked"][0]["reason"] == (
        "GitHub issue mapping is stale: issue not found"
    )
    assert next_result["blocked"][1]["reason"] == (
        "GitHub issue mapping is mismatched: "
        "GitHub issue title does not match planner manifest"
    )
    assert next_result["blocked"][1]["expected_title"] == status["tasks"][1]["title"]
    assert next_result["blocked"][1]["github_title"] == "Unexpected title"
    assert "mapping: stale — issue not found" in output
    assert (
        "mapping: mismatched — GitHub issue title does not match planner manifest"
        in output
    )
    assert f"expected title: {status['tasks'][1]['title']}" in output
    assert "GitHub title: Unexpected title" in output
    assert f"expected title: {status['tasks'][1]['title']}" in next_output
    assert "GitHub title: Unexpected title" in next_output


def test_build_planner_status_artifact_is_compact_and_recovery_oriented(
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
            11: {
                "state": "open",
                "github_state": "open",
                "workflow_state": None,
                "mapping_status": "mismatched",
                "mapping_reason": "GitHub issue title does not match planner manifest",
                "github_title": "Unexpected title",
            },
        },
    )

    artifact = build_planner_status_artifact(
        status,
        manifest_path=str(manifest_path),
    )

    assert artifact["version"] == "planner.status-artifact.v0.1"
    assert artifact["manifest"] == str(manifest_path)
    assert artifact["task_counts"]["blocked"] == 1
    assert artifact["next_roadmap_bootstrap"]["status"] == "not-found"
    assert artifact["tasks"][0] == {
        "key": "WATCH-001",
        "github_issue": 10,
        "state": "closed",
        "depends_on": [],
    }
    assert artifact["tasks"][1]["mapping_status"] == "mismatched"
    assert artifact["tasks"][1]["expected_title"] == status["tasks"][1]["title"]
    assert artifact["tasks"][1]["github_title"] == "Unexpected title"
    assert "body_file" not in artifact["tasks"][0]
    assert "github_url" not in artifact["tasks"][0]
    assert "No GitHub mutation was performed." in artifact["notes"]


def test_build_next_roadmap_bootstrap_status_artifact_reports_locked_task() -> None:
    status = {
        "manifest_status": "applied",
        "repo": "ExatronOmega/signposter",
        "status": "active",
        "notes": ["No GitHub mutation was performed."],
        "tasks": [
            {
                "key": "H051-078",
                "title": "H051-078 — Final hardening audit",
                "github_issue": 608,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/608",
                "state": "merged",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H051-079",
                "title": "H051-079 — Final roadmap readiness smoke",
                "github_issue": 609,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/609",
                "state": "open",
                "depends_on": ["H051-078"],
                "labels": [],
            },
            {
                "key": "H051-080",
                "title": "H051-080 — H051 final audit and H052 bootstrap",
                "github_issue": 610,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/610",
                "state": "open",
                "depends_on": ["H051-078", "H051-079"],
                "labels": [],
            },
        ],
    }

    artifact = build_next_roadmap_bootstrap_status_artifact(status)
    output = format_planner_status(status)

    assert artifact["version"] == "planner.next-roadmap-bootstrap-status.v0.1"
    assert artifact["status"] == "locked"
    assert artifact["final_tasks"][0]["key"] == "H051-080"
    assert artifact["final_tasks"][0]["status"] == "locked"
    assert artifact["final_tasks"][0]["current_prefix"] == "H051"
    assert artifact["final_tasks"][0]["next_prefix"] == "H052"
    assert artifact["final_tasks"][0]["waiting_on"] == ["H051-079"]
    assert artifact["final_tasks"][0]["dependency_count"] == 2
    assert "No GitHub mutation was performed." in artifact["notes"]
    assert "Next-roadmap bootstrap:" in output
    assert "status: locked" in output
    assert "final task: H051-080 — issue: #610 — state: open" in output
    assert "transition: H051 -> H052" in output
    assert "minimum DAG nodes: 80" in output
    assert "waiting on: H051-079" in output


def test_build_next_roadmap_bootstrap_status_artifact_reports_ready_task() -> None:
    status = {
        "manifest_status": "applied",
        "repo": "ExatronOmega/signposter",
        "status": "active",
        "notes": ["No GitHub mutation was performed."],
        "tasks": [
            {
                "key": "H051-078",
                "title": "H051-078 — Final hardening audit",
                "github_issue": 608,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/608",
                "state": "merged",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H051-079",
                "title": "H051-079 — Final roadmap readiness smoke",
                "github_issue": 609,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/609",
                "state": "merged",
                "depends_on": ["H051-078"],
                "labels": [],
            },
            {
                "key": "H051-080",
                "title": "H051-080 — H051 final audit and H052 bootstrap",
                "github_issue": 610,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/610",
                "state": "open",
                "depends_on": ["H051-078", "H051-079"],
                "labels": [],
            },
        ],
    }

    artifact = build_next_roadmap_bootstrap_status_artifact(status)
    output = format_planner_status(status)

    assert artifact["status"] == "ready"
    assert artifact["final_tasks"][0]["status"] == "ready"
    assert artifact["final_tasks"][0]["waiting_on"] == []
    assert "status: ready" in output
    assert "waiting on:" not in output


def test_build_planner_status_counts_groups_lifecycle_buckets() -> None:
    counts = build_planner_status_counts(
        [
            {"key": "UNSEEDED", "state": "unseeded"},
            {"key": "OPEN-BLOCKED", "state": "open", "depends_on": []},
            {"key": "OPEN-READY", "state": "open", "workflow_state": "ready"},
            {"key": "READY", "state": "ready"},
            {"key": "WAITING", "state": "open", "depends_on": ["MISSING"]},
            {"key": "ACTIVE", "state": "active"},
            {"key": "DONE", "state": "done"},
            {"key": "MERGED", "state": "merged"},
            {"key": "CLOSED", "state": "closed"},
            {"key": "BLOCKED", "state": "blocked"},
            {"key": "FAILED", "state": "failed"},
        ]
    )

    assert counts == {
        "total": 11,
        "pending": 1,
        "unseeded": 1,
        "ready": 2,
        "waiting": 1,
        "active": 1,
        "done": 1,
        "merged": 1,
        "blocked": 3,
        "completed": 3,
    }


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
    assert "Progress:" in output
    assert "  pending: 5" in output
    assert "  unseeded: 5" in output
    assert "  ready: 0" in output
    assert "  waiting: 0" in output
    assert "WATCH-001 — issue: none — state: unseeded" in output
    assert "depends on: WATCH-001" in output
    assert "Unseeded tasks have no GitHub issue yet." in output
    assert (
        "Open tasks need state:ready or unfinished dependencies to avoid blocked "
        "classification."
    ) in output
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
        {
            "key": "WATCH-001",
            "github_issue": None,
            "github_url": "",
            "status": "pending",
        }
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


def test_cli_planner_status_out_writes_compact_status_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    manifest_path = tmp_path / "seed-manifest.json"
    out_path = tmp_path / "artifacts" / "roadmap-status.json"
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
        [
            "signposter",
            "planner",
            "status",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    artifact = json.loads(out_path.read_text(encoding="utf-8"))

    assert exc_info.value.code in (None, 0)
    assert artifact["version"] == "planner.status-artifact.v0.1"
    assert artifact["manifest"] == str(manifest_path)
    assert artifact["task_counts"]["total"] == 5
    assert artifact["tasks"][0]["key"] == "WATCH-001"
    assert "Artifact:" in captured
    assert f"roadmap status: {out_path}" in captured
    assert "Local file only." in captured
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
        timeout: int | None = None,
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


def test_cli_planner_status_sync_github_surfaces_stale_and_mismatched_mappings(
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
        timeout: int | None = None,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        issue_number = command[3]
        if issue_number == "10":
            return _FakeGhIssueCreateResult(1, stderr="GraphQL: could not resolve")
        title = (
            "Unexpected title"
            if issue_number == "11"
            else manifest["issues"][int(issue_number) - 10]["title"]
        )
        return _FakeGhIssueCreateResult(
            0,
            stdout=json.dumps({"state": "OPEN", "labels": [], "title": title}),
        )

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
    assert calls[0][-1] == "state,labels,title"
    assert "WATCH-001 — issue: #10 — state: stale" in captured
    assert "mapping: stale — GraphQL: could not resolve" in captured
    assert "WATCH-002 — issue: #11 — state: open" in captured
    assert (
        "mapping: mismatched — GitHub issue title does not match planner manifest"
        in captured
    )
    assert "expected title: " + manifest["issues"][1]["title"] in captured
    assert "GitHub title: Unexpected title" in captured
    assert "No GitHub mutation was performed." in captured


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
    assert result["waiting"][0]["key"] == "WATCH-002"
    assert result["waiting"][0]["missing_dependencies"] == ["WATCH-001"]


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
            11: "ready",
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


def test_planner_root_ready_waiting_and_dependency_ready_consistency(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    body_dir = tmp_path / "issue-bodies"
    plan = write_planner_draft("build lifecycle watch", plan_path)
    seed_plan = build_planner_seed_plan(plan)

    assert "state:ready" in seed_plan["issues"][0]["labels"]
    assert "state:ready" not in seed_plan["issues"][1]["labels"]

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

    root_open_status = build_planner_status(
        manifest,
        {10: "open", 11: "open", 12: "open", 13: "open", 14: "open"},
    )
    root_result = build_planner_next_from_status(root_open_status)

    assert root_result["status"] == "ready"
    assert root_result["next"]["key"] == "WATCH-001"
    assert root_result["waiting"][0]["key"] == "WATCH-002"
    assert root_result["waiting"][0]["missing_dependencies"] == ["WATCH-001"]

    missing_ready_status = build_planner_status(
        manifest,
        {10: "done", 11: "open", 12: "open", 13: "open", 14: "open"},
    )
    missing_ready_result = build_planner_next_from_status(missing_ready_status)

    assert missing_ready_result["status"] == "blocked"
    assert missing_ready_result["next"] is None
    assert missing_ready_result["blocked"][0]["key"] == "WATCH-002"
    assert missing_ready_result["blocked"][0]["reconcile_issues"] == [10]

    dependency_ready_status = build_planner_status(
        manifest,
        {10: "done", 11: "ready", 12: "open", 13: "open", 14: "open"},
    )
    dependency_ready_result = build_planner_next_from_status(dependency_ready_status)

    assert dependency_ready_result["status"] == "ready"
    assert dependency_ready_result["next"]["key"] == "WATCH-002"
    assert dependency_ready_result["next"]["state"] == "ready"


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

    output = format_planner_advance_apply_result(result)
    assert "Status detail:" in output
    assert (
        "applied — GitHub label mutations listed below were executed because "
        "--apply was provided."
    ) in output
    assert "Issue closure was not performed." in output


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

    output = format_planner_advance_apply_result(result)
    assert "Status detail:" in output
    assert "blocked — no GitHub label mutation was executed." in output


def test_apply_planner_advance_plan_executes_multi_target_apply(
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

    assert result["status"] == "applied"
    assert result["promoted"] == [
        {
            "key": "WATCH-002",
            "github_issue": 11,
            "labels_added": ["state:ready"],
        },
        {
            "key": "WATCH-003",
            "github_issue": 12,
            "labels_added": ["state:ready"],
        },
    ]
    assert result["commands"] == [
        "gh issue edit 11 -R ExatronOmega/signposter --add-label state:ready",
        "gh issue edit 12 -R ExatronOmega/signposter --add-label state:ready",
    ]
    assert result["errors"] == []
    assert calls == [
        [
            "gh", "issue", "edit", "11", "-R", "ExatronOmega/signposter",
            "--add-label", "state:ready",
        ],
        [
            "gh", "issue", "edit", "12", "-R", "ExatronOmega/signposter",
            "--add-label", "state:ready",
        ],
    ]


def test_apply_planner_advance_plan_stops_after_failed_mutation() -> None:
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
            {
                "key": "WATCH-004",
                "github_issue": 13,
                "labels_to_add": ["state:ready"],
            },
        ],
        "planned_github_mutations": [],
        "reasons": [],
    }
    calls: list[list[str]] = []

    def fake_run(command: list[str]) -> _FakeGhIssueCreateResult:
        calls.append(command)
        if command[3] == "12":
            return _FakeGhIssueCreateResult(
                1,
                stdout="",
                stderr="label update rejected",
            )
        return _FakeGhIssueCreateResult(0, stdout="")

    result = apply_planner_advance_plan(
        advance_plan,
        repo="ExatronOmega/signposter",
        run_command=fake_run,
    )

    assert result["status"] == "partial"
    assert result["promoted"] == [
        {
            "key": "WATCH-002",
            "github_issue": 11,
            "labels_added": ["state:ready"],
        }
    ]
    assert result["commands"] == [
        "gh issue edit 11 -R ExatronOmega/signposter --add-label state:ready"
    ]
    assert result["failed"] == [
        {
            "key": "WATCH-003",
            "github_issue": 12,
            "command": (
                "gh issue edit 12 -R ExatronOmega/signposter "
                "--add-label state:ready"
            ),
            "status": "failed",
            "returncode": 1,
            "stdout": "",
            "stderr": "label update rejected",
        }
    ]
    assert result["skipped"] == [{"key": "WATCH-004", "github_issue": 13}]
    assert result["errors"] == [
        "stopped after failed promoting WATCH-003 (#12)"
    ]
    assert [command[3] for command in calls] == ["11", "12"]

    output = format_planner_advance_apply_result(result)
    assert "Status:\n  partial" in output
    assert "Failed mutation:" in output
    assert "WATCH-003 -> #12" in output
    assert "Skipped mutations after stop:" in output
    assert "WATCH-004 -> #13" in output
    assert "No later GitHub mutation was attempted after the failed command." in output


def test_apply_planner_advance_plan_blocks_after_timeout_before_mutation() -> None:
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

    def fake_run(command: list[str]) -> _FakeGhIssueCreateResult:
        calls.append(command)
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=30,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    result = apply_planner_advance_plan(
        advance_plan,
        repo="ExatronOmega/signposter",
        run_command=fake_run,
    )

    assert result["status"] == "blocked"
    assert result["promoted"] == []
    assert result["commands"] == []
    assert result["failed"] == [
        {
            "key": "WATCH-002",
            "github_issue": 11,
            "command": (
                "gh issue edit 11 -R ExatronOmega/signposter "
                "--add-label state:ready"
            ),
            "status": "timeout",
            "returncode": None,
            "stdout": "partial stdout",
            "stderr": "partial stderr",
        }
    ]
    assert result["skipped"] == [{"key": "WATCH-003", "github_issue": 12}]
    assert result["errors"] == [
        "stopped after timeout promoting WATCH-002 (#11)"
    ]
    assert [command[3] for command in calls] == ["11"]

    output = format_planner_advance_apply_result(result)
    assert "Status:\n  blocked" in output
    assert "status: timeout" in output
    assert "stdout: present" in output
    assert "stderr: present" in output
    assert "No later GitHub mutation was attempted after the failed command." in output


def test_apply_planner_advance_plan_completed_noops_already_ready_downstream() -> None:
    advance_plan = {
        "status": "completed",
        "issue": 10,
        "targets": [],
        "already_ready_downstream": [
            {
                "key": "WATCH-002",
                "github_issue": 11,
                "state": "open",
                "workflow_state": "ready",
            }
        ],
        "planned_github_mutations": [],
        "reasons": [
            "one or more downstream tasks are already state:ready; "
            "no duplicate mutation is planned"
        ],
    }
    calls: list[list[str]] = []

    result = apply_planner_advance_plan(
        advance_plan,
        repo="ExatronOmega/signposter",
        run_command=lambda command: calls.append(command),
    )

    assert result == {
        "status": "completed",
        "issue": 10,
        "promoted": [],
        "commands": [],
        "already_ready": [
            {
                "key": "WATCH-002",
                "github_issue": 11,
                "state": "open",
                "workflow_state": "ready",
            }
        ],
        "errors": [],
    }
    assert calls == []

    output = format_planner_advance_apply_result(result)
    assert "Status:\n  completed" in output
    assert "no GitHub mutation was needed" in output
    assert "Already ready GitHub issues:" in output
    assert "WATCH-002 -> #11 already has state:ready" in output


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
    assert result["status_counts"] == {
        "total": 5,
        "pending": 0,
        "unseeded": 0,
        "ready": 1,
        "waiting": 4,
        "active": 0,
        "done": 0,
        "merged": 0,
        "blocked": 0,
        "completed": 0,
    }
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
    assert result["next"]["status"] == "blocked"
    assert result["next"]["reason"] == (
        "dependency-ready open task is missing GitHub label state:ready"
    )
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
    assert "Task counts:" in output
    assert (
        "  total=5 pending=0 ready=1 waiting=4 active=0 "
        "done=0 merged=0 blocked=0 completed=0"
    ) in output
    assert "  total: 5" not in output
    assert "Next task:" in output
    assert "WATCH-001 — issue: #10 — state: open" in output
    assert "Reconcile hints:" in output
    assert "4 task(s) waiting for dependencies" in output
    assert "Suggested step command:" in output
    assert "signposter run --repo ExatronOmega/signposter --issue 10 --dry-run" in output
    assert "Advance candidates:" in output
    assert "none" in output
    assert "Reconcile policy:" in output
    assert "mode: deterministic-first" in output
    assert "default LLM analysis: false" in output
    assert "escalation: not required" in output
    assert "LLM/human reconcile only for requires_reconcile impact decisions" in output
    assert "planner run/advance/impact use zero LLM tokens by default" in output
    assert "No GitHub mutation was performed." in output
    assert "No manifest mutation was performed." in output
    assert "No claim was performed." in output
    assert "No worktree was created." in output
    assert "No OpenClaw execution was performed." in output
    assert "No LLM analysis was performed." in output


def test_format_planner_run_plan_reconcile_policy_blocks_without_llm() -> None:
    status = {
        "repo": "ExatronOmega/signposter",
        "status": "active",
        "tasks": [
            {
                "key": "DONE-001",
                "title": "Done task",
                "github_issue": 10,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/10",
                "state": "done",
                "github_state": "closed",
                "workflow_state": "done",
                "labels": ["state:done"],
                "depends_on": [],
                "github_depends_on": [],
                "dependency_metadata": [],
                "mainline": None,
                "parent": None,
                "return_to": None,
                "side_task": False,
            },
            {
                "key": "OPEN-001",
                "title": "Open task missing ready",
                "github_issue": 11,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
                "state": "open",
                "github_state": "open",
                "workflow_state": None,
                "labels": [],
                "depends_on": ["DONE-001"],
                "github_depends_on": [10],
                "dependency_metadata": [],
                "mainline": None,
                "parent": None,
                "return_to": None,
                "side_task": False,
            },
        ],
    }

    output = format_planner_run_plan(
        build_planner_run_plan_from_status(
            status,
            manifest_path="/tmp/seed-manifest.json",
        )
    )

    assert "Reconcile policy:" in output
    assert "mode: deterministic-first" in output
    assert "default LLM analysis: false" in output
    assert "escalation: blocked — deterministic stop before mutation" in output
    assert "Requires:\n  LLM analysis: false" in output
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


def test_build_planner_advance_plan_from_status_skips_workflow_ready_downstream(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    status = {
        "repo": "ExatronOmega/signposter",
        "tasks": [
            {
                "key": "H049-003",
                "github_issue": 210,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/210",
                "state": "merged",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H049-004",
                "github_issue": 211,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/211",
                "state": "open",
                "github_state": "open",
                "workflow_state": "ready",
                "depends_on": ["H049-003"],
                "labels": [],
            },
        ],
    }

    result = build_planner_advance_plan_from_status(
        status,
        issue=210,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "completed"
    assert result["targets"] == []
    assert result["already_ready_downstream"] == [
        {
            "key": "H049-004",
            "github_issue": 211,
            "state": "open",
            "workflow_state": "ready",
        }
    ]
    assert result["planned_github_mutations"] == []
    assert result["planned_manifest_mutations"] == []
    assert result["requires_llm_analysis"] is False
    assert (
        "one or more downstream tasks are already state:ready; "
        "no duplicate mutation is planned"
    ) in result["reasons"]

    output = format_planner_advance_plan(result)
    assert "Status:\n  completed" in output
    assert "Already ready downstream:" in output
    assert "H049-004 — issue: #211 — no duplicate state:ready mutation planned" in output


def test_build_planner_advance_plan_from_status_blocks_incomplete_multi_dependency(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    status = {
        "repo": "ExatronOmega/signposter",
        "tasks": [
            {
                "key": "H049-003",
                "github_issue": 210,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/210",
                "state": "merged",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H049-004",
                "github_issue": 211,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/211",
                "state": "open",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H049-005",
                "github_issue": 212,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/212",
                "state": "open",
                "depends_on": ["H049-003", "H049-004"],
                "labels": [],
            },
        ],
    }

    result = build_planner_advance_plan_from_status(
        status,
        issue=210,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "blocked"
    assert result["targets"] == []
    assert result["planned_github_mutations"] == []
    assert result["planned_manifest_mutations"] == []
    assert result["requires_llm_analysis"] is False
    assert "no downstream task is currently promotable" in result["reasons"]
    assert "H049-005 waits for dependencies: H049-004" in result["reasons"]


def test_build_planner_advance_plan_reports_locked_final_task_unlock(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    status = {
        "repo": "ExatronOmega/signposter",
        "tasks": [
            {
                "key": "H051-078",
                "title": "H051-078 — Final hardening audit",
                "github_issue": 608,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/608",
                "state": "merged",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H051-079",
                "title": "H051-079 — Final roadmap readiness smoke",
                "github_issue": 609,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/609",
                "state": "open",
                "depends_on": ["H051-078"],
                "labels": [],
            },
            {
                "key": "H051-080",
                "title": "H051-080 — H051 final audit and H052 bootstrap",
                "github_issue": 610,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/610",
                "state": "open",
                "depends_on": ["H051-078", "H051-079"],
                "labels": [],
            },
        ],
    }

    result = build_planner_advance_plan_from_status(
        status,
        issue=608,
        manifest_path=str(manifest_path),
    )
    output = format_planner_advance_plan(result)

    assert result["status"] == "ready"
    assert result["targets"][0]["key"] == "H051-079"
    assert result["final_task_unlocks"] == [
        {
            "key": "H051-080",
            "title": "H051-080 — H051 final audit and H052 bootstrap",
            "github_issue": 610,
            "status": "locked",
            "contract_status": "ready",
            "current_prefix": "H051",
            "next_prefix": "H052",
            "minimum_dag_nodes": NEXT_ROADMAP_MIN_DAG_NODES,
            "waiting_on": ["H051-079"],
            "errors": [],
            "safety_note": (
                "final task unlock is dry-run only until planner advance --apply "
                "is explicit"
            ),
        }
    ]
    assert "Final-task unlock contract:" in output
    assert "H051-080 — issue: #610 — status: locked" in output
    assert "contract: ready" in output
    assert "next prefix: H052" in output
    assert "waiting on: H051-079" in output
    assert "No GitHub mutation was performed." in output


def test_build_planner_advance_plan_reports_ready_final_task_unlock(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    status = {
        "repo": "ExatronOmega/signposter",
        "tasks": [
            {
                "key": "H051-078",
                "title": "H051-078 — Final hardening audit",
                "github_issue": 608,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/608",
                "state": "merged",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H051-079",
                "title": "H051-079 — Final roadmap readiness smoke",
                "github_issue": 609,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/609",
                "state": "merged",
                "depends_on": ["H051-078"],
                "labels": [],
            },
            {
                "key": "H051-080",
                "title": "H051-080 — H051 final audit and H052 bootstrap",
                "github_issue": 610,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/610",
                "state": "open",
                "depends_on": ["H051-078", "H051-079"],
                "labels": [],
            },
        ],
    }

    result = build_planner_advance_plan_from_status(
        status,
        issue=609,
        manifest_path=str(manifest_path),
    )
    output = format_planner_advance_plan(result)

    assert result["status"] == "ready"
    assert result["targets"] == [
        {
            "key": "H051-080",
            "github_issue": 610,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/610",
            "state": "open",
            "labels_to_add": ["state:ready"],
        }
    ]
    assert result["final_task_unlocks"][0]["status"] == "ready"
    assert result["final_task_unlocks"][0]["contract_status"] == "ready"
    assert result["final_task_unlocks"][0]["current_prefix"] == "H051"
    assert result["final_task_unlocks"][0]["next_prefix"] == "H052"
    assert result["final_task_unlocks"][0]["waiting_on"] == []
    assert "H051-080 — issue: #610 — status: ready" in output
    assert "minimum DAG nodes: 80" in output
    assert "waiting on:" not in output
    assert "No manifest mutation was performed." in output


def test_build_planner_advance_plan_from_status_promotes_after_all_dependencies_done(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "seed-manifest.json"
    status = {
        "repo": "ExatronOmega/signposter",
        "tasks": [
            {
                "key": "H049-003",
                "github_issue": 210,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/210",
                "state": "merged",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H049-004",
                "github_issue": 211,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/211",
                "state": "done",
                "depends_on": [],
                "labels": [],
            },
            {
                "key": "H049-005",
                "github_issue": 212,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/212",
                "state": "open",
                "depends_on": ["H049-003", "H049-004"],
                "labels": [],
            },
        ],
    }

    result = build_planner_advance_plan_from_status(
        status,
        issue=210,
        manifest_path=str(manifest_path),
    )

    assert result["status"] == "ready"
    assert result["targets"] == [
        {
            "key": "H049-005",
            "github_issue": 212,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/212",
            "state": "open",
            "labels_to_add": ["state:ready"],
        }
    ]
    assert result["planned_github_mutations"] == [
        "gh issue edit 212 -R ExatronOmega/signposter --add-label state:ready"
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
    assert "Status detail:" in output
    assert "ready — dry-run only; use planner advance --apply to add listed labels" in output
    assert "Source task:" in output
    assert "WATCH-001 — state: closed" in output
    assert "Would promote:" in output
    assert "WATCH-002 — issue: #11 — state: open" in output
    assert "Planned GitHub mutations:" in output
    assert "Preview only; these commands were not executed." in output
    assert "gh issue edit 11 -R ExatronOmega/signposter --add-label state:ready" in output
    assert "No GitHub mutation was performed." in output
    assert "No issue was closed." in output
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
    assert result["impact"]["signals"] == ["mainline_dependent"]
    assert result["downstream_tasks"] == ["WATCH-002"]
    assert result["advanceable_downstream_tasks"] == ["WATCH-002"]
    assert result["side_task_downstream_tasks"] == []
    assert result["blocked_downstream_tasks"] == []
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


def test_build_planner_impact_from_status_surfaces_side_task_downstream() -> None:
    status = {
        "tasks": [
            {
                "key": "MAIN-001",
                "state": "merged",
                "github_issue": 10,
                "depends_on": [],
                "side_task": False,
            },
            {
                "key": "SIDE-001",
                "state": "open",
                "github_issue": 11,
                "depends_on": ["MAIN-001"],
                "side_task": True,
                "return_to": 10,
            },
        ]
    }

    result = build_planner_impact_from_status(
        status,
        issue=10,
        manifest_path="/tmp/manifest.json",
    )

    assert result["status"] == "ready"
    assert result["impact"]["score"] == 20
    assert result["impact"]["decision"] == "advance_mainline"
    assert result["impact"]["signals"] == ["side_task_dependent"]
    assert result["downstream_tasks"] == ["SIDE-001"]
    assert result["advanceable_downstream_tasks"] == ["SIDE-001"]
    assert result["side_task_downstream_tasks"] == ["SIDE-001"]
    assert result["blocked_downstream_tasks"] == []
    assert result["requires_llm_analysis"] is False
    assert "task has side-task downstream dependents" in result["reasons"]


def test_build_planner_impact_from_status_reports_already_advanced_downstream() -> None:
    status = {
        "tasks": [
            {
                "key": "MAIN-001",
                "state": "merged",
                "github_issue": 10,
                "depends_on": [],
                "side_task": False,
            },
            {
                "key": "NEXT-001",
                "state": "active",
                "github_issue": 11,
                "depends_on": ["MAIN-001"],
                "side_task": False,
            },
        ]
    }

    result = build_planner_impact_from_status(
        status,
        issue=10,
        manifest_path="/tmp/manifest.json",
    )

    assert result["status"] == "ready"
    assert result["impact"]["score"] == 10
    assert result["impact"]["decision"] == "already_advanced"
    assert result["impact"]["signals"] == [
        "mainline_dependent",
        "downstream_already_advanced",
    ]
    assert result["downstream_tasks"] == ["NEXT-001"]
    assert result["advanceable_downstream_tasks"] == []
    assert result["suggested_command"] is None
    assert "downstream tasks are already ready, active, or completed" in result["reasons"]


def test_build_planner_impact_from_status_allows_optional_llm_for_reconcile() -> None:
    downstream = [
        {
            "key": f"NEXT-00{index}",
            "state": "open",
            "github_issue": 20 + index,
            "depends_on": ["MAIN-001"],
            "side_task": False,
        }
        for index in range(1, 5)
    ]
    status = {
        "tasks": [
            {
                "key": "MAIN-001",
                "state": "merged",
                "github_issue": 10,
                "depends_on": [],
                "side_task": False,
            },
            *downstream,
        ]
    }

    result = build_planner_impact_from_status(
        status,
        issue=10,
        manifest_path="/tmp/manifest.json",
    )

    assert result["status"] == "ready"
    assert result["impact"]["score"] == 40
    assert result["impact"]["decision"] == "requires_reconcile"
    assert result["requires_llm_analysis"] is True
    assert result["llm_reconcile"] == {
        "allowed": True,
        "default": "disabled",
        "boundary": (
            "optional only for requires_reconcile impact decisions after "
            "deterministic graph evidence is shown"
        ),
        "reason": "impact is ambiguous enough for optional reconcile",
    }
    assert result["suggested_command"] is None


def test_build_planner_impact_from_status_blocks_failed_downstream() -> None:
    status = {
        "tasks": [
            {
                "key": "MAIN-001",
                "state": "merged",
                "github_issue": 10,
                "depends_on": [],
                "side_task": False,
            },
            {
                "key": "NEXT-001",
                "state": "failed",
                "github_issue": 11,
                "depends_on": ["MAIN-001"],
                "side_task": False,
            },
        ]
    }

    result = build_planner_impact_from_status(
        status,
        issue=10,
        manifest_path="/tmp/manifest.json",
    )

    assert result["status"] == "ready"
    assert result["impact"]["score"] == 60
    assert result["impact"]["level"] == "high"
    assert result["impact"]["decision"] == "block_mainline"
    assert result["impact"]["signals"] == ["mainline_dependent", "blocked_downstream"]
    assert result["downstream_tasks"] == ["NEXT-001"]
    assert result["advanceable_downstream_tasks"] == []
    assert result["blocked_downstream_tasks"] == ["NEXT-001"]
    assert result["requires_llm_analysis"] is False
    assert result["suggested_command"] is None
    assert "one or more downstream tasks are blocked" in result["reasons"]


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
    assert "signals: mainline_dependent" in output
    assert "downstream: WATCH-002" in output
    assert "advanceable downstream: WATCH-002" in output
    assert "side-task downstream: none" in output
    assert "blocked downstream: none" in output
    assert "LLM reconcile:" in output
    assert "allowed: false" in output
    assert "default: disabled" in output
    assert "deterministic decision is available" in output
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


def test_build_planner_next_from_status_blocks_dependency_ready_open_task_missing_ready_label(
    tmp_path: Path,
) -> None:
    status = {
        "tasks": [
            {
                "key": "WATCH-001",
                "title": "Done task",
                "github_issue": 10,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/10",
                "state": "done",
                "github_state": "closed",
                "workflow_state": "done",
                "labels": ["state:done"],
                "depends_on": [],
                "github_depends_on": [],
                "dependency_metadata": [],
                "mainline": None,
                "parent": None,
                "return_to": None,
                "side_task": False,
            },
            {
                "key": "WATCH-002",
                "title": "Ready but unlabeled on GitHub",
                "github_issue": 11,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
                "state": "open",
                "github_state": "open",
                "workflow_state": None,
                "labels": [],
                "depends_on": ["WATCH-001"],
                "github_depends_on": [10],
                "dependency_metadata": [],
                "mainline": None,
                "parent": None,
                "return_to": None,
                "side_task": False,
            },
        ]
    }

    result = build_planner_next_from_status(status)

    assert result["status"] == "blocked"
    assert result["reason"] == "dependency-ready open task is missing GitHub label state:ready"
    assert result["next"] is None
    assert result["blocked"][0]["key"] == "WATCH-002"
    assert result["blocked"][0]["github_issue"] == 11
    assert result["blocked"][0]["reconcile_issues"] == [10]


def test_build_planner_next_from_status_prefers_side_task_before_mainline(
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
    manifest["issues"] = [
        {
            "key": "MAIN-001",
            "title": "Mainline",
            "labels": ["state:ready"],
            "depends_on": [],
            "body_file": "main.md",
            "body_size": 1,
            "github_issue": 10,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/10",
            "mainline": "H042",
            "parent": None,
            "return_to": None,
            "side_task": False,
        },
        {
            "key": "SIDE-001",
            "title": "Side task",
            "labels": ["state:ready"],
            "depends_on": [],
            "body_file": "side.md",
            "body_size": 1,
            "github_issue": 11,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
            "mainline": "H042",
            "parent": 10,
            "return_to": 10,
            "side_task": True,
        },
    ]

    status = build_planner_status(manifest, {10: "open", 11: "open"})
    result = build_planner_next_from_status(status)

    assert result["status"] == "ready"
    assert result["reason"] == "dependency-ready side-task selected before mainline"
    assert result["next"]["key"] == "SIDE-001"
    assert result["next"]["return_to"] == 10


def test_build_planner_next_from_status_returns_to_mainline_after_side_task_completion(
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
    manifest["issues"] = [
        {
            "key": "SIDE-001",
            "title": "Side task",
            "labels": ["state:merged"],
            "depends_on": [],
            "body_file": "side.md",
            "body_size": 1,
            "github_issue": 11,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
            "mainline": "H042",
            "parent": 10,
            "return_to": 10,
            "side_task": True,
        },
        {
            "key": "MAIN-001",
            "title": "Mainline",
            "labels": ["state:ready"],
            "depends_on": [],
            "body_file": "main.md",
            "body_size": 1,
            "github_issue": 10,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/10",
            "mainline": "H042",
            "parent": None,
            "return_to": None,
            "side_task": False,
        },
    ]

    status = build_planner_status(manifest, {10: "open", 11: "merged"})
    result = build_planner_next_from_status(status)

    assert result["status"] == "ready"
    assert result["reason"] == "first dependency-ready open task selected"
    assert result["next"]["key"] == "MAIN-001"


def test_build_planner_status_marks_mainline_waiting_on_active_side_task(
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
    manifest["issues"] = [
        {
            "key": "SIDE-001",
            "title": "Side task",
            "labels": ["state:active"],
            "depends_on": [],
            "body_file": "side.md",
            "body_size": 1,
            "github_issue": 11,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
            "mainline": "H042",
            "parent": 10,
            "return_to": 10,
            "side_task": True,
        },
        {
            "key": "MAIN-001",
            "title": "Mainline",
            "labels": ["state:ready"],
            "depends_on": ["SIDE-001"],
            "body_file": "main.md",
            "body_size": 1,
            "github_issue": 10,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/10",
            "mainline": "H042",
            "parent": None,
            "return_to": None,
            "side_task": False,
        },
    ]

    status = build_planner_status(manifest, {10: "open", 11: "active"})
    side_task = status["tasks"][0]

    assert side_task["return_status"]["state"] == "open"
    assert side_task["return_status"]["ready"] is False
    assert side_task["return_status"]["mainline_waiting"] is True
    assert side_task["return_status"]["missing_dependencies"] == ["SIDE-001"]


def test_build_planner_status_marks_return_target_ready_after_side_completion(
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
    manifest["issues"] = [
        {
            "key": "SIDE-001",
            "title": "Side task",
            "labels": ["state:merged"],
            "depends_on": [],
            "body_file": "side.md",
            "body_size": 1,
            "github_issue": 11,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
            "mainline": "H042",
            "parent": 10,
            "return_to": 10,
            "side_task": True,
        },
        {
            "key": "MAIN-001",
            "title": "Mainline",
            "labels": ["state:ready"],
            "depends_on": ["SIDE-001"],
            "body_file": "main.md",
            "body_size": 1,
            "github_issue": 10,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/10",
            "mainline": "H042",
            "parent": None,
            "return_to": None,
            "side_task": False,
        },
    ]

    status = build_planner_status(manifest, {10: "open", 11: "merged"})
    side_task = status["tasks"][0]
    output = format_planner_status(status)

    assert side_task["return_status"]["ready"] is True
    assert side_task["return_status"]["mainline_waiting"] is False
    assert "return state: open · return ready: yes" in output
    assert "mainline waiting on side-task: no" in output


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


def test_format_planner_next_from_status_shows_side_task_transition() -> None:
    result = {
        "status": "ready",
        "reason": "dependency-ready side-task selected before mainline",
        "next": {
            "key": "SIDE-001",
            "github_issue": 11,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
            "state": "open",
            "depends_on": [],
            "mainline": "H042",
            "parent": 10,
            "return_to": 10,
            "side_task": True,
        },
        "waiting": [],
        "blocked": [],
    }

    output = format_planner_next_from_status(result)

    assert "side-task: yes" in output
    assert "parent: #10" in output
    assert "return-to: #10" in output
    assert "mainline: H042" in output
    assert "return state: ready" not in output
    assert "No GitHub mutation was performed." in output
    assert "No claim was performed." in output
    assert "No worktree was created." in output
    assert "No OpenClaw execution was performed." in output
    assert "No task execution was performed." in output


def test_format_planner_next_from_status_shows_return_readiness() -> None:
    result = {
        "status": "ready",
        "reason": "dependency-ready side-task selected before mainline",
        "next": {
            "key": "SIDE-001",
            "github_issue": 11,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/11",
            "state": "ready",
            "depends_on": [],
            "mainline": "H042",
            "parent": 10,
            "return_to": 10,
            "side_task": True,
            "return_status": {
                "state": "ready",
                "ready": False,
                "mainline_waiting": True,
                "missing_dependencies": ["SIDE-001"],
            },
        },
        "waiting": [],
        "blocked": [],
    }

    output = format_planner_next_from_status(result)

    assert "return state: ready" in output
    assert "return ready: no" in output
    assert "mainline waiting on side-task: yes" in output


def test_format_planner_next_from_status_shows_missing_ready_label_reconcile_hint() -> None:
    result = {
        "status": "blocked",
        "reason": "dependency-ready open task is missing GitHub label state:ready",
        "next": None,
        "waiting": [],
        "blocked": [
            {
                "key": "WATCH-002",
                "reason": "dependency-ready task is open but missing GitHub label state:ready",
                "github_issue": 11,
                "reconcile_issues": [10],
            }
        ],
    }

    output = format_planner_next_from_status(result)

    assert (
        "WATCH-002 — dependency-ready task is open but missing GitHub label state:ready "
        "(issue #11)"
    ) in output
    assert (
        "reconcile hint: run planner advance/apply from completed dependency issue(s) #10"
    ) in output


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
        timeout: int | None = None,
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
    assert "Task counts:" in captured
    assert (
        "  total=5 pending=0 ready=1 waiting=4 active=0 "
        "done=0 merged=0 blocked=0 completed=0"
    ) in captured
    assert "Next task:" in captured
    assert "WATCH-001 — issue: #10 — state: open" in captured
    assert "Reconcile hints:" in captured
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
        timeout: int | None = None,
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
    assert "Next task:\n  none" in captured
    assert (
        "dependency-ready task is open but missing GitHub label state:ready"
        in captured
    )
    assert "issue #10 / WATCH-001:" in captured
    assert "decision: advance_mainline" in captured
    assert "targets: WATCH-002" in captured
    assert (
        f"signposter planner advance --manifest {manifest_path} --issue 10 --dry-run"
        in captured
    )


def test_cli_planner_run_sync_github_reports_issue_view_timeout(
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
        timeout: int,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        if command[:4] == ["gh", "issue", "view", "10"]:
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
        return _FakeGhIssueCreateResult(
            0,
            stdout=json.dumps({"state": "OPEN", "labels": []}),
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
    assert "Status:\n  ready" in captured
    assert "GitHub issue mapping is stale" in captured
    assert "gh issue view for issue #10 timed out after 30s" in captured
    assert "No GitHub mutation was performed." in captured


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
        timeout: int | None = None,
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
        timeout: int | None = None,
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
        timeout: int | None = None,
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
        timeout: int | None = None,
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


def test_cli_planner_advance_apply_noops_when_sync_shows_target_ready(
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
        timeout: int | None = None,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        if command[:4] == ["gh", "issue", "view", "10"]:
            return _FakeGhIssueCreateResult(
                0,
                stdout=json.dumps(
                    {"state": "OPEN", "labels": [{"name": "state:merged"}]}
                ),
            )
        if command[:4] == ["gh", "issue", "view", "11"]:
            return _FakeGhIssueCreateResult(
                0,
                stdout=json.dumps(
                    {"state": "OPEN", "labels": [{"name": "state:ready"}]}
                ),
            )
        return _FakeGhIssueCreateResult(
            0,
            stdout=json.dumps({"state": "OPEN", "labels": []}),
        )

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
    assert len(calls) == 5
    assert not any(command[:3] == ["gh", "label", "list"] for command in calls)
    assert not any(command[:3] == ["gh", "issue", "edit"] for command in calls)
    assert "Status:\n  completed" in captured
    assert "no duplicate state:ready mutation planned" in captured
    assert "Signposter Planner Advance Apply" in captured
    assert "WATCH-002 -> #11 already has state:ready" in captured
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
        timeout: int | None = None,
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
        timeout: int | None = None,
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
        timeout: int | None = None,
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


def test_cli_planner_next_manifest_sync_github_blocks_dependency_missing_ready_label(
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

    issue_payloads = {
        10: {"state": "CLOSED", "labels": [{"name": "state:merged"}]},
        11: {"state": "OPEN", "labels": []},
        12: {"state": "OPEN", "labels": []},
        13: {"state": "OPEN", "labels": []},
        14: {"state": "OPEN", "labels": []},
    }

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: int | None = None,
    ) -> _FakeGhIssueCreateResult:
        calls.append(command)
        issue_number = int(command[3])
        return _FakeGhIssueCreateResult(
            0,
            stdout=json.dumps(issue_payloads[issue_number]),
        )

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
    assert exc_info.value.code == 1
    assert len(calls) == 5
    assert calls[0][:4] == ["gh", "issue", "view", "10"]
    assert "Status:\n  blocked" in captured
    assert (
        "WATCH-002 — dependency-ready task is open but missing GitHub label "
        "state:ready (issue #11)"
    ) in captured
    assert (
        "reconcile hint: run planner advance/apply from completed dependency "
        "issue(s) #10"
    ) in captured
    assert "No GitHub mutation was performed." in captured


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


def _side_task_plan_manifest() -> dict[str, object]:
    return {
        "version": "planner.seed-manifest.v0.1",
        "repo": "ExatronOmega/signposter",
        "status": "applied",
        "issues": [
            {
                "key": "H049-010",
                "title": "Parent task",
                "labels": [
                    "phase:build",
                    "risk:medium",
                    "role:worker",
                    "area:scheduler",
                ],
                "depends_on": [],
                "github_issue": 210,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/210",
                "mainline": "H049",
                "parent": None,
                "return_to": None,
                "side_task": False,
            },
            {
                "key": "H049-011",
                "title": "Return task",
                "labels": [
                    "phase:build",
                    "risk:medium",
                    "role:worker",
                    "area:scheduler",
                ],
                "depends_on": ["H049-010"],
                "github_issue": 211,
                "github_url": "https://github.com/ExatronOmega/signposter/issues/211",
                "mainline": "H049",
                "parent": None,
                "return_to": None,
                "side_task": False,
            },
        ],
    }


def test_build_planner_side_task_plan_ready() -> None:
    result = build_planner_side_task_plan(
        manifest=_side_task_plan_manifest(),
        manifest_path="/tmp/manifest.json",
        key="H049-S003",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["H049-010"],
        parent=210,
        return_to=211,
        risk="high",
    )

    assert result["status"] == "ready"
    assert result["errors"] == []
    assert result["requires_llm_analysis"] is False
    assert result["planned_task"]["side_task"] is True
    assert result["planned_task"]["parent"] == 210
    assert result["planned_task"]["return_to"] == 211
    assert result["planned_task"]["mainline"] == "H049"
    assert "risk:high" in result["planned_task"]["labels"]
    assert result["planned_github_mutations"] == []


def test_side_task_insertion_smoke_selects_side_task_then_returns_to_mainline() -> None:
    manifest = _side_task_plan_manifest()
    result = build_planner_side_task_plan(
        manifest=manifest,
        manifest_path="/tmp/manifest.json",
        key="H049-S003",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["H049-010"],
        parent=210,
        return_to=211,
        risk="high",
    )

    assert result["status"] == "ready"

    represented_manifest = json.loads(json.dumps(manifest))
    represented_manifest["issues"][0]["labels"].append("state:merged")
    represented_manifest["issues"][1]["labels"].append("state:ready")
    represented_manifest["issues"][1]["depends_on"].append("H049-S003")

    planned_task = json.loads(json.dumps(result["planned_task"]))
    planned_task.update(
        {
            "github_issue": 212,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/212",
            "labels": [*planned_task["labels"], "state:ready"],
        }
    )
    represented_manifest["issues"].append(planned_task)

    status_with_side_ready = build_planner_status(
        represented_manifest,
        {210: "merged", 211: "open", 212: "open"},
    )
    next_with_side_ready = build_planner_next_from_status(status_with_side_ready)

    assert next_with_side_ready["status"] == "ready"
    assert next_with_side_ready["next"]["key"] == "H049-S003"
    assert next_with_side_ready["next"]["side_task"] is True
    assert next_with_side_ready["next"]["return_to"] == 211
    assert (
        next_with_side_ready["next"]["return_status"]["reason"]
        == "return target is waiting for side task or dependencies"
    )

    planned_task["labels"] = [
        label for label in planned_task["labels"] if label != "state:ready"
    ]
    planned_task["labels"].append("state:merged")
    represented_manifest["issues"][-1] = planned_task

    status_after_side_merge = build_planner_status(
        represented_manifest,
        {210: "merged", 211: "open", 212: "merged"},
    )
    next_after_side_merge = build_planner_next_from_status(status_after_side_merge)

    assert next_after_side_merge["status"] == "ready"
    assert next_after_side_merge["reason"] == "first dependency-ready open task selected"
    assert next_after_side_merge["next"]["key"] == "H049-011"
    output = format_planner_status(status_after_side_merge)
    assert "side-task: yes" in output
    assert "return ready: yes" in output
    assert "mainline waiting on side-task: no" in output


def test_side_task_return_advance_waits_for_side_then_promotes_mainline() -> None:
    manifest = _side_task_plan_manifest()
    result = build_planner_side_task_plan(
        manifest=manifest,
        manifest_path="/tmp/manifest.json",
        key="H049-S003",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["H049-010"],
        parent=210,
        return_to=211,
        risk="high",
    )

    assert result["status"] == "ready"

    represented_manifest = json.loads(json.dumps(manifest))
    represented_manifest["issues"][0]["labels"].append("state:merged")
    represented_manifest["issues"][1]["depends_on"].append("H049-S003")

    side_task = json.loads(json.dumps(result["planned_task"]))
    side_task.update(
        {
            "github_issue": 212,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/212",
            "labels": [*side_task["labels"], "state:ready"],
        }
    )
    represented_manifest["issues"].append(side_task)

    status_with_side_ready = build_planner_status(
        represented_manifest,
        {210: "merged", 211: "open", 212: "open"},
    )
    parent_advance = build_planner_advance_plan_from_status(
        status_with_side_ready,
        issue=210,
        manifest_path="/tmp/manifest.json",
    )

    assert parent_advance["status"] == "blocked"
    assert parent_advance["targets"] == []
    assert "one or more downstream tasks are waiting for dependencies" in parent_advance[
        "reasons"
    ]
    assert "H049-011 waits for dependencies: H049-S003" in parent_advance["reasons"]

    side_task["labels"] = [
        label for label in side_task["labels"] if label != "state:ready"
    ]
    side_task["labels"].append("state:merged")
    represented_manifest["issues"][-1] = side_task

    status_after_side_merge = build_planner_status(
        represented_manifest,
        {210: "merged", 211: "open", 212: "merged"},
    )
    side_advance = build_planner_advance_plan_from_status(
        status_after_side_merge,
        issue=212,
        manifest_path="/tmp/manifest.json",
    )

    assert side_advance["status"] == "ready"
    assert side_advance["targets"] == [
        {
            "key": "H049-011",
            "github_issue": 211,
            "github_url": "https://github.com/ExatronOmega/signposter/issues/211",
            "state": "open",
            "labels_to_add": ["state:ready"],
        }
    ]
    assert side_advance["planned_github_mutations"] == [
        "gh issue edit 211 -R ExatronOmega/signposter --add-label state:ready"
    ]

    represented_manifest["issues"][1]["labels"].append("state:ready")
    status_after_return_promotion = build_planner_status(
        represented_manifest,
        {210: "merged", 211: "open", 212: "merged"},
    )
    next_after_return_promotion = build_planner_next_from_status(
        status_after_return_promotion
    )
    completed_side_task = status_after_return_promotion["tasks"][-1]

    assert next_after_return_promotion["status"] == "ready"
    assert next_after_return_promotion["next"]["key"] == "H049-011"
    assert completed_side_task["return_status"]["ready"] is True
    assert completed_side_task["return_status"]["mainline_waiting"] is False


def test_build_planner_side_task_plan_does_not_mutate_input_manifest() -> None:
    manifest = _side_task_plan_manifest()
    before = json.loads(json.dumps(manifest))

    build_planner_side_task_plan(
        manifest=manifest,
        manifest_path="/tmp/manifest.json",
        key="H049-S003",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["H049-010"],
        parent=210,
        return_to=211,
    )

    assert manifest == before


def test_build_planner_side_task_plan_blocks_without_return_to() -> None:
    result = build_planner_side_task_plan(
        manifest=_side_task_plan_manifest(),
        manifest_path="/tmp/manifest.json",
        key="H049-S003",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["H049-010"],
        parent=210,
        return_to=None,
    )

    assert result["status"] == "blocked"
    assert "return_to issue is required" in result["errors"]
    assert result["planned_manifest_mutations"] == []


def test_build_planner_side_task_plan_blocks_missing_label_fields() -> None:
    result = build_planner_side_task_plan(
        manifest=_side_task_plan_manifest(),
        manifest_path="/tmp/manifest.json",
        key="H049-S003",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["H049-010"],
        parent=210,
        return_to=211,
        phase="",
        gate=" ",
    )

    assert result["status"] == "blocked"
    assert "phase is required" in result["errors"]
    assert "gate is required" in result["errors"]


def test_build_planner_side_task_plan_blocks_duplicate_key_and_unknown_dependency() -> None:
    result = build_planner_side_task_plan(
        manifest=_side_task_plan_manifest(),
        manifest_path="/tmp/manifest.json",
        key="H049-010",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["MISSING-001"],
        parent=210,
        return_to=211,
    )

    assert result["status"] == "blocked"
    assert "side-task key already exists in manifest: H049-010" in result["errors"]
    assert "unknown dependency: MISSING-001" in result["errors"]


def test_format_planner_side_task_plan_is_compact_and_safe() -> None:
    result = build_planner_side_task_plan(
        manifest=_side_task_plan_manifest(),
        manifest_path="/tmp/manifest.json",
        key="H049-S003",
        title="Fix discovered scheduler edge",
        reason="planner advance exposed a dependency edge",
        depends_on=["H049-010"],
        parent=210,
        return_to=211,
    )

    output = format_planner_side_task_plan(result)

    assert "Signposter Planner Side-Task Plan" in output
    assert "Status:\n  ready" in output
    assert "side-task: yes" in output
    assert "parent: #210" in output
    assert "return-to: #211" in output
    assert "No GitHub mutation was performed." in output
    assert "No manifest mutation was performed." in output
    assert len(output.splitlines()) < 60


def test_cli_planner_side_task_plan_reports_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(_side_task_plan_manifest()),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "planner",
            "side-task-plan",
            "--manifest",
            str(manifest_path),
            "--key",
            "H049-S003",
            "--title",
            "Fix discovered scheduler edge",
            "--reason",
            "planner advance exposed a dependency edge",
            "--depends-on",
            "H049-010",
            "--parent",
            "210",
            "--return-to",
            "211",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Signposter Planner Side-Task Plan" in captured
    assert "No GitHub issue was created." in captured
