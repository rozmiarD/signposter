#!/usr/bin/env python3
"""Generate H052 plan + seed manifest from live GitHub issues.

One-off operator tool for test-last-know-working branch bootstrap.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAPS = REPO_ROOT / "docs" / "roadmaps"
ISSUES_JSON = Path("/tmp/h052-issues.json")
REPO = "rozmiarD/signposter"
ACTIVE_BRANCH = "test-last-know-working"

STANDARD_STOP_CONDITIONS = [
    "ruff check fails",
    "targeted pytest fails",
    "full pytest fails",
    "CI fails",
    "GitHub mutation is requested without --apply",
    "execution backend is requested without explicit execution permission",
    "PR body contains auto-close keywords",
    "merge or integration plan is not ready",
]

BODY_DIR = REPO_ROOT / "local" / "roadmaps" / "h052-bodies"
BODY_FILE_PREFIX = "local/roadmaps/h052-bodies"


def fetch_issues() -> list[dict]:
    if ISSUES_JSON.exists():
        return json.loads(ISSUES_JSON.read_text(encoding="utf-8"))
    result = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "-R",
            REPO,
            "--search",
            "H052-",
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            "number,title,body,labels,state",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def label_value(labels: list[dict], prefix: str) -> str | None:
    for label in labels:
        name = label.get("name", "")
        if name.startswith(prefix):
            return name.split(":", 1)[1]
    return None


def label_names(labels: list[dict]) -> list[str]:
    return [label["name"] for label in labels if "name" in label]


def parse_key_title(github_title: str) -> tuple[str, str] | None:
    match = KEY_RE.match(github_title.strip())
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def parse_dependencies(body: str) -> list[str]:
    deps: list[str] = []
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == "Dependencies:":
            in_section = True
            continue
        if in_section and stripped.startswith("Dependency metadata:"):
            break
        if in_section and stripped.startswith("* "):
            value = stripped[2:].strip()
            if value.lower() != "none":
                deps.append(value)
    return deps


KEY_RE = re.compile(r"^(H052-(?:\d{3}|S\d{3}))(?:\s+—\s+|\s+-\s+)(.+)$")


def parse_section_bullets(body: str, section_name: str, *, until: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    in_section = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == section_name:
            in_section = True
            continue
        if in_section and any(stripped.startswith(prefix) for prefix in until):
            break
        if in_section and stripped.startswith("* "):
            lines.append(stripped[2:].strip())
    return lines


def parse_acceptance(body: str, key: str) -> list[str]:
    for section_name in ("Acceptance criteria:", "Acceptance:"):
        lines = parse_section_bullets(
            body,
            section_name,
            until=("Validation:", "Implementation notes:", "Signposter metadata:", "Stop conditions:"),
        )
        if lines:
            return lines
    return [
        f"{key}: scoped behavior or audit result is delivered with evidence.",
        f"{key}: output is deterministic and operator-readable.",
        f"{key}: safety semantics remain unchanged unless explicitly tested.",
        f"{key}: validation command succeeds.",
    ]


def parse_stop_conditions(body: str) -> list[str]:
    lines = parse_section_bullets(
        body,
        "Stop conditions:",
        until=("Report back:", "Lifecycle boundary:", "Signposter metadata:"),
    )
    return lines or list(STANDARD_STOP_CONDITIONS)


def parse_side_task(key: str, body: str) -> tuple[bool, int | None]:
    return_to = None
    for line in body.splitlines():
        match = re.search(r"return[_ ]to:\s*#?(\d+)", line, re.IGNORECASE)
        if match:
            return_to = int(match.group(1))
    side_task = key.endswith("-S001")
    if not side_task:
        metadata = parse_section_bullets(
            body,
            "Signposter metadata:",
            until=("Lifecycle boundary:", "Stop conditions:", "Report back:"),
        )
        side_task = any("side-task" in item.lower() for item in metadata)
    return side_task, return_to


def write_issue_bodies(plan_issues: list[dict]) -> None:
    BODY_DIR.mkdir(parents=True, exist_ok=True)
    for issue in plan_issues:
        path = BODY_DIR / f"{issue['key']}.md"
        path.write_text(issue["body"], encoding="utf-8")


def sort_key(key: str) -> tuple[int, str]:
    if key.startswith("H052-S"):
        return (2, key)
    number = int(key.split("-", 1)[1])
    return (1, f"{number:03d}")


def build_plan_issues(github_issues: list[dict]) -> list[dict]:
    parsed: list[tuple[str, str, dict]] = []
    for item in github_issues:
        parsed_title = parse_key_title(item["title"])
        if parsed_title is None:
            continue
        key, short_title = parsed_title
        parsed.append((key, short_title, item))

    parsed.sort(key=lambda row: sort_key(row[0]))
    plan_issues = []
    for key, short_title, item in parsed:
        labels = item.get("labels", [])
        phase = label_value(labels, "phase:") or "build"
        risk = label_value(labels, "risk:") or "medium"
        role = label_value(labels, "role:") or "worker"
        area = label_value(labels, "area:") or "core"
        depends_on = parse_dependencies(item.get("body", ""))
        side_task, return_to = parse_side_task(key, item.get("body", ""))

        issue = {
            "key": key,
            "title": short_title,
            "body": item.get("body", ""),
            "phase": phase,
            "risk": risk,
            "role": role,
            "area": area,
            "depends_on": depends_on,
            "acceptance": parse_acceptance(item.get("body", ""), key),
            "stop_conditions": parse_stop_conditions(item.get("body", "")),
            "allowed_mutations": [],
            "status": "pending",
        }
        if side_task:
            issue["side_task"] = True
        if return_to is not None:
            issue["return_to"] = return_to
        plan_issues.append(issue)
    return plan_issues


def body_size(body: str) -> dict:
    lines = body.splitlines()
    char_count = len(body)
    line_count = len(lines)
    status = "pass"
    warnings: list[str] = []
    errors: list[str] = []
    if line_count > 165:
        status = "warn"
        warnings.append("body exceeds hard max lines")
    if char_count > 12000:
        status = "warn"
        warnings.append("body exceeds hard max chars")
    return {
        "status": status,
        "line_count": line_count,
        "char_count": char_count,
        "warnings": warnings,
        "errors": errors,
    }


def build_manifest_issues(plan_issues: list[dict], github_by_key: dict[str, dict]) -> list[dict]:
    manifest_issues = []
    for issue in plan_issues:
        gh = github_by_key[issue["key"]]
        labels = label_names(gh.get("labels", []))
        manifest_labels = [
            label
            for label in labels
            if not label.startswith("state:")
        ]
        manifest_issues.append(
            {
                "key": issue["key"],
                "title": gh["title"],
                "labels": manifest_labels,
                "depends_on": issue["depends_on"],
                "body_file": f"{BODY_FILE_PREFIX}/{issue['key']}.md",
                "body_size": body_size(gh.get("body", "")),
                "github_issue": gh["number"],
                "github_url": f"https://github.com/{REPO}/issues/{gh['number']}",
                "mainline": issue.get("mainline"),
                "parent": issue.get("parent"),
                "return_to": issue.get("return_to"),
                "side_task": bool(issue.get("side_task", False)),
            }
        )
    return manifest_issues


def retire_manifest(path: Path, *, prefix: str, superseded_by: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "retired"
    data["roadmap_scope"] = {
        "prefix": prefix,
        "active_branch": "main",
        "superseded_by": superseded_by,
        "retired_for_branch": ACTIVE_BRANCH,
        "retired_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    notes = list(data.get("operator_notes", []))
    notes.extend(
        [
            f"Retired for {ACTIVE_BRANCH}; do not pass this manifest to planner run/status.",
            f"Active manifest: {superseded_by}",
        ]
    )
    data["operator_notes"] = notes
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def refresh_dependency_metadata(manifest: dict) -> dict:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from signposter.planner import _refresh_seed_manifest_dependency_metadata

    return _refresh_seed_manifest_dependency_metadata(manifest)


def main() -> int:
    github_issues = fetch_issues()
    plan_issues = build_plan_issues(github_issues)
    if not plan_issues:
        print("No H052 issues found", file=sys.stderr)
        return 1

    github_by_key: dict[str, dict] = {}
    for item in github_issues:
        parsed_title = parse_key_title(item["title"])
        if parsed_title:
            github_by_key[parsed_title[0]] = item

    plan = {
        "version": "planner.v0.1",
        "goal": (
            "H052 - Signposter token compaction, documentation truth, lifecycle hardening, "
            "and cross-repo transition groundwork on branch test-last-know-working."
        ),
        "mode": "supervised",
        "status": "draft",
        "mutation_policy": "existing GitHub issues; manifest maps live issues only",
        "required_capabilities": [
            "planner manifest-scoped status and advancement",
            "guarded GitHub lifecycle execution",
            "bounded worker and reviewer artifacts",
            "local validation before push",
        ],
        "issues": plan_issues,
    }

    plan_path = ROADMAPS / "h052-plan.json"
    manifest_path = ROADMAPS / "h052-seed-manifest.json"
    write_issue_bodies(plan_issues)
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    manifest = refresh_dependency_metadata(
        {
            "version": "planner.seed-manifest.v0.1",
            "plan": "docs/roadmaps/h052-plan.json",
            "repo": REPO,
            "status": "applied",
            "roadmap_scope": {
                "prefix": "H052",
                "active_branch": ACTIVE_BRANCH,
                "supersedes": [
                    "docs/roadmaps/h051-seed-manifest.json",
                    "docs/roadmaps/h050-seed-manifest.json",
                ],
            },
            "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "issues": build_manifest_issues(plan_issues, github_by_key),
            "operator_notes": [
                "Generated from live GitHub issues for test-last-know-working.",
                "Pass this manifest explicitly via --manifest docs/roadmaps/h052-seed-manifest.json.",
                "Older H050/H051 manifests are retired and must be ignored by the operator loop.",
            ],
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for old_prefix, old_name in (("H050", "h050-seed-manifest.json"), ("H051", "h051-seed-manifest.json")):
        old_path = ROADMAPS / old_name
        if old_path.exists():
            retire_manifest(
                old_path,
                prefix=old_prefix,
                superseded_by="docs/roadmaps/h052-seed-manifest.json",
            )

    print(f"plan issues: {len(plan_issues)}")
    print(f"wrote body files under {BODY_DIR}")
    print(f"wrote {plan_path}")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
