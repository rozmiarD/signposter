"""Build a local planner manifest from GitHub issue DAG metadata."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signposter.codex_cli_backend import RunCommand
from signposter.dependencies import parse_depends_on
from signposter.scheduler import parse_graph_metadata


@dataclass(frozen=True)
class IssueManifestPlan:
    repo: str
    output: Path
    manifest: dict[str, Any]
    apply: bool
    status: str
    notes: list[str]


def build_issue_dag_manifest(
    repo: str,
    *,
    limit: int = 200,
    run_command: RunCommand = subprocess.run,
) -> dict[str, Any]:
    issues = _fetch_issues(repo, limit=limit, run_command=run_command)
    tasks = []
    seen: set[int] = set()
    for issue in sorted(issues, key=lambda item: int(item["number"])):
        number = int(issue["number"])
        if number in seen:
            continue
        seen.add(number)
        labels = [label["name"] for label in issue.get("labels", [])]
        body = issue.get("body") or ""
        metadata = parse_graph_metadata(body)
        tasks.append(
            {
                "id": f"issue-{number}",
                "issue": number,
                "title": issue.get("title", ""),
                "state": _workflow_state(labels) or issue.get("state", "UNKNOWN").lower(),
                "depends_on": parse_depends_on(body),
                "mainline": metadata.mainline,
                "parent": metadata.parent,
                "return_to": metadata.return_to,
                "side_task": metadata.side_task,
                "labels": labels,
            }
        )
    return {
        "version": "planner.issue-dag-manifest.v0.1",
        "repo": repo,
        "tasks": tasks,
        "notes": [
            "Local manifest generated from GitHub issue metadata.",
            "Depends-On references were parsed from issue bodies.",
        ],
    }


def plan_issue_dag_manifest(
    repo: str,
    output: Path,
    *,
    limit: int = 200,
    apply: bool = False,
    run_command: RunCommand = subprocess.run,
) -> IssueManifestPlan:
    manifest = build_issue_dag_manifest(repo, limit=limit, run_command=run_command)
    if apply:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return IssueManifestPlan(
        repo=repo,
        output=output,
        manifest=manifest,
        apply=apply,
        status="written" if apply else "planned",
        notes=[
            "No GitHub mutation was performed.",
            "No issue was created or closed.",
            "Manifest file was written only because --apply was used."
            if apply
            else "No manifest mutation was performed.",
        ],
    )


def format_issue_dag_manifest_plan(plan: IssueManifestPlan) -> str:
    lines = [
        "Signposter Issue DAG Manifest",
        "",
        "Repository:",
        f"  {plan.repo}",
        "",
        "Output:",
        f"  {plan.output}",
        "",
        "Status:",
        f"  {plan.status}",
        "",
        "Tasks:",
    ]
    tasks = plan.manifest.get("tasks", [])
    if not tasks:
        lines.append("  none")
    for task in tasks:
        deps = ", ".join(f"#{dep}" for dep in task.get("depends_on", [])) or "none"
        side = " side-task=yes" if task.get("side_task") else ""
        return_to = (
            f" return-to=#{task['return_to']}" if task.get("return_to") is not None else ""
        )
        lines.append(
            f"  #{task['issue']}: {task.get('state', 'unknown')} deps={deps}"
            f"{side}{return_to} title={task.get('title', '')}"
        )
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in plan.notes)
    return "\n".join(lines)


def _fetch_issues(
    repo: str, *, limit: int, run_command: RunCommand = subprocess.run
) -> list[dict[str, Any]]:
    proc = run_command(
        [
            "gh",
            "issue",
            "list",
            "-R",
            repo,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,labels,state",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip() or "gh issue list failed")
    data = json.loads(proc.stdout or "[]")
    if not isinstance(data, list):
        raise ValueError("gh issue list returned unexpected data")
    return data


def _workflow_state(labels: list[str]) -> str | None:
    for label in labels:
        if label.startswith("state:"):
            return label.split(":", 1)[1]
    return None
