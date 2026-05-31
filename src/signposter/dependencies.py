"""Dependency parsing and computed blocked status for Signposter.

Dependencies are declared in issue bodies using:

    Depends-On: #3, #7

or one per line:

    Depends-On: #3
    Depends-On: #7

Dependency status is always *computed* from the current state of the
referenced issues. No dependency labels or `state:blocked` are stored.
"""

from __future__ import annotations

import json
import re
import subprocess

# Robust line-based parser for Depends-On declarations.
# Handles both comma lists and one-per-line styles.
_DEPENDS_ON_RE = re.compile(r"#(\d+)")
COMPLETED_DEPENDENCY_STATES = {"done", "merged"}


def parse_depends_on(body: str | None) -> list[int]:
    """Extract unique dependency issue numbers from issue body.

    Supports:
        Depends-On: #3, #7
        Depends-On: #3
        Depends-On: #7

    Only numbers appearing on lines containing "Depends-On" are considered.
    """
    if not body:
        return []

    deps: set[int] = set()
    for line in body.splitlines():
        if "depends-on" in line.lower():
            for m in _DEPENDS_ON_RE.finditer(line):
                deps.add(int(m.group(1)))

    return sorted(deps)


def fetch_issue_state_label(repo: str, number: int) -> str | None:
    """Return the workflow state label (e.g. 'done', 'active') for an issue, or None."""
    result = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "-R",
            repo,
            "--json",
            "labels",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
        labels = [lbl["name"] for lbl in data.get("labels", [])]
    except Exception:
        return None

    for label in labels:
        if label.startswith("state:"):
            return label.split(":", 1)[1]
    return None


def get_dependency_block_reason(
    repo: str, depends_on: list[int]
) -> tuple[bool, str]:
    """Determine if the current issue is blocked by its dependencies.

    Returns (is_blocked, compact_reason)
    """
    if not depends_on:
        return False, "no dependencies"

    blockers: list[str] = []

    for dep_num in depends_on:
        state = fetch_issue_state_label(repo, dep_num)
        if state in COMPLETED_DEPENDENCY_STATES:
            continue  # good, not a blocker

        if state is None:
            blockers.append(f"#{dep_num} → missing/unknown")
        else:
            blockers.append(f"#{dep_num} → state:{state}")

    if blockers:
        reason = ", ".join(blockers)
        return True, f"blocked by {reason}"

    return False, "all dependencies complete"


def is_dependency_blocked(
    repo: str, body: str | None, depends_on: list[int] | None = None
) -> tuple[bool, str]:
    """High-level helper: given body (or pre-parsed deps), return block status."""
    if depends_on is None:
        depends_on = parse_depends_on(body)

    if not depends_on:
        return False, "no dependencies declared"

    return get_dependency_block_reason(repo, depends_on)
