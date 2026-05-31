"""Manual execution artifact helpers.

Local-only helpers for creating parser-compatible human/operator summaries.
No GitHub mutation, no OpenClaw execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from signposter.artifact_safety import find_stale_or_failover_signal

DEFAULT_FULL_VALIDATION = [
    "ruff check .",
    "python -m pytest tests/ -q",
]


@dataclass(frozen=True)
class ManualArtifactPlan:
    artifact_type: str
    target: str
    path: str
    content: str
    status: str
    notes: list[str]


@dataclass(frozen=True)
class WorkerArtifactValidation:
    """Read-only validation result for a local worker summary artifact."""

    issue: int
    path: str
    exists: bool
    status: str
    missing: list[str]
    stale_signal: str | None
    notes: list[str]


def build_worker_summary(
    *,
    repo: str,
    issue: int,
    agent: str = "human/operator",
    changed_files: list[str] | None = None,
    implemented_behavior: list[str] | None = None,
    targeted_validation: list[str] | None = None,
    full_validation: list[str] | None = None,
    manual_smoke: list[str] | None = None,
) -> str:
    """Build a gate-friendly manual worker summary."""
    changed_files = changed_files or ["src/signposter/<file>.py", "tests/test_<file>.py"]
    implemented_behavior = implemented_behavior or [
        "Scoped behavior was implemented and verified.",
    ]
    targeted_validation = targeted_validation or [
        "ruff check <changed-files>",
        "python -m pytest <targeted-tests> -q",
    ]
    full_validation = full_validation or DEFAULT_FULL_VALIDATION
    manual_smoke = manual_smoke or ["Manual CLI smoke passed."]

    lines = [
        "# Signposter Execution Summary",
        "",
        f"**Repository:** {repo}",
        f"**Issue:** #{issue}",
        f"**Agent:** {agent}",
        "**Exit Code:** 0",
        "**Dirty Guard:** clean",
        "**Task execution complete:** yes",
        "**Acceptance:** pass",
        "",
        "## Scoped completion evidence",
        "",
        "PASS - scoped worker task completed with validation evidence.",
        "",
        "## Files changed",
        "",
    ]
    lines.extend(f"- `{path}`" for path in changed_files)
    lines.extend(
        [
            "",
            "## Implemented behavior / verified behavior",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in implemented_behavior)
    lines.extend(
        [
            "",
            "## Validation evidence",
            "",
            "Targeted validation passed:",
            "",
        ]
    )
    lines.extend(f"- `{cmd}`" for cmd in targeted_validation)
    lines.extend(["", "Full validation passed:", ""])
    lines.extend(f"- `{cmd}`" for cmd in full_validation)
    lines.extend(["", "Manual CLI smoke passed:", ""])
    lines.extend(f"- `{cmd}`" for cmd in manual_smoke)
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "No GitHub mutation was performed by the implemented code.",
            "No OpenClaw execution was performed by the implemented code.",
            "No manifest mutation was performed.",
            "No issue was closed by the implemented code.",
            "No merge was performed by the implemented code.",
            "No unrelated files were changed.",
            "",
            "## Gate recommendation",
            "",
            "PASS - scoped worker task completed with validation evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_review_summary(
    *,
    pr: int,
    agent: str = "human/operator",
    verdict: str = "APPROVE",
    confidence: float = 0.90,
    risk: str = "high",
    findings: list[str] | None = None,
    reasoning: str | None = None,
) -> str:
    """Build a parser-compatible manual reviewer summary."""
    findings = findings or [
        "Scoped change reviewed against the requested task.",
        "Validation evidence was considered.",
        "No merge or issue close is implied by this artifact.",
    ]
    reasoning = reasoning or (
        "The change is acceptable because scope, validation, and safety evidence "
        "match the requested task."
    )

    lines = [
        "# Signposter Reviewer Summary",
        "",
        f"Agent: {agent}",
        f"PR: #{pr}",
        f"Verdict: {verdict}",
        f"Confidence: {confidence:.2f}",
        f"Risk: {risk}",
        "Scope match: yes",
        "CI considered: yes",
        "Merge recommendation: yes",
        "Automerge eligible: no",
        "Findings:",
    ]
    lines.extend(f"- {finding}" for finding in findings)
    lines.extend(
        [
            "Reasoning summary:",
            reasoning,
            "",
            "## Validation considered",
            "",
            "- targeted validation passed",
            "- full validation passed",
            "- PR CI green",
            "",
            "## Safety notes",
            "",
            "No GitHub review was submitted by this artifact.",
            "No PR approval was submitted by this artifact.",
            "No merge was performed.",
            "No issue was closed.",
        ]
    )
    return "\n".join(lines) + "\n"


def plan_worker_summary(
    *,
    repo: str,
    issue: int,
    agent: str = "human/operator",
    changed_files: list[str] | None = None,
    implemented_behavior: list[str] | None = None,
    targeted_validation: list[str] | None = None,
    full_validation: list[str] | None = None,
    manual_smoke: list[str] | None = None,
    runs_dir: str | Path = "artifacts/runs",
) -> ManualArtifactPlan:
    path = Path(runs_dir) / f"issue-{issue}-worker.summary.md"
    content = build_worker_summary(
        repo=repo,
        issue=issue,
        agent=agent,
        changed_files=changed_files,
        implemented_behavior=implemented_behavior,
        targeted_validation=targeted_validation,
        full_validation=full_validation,
        manual_smoke=manual_smoke,
    )
    return ManualArtifactPlan(
        artifact_type="worker-summary",
        target=f"issue #{issue}",
        path=str(path),
        content=content,
        status="ready",
        notes=[
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
            "No issue was closed.",
        ],
    )


def plan_review_summary(
    *,
    pr: int,
    agent: str = "human/operator",
    verdict: str = "APPROVE",
    confidence: float = 0.90,
    risk: str = "high",
    findings: list[str] | None = None,
    reasoning: str | None = None,
    runs_dir: str | Path = "artifacts/runs",
) -> ManualArtifactPlan:
    path = Path(runs_dir) / f"pr-{pr}-reviewer.summary.md"
    content = build_review_summary(
        pr=pr,
        agent=agent,
        verdict=verdict,
        confidence=confidence,
        risk=risk,
        findings=findings,
        reasoning=reasoning,
    )
    return ManualArtifactPlan(
        artifact_type="review-summary",
        target=f"PR #{pr}",
        path=str(path),
        content=content,
        status="ready",
        notes=[
            "No GitHub review was submitted.",
            "No PR approval was submitted.",
            "No merge was performed.",
            "No issue was closed.",
        ],
    )


def write_manual_artifact(plan: ManualArtifactPlan, *, apply: bool = False) -> bool:
    """Write the planned artifact only when apply=True."""
    if not apply:
        return False
    path = Path(plan.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.content, encoding="utf-8")
    return True


def format_manual_artifact_plan(plan: ManualArtifactPlan, *, apply: bool = False) -> str:
    """Format a deterministic dry-run/apply summary."""
    lines = [f"Signposter Manual Artifact — {plan.artifact_type}\n"]
    lines.append("Target:")
    lines.append(f"  {plan.target}")
    lines.append("")
    lines.append("Artifact:")
    lines.append(f"  path: {plan.path}")
    lines.append(f"  status: {'written' if apply else 'planned'}")
    lines.append("")
    lines.append("Preview:")
    lines.append("```")
    lines.extend(plan.content.splitlines()[:40])
    if len(plan.content.splitlines()) > 40:
        lines.append("... (truncated)")
    lines.append("```")
    lines.append("")
    lines.append("Status:")
    lines.append(f"  {'completed' if apply else plan.status}")
    lines.append("")
    lines.append("Notes:")
    for note in plan.notes:
        lines.append(f"  {note}")
    if not apply:
        lines.append("  Dry-run only. Use --apply to write the artifact.")
    return "\n".join(lines)


def validate_worker_summary_artifact(
    issue: int,
    *,
    summary_path: str | Path | None = None,
    runs_dir: str | Path = "artifacts/runs",
) -> WorkerArtifactValidation:
    """Validate the local worker summary contract without mutating anything."""
    path = (
        Path(summary_path)
        if summary_path
        else Path(runs_dir) / f"issue-{issue}-worker.summary.md"
    )
    if not path.exists():
        return WorkerArtifactValidation(
            issue=issue,
            path=str(path),
            exists=False,
            status="missing",
            missing=["summary artifact"],
            stale_signal=None,
            notes=[
                "Read-only validation.",
                "No GitHub mutation was performed.",
                "No OpenClaw execution was performed.",
            ],
        )

    text = path.read_text(encoding="utf-8")
    stale_signal = find_stale_or_failover_signal(text)
    missing = _missing_worker_summary_fields(text)
    status = "pass" if not missing and stale_signal is None else "blocked"

    return WorkerArtifactValidation(
        issue=issue,
        path=str(path),
        exists=True,
        status=status,
        missing=missing,
        stale_signal=stale_signal,
        notes=[
            "Read-only validation.",
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
        ],
    )


def _missing_worker_summary_fields(text: str) -> list[str]:
    lowered = text.lower()
    required = {
        "exit code": "**exit code:** 0",
        "acceptance": "**acceptance:** pass",
        "scoped completion evidence": "scoped completion evidence",
        "validation evidence": "validation evidence",
        "targeted validation": "targeted validation passed",
        "full validation": "full validation passed",
        "safety section": "## safety",
        "no github mutation safety note": "no github mutation was performed",
        "no unrelated files safety note": "no unrelated files were changed",
    }
    return [name for name, needle in required.items() if needle not in lowered]


def format_worker_artifact_validation(result: WorkerArtifactValidation) -> str:
    """Render compact worker artifact validation output."""
    lines = [
        f"Signposter Worker Artifact Validation — Issue #{result.issue}",
        "",
        "Artifact:",
        f"  path: {result.path}",
        f"  exists: {'yes' if result.exists else 'no'}",
        "",
        "Status:",
        f"  {result.status}",
    ]
    if result.stale_signal:
        lines.extend(["", "Unsafe marker:", f"  {result.stale_signal}"])
    lines.extend(["", "Missing:"])
    if result.missing:
        lines.extend(f"  - {item}" for item in result.missing)
    else:
        lines.append("  none")
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)
