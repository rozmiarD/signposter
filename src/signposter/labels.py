"""Repository label preflight check (HARDENING-023A).

Read-only only. Centralized required label list.
No label creation or mutation of any kind.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

# Centralized required Signposter workflow labels.
# Keep this list in one place for all future label-related logic.
REQUIRED_LABELS: tuple[str, ...] = (
    "state:ready",
    "state:active",
    "state:done",
    "state:failed",
    "state:blocked",
    "state:merged",
    "phase:build",
    "risk:low",
    "role:worker",
    "area:docs",
)


@dataclass(frozen=True)
class LabelCheckResult:
    """Result of a read-only label preflight check."""

    repo: str
    present: list[str]
    missing: list[str]
    status: str  # "pass" | "blocked — required labels missing" | "blocked — ..."
    error: str | None = None  # bounded error message on gh failure


def _fetch_repo_labels(repo: str) -> tuple[list[str], str | None]:
    """Fetch label names using gh CLI (read-only). Returns (labels, error)."""
    try:
        result = subprocess.run(
            [
                "gh",
                "label",
                "list",
                "-R",
                repo,
                "--limit",
                "200",
                "--json",
                "name",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:250]
            msg = f"gh label list failed: {stderr}"
            return [], msg[:300]  # final bounded error

        import json

        data = json.loads(result.stdout or "[]")
        names = [item.get("name") for item in data if isinstance(item, dict)]
        return [n for n in names if isinstance(n, str)], None

    except Exception as e:
        return [], f"failed to fetch labels: {str(e)[:200]}"


def check_labels(repo: str) -> LabelCheckResult:
    """Perform read-only label preflight check."""
    if not repo or "/" not in repo:
        return LabelCheckResult(
            repo=repo or "",
            present=[],
            missing=list(REQUIRED_LABELS),
            status="blocked — invalid repo format (expected owner/name)",
            error="invalid repo",
        )

    existing_labels, fetch_error = _fetch_repo_labels(repo)

    if fetch_error:
        return LabelCheckResult(
            repo=repo,
            present=[],
            missing=list(REQUIRED_LABELS),
            status="blocked — failed to fetch labels",
            error=fetch_error,
        )

    present: list[str] = []
    missing: list[str] = []

    existing_set = set(existing_labels)
    for label in REQUIRED_LABELS:
        if label in existing_set:
            present.append(label)
        else:
            missing.append(label)

    if missing:
        status = "blocked — required labels missing"
    else:
        status = "pass"

    return LabelCheckResult(
        repo=repo,
        present=present,
        missing=missing,
        status=status,
        error=None,
    )


def format_label_check(result: LabelCheckResult) -> str:
    """Compact deterministic output for `labels check`."""
    lines = [f"Signposter Label Check — {result.repo}\n"]

    if result.error and "invalid" in (result.error or ""):
        lines.append("Status:")
        lines.append(f"  {result.status}")
        lines.append("\nNotes:")
        lines.append("  Read-only label check only.")
        lines.append("  No labels were created.")
        lines.append("  No labels were modified.")
        lines.append("  No GitHub mutation was performed.")
        return "\n".join(lines)

    if result.missing:
        lines.append("Missing labels:")
        for label in result.missing:
            lines.append(f"  {label}")
    else:
        lines.append("Required labels:")
        for label in REQUIRED_LABELS:
            lines.append(f"  {label}: present")

    lines.append("\nStatus:")
    lines.append(f"  {result.status}")

    lines.append("\nNotes:")
    lines.append("  Read-only label check only.")
    lines.append("  No labels were created.")
    lines.append("  No labels were modified.")
    lines.append("  No GitHub mutation was performed.")

    return "\n".join(lines)
