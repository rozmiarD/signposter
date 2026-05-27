"""Signposter GitHub scanner (read-only).

Inspects a GitHub repository for workflow-relevant items using neutral labels.
Uses GitHub CLI (`gh`) in read-only mode. No mutations of any kind.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

# Neutral labels that indicate a work item is potentially ready for processing
CANDIDATE_LABELS = frozenset(
    {
        "state:ready",
        "state:active",
        "phase:plan",
        "phase:build",
        "phase:review",
        "gate:ci",
        "gate:review",
        "gate:human",
    }
)


@dataclass(frozen=True)
class LabeledItem:
    """Represents an Issue or Pull Request with its labels."""

    number: int
    title: str
    html_url: str
    labels: list[str]
    item_type: str  # "issue" or "pr"


@dataclass(frozen=True)
class WorkflowRun:
    """Represents a recent GitHub Actions workflow run."""

    id: int
    name: str
    status: str
    conclusion: str | None
    head_branch: str
    updated_at: str
    url: str





def fetch_open_issues(repo: str, limit: int = 50) -> list[LabeledItem]:
    """Fetch open issues using repository-scoped gh issue list (works for private repos)."""
    result = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "-R",
            repo,
            "--state",
            "open",
            "--json",
            "number,title,url,labels,state,updatedAt",
            "--limit",
            str(limit),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list issues: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    items = []
    for item in data:
        labels = [lbl["name"] for lbl in item.get("labels", [])]
        items.append(
            LabeledItem(
                number=item["number"],
                title=item["title"],
                html_url=item.get("url", ""),
                labels=labels,
                item_type="issue",
            )
        )
    return items


def fetch_issue_by_number(repo: str, number: int) -> LabeledItem | None:
    """Fetch a single issue by number (read-only).

    Always returns the *current* labels from GitHub, regardless of state.
    Useful after claim mutations to get fresh state for prompt rendering.
    """
    result = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "-R",
            repo,
            "--json",
            "number,title,url,labels,state",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        # Issue may not exist or be inaccessible
        return None

    data = json.loads(result.stdout)
    labels = [lbl["name"] for lbl in data.get("labels", [])]
    return LabeledItem(
        number=data["number"],
        title=data["title"],
        html_url=data.get("url", ""),
        labels=labels,
        item_type="issue",
    )


def fetch_open_prs(repo: str, limit: int = 50) -> list[LabeledItem]:
    """Fetch open pull requests using repository-scoped gh pr list (works for private repos)."""
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "-R",
            repo,
            "--state",
            "open",
            "--json",
            "number,title,url,labels,state,updatedAt,isDraft,headRefName,baseRefName",
            "--limit",
            str(limit),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list PRs: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    items = []
    for item in data:
        labels = [lbl["name"] for lbl in item.get("labels", [])]
        items.append(
            LabeledItem(
                number=item["number"],
                title=item["title"],
                html_url=item.get("url", ""),
                labels=labels,
                item_type="pr",
            )
        )
    return items


def fetch_recent_workflow_runs(repo: str, limit: int = 10) -> list[WorkflowRun]:
    """Fetch recent workflow runs using repository-scoped gh run list."""
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "-R",
            repo,
            "--limit",
            str(limit),
            "--json",
            "databaseId,status,conclusion,workflowName,headBranch,displayTitle,createdAt,updatedAt,url",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch workflow runs: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    runs = []
    for run in data:
        runs.append(
            WorkflowRun(
                id=run["databaseId"],
                name=run.get("workflowName", run.get("displayTitle", "unknown")),
                status=run.get("status", "unknown"),
                conclusion=run.get("conclusion"),
                head_branch=run.get("headBranch", ""),
                updated_at=run.get("updatedAt", ""),
                url=run.get("url", ""),
            )
        )
    return runs


def find_candidates(items: list[LabeledItem]) -> list[LabeledItem]:
    """Return items that have at least one candidate label."""
    candidates = []
    for item in items:
        if any(label in CANDIDATE_LABELS for label in item.labels):
            candidates.append(item)
    return candidates


def run_scan(repo: str) -> dict[str, Any]:
    """Perform a full read-only scan of the repository."""
    if not repo or "/" not in repo:
        raise ValueError("Repository must be in 'owner/repo' format")

    issues = fetch_open_issues(repo)
    prs = fetch_open_prs(repo)
    runs = fetch_recent_workflow_runs(repo)

    all_items = issues + prs
    candidates = find_candidates(all_items)

    return {
        "repo": repo,
        "open_issues": len(issues),
        "open_prs": len(prs),
        "recent_runs": len(runs),
        "candidates": candidates,
        "issues": issues,
        "prs": prs,
        "runs": runs,
    }


def format_scan_report(scan_result: dict[str, Any]) -> str:
    """Produce a human-readable report from scan results."""
    repo = scan_result["repo"]
    lines = [f"Signposter Scan Report — {repo}\n"]

    lines.append(f"Open Issues: {scan_result['open_issues']}")
    lines.append(f"Open PRs:    {scan_result['open_prs']}")
    lines.append(f"Recent Runs: {scan_result['recent_runs']}")
    lines.append("")

    candidates = scan_result["candidates"]
    if candidates:
        lines.append(f"Candidate Items ({len(candidates)}):")
        for item in candidates:
            label_str = ", ".join(item.labels)
            lines.append(f"  [{item.item_type.upper()}] #{item.number} — {item.title}")
            lines.append(f"    Labels: {label_str}")
            lines.append(f"    {item.html_url}")
            lines.append("")
    else:
        lines.append("No candidate items found with workflow labels.")

    # Show recent runs summary
    runs = scan_result.get("runs", [])
    if runs:
        lines.append("Recent Workflow Runs:")
        for run in runs[:5]:
            conclusion = run.conclusion or run.status
            lines.append(f"  #{run.id} [{run.head_branch}] {run.name} — {conclusion}")

    return "\n".join(lines)


def cli_main(repo: str) -> int:
    """Entry point when called from the parent CLI."""
    try:
        result = run_scan(repo)
        print(format_scan_report(result))
        return 0
    except Exception as e:
        print(f"Scan failed: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Direct CLI entry point (used when running the module directly)."""
    import argparse

    parser = argparse.ArgumentParser(description="Read-only GitHub repository scanner")
    parser.add_argument(
        "--repo",
        required=True,
        help="Target repository in owner/repo format (e.g. ExatronOmega/signposter)",
    )
    args = parser.parse_args()

    return cli_main(args.repo)


if __name__ == "__main__":
    sys.exit(main())
