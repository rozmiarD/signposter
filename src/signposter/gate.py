"""Gate decision logic for Signposter.

Conservative, evidence-based gate evaluation for review gates.
All functions are pure where possible for easy testing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signposter.artifact import WorkerArtifactValidation, validate_worker_summary_artifact
from signposter.artifact_safety import find_stale_or_failover_signal


@dataclass
class GateDecision:
    decision: str  # "pass", "needs-work", or "fail"
    reason: str
    confidence: str  # "high", "medium", "low"
    proposed_transition: str | None
    proposed_command: str | None


@dataclass(frozen=True)
class GateHeuristicAudit:
    """Read-only map of gate evidence heuristics and known risk areas."""

    status: str
    gate_surfaces: tuple[str, ...]
    structural_evidence: tuple[str, ...]
    phrase_matchers: tuple[str, ...]
    false_positive_risks: tuple[str, ...]
    false_negative_risks: tuple[str, ...]
    recommendations: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class WorkerDisqualifierContext:
    """Structured location for a worker disqualifier phrase."""

    signal: str
    section: str
    line: str


def build_gate_heuristic_audit() -> GateHeuristicAudit:
    """Return a compact static audit of current gate heuristics.

    This deliberately does not evaluate a specific issue or artifact. It gives
    operators a deterministic map of where Signposter uses structured evidence
    versus phrase matching so follow-up hardening can be scoped precisely.
    """
    return GateHeuristicAudit(
        status="ready",
        gate_surfaces=(
            "review gate: evaluate_gate uses positive/negative reviewer phrases",
            "ci gate: evaluate_ci_gate uses worker artifact preflight plus scoped evidence",
            "human gate: evaluate_human_gate requires approval, scope, validation, safety",
            "no-op completion: evaluated as a structured CI-gate evidence path",
        ),
        structural_evidence=(
            "worker summary schema is validated before CI-gate evaluation",
            "stale/failover artifact markers block before scoped evidence checks",
            "actual Python exception output requires traceback framing, not the word alone",
            "worker disqualifier phrases are ignored only in neutral policy/audit context",
            "worker disqualifier reasons include bounded section and line context",
            "scoped code/test/no-op paths require validation and safety statements",
            "validated no-op completion requires explicit unchanged-tree evidence",
            "human gate structured fields: Human approval, Scope reviewed/match, "
            "Validation status/local validation",
            "human gate structured safety fields: GitHub mutation, Execution backend, "
            "Issue closure, Merge performed",
        ),
        phrase_matchers=(
            "review gate blocks on phrases such as critical blocker and missing evidence",
            "ci gate blocks on critical blocker, execution failed, and contextual error:",
            "human gate blocks on phrases such as approval denied and validation failed",
            "human gate legacy positive fallback requires approval wording plus proceed wording",
            "positive review fallback still requires multiple positive reviewer phrases",
        ),
        false_positive_risks=(
            "generic blocker words outside neutral audit/implementation context still block",
            "non-error blocker phrases still need section-aware failure context",
            "test-only disqualifiers include broad traceback wording rather than framed output",
        ),
        false_negative_risks=(
            "well-formed but semantically weak manual summaries can pass schema preflight",
            "phrase-based review positives may miss valid approvals with different wording",
            "structured scoped evidence is strong but still text-based rather than section parsed",
            "human gate legacy phrase fallback remains less precise than structured fields",
        ),
        recommendations=(
            "prefer section-aware parsing for safety/result/failure sections",
            "keep actual Python exception detection framed around traceback structure",
            "move broad phrase blockers behind explicit failure-context checks",
            "prefer structured human gate fields over legacy approval/proceed phrases",
            "preserve conservative default when evidence is unclear",
        ),
        notes=(
            "Read-only heuristic audit.",
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
            "No issue was closed.",
        ),
    )


def format_gate_heuristic_audit(audit: GateHeuristicAudit) -> str:
    """Render a deterministic gate heuristic audit."""
    lines = [
        "Signposter Gate Heuristic Audit",
        "",
        "Status:",
        f"  {audit.status}",
        "",
        "Gate surfaces:",
    ]
    _append_audit_lines(lines, audit.gate_surfaces)
    lines.extend(["", "Structural evidence:"])
    _append_audit_lines(lines, audit.structural_evidence)
    lines.extend(["", "Phrase matchers:"])
    _append_audit_lines(lines, audit.phrase_matchers)
    lines.extend(["", "False-positive risks:"])
    _append_audit_lines(lines, audit.false_positive_risks)
    lines.extend(["", "False-negative risks:"])
    _append_audit_lines(lines, audit.false_negative_risks)
    lines.extend(["", "Recommendations:"])
    _append_audit_lines(lines, audit.recommendations)
    lines.extend(["", "Notes:"])
    _append_audit_lines(lines, audit.notes, prefix="  ")
    return "\n".join(lines)


def _append_audit_lines(
    lines: list[str],
    values: tuple[str, ...],
    *,
    prefix: str = "  - ",
) -> None:
    if not values:
        lines.append(f"{prefix}none")
        return
    lines.extend(f"{prefix}{value}" for value in values)


def evaluate_gate(
    exit_code: int,
    summary_text: str,
    raw_text: str | None = None,
) -> GateDecision:
    """Pure decision function based on exit code and text content.

    Conservative heuristic for BOOTSTRAP-022.
    """
    text = (summary_text or "") + "\n" + (raw_text or "")

    if exit_code != 0:
        return GateDecision(
            decision="fail",
            reason=f"Non-zero exit code from reviewer: {exit_code}",
            confidence="high",
            proposed_transition="state:failed",
            proposed_command=None,
        )

    # Look for strong negative signals
    negative_signals = [
        "critical blocker",
        "cannot proceed",
        "missing required evidence",
        "evidence is insufficient",
        "review failed",
    ]
    for signal in negative_signals:
        if signal.lower() in text.lower():
            return GateDecision(
                decision="needs-work",
                reason=f"Reviewer mentioned: '{signal}'",
                confidence="medium",
                proposed_transition="state:active (or release to state:ready)",
                proposed_command=None,
            )

    # Look for positive completion signals
    positive_signals = [
        "review complete",
        "no risks",
        "no new risks",
        "no critical",
        "ready for",
        "no blockers",
        "all constraints respected",
    ]
    positive_count = sum(1 for s in positive_signals if s.lower() in text.lower())

    if positive_count >= 2:
        return GateDecision(
            decision="pass",
            reason=(
                "Reviewer completed successfully (exit 0) with multiple "
                "positive signals and no blockers mentioned."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    # Default conservative fallback
    return GateDecision(
        decision="needs-work",
        reason=(
            "Reviewer exited 0 but did not clearly signal completion "
            "with multiple positive indicators."
        ),
        confidence="low",
        proposed_transition="state:active (reviewer should be re-run with more evidence)",
        proposed_command=None,
    )


def evaluate_ci_gate(
    exit_code: int,
    summary_text: str,
    raw_text: str | None = None,
) -> GateDecision:
    """Decision function for worker/CI gates.

    This is intentionally conservative but distinct from review gates:
    worker tasks are allowed to pass when execution completed cleanly and
    the artifact shows scoped changes/evidence.
    """
    text = ((summary_text or "") + "\n" + (raw_text or "")).lower()

    if exit_code != 0:
        return GateDecision(
            decision="fail",
            reason=f"Non-zero exit code from worker: {exit_code}",
            confidence="high",
            proposed_transition="state:failed",
            proposed_command=None,
        )

    stale_signal = find_stale_or_failover_signal(text)
    if stale_signal:
        return GateDecision(
            decision="needs-work",
            reason=f"Worker artifact contains stale/failover signal: '{stale_signal}'",
            confidence="high",
            proposed_transition="state:active (manual artifact fallback required)",
            proposed_command=None,
        )

    if _has_actual_traceback_signal(text):
        return GateDecision(
            decision="needs-work",
            reason="Worker output contains actual Python exception output",
            confidence="high",
            proposed_transition="state:active (worker should be re-run)",
            proposed_command=None,
        )

    negative_signals = [
        "critical blocker",
        "cannot proceed",
        "missing required evidence",
        "execution failed",
    ]
    for signal in negative_signals:
        disqualifier_context = _find_contextual_worker_disqualifier_signal(text, signal)
        if disqualifier_context:
            return GateDecision(
                decision="needs-work",
                reason=(
                    "Worker output mentioned "
                    f"'{disqualifier_context.signal}' in "
                    f"{disqualifier_context.section}: "
                    f"'{disqualifier_context.line}'"
                ),
                confidence="medium",
                proposed_transition="state:active (worker should be re-run)",
                proposed_command=None,
            )
    error_signal = _has_contextual_worker_error_signal(text)
    if error_signal:
        return GateDecision(
            decision="needs-work",
            reason=f"Worker output mentioned failure-context error: '{error_signal}'",
            confidence="medium",
            proposed_transition="state:active (worker should be re-run)",
            proposed_command=None,
        )

    positive_signals = [
        "execution complete",
        "files changed",
        "readme.md",
        "only file edited",
        "git diff -- readme.md",
        "code behavior",
    ]
    positive_count = sum(1 for signal in positive_signals if signal in text)

    if positive_count >= 3:
        return GateDecision(
            decision="pass",
            reason=(
                "Worker completed successfully (exit 0) with scoped change "
                "evidence and no blocker signals."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    evidence_text = summary_text + "\n" + (raw_text or "")

    # H024G: Strong scoped worker completion evidence (docs-only / README-only cases)
    if _has_scoped_worker_completion_evidence(evidence_text):
        return GateDecision(
            decision="pass",
            reason=(
                "Worker completed successfully (exit 0) with strong scoped completion evidence "
                "(scope followed 100%, dirty guard clean, README/docs-only, no code changes, "
                "no scope broadening)."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    # H029N-B: Scoped code/CLI worker completion evidence.
    if _has_scoped_worker_code_completion_evidence(evidence_text):
        return GateDecision(
            decision="pass",
            reason=(
                "Worker completed successfully (exit 0) with scoped code change evidence, "
                "validation evidence, manual smoke evidence, and no unrelated changes."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    # H033A: Scoped test-only worker completion evidence.
    if _has_scoped_worker_test_completion_evidence(evidence_text):
        return GateDecision(
            decision="pass",
            reason=(
                "Worker completed successfully (exit 0) with scoped test-only evidence, "
                "validation evidence, safety evidence, and no unrelated changes."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    # H032B: Validated no-op completion evidence.
    if _has_validated_noop_completion_evidence(evidence_text):
        return GateDecision(
            decision="pass",
            reason=(
                "Worker completed successfully (exit 0) with validated no-op completion "
                "evidence: no repository changes were required because the requested behavior "
                "already exists; targeted/full validation passed; manual smoke passed; "
                "unchanged-tree evidence confirmed the worktree had no file changes. "
                "Continue with normal complete and integration steps; this gate does not "
                "close the issue."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    return GateDecision(
        decision="needs-work",
        reason=(
            "Worker exited 0 but did not provide enough scoped completion "
            "evidence for the CI gate."
        ),
        confidence="low",
        proposed_transition="state:active (worker should be re-run with more evidence)",
        proposed_command=None,
    )




def _line_value_has_allowed_value(
    text: str,
    prefixes: tuple[str, ...],
    allowed_values: tuple[str, ...],
) -> bool:
    for raw_line in text.splitlines():
        line = raw_line.strip().lower()
        if ":" not in line:
            continue
        prefix, value = line.split(":", 1)
        if prefix.strip() not in prefixes:
            continue
        normalized = value.strip().strip("`*_-. ")
        if normalized in allowed_values:
            return True
    return False


def _has_human_approval_evidence(text: str) -> bool:
    phrase_marker = (
        "human gate approval" in text
        or "human gate approved" in text
        or "manual human gate approval" in text
    )
    proceed_marker = (
        "approved to proceed" in text
        or "approval is granted" in text
        or "approved to move" in text
        or "approved to proceed to pr" in text
    )
    structured_marker = _line_value_has_allowed_value(
        text,
        (
            "human approval",
            "human gate approval",
            "operator approval",
        ),
        ("yes", "approved", "granted"),
    )
    return (phrase_marker and proceed_marker) or structured_marker


def _has_human_scope_evidence(text: str) -> bool:
    return (
        "scope reviewed" in text
        or _line_value_has_allowed_value(
            text,
            ("scope reviewed", "scope match", "scope approval"),
            ("yes", "approved", "pass", "passed"),
        )
    )


def _has_human_validation_evidence(text: str) -> bool:
    return (
        "validation passed" in text
        or ("ruff check ." in text and "pytest tests/ -q" in text)
        or ("targeted validation passed" in text and "full validation passed" in text)
        or _line_value_has_allowed_value(
            text,
            ("validation", "validation status", "local validation"),
            ("pass", "passed", "yes"),
        )
    )


def _has_human_safety_evidence(text: str) -> bool:
    return (
        (
            "no github mutation" in text
            and "no openclaw execution" in text
            and ("no issue close" in text or "no issue was closed" in text)
            and ("no merge" in text or "no merge was performed" in text)
        )
        or _has_structured_human_safety_evidence(text)
    )


def _has_structured_human_safety_evidence(text: str) -> bool:
    no_values = (
        "no",
        "none",
        "false",
        "not performed",
        "not run",
        "not executed",
    )
    github_mutation = _line_value_has_allowed_value(
        text,
        ("github mutation", "github mutations"),
        no_values,
    )
    execution_backend = _line_value_has_allowed_value(
        text,
        (
            "openclaw execution",
            "codex execution",
            "execution backend",
            "backend execution",
        ),
        no_values,
    )
    issue_closure = _line_value_has_allowed_value(
        text,
        ("issue closure", "issue close", "issue closed"),
        no_values,
    )
    merge_performed = _line_value_has_allowed_value(
        text,
        ("merge", "merge performed"),
        no_values,
    )
    return github_mutation and execution_backend and issue_closure and merge_performed


def evaluate_human_gate(
    exit_code: int,
    summary_text: str,
    raw_text: str | None = None,
) -> GateDecision:
    """Decision function for explicit human-gated issues.

    Human gates are intentionally conservative. They pass only when local,
    auditable evidence clearly records human approval plus validation/safety
    context. GitHub comments are not parsed here.
    """
    text = ((summary_text or "") + "\n" + (raw_text or "")).lower()

    if exit_code != 0:
        return GateDecision(
            decision="fail",
            reason=f"Non-zero exit code from human-gated evidence: {exit_code}",
            confidence="high",
            proposed_transition="state:failed",
            proposed_command=None,
        )

    disqualifiers = [
        "human gate rejected",
        "approval denied",
        "not approved",
        "do not proceed",
        "cannot proceed",
        "critical blocker",
        "validation failed",
        "scope not reviewed",
        "unrelated files changed",
    ]
    for signal in disqualifiers:
        if signal in text:
            return GateDecision(
                decision="needs-work",
                reason=f"Human gate evidence mentioned: '{signal}'",
                confidence="high",
                proposed_transition="state:active (human gate approval required)",
                proposed_command=None,
            )

    approval_marker = _has_human_approval_evidence(text)
    scope_reviewed = _has_human_scope_evidence(text)
    validation_passed = _has_human_validation_evidence(text)
    safety_recorded = _has_human_safety_evidence(text)

    if all(
        [
            approval_marker,
            scope_reviewed,
            validation_passed,
            safety_recorded,
        ]
    ):
        return GateDecision(
            decision="pass",
            reason="Human gate approval evidence found with validation and safety context.",
            confidence="high",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    missing = [
        label
        for label, present in (
            ("approval", approval_marker),
            ("scope", scope_reviewed),
            ("validation", validation_passed),
            ("safety", safety_recorded),
        )
        if not present
    ]

    return GateDecision(
        decision="needs-work",
        reason=(
            "Human gate approval evidence missing or incomplete: "
            + ", ".join(missing)
            + "."
        ),
        confidence="high",
        proposed_transition="state:active (human gate approval required)",
        proposed_command=None,
    )
def _has_scoped_worker_code_completion_evidence(text: str) -> bool:
    # Detect conservative scoped code/CLI worker completion evidence.
    # Supports legacy lifecycle-watch evidence plus general src+tests scoped-code evidence.
    t = (text or "").lower()

    disqualifiers = [
        "task incomplete",
        "not complete",
        "incomplete",
        "dirty guard: failed",
        "dirty guard failed",
        "dirty: true",
        "working tree dirty",
        "scope not followed",
        "scope was broadened",
        "scope broadened",
        "actual scope broadening",
        "unexpected scope broadening",
        "unexpected change",
        "critical blocker",
        "cannot proceed",
        "execution failed",
        "validation failed",
        "pytest failed",
        "pytest fails",
        "ruff failed",
    ]
    if _has_contextual_worker_disqualifier(t, disqualifiers):
        return False
    if _has_actual_traceback_signal(t):
        return False

    if "unrelated files changed:" in t or "unrelated files were changed:" in t:
        return False

    required_core = [
        "exit code:** 0",
        "dirty guard:** clean",
        "task execution complete:** yes",
        "acceptance:** pass",
        "files changed",
        "ruff check .",
        "pytest tests/ -q",
        "no unrelated files were changed",
    ]
    if not all(signal in t for signal in required_core):
        return False

    legacy_lifecycle_watch_signals = [
        "src/signposter/cli.py",
        "tests/test_lifecycle.py",
        "lifecycle watch",
    ]
    if all(signal in t for signal in legacy_lifecycle_watch_signals):
        return True

    validation_signals = ["targeted validation", "full validation"]
    if not all(signal in t for signal in validation_signals):
        return False

    code_path_signals = (
        "src/",
        "govengine/",
        "sclite/",
    )
    has_code_path = any(signal in t for signal in code_path_signals)
    has_test_path = "tests/test_" in t
    if not (has_code_path and has_test_path):
        return False

    safety_signals = [
        "no github mutation",
        "no openclaw execution",
        "no issue was closed",
        "no merge was performed",
    ]
    return all(signal in t for signal in safety_signals)


def _has_actual_traceback_signal(text: str) -> bool:
    """Detect traceback only when it looks like real Python exception output."""
    t = (text or "").lower()
    traceback_markers = [
        "traceback (most recent call last):",
        'file "',
        "raise ",
    ]
    exception_markers = [
        "exception:",
        "runtimeerror:",
        "valueerror:",
        "typeerror:",
        "keyerror:",
        "modulenotfounderror:",
    ]
    return "traceback" in t and (
        any(marker in t for marker in traceback_markers)
        or any(marker in t for marker in exception_markers)
    )


def _has_contextual_worker_error_signal(text: str) -> str | None:
    """Return a blocking error line only when it looks like a real failure.

    CI-gate summaries often need to discuss literal matcher text such as
    ``error:``. Treat that as blocking only when the line also carries failure
    context in an execution/validation section; otherwise the structured
    scoped-evidence paths decide the gate.
    """
    failure_context = (
        "failed",
        "failure",
        "exception",
        "validation",
        "execution",
        "pytest",
        "ruff",
        "cannot",
        "critical",
    )
    failure_sections = (
        "validation evidence",
        "execution output",
        "failure",
        "failures",
        "errors",
        "stderr",
        "raw output",
    )
    current_section = ""
    for line in (text or "").splitlines():
        lowered = line.strip().lower()
        if lowered.startswith("#"):
            current_section = lowered.lstrip("#").strip()
            continue
        in_failure_section = any(section in current_section for section in failure_sections)
        starts_as_failure = lowered.startswith(("error:", "validation error:", "execution error:"))
        if (
            "error:" in lowered
            and (in_failure_section or starts_as_failure)
            and any(marker in lowered for marker in failure_context)
        ):
            return lowered[:160]
    return None


def _has_contextual_worker_disqualifier(
    text: str,
    disqualifiers: list[str],
) -> bool:
    return any(
        _has_contextual_worker_disqualifier_signal(text, disqualifier)
        for disqualifier in disqualifiers
    )


def _has_contextual_worker_disqualifier_signal(text: str, signal: str) -> bool:
    """Block real worker disqualifiers while allowing explicit policy examples."""
    return _find_contextual_worker_disqualifier_signal(text, signal) is not None


def _find_contextual_worker_disqualifier_signal(
    text: str,
    signal: str,
) -> WorkerDisqualifierContext | None:
    """Return structured context for real worker disqualifiers."""
    neutral_context = (
        "example",
        "examples",
        "literal",
        "policy context",
        "negative signal",
        "negative-signal",
        "trigger word",
        "trigger-word",
        "documented as",
        "words that still block",
        "when actual failure output",
        "without indicating real blockers",
    )
    neutral_sections = (
        "implemented behavior",
        "verified behavior",
        "audit",
        "audit result",
        "regression coverage",
        "findings",
        "reasoning summary",
    )
    failure_sections = (
        "validation evidence",
        "execution output",
        "failure",
        "failures",
        "errors",
        "stderr",
        "raw output",
    )
    current_section = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lower()
        if line.startswith("#"):
            current_section = line.lstrip("#").strip()
            continue
        if signal not in line:
            continue
        if any(marker in line for marker in neutral_context):
            continue
        if f"no {signal}" in line:
            continue
        in_failure_section = any(section in current_section for section in failure_sections)
        in_neutral_section = any(section in current_section for section in neutral_sections)
        if (
            in_neutral_section
            and not in_failure_section
            and _is_neutral_worker_disqualifier_discussion(line)
        ):
            continue
        return WorkerDisqualifierContext(
            signal=signal,
            section=current_section or "unsectioned output",
            line=line[:160],
        )
    return None


def _is_neutral_worker_disqualifier_discussion(line: str) -> bool:
    """Return True for meta-discussion of blocker phrases, not actual failures."""
    neutral_markers = (
        "regression",
        "coverage",
        "test",
        "tests",
        "guard",
        "preserve",
        "preserved",
        "still block",
        "still blocks",
        "continues to block",
        "continue to block",
        "remains blocked",
        "remain blocked",
        "blocked-path",
        "blocked path",
        "policy",
        "heuristic",
    )
    return any(marker in line for marker in neutral_markers)


def _has_scoped_worker_test_completion_evidence(text: str) -> bool:
    """Detect conservative scoped test-only worker completion evidence.

    This is intentionally separate from code/CLI evidence. Test-only tasks should
    not need to pretend they changed src/ files or provide manual CLI smoke when
    the scoped work is only test coverage.
    """
    t = (text or "").lower()

    disqualifiers = [
        "task incomplete",
        "not complete",
        "incomplete",
        "dirty guard: failed",
        "dirty guard failed",
        "dirty: true",
        "working tree dirty",
        "scope not followed",
        "scope was broadened",
        "scope broadened",
        "actual scope broadening",
        "unexpected scope broadening",
        "unexpected change",
        "modified src/",
        "src/signposter/",
        "traceback",
        "critical blocker",
        "cannot proceed",
        "execution failed",
        "ruff check fails",
        "pytest fails",
        "validation failed",
    ]
    if _has_contextual_worker_disqualifier(t, disqualifiers):
        return False

    required = [
        "exit code:** 0",
        "dirty guard:** clean",
        "task execution complete:** yes",
        "acceptance:** pass",
        "files changed",
        "ruff check .",
        "pytest tests/ -q",
    ]
    if not all(signal in t for signal in required):
        return False

    test_scope_signals = [
        "test-only",
        "tests/test_",
        "tests/",
    ]
    if sum(1 for signal in test_scope_signals if signal in t) < 2:
        return False

    safety_signals = [
        "no github mutation",
        "no openclaw execution",
        "no manifest mutation",
        "no unrelated files",
    ]
    return sum(1 for signal in safety_signals if signal in t) >= 3

def _has_validated_noop_completion_evidence(text: str) -> bool:
    """Detect conservative validated no-op worker completion evidence.

    This is for scoped tasks where the worker finds the requested behavior is
    already present in the current worktree/main state. It must be backed by
    validation and manual smoke evidence, and it must not hide dirty worktrees,
    failures, or unrelated changes.
    """
    t = (text or "").lower()

    disqualifiers = [
        "task incomplete",
        "not complete",
        "incomplete",
        "dirty guard: failed",
        "dirty guard failed",
        "dirty: true",
        "working tree dirty",
        "scope not followed",
        "scope broadening",
        "unexpected change",
        "traceback",
        "critical blocker",
        "cannot proceed",
        "execution failed",
        "ruff check fails",
        "pytest fails",
        "validation failed",
    ]
    if _has_contextual_worker_disqualifier(t, disqualifiers):
        return False

    required = [
        "exit code:** 0",
        "dirty guard:** clean",
        "task execution complete:** yes",
        "acceptance:** pass",
        "targeted validation",
        "full validation",
        "manual cli smoke passed",
    ]
    if not all(signal in t for signal in required):
        return False

    noop_claim_signals = [
        "no-op completion",
        "already exists",
        "already present",
        "no additional code changes were needed",
        "requested behavior already exists",
    ]
    if sum(1 for signal in noop_claim_signals if signal in t) < 2:
        return False

    unchanged_tree_signals = [
        "no files were changed",
        "no files changed",
        "files changed\n\nno files",
        "files changed\n\nnone",
        "changed files: none",
        "worktree changes: none",
        "isolated worktree remained unchanged",
        "no diff was produced",
    ]
    if not any(signal in t for signal in unchanged_tree_signals):
        return False

    behavior_signals = [
        "requested behavior already exists",
        "existing implementation",
        "existing ready output",
        "existing blocked output",
        "deterministic",
        "terminal-friendly",
    ]
    if sum(1 for signal in behavior_signals if signal in t) < 2:
        return False

    safety_signals = [
        "no github mutation",
        "no openclaw execution",
        "no manifest mutation",
        "no unrelated files",
    ]
    return sum(1 for signal in safety_signals if signal in t) >= 3


def _has_scoped_worker_completion_evidence(text: str) -> bool:
    """H024G: Detect strong, conservative evidence of scoped worker completion.

    Recognizes realistic low-risk docs-only / README-only worker summaries
    without making the gate permissive. Requires exit_code==0 already checked.
    """
    t = (text or "").lower()

    # Core strong signals (require several of these)
    strong_signals = [
        "scope followed: 100%",
        "scope followed 100%",
        "dirty guard: clean",
        "dirty guard clean",
        "readme.md only",
        "docs-only",
        "no code changes",
        "no scope broadening",
        "exact line",
        "exact diff",
        "only file edited",
    ]
    strong_count = sum(1 for s in strong_signals if s in t)

    # Supportive scoped evidence
    supportive = [
        "files changed",
        "readme.md",
        "git diff -- readme.md",
        "execution complete",
        "worker completed",
        "task complete",
        "no risks",
        "no blockers",
    ]
    supportive_count = sum(1 for s in supportive if s in t)

    # Negative / disqualifying signals (even if strong signals present)
    disqualifiers = [
        "task incomplete",
        "not complete",
        "incomplete",
        "dirty guard: failed",
        "dirty guard failed",
        "dirty: true",
        "working tree dirty",
        "scope not followed",
        "scope was broadened",
        "scope broadened",
        "actual scope broadening",
        "unexpected scope broadening",
        "unexpected change",
        "code files changed",
        "modified python",
        "modified src/",
    ]
    if any(d in t for d in disqualifiers):
        return False

    # Require strong evidence + some supportive context
    return strong_count >= 2 and supportive_count >= 1


def _is_already_integrated_issue(issue_state: dict[str, Any]) -> bool:
    """Return True if issue is CLOSED and carries the state:merged workflow label.

    This indicates the issue has completed the full lifecycle (PR merged + integrated).
    Used for early short-circuit in gate evaluation.
    """
    state = issue_state.get("state", "")
    labels = issue_state.get("labels", []) or []
    return state == "CLOSED" and "state:merged" in labels


def fetch_issue_state(repo: str, issue: int) -> dict[str, Any]:
    """Fetch current labels and state for the issue (read-only)."""
    result = subprocess.run(
        [
            "gh", "issue", "view", str(issue),
            "-R", repo,
            "--json", "number,title,state,labels",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch issue {issue}: {result.stderr.strip()}")

    import json
    data = json.loads(result.stdout)
    labels = [lbl["name"] for lbl in data.get("labels", [])]
    return {
        "number": data["number"],
        "title": data["title"],
        "state": data["state"],
        "labels": labels,
    }


def load_summary(summary_path: str | Path) -> str:
    path = Path(summary_path)
    if not path.exists():
        raise FileNotFoundError(f"Summary artifact not found: {path}")
    return path.read_text(encoding="utf-8")


def load_raw_if_exists(raw_path: str | Path) -> str | None:
    path = Path(raw_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def run_gate_dry_run(
    repo: str,
    issue: int,
    summary_path: str | Path,
) -> dict[str, Any]:
    """Main dry-run gate evaluation."""
    issue_state = fetch_issue_state(repo, issue)
    labels = issue_state["labels"]

    # HARDENING-025E-B: Already-integrated short-circuit
    # Must happen before any gate label checks or artifact loading.
    if _is_already_integrated_issue(issue_state):
        return {
            "repo": repo,
            "issue": issue,
            "issue_title": issue_state["title"],
            "current_state": issue_state["state"],
            "labels": labels,
            "is_already_integrated": True,
            "decision": "NOT-APPLICABLE",
            "reason": "Issue is already closed and integrated.",
            "confidence": "high",
            "status": "completed",
            "notes": [
                "No gate action is required.",
                "No GitHub mutation was performed.",
            ],
        }

    has_active = "state:active" in labels
    has_gate_review = "gate:review" in labels
    has_gate_ci = "gate:ci" in labels
    has_gate_human = "gate:human" in labels

    if has_gate_review:
        gate_type = "review"
    elif has_gate_ci:
        gate_type = "ci"
    elif has_gate_human:
        gate_type = "human"
    else:
        gate_type = "unknown"
    summary_text = load_summary(summary_path)

    # Try to derive raw path and load it for better signals
    raw_path = Path(summary_path).with_name(
        Path(summary_path).name.replace(".summary.md", ".raw.txt")
    )
    raw_text = load_raw_if_exists(raw_path)

    worker_artifact_validation: WorkerArtifactValidation | None = None
    if gate_type == "ci":
        worker_artifact_validation = validate_worker_summary_artifact(
            issue,
            summary_path=summary_path,
        )
        if worker_artifact_validation.status != "pass":
            reason = _worker_artifact_block_reason(worker_artifact_validation)
            return {
                "repo": repo,
                "issue": issue,
                "issue_title": issue_state["title"],
                "current_state": issue_state["state"],
                "labels": labels,
                "has_state_active": has_active,
                "has_gate_review": has_gate_review,
                "has_gate_ci": has_gate_ci,
                "has_gate_human": has_gate_human,
                "gate_type": gate_type,
                "summary_path": str(summary_path),
                "raw_path": str(raw_path) if raw_path.exists() else None,
                "exit_code": None,
                "decision": "needs-work",
                "reason": reason,
                "confidence": "high",
                "proposed_transition": "state:active (worker artifact repair required)",
                "proposed_command": None,
                "valid_for_gate": False,
                "worker_artifact_validation": _worker_artifact_validation_payload(
                    worker_artifact_validation
                ),
            }

    # Extract exit code from summary (simple heuristic)
    exit_code = 0
    for line in summary_text.splitlines():
        if line.startswith("**Exit Code:**"):
            try:
                exit_code = int(line.split(":", 1)[1].strip())
            except Exception:
                pass
            break

    if gate_type == "ci":
        decision = evaluate_ci_gate(exit_code, summary_text, raw_text)
    elif gate_type == "review":
        decision = evaluate_gate(exit_code, summary_text, raw_text)
    elif gate_type == "human":
        decision = evaluate_human_gate(exit_code, summary_text, raw_text)
    else:
        decision = GateDecision(
            decision="needs-work",
            reason=(
                "Issue has no supported gate label "
                "(expected gate:ci, gate:review, or gate:human)."
            ),
            confidence="high",
            proposed_transition=None,
            proposed_command=None,
        )
    # Fill in repo/issue in proposed command if present
    proposed_cmd = None
    if decision.proposed_command:
        proposed_cmd = decision.proposed_command.format(repo=repo, issue=issue)

    return {
        "repo": repo,
        "issue": issue,
        "issue_title": issue_state["title"],
        "current_state": issue_state["state"],
        "labels": labels,
        "has_state_active": has_active,
        "has_gate_review": has_gate_review,
        "has_gate_ci": has_gate_ci,
        "has_gate_human": has_gate_human,
        "gate_type": gate_type,
        "summary_path": str(summary_path),
        "raw_path": str(raw_path) if raw_path.exists() else None,
        "exit_code": exit_code,
        "decision": decision.decision,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "proposed_transition": decision.proposed_transition,
        "proposed_command": proposed_cmd,
        "valid_for_gate": has_active and gate_type in {"review", "ci", "human"},
        "worker_artifact_validation": (
            _worker_artifact_validation_payload(worker_artifact_validation)
            if worker_artifact_validation is not None
            else None
        ),
    }


def format_gate_report(result: dict[str, Any]) -> str:
    # HARDENING-025E-B: Clean output for already-integrated issues
    if result.get("is_already_integrated"):
        lines = [
            "Signposter Gate Decision (dry-run)",
            "",
            f"Repository: {result['repo']}",
            f"Issue: #{result['issue']} — {result['issue_title']}",
            "",
            "Current State:",
            f"  state: {result['current_state']}",
            f"  labels: {', '.join(result['labels'])}",
            "",
            "Decision:",
            f"  {result['decision']}",
            f"  Reason: {result['reason']}",
            f"  Confidence: {result['confidence']}",
            "",
            "Status:",
            f"  {result['status']}",
            "",
            "Notes:",
        ]
        for note in result.get("notes", []):
            lines.append(f"  {note}")
        return "\n".join(lines)

    lines = [
        "Signposter Gate Decision (dry-run)",
        "",
        f"Repository: {result['repo']}",
        f"Issue: #{result['issue']} — {result['issue_title']}",
        "",
        "Current State:",
        f"  state: {result['current_state']}",
        f"  labels: {', '.join(result['labels'])}",
        "",
        "Evidence:",
        f"  Summary: {result['summary_path']}",
    ]
    if result.get("raw_path"):
        lines.append(f"  Raw:     {result['raw_path']}")
    else:
        lines.append("  Raw:     (not found)")

    worker_artifact = result.get("worker_artifact_validation")
    if worker_artifact:
        lines.extend(
            [
                "",
                "Worker artifact preflight:",
                f"  status: {worker_artifact['status']}",
                f"  summary: {worker_artifact['summary_path']}",
                f"  raw: {worker_artifact['raw_path'] or 'none'}",
                f"  raw exists: {worker_artifact['raw_exists']}",
            ]
        )
        if worker_artifact["summary_signal"]:
            lines.append(f"  summary unsafe marker: {worker_artifact['summary_signal']}")
        if worker_artifact["raw_signal"]:
            lines.append(f"  raw unsafe marker: {worker_artifact['raw_signal']}")
        if worker_artifact["missing"]:
            lines.append("  missing:")
            lines.extend(f"    - {item}" for item in worker_artifact["missing"])
        if worker_artifact["guidance"]:
            lines.append("  guidance:")
            lines.extend(f"    - {item}" for item in worker_artifact["guidance"][:4])

    lines.extend(
        [
            "",
            "Gate Validation:",
            f"  state:active present: {result.get('has_state_active', False)}",
            f"  gate type:            {result.get('gate_type', 'unknown')}",
            f"  gate:review present:  {result.get('has_gate_review', False)}",
            f"  gate:ci present:      {result.get('has_gate_ci', False)}",
            f"  gate:human present:   {result.get('has_gate_human', False)}",
            "",
            "Decision:",
            f"  {result['decision'].upper()}",
            f"  Reason: {result['reason']}",
            f"  Confidence: {result['confidence']}",
        ]
    )

    blocked_sections = _blocked_evidence_sections(result)
    if blocked_sections:
        lines.extend(["", "Blocked evidence sections:"])
        lines.extend(f"  - {section}" for section in blocked_sections)

    if result.get("proposed_transition"):
        lines.append(f"  Proposed: {result['proposed_transition']}")

    if result.get("proposed_command"):
        lines.append("")
        lines.append("Suggested next command:")
        lines.append(f"  {result['proposed_command']}")

    if not result.get("valid_for_gate", False):
        lines.append("")
        lines.append("WARNING: This issue does not appear ready for a supported gate decision.")

    return "\n".join(lines)


def _blocked_evidence_sections(result: dict[str, Any]) -> tuple[str, ...]:
    """Return operator-facing evidence sections to repair for blocked gates."""
    decision = str(result.get("decision", "")).lower()
    if decision in {"pass", "not-applicable"}:
        return ()

    reason = str(result.get("reason", "")).lower()
    gate_type = str(result.get("gate_type", "unknown")).lower()
    worker_artifact = result.get("worker_artifact_validation") or {}

    if worker_artifact and worker_artifact.get("status") != "pass":
        sections = ["Worker summary schema: repair missing fields shown above."]
        if worker_artifact.get("summary_signal") or worker_artifact.get("raw_signal"):
            sections.append(
                "Artifact safety: replace stale/failover runtime output with a reviewed summary."
            )
        return tuple(sections)

    if gate_type == "ci":
        if "non-zero exit code" in reason:
            return (
                "**Exit Code:** use a successful execution summary or documented takeover.",
                "## Validation evidence",
                "## Safety",
            )
        if "stale/failover" in reason:
            return (
                "## Scoped completion evidence",
                "## Validation evidence",
                "Artifact safety: write a reviewed human/operator summary.",
            )
        if "python exception" in reason or "worker output mentioned" in reason:
            return (
                "## Scoped completion evidence",
                "## Validation evidence",
                "## Safety",
            )
        return (
            "## Scoped completion evidence",
            "## Validation evidence",
            "## Safety",
            "## Gate recommendation",
        )

    if gate_type == "human":
        sections = []
        if "approval" in reason:
            sections.append("Human approval: approved")
        if "scope" in reason:
            sections.append("Scope reviewed: yes")
        if "validation" in reason:
            sections.append("Validation status: passed")
        if "safety" in reason:
            sections.extend(
                [
                    "GitHub mutation: no",
                    "Execution backend: no",
                    "Issue closure: no",
                    "Merge performed: no",
                ]
            )
        return tuple(sections or ("Human approval, scope, validation, and safety fields",))

    if gate_type == "review":
        return (
            "Verdict:",
            "Confidence:",
            "Scope match:",
            "CI considered:",
            "Merge recommendation:",
            "Findings:",
        )

    return ("gate label: add one supported gate label: gate:ci, gate:review, or gate:human",)


def evaluate_gate_for_complete(repo: str, issue: int) -> tuple[bool, str, str, str]:
    """Evaluate whether a gated issue can be completed.

    Returns:
        (passes: bool, decision: str, reason: str, gate_type: str)

    Supports gate:ci, gate:review, and gate:human. If no supported gate label
    is present, preserves existing non-gated behavior.
    """
    try:
        issue_state = fetch_issue_state(repo, issue)
    except Exception as e:
        return False, "error", f"Failed to fetch issue state: {e}", "error"

    labels = issue_state.get("labels", [])
    has_gate_ci = "gate:ci" in labels
    has_gate_review = "gate:review" in labels
    has_gate_human = "gate:human" in labels

    if has_gate_ci:
        gate_type = "ci"
    elif has_gate_review:
        gate_type = "review"
    elif has_gate_human:
        gate_type = "human"
    else:
        return True, "no-gate", "no supported gate label present", "none"

    # Find latest summary artifact for this issue
    from signposter.cli import _find_latest_summary_for_issue

    summary_path = _find_latest_summary_for_issue(issue)
    if not summary_path:
        return (
            False,
            "needs-work",
            (
                f"No summary artifact found for issue #{issue} "
                f"(expected artifacts/runs/issue-{issue}-*.summary.md)"
            ),
            gate_type,
        )

    try:
        summary_text = load_summary(summary_path)
    except Exception as e:
        return False, "error", f"Failed to load summary: {e}", gate_type

    if has_gate_ci:
        worker_artifact_validation = validate_worker_summary_artifact(
            issue,
            summary_path=summary_path,
        )
        if worker_artifact_validation.status != "pass":
            return (
                False,
                "needs-work",
                _worker_artifact_block_reason(worker_artifact_validation),
                gate_type,
            )

    # Load raw if present for better signals
    raw_path = Path(summary_path).with_name(
        Path(summary_path).name.replace(".summary.md", ".raw.txt")
    )
    raw_text = load_raw_if_exists(raw_path)

    # Extract exit code from summary
    exit_code = 0
    for line in summary_text.splitlines():
        if line.startswith("**Exit Code:**"):
            try:
                exit_code = int(line.split(":", 1)[1].strip())
            except Exception:
                pass
            break

    if has_gate_ci:
        gate_decision = evaluate_ci_gate(exit_code, summary_text, raw_text)
    elif has_gate_review:
        gate_decision = evaluate_gate(exit_code, summary_text, raw_text)
    else:
        gate_decision = evaluate_human_gate(exit_code, summary_text, raw_text)

    return (
        gate_decision.decision == "pass",
        gate_decision.decision,
        gate_decision.reason,
        gate_type,
    )


def _worker_artifact_validation_payload(result: WorkerArtifactValidation) -> dict[str, Any]:
    return {
        "status": result.status,
        "summary_path": result.path,
        "summary_exists": result.exists,
        "summary_signal": result.stale_signal,
        "raw_path": result.raw_path,
        "raw_exists": result.raw_exists,
        "raw_signal": result.raw_stale_signal,
        "missing": list(result.missing),
        "guidance": list(result.guidance or []),
    }


def _worker_artifact_block_reason(result: WorkerArtifactValidation) -> str:
    problems: list[str] = []
    if not result.exists:
        problems.append("summary artifact is missing")
    if result.missing:
        problems.append("summary artifact is missing required fields")
    if result.stale_signal:
        problems.append(f"summary artifact contains unsafe marker: {result.stale_signal}")
    if result.raw_stale_signal:
        problems.append(f"raw artifact contains unsafe marker: {result.raw_stale_signal}")
    return "Worker artifact preflight blocked: " + "; ".join(problems)
