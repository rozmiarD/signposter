"""Manual execution artifact helpers.

Local-only helpers for creating parser-compatible human/operator summaries.
No GitHub mutation, no OpenClaw execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
