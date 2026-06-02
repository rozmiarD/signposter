"""Manual execution artifact helpers.

Local-only helpers for creating parser-compatible human/operator summaries.
No GitHub mutation, no OpenClaw execution.
"""

from __future__ import annotations

import re
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
    raw_path: str | None = None
    raw_exists: bool = False
    raw_stale_signal: str | None = None
    guidance: list[str] | None = None


@dataclass(frozen=True)
class RunsArtifactAudit:
    """Read-only audit result for local run artifact naming and pairing."""

    runs_dir: str
    exists: bool
    status: str
    total_files: int
    summary_count: int
    raw_count: int
    canonical_pairs: int
    diagnostic_pairs: int
    summary_without_raw: tuple[str, ...]
    raw_without_summary: tuple[str, ...]
    unknown_names: tuple[str, ...]
    unsafe_markers: tuple[str, ...]
    limit: int


_RUN_ARTIFACT_RE = re.compile(
    r"^(?P<target>(?:issue-\d+-(?:worker|reviewer|gate)|pr-\d+-reviewer))"
    r"(?P<variant>(?:\.[A-Za-z0-9_-]+)*)"
    r"\.(?P<kind>summary\.md|raw\.txt)$"
)


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


def audit_run_artifacts(
    *,
    runs_dir: str | Path = "artifacts/runs",
    limit: int = 8,
) -> RunsArtifactAudit:
    """Audit local run artifact names and raw/summary pair retention.

    This is intentionally read-only. It identifies naming and pairing gaps but
    never deletes or rewrites local evidence.
    """
    root = Path(runs_dir)
    bounded_limit = max(0, limit)
    if not root.is_dir():
        return RunsArtifactAudit(
            runs_dir=str(root),
            exists=False,
            status="blocked",
            total_files=0,
            summary_count=0,
            raw_count=0,
            canonical_pairs=0,
            diagnostic_pairs=0,
            summary_without_raw=(),
            raw_without_summary=(),
            unknown_names=(),
            unsafe_markers=(),
            limit=bounded_limit,
        )

    files = sorted(path for path in root.iterdir() if path.is_file())
    grouped: dict[str, set[str]] = {}
    canonical_pairs = 0
    diagnostic_pairs = 0
    unknown_names: list[str] = []
    unsafe_markers: list[str] = []
    summary_count = 0
    raw_count = 0

    for path in files:
        name = path.name
        match = _RUN_ARTIFACT_RE.match(name)
        if not match:
            unknown_names.append(name)
            continue

        kind = match.group("kind")
        variant = match.group("variant")
        stem = name.removesuffix(".summary.md").removesuffix(".raw.txt")
        grouped.setdefault(stem, set()).add(kind)
        if kind == "summary.md":
            summary_count += 1
        else:
            raw_count += 1

        signal = _read_artifact_safety_signal(path)
        if signal:
            unsafe_markers.append(f"{name}: {signal}")

        if {"summary.md", "raw.txt"} <= grouped[stem]:
            if variant:
                diagnostic_pairs += 1
            else:
                canonical_pairs += 1

    summary_without_raw: list[str] = []
    raw_without_summary: list[str] = []
    for stem, kinds in sorted(grouped.items()):
        if "summary.md" in kinds and "raw.txt" not in kinds:
            summary_without_raw.append(f"{stem}.summary.md")
        if "raw.txt" in kinds and "summary.md" not in kinds:
            raw_without_summary.append(f"{stem}.raw.txt")

    return RunsArtifactAudit(
        runs_dir=str(root),
        exists=True,
        status="ready",
        total_files=len(files),
        summary_count=summary_count,
        raw_count=raw_count,
        canonical_pairs=canonical_pairs,
        diagnostic_pairs=diagnostic_pairs,
        summary_without_raw=tuple(summary_without_raw[:bounded_limit]),
        raw_without_summary=tuple(raw_without_summary[:bounded_limit]),
        unknown_names=tuple(unknown_names[:bounded_limit]),
        unsafe_markers=tuple(unsafe_markers[:bounded_limit]),
        limit=bounded_limit,
    )


def _read_artifact_safety_signal(path: Path) -> str | None:
    try:
        return find_stale_or_failover_signal(
            path.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return "unreadable artifact"


def format_run_artifact_audit(result: RunsArtifactAudit) -> str:
    """Render a compact read-only run artifact audit."""
    lines = [
        "Signposter Run Artifact Audit",
        "",
        "Runs:",
        f"  path: {result.runs_dir}",
        f"  exists: {'yes' if result.exists else 'no'}",
        "",
        "Status:",
        f"  {result.status}",
    ]
    if not result.exists:
        lines.extend(
            [
                "",
                "Reason:",
                "  runs directory is missing or is not a directory",
                "",
                "Notes:",
                "  No GitHub mutation was performed.",
                "  No OpenClaw execution was performed.",
                "  No local artifact was modified.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "",
            "Counts:",
            f"  files: {result.total_files}",
            f"  summaries: {result.summary_count}",
            f"  raw outputs: {result.raw_count}",
            f"  canonical raw/summary pairs: {result.canonical_pairs}",
            f"  retained diagnostic raw/summary pairs: {result.diagnostic_pairs}",
            "",
            "Findings:",
            f"  summary without raw: {len(result.summary_without_raw)} shown",
            f"  raw without summary: {len(result.raw_without_summary)} shown",
            f"  unknown names: {len(result.unknown_names)} shown",
            f"  unsafe markers: {len(result.unsafe_markers)} shown",
        ]
    )
    _append_limited_examples(lines, "Summary without raw", result.summary_without_raw)
    _append_limited_examples(lines, "Raw without summary", result.raw_without_summary)
    _append_limited_examples(lines, "Unknown names", result.unknown_names)
    _append_limited_examples(lines, "Unsafe markers", result.unsafe_markers)
    lines.extend(
        [
            "",
            "Retention:",
            "  active evidence uses canonical issue-N-worker/pr-N-reviewer raw+summary pairs",
            "  diagnostic suffixes such as .codex-runtime.* are retained local evidence",
            "  this audit does not delete, rename, upload, or rewrite artifacts",
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No local artifact was modified.",
        ]
    )
    return "\n".join(lines)


def _append_limited_examples(lines: list[str], heading: str, values: tuple[str, ...]) -> None:
    if not values:
        return
    lines.extend(["", f"{heading}:"])
    lines.extend(f"  - {value}" for value in values)


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
            raw_path=str(_worker_raw_path_for_summary(path)),
            raw_exists=False,
            raw_stale_signal=None,
            guidance=[
                "write a parser-compatible worker summary before gate or complete",
                "keep raw backend output local when backend execution produced one",
            ],
        )

    text = path.read_text(encoding="utf-8")
    stale_signal = find_stale_or_failover_signal(text)
    missing = _missing_worker_summary_fields(text)
    raw_path = _worker_raw_path_for_summary(path)
    raw_exists = raw_path.exists()
    raw_stale_signal = (
        find_stale_or_failover_signal(raw_path.read_text(encoding="utf-8"))
        if raw_exists
        else None
    )
    status = (
        "pass"
        if not missing and stale_signal is None and raw_stale_signal is None
        else "blocked"
    )

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
        raw_path=str(raw_path),
        raw_exists=raw_exists,
        raw_stale_signal=raw_stale_signal,
        guidance=_worker_artifact_guidance(
            missing=missing,
            summary_signal=stale_signal,
            raw_signal=raw_stale_signal,
            raw_exists=raw_exists,
        ),
    )


def _worker_raw_path_for_summary(summary_path: Path) -> Path:
    name = summary_path.name
    if name.endswith(".summary.md"):
        return summary_path.with_name(name.removesuffix(".summary.md") + ".raw.txt")
    return summary_path.with_name(f"issue-{summary_path.stem}-worker.raw.txt")


def _worker_artifact_guidance(
    *,
    missing: list[str],
    summary_signal: str | None,
    raw_signal: str | None,
    raw_exists: bool,
) -> list[str]:
    guidance: list[str] = []
    if missing:
        guidance.append("repair worker summary fields before gate or complete")
    if summary_signal or raw_signal:
        guidance.append(
            "preserve unsafe backend output separately and provide clean manual evidence"
        )
    if not raw_exists:
        guidance.append("raw output artifact not found; keep raw local for backend runs")
    if not guidance:
        guidance.append("worker artifact contract is ready for gate and complete")
    return guidance


def _missing_worker_summary_fields(text: str) -> list[str]:
    lowered = text.lower()
    required = {
        "repository": "**repository:**",
        "issue": "**issue:** #",
        "agent": "**agent:**",
        "exit code": "**exit code:** 0",
        "dirty guard": "**dirty guard:** clean",
        "task execution complete": "**task execution complete:** yes",
        "acceptance": "**acceptance:** pass",
        "scoped completion evidence": "scoped completion evidence",
        "files changed": "## files changed",
        "implemented behavior": "## implemented behavior",
        "validation evidence": "validation evidence",
        "targeted validation": "targeted validation passed",
        "full validation": "full validation passed",
        "safety section": "## safety",
        "no github mutation safety note": "no github mutation was performed",
        "no openclaw execution safety note": "no openclaw execution was performed",
        "no issue close safety note": "no issue was closed",
        "no merge safety note": "no merge was performed",
        "no unrelated files safety note": "no unrelated files were changed",
        "gate recommendation": "## gate recommendation",
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
        lines.extend(["", "Summary unsafe marker:", f"  {result.stale_signal}"])
    if result.raw_path:
        lines.extend(
            [
                "",
                "Raw artifact:",
                f"  path: {result.raw_path}",
                f"  exists: {'yes' if result.raw_exists else 'no'}",
            ]
        )
    if result.raw_stale_signal:
        lines.extend(["", "Raw unsafe marker:", f"  {result.raw_stale_signal}"])
    lines.extend(["", "Missing:"])
    if result.missing:
        lines.extend(f"  - {item}" for item in result.missing)
    else:
        lines.append("  none")
    guidance = result.guidance or []
    if guidance:
        lines.extend(["", "Guidance:"])
        lines.extend(f"  - {item}" for item in guidance)
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)
