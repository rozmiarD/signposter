"""Tests for signposter.gate decision logic.

Pure tests using string fixtures — no network, no real files.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from signposter.gate import (
    _is_already_integrated_issue,
    build_gate_heuristic_audit,
    evaluate_gate,
    evaluate_gate_for_complete,
    format_gate_heuristic_audit,
    format_gate_report,
    run_gate_dry_run,
)


def test_evaluate_pass_on_clean_success():
    summary = """
**Exit Code:** 0
**Review Findings**
**Evidence Status:** All evidence present.
**Observations:**
- All constraints respected
- No risks/gaps
**Next Steps:** Review complete.
"""
    decision = evaluate_gate(0, summary)
    assert decision.decision == "pass"
    assert "successful" in decision.reason.lower() or "complete" in decision.reason.lower()
    assert decision.proposed_transition == "state:active → state:done"


def test_evaluate_fail_on_nonzero_exit():
    decision = evaluate_gate(1, "Some output")
    assert decision.decision == "fail"
    assert "non-zero" in decision.reason.lower()


def test_evaluate_needs_work_on_missing_evidence():
    summary = """
**Exit Code:** 0
**Evidence Status:** missing required evidence
**Observations:** cannot proceed without more context
"""
    decision = evaluate_gate(0, summary)
    assert decision.decision == "needs-work"
    assert (
        "missing required evidence" in decision.reason
        or "cannot proceed" in decision.reason.lower()
    )


def test_evaluate_needs_work_when_unclear():
    """Conservative default when exit 0 but no strong positive signals."""
    summary = "**Exit Code:** 0\nSome generic output with no clear signals."
    decision = evaluate_gate(0, summary)
    assert decision.decision == "needs-work"
    assert decision.confidence == "low"


def test_evaluate_ci_gate_pass_on_worker_execution_complete():
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Agent:** worker

**Execution complete.**

### 1. Files changed
- `README.md` (only file edited for this task)

### 2. Summary of README change
Added MVP Status section.

### 3. Evidence: `git diff -- README.md`

Code behavior was unchanged.
"""
    decision = evaluate_ci_gate(0, summary)
    assert decision.decision == "pass"
    assert decision.proposed_transition == "state:active → state:done"


def test_evaluate_ci_gate_needs_work_when_worker_output_unclear():
    from signposter.gate import evaluate_ci_gate

    decision = evaluate_ci_gate(0, "**Exit Code:** 0\nGeneric output only.")
    assert decision.decision == "needs-work"
    assert decision.confidence == "low"


def test_evaluate_ci_gate_allows_literal_error_example_with_structured_evidence():
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #250
**Agent:** human/operator
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

PASS - scoped code task completed.

## Files changed

- `src/signposter/gate.py`
- `tests/test_gate.py`

## Implemented behavior / verified behavior

- The audit output mentions the literal matcher text `error:` as policy context.

## Validation evidence

Targeted validation passed:

- `ruff check src/signposter/gate.py tests/test_gate.py`

Full validation passed:

- `ruff check .`
- `pytest tests/ -q`

Manual CLI smoke passed:

- `signposter gate --audit-heuristics`

## Safety

No GitHub mutation was performed by the implemented code.
No OpenClaw execution was performed by the implemented code.
No issue was closed by the implemented code.
No merge was performed by the implemented code.
No unrelated files were changed.
"""
    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"


def test_evaluate_ci_gate_blocks_contextual_validation_error():
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Validation evidence

Validation error: pytest failed on tests/test_gate.py
"""
    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "needs-work"
    assert "failure-context error" in decision.reason


def test_evaluate_ci_gate_blocks_provider_runtime_signal():
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass
"""
    raw = "Provider unavailable while running the selected execution backend."

    decision = evaluate_ci_gate(0, summary, raw)

    assert decision.decision == "needs-work"
    assert "stale/failover signal" in decision.reason


def test_evaluate_ci_gate_blocks_actual_execution_failed_signal():
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

Execution failed during backend execution.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "needs-work"
    assert "execution failed" in decision.reason


def test_evaluate_ci_gate_allows_positive_manual_takeover_summary():
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #258
**Agent:** human/operator
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

PASS - scoped test-only task completed with validation evidence.

## Files changed

- tests/test_gate.py

## Implemented behavior / verified behavior

- Manual takeover artifacts were preserved locally.
- Positive worker evidence stays structured and bounded.

## Validation evidence

- ruff check .
- pytest tests/ -q

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert "scoped test-only evidence" in decision.reason


def test_gate_heuristic_audit_maps_gate_surfaces_and_risks():
    audit = build_gate_heuristic_audit()
    output = format_gate_heuristic_audit(audit)

    assert audit.status == "ready"
    assert any("ci gate" in item for item in audit.gate_surfaces)
    assert any("review gate" in item for item in audit.gate_surfaces)
    assert any("human gate" in item for item in audit.gate_surfaces)
    assert any("no-op" in item for item in audit.gate_surfaces)
    assert "human gate structured safety fields" in output
    assert "legacy positive fallback" in output
    assert "prefer structured human gate fields" in output
    assert "False-positive risks:" in output
    assert "False-negative risks:" in output
    assert "No GitHub mutation was performed." in output
    assert "No OpenClaw execution was performed." in output


def test_cli_gate_audit_heuristics_does_not_require_repo_or_issue(capsys):
    from signposter.cli import run_gate

    result = run_gate(
        argparse.Namespace(
            audit_heuristics=True,
            repo=None,
            issue=None,
            summary=None,
        )
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "Signposter Gate Heuristic Audit" in output
    assert "Status:\n  ready" in output


def test_cli_gate_requires_repo_and_issue_without_audit(capsys):
    from signposter.cli import run_gate

    result = run_gate(
        argparse.Namespace(
            audit_heuristics=False,
            repo=None,
            issue=None,
            summary=None,
        )
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "--repo and --issue are required" in captured.err


# --- HARDENING-025E-B: Already-integrated gate behavior ---


@pytest.mark.parametrize(
    "issue_state,expected",
    [
        ({"state": "CLOSED", "labels": ["state:merged", "phase:build"]}, True),
        ({"state": "CLOSED", "labels": ["state:merged"]}, True),
        ({"state": "OPEN", "labels": ["state:merged"]}, False),
        ({"state": "CLOSED", "labels": ["phase:build"]}, False),
        ({"state": "CLOSED", "labels": []}, False),
        ({"state": "CLOSED", "labels": ["state:active", "gate:ci"]}, False),
    ],
)
def test_is_already_integrated_issue(issue_state, expected):
    assert _is_already_integrated_issue(issue_state) is expected


def test_run_gate_dry_run_returns_not_applicable_for_integrated_issue():
    """CLOSED + state:merged must short-circuit to NOT-APPLICABLE before any gate logic."""
    fake_issue = {
        "number": 6,
        "title": "Smoke test: full lifecycle docs-only issue",
        "state": "CLOSED",
        "labels": ["state:merged", "phase:build", "risk:low"],
    }

    with patch("signposter.gate.fetch_issue_state", return_value=fake_issue):
        # summary_path is never used (short-circuit before load_summary)
        result = run_gate_dry_run(
            "ExatronOmega/signposter", 6, "artifacts/runs/issue-6.summary.md"
        )

    assert result["is_already_integrated"] is True
    assert result["decision"] == "NOT-APPLICABLE"
    assert result["reason"] == "Issue is already closed and integrated."
    assert result["confidence"] == "high"
    assert result["status"] == "completed"
    assert "No gate action is required." in result["notes"]
    assert "No GitHub mutation was performed." in result["notes"]


def test_run_gate_dry_run_blocks_malformed_worker_artifact(tmp_path):
    summary = tmp_path / "issue-72-worker.summary.md"
    summary.write_text("short summary\n**Exit Code:** 0\n", encoding="utf-8")
    fake_issue = {
        "number": 72,
        "title": "Worker artifact check",
        "state": "OPEN",
        "labels": ["state:active", "gate:ci", "phase:build"],
    }

    with patch("signposter.gate.fetch_issue_state", return_value=fake_issue):
        result = run_gate_dry_run("test/repo", 72, summary_path=summary)

    output = format_gate_report(result)

    assert result["decision"] == "needs-work"
    assert "Worker artifact preflight blocked" in result["reason"]
    assert result["valid_for_gate"] is False
    assert "Worker artifact preflight:" in output
    assert "summary artifact is missing required fields" in output
    assert "guidance:" in output


def test_complete_gate_blocks_malformed_worker_artifact(tmp_path):
    summary = tmp_path / "issue-72-worker.summary.md"
    summary.write_text("short summary\n**Exit Code:** 0\n", encoding="utf-8")
    fake_issue = {
        "number": 72,
        "title": "Worker artifact check",
        "state": "OPEN",
        "labels": ["state:active", "gate:ci", "phase:build"],
    }

    with (
        patch("signposter.gate.fetch_issue_state", return_value=fake_issue),
        patch("signposter.cli._find_latest_summary_for_issue", return_value=str(summary)),
    ):
        passes, decision, reason, gate_type = evaluate_gate_for_complete("test/repo", 72)

    assert passes is False
    assert decision == "needs-work"
    assert gate_type == "ci"
    assert "Worker artifact preflight blocked" in reason


def test_format_gate_report_for_already_integrated_issue():
    result = {
        "repo": "ExatronOmega/signposter",
        "issue": 6,
        "issue_title": "Smoke test: full lifecycle docs-only issue",
        "current_state": "CLOSED",
        "labels": ["state:merged", "phase:build"],
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
    output = format_gate_report(result)

    assert "NOT-APPLICABLE" in output
    assert "Reason: Issue is already closed and integrated." in output
    assert "Status:" in output
    assert "completed" in output
    assert "No gate action is required." in output
    assert "No GitHub mutation was performed." in output
    # Must NOT contain the normal gate validation sections
    assert "Gate Validation:" not in output
    assert "Evidence:" not in output
    assert "WARNING:" not in output


def test_closed_without_state_merged_does_not_report_completed():
    """CLOSED issue without state:merged must NOT take the integrated path."""
    fake_issue = {
        "number": 99,
        "title": "Some closed issue",
        "state": "CLOSED",
        "labels": ["phase:build"],
    }

    with patch("signposter.gate.fetch_issue_state", return_value=fake_issue), \
         patch("signposter.gate.load_summary", return_value="**Exit Code:** 0\nDummy summary"):
        result = run_gate_dry_run(
            "ExatronOmega/signposter", 99, "artifacts/runs/issue-99.summary.md"
        )

    # Should fall through to normal "no gate label" path
    assert result.get("is_already_integrated") is not True
    assert result["decision"] == "needs-work"
    assert "no supported gate label" in result["reason"].lower()


def test_integrated_path_does_not_require_state_active_or_gate_labels():
    """The integrated short-circuit must succeed even without state:active or any gate:* label."""
    fake_issue = {
        "number": 6,
        "title": "Integrated issue",
        "state": "CLOSED",
        "labels": ["state:merged"],
    }

    with patch("signposter.gate.fetch_issue_state", return_value=fake_issue):
        result = run_gate_dry_run("ExatronOmega/signposter", 6, "/nonexistent/summary.md")

    assert result["decision"] == "NOT-APPLICABLE"
    assert result["status"] == "completed"


def test_active_gate_ci_behavior_unchanged_positive():
    """Active gate:ci positive path must continue to work (regression guard)."""
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Agent:** worker

**Execution complete.**

### 1. Files changed
- `README.md` (only file edited)

### 3. Evidence: `git diff -- README.md`

Code behavior was unchanged.
Scope followed: 100%
Dirty guard: clean
"""
    decision = evaluate_ci_gate(0, summary)
    assert decision.decision == "pass"


def test_active_gate_ci_behavior_unchanged_negative():
    """Active gate:ci negative path must continue to block (regression guard)."""
    from signposter.gate import evaluate_ci_gate

    decision = evaluate_ci_gate(0, "**Exit Code:** 0\nGeneric unclear output.")
    assert decision.decision == "needs-work"


def test_evaluate_ci_gate_pass_on_scoped_lifecycle_watch_code_task():
    """Scoped src+tests worker task with validation evidence should pass CI gate."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #10 — WATCH-001 — Define lifecycle watch CLI contract
**Agent:** worker
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Files changed

- src/signposter/cli.py
- tests/test_lifecycle.py

## Implemented behavior

Added lifecycle watch subcommand under existing signposter lifecycle.

## Validation evidence

Targeted validation in isolated worktree passed:

- ruff check src/signposter/cli.py tests/test_lifecycle.py
- pytest tests/test_lifecycle.py -q

Full validation in isolated worktree passed:

- ruff check .
- pytest tests/ -q

Manual CLI smoke passed.

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert decision.proposed_transition == "state:active → state:done"
    assert "scoped code change evidence" in decision.reason


def test_ci_gate_passes_validated_noop_completion():
    """Validated no-op completion should pass without requiring fake code changes."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #12 — WATCH-003 — Add simple terminal refresh renderer
**Agent:** worker
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

WATCH-003 was evaluated as a no-op completion: the requested behavior already exists.
The existing implementation provides deterministic terminal-friendly output.
Existing ready output is deterministic and terminal-friendly.
Existing blocked output is deterministic and terminal-friendly.

## Files changed

No files were changed in the isolated worktree.

## Validation evidence

Targeted validation in isolated worktree passed:
- ruff check src/signposter/cli.py src/signposter/lifecycle.py tests/test_lifecycle.py
- pytest tests/test_lifecycle.py -q

Full validation in isolated worktree passed:
- ruff check .
- pytest tests/ -q

Manual CLI smoke passed.

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.

## Gate recommendation

PASS — scoped no-op worker task completed with validation evidence.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert "validated no-op completion" in decision.reason
    assert decision.proposed_transition == "state:active → state:done"


def test_ci_gate_passes_validated_noop_with_structured_unchanged_tree_evidence():
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

Validated no-op completion: requested behavior already exists.
The existing implementation provides deterministic terminal-friendly output.
Existing ready output is deterministic and terminal-friendly.
Existing blocked output is deterministic and terminal-friendly.

Changed files: none

## Validation evidence

Targeted validation passed:
- ruff check src/signposter/cli.py tests/test_lifecycle.py
- pytest tests/test_lifecycle.py -q

Full validation passed:
- ruff check .
- pytest tests/ -q

Manual CLI smoke passed.

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert "worktree had no file changes" in decision.reason


def test_ci_gate_blocks_noop_without_unchanged_tree_evidence():
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

Validated no-op completion: requested behavior already exists.
The existing implementation provides deterministic terminal-friendly output.
Existing ready output is deterministic and terminal-friendly.
Existing blocked output is deterministic and terminal-friendly.

Targeted validation passed:
- ruff check src/signposter/cli.py tests/test_lifecycle.py
- pytest tests/test_lifecycle.py -q

Full validation passed:
- ruff check .
- pytest tests/ -q

Manual CLI smoke passed.

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "needs-work"
    assert "validated no-op completion" not in decision.reason


def test_ci_gate_blocks_noop_without_validation():
    """No-op claims without validation/smoke evidence must remain blocked."""
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

No files were changed.
The behavior already exists.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "needs-work"



def test_ci_gate_docs_only_allows_no_code_changes_phrase():
    """Docs-only evidence must not be rejected by the phrase 'no code changes'."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #14 — WATCH-005 — Document lifecycle watch operator usage
**Agent:** worker
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

Worker completed the requested docs-only task.
Scope followed: 100%.
Dirty guard: clean.
No code changes.

## Files changed

- docs/operator-lifecycle-runbook.md

## Validation evidence

- ruff check .
- pytest tests/ -q

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert "strong scoped completion evidence" in decision.reason


def test_ci_gate_docs_only_allows_no_scope_broadening_phrase():
    """Docs-only evidence must not be rejected by the phrase 'no scope broadening'."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #14 — WATCH-005 — Document lifecycle watch operator usage
**Agent:** worker
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

Worker completed the requested docs-only task.
Scope followed: 100%.
Dirty guard: clean.
No scope broadening.

## Files changed

- docs/operator-lifecycle-runbook.md

## Validation evidence

- ruff check .
- pytest tests/ -q

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert "strong scoped completion evidence" in decision.reason


def test_ci_gate_docs_only_blocks_real_modified_src_evidence():
    """Docs-only helper must still block evidence that admits src modifications."""
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

Worker completed the requested docs-only task.
Scope followed: 100%.
Dirty guard: clean.
Docs-only evidence.

## Files changed

- docs/operator-lifecycle-runbook.md
- modified src/signposter/gate.py

Validation:
- ruff check .
- pytest tests/ -q
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "needs-work"


def test_ci_gate_passes_scoped_test_only_completion():
    """Valid test-only worker completion should pass without fake src changes."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #19 — H033A — Harden CI gate evidence matching
**Agent:** worker
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Scoped completion evidence

PASS — completed as a narrow test-only task.

## Files changed

- tests/test_gate.py

## Validation evidence

- ruff check .
- pytest tests/ -q

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert "scoped test-only evidence" in decision.reason


def test_ci_gate_blocks_test_only_completion_without_validation():
    """Test-only claims without validation must remain blocked."""
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

Test-only task completed.

## Files changed

- tests/test_gate.py

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "needs-work"


def test_evaluate_human_gate_blocks_without_approval_evidence():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Execution Summary

**Exit Code:** 0

Validation passed.
No GitHub mutation was performed.
No OpenClaw execution was performed.
No issue was closed.
No merge was performed.
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "needs-work"
    assert "approval evidence missing" in decision.reason.lower()


def test_evaluate_human_gate_passes_with_explicit_approval_evidence():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Human Gate Summary

**Exit Code:** 0

Human gate approval recorded.
Approval is granted.
Approved to proceed to PR.

Scope reviewed:
- src/signposter/gate.py
- tests/test_gate.py

Validation passed:
- ruff check .
- pytest tests/ -q

Safety:
No GitHub mutation was performed by the implemented code.
No OpenClaw execution was performed by the implemented code.
No issue was closed by the implemented code.
No merge was performed by the implemented code.
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "pass"
    assert decision.confidence == "high"
    assert decision.proposed_transition == "state:active → state:done"


def test_evaluate_human_gate_passes_with_structured_evidence_fields():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Human Gate Summary

**Exit Code:** 0

Human approval: approved
Scope reviewed: yes
Validation status: passed

Safety:
No GitHub mutation was performed by the implemented code.
No OpenClaw execution was performed by the implemented code.
No issue was closed by the implemented code.
No merge was performed by the implemented code.
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "pass"
    assert (
        decision.reason
        == "Human gate approval evidence found with validation and safety context."
    )


def test_evaluate_human_gate_passes_with_structured_safety_fields():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Human Gate Summary

**Exit Code:** 0

Human approval: approved
Scope match: yes
Local validation: pass
GitHub mutation: no
Execution backend: no
Issue closure: no
Merge performed: no
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "pass"
    assert decision.confidence == "high"


def test_evaluate_human_gate_blocks_incomplete_structured_safety_fields():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Human Gate Summary

**Exit Code:** 0

Human approval: approved
Scope match: yes
Local validation: pass
GitHub mutation: no
Execution backend: no
Issue closure: no
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "needs-work"
    assert decision.reason == "Human gate approval evidence missing or incomplete: safety."


def test_evaluate_human_gate_reports_missing_components():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Human Gate Summary

**Exit Code:** 0

Human approval: approved
Validation status: passed

Safety:
No GitHub mutation was performed by the implemented code.
No OpenClaw execution was performed by the implemented code.
No issue was closed by the implemented code.
No merge was performed by the implemented code.
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "needs-work"
    assert decision.reason == "Human gate approval evidence missing or incomplete: scope."


def test_evaluate_human_gate_blocks_ambiguous_approval():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Human Gate Summary

**Exit Code:** 0

Looks okay maybe.
Scope reviewed.
Validation passed.
No GitHub mutation was performed.
No OpenClaw execution was performed.
No issue was closed.
No merge was performed.
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "needs-work"


def test_evaluate_human_gate_blocks_negative_approval_signal():
    from signposter.gate import evaluate_human_gate

    summary = """
# Signposter Human Gate Summary

**Exit Code:** 0

Human gate approval recorded.
Approval is granted.
Approved to proceed.
Scope reviewed.
Validation passed.
No GitHub mutation was performed.
No OpenClaw execution was performed.
No issue was closed.
No merge was performed.

Critical blocker: policy behavior unclear.
"""

    decision = evaluate_human_gate(0, summary)

    assert decision.decision == "needs-work"
    assert "critical blocker" in decision.reason


def test_gate_human_output_identifies_supported_human_gate():
    from signposter.gate import format_gate_report

    result = {
        "repo": "test/repo",
        "issue": 23,
        "issue_title": "H033C — Add formal gate:human issue gate support",
        "current_state": "OPEN",
        "labels": ["phase:build", "state:active", "risk:high", "gate:human"],
        "summary_path": "artifacts/runs/issue-23-worker.summary.md",
        "raw_path": "artifacts/runs/issue-23-worker.raw.txt",
        "has_state_active": True,
        "gate_type": "human",
        "has_gate_review": False,
        "has_gate_ci": False,
        "has_gate_human": True,
        "valid_for_gate": True,
        "decision": "pass",
        "reason": "Human gate approval evidence found with validation and safety context.",
        "confidence": "high",
        "proposed_transition": "state:active → state:done",
        "proposed_command": "signposter complete --repo test/repo --issue 23 --apply",
    }

    output = format_gate_report(result)

    assert "gate type:            human" in output
    assert "gate:human present:   True" in output
    assert "Decision:" in output
    assert "PASS" in output
    assert "WARNING:" not in output


def test_evaluate_ci_gate_pass_on_general_scoped_code_task():
    """Scoped src+tests code task should not require lifecycle-watch wording."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #28 — H033E — Generalize scoped code CI gate evidence
**Agent:** human/operator
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Files changed

- src/signposter/gate.py
- tests/test_gate.py

## Implemented behavior

Added a conservative general scoped-code CI gate evidence path.

## Validation evidence

Targeted validation passed:

- ruff check src/signposter/gate.py tests/test_gate.py
- pytest tests/test_gate.py -q

Full validation passed:

- ruff check .
- pytest tests/ -q

## Safety

No GitHub mutation was performed by the implemented code.
No OpenClaw execution was performed by the implemented code.
No issue was closed by the implemented code.
No merge was performed by the implemented code.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert decision.proposed_transition == "state:active → state:done"
    assert "scoped code change evidence" in decision.reason


def test_evaluate_ci_gate_allows_neutral_traceback_example_in_formal_summary():
    """Neutral discussion of blocker words should not block scoped evidence."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #40 — H034I — Safer CI gate evidence trigger matching
**Agent:** human/operator
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Files changed

- src/signposter/gate.py
- tests/test_gate.py

## Implemented behavior

Negative signal examples such as traceback are documented as words that still block
when actual failure output is present.

## Validation evidence

Targeted validation passed:

- ruff check src/signposter/gate.py tests/test_gate.py
- pytest tests/test_gate.py -q

Full validation passed:

- ruff check .
- pytest tests/ -q

## Safety

No GitHub mutation was performed by the implemented code.
No OpenClaw execution was performed by the implemented code.
No issue was closed by the implemented code.
No merge was performed by the implemented code.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"


def test_evaluate_ci_gate_blocks_actual_traceback_output():
    from signposter.gate import evaluate_ci_gate

    raw = """
Traceback (most recent call last):
  File "worker.py", line 12, in <module>
    raise RuntimeError("boom")
RuntimeError: boom
"""

    decision = evaluate_ci_gate(0, raw)

    assert decision.decision == "needs-work"
    assert "Python exception output" in decision.reason
