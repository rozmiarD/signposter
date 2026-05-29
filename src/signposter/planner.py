"""Local planner draft surface for Signposter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PLAN_VERSION = "planner.v0.1"

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
