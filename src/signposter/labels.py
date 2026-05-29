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

# Deterministic metadata for required labels (description, color)
LABEL_METADATA: dict[str, tuple[str, str]] = {
    "state:ready":   ("Ready to be claimed", "0E8A16"),
    "state:active":  ("Currently being worked on", "1D76DB"),
    "state:done":    ("Completed successfully", "C2E0C6"),
    "state:failed":  ("Failed", "D93F0B"),
    "state:blocked": ("Blocked - cannot proceed", "B60205"),
    "state:merged":  ("Merged/integrated into main", "5319E7"),
    "phase:build":  ("Phase: implementation/build", "1D76DB"),
    "risk:low":     ("Low risk change", "C2E0C6"),
    "role:worker":  ("Execution worker", "0052CC"),
    "area:docs":    ("Documentation changes", "0075CA"),
}


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


# =============================================================================
# H023B: Guarded label ensure (create missing required labels)
# =============================================================================

@dataclass(frozen=True)
class LabelEnsureResult:
    """Result of a guarded label ensure operation."""

    repo: str
    missing_before: list[str]
    created: list[str]
    failed: list[str]
    status: str  # "ready" | "completed" | "failed / partial"
    mode: str    # "dry_run" | "apply"
    error: str | None = None


def plan_label_ensure(repo: str) -> LabelEnsureResult:
    """Read-only plan for what ensure would do."""
    check = check_labels(repo)

    if check.error:
        return LabelEnsureResult(
            repo=repo,
            missing_before=check.missing,
            created=[],
            failed=[],
            status="failed / partial",
            mode="dry_run",
            error=check.error,
        )

    if not check.missing:
        return LabelEnsureResult(
            repo=repo,
            missing_before=[],
            created=[],
            failed=[],
            status="completed",
            mode="dry_run",
            error=None,
        )

    return LabelEnsureResult(
        repo=repo,
        missing_before=check.missing,
        created=[],
        failed=[],
        status="ready",
        mode="dry_run",
        error=None,
    )


def ensure_labels(repo: str, *, apply: bool = False) -> LabelEnsureResult:
    """Ensure all REQUIRED_LABELS exist. Dry-run by default."""
    plan = plan_label_ensure(repo)

    if not apply:
        return plan

    if plan.status != "ready":
        # Nothing to do or already failed to plan
        return LabelEnsureResult(
            repo=repo,
            missing_before=plan.missing_before,
            created=[],
            failed=[],
            status=plan.status,
            mode="apply",
            error=plan.error,
        )

    created: list[str] = []
    failed: list[str] = []

    for label in plan.missing_before:
        desc, color = LABEL_METADATA.get(label, ("Signposter workflow label", "ededed"))

        cmd = [
            "gh",
            "label",
            "create",
            label,
            "-R",
            repo,
            "--description",
            desc,
            "--color",
            color,
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()[:300]
                failed.append(f"{label}: {stderr}")
                break  # fail fast
            else:
                created.append(label)
        except Exception as e:
            failed.append(f"{label}: {str(e)[:200]}")
            break

    final_status = "completed" if not failed else "failed / partial"

    return LabelEnsureResult(
        repo=repo,
        missing_before=plan.missing_before,
        created=created,
        failed=failed,
        status=final_status,
        mode="apply",
        error=None,
    )


def format_label_ensure(result: LabelEnsureResult) -> str:
    """Compact output for ensure plan and apply results."""
    header = f"Signposter Label Ensure Plan — {result.repo}"
    if result.mode == "apply":
        header = f"Signposter Label Ensure — {result.repo}"

    lines = [f"{header}\n"]

    if result.missing_before:
        lines.append("Missing labels:")
        for m in result.missing_before:
            lines.append(f"  {m}")
    else:
        lines.append("Missing labels:")
        lines.append("  (none)")

    if result.mode == "dry_run":
        if result.status == "ready":
            lines.append("\nPlanned GitHub mutations:")
            for m in result.missing_before:
                lines.append(f"  create label: {m}")
            lines.append("\nStatus:")
            lines.append(f"  {result.status}")
            lines.append("\nNotes:")
            lines.append("  DRY RUN: no labels were created.")
            lines.append("  No labels were modified.")
            lines.append("  No labels were deleted.")
        else:
            lines.append("\nStatus:")
            lines.append(f"  {result.status}")
            lines.append("\nNotes:")
            lines.append("  No labels were created.")
            lines.append("  No labels were modified.")
            lines.append("  No labels were deleted.")
    else:
        # apply mode
        if result.created:
            lines.append("\nCreated labels:")
            for c in result.created:
                lines.append(f"  {c}")

        if result.failed:
            lines.append("\nFailed:")
            for f in result.failed:
                lines.append(f"  {f}")

        lines.append("\nStatus:")
        lines.append(f"  {result.status}")

        lines.append("\nNotes:")
        lines.append("  No existing labels were modified.")
        lines.append("  No labels were deleted.")

    return "\n".join(lines)
