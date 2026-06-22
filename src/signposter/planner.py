"""Local planner draft and validation surfaces for Signposter."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from signposter.codex_cli_backend import RunCommand
from signposter.comments import contains_auto_close_keyword

PLAN_VERSION = "planner.v0.1"
NEXT_ROADMAP_BOOTSTRAP_VERSION = "planner.next-roadmap-bootstrap.v0.1"

WORKER_ISSUE_PREFERRED_MIN_LINES = 35
WORKER_ISSUE_PREFERRED_MAX_LINES = 120
WORKER_ISSUE_HARD_MAX_LINES = 165
WORKER_ISSUE_HARD_MAX_CHARS = 12000
NEXT_ROADMAP_MIN_DAG_NODES = 80

NEXT_ROADMAP_BOOTSTRAP_REQUIRED_STEPS = [
    "final current-roadmap completion audit",
    "verify planner and lifecycle status have no pending current-roadmap task",
    "verify local validation and remote CI evidence",
    "choose next roadmap direction from repository evidence",
    "create a dependency-aware DAG with at least 80 small tasks",
    "validate required task fields, dependencies, gates, risks, and acceptance criteria",
    "run seed/sync dry-run before any GitHub mutation",
    "seed/sync through Signposter only when the guarded plan is ready and --apply is explicit",
    "verify root tasks are ready and dependent tasks are waiting",
    "record issue mappings in the manifest when supported",
    "identify the first eligible next task",
    "return the operator to the standard Signposter execution loop",
]

NEXT_ROADMAP_BOOTSTRAP_SAFETY_RULES = [
    "GitHub mutation only through guarded Signposter --apply paths",
    "no worker/reviewer execution from the bootstrap contract itself",
    "no manual issue closure; integration owns issue closure",
    "do not seed duplicate tasks for an existing roadmap prefix",
    "block with evidence when manifest validation or seed dry-run is not ready",
]

PLANNER_SCOPE_CLASSES = ("narrow", "normal", "wide")
PLANNER_VALIDATION_PROFILES = (
    "docs-only",
    "targeted",
    "targeted-plus-lint",
    "full-suite",
    "manual-smoke-required",
    "blocked-until-evidence",
)
PLANNER_DRY_RUN_POLICIES = ("required", "recommended", "optional")

STOP_CONDITIONS = [
    "ruff check fails",
    "targeted pytest fails",
    "full pytest fails",
    "CI fails",
    "GitHub mutation is requested without --apply",
    "OpenClaw execution is requested without --execute",
    "PR body contains auto-close keywords",
    "merge plan would close an issue",
]


def build_planner_draft(goal: str) -> dict[str, Any]:
    """Build a deterministic local-only planner draft."""
    goal = goal.strip()
    if not goal:
        raise ValueError("planner goal must not be empty")

    return {
        "version": PLAN_VERSION,
        "goal": goal,
        "mode": "supervised",
        "status": "draft",
        "mutation_policy": "local draft only; no GitHub mutation; no OpenClaw execution",
        "required_capabilities": [
            "read-only lifecycle inspection",
            "issue and PR state summarization",
            "gate and CI status visibility",
            "local worktree and branch visibility",
            "compact terminal output",
        ],
        "issues": [
            _issue("WATCH-001", "Define lifecycle watch CLI contract", "cli", []),
            _issue(
                "WATCH-002",
                "Add read-only lifecycle watch data collector",
                "cli",
                ["WATCH-001"],
            ),
            _issue(
                "WATCH-003",
                "Add simple terminal refresh renderer",
                "cli",
                ["WATCH-002"],
            ),
            _issue("WATCH-004", "Add lifecycle watch tests", "tests", ["WATCH-003"]),
            _issue(
                "WATCH-005",
                "Document lifecycle watch operator usage",
                "docs",
                ["WATCH-004"],
            ),
        ],
    }


def write_planner_draft(goal: str, output_path: Path) -> dict[str, Any]:
    """Write a planner draft JSON file and return the plan."""
    plan = build_planner_draft(goal)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return plan


def load_planner_plan(plan_path: Path) -> dict[str, Any]:
    """Load a planner JSON file."""
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("planner file must contain a JSON object")
    return data


def validate_planner_plan(plan: dict[str, Any]) -> list[str]:
    """Return validation errors for a planner plan."""
    errors: list[str] = []

    _require(plan, "version", str, errors)
    _require(plan, "goal", str, errors)
    _require(plan, "issues", list, errors)

    if plan.get("version") != PLAN_VERSION:
        errors.append(f"version must be {PLAN_VERSION}")

    if not str(plan.get("goal", "")).strip():
        errors.append("goal must not be empty")

    issues = plan.get("issues")
    if not isinstance(issues, list) or not issues:
        errors.append("issues must be a non-empty list")
        return errors

    keys: set[str] = set()
    for index, issue in enumerate(issues, start=1):
        if not isinstance(issue, dict):
            errors.append(f"issue #{index} must be an object")
            continue

        key = str(issue.get("key", f"#{index}"))
        if key in keys:
            errors.append(f"{key}: duplicate issue key")
        keys.add(key)

        for field in [
            "key",
            "title",
            "body",
            "phase",
            "risk",
            "role",
            "area",
            "depends_on",
            "acceptance",
            "stop_conditions",
            "allowed_mutations",
        ]:
            if field not in issue:
                errors.append(f"{key}: missing {field}")

        _list_required(issue, "depends_on", key, errors)
        _list_required(issue, "acceptance", key, errors)
        _list_required(issue, "stop_conditions", key, errors)

        if issue.get("allowed_mutations") != []:
            errors.append(f"{key}: allowed_mutations must be empty for local draft plans")

        status = str(issue.get("status", "pending")).strip().lower()
        if status not in ALLOWED_TASK_STATUSES:
            allowed = ", ".join(sorted(ALLOWED_TASK_STATUSES))
            errors.append(f"{key}: status must be one of {allowed}")

        searchable = " ".join(
            str(issue.get(field, "")) for field in ["title", "body", "acceptance"]
        )
        if contains_auto_close_keyword(searchable):
            errors.append(f"{key}: contains auto-close keyword")

    for issue in issues:
        if isinstance(issue, dict):
            key = str(issue.get("key", "unknown"))
            for dependency in issue.get("depends_on", []):
                if dependency not in keys:
                    errors.append(f"{key}: unknown dependency {dependency}")

    return errors


def build_next_roadmap_bootstrap_contract(
    *,
    current_prefix: str,
    next_prefix: str,
    minimum_dag_nodes: int = NEXT_ROADMAP_MIN_DAG_NODES,
) -> dict[str, Any]:
    """Return the deterministic contract for final roadmap bootstrap tasks."""
    return {
        "version": NEXT_ROADMAP_BOOTSTRAP_VERSION,
        "current_prefix": current_prefix.strip(),
        "next_prefix": next_prefix.strip(),
        "minimum_dag_nodes": minimum_dag_nodes,
        "required_steps": list(NEXT_ROADMAP_BOOTSTRAP_REQUIRED_STEPS),
        "safety_rules": list(NEXT_ROADMAP_BOOTSTRAP_SAFETY_RULES),
        "required_task_fields": [
            "unique ID",
            "title",
            "purpose",
            "concrete scope",
            "non-goals",
            "dependencies",
            "route/role",
            "expected model tier",
            "gate",
            "risk level",
            "acceptance criteria",
            "validation commands",
            "done criteria",
        ],
        "done_criteria": [
            "current roadmap audit is complete",
            "next roadmap manifest or artifact is validated",
            "seed/sync dry-run is ready before apply",
            "root tasks and waiting tasks are consistent with dependencies",
            "first eligible next task is identified",
            "standard Signposter loop can continue from the next task",
        ],
    }


def validate_next_roadmap_bootstrap_contract(contract: dict[str, Any]) -> list[str]:
    """Return validation errors for a final-task next-roadmap bootstrap contract."""
    errors: list[str] = []

    _require(contract, "version", str, errors)
    _require(contract, "current_prefix", str, errors)
    _require(contract, "next_prefix", str, errors)
    _require(contract, "minimum_dag_nodes", int, errors)
    _require(contract, "required_steps", list, errors)
    _require(contract, "safety_rules", list, errors)
    _require(contract, "required_task_fields", list, errors)
    _require(contract, "done_criteria", list, errors)

    if contract.get("version") != NEXT_ROADMAP_BOOTSTRAP_VERSION:
        errors.append(f"version must be {NEXT_ROADMAP_BOOTSTRAP_VERSION}")

    if not str(contract.get("current_prefix", "")).strip():
        errors.append("current_prefix must not be empty")
    if not str(contract.get("next_prefix", "")).strip():
        errors.append("next_prefix must not be empty")
    if contract.get("current_prefix") == contract.get("next_prefix"):
        errors.append("next_prefix must differ from current_prefix")

    minimum = contract.get("minimum_dag_nodes")
    if isinstance(minimum, int) and minimum < NEXT_ROADMAP_MIN_DAG_NODES:
        errors.append(
            "minimum_dag_nodes must be at least "
            f"{NEXT_ROADMAP_MIN_DAG_NODES}"
        )

    _list_contains_all(
        contract,
        "required_steps",
        NEXT_ROADMAP_BOOTSTRAP_REQUIRED_STEPS,
        errors,
    )
    _list_contains_all(
        contract,
        "safety_rules",
        NEXT_ROADMAP_BOOTSTRAP_SAFETY_RULES,
        errors,
    )
    _list_contains_all(
        contract,
        "done_criteria",
        [
            "next roadmap manifest or artifact is validated",
            "first eligible next task is identified",
            "standard Signposter loop can continue from the next task",
        ],
        errors,
    )

    return errors


def format_next_roadmap_bootstrap_contract(contract: dict[str, Any]) -> str:
    """Format the next-roadmap bootstrap contract for operator review."""
    errors = validate_next_roadmap_bootstrap_contract(contract)
    status = "blocked" if errors else "ready"

    lines = [
        "Signposter Next Roadmap Bootstrap Contract",
        "",
        "Status:",
        status,
        "",
        "Roadmap transition:",
        f"  current: {contract.get('current_prefix', '')}",
        f"  next: {contract.get('next_prefix', '')}",
        f"  minimum DAG nodes: {contract.get('minimum_dag_nodes', '')}",
    ]

    if errors:
        lines.extend(["", "Validation errors:"])
        lines.extend(f"  - {error}" for error in errors)
    else:
        lines.extend(
            [
                "",
                "Required steps:",
                _markdown_bullets(contract["required_steps"]),
                "",
                "Safety:",
                _markdown_bullets(contract["safety_rules"]),
                "",
                "Done criteria:",
                _markdown_bullets(contract["done_criteria"]),
            ]
        )

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No manifest mutation was performed.",
            "  No worker or reviewer execution was performed.",
            "  No issue was closed.",
        ]
    )
    return "\n".join(lines)




DONE_STATUSES = {"done"}
BLOCKED_STATUSES = {"blocked", "failed"}
ALLOWED_TASK_STATUSES = {"pending", "active", "done", "blocked", "failed"}



def mark_planner_task(
    plan_path: Path,
    task_key: str,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a task status in a local planner JSON file."""
    status = status.strip().lower()
    if status not in ALLOWED_TASK_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_TASK_STATUSES))
        return {
            "status": "blocked",
            "errors": [f"status must be one of {allowed}"],
            "task": task_key,
            "task_status": status,
        }

    plan = load_planner_plan(plan_path)
    errors = validate_planner_plan(plan)
    if errors:
        return {
            "status": "blocked",
            "errors": errors,
            "task": task_key,
            "task_status": status,
        }

    target = None
    for issue in plan["issues"]:
        if issue["key"] == task_key:
            target = issue
            break

    if target is None:
        return {
            "status": "blocked",
            "errors": [f"unknown task {task_key}"],
            "task": task_key,
            "task_status": status,
        }

    target["status"] = status
    if reason:
        target["status_reason"] = reason
    else:
        target.pop("status_reason", None)
    target["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")

    post_errors = validate_planner_plan(plan)
    if post_errors:
        return {
            "status": "blocked",
            "errors": post_errors,
            "task": task_key,
            "task_status": status,
        }

    plan_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "updated",
        "errors": [],
        "task": task_key,
        "task_status": status,
        "reason": reason or "",
    }


def format_planner_mark_result(plan_path: Path, result: dict[str, Any]) -> str:
    """Format local planner mark result."""
    lines = [
        "Signposter Planner Mark",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Task:",
        f"  {result['task']}",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Task status:",
        f"  {result['task_status']}",
    ]

    if result.get("reason"):
        lines.extend(["", "Reason:", f"  {result['reason']}"])

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Safety:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)



def format_gh_issue_create_command(
    *,
    repo: str,
    title: str,
    body_file: Path,
    labels: list[str],
) -> str:
    """Format a future gh issue create command without executing it."""
    args = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body-file",
        str(body_file),
    ]
    for label in labels:
        args.extend(["--label", label])

    quoted = [shlex.quote(arg) for arg in args]
    return " \\\n  ".join(quoted)


def build_planner_next(plan: dict[str, Any]) -> dict[str, Any]:
    """Choose the next dependency-ready issue from a planner plan."""
    errors = validate_planner_plan(plan)
    if errors:
        return {
            "status": "blocked",
            "reason": "plan validation failed",
            "errors": errors,
            "next": None,
        }

    issues = plan["issues"]
    completed = {
        issue["key"]
        for issue in issues
        if _issue_status(issue) in DONE_STATUSES
    }
    remaining = [
        issue
        for issue in issues
        if _issue_status(issue) not in DONE_STATUSES
    ]

    if not remaining:
        return {
            "status": "completed",
            "reason": "all issues are completed",
            "errors": [],
            "next": None,
        }

    blocked = [
        issue["key"]
        for issue in remaining
        if _issue_status(issue) in BLOCKED_STATUSES
    ]

    for issue in remaining:
        status = _issue_status(issue)
        if status in BLOCKED_STATUSES:
            continue

        missing_dependencies = [
            dependency
            for dependency in issue["depends_on"]
            if dependency not in completed
        ]
        if missing_dependencies:
            continue

        return {
            "status": "ready",
            "reason": "first dependency-ready issue selected",
            "errors": [],
            "next": {
                "key": issue["key"],
                "title": issue["title"],
                "status": status,
                "depends_on": issue["depends_on"],
            },
        }

    return {
        "status": "waiting",
        "reason": "no dependency-ready issue is available",
        "errors": [f"blocked issue: {key}" for key in blocked],
        "next": None,
    }


def format_planner_next(plan_path: Path, next_plan: dict[str, Any]) -> str:
    """Format planner next result."""
    lines = [
        "Signposter Planner Next",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Status:",
        f"  {next_plan['status']}",
        "",
        "Reason:",
        f"  {next_plan['reason']}",
    ]

    if next_plan["next"]:
        issue = next_plan["next"]
        deps = ", ".join(issue["depends_on"]) if issue["depends_on"] else "none"
        lines.extend(
            [
                "",
                "Next issue:",
                f"  {issue['key']} — {issue['title']}",
                f"  status: {issue['status']}",
                f"  depends on: {deps}",
            ]
        )

    if next_plan["errors"]:
        lines.extend(["", "Notes:"])
        lines.extend(f"  - {error}" for error in next_plan["errors"])

    lines.extend(
        [
            "",
            "Safety:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)



def evaluate_worker_issue_body_size(body: str) -> dict[str, Any]:
    """Evaluate whether a worker issue body fits the bounded task policy."""
    line_count = len(body.splitlines())
    char_count = len(body)
    warnings: list[str] = []
    errors: list[str] = []

    if line_count > WORKER_ISSUE_HARD_MAX_LINES:
        errors.append(
            f"issue body has {line_count} lines; hard max is "
            f"{WORKER_ISSUE_HARD_MAX_LINES}; split into A/B/C"
        )
    elif line_count > WORKER_ISSUE_PREFERRED_MAX_LINES:
        warnings.append(
            f"issue body has {line_count} lines; preferred max is "
            f"{WORKER_ISSUE_PREFERRED_MAX_LINES}"
        )
    elif line_count < WORKER_ISSUE_PREFERRED_MIN_LINES:
        warnings.append(
            f"issue body has {line_count} lines; preferred min is "
            f"{WORKER_ISSUE_PREFERRED_MIN_LINES}"
        )

    if char_count > WORKER_ISSUE_HARD_MAX_CHARS:
        errors.append(
            f"issue body has {char_count} chars; hard max is "
            f"{WORKER_ISSUE_HARD_MAX_CHARS}; split into A/B/C"
        )

    if errors:
        status = "blocked"
    elif warnings:
        status = "warning"
    else:
        status = "pass"

    return {
        "status": status,
        "line_count": line_count,
        "char_count": char_count,
        "warnings": warnings,
        "errors": errors,
    }


def classify_planner_task(issue: dict[str, Any]) -> dict[str, Any]:
    """Classify planner task scope, validation profile, and dry-run policy."""
    title = str(issue.get("title", ""))
    body = str(issue.get("body", ""))
    labels = issue.get("labels", [])
    text = f"{title}\n{body}".lower()
    area = str(issue.get("area") or _planner_label_value(labels, "area") or "").lower()
    risk = str(issue.get("risk") or _planner_label_value(labels, "risk") or "medium")
    risk = risk.lower()

    docs_like = area == "docs" or any(
        token in text
        for token in (
            "docs",
            "documentation",
            "readme",
            "runbook",
            "troubleshooting",
            "operator-visible",
            "guidance",
        )
    )
    planner_like = area in {"planner", "scheduler"} or "planner" in text
    smoke_like = "smoke" in text
    shared_safety_like = area in {
        "gate",
        "lifecycle",
        "merge",
        "integration",
        "cleanup",
        "security",
    } or any(
        token in text
        for token in (
            "gate",
            "lifecycle",
            "merge",
            "integration",
            "cleanup",
            "security",
            "mutation boundary",
        )
    )
    wide_like = any(
        token in text
        for token in (
            "architecture",
            "global",
            "wide",
            "final audit",
            "bootstrap",
            "roadmap",
            "regenerate",
            "intelligent planning",
            "multiple independent modules",
        )
    )

    if wide_like and not smoke_like:
        scope_class = "wide"
    elif docs_like or smoke_like or any(
        token in text for token in ("wording", "compactness", "one small", "one file")
    ):
        scope_class = "narrow"
    elif planner_like or shared_safety_like or area in {"github", "core"}:
        scope_class = "normal"
    else:
        scope_class = "normal"

    if docs_like and not planner_like and not shared_safety_like:
        validation_profile = "docs-only"
        required_evidence = [
            "changed docs files only",
            "no code behavior changes",
            "git diff --check -- docs/",
        ]
    elif smoke_like:
        validation_profile = "manual-smoke-required"
        required_evidence = [
            "manual smoke command output",
            "bounded summary artifact",
        ]
    elif shared_safety_like or scope_class == "wide" or risk == "high":
        validation_profile = "full-suite"
        required_evidence = [
            "targeted tests for changed surface",
            "ruff check .",
            "python -m pytest tests/ -q",
        ]
    elif planner_like:
        validation_profile = "targeted-plus-lint"
        required_evidence = [
            "python -m pytest tests/test_planner.py -q",
            "ruff check changed planner files/tests",
        ]
    else:
        validation_profile = "targeted"
        required_evidence = ["targeted tests for changed surface"]

    if risk in {"medium", "high"} or scope_class == "wide":
        dry_run_policy = "required"
    elif validation_profile == "docs-only":
        dry_run_policy = "optional"
    else:
        dry_run_policy = "recommended"

    return {
        "key": issue.get("key"),
        "title": title,
        "area": area or "unknown",
        "risk": risk,
        "scope_class": scope_class,
        "validation_profile": validation_profile,
        "dry_run_policy": dry_run_policy,
        "required_evidence": required_evidence,
        "reason": _planner_classification_reason(
            scope_class=scope_class,
            validation_profile=validation_profile,
            dry_run_policy=dry_run_policy,
        ),
    }


def build_planner_regeneration_plan(
    *,
    manifest: dict[str, Any],
    manifest_path: str,
    plan: dict[str, Any] | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Build a local-only intelligent planning/regeneration proposal."""
    issues = list(manifest.get("issues", []))
    if plan and isinstance(plan.get("issues"), list):
        plan_by_key = {item.get("key"): item for item in plan["issues"]}
        enriched_issues = []
        for issue in issues:
            plan_issue = plan_by_key.get(issue.get("key"), {})
            merged = dict(plan_issue)
            merged.update(issue)
            enriched_issues.append(merged)
        issues = enriched_issues

    classifications = [classify_planner_task(issue) for issue in issues]
    combination_candidates = _planner_combination_candidates(classifications)
    split_candidates = [
        item for item in classifications if item["scope_class"] == "wide"
    ]
    validation_updates = [
        item
        for item in classifications
        if item["validation_profile"] in {"docs-only", "targeted-plus-lint"}
    ]
    preserved = [item for item in classifications if item.get("key") == "H051-017"]

    proposed_updates = [
        {
            "type": "combine",
            "keys": candidate["keys"],
            "reason": candidate["reason"],
        }
        for candidate in combination_candidates
    ]
    proposed_updates.extend(
        {
            "type": "split-or-escalate",
            "keys": [item["key"]],
            "reason": (
                f"{item['key']} is wide; keep as audit/bootstrap or split "
                "if scope grows"
            ),
        }
        for item in split_candidates
    )
    proposed_updates.extend(
        {
            "type": "validation-profile",
            "keys": [item["key"]],
            "reason": (
                f"{item['key']} should use {item['validation_profile']} "
                f"validation and dry-run {item['dry_run_policy']}"
            ),
        }
        for item in validation_updates[:8]
    )

    return {
        "status": "ready" if issues else "blocked",
        "repo": repo or manifest.get("repo", ""),
        "manifest_path": manifest_path,
        "goal": _planner_regeneration_goal(manifest, plan),
        "tasks_inspected": len(issues),
        "tasks_kept": len(issues),
        "tasks_combined": len(combination_candidates),
        "tasks_expanded": 0,
        "tasks_split": len(split_candidates),
        "tasks_needing_github_issue_updates": len(proposed_updates),
        "classifications": classifications,
        "combination_candidates": combination_candidates,
        "split_candidates": split_candidates,
        "proposed_issue_updates": proposed_updates,
        "preserved_tasks": preserved,
        "policy": {
            "scope_classifier": "enabled",
            "validation_profiles": "enabled",
            "dry_run_optimization": "enabled",
            "llm_analysis": False,
        },
        "notes": [
            "Local regeneration proposal only.",
            "No GitHub mutation was performed.",
            "No backend execution was performed.",
            "Use a separate guarded apply path before editing GitHub issues.",
        ],
    }


def format_planner_regeneration_plan(result: dict[str, Any]) -> str:
    """Format an intelligent planning regeneration proposal."""
    lines = [
        "Signposter Planner Regeneration",
        "",
        "Status:",
        f"  {result.get('status', 'unknown')}",
        "",
        "Repo:",
        f"  {result.get('repo') or 'unknown'}",
        "",
        "Manifest:",
        f"  {result.get('manifest_path')}",
        "",
        "Goal:",
        f"  {result.get('goal')}",
        "",
        "Plan:",
        f"  tasks inspected: {result.get('tasks_inspected', 0)}",
        f"  tasks kept: {result.get('tasks_kept', 0)}",
        f"  tasks combined: {result.get('tasks_combined', 0)}",
        f"  tasks expanded: {result.get('tasks_expanded', 0)}",
        f"  tasks split: {result.get('tasks_split', 0)}",
        (
            "  tasks needing GitHub issue updates: "
            f"{result.get('tasks_needing_github_issue_updates', 0)}"
        ),
        "",
        "Policy:",
        f"  scope classifier: {result['policy']['scope_classifier']}",
        f"  validation profiles: {result['policy']['validation_profiles']}",
        f"  dry-run optimization: {result['policy']['dry_run_optimization']}",
        f"  LLM analysis: {str(result['policy']['llm_analysis']).lower()}",
    ]

    if result.get("preserved_tasks"):
        lines.extend(["", "Required preserved tasks:"])
        for item in result["preserved_tasks"]:
            lines.append(
                f"  {item['key']} — scope: {item['scope_class']} — "
                f"validation: {item['validation_profile']}"
            )

    lines.extend(["", "Task classification sample:"])
    for item in result.get("classifications", [])[:10]:
        lines.append(
            f"  {item['key']} — scope: {item['scope_class']} — "
            f"validation: {item['validation_profile']} — "
            f"dry-run: {item['dry_run_policy']}"
        )

    updates = result.get("proposed_issue_updates", [])
    lines.extend(["", "Proposed issue updates:"])
    if updates:
        for update in updates[:12]:
            keys = " + ".join(str(key) for key in update["keys"])
            lines.append(f"  - {keys} -> {update['reason']}")
        omitted = len(updates) - 12
        if omitted > 0:
            lines.append(f"  - ... {omitted} additional bounded proposals omitted")
    else:
        lines.append("  none")

    lines.extend(
        [
            "",
            "Dry-run policy:",
            "  required: medium/high risk or wide workflow changes",
            "  recommended: low-risk normal code changes",
            "  optional: low-risk docs-only changes with no behavior change",
            "",
            "Notes:",
        ]
    )
    lines.extend(f"  {note}" for note in result.get("notes", []))
    return "\n".join(lines)


def _planner_label_value(labels: Any, prefix: str) -> str | None:
    if not isinstance(labels, list):
        return None
    label_prefix = f"{prefix}:"
    for label in labels:
        if isinstance(label, dict):
            name = str(label.get("name") or "")
        else:
            name = str(label)
        if name.startswith(label_prefix):
            return name.split(":", 1)[1].strip()
    return None


def _planner_classification_reason(
    *,
    scope_class: str,
    validation_profile: str,
    dry_run_policy: str,
) -> str:
    return (
        f"{scope_class} scope with {validation_profile} validation; "
        f"dry-run {dry_run_policy}"
    )


def _planner_regeneration_goal(
    manifest: dict[str, Any],
    plan: dict[str, Any] | None,
) -> str:
    if plan and plan.get("goal"):
        return str(plan["goal"])
    if manifest.get("goal"):
        return str(manifest["goal"])
    return "planner regeneration proposal for current manifest"


def _planner_combination_candidates(
    classifications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for left, right in zip(classifications, classifications[1:], strict=False):
        if "H051-017" in {left.get("key"), right.get("key")}:
            continue
        if left["scope_class"] == "wide" or right["scope_class"] == "wide":
            continue
        if left["validation_profile"] != right["validation_profile"]:
            continue
        if left["area"] != right["area"]:
            continue
        if _planner_title_family(left["title"]) != _planner_title_family(right["title"]):
            continue
        candidates.append(
            {
                "keys": [left["key"], right["key"]],
                "reason": (
                    "combine related "
                    f"{left['area']} tasks with {left['validation_profile']} validation"
                ),
            }
        )
    return candidates


def _planner_title_family(title: str) -> str:
    title = title.lower()
    for token in (
        "planner",
        "github",
        "runtime",
        "reviewer",
        "integration",
        "cleanup",
        "subagent",
        "handoff",
        "merge",
    ):
        if token in title:
            return token
    words = re.findall(r"[a-z0-9]+", title)
    if len(words) > 1 and words[0].startswith("h0"):
        return str(words[1])
    return str(words[0]) if words else ""



def format_planner_roadmap(plan: dict[str, Any]) -> str:
    """Format a generic planner-level roadmap template.

    This is not a worker issue body and does not render concrete issue DAG
    entries. Concrete worker tasks belong to seed/next/issue-body surfaces.
    """
    errors = validate_planner_plan(plan)
    if errors:
        return "\n".join(
            [
                "Roadmap Template",
                "",
                "Status:",
                "blocked",
                "",
                "Validation errors:",
                _markdown_bullets(errors),
            ]
        )

    return "\n".join(
        [
            "Roadmap Template",
            "",
            "User goal:",
            f"{plan['goal']}",
            "",
            "Purpose:",
            "Convert a broad user goal into a bounded, reviewable, dependency-aware plan.",
            "",
            "Roadmap role:",
            "* Describe strategy, scope, sequencing, risk, and validation.",
            "* Decide what must become worker-ready tasks.",
            "* Keep architecture-level decisions separate from execution details.",
            "* Prevent broad work from becoming one oversized worker issue.",
            "",
            "Outcome:",
            "* Clear implementation direction.",
            "* Explicit non-goals and boundaries.",
            "* A later issue DAG made of small worker-ready tasks.",
            "* Validation and stop conditions before any execution.",
            "",
            "Non-goals:",
            "* Do not execute worker tasks from the roadmap document.",
            "* Do not mutate GitHub from the roadmap document.",
            "* Do not run OpenClaw from the roadmap document.",
            "* Do not include full worker issue bodies inside the roadmap.",
            "* Do not hard-code product-specific task names in the roadmap template.",
            "",
            "Planning sections:",
            "* Intent and desired end state.",
            "* Scope and non-goals.",
            "* Assumptions and constraints.",
            "* Required capabilities.",
            "* Proposed milestones.",
            "* Dependency strategy.",
            "* Risk model.",
            "* Validation strategy.",
            "* Stop conditions.",
            "* Follow-up and branching policy.",
            "* Done definition.",
            "",
            "Milestone model:",
            "* M1 — clarify intent, outcome, and boundaries.",
            "* M2 — identify required capabilities.",
            "* M3 — split work into small worker-ready tasks.",
            "* M4 — define dependencies and ordering.",
            "* M5 — validate task size, safety, and acceptance criteria.",
            "* M6 — execute one task at a time through guarded workflow.",
            "",
            "Worker task sizing policy:",
            f"* Preferred range: {WORKER_ISSUE_PREFERRED_MIN_LINES}–"
            f"{WORKER_ISSUE_PREFERRED_MAX_LINES} lines.",
            f"* Hard max: {WORKER_ISSUE_HARD_MAX_LINES} lines.",
            f"* Hard max chars: {WORKER_ISSUE_HARD_MAX_CHARS}.",
            "* Split larger work into A/B/C follow-up tasks.",
            "",
            "Risk model:",
            "* low — small bounded local change.",
            "* medium — broader refactor or guarded GitHub mutation.",
            "* high — secrets, auth, CI, release, destructive action, or external side effect.",
            "",
            "Mutation policy:",
            "* GitHub mutation only with --apply.",
            "* OpenClaw execution only with --execute.",
            "* Merge must not close issues.",
            "* Issue closure belongs to integration apply.",
            "",
            "Validation strategy:",
            "* ruff check .",
            "* targeted pytest for changed surface.",
            "* full pytest.",
            "* real CLI smoke command.",
            "* CI after push.",
            "",
            "Stop conditions:",
            _markdown_bullets(STOP_CONDITIONS),
            "",
            "Follow-up policy:",
            "* Create follow-up tasks when scope exceeds worker task limits.",
            "* Use dependencies instead of embedding blockers inside oversized tasks.",
            "* Return to pending DAG items after blockers are resolved.",
            "",
            "Next-roadmap bootstrap contract:",
            f"* Final roadmap tasks must create at least {NEXT_ROADMAP_MIN_DAG_NODES} "
            "small dependency-aware DAG nodes for the next roadmap.",
            "* Validate the next manifest or roadmap artifact before seeding.",
            "* Run seed/sync dry-run before any guarded --apply path.",
            "* Identify the first eligible next task and return to the execution loop.",
            "",
            "Done definition:",
            "* Roadmap has a clear issue DAG candidate.",
            "* Worker tasks fit the sizing policy.",
            "* Blockers and risks are explicit.",
            "* Required validation strategy is defined.",
            "* No unintended GitHub mutation or OpenClaw execution occurred.",
        ]
    )


def _parse_planner_issue_sections(body: str) -> dict[str, str]:
    """Parse common planner issue Markdown sections without requiring them."""
    sections: dict[str, list[str]] = {}
    current = "Details"
    sections[current] = []

    for line in body.splitlines():
        match = re.match(r"^\s{0,4}#{2,3}\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().rstrip(":")
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)

    return {
        name: _normalize_planner_section(lines)
        for name, lines in sections.items()
        if _normalize_planner_section(lines)
    }


def _normalize_planner_section(lines: list[str]) -> str:
    normalized: list[str] = []
    for line in lines:
        normalized.append(line[4:] if line.startswith("    ") else line)
    text = "\n".join(line.rstrip() for line in normalized).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _compact_single_line(text: str, *, max_chars: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _section_or_fallback(
    sections: dict[str, str],
    name: str,
    fallback: str,
) -> str:
    text = sections.get(name, "").strip()
    return text if text else fallback


def _planner_worker_objective(
    *,
    issue: dict[str, Any],
    sections: dict[str, str],
) -> str:
    explicit = sections.get("Worker objective", "").strip()
    if explicit:
        return explicit

    goal = _section_or_fallback(sections, "Goal", str(issue["title"]))
    notes = sections.get("Implementation notes", "").strip()
    if notes:
        first_action = _first_planner_action(notes)
        return (
            f"{_compact_single_line(goal)} Use this task to {first_action}. "
            "Success means the deliverables are completed with enough evidence for "
            "Signposter to advance the lifecycle without guessing."
        )
    return (
        f"{_compact_single_line(goal)} "
        "Success means the worker can identify and deliver the exact code, test, "
        "or documentation change from this issue body alone."
    )


def _first_planner_action(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        line = line.rstrip(".")
        if not line:
            continue
        return line[:1].lower() + line[1:]
    return "deliver the scoped planner task"


def _planner_deliverables(issue: dict[str, Any], sections: dict[str, str]) -> str:
    explicit = sections.get("Deliverables", "").strip()
    if explicit:
        return explicit

    notes = sections.get("Implementation notes", "").strip()
    if notes:
        return notes

    scope = sections.get("Scope", "").strip()
    if scope:
        return scope

    details = sections.get("Details", "").strip()
    if details:
        return details

    return f"- Deliver the scoped task: {issue['title']}."


def _planner_expected_changed_areas(issue: dict[str, Any], sections: dict[str, str]) -> str:
    explicit = (
        sections.get("Expected changed areas", "").strip()
        or sections.get("Likely changed files", "").strip()
        or sections.get("Files", "").strip()
    )
    if explicit:
        return explicit
    area = str(issue.get("area", "")).strip() or "unknown"
    return f"- area:{area}"


def format_planner_issue_body(plan: dict[str, Any], issue: dict[str, Any]) -> str:
    """Format a planner task as a bounded GitHub issue body."""
    dependencies = issue.get("depends_on", [])
    dependency_lines = _markdown_bullets(dependencies, fallback="none")
    dependency_metadata_lines = _format_issue_dependency_metadata(
        dependencies,
        issue.get("dependency_metadata", []),
    )
    sections = _parse_planner_issue_sections(str(issue.get("body", "")))
    worker_objective = _planner_worker_objective(issue=issue, sections=sections)
    deliverables = _planner_deliverables(issue, sections)
    changed_areas = _planner_expected_changed_areas(issue, sections)
    non_goals = _section_or_fallback(sections, "Non-goals", "No broad rewrite.")
    validation = sections.get("Validation", "").strip()
    implementation_notes = sections.get("Implementation notes", "").strip()
    metadata = sections.get("Signposter metadata", "").strip()
    acceptance_lines = _markdown_bullets(issue.get("acceptance", []))
    stop_condition_lines = _markdown_bullets(issue.get("stop_conditions", []))

    lines = [
        f"Task: {issue['key']} — {issue['title']}",
        "",
        "Roadmap context:",
        _compact_single_line(str(plan["goal"])),
        "",
        "Worker objective:",
        worker_objective,
        "",
        "Deliverables:",
        deliverables,
        "",
        "Expected changed areas:",
        changed_areas,
        "",
        "Non-goals:",
        non_goals,
        "",
        "Dependencies:",
        dependency_lines,
        "",
        "Dependency metadata:",
        dependency_metadata_lines,
        "",
        "Acceptance criteria:",
        acceptance_lines,
        "* ruff check . passes.",
        "* python -m pytest tests/ -q passes.",
    ]

    if validation:
        lines.extend(["", "Validation:", validation])
    if implementation_notes:
        lines.extend(["", "Implementation notes:", implementation_notes])
    if metadata:
        lines.extend(["", "Signposter metadata:", metadata])

    lines.extend(
        [
            "",
            "Lifecycle boundary:",
            (
                "Local-first. GitHub mutation only with --apply. Backend execution only "
                "with --execute. Mutating worker work must run from an isolated "
                "task branch/worktree, never directly from a protected base branch. "
                "Keep public output bounded."
            ),
            "",
            "Stop conditions:",
            stop_condition_lines,
        ]
    )
    return "\n".join(lines)


def build_planner_seed_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Build a dry-run issue seed plan from a validated planner plan."""
    errors = validate_planner_plan(plan)
    if errors:
        return {"status": "blocked", "errors": errors, "issues": []}

    issues = []
    for issue in plan["issues"]:
        body = format_planner_issue_body(plan, issue)
        body_size = evaluate_worker_issue_body_size(body)
        labels = [
            f"phase:{issue['phase']}",
            f"risk:{issue['risk']}",
            f"role:{issue['role']}",
            f"area:{issue['area']}",
        ]
        if not issue["depends_on"]:
            labels.append("state:ready")

        issues.append(
            {
                "key": issue["key"],
                "title": issue["title"],
                "github_title": f"{issue['key']} — {issue['title']}",
                "labels": labels,
                "depends_on": issue["depends_on"],
                "body": body,
                "body_size": body_size,
            }
        )

    body_errors = [
        f"{issue['key']}: {error}"
        for issue in issues
        for error in issue["body_size"]["errors"]
    ]
    if body_errors:
        return {"status": "blocked", "errors": body_errors, "issues": issues}

    return {"status": "ready", "errors": [], "issues": issues}


def _format_issue_dependency_metadata(
    dependencies: list[str],
    dependency_metadata: list[dict[str, Any]] | None = None,
) -> str:
    if not dependencies:
        return "* none"

    metadata_by_key = {
        str(item.get("key") or ""): item
        for item in dependency_metadata or []
        if isinstance(item, dict)
    }
    lines: list[str] = []
    for dependency in dependencies:
        metadata = metadata_by_key.get(str(dependency), {})
        github_issue = metadata.get("github_issue")
        if github_issue is None:
            github_issue_line = "  github issue: assigned during guarded seed apply"
        else:
            github_issue_line = f"  github issue: #{int(github_issue)}"
        status = str(metadata.get("status") or "pending")
        lines.extend(
            [
                f"* key: {dependency}",
                github_issue_line,
                f"  status: {status}",
            ]
        )
    return "\n".join(lines)







def validate_seed_plan_labels(
    seed_plan: dict[str, Any],
    existing_labels: set[str],
) -> dict[str, Any]:
    """Validate that every label required by a seed plan exists."""
    required_labels = sorted(
        {
            label
            for issue in seed_plan.get("issues", [])
            for label in issue.get("labels", [])
        }
    )
    missing_labels = [
        label for label in required_labels if label not in existing_labels
    ]

    if missing_labels:
        return {
            "status": "blocked",
            "required_labels": required_labels,
            "missing_labels": missing_labels,
            "errors": [
                f"missing GitHub label: {label}" for label in missing_labels
            ],
        }

    return {
        "status": "ready",
        "required_labels": required_labels,
        "missing_labels": [],
        "errors": [],
    }


def format_seed_label_preflight(result: dict[str, Any]) -> str:
    """Format seed label preflight result."""
    lines = [
        "",
        "Seed Label Preflight",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Required labels:",
    ]
    lines.extend(f"  - {label}" for label in result["required_labels"])

    if result["missing_labels"]:
        lines.extend(["", "Missing labels:"])
        lines.extend(f"  - {label}" for label in result["missing_labels"])

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  Label preflight runs before any GitHub issue creation.",
            "  Missing labels block before any GitHub issue creation.",
            "  No GitHub issue was created.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def format_planner_seed_apply_result(
    manifest_path: Path,
    result: dict[str, Any],
) -> str:
    """Format guarded seed apply result."""
    lines = [
        "",
        "Planner Seed Apply",
        "",
        "Manifest:",
        f"  {manifest_path}",
        "",
        "Status:",
        f"  {result['status']}",
    ]

    if result["created"]:
        lines.extend(["", "Created GitHub issues:"])
        for issue in result["created"]:
            url = issue.get("github_url") or ""
            lines.append(f"  {issue['key']} -> #{issue['github_issue']} {url}".rstrip())

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  GitHub mutation is only performed when --apply is explicitly used.",
            "  OpenClaw execution was not performed.",
            "  Task execution was not performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_clear_plan(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    archive_manifest_path: Path,
    issue_states: dict[int, object] | None = None,
    implemented_issue_numbers: set[int] | None = None,
) -> dict[str, Any]:
    """Build a guarded plan to retire a seeded roadmap and clear its manifest."""
    manifest = _refresh_seed_manifest_dependency_metadata(dict(manifest))
    tasks = build_planner_status(manifest, issue_states or {}).get("tasks", [])
    repo = str(manifest.get("repo", "") or "")

    if manifest_path == archive_manifest_path:
        return {
            "status": "blocked",
            "repo": repo,
            "manifest_path": str(manifest_path),
            "archive_manifest_path": str(archive_manifest_path),
            "close_targets": [],
            "already_closed": [],
            "unseeded": [],
            "errors": ["archive manifest path must differ from the active manifest path"],
            "requires_llm_analysis": False,
        }

    if not repo:
        return {
            "status": "blocked",
            "repo": repo,
            "manifest_path": str(manifest_path),
            "archive_manifest_path": str(archive_manifest_path),
            "close_targets": [],
            "already_closed": [],
            "unseeded": [],
            "errors": ["manifest repo is missing"],
            "requires_llm_analysis": False,
        }

    if not tasks:
        return {
            "status": "completed",
            "repo": repo,
            "manifest_path": str(manifest_path),
            "archive_manifest_path": str(archive_manifest_path),
            "close_targets": [],
            "already_closed": [],
            "unseeded": [],
            "errors": [],
            "requires_llm_analysis": False,
        }

    close_targets: list[dict[str, Any]] = []
    already_closed: list[dict[str, Any]] = []
    unseeded: list[dict[str, Any]] = []
    errors: list[str] = []
    implemented_issue_numbers = implemented_issue_numbers or set()

    for task in tasks:
        key = str(task.get("key") or "unknown")
        github_issue = task.get("github_issue")
        if github_issue is None:
            unseeded.append({"key": key})
            continue

        mapping_status = str(task.get("mapping_status", "") or "").strip().lower()
        if mapping_status in {"stale", "missing", "mismatched"}:
            reason = str(task.get("mapping_reason", "") or "").strip()
            error = f"{key}: GitHub issue mapping is {mapping_status}"
            if reason:
                error += f": {reason}"
            errors.append(error)
            continue

        item = {
            "key": key,
            "github_issue": int(github_issue),
            "title": str(task.get("title") or ""),
            "github_url": str(task.get("github_url") or ""),
            "state": str(task.get("state") or "").lower(),
        }
        if (
            item["github_issue"] in implemented_issue_numbers
            and item["state"] not in COMPLETED_PLANNER_STATES
        ):
            errors.append(
                f"{key}: issue has merged PR evidence; recover or integrate it instead of clearing"
            )
            continue
        if item["state"] in COMPLETED_PLANNER_STATES:
            already_closed.append(item)
        else:
            close_targets.append(item)

    status = "ready"
    if errors:
        status = "blocked"
    elif archive_manifest_path.exists():
        status = "blocked"
        errors.append(f"archive manifest already exists: {archive_manifest_path}")

    return {
        "status": status,
        "repo": repo,
        "manifest_path": str(manifest_path),
        "archive_manifest_path": str(archive_manifest_path),
        "close_targets": close_targets,
        "already_closed": already_closed,
        "unseeded": unseeded,
        "errors": errors,
        "requires_llm_analysis": False,
    }


def format_planner_clear_plan(plan: dict[str, Any]) -> str:
    """Format a dry-run roadmap clear plan."""
    lines = [
        "Signposter Planner Clear",
        "",
        "Manifest:",
        f"  {plan['manifest_path']}",
        "",
        "Archive manifest:",
        f"  {plan['archive_manifest_path']}",
        "",
        "Repo:",
        f"  {plan['repo']}",
        "",
        "Status:",
        f"  {plan['status']}",
        "",
        "Close targets:",
        f"  total: {len(plan.get('close_targets', []))}",
    ]

    for item in plan.get("close_targets", [])[:5]:
        lines.append(f"  {item['key']} -> #{item['github_issue']}")
    remaining_targets = len(plan.get("close_targets", [])) - 5
    if remaining_targets > 0:
        lines.append(f"  ... {remaining_targets} additional close target(s) omitted")

    lines.extend(
        [
            "",
            "Already closed:",
            f"  total: {len(plan.get('already_closed', []))}",
        ]
    )
    for item in plan.get("already_closed", [])[:3]:
        lines.append(f"  {item['key']} -> #{item['github_issue']}")
    remaining_closed = len(plan.get("already_closed", [])) - 3
    if remaining_closed > 0:
        lines.append(f"  ... {remaining_closed} additional closed task(s) omitted")

    if plan.get("unseeded"):
        lines.extend(["", "Unseeded tasks:"])
        for item in plan["unseeded"][:3]:
            lines.append(f"  {item['key']}")
        remaining_unseeded = len(plan["unseeded"]) - 3
        if remaining_unseeded > 0:
            lines.append(f"  ... {remaining_unseeded} additional unseeded task(s) omitted")

    if plan.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in plan["errors"])

    lines.extend(
        [
            "",
            "Planned mutations:",
        ]
    )
    if plan["status"] == "ready":
        lines.append("  close mapped GitHub issues with reason: not planned")
        lines.append("  write archive manifest copy")
        lines.append("  rewrite active manifest as empty")
    else:
        lines.append(f"  none — clear plan is not ready ({plan['status']})")

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only.",
            "  Issues are closed, not deleted.",
            "  Manifest is rewritten only after all GitHub issue closures succeed.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def _public_manifest_path(path: Path) -> str:
    raw = path.as_posix()
    for marker in ("docs/roadmaps/", "artifacts/", "tests/", "src/"):
        if marker in raw:
            return marker + raw.split(marker, 1)[1]
    return path.name


def _planner_clear_comment(
    *,
    manifest_path: Path,
    archive_manifest_path: Path,
) -> str:
    return (
        "Signposter roadmap reset\n\n"
        "This DAG issue was retired through the guarded Signposter planner clear flow.\n"
        f"Active manifest: `{_public_manifest_path(manifest_path)}`\n"
        f"Archive manifest: `{_public_manifest_path(archive_manifest_path)}`\n"
        "Reason: roadmap reset requested; issue closed as not planned."
    )


def _sanitize_retired_seed_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Remove host-local paths from a retired manifest copy."""
    sanitized = dict(manifest)
    plan_path = sanitized.get("plan")
    if isinstance(plan_path, str):
        sanitized["plan"] = _public_manifest_path(Path(plan_path))
    sanitized_issues: list[dict[str, Any]] = []
    for issue in manifest.get("issues", []):
        sanitized_issue = dict(issue)
        body_file = sanitized_issue.get("body_file")
        if isinstance(body_file, str):
            sanitized_issue["body_file"] = _public_manifest_path(Path(body_file))
        sanitized_issues.append(sanitized_issue)
    sanitized["issues"] = sanitized_issues
    return sanitized


def _build_gh_issue_close_args(
    *,
    repo: str,
    issue_number: int,
    comment: str,
) -> list[str]:
    return [
        "gh",
        "issue",
        "close",
        str(issue_number),
        "-R",
        repo,
        "--reason",
        "not planned",
        "--comment",
        comment,
    ]


def _build_cleared_seed_manifest(
    *,
    manifest: dict[str, Any],
    archive_manifest_path: Path,
) -> dict[str, Any]:
    return {
        "version": manifest.get("version", "planner.seed-manifest.v0.1"),
        "plan": manifest.get("plan"),
        "repo": manifest.get("repo"),
        "status": "cleared",
        "cleared_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "archived_manifest": _public_manifest_path(archive_manifest_path),
        "cleared_issue_count": len(manifest.get("issues", [])),
        "issues": [],
        "issue_key_map": {},
    }


def apply_planner_clear_manifest(
    *,
    manifest_path: Path,
    archive_manifest_path: Path,
    issue_states: dict[int, object] | None,
    implemented_issue_numbers: set[int] | None,
    runner: Any,
) -> dict[str, Any]:
    """Apply a guarded roadmap clear after manifest mapping verification."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = build_planner_clear_plan(
        manifest=manifest,
        manifest_path=manifest_path,
        archive_manifest_path=archive_manifest_path,
        issue_states=issue_states,
        implemented_issue_numbers=implemented_issue_numbers,
    )

    if plan["status"] == "completed":
        return {
            "status": "completed",
            "closed": [],
            "errors": [],
            "plan": plan,
        }

    if plan["status"] != "ready":
        return {
            "status": "blocked",
            "closed": [],
            "errors": list(plan.get("errors", [])),
            "plan": plan,
        }

    comment = _planner_clear_comment(
        manifest_path=manifest_path,
        archive_manifest_path=archive_manifest_path,
    )
    closed: list[dict[str, Any]] = []
    for target in plan["close_targets"]:
        result = runner(
            _build_gh_issue_close_args(
                repo=plan["repo"],
                issue_number=int(target["github_issue"]),
                comment=comment,
            )
        )
        returncode = int(getattr(result, "returncode", 1))
        if returncode != 0:
            stderr = str(getattr(result, "stderr", "") or "")
            stdout = str(getattr(result, "stdout", "") or "")
            output = "\n".join(part for part in [stderr.strip(), stdout.strip()] if part)
            return {
                "status": "failed",
                "closed": closed,
                "errors": [
                    _bounded_error(
                        output or f"gh issue close failed for #{target['github_issue']}"
                    )
                ],
                "plan": plan,
            }
        closed.append(target)

    archived_manifest = _sanitize_retired_seed_manifest(manifest)
    archived_manifest["status"] = "retired"
    archived_manifest["retired_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    archived_manifest["retired_reason"] = "roadmap reset"
    write_planner_seed_manifest(archived_manifest, archive_manifest_path)
    write_planner_seed_manifest(
        _build_cleared_seed_manifest(
            manifest=manifest,
            archive_manifest_path=archive_manifest_path,
        ),
        manifest_path,
    )
    return {
        "status": "cleared",
        "closed": closed,
        "errors": [],
        "plan": plan,
    }


def format_planner_clear_apply_result(result: dict[str, Any]) -> str:
    """Format the apply result for planner clear."""
    plan = result.get("plan", {})
    lines = [
        "Planner Clear Apply",
        "",
        "Manifest:",
        f"  {plan.get('manifest_path', 'unknown')}",
        "",
        "Archive manifest:",
        f"  {plan.get('archive_manifest_path', 'unknown')}",
        "",
        "Status:",
        f"  {result['status']}",
    ]

    if result.get("closed"):
        lines.extend(["", "Closed GitHub issues:"])
        for item in result["closed"]:
            lines.append(f"  {item['key']} -> #{item['github_issue']}")

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  GitHub mutation is only performed when --apply is explicitly used.",
            "  Issues are closed as not planned, not deleted.",
            "  Manifest rewrite happens only after successful issue closure.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_clear_recovery_plan(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    issue_states: dict[int, object] | None = None,
    merged_prs_by_issue: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a guarded plan to recover implemented issues after an overly broad clear."""
    manifest = _refresh_seed_manifest_dependency_metadata(dict(manifest))
    tasks = build_planner_status(manifest, issue_states or {}).get("tasks", [])
    repo = str(manifest.get("repo", "") or "")
    merged_prs_by_issue = merged_prs_by_issue or {}

    recover_targets: list[dict[str, Any]] = []
    already_recovered: list[dict[str, Any]] = []
    retained_not_planned: list[dict[str, Any]] = []
    errors: list[str] = []

    for task in tasks:
        key = str(task.get("key") or "unknown")
        github_issue = task.get("github_issue")
        if github_issue is None:
            continue

        mapping_status = str(task.get("mapping_status", "") or "").strip().lower()
        if mapping_status in {"stale", "missing", "mismatched"}:
            reason = str(task.get("mapping_reason", "") or "").strip()
            error = f"{key}: GitHub issue mapping is {mapping_status}"
            if reason:
                error += f": {reason}"
            errors.append(error)
            continue

        item = {
            "key": key,
            "github_issue": int(github_issue),
            "title": str(task.get("title") or ""),
            "github_url": str(task.get("github_url") or ""),
            "state": str(task.get("state") or "").lower(),
        }
        pr_info = merged_prs_by_issue.get(int(github_issue))
        if pr_info is None:
            retained_not_planned.append(item)
            continue
        item["pr_number"] = int(pr_info["number"])
        if item["state"] == "merged":
            already_recovered.append(item)
        else:
            recover_targets.append(item)

    status = "ready"
    if errors:
        status = "blocked"
    elif not recover_targets:
        status = "completed"

    return {
        "status": status,
        "repo": repo,
        "manifest_path": str(manifest_path),
        "recover_targets": recover_targets,
        "already_recovered": already_recovered,
        "retained_not_planned": retained_not_planned,
        "errors": errors,
        "requires_llm_analysis": False,
    }


def format_planner_clear_recovery_plan(plan: dict[str, Any]) -> str:
    """Format a dry-run planner clear recovery plan."""
    lines = [
        "Signposter Planner Clear Recovery",
        "",
        "Manifest:",
        f"  {plan['manifest_path']}",
        "",
        "Repo:",
        f"  {plan['repo']}",
        "",
        "Status:",
        f"  {plan['status']}",
        "",
        "Recover implemented issues:",
        f"  total: {len(plan.get('recover_targets', []))}",
    ]
    for item in plan.get("recover_targets", [])[:5]:
        lines.append(f"  {item['key']} -> #{item['github_issue']} via PR #{item['pr_number']}")
    remaining_targets = len(plan.get("recover_targets", [])) - 5
    if remaining_targets > 0:
        lines.append(f"  ... {remaining_targets} additional recovery target(s) omitted")

    lines.extend(
        [
            "",
            "Already recovered:",
            f"  total: {len(plan.get('already_recovered', []))}",
        ]
    )
    for item in plan.get("already_recovered", [])[:3]:
        lines.append(f"  {item['key']} -> #{item['github_issue']}")

    lines.extend(
        [
            "",
            "Retained not planned:",
            f"  total: {len(plan.get('retained_not_planned', []))}",
        ]
    )
    for item in plan.get("retained_not_planned", [])[:3]:
        lines.append(f"  {item['key']} -> #{item['github_issue']}")

    if plan.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in plan["errors"])

    lines.extend(["", "Planned mutations:"])
    if plan["status"] == "ready":
        lines.append("  reopen implemented issues closed as not planned")
        lines.append("  add label: state:merged")
        lines.append("  close those issues again with reason: completed")
    else:
        lines.append(f"  none — clear recovery plan is not ready ({plan['status']})")

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only.",
            "  Only issues with merged PR evidence are recovered.",
            "  Issues without implementation evidence remain closed as not planned.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def _planner_clear_recovery_reopen_comment(pr_number: int) -> str:
    return (
        "Signposter roadmap clear recovery\n\n"
        "This issue is being reopened because it was previously retired too broadly.\n"
        f"Implementation evidence: merged PR #{pr_number}."
    )


def _planner_clear_recovery_close_comment(pr_number: int) -> str:
    return (
        "Signposter roadmap clear recovery complete\n\n"
        "Recovered from mistaken roadmap retirement. "
        f"Implementation evidence: merged PR #{pr_number}.\n"
        "Issue is now closed as completed."
    )


def apply_planner_clear_recovery_plan(
    *,
    manifest_path: Path,
    issue_states: dict[int, object] | None,
    merged_prs_by_issue: dict[int, dict[str, Any]] | None,
    runner: Any,
) -> dict[str, Any]:
    """Recover implemented issues from a mistaken planner clear operation."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = build_planner_clear_recovery_plan(
        manifest=manifest,
        manifest_path=manifest_path,
        issue_states=issue_states,
        merged_prs_by_issue=merged_prs_by_issue,
    )

    if plan["status"] == "completed":
        return {
            "status": "completed",
            "recovered": [],
            "errors": [],
            "plan": plan,
        }

    if plan["status"] != "ready":
        return {
            "status": "blocked",
            "recovered": [],
            "errors": list(plan.get("errors", [])),
            "plan": plan,
        }

    recovered: list[dict[str, Any]] = []
    for target in plan["recover_targets"]:
        issue_number = int(target["github_issue"])
        pr_number = int(target["pr_number"])
        commands = [
            [
                "gh",
                "issue",
                "reopen",
                str(issue_number),
                "-R",
                plan["repo"],
                "--comment",
                _planner_clear_recovery_reopen_comment(pr_number),
            ],
            [
                "gh",
                "issue",
                "edit",
                str(issue_number),
                "-R",
                plan["repo"],
                "--add-label",
                "state:merged",
            ],
            [
                "gh",
                "issue",
                "close",
                str(issue_number),
                "-R",
                plan["repo"],
                "--reason",
                "completed",
                "--comment",
                _planner_clear_recovery_close_comment(pr_number),
            ],
        ]
        for command in commands:
            result = runner(command)
            returncode = int(getattr(result, "returncode", 1))
            if returncode != 0:
                stderr = str(getattr(result, "stderr", "") or "")
                stdout = str(getattr(result, "stdout", "") or "")
                output = "\n".join(part for part in [stderr.strip(), stdout.strip()] if part)
                return {
                    "status": "failed",
                    "recovered": recovered,
                    "errors": [
                        _bounded_error(
                            output or f"recovery command failed for #{issue_number}"
                        )
                    ],
                    "plan": plan,
                }
        recovered.append(target)

    manifest["clear_recovery_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    manifest["recovered_completed_issue_count"] = len(recovered)
    manifest["retained_not_planned_issue_count"] = len(plan.get("retained_not_planned", []))
    write_planner_seed_manifest(manifest, manifest_path)
    return {
        "status": "recovered",
        "recovered": recovered,
        "errors": [],
        "plan": plan,
    }


def format_planner_clear_recovery_apply_result(result: dict[str, Any]) -> str:
    """Format the apply result for planner clear recovery."""
    plan = result.get("plan", {})
    lines = [
        "Planner Clear Recovery Apply",
        "",
        "Manifest:",
        f"  {plan.get('manifest_path', 'unknown')}",
        "",
        "Status:",
        f"  {result['status']}",
    ]

    if result.get("recovered"):
        lines.extend(["", "Recovered issues:"])
        for item in result["recovered"]:
            lines.append(
                f"  {item['key']} -> #{item['github_issue']} via PR #{item['pr_number']}"
            )

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  GitHub mutation is only performed when --apply is explicitly used.",
            "  Recovered issues are reopened and reclosed as completed.",
            "  Issues without implementation evidence remain closed as not planned.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_body_sync_plan(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    task_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Build a guarded plan to sync seeded GitHub issue bodies from body files."""
    manifest = _refresh_seed_manifest_dependency_metadata(_copy_json_object(manifest))
    repo = str(manifest.get("repo", "") or "")
    selected_keys = {key.strip() for key in (task_keys or []) if key.strip()}
    errors: list[str] = []
    targets: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    if not repo:
        errors.append("manifest repo is missing")

    issues = manifest.get("issues", [])
    if not isinstance(issues, list) or not issues:
        errors.append("manifest contains no seeded issues")
        issues = []

    available_keys = {
        str(issue.get("key") or "")
        for issue in issues
        if isinstance(issue, dict)
    }
    for key in sorted(selected_keys - available_keys):
        errors.append(f"unknown task key: {key}")

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        key = str(issue.get("key") or "unknown")
        if selected_keys and key not in selected_keys:
            skipped.append({"key": key, "reason": "not selected"})
            continue

        github_issue = issue.get("github_issue")
        body_file = str(issue.get("body_file") or "")
        if github_issue is None:
            errors.append(f"{key}: missing github_issue mapping")
            continue
        if not body_file:
            errors.append(f"{key}: missing body_file")
            continue

        body_path = Path(body_file)
        if not body_path.is_file():
            errors.append(f"{key}: body_file does not exist: {body_file}")
            continue

        body = body_path.read_text(encoding="utf-8")
        refreshed_body = _body_with_resolved_dependency_metadata(body, issue)
        targets.append(
            {
                "key": key,
                "github_issue": int(github_issue),
                "github_url": str(issue.get("github_url") or ""),
                "title": str(issue.get("title") or ""),
                "body_file": body_file,
                "body_size": evaluate_worker_issue_body_size(refreshed_body),
                "dependency_metadata_refresh": refreshed_body != body,
            }
        )

    status = "blocked" if errors else "ready" if targets else "completed"
    return {
        "status": status,
        "repo": repo,
        "manifest_path": str(manifest_path),
        "sync_targets": targets,
        "skipped": skipped,
        "errors": errors,
        "requires_llm_analysis": False,
    }


def format_planner_body_sync_plan(plan: dict[str, Any]) -> str:
    """Format a dry-run issue body sync plan."""
    lines = [
        "Signposter Planner Body Sync",
        "",
        "Manifest:",
        f"  {plan['manifest_path']}",
        "",
        "Repo:",
        f"  {plan['repo'] or 'unknown'}",
        "",
        "Status:",
        f"  {plan['status']}",
        "",
        "Sync targets:",
        f"  total: {len(plan.get('sync_targets', []))}",
    ]
    for item in plan.get("sync_targets", [])[:8]:
        size = item.get("body_size", {})
        lines.append(
            f"  {item['key']} -> #{item['github_issue']} "
            f"({size.get('line_count', '?')} lines, {size.get('char_count', '?')} chars)"
        )
    remaining = len(plan.get("sync_targets", [])) - 8
    if remaining > 0:
        lines.append(f"  ... {remaining} additional sync target(s) omitted")

    if plan.get("skipped"):
        lines.extend(["", "Skipped:"])
        for item in plan["skipped"][:5]:
            lines.append(f"  {item['key']}: {item['reason']}")
        remaining_skipped = len(plan["skipped"]) - 5
        if remaining_skipped > 0:
            lines.append(f"  ... {remaining_skipped} additional skipped task(s) omitted")

    if plan.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in plan["errors"])

    lines.extend(["", "Planned mutations:"])
    if plan["status"] == "ready":
        lines.append("  update mapped GitHub issue bodies from local body files")
        lines.append("  no label, state, comment, PR, merge, integration, or cleanup mutation")
    else:
        lines.append(f"  none — body sync plan is not ready ({plan['status']})")

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only unless --apply is explicitly used.",
            "  Body content is read from local body files.",
            "  No OpenClaw execution is performed.",
        ]
    )
    return "\n".join(lines)


def _body_with_resolved_dependency_metadata(body: str, issue: dict[str, Any]) -> str:
    dependencies = [
        str(dependency)
        for dependency in issue.get("depends_on", [])
        if str(dependency).strip()
    ]
    if not dependencies:
        return body

    dependency_metadata_lines = _format_issue_dependency_metadata(
        dependencies,
        issue.get("dependency_metadata", []),
    )
    replacement = f"Dependency metadata:\n{dependency_metadata_lines}"
    pattern = re.compile(r"(?ms)^Dependency metadata:\n.*?(?=\n\nAcceptance criteria:\n)")
    if pattern.search(body):
        return pattern.sub(replacement, body, count=1)

    marker = "\n\nAcceptance criteria:\n"
    if marker in body:
        return body.replace(marker, f"\n\n{replacement}{marker}", 1)
    return body


def _refresh_issue_body_file_dependency_metadata(
    body_path: Path,
    issue: dict[str, Any],
) -> bool:
    body = body_path.read_text(encoding="utf-8")
    refreshed = _body_with_resolved_dependency_metadata(body, issue)
    if refreshed == body:
        return False
    body_path.write_text(refreshed, encoding="utf-8")
    return True


def apply_planner_body_sync_plan(
    *,
    manifest_path: Path,
    task_keys: list[str] | None,
    runner: Any,
) -> dict[str, Any]:
    """Apply a guarded issue body sync plan using an injected command runner."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = build_planner_body_sync_plan(
        manifest=manifest,
        manifest_path=manifest_path,
        task_keys=task_keys,
    )

    if plan["status"] == "completed":
        return {"status": "completed", "updated": [], "errors": [], "plan": plan}
    if plan["status"] != "ready":
        return {
            "status": "blocked",
            "updated": [],
            "errors": list(plan.get("errors", [])),
            "plan": plan,
        }

    updated: list[dict[str, Any]] = []
    refreshed: list[dict[str, Any]] = []
    issues_by_key = {
        str(issue.get("key") or ""): issue
        for issue in _refresh_seed_manifest_dependency_metadata(manifest).get("issues", [])
        if isinstance(issue, dict)
    }
    for target in plan["sync_targets"]:
        issue = issues_by_key.get(str(target["key"]))
        if issue is not None and _refresh_issue_body_file_dependency_metadata(
            Path(str(target["body_file"])),
            issue,
        ):
            refreshed.append(target)
        result = runner(
            [
                "gh",
                "issue",
                "edit",
                str(target["github_issue"]),
                "-R",
                plan["repo"],
                "--body-file",
                target["body_file"],
            ]
        )
        returncode = int(getattr(result, "returncode", 1))
        if returncode != 0:
            stderr = str(getattr(result, "stderr", "") or "")
            stdout = str(getattr(result, "stdout", "") or "")
            output = "\n".join(part for part in [stderr.strip(), stdout.strip()] if part)
            return {
                "status": "failed",
                "updated": updated,
                "errors": [
                    _bounded_error(
                        output or f"gh issue edit failed for #{target['github_issue']}"
                    )
                ],
                "plan": plan,
            }
        updated.append(target)

    manifest["body_sync_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    manifest["body_sync_issue_count"] = len(updated)
    manifest["body_sync_dependency_metadata_refresh_count"] = len(refreshed)
    write_planner_seed_manifest(manifest, manifest_path)
    return {
        "status": "synced",
        "updated": updated,
        "refreshed": refreshed,
        "errors": [],
        "plan": plan,
    }


def format_planner_body_sync_apply_result(result: dict[str, Any]) -> str:
    """Format the apply result for planner body sync."""
    plan = result.get("plan", {})
    lines = [
        "Planner Body Sync Apply",
        "",
        "Manifest:",
        f"  {plan.get('manifest_path', 'unknown')}",
        "",
        "Repo:",
        f"  {plan.get('repo') or 'unknown'}",
        "",
        "Status:",
        f"  {result['status']}",
    ]

    if result.get("updated"):
        lines.extend(["", "Updated GitHub issue bodies:"])
        for item in result["updated"][:10]:
            lines.append(f"  {item['key']} -> #{item['github_issue']}")
        remaining = len(result["updated"]) - 10
        if remaining > 0:
            lines.append(f"  ... {remaining} additional update(s) omitted")

    if result.get("refreshed"):
        lines.extend(["", "Resolved dependency metadata in local body files:"])
        lines.append(f"  total: {len(result['refreshed'])}")

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  Only issue bodies were updated.",
            "  No labels, states, PRs, merges, integration, or cleanup were changed.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def apply_planner_seed_manifest(
    manifest_path: Path,
    runner: Any,
) -> dict[str, Any]:
    """Apply a seed manifest using an injected command runner.

    This core function is intentionally runner-injected so tests can use a fake
    runner. The CLI layer must remain responsible for guarding real execution
    behind explicit --apply.
    """
    manifest = _refresh_seed_manifest_dependency_metadata(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    repo = manifest.get("repo", "")
    issues = manifest.get("issues", [])
    idempotence_errors = _validate_seed_manifest_idempotence(manifest)
    if idempotence_errors:
        return {
            "status": "blocked",
            "created": [],
            "errors": idempotence_errors,
        }

    missing_body_files = [
        issue["body_file"]
        for issue in issues
        if issue.get("github_issue") is None and not Path(issue["body_file"]).exists()
    ]
    if missing_body_files:
        return {
            "status": "blocked",
            "created": [],
            "errors": [
                f"missing body file: {body_file}" for body_file in missing_body_files
            ],
        }

    created: list[dict[str, Any]] = []
    for issue in issues:
        if issue.get("github_issue") is not None:
            continue

        args = _build_gh_issue_create_args(
            repo=repo,
            title=issue["title"],
            body_file=Path(issue["body_file"]),
            labels=issue["labels"],
        )
        result = runner(args)
        returncode = int(getattr(result, "returncode", 1))
        stdout = str(getattr(result, "stdout", "") or "")
        stderr = str(getattr(result, "stderr", "") or "")
        output = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)

        if returncode != 0:
            manifest["status"] = "partial"
            write_planner_seed_manifest(manifest, manifest_path)
            return {
                "status": "failed",
                "created": created,
                "errors": [_bounded_error(output or "gh issue create failed")],
            }

        issue_number = _parse_github_issue_number(output)
        if issue_number is None:
            manifest["status"] = "partial"
            write_planner_seed_manifest(manifest, manifest_path)
            return {
                "status": "failed",
                "created": created,
                "errors": [_bounded_error("could not parse created issue number")],
            }

        issue_url = _parse_github_issue_url(output)
        issue["github_issue"] = issue_number
        issue["github_url"] = issue_url
        _refresh_seed_manifest_dependency_metadata(manifest)
        created.append(
            {
                "key": issue["key"],
                "github_issue": issue_number,
                "github_url": issue_url,
            }
        )
        manifest["status"] = "partial"
        write_planner_seed_manifest(manifest, manifest_path)

    _refresh_seed_manifest_dependency_metadata(manifest)
    manifest["status"] = "applied"
    manifest["applied_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    write_planner_seed_manifest(manifest, manifest_path)
    return {"status": "applied", "created": created, "errors": []}


def _build_gh_issue_create_args(
    *,
    repo: str,
    title: str,
    body_file: Path,
    labels: list[str],
) -> list[str]:
    args = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body-file",
        str(body_file),
    ]
    for label in labels:
        args.extend(["--label", label])
    return args


def _parse_github_issue_number(output: str) -> int | None:
    match = re.search(r"/issues/(\d+)\b", output)
    if not match:
        return None
    return int(match.group(1))


def _parse_github_issue_url(output: str) -> str:
    match = re.search(r"https://github\.com/\S+/issues/\d+", output)
    if not match:
        return output.strip()
    return match.group(0)


def _bounded_error(message: str, limit: int = 500) -> str:
    message = message.strip()
    if len(message) <= limit:
        return message
    return message[:limit].rstrip() + "..."




def format_prepared_seed_manifest(
    manifest_path: Path,
    result: dict[str, Any],
) -> str:
    """Format seed manifest preparation/idempotence result."""
    lines = [
        "",
        "Prepared seed manifest:",
        f"  {manifest_path}",
        "",
        "Status:",
        f"  {result['status']}",
    ]

    if result.get("reused_existing"):
        lines.extend(["", "Existing manifest:", "  reused"])
    else:
        lines.extend(["", "Existing manifest:", "  none — created"])

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  Existing applied manifests are treated as completed/no-op.",
            "  Partial manifests are reused so missing issues can be continued.",
            "  No GitHub mutation was performed during manifest preparation.",
        ]
    )
    return "\n".join(lines)


def prepare_planner_seed_manifest(
    *,
    plan_path: Path,
    repo: str,
    seed_plan: dict[str, Any],
    body_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Prepare or reuse a seed manifest without creating duplicate issues."""
    new_manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo=repo,
        seed_plan=seed_plan,
        body_dir=body_dir,
    )

    if not manifest_path.exists():
        write_planner_seed_manifest(new_manifest, manifest_path)
        return {
            "status": "ready",
            "manifest": new_manifest,
            "errors": [],
            "reused_existing": False,
        }

    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_seed_manifest_dependency_metadata(existing)
    errors = _validate_seed_manifest_compatibility(
        existing=existing,
        expected=new_manifest,
    )
    errors.extend(_validate_seed_manifest_idempotence(existing))
    if errors:
        return {
            "status": "blocked",
            "manifest": existing,
            "errors": errors,
            "reused_existing": True,
        }

    if _seed_manifest_is_applied(existing):
        return {
            "status": "completed",
            "manifest": existing,
            "errors": [],
            "reused_existing": True,
        }

    return {
        "status": "ready",
        "manifest": existing,
        "errors": [],
        "reused_existing": True,
    }


def _validate_seed_manifest_compatibility(
    *,
    existing: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    errors: list[str] = []

    if existing.get("version") != expected.get("version"):
        errors.append("manifest version mismatch")
    if existing.get("repo") != expected.get("repo"):
        errors.append("manifest repo mismatch")
    if existing.get("plan") != expected.get("plan"):
        errors.append("manifest plan mismatch")

    existing_keys = [issue.get("key") for issue in existing.get("issues", [])]
    expected_keys = [issue.get("key") for issue in expected.get("issues", [])]
    if existing_keys != expected_keys:
        errors.append("manifest issue key mismatch")

    return errors


def _validate_seed_manifest_idempotence(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    keys_seen: set[str] = set()
    issues_by_number: dict[int, str] = {}

    for issue in manifest.get("issues", []):
        key = str(issue.get("key", "")).strip()
        if not key:
            errors.append("seed manifest contains an issue without a task key")
            continue

        if key in keys_seen:
            errors.append(f"duplicate task key in seed manifest: {key}")
        keys_seen.add(key)

        github_issue = issue.get("github_issue")
        if github_issue is None:
            continue

        try:
            github_issue_number = int(github_issue)
        except (TypeError, ValueError):
            errors.append(f"{key}: github_issue must be an integer")
            continue

        previous_key = issues_by_number.get(github_issue_number)
        if previous_key is not None and previous_key != key:
            errors.append(
                f"duplicate GitHub issue mapping: #{github_issue_number} "
                f"is assigned to {previous_key} and {key}"
            )
        issues_by_number[github_issue_number] = key

        mapping_status = str(issue.get("mapping_status", "") or "").strip().lower()
        if mapping_status in {"stale", "missing", "mismatched"}:
            mapping_reason = str(issue.get("mapping_reason", "") or "").strip()
            error = f"{key}: GitHub issue mapping is {mapping_status}"
            if mapping_reason:
                error += f": {mapping_reason}"
            errors.append(error)

    return errors


def _seed_manifest_is_applied(manifest: dict[str, Any]) -> bool:
    issues = manifest.get("issues", [])
    return (
        manifest.get("status") == "applied"
        and bool(issues)
        and all(issue.get("github_issue") is not None for issue in issues)
    )


def _refresh_seed_manifest_dependency_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    """Materialize key-based dependencies into GitHub-ready manifest metadata."""
    issues = manifest.get("issues", [])
    issue_index = {
        issue.get("key"): {
            "github_issue": issue.get("github_issue"),
            "github_url": issue.get("github_url", ""),
        }
        for issue in issues
    }

    for issue in issues:
        dependency_metadata: list[dict[str, Any]] = []
        github_depends_on: list[int] = []
        github_dependency_urls: list[str] = []

        for dependency_key in issue.get("depends_on", []):
            dependency = issue_index.get(dependency_key, {})
            github_issue = dependency.get("github_issue")
            github_url = dependency.get("github_url", "")
            dependency_metadata.append(
                {
                    "key": dependency_key,
                    "github_issue": github_issue,
                    "github_url": github_url,
                    "status": "resolved" if github_issue is not None else "pending",
                }
            )
            if github_issue is not None:
                github_depends_on.append(int(github_issue))
            if github_url:
                github_dependency_urls.append(str(github_url))

        issue["dependency_metadata"] = dependency_metadata
        issue["github_depends_on"] = github_depends_on
        issue["github_dependency_urls"] = github_dependency_urls

    manifest["issue_key_map"] = {
        key: value["github_issue"]
        for key, value in issue_index.items()
        if value["github_issue"] is not None
    }
    return manifest




COMPLETED_PLANNER_STATES = {"closed", "done", "merged"}


def build_planner_status_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Return compact planner task counts for dashboard rendering."""
    completed = {
        task.get("key")
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }
    counts = {
        "total": len(tasks),
        "pending": 0,
        "unseeded": 0,
        "ready": 0,
        "waiting": 0,
        "active": 0,
        "done": 0,
        "merged": 0,
        "blocked": 0,
        "completed": 0,
    }

    for task in tasks:
        state = str(task.get("state", "")).lower()
        workflow_state = str(task.get("workflow_state", "") or "").lower()
        mapping_status = str(task.get("mapping_status", "") or "").lower()
        missing_dependencies = [
            dependency
            for dependency in task.get("depends_on", [])
            if dependency not in completed
        ]
        if mapping_status in {"stale", "missing", "mismatched"}:
            counts["blocked"] += 1
        elif state == "active":
            counts["active"] += 1
        elif state == "unseeded":
            counts["unseeded"] += 1
            counts["pending"] += 1
        elif state == "done":
            counts["done"] += 1
            counts["completed"] += 1
        elif state == "merged":
            counts["merged"] += 1
            counts["completed"] += 1
        elif state == "closed":
            counts["completed"] += 1
        elif state in {"blocked", "failed"}:
            counts["blocked"] += 1
        elif missing_dependencies:
            counts["waiting"] += 1
        elif state == "ready" or workflow_state == "ready":
            counts["ready"] += 1
        elif state == "open":
            counts["blocked"] += 1
        else:
            counts["pending"] += 1

    return counts


def build_planner_status_artifact(
    status: dict[str, Any],
    *,
    manifest_path: str,
) -> dict[str, Any]:
    """Build a compact JSON-safe roadmap status artifact for handoff/recovery."""
    tasks = []
    for task in status.get("tasks", []):
        item = {
            "key": task.get("key"),
            "github_issue": task.get("github_issue"),
            "state": task.get("state"),
            "depends_on": task.get("depends_on", []),
        }
        mapping_status = task.get("mapping_status")
        if mapping_status:
            item["mapping_status"] = mapping_status
        mapping_reason = task.get("mapping_reason")
        if mapping_reason:
            item["mapping_reason"] = mapping_reason
        if mapping_status:
            expected_title = task.get("expected_title")
            if expected_title:
                item["expected_title"] = expected_title
            github_title = task.get("github_title")
            if github_title:
                item["github_title"] = github_title
        tasks.append(item)

    return {
        "version": "planner.status-artifact.v0.1",
        "repo": status.get("repo", ""),
        "manifest": manifest_path,
        "manifest_status": status.get("manifest_status", "unknown"),
        "status": status.get("status", "unknown"),
        "task_counts": build_planner_status_counts(status.get("tasks", [])),
        "manifest_issue_mapping": build_manifest_issue_mapping_consistency(status),
        "next_roadmap_bootstrap": build_next_roadmap_bootstrap_status_artifact(status),
        "tasks": tasks,
        "notes": [
            "Compact roadmap status artifact for handoff and loop recovery.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
            "No task execution was performed.",
        ],
    }


def build_manifest_issue_mapping_consistency(
    status: dict[str, Any],
) -> dict[str, Any]:
    """Build compact consistency evidence for manifest issue mappings."""
    counts = {
        "total": 0,
        "mapped": 0,
        "unseeded": 0,
        "ok": 0,
        "unchecked": 0,
        "stale": 0,
        "missing": 0,
        "mismatched": 0,
    }
    inconsistent_tasks: list[dict[str, Any]] = []

    for task in status.get("tasks", []):
        counts["total"] += 1
        github_issue = task.get("github_issue")
        if github_issue is None:
            counts["unseeded"] += 1
            continue

        counts["mapped"] += 1
        mapping_status = str(task.get("mapping_status", "") or "").lower()
        state = str(task.get("state", "") or "").lower()
        if mapping_status in {"stale", "missing", "mismatched"}:
            counts[mapping_status] += 1
            inconsistent_tasks.append(
                {
                    "key": task.get("key"),
                    "github_issue": github_issue,
                    "mapping_status": mapping_status,
                    "mapping_reason": task.get("mapping_reason"),
                    "expected_title": task.get("expected_title"),
                    "github_title": task.get("github_title"),
                }
            )
        elif mapping_status == "ok" or state in COMPLETED_PLANNER_STATES | {
            "open",
            "ready",
            "active",
            "done",
            "closed",
        }:
            counts["ok"] += 1
        else:
            counts["unchecked"] += 1

    if counts["stale"] or counts["missing"] or counts["mismatched"]:
        mapping_status = "inconsistent"
    elif counts["unchecked"]:
        mapping_status = "unchecked"
    elif counts["unseeded"]:
        mapping_status = "partial"
    else:
        mapping_status = "consistent"

    return {
        "version": "planner.manifest-issue-mapping.v0.1",
        "status": mapping_status,
        "counts": counts,
        "inconsistent_tasks": inconsistent_tasks,
        "notes": [
            "Read-only manifest issue mapping consistency evidence.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
        ],
    }


def build_next_roadmap_bootstrap_status_artifact(
    status: dict[str, Any],
) -> dict[str, Any]:
    """Build compact status for final roadmap bootstrap task readiness."""
    tasks = status.get("tasks", [])
    completed = {
        task["key"]
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }
    final_tasks = []
    for task in tasks:
        if not _is_final_bootstrap_task(task):
            continue

        dependencies = list(task.get("depends_on", []))
        missing_dependencies = [
            dependency for dependency in dependencies if dependency not in completed
        ]
        entry = _build_final_task_unlock_entry(
            task,
            missing_dependencies=missing_dependencies,
        )
        state = str(task.get("state", "") or "unknown").lower()
        if state in COMPLETED_PLANNER_STATES:
            entry["status"] = "completed"
        entry["state"] = state
        entry["dependencies"] = dependencies
        entry["dependency_count"] = len(dependencies)
        final_tasks.append(entry)

    if not final_tasks:
        bootstrap_status = "not-found"
    elif any(task.get("errors") for task in final_tasks):
        bootstrap_status = "blocked"
    elif any(task.get("status") == "ready" for task in final_tasks):
        bootstrap_status = "ready"
    elif all(task.get("status") == "completed" for task in final_tasks):
        bootstrap_status = "completed"
    else:
        bootstrap_status = "locked"

    return {
        "version": "planner.next-roadmap-bootstrap-status.v0.1",
        "status": bootstrap_status,
        "final_tasks": final_tasks,
        "notes": [
            "Read-only final roadmap bootstrap readiness evidence.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
            "No backend execution was performed.",
        ],
    }


def _planner_run_reconcile_hints(next_plan: dict[str, Any]) -> list[str]:
    """Build bounded reconcile hints from deterministic next-task analysis."""
    hints: list[str] = []

    blocked_items = next_plan.get("blocked", [])
    for blocked in blocked_items[:3]:
        key = blocked.get("key", "unknown")
        reason = blocked.get("reason", "blocked")
        hints.append(f"{key}: {reason}")

    waiting = next_plan.get("waiting", [])
    if waiting:
        hints.append(f"{len(waiting)} task(s) waiting for dependencies")

    if len(blocked_items) > 3:
        remaining = len(blocked_items) - 3
        hints.append(f"{remaining} additional blocked task(s) omitted")

    return hints


def _planner_run_reconcile_policy(
    next_plan: dict[str, Any],
    advance_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return token-efficient reconcile boundaries for planner run output."""
    blocked = next_plan.get("status") == "blocked"
    has_advance = bool(advance_candidates)
    escalation = "not required"
    if blocked:
        escalation = "blocked — deterministic stop before mutation"
    elif has_advance:
        escalation = "deterministic advance available"

    return {
        "mode": "deterministic-first",
        "default_llm_analysis": False,
        "escalation": escalation,
        "boundary": (
            "LLM/human reconcile only for requires_reconcile impact decisions "
            "or ambiguous DAG edits"
        ),
        "token_policy": "planner run/advance/impact use zero LLM tokens by default",
    }


def _planner_impact_level(score: int) -> str:
    """Map deterministic impact score to a compact level."""
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "low"


def apply_planner_advance_plan(
    advance_plan: dict[str, Any],
    *,
    repo: str,
    run_command: RunCommand = subprocess.run,
) -> dict[str, Any]:
    """Apply one guarded planner advance label mutation."""
    if advance_plan.get("status") == "completed":
        return {
            "status": "completed",
            "issue": advance_plan.get("issue"),
            "promoted": [],
            "commands": [],
            "already_ready": advance_plan.get("already_ready_downstream", []),
            "errors": [],
        }

    if advance_plan.get("status") != "ready":
        return {
            "status": "blocked",
            "issue": advance_plan.get("issue"),
            "promoted": [],
            "commands": [],
            "errors": ["advance plan is not ready"],
        }

    targets = advance_plan.get("targets", [])
    if not targets:
        return {
            "status": "blocked",
            "issue": advance_plan.get("issue"),
            "promoted": [],
            "commands": [],
            "errors": ["advance plan has no promotable targets"],
        }

    promoted: list[dict[str, Any]] = []
    commands: list[str] = []
    ordered_targets = sorted(
        targets,
        key=lambda item: (int(item.get("github_issue", 0)), str(item.get("key", ""))),
    )

    for index, target in enumerate(ordered_targets):
        labels_to_add = target.get("labels_to_add", [])
        if labels_to_add != ["state:ready"]:
            return {
                "status": "blocked",
                "issue": advance_plan.get("issue"),
                "promoted": [],
                "commands": [],
                "errors": ["advance target must add exactly state:ready"],
            }

        github_issue = str(target["github_issue"])
        command = [
            "gh",
            "issue",
            "edit",
            github_issue,
            "-R",
            repo,
            "--add-label",
            "state:ready",
        ]
        try:
            result = run_command(command)
        except subprocess.TimeoutExpired as exc:
            failed = _planner_advance_failed_mutation(
                target=target,
                command=command,
                status="timeout",
                returncode=None,
                stdout=exc.stdout,
                stderr=exc.stderr,
            )
            return _planner_advance_stopped_result(
                advance_plan=advance_plan,
                promoted=promoted,
                commands=commands,
                failed=failed,
                skipped_targets=ordered_targets[index + 1 :],
            )

        returncode = getattr(result, "returncode", 0) if result is not None else 0
        if returncode != 0:
            failed = _planner_advance_failed_mutation(
                target=target,
                command=command,
                status="failed",
                returncode=returncode,
                stdout=getattr(result, "stdout", ""),
                stderr=getattr(result, "stderr", ""),
            )
            return _planner_advance_stopped_result(
                advance_plan=advance_plan,
                promoted=promoted,
                commands=commands,
                failed=failed,
                skipped_targets=ordered_targets[index + 1 :],
            )

        commands.append(" ".join(command))
        promoted.append(
            {
                "key": target["key"],
                "github_issue": target["github_issue"],
                "labels_added": labels_to_add,
            }
        )

    return {
        "status": "applied",
        "issue": advance_plan.get("issue"),
        "promoted": promoted,
        "commands": commands,
        "errors": [],
    }


def _planner_advance_failed_mutation(
    *,
    target: dict[str, Any],
    command: list[str],
    status: str,
    returncode: int | None,
    stdout: object,
    stderr: object,
) -> dict[str, Any]:
    return {
        "key": target["key"],
        "github_issue": target["github_issue"],
        "command": " ".join(command),
        "status": status,
        "returncode": returncode,
        "stdout": _bounded_planner_command_output(stdout),
        "stderr": _bounded_planner_command_output(stderr),
    }


def _planner_advance_stopped_result(
    *,
    advance_plan: dict[str, Any],
    promoted: list[dict[str, Any]],
    commands: list[str],
    failed: dict[str, Any],
    skipped_targets: list[dict[str, Any]],
) -> dict[str, Any]:
    skipped = [
        {"key": target["key"], "github_issue": target["github_issue"]}
        for target in skipped_targets
    ]
    return {
        "status": "partial" if promoted else "blocked",
        "issue": advance_plan.get("issue"),
        "promoted": promoted,
        "commands": commands,
        "failed": [failed],
        "skipped": skipped,
        "errors": [
            (
                f"stopped after {failed['status']} promoting "
                f"{failed['key']} (#{failed['github_issue']})"
            )
        ],
    }


def _bounded_planner_command_output(value: object, *, limit: int = 300) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_planner_advance_apply_result(result: dict[str, Any]) -> str:
    """Format guarded planner advance apply result."""
    status = str(result.get("status", "unknown"))
    status_details = {
        "applied": (
            "  applied — GitHub label mutations listed below were executed because "
            "--apply was provided."
        ),
        "completed": (
            "  completed — downstream issue labels already show state:ready; "
            "no GitHub mutation was needed."
        ),
        "partial": (
            "  partial — one or more GitHub label mutations executed, then "
            "Signposter stopped after a failed mutation."
        ),
    }
    lines = [
        "Signposter Planner Advance Apply",
        "",
        "Status:",
        f"  {status}",
        "",
        "Status detail:",
        status_details.get(status, "  blocked — no GitHub label mutation was executed."),
    ]

    if result.get("promoted"):
        lines.extend(["", "Promoted GitHub issues:"])
        for item in result["promoted"]:
            labels = ", ".join(item.get("labels_added", []))
            lines.append(
                f"  {item['key']} -> #{item['github_issue']} "
                f"added labels: {labels}"
            )

    if result.get("already_ready"):
        lines.extend(["", "Already ready GitHub issues:"])
        for item in result["already_ready"]:
            lines.append(
                f"  {item['key']} -> #{item['github_issue']} "
                "already has state:ready"
            )

    if result.get("commands"):
        lines.extend(["", "Executed commands:"])
        lines.extend(f"  {command}" for command in result["commands"])

    if result.get("failed"):
        lines.extend(["", "Failed mutation:"])
        for item in result["failed"]:
            returncode = item["returncode"] if item["returncode"] is not None else "none"
            lines.extend(
                [
                    f"  {item['key']} -> #{item['github_issue']}",
                    f"    status: {item['status']}",
                    f"    returncode: {returncode}",
                    f"    command: {item['command']}",
                    f"    stdout: {'present' if item.get('stdout') else 'empty'}",
                    f"    stderr: {'present' if item.get('stderr') else 'empty'}",
                ]
            )

    if result.get("skipped"):
        lines.extend(["", "Skipped mutations after stop:"])
        for item in result["skipped"]:
            lines.append(f"  {item['key']} -> #{item['github_issue']}")

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    if result.get("failed"):
        lines.extend(
            [
                "",
                "Retry guidance:",
                "  Inspect the listed GitHub issue labels before retrying planner advance.",
                "  No later GitHub mutation was attempted after the failed command.",
            ]
        )

    lines.extend(
        [
            "",
            "Notes:",
            "  Issue closure was not performed.",
            "  No manifest mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No LLM analysis was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_run_plan_from_status(
    status: dict[str, Any],
    *,
    manifest_path: str,
) -> dict[str, Any]:
    """Build a read-only planner run dashboard from planner status."""
    next_plan = build_planner_next_from_status(status)
    step_plan = build_planner_step_from_next(next_plan)

    advance_candidates = []
    for task in status.get("tasks", []):
        github_issue = task.get("github_issue")
        task_state = str(task.get("state", "")).lower()
        if github_issue is None or task_state not in COMPLETED_PLANNER_STATES:
            continue

        impact = build_planner_impact_from_status(
            status,
            issue=int(github_issue),
            manifest_path=manifest_path,
        )
        if impact.get("status") != "ready":
            continue
        if impact.get("impact", {}).get("decision") != "advance_mainline":
            continue

        advance_plan = build_planner_advance_plan_from_status(
            status,
            issue=int(github_issue),
            manifest_path=manifest_path,
        )
        if advance_plan.get("status") != "ready":
            continue

        advance_candidates.append(
            {
                "issue": int(github_issue),
                "task_key": task["key"],
                "decision": impact["impact"]["decision"],
                "suggested_command": impact.get("suggested_command"),
                "targets": [target["key"] for target in advance_plan["targets"]],
            }
        )

    run_status = (
        "completed"
        if next_plan.get("status") == "completed" and not advance_candidates
        else "ready"
    )

    return {
        "status": run_status,
        "repo": status.get("repo", ""),
        "manifest_path": manifest_path,
        "planner_status": status.get("status", "unknown"),
        "status_counts": build_planner_status_counts(status.get("tasks", [])),
        "next": next_plan,
        "step": step_plan,
        "advance_candidates": advance_candidates,
        "reconcile_policy": _planner_run_reconcile_policy(next_plan, advance_candidates),
        "requires_llm_analysis": any(
            candidate.get("requires_llm_analysis", False)
            for candidate in advance_candidates
        ),
        "notes": [
            "Read-only planner run dashboard.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
            "No claim was performed.",
            "No worktree was created.",
            "No OpenClaw execution was performed.",
            "No LLM analysis was performed.",
        ],
    }


def format_planner_run_plan(result: dict[str, Any]) -> str:
    """Format a read-only planner run dashboard."""
    lines = [
        "Signposter Planner Run",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Repo:",
        f"  {result.get('repo', '')}",
        "",
        "Manifest:",
        f"  {result.get('manifest_path', '')}",
        "",
        "Planner status:",
        f"  {result.get('planner_status', 'unknown')}",
    ]

    counts = result.get("status_counts", {})
    if counts:
        lines.extend(
            [
                "",
                "Task counts:",
                f"  {_format_planner_run_counts(counts)}",
            ]
        )

    next_plan = result.get("next", {})
    next_task = next_plan.get("next")
    lines.extend(["", "Next task:"])
    if next_task:
        deps = ", ".join(next_task.get("depends_on", [])) or "none"
        lines.extend(
            [
                f"  {next_task['key']} — issue: #{next_task['github_issue']} — "
                f"state: {next_task['state']}",
                f"  {next_task.get('github_url', '')}",
                f"  depends on: {deps}",
            ]
        )
    else:
        lines.append("  none")
        completion = _planner_run_completion_lines(result, next_plan, counts)
        if completion:
            lines.extend(["", "Roadmap completion:"])
            lines.extend(f"  {line}" for line in completion)

    reconcile_hints = _planner_run_reconcile_hints(next_plan)
    lines.extend(["", "Reconcile hints:"])
    if reconcile_hints:
        lines.extend(f"  - {hint}" for hint in reconcile_hints)
    else:
        lines.append("  none")

    step = result.get("step", {})
    lines.extend(["", "Suggested step command:"])
    if step.get("suggested_command"):
        lines.append(f"  {step['suggested_command']}")
    else:
        lines.append("  none")

    candidates = result.get("advance_candidates", [])
    lines.extend(["", "Advance candidates:"])
    if candidates:
        for candidate in candidates:
            targets = ", ".join(candidate.get("targets", [])) or "none"
            lines.extend(
                [
                    f"  issue #{candidate['issue']} / {candidate['task_key']}:",
                    f"    decision: {candidate['decision']}",
                    f"    targets: {targets}",
                    f"    suggested command: {candidate['suggested_command']}",
                ]
            )
    else:
        lines.append("  none")

    policy = result.get("reconcile_policy", {})
    lines.extend(
        [
            "",
            "Reconcile policy:",
            f"  mode: {policy.get('mode', 'deterministic-first')}",
            "  default LLM analysis: "
            f"{str(policy.get('default_llm_analysis', False)).lower()}",
            f"  escalation: {policy.get('escalation', 'not required')}",
            f"  boundary: {policy.get('boundary', 'not specified')}",
            f"  token policy: {policy.get('token_policy', 'zero LLM tokens by default')}",
        ]
    )

    lines.extend(
        [
            "",
            "Requires:",
            f"  LLM analysis: {str(result.get('requires_llm_analysis', False)).lower()}",
        ]
    )

    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.get("notes", []))
    return "\n".join(lines)


def _planner_run_completion_lines(
    result: dict[str, Any],
    next_plan: dict[str, Any],
    counts: dict[str, Any],
) -> list[str]:
    """Return explicit completed-roadmap wording for planner run output."""
    if next_plan.get("status") != "completed" and result.get("planner_status") != "completed":
        return []
    total = counts.get("total", 0)
    completed = counts.get("completed", 0)
    reason = str(next_plan.get("reason") or "no dependency-ready task remains")
    return [
        "status: completed",
        f"reason: {reason}",
        f"completed tasks: {completed}/{total}",
        "next task: none",
        "next action: no planner advance is required for this manifest",
    ]


def _format_planner_run_counts(counts: dict[str, Any]) -> str:
    order = (
        "total",
        "pending",
        "ready",
        "waiting",
        "active",
        "done",
        "merged",
        "blocked",
        "completed",
    )
    return " ".join(f"{key}={counts.get(key, 0)}" for key in order)


def build_planner_advance_plan_from_status(
    status: dict[str, Any],
    *,
    issue: int,
    manifest_path: str,
) -> dict[str, Any]:
    """Build a dry-run plan to promote downstream planner tasks."""
    tasks = status.get("tasks", [])
    repo = status.get("repo", "")
    source_task = next((item for item in tasks if item.get("github_issue") == issue), None)

    if source_task is None:
        return {
            "status": "blocked",
            "issue": issue,
            "source_task": None,
            "targets": [],
            "final_task_unlocks": [],
            "planned_github_mutations": [],
            "planned_manifest_mutations": [],
            "requires_llm_analysis": False,
            "manifest_path": manifest_path,
            "reasons": [f"issue #{issue} is not present in the planner manifest"],
        }

    source_state = str(source_task.get("state", "")).lower()
    if source_state not in COMPLETED_PLANNER_STATES:
        return {
            "status": "blocked",
            "issue": issue,
            "source_task": source_task,
            "targets": [],
            "final_task_unlocks": [],
            "planned_github_mutations": [],
            "planned_manifest_mutations": [],
            "requires_llm_analysis": False,
            "manifest_path": manifest_path,
            "reasons": [
                f"issue is not completed: state={source_task.get('state')}",
                "advance waits for completed/merged/done source tasks",
            ],
        }

    source_key = source_task.get("key")
    downstream = [
        task
        for task in tasks
        if source_key in task.get("depends_on", [])
    ]
    completed = {
        task["key"]
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }

    targets = []
    blocked_downstream = []
    already_ready_downstream = []
    final_task_unlocks = []
    planned_github_mutations = []
    for task in downstream:
        github_issue = task.get("github_issue")
        state = str(task.get("state", "")).lower()
        workflow_state = str(task.get("workflow_state", "") or "").lower()
        labels = set(task.get("labels", []))
        if github_issue is None:
            continue
        if state == "ready" or workflow_state == "ready" or "state:ready" in labels:
            if _is_final_bootstrap_task(task):
                final_task_unlocks.append(
                    _build_final_task_unlock_entry(
                        task,
                        missing_dependencies=[],
                    )
                )
            already_ready_downstream.append(
                {
                    "key": task["key"],
                    "github_issue": github_issue,
                    "state": task.get("state"),
                    "workflow_state": task.get("workflow_state"),
                }
            )
            continue
        if state not in {"open", "unknown"}:
            continue
        missing_dependencies = [
            dependency
            for dependency in task.get("depends_on", [])
            if dependency not in completed
        ]
        if missing_dependencies:
            if _is_final_bootstrap_task(task):
                final_task_unlocks.append(
                    _build_final_task_unlock_entry(
                        task,
                        missing_dependencies=missing_dependencies,
                    )
                )
            blocked_downstream.append(
                {
                    "key": task["key"],
                    "missing_dependencies": missing_dependencies,
                }
            )
            continue

        target = {
            "key": task["key"],
            "github_issue": github_issue,
            "github_url": task.get("github_url", ""),
            "state": task.get("state"),
            "labels_to_add": ["state:ready"],
        }
        targets.append(target)
        if _is_final_bootstrap_task(task):
            final_task_unlocks.append(
                _build_final_task_unlock_entry(
                    task,
                    missing_dependencies=[],
                )
            )

        command = f"gh issue edit {github_issue}"
        if repo:
            command += f" -R {repo}"
        command += " --add-label state:ready"
        planned_github_mutations.append(command)

    status_value = (
        "ready"
        if targets
        else "completed"
        if already_ready_downstream and not blocked_downstream
        else "blocked"
    )
    reasons = [
        f"source issue is completed: state={source_task.get('state')}",
        "advance dry-run used zero LLM tokens",
    ]
    if targets:
        reasons.append("one or more downstream tasks can be promoted")
    elif already_ready_downstream and not blocked_downstream:
        reasons.append(
            "one or more downstream tasks are already state:ready; "
            "no duplicate mutation is planned"
        )
    elif downstream:
        reasons.append("no downstream task is currently promotable")
        if blocked_downstream:
            reasons.append("one or more downstream tasks are waiting for dependencies")
            for blocked in blocked_downstream[:3]:
                missing = ", ".join(blocked["missing_dependencies"])
                reasons.append(f"{blocked['key']} waits for dependencies: {missing}")
    else:
        reasons.append("source task has no downstream dependents")

    return {
        "status": status_value,
        "issue": issue,
        "source_task": source_task,
        "targets": targets,
        "already_ready_downstream": already_ready_downstream,
        "final_task_unlocks": final_task_unlocks,
        "planned_github_mutations": planned_github_mutations,
        "planned_manifest_mutations": [],
        "requires_llm_analysis": False,
        "manifest_path": manifest_path,
        "reasons": reasons,
    }


def format_planner_advance_plan(result: dict[str, Any]) -> str:
    """Format a dry-run planner advance plan."""
    issue = result["issue"]
    status = str(result.get("status", "unknown"))
    if status == "ready":
        status_detail = (
            "ready — dry-run only; use planner advance --apply to add listed labels"
        )
    elif status == "completed":
        status_detail = (
            "completed — downstream issue labels already show state:ready; "
            "no apply mutation is needed"
        )
    else:
        status_detail = "blocked — dry-run only; do not run apply until reasons are resolved"
    lines = [
        f"Signposter Planner Advance — Issue #{issue}",
        "",
        "Status:",
        f"  {status}",
        "",
        "Status detail:",
        f"  {status_detail}",
    ]

    source_task = result.get("source_task")
    if source_task:
        lines.extend(
            [
                "",
                "Source task:",
                f"  {source_task['key']} — state: {source_task['state']}",
            ]
        )

    targets = result.get("targets", [])
    lines.extend(["", "Would promote:"])
    if targets:
        for target in targets:
            lines.append(
                f"  {target['key']} — issue: #{target['github_issue']} — "
                f"state: {target['state']}"
            )
    else:
        lines.append("  none")

    already_ready = result.get("already_ready_downstream", [])
    if already_ready:
        lines.extend(["", "Already ready downstream:"])
        for target in already_ready:
            lines.append(
                f"  {target['key']} — issue: #{target['github_issue']} — "
                "no duplicate state:ready mutation planned"
            )

    final_task_unlocks = result.get("final_task_unlocks", [])
    if final_task_unlocks:
        lines.extend(["", "Final-task unlock contract:"])
        for unlock in final_task_unlocks:
            github_issue = unlock.get("github_issue")
            issue_text = f"#{github_issue}" if github_issue is not None else "unknown"
            lines.append(
                f"  {unlock.get('key', 'unknown')} — issue: {issue_text} — "
                f"status: {unlock.get('status', 'unknown')}"
            )
            lines.append(f"    contract: {unlock.get('contract_status', 'unknown')}")
            lines.append(f"    current prefix: {unlock.get('current_prefix', '')}")
            lines.append(f"    next prefix: {unlock.get('next_prefix', '')}")
            lines.append(
                f"    minimum DAG nodes: {unlock.get('minimum_dag_nodes', '')}"
            )
            waiting_on = unlock.get("waiting_on", [])
            if waiting_on:
                lines.append(f"    waiting on: {', '.join(waiting_on)}")
            errors = unlock.get("errors", [])
            if errors:
                lines.append(f"    errors: {'; '.join(errors)}")
            lines.append(f"    safety: {unlock.get('safety_note', '')}")

    lines.extend(["", "Planned GitHub mutations:"])
    mutations = result.get("planned_github_mutations", [])
    if mutations:
        lines.append("  Preview only; these commands were not executed.")
        lines.extend(f"  {mutation}" for mutation in mutations)
    else:
        lines.append("  none")

    lines.extend(["", "Planned manifest mutations:"])
    manifest_mutations = result.get("planned_manifest_mutations", [])
    if manifest_mutations:
        lines.extend(f"  {mutation}" for mutation in manifest_mutations)
    else:
        lines.append("  none")

    if result.get("reasons"):
        lines.extend(["", "Reasons:"])
        lines.extend(f"  - {reason}" for reason in result["reasons"])

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only; command previews were not executed.",
            "  No GitHub mutation was performed.",
            "  No issue was closed.",
            "  No manifest mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No LLM analysis was performed.",
        ]
    )
    return "\n".join(lines)


def _is_final_bootstrap_task(task: dict[str, Any]) -> bool:
    key = str(task.get("key", ""))
    title = str(task.get("title") or task.get("expected_title") or "")
    searchable = f"{key} {title}".lower()
    return (
        "final" in searchable
        and "bootstrap" in searchable
        and _mainline_from_task_key(key) is not None
    )


def _build_final_task_unlock_entry(
    task: dict[str, Any],
    *,
    missing_dependencies: list[str],
) -> dict[str, Any]:
    current_prefix = _mainline_from_task_key(str(task.get("key", ""))) or ""
    next_prefix = _infer_next_roadmap_prefix(current_prefix, task)
    contract = build_next_roadmap_bootstrap_contract(
        current_prefix=current_prefix,
        next_prefix=next_prefix,
    )
    errors = validate_next_roadmap_bootstrap_contract(contract)
    contract_status = "blocked" if errors else "ready"
    dependency_status = "locked" if missing_dependencies else "ready"
    status = "blocked" if errors else dependency_status
    return {
        "key": task.get("key"),
        "title": task.get("title") or task.get("expected_title") or "",
        "github_issue": task.get("github_issue"),
        "status": status,
        "contract_status": contract_status,
        "current_prefix": current_prefix,
        "next_prefix": next_prefix,
        "minimum_dag_nodes": contract.get("minimum_dag_nodes"),
        "waiting_on": list(missing_dependencies),
        "errors": errors,
        "safety_note": (
            "final task unlock is dry-run only until planner advance --apply "
            "is explicit"
        ),
    }


def _infer_next_roadmap_prefix(current_prefix: str, task: dict[str, Any]) -> str:
    title = str(task.get("title") or task.get("expected_title") or "")
    text = f"{task.get('key', '')} {title}".upper()
    current = current_prefix.upper()
    for prefix in re.findall(r"\bH\d{3,}\b", text):
        if prefix != current:
            return str(prefix)

    match = re.fullmatch(r"([A-Z]+)(\d+)", current)
    if not match:
        return ""
    prefix, number = match.groups()
    return f"{prefix}{int(number) + 1:0{len(number)}d}"


def build_planner_impact_from_status(
    status: dict[str, Any],
    *,
    issue: int,
    manifest_path: str,
) -> dict[str, Any]:
    """Build a token-free planner impact assessment from a status summary."""
    tasks = status.get("tasks", [])
    task = next((item for item in tasks if item.get("github_issue") == issue), None)
    if task is None:
        return {
            "status": "blocked",
            "issue": issue,
            "task": None,
            "impact": {
                "score": 0,
                "level": "unknown",
                "decision": "issue_not_in_manifest",
            },
            "downstream_tasks": [],
            "requires_llm_analysis": False,
            "suggested_command": None,
            "reasons": [f"issue #{issue} is not present in the planner manifest"],
        }

    state = str(task.get("state", "")).lower()
    downstream_task_items = [
        item
        for item in tasks
        if task.get("key") in item.get("depends_on", [])
    ]
    downstream_tasks = [item["key"] for item in downstream_task_items]
    side_task_downstream_tasks = [
        item["key"] for item in downstream_task_items if item.get("side_task")
    ]
    blocked_downstream_tasks = [
        item["key"]
        for item in downstream_task_items
        if str(item.get("state", "")).lower() in {"blocked", "failed"}
    ]
    advanceable_downstream_tasks = [
        item["key"]
        for item in downstream_task_items
        if str(item.get("state", "")).lower() == "open"
    ]
    mainline_downstream_tasks = [
        item["key"] for item in downstream_task_items if not item.get("side_task")
    ]

    if state not in COMPLETED_PLANNER_STATES:
        return {
            "status": "blocked",
            "issue": issue,
            "task": task,
            "impact": {
                "score": 0,
                "level": "none",
                "decision": "wait_for_completion",
            },
            "downstream_tasks": downstream_tasks,
            "requires_llm_analysis": False,
            "suggested_command": None,
            "reasons": [
                f"issue is not completed: state={task.get('state')}",
                "impact scoring waits for completed/merged/done tasks",
            ],
        }

    score = 10 if downstream_tasks else 0
    if len(downstream_tasks) > 1:
        score += min(30, (len(downstream_tasks) - 1) * 10)
    if side_task_downstream_tasks:
        score += 10
    if task.get("side_task"):
        score += 5
    if blocked_downstream_tasks:
        score += 50

    level = _planner_impact_level(score)
    if blocked_downstream_tasks:
        decision = "block_mainline"
    elif downstream_tasks and not advanceable_downstream_tasks:
        decision = "already_advanced"
    elif score >= 40:
        decision = "requires_reconcile"
    else:
        decision = "advance_mainline"

    signals: list[str] = []
    if mainline_downstream_tasks:
        signals.append("mainline_dependent")
    if side_task_downstream_tasks:
        signals.append("side_task_dependent")
    if task.get("side_task"):
        signals.append("completed_side_task")
    if blocked_downstream_tasks:
        signals.append("blocked_downstream")
    if downstream_tasks and not advanceable_downstream_tasks and not blocked_downstream_tasks:
        signals.append("downstream_already_advanced")

    reasons = [
        f"issue is completed: state={task.get('state')}",
        "deterministic impact scoring used zero LLM tokens",
    ]
    if downstream_tasks:
        reasons.append("task has downstream dependents")
    else:
        reasons.append("task has no downstream dependents")
    if side_task_downstream_tasks:
        reasons.append("task has side-task downstream dependents")
    if task.get("side_task"):
        reasons.append("completed task is a side-task")
    if blocked_downstream_tasks:
        reasons.append("one or more downstream tasks are blocked")
    if downstream_tasks and not advanceable_downstream_tasks and not blocked_downstream_tasks:
        reasons.append("downstream tasks are already ready, active, or completed")

    suggested_command = None
    if decision == "advance_mainline":
        suggested_command = (
            f"signposter planner advance --manifest {manifest_path} "
            f"--issue {issue} --dry-run"
        )
    llm_reconcile = {
        "allowed": decision == "requires_reconcile",
        "default": "disabled",
        "boundary": (
            "optional only for requires_reconcile impact decisions after "
            "deterministic graph evidence is shown"
        ),
        "reason": (
            "impact is ambiguous enough for optional reconcile"
            if decision == "requires_reconcile"
            else "deterministic decision is available"
        ),
    }

    return {
        "status": "ready",
        "issue": issue,
        "task": task,
        "impact": {
            "score": score,
            "level": level,
            "decision": decision,
            "signals": signals,
        },
        "downstream_tasks": downstream_tasks,
        "side_task_downstream_tasks": side_task_downstream_tasks,
        "blocked_downstream_tasks": blocked_downstream_tasks,
        "advanceable_downstream_tasks": advanceable_downstream_tasks,
        "requires_llm_analysis": decision == "requires_reconcile",
        "llm_reconcile": llm_reconcile,
        "suggested_command": suggested_command,
        "reasons": reasons,
    }


def format_planner_impact(result: dict[str, Any]) -> str:
    """Format a token-free planner impact assessment."""
    issue = result["issue"]
    impact = result["impact"]
    lines = [
        f"Signposter Planner Impact — Issue #{issue}",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Impact:",
        f"  score: {impact['score']}",
        f"  level: {impact['level']}",
        f"  decision: {impact['decision']}",
        f"  signals: {', '.join(impact.get('signals', [])) or 'none'}",
    ]

    task = result.get("task")
    if task:
        lines.extend(
            [
                "",
                "Task:",
                f"  {task['key']} — state: {task['state']}",
            ]
        )

    downstream = result.get("downstream_tasks", [])
    lines.extend(
        [
            "",
            "Downstream:",
            f"  downstream: {', '.join(downstream) if downstream else 'none'}",
            "  advanceable downstream: "
            f"{', '.join(result.get('advanceable_downstream_tasks', [])) or 'none'}",
            "  side-task downstream: "
            f"{', '.join(result.get('side_task_downstream_tasks', [])) or 'none'}",
            "  blocked downstream: "
            f"{', '.join(result.get('blocked_downstream_tasks', [])) or 'none'}",
            "",
            "Requires:",
            f"  LLM analysis: {str(result.get('requires_llm_analysis', False)).lower()}",
        ]
    )

    llm_reconcile = result.get("llm_reconcile")
    if llm_reconcile:
        lines.extend(
            [
                "",
                "LLM reconcile:",
                f"  allowed: {str(llm_reconcile.get('allowed', False)).lower()}",
                f"  default: {llm_reconcile.get('default', 'disabled')}",
                f"  boundary: {llm_reconcile.get('boundary', 'not specified')}",
                f"  reason: {llm_reconcile.get('reason', 'not specified')}",
            ]
        )

    if result.get("suggested_command"):
        lines.extend(
            [
                "",
                "Suggested next command:",
                f"  {result['suggested_command']}",
            ]
        )

    if result.get("reasons"):
        lines.extend(["", "Reasons:"])
        lines.extend(f"  - {reason}" for reason in result["reasons"])

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No manifest mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No LLM analysis was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_side_task_plan(
    *,
    manifest: dict[str, Any],
    manifest_path: str,
    key: str,
    title: str,
    reason: str,
    depends_on: list[str],
    parent: int | None,
    return_to: int | None,
    phase: str = "build",
    risk: str = "medium",
    role: str = "worker",
    area: str = "scheduler",
    gate: str = "ci",
    mainline: str | None = None,
) -> dict[str, Any]:
    """Build a read-only side-task insertion plan from an existing manifest."""
    manifest = _refresh_seed_manifest_dependency_metadata(_copy_json_object(manifest))
    issues = manifest.get("issues", [])
    task_keys = {str(issue.get("key", "")) for issue in issues}
    errors: list[str] = []

    key = key.strip()
    title = title.strip()
    reason = reason.strip()
    depends_on = [dependency.strip() for dependency in depends_on if dependency.strip()]

    if not key:
        errors.append("side-task key is required")
    elif key in task_keys:
        errors.append(f"side-task key already exists in manifest: {key}")

    if not title:
        errors.append("side-task title is required")
    if not reason:
        errors.append("side-task reason is required")
    if not depends_on:
        errors.append("depends_on must include at least one existing task key")
    for field_name, field_value in {
        "phase": phase,
        "risk": risk,
        "role": role,
        "area": area,
        "gate": gate,
    }.items():
        if not field_value.strip():
            errors.append(f"{field_name} is required")

    unknown_dependencies = [
        dependency for dependency in depends_on if dependency not in task_keys
    ]
    for dependency in unknown_dependencies:
        errors.append(f"unknown dependency: {dependency}")

    parent_task = None
    if parent is None:
        errors.append("parent issue is required")
    else:
        parent_task = _find_manifest_task_by_github_issue(issues, parent)
        if parent_task is None:
            errors.append(f"parent issue #{parent} is not present in the manifest")

    return_task = None
    if return_to is None:
        errors.append("return_to issue is required")
    else:
        return_task = _find_manifest_task_by_github_issue(issues, return_to)
        if return_task is None:
            errors.append(f"return_to issue #{return_to} is not present in the manifest")

    labels = [
        f"phase:{phase.strip()}",
        f"risk:{risk.strip()}",
        f"role:{role.strip()}",
        f"area:{area.strip()}",
    ]
    inferred_mainline = (
        mainline
        or (return_task or {}).get("mainline")
        or (parent_task or {}).get("mainline")
        or _mainline_from_task_key(str((return_task or parent_task or {}).get("key", "")))
    )
    planned_task = {
        "key": key,
        "title": title,
        "labels": labels,
        "depends_on": depends_on,
        "mainline": inferred_mainline,
        "parent": parent,
        "return_to": return_to,
        "side_task": True,
        "gate": gate.strip(),
        "github_issue": None,
        "github_url": "",
    }

    if errors:
        return {
            "status": "blocked",
            "manifest_path": manifest_path,
            "planned_task": planned_task,
            "planned_manifest_mutations": [],
            "planned_github_mutations": [],
            "requires_llm_analysis": False,
            "errors": errors,
            "reasons": [
                "side-task insertion planning stopped before mutation preview",
                "side-task planning used zero LLM tokens",
            ],
        }

    return {
        "status": "ready",
        "manifest_path": manifest_path,
        "planned_task": planned_task,
        "planned_manifest_mutations": [
            f"append side-task {key} to {manifest_path}",
            "set side_task: true",
            f"set parent: #{parent}",
            f"set return_to: #{return_to}",
            f"set depends_on: {', '.join(depends_on)}",
        ],
        "planned_github_mutations": [],
        "requires_llm_analysis": False,
        "errors": [],
        "reasons": [
            f"parent issue #{parent} is present in the manifest",
            f"return target issue #{return_to} is present in the manifest",
            "all dependency keys are present in the manifest",
            "side-task plan used zero LLM tokens",
        ],
    }


def format_planner_side_task_plan(result: dict[str, Any]) -> str:
    """Format a read-only side-task insertion plan."""
    task = result.get("planned_task", {})
    status = str(result.get("status", "unknown"))
    deps = ", ".join(task.get("depends_on", [])) or "none"
    labels = ", ".join(task.get("labels", [])) or "none"
    parent = f"#{task.get('parent')}" if task.get("parent") is not None else "none"
    return_to = (
        f"#{task.get('return_to')}" if task.get("return_to") is not None else "none"
    )
    lines = [
        "Signposter Planner Side-Task Plan",
        "",
        "Status:",
        f"  {status}",
        "",
        "Manifest:",
        f"  {result.get('manifest_path', '')}",
        "",
        "Side task:",
        f"  key: {task.get('key', '')}",
        f"  title: {task.get('title', '')}",
        "  side-task: yes",
        f"  parent: {parent}",
        f"  return-to: {return_to}",
        f"  mainline: {task.get('mainline') or 'none'}",
        f"  depends on: {deps}",
        f"  labels: {labels}",
        f"  gate: {task.get('gate') or 'none'}",
        "",
        "Planned manifest mutations:",
    ]

    manifest_mutations = result.get("planned_manifest_mutations", [])
    if manifest_mutations:
        lines.append("  Preview only; these changes were not written.")
        lines.extend(f"  - {mutation}" for mutation in manifest_mutations)
    else:
        lines.append("  none")

    lines.extend(["", "Planned GitHub mutations:"])
    github_mutations = result.get("planned_github_mutations", [])
    if github_mutations:
        lines.append("  Preview only; these commands were not executed.")
        lines.extend(f"  {mutation}" for mutation in github_mutations)
    else:
        lines.append("  none")

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    if result.get("reasons"):
        lines.extend(["", "Reasons:"])
        lines.extend(f"  - {reason}" for reason in result["reasons"])

    lines.extend(
        [
            "",
            "Requires:",
            f"  LLM analysis: {str(result.get('requires_llm_analysis', False)).lower()}",
            "",
            "Notes:",
            "  Dry-run only; no side task was inserted.",
            "  No GitHub mutation was performed.",
            "  No GitHub issue was created.",
            "  No manifest mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No LLM analysis was performed.",
        ]
    )
    return "\n".join(lines)


def _find_manifest_task_by_github_issue(
    issues: list[dict[str, Any]],
    github_issue: int,
) -> dict[str, Any] | None:
    for issue in issues:
        if issue.get("github_issue") == github_issue:
            return issue
    return None


def _copy_json_object(value: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(value))
    if not isinstance(copied, dict):
        raise TypeError("expected JSON object")
    return copied


def _mainline_from_task_key(key: str) -> str | None:
    if "-" not in key:
        return None
    prefix = key.split("-", 1)[0].strip()
    return prefix or None


def build_planner_next_from_status(status: dict[str, Any]) -> dict[str, Any]:
    """Choose the next dependency-ready task from a planner status summary."""
    tasks = status.get("tasks", [])
    if not tasks:
        return {
            "status": "completed",
            "reason": "no planner tasks found",
            "next": None,
            "waiting": [],
            "blocked": [],
        }

    completed = {
        task["key"]
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }

    waiting: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    ready_mainline: dict[str, Any] | None = None
    missing_ready_label = False
    mapping_blocked = False

    for task in tasks:
        state = str(task.get("state", "")).lower()
        if state in COMPLETED_PLANNER_STATES:
            continue

        if task.get("github_issue") is None:
            blocked.append(
                {
                    "key": task["key"],
                    "reason": "task is not seeded to a GitHub issue",
                }
            )
            continue

        mapping_status = str(task.get("mapping_status", "") or "").lower()
        if mapping_status in {"stale", "missing", "mismatched"}:
            mapping_blocked = True
            mapping_reason = str(task.get("mapping_reason", "") or "").strip()
            reason = f"GitHub issue mapping is {mapping_status}"
            if mapping_reason:
                reason += f": {mapping_reason}"
            blocked.append(
                {
                    "key": task["key"],
                    "reason": reason,
                    "github_issue": task.get("github_issue"),
                    "expected_title": task.get("expected_title"),
                    "github_title": task.get("github_title"),
                }
            )
            continue

        missing_dependencies = [
            dependency
            for dependency in task.get("depends_on", [])
            if dependency not in completed
        ]
        if missing_dependencies:
            waiting.append(
                {
                    "key": task["key"],
                    "reason": "waiting for dependencies",
                    "missing_dependencies": missing_dependencies,
                }
            )
            continue

        if state == "ready":
            next_task = {
                "key": task["key"],
                "title": task["title"],
                "github_issue": task["github_issue"],
                "github_url": task["github_url"],
                "state": task["state"],
                "depends_on": task["depends_on"],
                "mainline": task.get("mainline"),
                "parent": task.get("parent"),
                "return_to": task.get("return_to"),
                "side_task": bool(task.get("side_task", False)),
                "return_status": task.get("return_status"),
            }
            if task.get("side_task"):
                return {
                    "status": "ready",
                    "reason": "dependency-ready side-task selected before mainline",
                    "next": next_task,
                    "waiting": waiting,
                    "blocked": blocked,
                }
            if ready_mainline is None:
                ready_mainline = next_task
            continue

        if (
            state == "open"
            and task.get("github_state") == "open"
            and not task.get("workflow_state")
        ):
            missing_ready_label = True
            completed_source_issues = [
                dependency_issue
                for dependency_issue in task.get("github_depends_on", [])
                if dependency_issue is not None
            ]
            blocked.append(
                {
                    "key": task["key"],
                    "reason": "dependency-ready task is open but missing GitHub label state:ready",
                    "github_issue": task.get("github_issue"),
                    "reconcile_issues": completed_source_issues,
                }
            )
            continue

        if state == "open":
            next_task = {
                "key": task["key"],
                "title": task["title"],
                "github_issue": task["github_issue"],
                "github_url": task["github_url"],
                "state": task["state"],
                "depends_on": task["depends_on"],
                "mainline": task.get("mainline"),
                "parent": task.get("parent"),
                "return_to": task.get("return_to"),
                "side_task": bool(task.get("side_task", False)),
                "return_status": task.get("return_status"),
            }
            if task.get("side_task"):
                return {
                    "status": "ready",
                    "reason": "dependency-ready side-task selected before mainline",
                    "next": next_task,
                    "waiting": waiting,
                    "blocked": blocked,
                }
            if ready_mainline is None:
                ready_mainline = next_task
            continue

        blocked.append(
            {
                "key": task["key"],
                "reason": f"unsupported task state: {task.get('state')}",
            }
        )

    if ready_mainline is not None:
        return {
            "status": "ready",
            "reason": "first dependency-ready open task selected",
            "next": ready_mainline,
            "waiting": waiting,
            "blocked": blocked,
        }

    if len(completed) == len(tasks):
        return {
            "status": "completed",
            "reason": "all planner tasks are completed",
            "next": None,
            "waiting": waiting,
            "blocked": blocked,
        }

    if missing_ready_label:
        return {
            "status": "blocked",
            "reason": "dependency-ready open task is missing GitHub label state:ready",
            "next": None,
            "waiting": waiting,
            "blocked": blocked,
        }

    if mapping_blocked:
        return {
            "status": "blocked",
            "reason": "one or more GitHub issue mappings are stale or mismatched",
            "next": None,
            "waiting": waiting,
            "blocked": blocked,
        }

    return {
        "status": "waiting",
        "reason": "no dependency-ready open task is available",
        "next": None,
        "waiting": waiting,
        "blocked": blocked,
    }


def format_planner_next_from_status(result: dict[str, Any]) -> str:
    """Format next-from-status result."""
    lines = [
        "Signposter Planner Next",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Reason:",
        f"  {result['reason']}",
    ]

    if result["next"] is not None:
        task = result["next"]
        deps = ", ".join(task["depends_on"]) if task["depends_on"] else "none"
        lines.extend(
            [
                "",
                "Next task:",
                f"  {task['key']} — issue: #{task['github_issue']} — state: {task['state']}",
                f"  {task['github_url']}",
                f"  depends on: {deps}",
            ]
        )
        if task.get("side_task"):
            parent = f"#{task['parent']}" if task.get("parent") is not None else "none"
            return_to = f"#{task['return_to']}" if task.get("return_to") is not None else "none"
            mainline = task.get("mainline") or "none"
            lines.extend(
                [
                    "  side-task: yes",
                    f"  parent: {parent}",
                    f"  return-to: {return_to}",
                    f"  mainline: {mainline}",
                ]
            )
            return_status = task.get("return_status") or {}
            if return_status:
                lines.extend(
                    [
                        f"  return state: {return_status.get('state', 'unknown')}",
                        "  return ready: "
                        f"{'yes' if return_status.get('ready') else 'no'}",
                        "  mainline waiting on side-task: "
                        f"{'yes' if return_status.get('mainline_waiting') else 'no'}",
                    ]
                )

    if result["waiting"]:
        lines.extend(["", "Waiting:"])
        for item in result["waiting"]:
            missing = ", ".join(item.get("missing_dependencies", []))
            lines.append(f"  {item['key']} — {item['reason']}: {missing}")

    if result["blocked"]:
        lines.extend(["", "Blocked:"])
        for item in result["blocked"]:
            line = f"  {item['key']} — {item['reason']}"
            github_issue = item.get("github_issue")
            if github_issue is not None:
                line += f" (issue #{github_issue})"
            lines.append(line)
            expected_title = item.get("expected_title")
            if expected_title:
                lines.append(f"    expected title: {expected_title}")
            github_title = item.get("github_title")
            if github_title:
                lines.append(f"    GitHub title: {github_title}")
            reconcile_issues = item.get("reconcile_issues", [])
            if reconcile_issues:
                issues = ", ".join(f"#{issue}" for issue in reconcile_issues)
                lines.append(
                    "    reconcile hint: run planner advance/apply from completed "
                    f"dependency issue(s) {issues} to restore state:ready before claim"
                )

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No claim was performed.",
            "  No worktree was created.",
            "  No OpenClaw execution was performed.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)



def _repo_from_github_url(url: str) -> str | None:
    """Best-effort owner/repo extraction from a GitHub issue URL."""
    marker = "github.com/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1]
    parts = tail.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def build_planner_step_from_next(next_result: dict[str, Any]) -> dict[str, Any]:
    """Build a dry-run planner step plan from a planner next result."""
    next_task = next_result.get("next")
    if next_result.get("status") != "ready" or next_task is None:
        return {
            "status": next_result.get("status", "blocked"),
            "reason": next_result.get("reason", "no ready task"),
            "next": next_task,
            "suggested_command": None,
            "errors": next_result.get("errors", []),
        }

    github_issue = next_task.get("github_issue")
    if github_issue is None:
        return {
            "status": "blocked",
            "reason": "next task has no GitHub issue number",
            "next": next_task,
            "suggested_command": None,
            "errors": ["next task has no GitHub issue number"],
        }

    repo = _repo_from_github_url(str(next_task.get("github_url", "")))
    run_command = (
        f"signposter run --repo {repo} --issue {github_issue} --dry-run"
        if repo
        else f"signposter run --issue {github_issue} --dry-run"
    )
    workflow_hints = [
        {
            "label": "inspect lifecycle",
            "command": (
                f"signposter lifecycle status --repo {repo} --issue {github_issue}"
                if repo
                else f"signposter lifecycle status --issue {github_issue}"
            ),
        },
        {
            "label": "claim dry-run",
            "command": (
                f"signposter claim --repo {repo} --dry-run"
                if repo
                else "signposter claim --dry-run"
            ),
        },
        {
            "label": "worktree plan",
            "command": (
                f"signposter worktree plan --repo {repo} --issue {github_issue}"
                if repo
                else f"signposter worktree plan --issue {github_issue}"
            ),
        },
        {
            "label": "run dry-run",
            "command": run_command,
        },
    ]
    if next_task.get("side_task") and next_task.get("return_to") is not None:
        return_to = int(next_task["return_to"])
        workflow_hints.append(
            {
                "label": "return target",
                "command": (
                    f"signposter lifecycle status --repo {repo} --issue {return_to}"
                    if repo
                    else f"signposter lifecycle status --issue {return_to}"
                ),
            }
        )

    return {
        "status": "ready",
        "reason": next_result.get("reason", "next task is ready"),
        "next": next_task,
        "suggested_command": run_command,
        "workflow_hints": workflow_hints,
        "errors": [],
    }


def format_planner_step(result: dict[str, Any]) -> str:
    """Format a dry-run planner step plan."""
    lines = [
        "Signposter Planner Step",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Reason:",
        f"  {result.get('reason', '')}",
    ]

    if result.get("next") is not None:
        task = result["next"]
        deps = ", ".join(task["depends_on"]) if task["depends_on"] else "none"
        lines.extend(
            [
                "",
                "Next task:",
                f"  {task['key']} — issue: #{task['github_issue']} — state: {task['state']}",
                f"  {task['github_url']}",
                f"  depends on: {deps}",
            ]
        )

    if result.get("suggested_command"):
        lines.extend(
            [
                "",
                "Suggested command:",
                f"  {result['suggested_command']}",
            ]
        )

    if result.get("workflow_hints"):
        lines.extend(["", "Workflow hints:"])
        for hint in result["workflow_hints"]:
            lines.append(f"  {hint['label']}:")
            lines.append(f"    {hint['command']}")
        lines.extend(
            [
                "  note:",
                "    Hints only; no command above was executed.",
            ]
        )

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No claim was performed.",
            "  No worktree was created.",
            "  No OpenClaw execution was performed.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)


def _planner_status_expected_github_title(issue: dict[str, Any]) -> str:
    return str(issue.get("github_title") or issue.get("title") or "").strip()


def build_planner_status(
    manifest: dict[str, Any],
    issue_states: Mapping[int, object] | None = None,
) -> dict[str, Any]:
    """Build a local planner status summary from a seed manifest."""
    manifest = _refresh_seed_manifest_dependency_metadata(dict(manifest))
    issue_states = issue_states or {}
    tasks = []

    for issue in manifest.get("issues", []):
        github_issue = issue.get("github_issue")
        state = "unseeded"
        github_state: str | None = None
        workflow_state: str | None = None
        mapping_status: str | None = None
        mapping_reason: str | None = None
        github_title: str | None = None
        expected_title: str | None = _planner_status_expected_github_title(issue)
        manifest_workflow_state = _workflow_state_from_manifest_labels(
            issue.get("labels", [])
        )
        if github_issue is not None:
            snapshot = issue_states.get(int(github_issue), "unknown")
            if isinstance(snapshot, dict):
                raw_github_state = snapshot.get("github_state")
                raw_workflow_state = snapshot.get("workflow_state")
                raw_state = snapshot.get("state")
                raw_mapping_status = snapshot.get("mapping_status")
                raw_mapping_reason = snapshot.get("mapping_reason")
                raw_github_title = snapshot.get("github_title")
                raw_expected_title = snapshot.get("expected_title")
                github_state = (
                    str(raw_github_state).strip().lower()
                    if raw_github_state
                    else None
                )
                workflow_state = (
                    str(raw_workflow_state).strip().lower()
                    if raw_workflow_state
                    else None
                )
                state = str(raw_state).strip().lower() if raw_state else "unknown"
                if (
                    state == "open"
                    and github_state == "open"
                    and workflow_state is None
                    and manifest_workflow_state
                ):
                    workflow_state = manifest_workflow_state
                    state = workflow_state
                mapping_status = (
                    str(raw_mapping_status).strip().lower()
                    if raw_mapping_status
                    else None
                )
                mapping_reason = (
                    str(raw_mapping_reason).strip() if raw_mapping_reason else None
                )
                github_title = str(raw_github_title).strip() if raw_github_title else None
                expected_title = (
                    str(raw_expected_title).strip()
                    if raw_expected_title
                    else expected_title
                )
            else:
                state = str(snapshot).lower()
                if state == "open":
                    github_state = "open"
                    if manifest_workflow_state:
                        workflow_state = manifest_workflow_state
                elif state == "closed":
                    github_state = "closed"
                elif state in ALLOWED_TASK_STATUSES:
                    workflow_state = state
                    github_state = "closed" if state == "merged" else "open"

        tasks.append(
            {
                "key": issue.get("key"),
                "title": issue.get("title"),
                "github_issue": github_issue,
                "github_url": issue.get("github_url", ""),
                "state": state,
                "github_state": github_state,
                "workflow_state": workflow_state,
                "mapping_status": mapping_status,
                "mapping_reason": mapping_reason,
                "github_title": github_title,
                "expected_title": expected_title,
                "labels": issue.get("labels", []),
                "depends_on": issue.get("depends_on", []),
                "github_depends_on": issue.get("github_depends_on", []),
                "dependency_metadata": issue.get("dependency_metadata", []),
                "mainline": issue.get("mainline"),
                "parent": issue.get("parent"),
                "return_to": issue.get("return_to"),
                "side_task": bool(issue.get("side_task", False)),
            }
        )

    _annotate_side_task_return_status(tasks)

    if not tasks:
        status = "empty"
    elif all(task["github_issue"] is None for task in tasks):
        status = "unseeded"
    elif any(task["github_issue"] is None for task in tasks):
        status = "partial"
    elif all(task["state"] in {"closed", "merged", "done"} for task in tasks):
        status = "completed"
    else:
        status = "active"

    return {
        "version": "planner.status.v0.1",
        "manifest_status": manifest.get("status", "unknown"),
        "repo": manifest.get("repo", ""),
        "status": status,
        "tasks": tasks,
        "notes": [
            "Local status summary only.",
            "Unseeded tasks have no GitHub issue yet.",
            (
                "Open tasks need state:ready or unfinished dependencies "
                "to avoid blocked classification."
            ),
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
            "No task execution was performed.",
        ],
    }


def _annotate_side_task_return_status(tasks: list[dict[str, Any]]) -> None:
    """Attach return-to-mainline readiness metadata to side-task rows."""
    tasks_by_issue = {
        int(task["github_issue"]): task
        for task in tasks
        if task.get("github_issue") is not None
    }
    completed = {
        task["key"]
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }

    for task in tasks:
        if not task.get("side_task"):
            continue

        return_issue = _coerce_int(task.get("return_to"))
        return_task = tasks_by_issue.get(return_issue) if return_issue is not None else None
        side_completed = str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
        if return_task is None:
            task["return_status"] = {
                "state": "missing",
                "ready": False,
                "mainline_waiting": not side_completed,
                "missing_dependencies": [],
                "reason": "return target is not present in the manifest",
            }
            continue

        missing_dependencies = [
            dependency
            for dependency in return_task.get("depends_on", [])
            if dependency not in completed
        ]
        return_state = str(return_task.get("state", "")).lower() or "unknown"
        return_workflow_state = str(return_task.get("workflow_state", "") or "").lower()
        return_is_ready_state = return_state == "ready" or (
            return_state == "open" and return_workflow_state == "ready"
        )
        return_ready = (
            side_completed
            and return_is_ready_state
            and not missing_dependencies
        )
        mainline_waiting = (
            not side_completed
            and return_state not in COMPLETED_PLANNER_STATES
        )
        task["return_status"] = {
            "state": return_state,
            "ready": return_ready,
            "mainline_waiting": mainline_waiting,
            "missing_dependencies": missing_dependencies,
            "reason": (
                "side task complete and return target can resume"
                if return_ready
                else "return target is waiting for side task or dependencies"
            ),
        }


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _workflow_state_from_manifest_labels(labels: list[Any]) -> str | None:
    for label in labels:
        name = str(label).strip().lower()
        if not name.startswith("state:"):
            continue
        workflow_state = name.split(":", 1)[1].strip()
        if workflow_state:
            return workflow_state
    return None


def format_planner_status(status: dict[str, Any]) -> str:
    """Format planner status summary."""
    counts = build_planner_status_counts(status["tasks"])
    lines = [
        "Signposter Planner Status",
        "",
        "Repo:",
        f"  {status['repo']}",
        "",
        "Manifest status:",
        f"  {status['manifest_status']}",
        "",
        "Status:",
        f"  {status['status']}",
        "",
        "Progress:",
        f"  total: {counts.get('total', 0)}",
        f"  pending: {counts.get('pending', 0)}",
        f"  unseeded: {counts.get('unseeded', 0)}",
        f"  ready: {counts.get('ready', 0)}",
        f"  waiting: {counts.get('waiting', 0)}",
        f"  active: {counts.get('active', 0)}",
        f"  done: {counts.get('done', 0)}",
        f"  merged: {counts.get('merged', 0)}",
        f"  blocked: {counts.get('blocked', 0)}",
        f"  completed: {counts.get('completed', 0)}",
    ]

    mapping = status.get("manifest_issue_mapping")
    if mapping is None:
        mapping = build_manifest_issue_mapping_consistency(status)
    mapping_counts = mapping.get("counts", {})
    lines.extend(
        [
            "",
            "Manifest issue mapping:",
            f"  status: {mapping.get('status', 'unknown')}",
            f"  mapped: {mapping_counts.get('mapped', 0)}",
            f"  unseeded: {mapping_counts.get('unseeded', 0)}",
            f"  unchecked: {mapping_counts.get('unchecked', 0)}",
            f"  ok: {mapping_counts.get('ok', 0)}",
            f"  stale: {mapping_counts.get('stale', 0)}",
            f"  missing: {mapping_counts.get('missing', 0)}",
            f"  mismatched: {mapping_counts.get('mismatched', 0)}",
        ]
    )
    inconsistent_tasks = mapping.get("inconsistent_tasks", [])
    if inconsistent_tasks:
        lines.append("  inconsistent tasks:")
        for item in inconsistent_tasks[:3]:
            github_issue = item.get("github_issue")
            issue_text = f"#{github_issue}" if github_issue is not None else "none"
            line = (
                f"    {item.get('key', 'unknown')} — issue: {issue_text} — "
                f"{item.get('mapping_status', 'unknown')}"
            )
            reason = str(item.get("mapping_reason", "") or "").strip()
            if reason:
                line += f" — {reason}"
            lines.append(line)
        omitted = len(inconsistent_tasks) - 3
        if omitted > 0:
            lines.append(f"    ... {omitted} additional inconsistent task(s) omitted")

    bootstrap = status.get("next_roadmap_bootstrap")
    if bootstrap is None:
        bootstrap = build_next_roadmap_bootstrap_status_artifact(status)
    if bootstrap.get("status") != "not-found":
        lines.extend(
            [
                "",
                "Next-roadmap bootstrap:",
                f"  status: {bootstrap.get('status', 'unknown')}",
            ]
        )
        for final_task in bootstrap.get("final_tasks", []):
            github_issue = final_task.get("github_issue")
            issue_text = f"#{github_issue}" if github_issue is not None else "none"
            lines.append(
                f"  final task: {final_task.get('key', 'unknown')} — "
                f"issue: {issue_text} — state: {final_task.get('state', 'unknown')}"
            )
            lines.append(
                f"    transition: {final_task.get('current_prefix', '')} -> "
                f"{final_task.get('next_prefix', '')}"
            )
            lines.append(
                f"    minimum DAG nodes: {final_task.get('minimum_dag_nodes', '')}"
            )
            waiting_on = final_task.get("waiting_on", [])
            if waiting_on:
                lines.append(f"    waiting on: {', '.join(waiting_on)}")
            errors = final_task.get("errors", [])
            if errors:
                lines.append(f"    errors: {'; '.join(errors)}")

    lines.extend(["", "Tasks:"])

    if not status["tasks"]:
        lines.append("  none")
    else:
        for task in status["tasks"]:
            issue_text = (
                f"#{task['github_issue']}"
                if task["github_issue"] is not None
                else "none"
            )
            url = f" {task['github_url']}" if task["github_url"] else ""
            lines.append(
                f"  {task['key']} — issue: {issue_text} — state: {task['state']}{url}"
            )
            mapping_status = str(task.get("mapping_status", "") or "").lower()
            if mapping_status in {"stale", "missing", "mismatched"}:
                mapping_reason = str(task.get("mapping_reason", "") or "").strip()
                detail = f"    mapping: {mapping_status}"
                if mapping_reason:
                    detail += f" — {mapping_reason}"
                lines.append(detail)
                github_title = str(task.get("github_title", "") or "").strip()
                if github_title:
                    lines.append(f"    GitHub title: {github_title}")
                expected_title = str(task.get("expected_title", "") or "").strip()
                if expected_title:
                    lines.append(f"    expected title: {expected_title}")
            dependency_metadata = task.get("dependency_metadata", [])
            if dependency_metadata:
                deps = ", ".join(
                    f"{dependency['key']} (#{dependency['github_issue']})"
                    if dependency.get("github_issue") is not None
                    else dependency["key"]
                    for dependency in dependency_metadata
                )
                lines.append(f"    depends on: {deps}")
            if task.get("side_task"):
                parent = f"#{task['parent']}" if task.get("parent") is not None else "none"
                return_to = (
                    f"#{task['return_to']}" if task.get("return_to") is not None else "none"
                )
                mainline = task.get("mainline") or "none"
                lines.append(
                    "    side-task: yes"
                    f" · parent: {parent}"
                    f" · return-to: {return_to}"
                    f" · mainline: {mainline}"
                )
                return_status = task.get("return_status") or {}
                if return_status:
                    ready = "yes" if return_status.get("ready") else "no"
                    waiting = "yes" if return_status.get("mainline_waiting") else "no"
                    missing = ", ".join(return_status.get("missing_dependencies", []))
                    detail = (
                        "    return state: "
                        f"{return_status.get('state', 'unknown')}"
                        f" · return ready: {ready}"
                        f" · mainline waiting on side-task: {waiting}"
                    )
                    if missing:
                        detail += f" · missing: {missing}"
                    lines.append(detail)

    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in status["notes"])
    return "\n".join(lines)


def build_planner_seed_manifest(
    *,
    plan_path: Path,
    repo: str,
    seed_plan: dict[str, Any],
    body_dir: Path,
) -> dict[str, Any]:
    """Build a local seed manifest for future guarded GitHub issue creation."""
    issues = []
    for issue in seed_plan["issues"]:
        body_file = body_dir / f"{issue['key']}.md"
        issues.append(
            {
                "key": issue["key"],
                "title": issue["github_title"],
                "labels": issue["labels"],
                "depends_on": issue["depends_on"],
                "body_file": str(body_file),
                "body_size": issue["body_size"],
                "github_issue": None,
                "github_url": "",
                "mainline": issue.get("mainline"),
                "parent": issue.get("parent"),
                "return_to": issue.get("return_to"),
                "side_task": bool(issue.get("side_task", False)),
            }
        )

    return _refresh_seed_manifest_dependency_metadata(
        {
        "version": "planner.seed-manifest.v0.1",
        "plan": str(plan_path),
        "repo": repo,
        "status": "dry-run",
        "issues": issues,
        "notes": [
            "Local manifest only.",
            "No GitHub mutation was performed.",
            "No GitHub issue was created.",
            "No OpenClaw execution was performed.",
        ],
        }
    )



def format_written_seed_manifest(manifest_path: Path) -> str:
    """Format local seed manifest write result."""
    return "\n".join(
        [
            "",
            "Written seed manifest:",
            f"  {manifest_path}",
            "",
            "Notes:",
            "  Local manifest only.",
            "  No GitHub mutation was performed.",
            "  No GitHub issue was created.",
            "  No OpenClaw execution was performed.",
        ]
    )


def write_planner_seed_manifest(
    manifest: dict[str, Any],
    manifest_path: Path,
) -> None:
    """Write a local planner seed manifest JSON file."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_planner_seed_issue_bodies(
    seed_plan: dict[str, Any],
    body_dir: Path,
) -> list[Path]:
    """Write generated issue bodies to local Markdown files."""
    if seed_plan["status"] != "ready":
        return []

    body_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for issue in seed_plan["issues"]:
        body_path = body_dir / f"{issue['key']}.md"
        body_path.write_text(issue["body"] + "\n", encoding="utf-8")
        written.append(body_path)

    return written


def format_written_issue_bodies(paths: list[Path]) -> str:
    """Format local issue body write result."""
    lines = [
        "",
        "Written issue body files:",
    ]

    if not paths:
        lines.append("  none")
    else:
        lines.extend(f"  {path}" for path in paths)

    lines.extend(
        [
            "",
            "Notes:",
            "  Local files only.",
            "  No GitHub mutation was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def format_planner_seed_plan(
    plan_path: Path,
    seed_plan: dict[str, Any],
    *,
    repo: str = "<owner/repo>",
    body_dir: Path = Path("artifacts/plans/issue-bodies"),
    show_body: bool = False,
    show_commands: bool = False,
) -> str:
    """Format a dry-run planner seed plan."""
    lines = [
        "Signposter Planner Seed",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Status:",
        f"  {seed_plan['status']}",
    ]

    if seed_plan["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in seed_plan["errors"])
    else:
        lines.extend(["", "Proposed GitHub issues:"])
        for issue in seed_plan["issues"]:
            deps = ", ".join(issue["depends_on"]) if issue["depends_on"] else "none"
            labels = ", ".join(issue["labels"])
            body_size = issue.get("body_size", {})
            size_status = body_size.get("status", "unknown")
            line_count = body_size.get("line_count", "?")
            char_count = body_size.get("char_count", "?")
            lines.append(f"  {issue['key']} — {issue['title']}")
            lines.append(f"    GitHub title: {issue['github_title']}")
            lines.append(f"    labels: {labels}")
            lines.append(f"    depends on: {deps}")
            lines.append(
                f"    body size: {size_status} "
                f"({line_count} lines, {char_count} chars)"
            )
            if show_commands:
                body_file = body_dir / f"{issue['key']}.md"
                command = format_gh_issue_create_command(
                    repo=repo,
                    title=issue["github_title"],
                    body_file=body_file,
                    labels=issue["labels"],
                )
                lines.extend(
                    [
                        "    command preview:",
                        "      ----- BEGIN GH COMMAND -----",
                    ]
                )
                lines.extend(f"      {line}" for line in command.splitlines())
                lines.append("      ----- END GH COMMAND -----")
            if show_body:
                lines.extend(
                    [
                        "    body:",
                        "      ----- BEGIN ISSUE BODY -----",
                    ]
                )
                lines.extend(f"      {line}" for line in issue["body"].splitlines())
                lines.append("      ----- END ISSUE BODY -----")

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only.",
            "  Command previews are not executed.",
            "  Body-file paths are preview placeholders.",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def format_planner_draft(plan: dict[str, Any], output_path: Path) -> str:
    """Format a compact human-readable planner draft summary."""
    lines = [
        "Signposter Planner Draft",
        "",
        "Goal:",
        f"  {plan['goal']}",
        "",
        "Proposed issues:",
    ]

    for issue in plan["issues"]:
        deps = ", ".join(issue["depends_on"]) if issue["depends_on"] else "none"
        lines.append(f"  {issue['key']} — {issue['title']} (depends on: {deps})")

    lines.extend(
        [
            "",
            "Status:",
            f"  {plan['status']}",
            "",
            "Output:",
            f"  {output_path}",
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def format_planner_validation(plan_path: Path, errors: list[str]) -> str:
    """Format planner validation result."""
    status = "pass" if not errors else "blocked"
    lines = [
        "Signposter Planner Validate",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Status:",
        f"  {status}",
    ]

    if errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in errors)

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def _issue(key: str, title: str, area: str, depends_on: list[str]) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "body": f"{title}. Keep this task narrow and bounded.",
        "phase": "build",
        "risk": "low",
        "role": "worker",
        "area": area,
        "depends_on": depends_on,
        "acceptance": [f"{title} is implemented and tested."],
        "stop_conditions": STOP_CONDITIONS,
        "allowed_mutations": [],
    }


def _require(
    value: dict[str, Any],
    field: str,
    expected_type: type,
    errors: list[str],
) -> None:
    if field not in value:
        errors.append(f"missing {field}")
    elif not isinstance(value[field], expected_type):
        errors.append(f"{field} must be {expected_type.__name__}")


def _list_required(
    issue: dict[str, Any],
    field: str,
    key: str,
    errors: list[str],
) -> None:
    value = issue.get(field)
    if not isinstance(value, list):
        errors.append(f"{key}: {field} must be a list")
    elif field != "depends_on" and not value:
        errors.append(f"{key}: {field} must not be empty")


def _list_contains_all(
    obj: dict[str, Any],
    field: str,
    required: list[str],
    errors: list[str],
) -> None:
    value = obj.get(field)
    if not isinstance(value, list):
        return

    for item in required:
        if item not in value:
            errors.append(f"{field} missing required item: {item}")


def _issue_status(issue: dict[str, Any]) -> str:
    return str(issue.get("status", "pending")).strip().lower() or "pending"


def _markdown_bullets(items: list[Any], fallback: str | None = None) -> str:
    if not items:
        if fallback is None:
            return "* none"
        return f"* {fallback}"
    return "\n".join(f"* {item}" for item in items)


def _target_command_for_issue(issue: dict[str, Any]) -> str:
    area = str(issue.get("area", "")).strip()
    key = str(issue.get("key", "")).strip()
    if area == "docs":
        return "python -m pytest tests/ -q"
    if area == "tests":
        return "python -m pytest tests/ -q"
    if key.startswith("WATCH-"):
        return "signposter lifecycle watch --repo owner/repo --issue N --interval 5"
    return "signposter <command> --repo owner/repo"
